"""Stage 1 (alternative) · Apollo.io — seed domain → lookalike company domains.

Approved by the brief as an Ocean.io substitute ("find an alternative that
provides free API credits"). Apollo has no single "lookalike" endpoint, so we
build the lookalike in two documented calls:

  1. Enrich the seed:   GET  /api/v1/organizations/enrich?domain=<seed>
                        → industry, keywords, estimated_num_employees
  2. Search similar:    POST /api/v1/organizations/search
                        → companies matching the seed's keyword tags + headcount

Both endpoints work on Apollo's FREE plan. (The richer
``/api/v1/mixed_companies/search`` is paywalled — 403 / API_INACCESSIBLE — so we
deliberately use ``organizations/search``.) Auth: header ``X-Api-Key: <key>``.
This class exposes the exact same ``find_lookalikes`` interface as OceanStage,
so the orchestrator can't tell them apart.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from ..config import Config
from ..http_client import APIError, HttpClient
from ..logging_utils import Logger
from ..models import Company

# organizations/search is accessible on Apollo's FREE plan (mixed_companies/search
# is gated behind a paid plan — returns 403/API_INACCESSIBLE).
APOLLO_ENRICH_URL = "https://api.apollo.io/api/v1/organizations/enrich"
APOLLO_SEARCH_URL = "https://api.apollo.io/api/v1/organizations/search"
PER_PAGE = 100

# Apollo expects headcount filters as "min,max" strings. We map the seed's
# employee count to the bucket it falls in, so lookalikes are similarly sized.
EMPLOYEE_BUCKETS = [
    (1, 10), (11, 50), (51, 200), (201, 500),
    (501, 1000), (1001, 5000), (5001, 10000), (10001, 1000000),
]


class ApolloStage:
    def __init__(self, config: Config, http: HttpClient, logger: Logger) -> None:
        self.cfg = config
        self.http = http
        self.log = logger

    def _headers(self) -> Dict[str, str]:
        return {
            self.cfg.apollo_auth_header: self.cfg.apollo_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def find_lookalikes(self, seed_domain: str, limit: int) -> List[Company]:
        """Return up to ``limit`` lookalike companies for the seed domain."""
        self.log.info(f"Enriching seed {seed_domain!r} on Apollo to learn its profile")
        industry, keywords, size = self._enrich_seed(seed_domain)
        if industry:
            self.log.debug(f"seed industry={industry!r} size={size} keywords={keywords[:3]}")
        else:
            self.log.warn("Could not enrich seed; falling back to keyword-only search")

        companies = self._search_similar(seed_domain, industry, keywords, size, limit)

        # If strict filters returned too little, broaden once (drop size filter).
        if len(companies) < limit and size is not None:
            self.log.debug("Broadening search: dropping headcount filter")
            more = self._search_similar(seed_domain, industry, keywords, None, limit,
                                        already=set(c.domain for c in companies))
            companies.extend(more)

        companies = companies[:limit]
        self.log.ok(f"Found {len(companies)} lookalike companies")
        for c in companies:
            self.log.debug(f"{c.domain}  {c.name or ''}")
        return companies

    # ── step 1: enrich the seed ───────────────────────────────────────────
    def _enrich_seed(self, seed_domain: str):
        try:
            data = self.http.get_json(
                APOLLO_ENRICH_URL,
                params={"domain": seed_domain},
                headers=self._headers(),
            )
        except APIError as exc:
            self._explain(exc, "enrich")
            return None, [], None

        org = data.get("organization") or {}
        industry = org.get("industry")
        keywords = org.get("keywords") or []
        size = org.get("estimated_num_employees")
        return industry, keywords, size

    # ── step 2: search for similar companies ──────────────────────────────
    def _search_similar(
        self,
        seed_domain: str,
        industry: Optional[str],
        keywords: List[str],
        size: Optional[int],
        limit: int,
        already: Optional[set] = None,
    ) -> List[Company]:
        seen = already or set()
        companies: List[Company] = []

        # Build keyword tags. Prefer the seed's *specific* keywords (e.g.
        # "payments", "developer tools") — they yield far closer lookalikes than
        # the broad industry string ("information technology & services"), which
        # matches tens of thousands of unrelated firms. Industry is the fallback.
        tags = [k for k in keywords[:3] if k]
        if not tags and industry:
            tags.append(industry)

        body: Dict = {"page": 1, "per_page": min(PER_PAGE, max(limit * 2, 10))}
        if tags:
            body["q_organization_keyword_tags"] = tags
        bucket = self._bucket_for(size)
        if bucket:
            body["organization_num_employees_ranges"] = [bucket]

        if not tags and not bucket:
            self.log.warn("No usable signal from seed; cannot build a similar search.")
            return companies

        page = 1
        while len(companies) < limit:
            body["page"] = page
            try:
                data = self.http.post_json(
                    APOLLO_SEARCH_URL, json=body, headers=self._headers()
                )
            except APIError as exc:
                self._explain(exc, "search")
                break

            orgs = (data.get("organizations") or []) + (data.get("accounts") or [])
            if not orgs:
                break

            for org in orgs:
                domain = self._domain_of(org)
                if not domain or domain == seed_domain.lower() or domain in seen:
                    continue
                seen.add(domain)
                companies.append(
                    Company(domain=domain, name=org.get("name"), score=None)
                )
                if len(companies) >= limit:
                    break

            pagination = data.get("pagination") or {}
            if page >= pagination.get("total_pages", page):
                break
            page += 1

        return companies

    # ── helpers ────────────────────────────────────────────────────────────
    @staticmethod
    def _domain_of(org: Dict) -> Optional[str]:
        raw = org.get("primary_domain") or org.get("website_url") or org.get("domain")
        if not raw:
            return None
        d = raw.strip().lower()
        for prefix in ("https://", "http://", "www."):
            if d.startswith(prefix):
                d = d[len(prefix):]
        return d.split("/")[0] or None

    @staticmethod
    def _bucket_for(size: Optional[int]) -> Optional[str]:
        if not size:
            return None
        for lo, hi in EMPLOYEE_BUCKETS:
            if lo <= size <= hi:
                return f"{lo},{hi}"
        return None

    def _explain(self, exc: APIError, what: str) -> None:
        if exc.status == 403:
            self.log.error(
                f"Apollo {what} blocked (403) — your plan likely lacks API access "
                f"for this endpoint, or you're out of credits."
            )
        elif exc.status == 401:
            self.log.error("Apollo auth failed (401) — check APOLLO_API_KEY.")
        else:
            self.log.warn(f"Apollo {what} failed: {exc}")
