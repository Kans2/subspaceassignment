"""Stage 2 · Prospeo — company domain → decision-makers + LinkedIn URLs.

Docs: POST https://api.prospeo.io/search-person
Auth: header ``X-KEY: <key>``.
We use *search-person* (not domain-search) on purpose: it filters by seniority
and returns the LinkedIn URL + job title without spending an email credit —
email resolution is Stage 3's job (enrich-person). 25 results/page, ``page`` paged.

Body:
  { "page": 1,
    "filters": {
      "person_seniority": { "include": ["Founder/Owner", "C-Suite", "VP"] },
      "company": { "websites": { "include": ["domain.com"] } } } }
"""
from __future__ import annotations

from typing import Dict, List

from ..config import Config
from ..http_client import APIError, HttpClient
from ..logging_utils import Logger
from ..models import Company, Prospect

PROSPEO_SEARCH_URL = "https://api.prospeo.io/search-person"


class ProspeoStage:
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

    def find_decision_makers(
        self, company: Company, limit: int
    ) -> List[Prospect]:
        """Return up to ``limit`` senior decision-makers for one company.

        A failure for a single company is logged and swallowed — one bad
        domain must never crash the whole run.
        """
        domain = company.clean_domain()
        prospects: List[Prospect] = []
        seen_links: set[str] = set()
        page = 1

        while len(prospects) < limit:
            body = {
                "page": page,
                "filters": {
                    "person_seniority": {"include": self.cfg.target_seniority},
                    "company": {"websites": {"include": [domain]}},
                },
            }
            try:
                data = self.http.post_json(
                    PROSPEO_SEARCH_URL, json=body, headers=self._headers()
                )
            except APIError as exc:
                # Prospeo returns 400 / NO_RESULTS when a domain simply has no
                # matching people — that's normal, not a failure. Skip quietly.
                code = exc.body.get("error_code", "") if isinstance(exc.body, dict) else ""
                if exc.status == 400 and code == "NO_RESULTS":
                    self.log.debug(f"{domain}: no people match the filters")
                else:
                    self.log.warn(f"Prospeo lookup failed for {domain}: {exc}")
                break

            if data.get("error"):
                self.log.warn(f"Prospeo returned error for {domain}: {data}")
                break

            results = data.get("results") or []
            if not results:
                break

            for row in results:
                prospect = self._parse(row, company)
                if not prospect:
                    continue
                key = (prospect.linkedin_url or prospect.full_name).lower()
                if key in seen_links:
                    continue
                seen_links.add(key)
                prospects.append(prospect)
                if len(prospects) >= limit:
                    break

            pagination = data.get("pagination") or {}
            current = pagination.get("current_page", page)
            total = pagination.get("total_page", page)
            if current >= total:
                break
            page += 1

        if prospects:
            self.log.ok(f"{domain}: {len(prospects)} decision-maker(s)")
        else:
            self.log.warn(f"{domain}: no matching decision-makers")
        return prospects

    @staticmethod
    def _parse(row: Dict, company: Company) -> Prospect | None:
        person = row.get("person") or row  # tolerate flat or nested rows
        first = (person.get("first_name") or "").strip()
        last = (person.get("last_name") or "").strip()
        if not (first or last):
            return None
        comp = row.get("company") or {}
        return Prospect(
            first_name=first,
            last_name=last,
            job_title=person.get("current_job_title")
            or person.get("job_title")
            or person.get("title"),
            seniority=person.get("seniority"),
            linkedin_url=person.get("linkedin_url") or person.get("linkedin"),
            company_domain=company.clean_domain() or comp.get("domain") or "",
            company_name=company.name or comp.get("name"),
        )
