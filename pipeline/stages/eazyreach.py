"""Stage 3 · Eazyreach — LinkedIn profile URL → verified work email.

Real API (docs.eazyreach.app), served by superflow.run, with a two-step
client-credentials auth:

  1. Exchange credentials for a token (once, then cached):
        POST {base}/b2b/createAuthToken/
        body: { "clientId": "...", "clientSecret": "..." }
        200:  { "status": "success", "auth_token": "...", "id": "..." }

  2. Resolve the email, authenticated with that token:
        POST {base}/b2b/linkedin-emails
        Header: Authorization: Bearer <auth_token>
        body:   { "linkedinUrl": "<linkedin url>" }
        200:    { "status": "success",
                  "emails": [ { "email": "...",
                                "verification": "verified|probable",
                                "source": "..." } ] }
        Errors: 400 bad URL · 401 auth · 402 insufficient balance · 404 not found

The token is fetched lazily on first use and reused for the whole run; a 401 on
the email call triggers exactly one token refresh in case it expired. The parser
prefers a *verified* address over a *probable* one.
"""
from __future__ import annotations

import re
from typing import Dict, Optional, Tuple

from ..config import Config
from ..http_client import APIError, HttpClient
from ..logging_utils import Logger
from ..models import Contact, Prospect

_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def _is_balance_error(exc: APIError) -> bool:
    """Eazyreach reports out-of-credits as a 401 with a 'balance' message."""
    body = getattr(exc, "body", None)
    msg = ""
    if isinstance(body, dict):
        msg = str(body.get("message", ""))
    msg = (msg or str(body or "")).lower()
    return "balance" in msg or "recharge" in msg


class EazyreachStage:
    def __init__(self, config: Config, http: HttpClient, logger: Logger) -> None:
        self.cfg = config
        self.http = http
        self.log = logger
        self._token: Optional[str] = None  # cached bearer token for the run

    # ── URLs ──────────────────────────────────────────────────────────────
    def _auth_url(self) -> str:
        return f"{self.cfg.eazyreach_base_url}{self.cfg.eazyreach_auth_endpoint}"

    def _email_url(self) -> str:
        return f"{self.cfg.eazyreach_base_url}{self.cfg.eazyreach_email_endpoint}"

    # ── auth (step 1) ─────────────────────────────────────────────────────
    def _get_token(self, force: bool = False) -> str:
        if self._token and not force:
            return self._token
        body = {
            "clientId": self.cfg.eazyreach_client_id,
            "clientSecret": self.cfg.eazyreach_client_secret,
        }
        data = self.http.post_json(
            self._auth_url(), json=body, headers={"Content-Type": "application/json"}
        )
        # docs say "auth_token" but the live API returns "authToken"
        token = data.get("authToken") or data.get("auth_token") or data.get("token")
        if not token:
            raise APIError(f"Eazyreach auth returned no token: {data}")
        self._token = token
        self.log.debug("Eazyreach: obtained auth token")
        return token

    # ── resolve (step 2) ──────────────────────────────────────────────────
    def resolve_email(self, prospect: Prospect) -> Optional[Contact]:
        """Resolve one prospect's email. Returns None if it can't be found.

        Prospects without a LinkedIn URL are skipped; per-call errors are logged
        and swallowed so the run continues.
        """
        if not prospect.linkedin_url:
            self.log.debug(f"Skip {prospect.full_name}: no LinkedIn URL")
            return None

        body = {self.cfg.eazyreach_input_field: prospect.linkedin_url}
        try:
            data = self._post_authenticated(body)
        except APIError as exc:
            self._explain(exc, prospect)
            return None

        email, status = self._extract(data)
        if not email:
            self.log.debug(f"No email found for {prospect.full_name}")
            return None

        self.log.ok(f"{prospect.full_name} → {email} ({status})")
        return Contact(prospect=prospect, email=email.lower(), email_status=status)

    def _post_authenticated(self, body: Dict, _retry: bool = True) -> Dict:
        """POST the email request with the bearer token; refresh once on 401."""
        token = self._get_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        try:
            return self.http.post_json(self._email_url(), json=body, headers=headers)
        except APIError as exc:
            # Only refresh on a genuine token problem — a "zero balance" 401 is
            # not an auth issue and refreshing would just waste calls.
            if exc.status == 401 and _retry and not _is_balance_error(exc):
                self.log.debug("Eazyreach token rejected (401) — refreshing once")
                self._get_token(force=True)
                return self._post_authenticated(body, _retry=False)
            raise

    def _explain(self, exc: APIError, prospect: Prospect) -> None:
        if exc.status == 402 or _is_balance_error(exc):
            self.log.error(
                "Eazyreach: account has no credits (zero balance). The auth is "
                "working — just top up credits (WhatsApp the assignment contact) "
                "to resolve emails."
            )
        elif exc.status == 401:
            self.log.error(
                "Eazyreach auth failed (401) — check EAZYREACH_CLIENT_ID / "
                "EAZYREACH_CLIENT_SECRET."
            )
        elif exc.status == 404:
            self.log.debug(f"Eazyreach: profile not found for {prospect.full_name}")
        else:
            self.log.warn(f"Eazyreach failed for {prospect.full_name}: {exc}")

    # ── response parsing ─────────────────────────────────────────────────
    @classmethod
    def _extract(cls, data: Dict) -> Tuple[Optional[str], str]:
        """Pull (email, status) out of whatever envelope the API used."""
        if not isinstance(data, dict):
            return None, "unknown"

        # Primary shape: { "emails": [ {email, verification, source}, ... ] }
        emails = data.get("emails")
        if isinstance(emails, list) and emails:
            best = cls._best_from_list(emails)
            if best[0]:
                return best

        # unwrap a common envelope key if present
        for wrapper in ("data", "response", "result"):
            inner = data.get(wrapper)
            if isinstance(inner, dict):
                data = {**data, **inner}

        email = None
        for key in ("email", "work_email", "professional_email", "email_address"):
            val = data.get(key)
            if isinstance(val, str) and _EMAIL_RE.fullmatch(val.strip()):
                email = val.strip()
                break

        if not email:  # last resort: scan stringified payload for an address
            match = _EMAIL_RE.search(str(data))
            if match:
                email = match.group(0)

        status = (
            data.get("verification")
            or data.get("email_status")
            or ("verified" if email else "unknown")
        )
        return email, str(status).lower()

    @staticmethod
    def _best_from_list(emails: list) -> Tuple[Optional[str], str]:
        """Pick the best address from an ``emails`` array.

        Ranking: verified < probable < anything else, so sorting ascending puts
        the most trustworthy address first.
        """
        rank = {"verified": 0, "probable": 1}
        candidates = []
        for e in emails:
            if not isinstance(e, dict):
                continue
            addr = (e.get("email") or "").strip()
            if not addr or not _EMAIL_RE.fullmatch(addr):
                continue
            status = str(e.get("verification") or e.get("status") or "unknown").lower()
            candidates.append((rank.get(status, 2), addr.lower(), status))
        if not candidates:
            return None, "unknown"
        candidates.sort(key=lambda c: c[0])
        _, addr, status = candidates[0]
        return addr, status
