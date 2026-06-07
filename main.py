#!/usr/bin/env python3
"""Automated Outreach Pipeline — one command, four stages, zero handoffs.

    python main.py example.com
    python main.py example.com --max-companies 5 --max-contacts-per-company 2
    python main.py example.com --dry-run           # resolve everything, send nothing
    python main.py example.com --mock              # no keys / no network, fake data
    python main.py example.com --yes               # hands-off: skip the confirm prompt

Stages:  Ocean.io → Prospeo → Eazyreach → Brevo
"""
from __future__ import annotations

import argparse
import sys

# Windows terminals default to cp1252 and crash on box-drawing / arrow glyphs.
# Force UTF-8 so the pretty output works everywhere (no-op where unsupported).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from pipeline import __version__
from pipeline.config import Config
from pipeline.http_client import APIError, HttpClient
from pipeline.logging_utils import Logger, bold, red
from pipeline.orchestrator import Orchestrator, RunOptions
from pipeline.stages.apollo import ApolloStage
from pipeline.stages.brevo import BrevoStage
from pipeline.stages.eazyreach import EazyreachStage
from pipeline.stages.mock import MockEazyreach, MockProspeo, MockSource
from pipeline.stages.ocean import OceanStage
from pipeline.stages.prospeo import ProspeoStage


def clean_seed(domain: str) -> str:
    d = domain.strip().lower()
    for prefix in ("https://", "http://", "www."):
        if d.startswith(prefix):
            d = d[len(prefix):]
    return d.split("/")[0]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="outreach",
        description="Automated cold-outreach pipeline: one seed domain in, "
        "personalized emails out (Ocean.io → Prospeo → Eazyreach → Brevo).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("domain", help="seed company domain, e.g. stripe.com")
    p.add_argument("--source", choices=("apollo", "ocean"), default=None,
                   help="Stage 1 lookalike source (default: apollo, or STAGE1_PROVIDER)")
    p.add_argument("--companies", default=None,
                   help="comma-separated company domains to target directly, "
                        "skipping Stage 1 sourcing (testing / known-target override)")
    p.add_argument("--max-companies", type=int, default=None,
                   help="max lookalike companies to expand (default from .env)")
    p.add_argument("--max-contacts-per-company", type=int, default=None,
                   help="max decision-makers per company (default from .env)")
    p.add_argument("--max-emails", type=int, default=None,
                   help="hard cap on total emails for the run (safety, default from .env)")
    p.add_argument("--seniority", default=None,
                   help="comma-separated seniority levels for Prospeo "
                        "(default: Founder/Owner,C-Suite,VP)")
    p.add_argument("--dry-run", action="store_true",
                   help="run all stages but render emails instead of sending")
    p.add_argument("--yes", action="store_true",
                   help="skip the confirmation prompt (fully hands-off send)")
    p.add_argument("--mock", action="store_true",
                   help="use fake data, no API keys or network — proves the wiring")
    p.add_argument("--output", default=None,
                   help="write a JSON run report to this path")
    p.add_argument("--verbose", action="store_true", help="verbose per-item logging")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    log = Logger(verbose=args.verbose)

    cfg = Config.from_env()
    if args.source:
        cfg.stage1_provider = args.source
    if args.seniority:
        cfg.target_seniority = [s.strip() for s in args.seniority.split(",") if s.strip()]

    seed = clean_seed(args.domain)
    if not seed or "." not in seed:
        log.error(f"{args.domain!r} doesn't look like a valid domain.")
        return 2

    # fail fast on missing credentials (unless mocking)
    missing = cfg.missing_keys(mock=args.mock, skip_stage1=bool(args.companies))
    if missing:
        log.error("Missing required configuration: " + ", ".join(missing))
        log.info("Copy .env.example to .env and fill it in, or run with --mock.")
        return 2

    opts = RunOptions(
        seed_domain=seed,
        max_companies=args.max_companies or cfg.max_companies,
        max_contacts_per_company=args.max_contacts_per_company
        or cfg.max_contacts_per_company,
        max_emails=args.max_emails or cfg.max_emails,
        dry_run=args.dry_run,
        auto_yes=args.yes,
        mock=args.mock,
        output_path=args.output,
        companies=[clean_seed(c) for c in args.companies.split(",") if c.strip()]
        if args.companies else None,
    )

    print(bold(f"\n  Automated Outreach Pipeline  ·  seed = {seed}"))
    mode = "MOCK" if args.mock else ("DRY-RUN" if args.dry_run else "LIVE")
    src = "mock" if args.mock else cfg.stage1_provider
    print(f"  mode: {mode}   stage-1: {src}   caps: {opts.max_companies} companies × "
          f"{opts.max_contacts_per_company} contacts, ≤{opts.max_emails} emails")

    http = HttpClient(
        logger=log,
        request_delay=0.0 if args.mock else cfg.request_delay_seconds,
    )

    if args.mock:
        source, prospeo, eazyreach = MockSource(), MockProspeo(), MockEazyreach()
    else:
        # Stage 1 is swappable: Apollo (default) or Ocean.io, same interface.
        if cfg.stage1_provider == "ocean":
            source = OceanStage(cfg, http, log)
        else:
            source = ApolloStage(cfg, http, log)
        prospeo = ProspeoStage(cfg, http, log)
        eazyreach = EazyreachStage(cfg, http, log)
    brevo = BrevoStage(cfg, http, log)  # used in dry-run/mock without sending

    orch = Orchestrator(source, prospeo, eazyreach, brevo, cfg, log)
    try:
        result = orch.run(opts)
    except APIError as exc:
        log.error(f"Pipeline stopped: {exc}")
        return 1
    except KeyboardInterrupt:
        print()
        log.warn("Interrupted by user.")
        return 130

    if result.aborted:
        return 0
    return 0 if result.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
