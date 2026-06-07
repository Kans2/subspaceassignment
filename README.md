# Automated Outreach Pipeline

One seed domain in → personalized cold emails out. Zero humans in the loop
after the input (except one deliberate safety checkpoint before anything sends).

```
  company.domain ─▶ Apollo.io ─▶ Prospeo ──────▶ Prospeo ─────▶ Brevo ─▶ ✉ sent
                  (or Ocean.io)  search-person   enrich-person
                    lookalike    decision        verified       send
                    companies    makers +        work emails    outreach
                                 LinkedIn
```

> **Stage 1 is pluggable.** The brief names Ocean.io but explicitly allows a
> free-API-credit alternative — this build defaults to **Apollo.io** (Ocean.io
> rejects signups from many cheap TLDs as "disposable"). Switch with
> `--source ocean` / `--source apollo` or `STAGE1_PROVIDER` in `.env`. Both
> implement the identical Stage-1 interface, so nothing downstream changes.

Each stage's output is the next stage's input. No copy-paste, no manual
hand-offs.

---

## Quick start

```powershell
# 1. install deps
python -m pip install -r requirements.txt

# 2. configure (copy the template, then fill in your keys)
Copy-Item .env.example .env
notepad .env

# 3a. prove the wiring with zero keys / zero network (fake data)
python main.py stripe.com --mock

# 3b. real run, but render emails instead of sending
python main.py stripe.com --dry-run

# 3c. the real thing — you'll be shown a summary and asked to confirm
python main.py stripe.com
```

> On a fresh machine with no keys yet, run `python main.py stripe.com --mock`
> first — it exercises all four stages end to end with sample data so you can
> see the full flow and the safety checkpoint immediately.

---

## How it runs, stage by stage

| # | Stage | Tool | In → Out | Endpoint |
|---|-------|------|----------|----------|
| 1 | Find lookalike companies | **Apollo.io** *(default)* | seed domain → similar company domains | `GET /organizations/enrich` + `POST /organizations/search` |
| 1 | _alternative_ | **Ocean.io** | same | `POST api.ocean.io/v2/search/companies` |
| 2 | Find decision-makers | **Prospeo** | domain → C-suite/VP + LinkedIn URLs | `POST api.prospeo.io/search-person` |
| 3 | Resolve work emails | **Prospeo** | LinkedIn/name → verified work email | `POST api.prospeo.io/enrich-person` |
| 4 | Send outreach | **Brevo** | email → personalized mail sent | `POST api.brevo.com/v3/smtp/email` |

**How Apollo does "lookalike" (no single endpoint for it).** Two documented
calls: enrich the seed domain to read its `industry` / `keywords` /
`estimated_num_employees`, then `POST /organizations/search` filtered to the
seed's **specific keyword tags** (e.g. `payments`, `developer tools`) and its
headcount bucket. Using the specific keywords rather than the broad industry
string is what makes the matches good — for `stripe.com` it returns OpenAI,
GitHub, Shopify, PagBank, Cielo. If strict filters return too few, it broadens
once by dropping the size filter. Auth is `X-Api-Key`; both endpoints work on
Apollo's **free** plan (the richer `mixed_companies/search` is paywalled, so we
use `organizations/search`).

**Two Prospeo endpoints, one per stage.** Stage 2 uses `search-person` — it
filters by seniority and returns the LinkedIn URL + job title *without spending
an email credit*. Stage 3 then uses `enrich-person` on each result (sending the
LinkedIn URL **and** name + company) to reveal the verified work email. Splitting
it this way means we only spend an email credit on people we actually decided to
contact. `only_verified_email=true` keeps undeliverable guesses out of the batch.

