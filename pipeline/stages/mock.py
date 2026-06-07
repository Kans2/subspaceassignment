"""Mock stages — same interfaces as the real ones, but no network calls.

Used by ``--mock`` so you can prove the end-to-end wiring (and the safety
checkpoint) without API keys or spending credits. They mirror the exact method
signatures the orchestrator calls, so swapping them in is a one-line change.
"""
from __future__ import annotations

from typing import List, Optional

from ..models import Company, Contact, Prospect

_FAKE_PEOPLE = [
    ("Jordan", "Avery", "CEO & Co-Founder", "C-Suite"),
    ("Priya", "Nair", "VP of Sales", "VP"),
    ("Marcus", "Lee", "Chief Revenue Officer", "C-Suite"),
    ("Sofia", "Garcia", "VP Marketing", "VP"),
]


class MockSource:
    """Stand-in for Stage 1 (Apollo/Ocean) — emits fake lookalike companies."""

    def find_lookalikes(self, seed_domain: str, limit: int) -> List[Company]:
        base = seed_domain.split(".")[0]
        companies = [
            Company(domain=f"{base}-rival{i}.com", name=f"{base.title()} Rival {i}",
                    score=round(0.95 - i * 0.03, 2))
            for i in range(1, limit + 1)
        ]
        return companies[:limit]


class MockProspeo:
    def find_decision_makers(self, company: Company, limit: int) -> List[Prospect]:
        out = []
        for i, (first, last, title, sen) in enumerate(_FAKE_PEOPLE[:limit]):
            slug = f"{first}-{last}".lower()
            out.append(
                Prospect(
                    first_name=first,
                    last_name=last,
                    job_title=title,
                    seniority=sen,
                    linkedin_url=f"https://linkedin.com/in/{slug}-{i}",
                    company_domain=company.clean_domain(),
                    company_name=company.name,
                )
            )
        return out


class MockEmailResolver:
    """Stand-in for Stage 3 (Prospeo enrich-person) — fakes email resolution."""

    def resolve_email(self, prospect: Prospect) -> Optional[Contact]:
        if not prospect.linkedin_url:
            return None
        # simulate occasionally failing to find an address
        if prospect.last_name.lower() == "garcia":
            return None
        local = f"{prospect.first_name}.{prospect.last_name}".lower()
        email = f"{local}@{prospect.company_domain}"
        return Contact(prospect=prospect, email=email, email_status="verified")
