"""The conductor: runs all four stages end to end, with de-duplication and the
safety checkpoint that gates the actual send.

Every stage's output is the next stage's input; no human touches the data in
between. The only human moment is the confirmation prompt before emails fire —
and even that can be skipped with --yes for a fully hands-off run.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

from .config import Config
from .logging_utils import Logger, bold, dim, green, yellow
from .models import Company, Contact, Prospect


@dataclass
class RunOptions:
    seed_domain: str
    max_companies: int
    max_contacts_per_company: int
    max_emails: int
    dry_run: bool = False
    auto_yes: bool = False
    mock: bool = False
    output_path: Optional[str] = None
    # Optional: skip Stage 1 sourcing and target these domains directly.
    companies: Optional[List[str]] = None


@dataclass
class RunResult:
    companies: List[Company] = field(default_factory=list)
    prospects: List[Prospect] = field(default_factory=list)
    contacts: List[Contact] = field(default_factory=list)
    sent: int = 0
    failed: int = 0
    aborted: bool = False


class Orchestrator:
    def __init__(self, source, prospeo, resolver, brevo, config: Config, logger: Logger):
        self.source = source  # Stage 1 provider (Apollo or Ocean), same interface
        self.prospeo = prospeo
        self.resolver = resolver  # Stage 3 email resolver (Prospeo enrich-person)
        self.brevo = brevo
        self.cfg = config
        self.log = logger

    def run(self, opts: RunOptions) -> RunResult:
        result = RunResult()

        # ── Stage 1 · lookalike companies (Apollo / Ocean) ────────────────
        if opts.companies:
            # Override: human supplied target domains, skip sourcing entirely.
            self.log.stage(1, "Target companies (provided)", "manual override")
            result.companies = [Company(domain=d) for d in opts.companies]
            self.log.ok(f"Using {len(result.companies)} provided company domain(s)")
        else:
            tool = {"ocean": "Ocean.io", "apollo": "Apollo.io"}.get(
                self.cfg.stage1_provider, self.cfg.stage1_provider
            )
            self.log.stage(1, "Find lookalike companies", tool)
            try:
                result.companies = self.source.find_lookalikes(
                    opts.seed_domain, opts.max_companies
                )
            except Exception as exc:  # never let sourcing crash the program
                self.log.error(f"Stage 1 sourcing failed: {exc}")
                result.companies = []
        if not result.companies:
            self.log.error("No lookalike companies found — nothing to do.")
            return result

        # ── Stage 2 · Prospeo ─────────────────────────────────────────────
        self.log.stage(2, "Find decision-makers", "Prospeo")
        for company in result.companies:
            try:
                prospects = self.prospeo.find_decision_makers(
                    company, opts.max_contacts_per_company
                )
            except Exception as exc:  # one bad company never stops the rest
                self.log.warn(f"Skipping {company.clean_domain()}: {exc}")
                continue
            result.prospects.extend(prospects)
        self.log.ok(
            f"{len(result.prospects)} decision-maker(s) across "
            f"{len(result.companies)} companies"
        )
        if not result.prospects:
            self.log.error("No decision-makers found — nothing to do.")
            return result

        # ── Stage 3 · Prospeo enrich-person ───────────────────────────────
        self.log.stage(3, "Resolve work emails", "Prospeo")
        seen_emails: set[str] = set()
        for prospect in result.prospects:
            if len(result.contacts) >= opts.max_emails:
                self.log.warn(
                    f"Reached email cap ({opts.max_emails}); skipping the rest."
                )
                break
            try:
                contact = self.resolver.resolve_email(prospect)
            except Exception as exc:  # one bad prospect never stops the rest
                self.log.warn(f"Skipping {prospect.full_name}: {exc}")
                continue
            if not contact:
                continue
            if contact.email in seen_emails:
                self.log.debug(f"Duplicate email skipped: {contact.email}")
                continue
            seen_emails.add(contact.email)
            result.contacts.append(contact)
        self.log.ok(f"{len(result.contacts)} deliverable email(s) resolved")
        if not result.contacts:
            self.log.error("No emails resolved — nothing to send.")
            return result

        # ── Safety checkpoint ─────────────────────────────────────────────
        self._print_summary(result, opts)
        if not self._confirm(result, opts):
            self.log.warn("Aborted before sending. No emails were fired.")
            result.aborted = True
            self._maybe_write(result, opts)
            return result

        # ── Stage 4 · Brevo ───────────────────────────────────────────────
        self.log.stage(4, "Send personalized outreach", "Brevo")
        effective_dry = opts.dry_run or opts.mock
        for contact in result.contacts:
            try:
                self.brevo.send(contact, dry_run=effective_dry)
            except Exception as exc:  # belt-and-suspenders; send() already guards
                contact.sent = False
                contact.send_error = str(exc)
                self.log.error(f"Send failed for {contact.email}: {exc}")
            # In dry-run/mock nothing actually sends, so a render counts as a
            # success, not a failure — only real send errors are failures.
            if effective_dry or contact.sent:
                result.sent += 1
            else:
                result.failed += 1

        self._print_final(result, effective_dry)
        self._maybe_write(result, opts)
        return result

    # ── checkpoint helpers ────────────────────────────────────────────────
    def _print_summary(self, result: RunResult, opts: RunOptions) -> None:
        self.log.banner("── SAFETY CHECKPOINT ─ review before anything sends ──")
        print(
            dim(
                f"  seed: {opts.seed_domain}   "
                f"companies: {len(result.companies)}   "
                f"prospects: {len(result.prospects)}   "
                f"emails ready: {len(result.contacts)}"
            )
        )
        print()
        header = f"  {'NAME':22} {'TITLE':26} {'COMPANY':22} EMAIL"
        print(bold(header))
        print(dim("  " + "─" * (len(header) - 2)))
        for c in result.contacts:
            p = c.prospect
            print(
                f"  {_trim(p.full_name, 22):22} "
                f"{_trim(p.job_title or '-', 26):26} "
                f"{_trim(p.company_name or p.company_domain, 22):22} "
                f"{c.email}  {dim('[' + c.email_status + ']')}"
            )
        print()

    def _confirm(self, result: RunResult, opts: RunOptions) -> bool:
        n = len(result.contacts)
        if opts.dry_run:
            self.log.info(green("[dry-run] ") + "skipping send; will render only.")
            return True
        if opts.mock:
            self.log.info(green("[mock] ") + "no real emails will be sent.")
            return True
        if opts.auto_yes:
            self.log.info(yellow(f"--yes set: sending {n} email(s) without prompt."))
            return True
        try:
            answer = input(bold(f"  Send {n} email(s) now? [y/N] ")).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return False
        return answer in ("y", "yes")

    def _print_final(self, result: RunResult, dry: bool) -> None:
        self.log.banner("── RUN COMPLETE ──")
        label = "rendered (dry-run/mock)" if dry else "sent"
        self.log.ok(f"{result.sent} email(s) {label}")
        if result.failed:
            self.log.error(f"{result.failed} email(s) failed")

    def _maybe_write(self, result: RunResult, opts: RunOptions) -> None:
        if not opts.output_path:
            return
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "seed_domain": opts.seed_domain,
            "companies": [c.__dict__ for c in result.companies],
            "contacts": [
                {
                    "name": c.prospect.full_name,
                    "title": c.prospect.job_title,
                    "seniority": c.prospect.seniority,
                    "company_domain": c.prospect.company_domain,
                    "linkedin_url": c.prospect.linkedin_url,
                    "email": c.email,
                    "email_status": c.email_status,
                    "sent": c.sent,
                    "message_id": c.message_id,
                    "send_error": c.send_error,
                }
                for c in result.contacts
            ],
            "summary": {
                "companies": len(result.companies),
                "prospects": len(result.prospects),
                "emails_resolved": len(result.contacts),
                "sent": result.sent,
                "failed": result.failed,
                "aborted": result.aborted,
            },
        }
        parent = os.path.dirname(os.path.abspath(opts.output_path))
        os.makedirs(parent, exist_ok=True)
        with open(opts.output_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        self.log.info(f"Run report written to {opts.output_path}")


def _trim(text: str, width: int) -> str:
    text = text or ""
    return text if len(text) <= width else text[: width - 1] + "…"
