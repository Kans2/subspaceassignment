"""Typed data objects passed between stages.

Each stage's output is the next stage's input, so the contract between them is
just these three dataclasses — nothing leaks raw API JSON across boundaries.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Company:
    """A lookalike company produced by Stage 1 (Ocean.io)."""

    domain: str
    name: Optional[str] = None
    score: Optional[float] = None  # similarity / relevance to the seed

    def clean_domain(self) -> str:
        d = (self.domain or "").strip().lower()
        for prefix in ("https://", "http://", "www."):
            if d.startswith(prefix):
                d = d[len(prefix):]
        return d.split("/")[0]


@dataclass
class Prospect:
    """A decision-maker produced by Stage 2 (Prospeo)."""

    first_name: str
    last_name: str
    job_title: Optional[str]
    seniority: Optional[str]
    linkedin_url: Optional[str]
    company_domain: str
    company_name: Optional[str] = None

    @property
    def full_name(self) -> str:
        return " ".join(p for p in (self.first_name, self.last_name) if p).strip()


@dataclass
class Contact:
    """A prospect resolved to a deliverable email by Stage 3 (Prospeo enrich)."""

    prospect: Prospect
    email: str
    email_status: str = "unknown"  # verified | risky | unknown

    # filled in after Stage 4 (Brevo)
    sent: bool = False
    send_error: Optional[str] = None
    message_id: Optional[str] = None
