"""Stage 3 · Prospeo (enrich-person) — decision-maker → verified work email.

We resolve emails with Prospeo itself rather than a separate tool: Stage 2 hands
us a LinkedIn URL (plus name + company), and Prospeo's enrich endpoint turns that
into a verified, deliverable address.

Docs: POST https://api.prospeo.io/enrich-person
Auth: header ``X-KEY``.
Body: { "only_verified_email": true,
        "data": { "linkedin_url": "...", "first_name": "...", "last_name": "...",
                  "company_website": "..." } }
200:  { "error": false,
        "person": { "email": { "status": "VERIFIED", "revealed": true,
                               "email": "skhan@stripe.com",
                               "verification_method": "BOUNCEBAN" } } }

We send both the LinkedIn URL and the name+company so Prospeo has the best chance
of a match; ``only_verified_email`` keeps undeliverable guesses out of the batch.
"""
from __future__ import annotations

from typing import Dict, Optional

from ..config import Config
from ..http_client import APIError, HttpClient
from ..logging_utils import Logger
from ..models import Contact, Prospect

PROSPEO_ENRICH_URL = "https://api.prospeo.io/enrich-person"


class ProspeoEmailStage:
    def __init__(self, config: Config, http: HttpClient, logger: Logger) -> None:
        self.cfg = config
        self.http = http
        self.log = logger

    def _headers(self) -> Dict[str, str]:
        return {
            "X-KEY": self.cfg.prospeo_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def resolve_email(self, prospect: Prospect) -> Optional[Contact]:
        """Resolve one prospect's verified email. Returns None if not found.

        Per-call errors are logged and swallowed so the run continues.
        """
        data = self._build_data(prospect)
        if not data:
            self.log.debug(f"Skip {prospect.full_name}: not enough identity to enrich")
            return None

        body = {"only_verified_email": self.cfg.prospeo_only_verified, "data": data}
        try:
            resp = self.http.post_json(
                PROSPEO_ENRICH_URL, json=body, headers=self._headers()
            )
        except APIError as exc:
            self._explain(exc, prospect)
            return None

        if resp.get("error"):
            self.log.debug(f"No email for {prospect.full_name}: {resp.get('error_code')}")
            return None

        email, status = self._extract(resp)
        if not email:
            self.log.debug(f"No revealed email for {prospect.full_name}")
            return None

        self.log.ok(f"{prospect.full_name} → {email} ({status})")
        return Contact(prospect=prospect, email=email.lower(), email_status=status)

    @staticmethod
    def _build_data(prospect: Prospect) -> Dict[str, str]:
        data: Dict[str, str] = {}
        if prospect.linkedin_url:
            data["linkedin_url"] = prospect.linkedin_url
        if prospect.first_name:
            data["first_name"] = prospect.first_name
        if prospect.last_name:
            data["last_name"] = prospect.last_name
        if prospect.company_domain:
            data["company_website"] = prospect.company_domain
        if prospect.company_name:
            data["company_name"] = prospect.company_name
        # Need either a LinkedIn URL or a name + company to have any chance.
        has_name = "first_name" in data and "last_name" in data
        if "linkedin_url" not in data and not (has_name and "company_website" in data):
            return {}
        return data

    @staticmethod
    def _extract(resp: Dict) -> tuple[Optional[str], str]:
        email_obj = (resp.get("person") or {}).get("email") or {}
        if not isinstance(email_obj, dict):
            return None, "unknown"
        if email_obj.get("revealed") is False:
            return None, "hidden"
        email = email_obj.get("email")
        status = str(email_obj.get("status") or "unknown").lower()
        return (email, status) if email else (None, status)

    def _explain(self, exc: APIError, prospect: Prospect) -> None:
        code = exc.body.get("error_code", "") if isinstance(exc.body, dict) else ""
        if code in ("INSUFFICIENT_CREDITS", "NO_CREDITS") or exc.status == 402:
            self.log.error(
                "Prospeo: out of enrichment credits. Top up your Prospeo plan to "
                "resolve more emails."
            )
        elif exc.status == 400:
            # No verified email for this person — expected, not a failure.
            self.log.debug(
                f"Prospeo: no verified email for {prospect.full_name} "
                f"({code or 'no result'})"
            )
        else:
            self.log.warn(f"Prospeo enrich failed for {prospect.full_name}: {exc}")
