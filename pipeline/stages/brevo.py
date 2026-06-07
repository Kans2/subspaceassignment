"""Stage 4 · Brevo — verified email → personalized outreach sent.

Docs: POST https://api.brevo.com/v3/smtp/email
Auth: header ``api-key: <key>``.
Body: { sender:{name,email}, to:[{email,name}], subject, htmlContent, textContent }
On success Brevo returns ``{ "messageId": "<...>" }``.
"""
from __future__ import annotations

from typing import Dict

from .. import email_copy
from ..config import Config
from ..http_client import APIError, HttpClient
from ..logging_utils import Logger
from ..models import Contact

BREVO_SEND_URL = "https://api.brevo.com/v3/smtp/email"


class BrevoStage:
    def __init__(self, config: Config, http: HttpClient, logger: Logger) -> None:
        self.cfg = config
        self.http = http
        self.log = logger

    def _headers(self) -> Dict[str, str]:
        return {
            "api-key": self.cfg.brevo_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def send(self, contact: Contact, dry_run: bool = False) -> Contact:
        """Send one personalized email. Records the outcome on the Contact.

        A failure for one recipient is captured on the contact and the run
        continues — partial failures must not abort the batch.
        """
        rendered = email_copy.render(contact)
        sender_name = self.cfg.sender_name or "The Team"
        html = rendered.html.replace("{sender_name}", sender_name)
        text = rendered.text.replace("{sender_name}", sender_name)

        if dry_run:
            self.log.info(f"[dry-run] would email {contact.email} — \"{rendered.subject}\"")
            contact.sent = False
            contact.send_error = "dry-run (not sent)"
            return contact

        body = {
            "sender": {"name": sender_name, "email": self.cfg.sender_email},
            "to": [{"email": contact.email, "name": contact.prospect.full_name}],
            "subject": rendered.subject,
            "htmlContent": html,
            "textContent": text,
        }
        try:
            data = self.http.post_json(
                BREVO_SEND_URL, json=body, headers=self._headers()
            )
            contact.sent = True
            contact.message_id = data.get("messageId")
            self.log.ok(f"Sent to {contact.email} ({contact.message_id})")
        except APIError as exc:
            contact.sent = False
            contact.send_error = str(exc)
            self.log.error(f"Failed to email {contact.email}: {exc}")
        return contact
