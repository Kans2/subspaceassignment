"""Stage 1 · Ocean.io — seed domain → lookalike company domains.

Docs: POST https://api.ocean.io/v2/search/companies
Auth: header ``X-Api-Token: <token>`` (token also accepted as ?apiToken=).
Body: { size, from, companiesFilters: { lookalikeDomains: [...], minScore } }
The response shape varies slightly across accounts (domain at the top level vs.
nested under ``company``), so parsing here is defensive.
"""
from __future__ import annotations

from typing import Dict, List

from ..config import Config
from ..http_client import APIError, HttpClient
from ..logging_utils import Logger
from ..models import Company

OCEAN_SEARCH_URL = "https://api.ocean.io/v2/search/companies"
PAGE_SIZE = 50


class OceanStage:
    def __init__(self, config: Config, http: HttpClient, logger: Logger) -> None:
        self.cfg = config
        self.http = http
        self.log = logger

    def _headers(self) -> Dict[str, str]:
        return {
            "X-Api-Token": self.cfg.ocean_token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def find_lookalikes(self, seed_domain: str, limit: int) -> List[Company]:
        """Return up to ``limit`` lookalike companies for the seed domain."""
        self.log.info(f"Searching Ocean.io for companies similar to {seed_domain!r}")
        companies: List[Company] = []
        seen: set[str] = set()
        offset = 0

        while len(companies) < limit:
            page_size = min(PAGE_SIZE, limit - len(companies))
            body = {
                "size": page_size,
                "from": offset,
                "companiesFilters": {
                    "lookalikeDomains": [seed_domain],
                    "minScore": 0.79,
                },
            }
            try:
                data = self.http.post_json(
                    OCEAN_SEARCH_URL, json=body, headers=self._headers()
                )
            except APIError as exc:
                # Stage 1 is the root of the run — if it can't return anything,
                # surface a clear error rather than silently producing nothing.
                raise APIError(
                    f"Ocean.io search failed: {exc} "
                    f"(body={getattr(exc, 'body', None)})"
                ) from exc

            raw = data.get("companies") or []
            if not raw:
                break

            for item in raw:
                company = self._parse(item)
                if not company:
                    continue
                domain = company.clean_domain()
                if not domain or domain == seed_domain.lower():
                    continue  # skip the seed itself and empty rows
                if domain in seen:
                    continue  # de-duplicate
                seen.add(domain)
                company.domain = domain
                companies.append(company)
                if len(companies) >= limit:
                    break

            offset += len(raw)
            if len(raw) < page_size:
                break  # no more pages

        self.log.ok(f"Found {len(companies)} lookalike companies")
        for c in companies:
            score = f"  ({c.score:.2f})" if c.score is not None else ""
            self.log.debug(f"{c.domain}{score}  {c.name or ''}")
        return companies

    @staticmethod
    def _parse(item: Dict) -> Company | None:
        # domain/name may be flat or nested under "company"
        nested = item.get("company") if isinstance(item.get("company"), dict) else {}
        domain = item.get("domain") or nested.get("domain")
        if not domain:
            return None
        name = item.get("name") or nested.get("name")
        score = (
            item.get("relevance")
            or item.get("ranking")
            or item.get("score")
            or nested.get("relevance")
        )
        try:
            score = float(score) if score is not None else None
        except (TypeError, ValueError):
            score = None
        return Company(domain=domain, name=name, score=score)
