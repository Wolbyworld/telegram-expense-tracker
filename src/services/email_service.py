"""Email delivery service for sending reports."""

import logging
from email.message import EmailMessage

import aiosmtplib

from src.config import settings

logger = logging.getLogger(__name__)


async def send_report_email(
    to_email: str,
    subject: str,
    body: str,
    attachments: list[tuple[str, bytes, str]] | None = None,
) -> None:
    """Send a report email with optional attachments.

    Args:
        to_email: Recipient email address.
        subject: Email subject line.
        body: Plain text email body.
        attachments: List of (filename, content_bytes, mime_type) tuples.
    """
    msg = EmailMessage()
    msg["From"] = settings.email_from
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)

    if attachments:
        for filename, content, mime_type in attachments:
            maintype, _, subtype = mime_type.partition("/")
            msg.add_attachment(
                content,
                maintype=maintype,
                subtype=subtype,
                filename=filename,
            )

    await aiosmtplib.send(
        msg,
        hostname=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_user or None,
        password=settings.smtp_pass or None,
        start_tls=True,
    )
    logger.info("Report email sent to %s: %s", to_email, subject)
