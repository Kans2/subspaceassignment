"""Personalized outreach copy. This is the part that earns the open.

Rather than a generic blast, the copy adapts to the recipient's seniority and
references their company by name. Edit ``PRODUCT_*`` constants below to pitch
your own product — everything else flows from the Contact object.
"""
from __future__ import annotations

from dataclasses import dataclass

from .models import Contact

# ── Pitch these once; they apply to every email ──────────────────────────
PRODUCT_NAME = "AIgnite"
PRODUCT_ONE_LINER = "AI voice agents that qualify and follow up with every inbound lead in seconds"
CALENDAR_LINK = "https://cal.com/your-handle/15min"


@dataclass
class RenderedEmail:
    subject: str
    html: str
    text: str


def _first_name(contact: Contact) -> str:
    return contact.prospect.first_name or "there"


def _hook_for_role(title: str | None) -> str:
    """A one-line opener tuned to the recipient's role."""
    t = (title or "").lower()
    if any(k in t for k in ("sales", "revenue", "growth", "cro")):
        return "your reps are almost certainly losing deals to slow lead response"
    if any(k in t for k in ("marketing", "cmo", "demand")):
        return "the leads your campaigns work so hard to generate are going cold before anyone calls them back"
    if any(k in t for k in ("founder", "ceo", "owner", "coo")):
        return "every inbound lead that waits more than five minutes is a coin-flip you're losing"
    if any(k in t for k in ("operations", "support", "success", "customer")):
        return "your team is spending hours on first-touch calls software could handle instantly"
    return "speed-to-lead is quietly costing you pipeline every single week"


def render(contact: Contact) -> RenderedEmail:
    p = contact.prospect
    name = _first_name(contact)
    company = p.company_name or p.company_domain
    hook = _hook_for_role(p.job_title)

    subject = f"{company}'s inbound leads — answered in seconds?"

    text = f"""Hi {name},

I'll keep this short. I came across {company} and figured {hook}.

{PRODUCT_NAME} is {PRODUCT_ONE_LINER}. It picks up the moment a lead comes in,
qualifies them in a natural conversation, and books the meeting straight into
your team's calendar — 24/7, no extra headcount.

Teams switch the day they realise how many of those after-hours leads were
never getting a call back at all.

Worth a quick look? Here's 15 minutes whenever it suits you:
{CALENDAR_LINK}

Best,
{{sender_name}}

P.S. Not the right person at {company}? A pointer to whoever owns lead response
would mean a lot.
"""

    html = f"""\
<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;font-size:15px;line-height:1.55;color:#1a1a1a;">
  <p>Hi {name},</p>
  <p>I'll keep this short. I came across <strong>{company}</strong> and figured {hook}.</p>
  <p><strong>{PRODUCT_NAME}</strong> is {PRODUCT_ONE_LINER}. It picks up the moment a
     lead comes in, qualifies them in a natural conversation, and books the meeting
     straight into your team's calendar — 24/7, no extra headcount.</p>
  <p>Teams switch the day they realise how many of those after-hours leads were never
     getting a call back at all.</p>
  <p>Worth a quick look?
     <a href="{CALENDAR_LINK}">Grab 15 minutes here</a> whenever it suits you.</p>
  <p>Best,<br>{{sender_name}}</p>
  <p style="color:#888;font-size:13px;">P.S. Not the right person at {company}? A pointer
     to whoever owns lead response would mean a lot.</p>
</div>"""

    return RenderedEmail(subject=subject, html=html, text=text)
