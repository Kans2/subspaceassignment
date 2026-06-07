"""Central configuration, loaded from environment / .env.

Keeping all the knobs in one typed object means every stage reads from the
same place and the CLI can override any default without touching stage code.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List

try:
    from dotenv import load_dotenv

    load_dotenv()  # populate os.environ from a local .env if present
except ImportError:  # dotenv is optional; env vars still work without it
    pass


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    try:
        return int(raw) if raw not in (None, "") else default
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    try:
        return float(raw) if raw not in (None, "") else default
    except ValueError:
        return default


def _csv(name: str, default: List[str]) -> List[str]:
    raw = os.getenv(name)
    if not raw:
        return list(default)
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass
class Config:
    # ── Credentials ──
    ocean_token: str = ""
    apollo_key: str = ""
    prospeo_key: str = ""
    brevo_key: str = ""

    # ── Stage 1 source: "apollo" or "ocean" ──
    stage1_provider: str = "apollo"
    apollo_auth_header: str = "X-Api-Key"

    # ── Eazyreach / superflow (client-credentials → bearer token) ──
    eazyreach_client_id: str = ""
    eazyreach_client_secret: str = ""
    eazyreach_base_url: str = "https://api.superflow.run"
    eazyreach_auth_endpoint: str = "/b2b/createAuthToken/"
    eazyreach_email_endpoint: str = "/b2b/linkedin-emails"
    eazyreach_input_field: str = "linkedinUrl"

    # ── Sender identity (Brevo) ──
    sender_email: str = ""
    sender_name: str = ""

    # ── Safety / volume defaults ──
    max_companies: int = 10
    max_contacts_per_company: int = 3
    max_emails: int = 25
    request_delay_seconds: float = 0.4
    target_seniority: List[str] = field(
        default_factory=lambda: ["Founder/Owner", "C-Suite", "Vice President"]
    )

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            ocean_token=os.getenv("OCEAN_API_TOKEN", "").strip(),
            apollo_key=os.getenv("APOLLO_API_KEY", "").strip(),
            stage1_provider=os.getenv("STAGE1_PROVIDER", "apollo").strip().lower(),
            apollo_auth_header=os.getenv("APOLLO_AUTH_HEADER", "X-Api-Key").strip(),
            prospeo_key=os.getenv("PROSPEO_API_KEY", "").strip(),
            brevo_key=os.getenv("BREVO_API_KEY", "").strip(),
            eazyreach_client_id=os.getenv("EAZYREACH_CLIENT_ID", "").strip(),
            eazyreach_client_secret=os.getenv("EAZYREACH_CLIENT_SECRET", "").strip(),
            eazyreach_base_url=os.getenv(
                "EAZYREACH_BASE_URL", "https://api.superflow.run"
            ).rstrip("/"),
            eazyreach_auth_endpoint=os.getenv(
                "EAZYREACH_AUTH_ENDPOINT", "/b2b/createAuthToken/"
            ),
            eazyreach_email_endpoint=os.getenv(
                "EAZYREACH_EMAIL_ENDPOINT", "/b2b/linkedin-emails"
            ),
            eazyreach_input_field=os.getenv("EAZYREACH_INPUT_FIELD", "linkedinUrl"),
            sender_email=os.getenv("SENDER_EMAIL", "").strip(),
            sender_name=os.getenv("SENDER_NAME", "").strip(),
            max_companies=_int("MAX_COMPANIES", 10),
            max_contacts_per_company=_int("MAX_CONTACTS_PER_COMPANY", 3),
            max_emails=_int("MAX_EMAILS", 25),
            request_delay_seconds=_float("REQUEST_DELAY_SECONDS", 0.4),
            target_seniority=_csv(
                "TARGET_SENIORITY", ["Founder/Owner", "C-Suite", "Vice President"]
            ),
        )

    def missing_keys(self, mock: bool = False, skip_stage1: bool = False) -> List[str]:
        """Return human-readable names of credentials needed but not set."""
        if mock:
            return []
        missing = []
        # Stage 1 needs one of the two providers' credentials — unless the user
        # supplied target companies directly (then sourcing is skipped).
        if not skip_stage1:
            if self.stage1_provider == "ocean":
                if not self.ocean_token:
                    missing.append("OCEAN_API_TOKEN")
            else:  # apollo (default)
                if not self.apollo_key:
                    missing.append("APOLLO_API_KEY")
        if not self.prospeo_key:
            missing.append("PROSPEO_API_KEY")
        if not self.eazyreach_client_id:
            missing.append("EAZYREACH_CLIENT_ID")
        if not self.eazyreach_client_secret:
            missing.append("EAZYREACH_CLIENT_SECRET")
        if not self.brevo_key:
            missing.append("BREVO_API_KEY")
        if not self.sender_email:
            missing.append("SENDER_EMAIL")
        return missing