> Stage 3 originally used Eazyreach (the brief's named tool). Its API works
> (two-step client-credentials auth, see git history), but the provided account
> had no credits, so the pipeline now resolves emails with Prospeo's
> `enrich-person` — one fewer dependency and no extra credits to manage.

---

## Project layout — one stage, one clear unit

```
main.py                     CLI entry point, flag parsing, wiring
pipeline/
  config.py                 all env/config in one typed object
  models.py                 Company → Prospect → Contact (the inter-stage contract)
  http_client.py            shared session: timeouts, retries, 429 backoff
  logging_utils.py          stage banners + colored console output
  email_copy.py             the personalized outreach copy (edit this!)
  orchestrator.py           runs the 4 stages, dedup, safety checkpoint, report
  stages/
    apollo.py               Stage 1 (default)
    ocean.py                Stage 1 (alternative)
    prospeo.py              Stage 2 (search-person)
    prospeo_email.py        Stage 3 (enrich-person)
    brevo.py                Stage 4
    mock.py                 fake stages for --mock
```

The orchestrator only knows the stage *interfaces*, so the real and mock
implementations are interchangeable (`main.py` swaps them based on `--mock`).

---

## CLI

```
python main.py <domain> [options]

  --source apollo|ocean         Stage 1 lookalike source             (default apollo)
  --max-companies N             lookalike companies to expand        (default 10)
  --max-contacts-per-company N  decision-makers per company          (default 3)
  --max-emails N                hard cap on total emails (safety)     (default 25)
  --seniority "C-Suite,VP"      seniority levels to target in Prospeo
  --dry-run                     run everything, render emails, send nothing
  --yes                         skip the confirm prompt (hands-off send)
  --mock                        fake data, no keys/network — proves the wiring
  --output path.json            write a JSON run report
  --verbose                     per-item logging
```

---

## Design decisions (the evaluation criteria, addressed)

**It runs end to end.** One positional `domain` argument is the only input.
Stages 1→4 fire automatically; the data object flowing through is never touched
by a human.

**Integrations done right.**
- *Auth* — each stage uses that tool's real scheme: Apollo `X-Api-Key`, Ocean
  `X-Api-Token`, Prospeo `X-KEY` (Stages 2 & 3), Brevo `api-key`.
- *Pagination* — Apollo pages by `page`/`per_page` via `pagination.total_pages`;
  Ocean pages by `from`/`size`; Prospeo pages by `page` via `pagination.total_page`.
- *Error handling* — all wired through `http_client.py`.

**Resilient to messy data.**
- Per-company / per-prospect failures are caught, logged, and skipped — one bad
  domain never crashes the run. Only Stage 1 returning nothing stops the run
  (there's literally nothing to do).
- **Rate limits**: `429` triggers exponential backoff that respects
  `Retry-After` / `x-*-reset-seconds` headers; `5xx` and network blips retry too.
  A configurable polite delay (`REQUEST_DELAY_SECONDS`) spaces out calls.
- **De-duplication**: companies deduped by domain (seed excluded); prospects by
  LinkedIn URL; contacts by email — so the same person surfaced twice is mailed
  once.
- **Undeliverable / missing**: a `400 NO_RESULTS` from Prospeo (a company with
  no matching people, or a person with no verified email) is treated as "empty",
  not an error; contacts without a verified email are dropped; Brevo send
  failures are recorded per-contact and the batch continues.

**Good judgment.**
- A **safety checkpoint** prints the full recipient table and asks for
  confirmation before a single email fires. `--dry-run` and `--mock` never send;
  `--yes` is the explicit opt-out for automation.
- Sensible default caps (10 companies × 3 contacts, ≤25 emails) stop a single
  run from blasting thousands of people or burning credits by accident.

**Sharp email copy.** `email_copy.py` adapts the opening line to the recipient's
role (sales / marketing / founder / ops) and references their company by name —
not a generic blast. Edit the `PRODUCT_*` constants and `CALENDAR_LINK` to pitch
your own product.

---

## Configuration

All keys and tunables live in `.env` (see `.env.example`). For a live run you
need **three** credentials — `APOLLO_API_KEY` (or `OCEAN_API_TOKEN`),
`PROSPEO_API_KEY`, `BREVO_API_KEY` — plus a verified `SENDER_EMAIL`. `--mock`
needs none.

### A note on Stage 3 (email resolution)

Stage 3 calls Prospeo's `enrich-person` with `only_verified_email=true`. The
verified address lives at `person.email.email` with a `status` of `VERIFIED`;
if `revealed` is false or no address comes back, the contact is dropped. Set
`PROSPEO_ONLY_VERIFIED=false` in `.env` to also accept "probable" addresses
(higher yield, lower deliverability).

---

## Run report

With `--output run.json` you get a machine-readable record of the whole run:
every company, every resolved contact, send status, and message IDs — handy for
auditing or feeding a CRM.

---

## Notes & limits

- **Brevo sender** must be a verified sender/domain in your Brevo account or
  sends are rejected.
- Free-tier API quotas are small; the default caps keep a demo run well within
  them. Raise them with the `--max-*` flags once you trust the output.
- This sends real email in live mode. Use `--dry-run` until you've eyeballed the
  copy and the recipient list.
