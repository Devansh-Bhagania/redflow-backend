import asyncio
import smtplib
import uuid
from email.message import EmailMessage

from app.integrations.base import MessagingProvider


class EmailProvider(MessagingProvider):
    """Outbound-only: sends via the org's own SMTP credentials over stdlib
    smtplib. No inbound webhook — receiving/parsing email into conversations
    would need a mail-receiving service (SES/Mailgun inbound, IMAP polling),
    which is out of scope for now. Same as WhatsAppProvider, this is thin: no
    templating, attachments, or HTML bodies."""

    async def send(self, *, to: str, text: str, credentials: dict) -> str:
        return await asyncio.to_thread(self._send_sync, to, text, credentials)

    def _send_sync(self, to: str, text: str, credentials: dict) -> str:
        message = EmailMessage()
        message["From"] = credentials["from_email"]
        message["To"] = to
        message["Subject"] = credentials.get("subject", "Message from " + credentials["from_email"])
        message.set_content(text)

        with smtplib.SMTP(credentials["smtp_host"], int(credentials["smtp_port"]), timeout=10) as smtp:
            smtp.starttls()
            smtp.login(credentials["smtp_username"], credentials["smtp_password"])
            smtp.send_message(message)

        return f"smtp-{uuid.uuid4().hex}"
