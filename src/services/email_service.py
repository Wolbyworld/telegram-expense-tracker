"""Email delivery service for sending reports via Resend."""

import base64
import logging
import re

import aiohttp

from src.config import settings

logger = logging.getLogger(__name__)

RESEND_URL = "https://api.resend.com/emails"
_EMAIL_RE = re.compile(r"^[^@\s,]+@[^@\s,]+\.[^@\s,]+$")


def parse_recipients(value: str | None) -> list[str]:
    """Split a comma-separated list of emails into a clean, validated list."""
    if not value:
        return []
    out: list[str] = []
    for raw in value.split(","):
        addr = raw.strip()
        if addr and _EMAIL_RE.match(addr) and addr not in out:
            out.append(addr)
    return out


class EmailNotConfiguredError(RuntimeError):
    """Raised when Resend is missing required config."""


async def send_report_email(
    to_emails: list[str],
    subject: str,
    body: str,
    attachments: list[tuple[str, bytes, str]] | None = None,
) -> str:
    """Send an email via Resend. Returns the Resend message id.

    Args:
        to_emails: Recipient addresses. At least one required.
        subject: Email subject line.
        body: Plain text body.
        attachments: List of (filename, content_bytes, mime_type) tuples.
    """
    if not to_emails:
        raise ValueError("send_report_email requires at least one recipient")
    if not settings.resend_api_key:
        raise EmailNotConfiguredError("RESEND_API_KEY is not set")

    payload: dict = {
        "from": settings.email_from,
        "to": to_emails,
        "subject": subject,
        "text": body,
    }
    if attachments:
        payload["attachments"] = [
            {
                "filename": filename,
                "content": base64.b64encode(content).decode("ascii"),
                "content_type": mime_type,
            }
            for filename, content, mime_type in attachments
        ]

    headers = {
        "Authorization": f"Bearer {settings.resend_api_key}",
        "Content-Type": "application/json",
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(RESEND_URL, json=payload, headers=headers) as resp:
            text = await resp.text()
            if resp.status >= 400:
                logger.error("Resend %d: %s", resp.status, text)
                raise RuntimeError(f"Resend send failed ({resp.status}): {text}")
            data = await resp.json(content_type=None)
    msg_id = data.get("id", "")
    logger.info("Resend message %s sent to %s: %s", msg_id, to_emails, subject)
    return msg_id
