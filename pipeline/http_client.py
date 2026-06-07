"""Shared HTTP client: one place for auth headers, timeouts, retries and the
rate-limit handling that every external API needs.

Retries cover the failure modes the brief calls out — rate limits (HTTP 429)
and transient server/network errors — using exponential backoff that also
respects ``Retry-After`` / reset headers when the API provides them.
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional

import requests

from .logging_utils import Logger


class APIError(Exception):
    """Raised when an API call fails in a way the caller should handle."""

    def __init__(self, message: str, status: Optional[int] = None, body: Any = None):
        super().__init__(message)
        self.status = status
        self.body = body


class HttpClient:
    def __init__(
        self,
        logger: Logger,
        timeout: float = 30.0,
        max_retries: int = 4,
        backoff_base: float = 1.5,
        request_delay: float = 0.0,
    ) -> None:
        self.log = logger
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.request_delay = request_delay
        self.session = requests.Session()

    # ── public helpers ────────────────────────────────────────────────────
    def post_json(
        self,
        url: str,
        json: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        return self._request("POST", url, json=json, headers=headers)

    def get_json(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        return self._request("GET", url, params=params, headers=headers)

    # ── core ──────────────────────────────────────────────────────────────
    def _request(
        self,
        method: str,
        url: str,
        json: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        attempt = 0
        while True:
            attempt += 1
            try:
                resp = self.session.request(
                    method,
                    url,
                    json=json,
                    params=params,
                    headers=headers,
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                # network blip / timeout — retry a few times, then give up
                if attempt <= self.max_retries:
                    self._sleep_backoff(attempt, reason=f"network error: {exc}")
                    continue
                raise APIError(f"Network error calling {url}: {exc}") from exc

            if resp.status_code == 429 and attempt <= self.max_retries:
                wait = self._retry_after(resp) or self._backoff(attempt)
                self.log.warn(
                    f"Rate limited (429) on {url} — waiting {wait:.1f}s "
                    f"(attempt {attempt}/{self.max_retries})"
                )
                time.sleep(wait)
                continue

            if resp.status_code >= 500 and attempt <= self.max_retries:
                self._sleep_backoff(attempt, reason=f"server {resp.status_code}")
                continue

            if not resp.ok:
                raise APIError(
                    f"{method} {url} failed: HTTP {resp.status_code}",
                    status=resp.status_code,
                    body=_safe_json(resp),
                )

            # polite spacing between successful calls to stay under limits
            if self.request_delay:
                time.sleep(self.request_delay)

            return _safe_json(resp) or {}

    # ── backoff helpers ──────────────────────────────────────────────────
    def _backoff(self, attempt: int) -> float:
        return self.backoff_base ** attempt

    def _sleep_backoff(self, attempt: int, reason: str) -> None:
        wait = self._backoff(attempt)
        self.log.debug(f"Retry {attempt}/{self.max_retries} ({reason}) — {wait:.1f}s")
        time.sleep(wait)

    @staticmethod
    def _retry_after(resp: requests.Response) -> Optional[float]:
        # honour explicit retry hints when the API sends them
        for header in ("Retry-After", "x-minute-reset-seconds", "x-second-reset-seconds"):
            val = resp.headers.get(header)
            if val:
                try:
                    return float(val)
                except ValueError:
                    continue
        return None


def _safe_json(resp: requests.Response) -> Optional[Dict[str, Any]]:
    try:
        return resp.json()
    except ValueError:
        return {"_raw": resp.text}
