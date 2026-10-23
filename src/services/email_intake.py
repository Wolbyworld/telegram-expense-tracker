"""Resend Receiving polling. No public endpoint, outgoing key fallback, or auto-save."""

import asyncio
import ipaddress
import json
import logging
import os
import random
import socket
import uuid
from datetime import timedelta
from email.utils import getaddresses
from html.parser import HTMLParser
from urllib.parse import quote, urlsplit

import aiohttp
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from src.config import settings
from src.models import async_session
from src.models.intake import IncomingMessage, IntakePollState
from src.services.receipt_files import UnsafeReceiptError, store_private
from src.services.receipt_intake import (
    checked_owner,
    lock_owner,
    now,
    process_pending,
    register_source,
)

logger = logging.getLogger(__name__)
API = "https://api.resend.com"


class ReceivingCredentialsError(RuntimeError):
    pass


class ReceivingError(RuntimeError):
    def __init__(self, code, retry_after=0):
        super().__init__(code)
        self.retry_after = retry_after


class PublicResolver(aiohttp.abc.AbstractResolver):
    async def resolve(self, host, port=0, family=socket.AF_INET):
        addresses = await asyncio.get_running_loop().getaddrinfo(
            host,
            port,
            type=socket.SOCK_STREAM,
            family=family,
        )
        output = []
        for address_family, _, proto, _, sockaddr in addresses:
            if not ipaddress.ip_address(sockaddr[0]).is_global:
                raise UnsafeReceiptError("download_address_not_public")
            output.append(
                {
                    "hostname": host,
                    "host": sockaddr[0],
                    "port": port,
                    "family": address_family,
                    "proto": proto,
                    "flags": 0,
                }
            )
        return output

    async def close(self):
        pass


def addresses(values) -> set[str]:
    if isinstance(values, str):
        values = [values]
    return {address.strip().casefold() for _, address in getaddresses(values or []) if address}


def permitted_metadata(email):
    intended = settings.email_intake_address.strip().casefold()
    recipients = addresses(email.get("to")) | addresses(email.get("received_for"))
    senders = addresses(email.get("from"))
    allowed = addresses(settings.email_intake_senders.split(","))
    return bool(intended and intended in recipients and len(senders) == 1 and senders <= allowed)


def authenticated(email):
    auth = email.get("authentication") or {}
    # These are Resend's receiving-server results, not message-supplied headers.
    return auth.get("dmarc") == "pass" or (
        auth.get("dkim") == "pass" and auth.get("dmarc") == "gray"
    )


class TextOnly(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "iframe", "form", "noscript"}:
            self.hidden += 1
        if not self.hidden and tag in {"p", "div", "br", "tr", "li"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "iframe", "form", "noscript"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def body_text(email):
    plain = email.get("text")
    if plain:
        body = plain
    else:
        html = email.get("html") or ""
        # Current API may return data-URI HTML. Decode locally, never fetch a URL.
        if html.startswith("data:"):
            import base64
            from urllib.parse import unquote_to_bytes

            header, _, content = html.partition(",")
            try:
                html = (
                    base64.b64decode(content, validate=True)
                    if ";base64" in header
                    else unquote_to_bytes(content)
                ).decode("utf-8")
            except (ValueError, UnicodeError):
                raise UnsafeReceiptError("invalid_html_encoding") from None
        parser = TextOnly()
        parser.feed(html[:100_001])
        body = "".join(parser.parts)
    if not body.strip() or len(body) > 100_000:
        raise UnsafeReceiptError("missing_or_oversized_email_body")
    return body


class ResendReceiving:
    def __init__(self, key: str):
        if not key:
            raise ReceivingCredentialsError("receiving_key_not_configured")
        self.key = key
        self.last_request_at = 0.0

    async def get(self, path, params=None):
        loop = asyncio.get_running_loop()
        await asyncio.sleep(max(0, self.last_request_at + 0.25 - loop.time()))
        self.last_request_at = loop.time()
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as http:
            async with http.get(
                API + path,
                params=params,
                allow_redirects=False,
                headers={"Authorization": f"Bearer {self.key}"},
            ) as response:
                if response.status in {401, 403}:
                    raise ReceivingCredentialsError("receiving_key_missing_or_insufficient_scope")
                if response.status != 200:
                    # Provider bodies may contain private content; never log them.
                    delay = response.headers.get("Retry-After", "0")
                    raise ReceivingError(
                        f"provider_http_{response.status}",
                        min(3600, int(delay)) if delay.isdigit() else 0,
                    )
                chunks = bytearray()
                async for chunk in response.content.iter_chunked(65_536):
                    chunks.extend(chunk)
                    if len(chunks) > settings.intake_max_message_bytes:
                        raise UnsafeReceiptError("provider_payload_size_limit")
                return json.loads(chunks)

    async def download(self, url):
        target = urlsplit(url)
        if (
            target.scheme != "https"
            or target.username
            or target.password
            or target.port not in {None, 443}
            or not target.hostname
            or not any(
                target.hostname.endswith(suffix)
                for suffix in (
                    ".resend.com",
                    ".resend.dev",
                    ".cloudfront.net",
                    ".amazonaws.com",
                    ".rmta.net",
                )
            )
        ):
            raise UnsafeReceiptError("unapproved_attachment_download_url")
        connector = aiohttp.TCPConnector(resolver=PublicResolver())
        async with aiohttp.ClientSession(
            connector=connector, timeout=aiohttp.ClientTimeout(total=45)
        ) as http:
            async with http.get(url, allow_redirects=False) as response:
                if response.status != 200:
                    # Re-fetch the attachment API on retry to renew its signed URL.
                    raise ReceivingError(f"attachment_http_{response.status}")
                data = bytearray()
                async for chunk in response.content.iter_chunked(65_536):
                    data.extend(chunk)
                    if len(data) > settings.intake_max_attachment_bytes:
                        raise UnsafeReceiptError("attachment_size_limit")
                return bytes(data)


def require_config():
    checked_owner(settings.email_intake_owner_id)
    if (
        not settings.allowed_user_ids
        or settings.email_intake_owner_id not in settings.allowed_user_ids
    ):
        raise ValueError("email_owner_must_be_explicitly_allowed")
    if len(addresses(settings.email_intake_address)) != 1 or not addresses(
        settings.email_intake_senders.split(",")
    ):
        raise ValueError("email_address_and_sender_allowlist_required")
    if not settings.resend_receiving_api_key:
        raise ReceivingCredentialsError("receiving_key_not_configured")


async def discover(metadata):
    external_id = str(metadata["id"])
    if len(external_id) > 255:
        raise ReceivingError("invalid_provider_id")
    async with async_session() as session:
        async with session.begin():
            values = dict(
                id=uuid.uuid4().hex,
                provider="resend",
                account=settings.email_intake_account,
                external_id=external_id,
                owner_id=settings.email_intake_owner_id,
                sender=next(iter(addresses(metadata.get("from"))), "")[:320],
                recipient=settings.email_intake_address[:320],
                status="received" if permitted_metadata(metadata) else "quarantined",
                error_code=None
                if permitted_metadata(metadata)
                else "sender_or_recipient_not_allowed",
                attempts=0,
            )
            await session.execute(
                insert(IncomingMessage)
                .values(**values)
                .on_conflict_do_nothing(
                    index_elements=["provider", "account", "external_id"],
                )
            )


async def stage_message(message_id, provider):
    token = uuid.uuid4().hex
    async with async_session() as session:
        async with session.begin():
            message = (
                await session.execute(
                    select(IncomingMessage)
                    .where(
                        IncomingMessage.id == message_id,
                    )
                    .with_for_update()
                )
            ).scalar_one()
            if message.status not in {"received", "retry", "processing"}:
                return
            if message.lease_until and message.lease_until > now():
                return
            if message.next_attempt_at and message.next_attempt_at > now():
                return
            message.status = "processing"
            message.lease_until = now() + timedelta(minutes=10)
            message.lease_token = token
            message.attempts += 1
        snapshot = message
    try:
        external_id = quote(snapshot.external_id, safe="")
        email = await provider.get(f"/emails/receiving/{external_id}")
        if not permitted_metadata(email) or not authenticated(email):
            raise UnsafeReceiptError("sender_authentication_or_recipient_failed")
        total = 0
        payload = json.dumps(email, ensure_ascii=False).encode()
        if len(payload) > settings.intake_max_message_bytes:
            raise UnsafeReceiptError("message_size_limit")
        # Persist evidence without signed URLs: those are short-lived bearer credentials.
        evidence = {
            key: email.get(key)
            for key in (
                "id",
                "message_id",
                "from",
                "to",
                "received_for",
                "subject",
                "text",
                "html",
                "authentication",
                "created_at",
            )
        }
        evidence_path = store_private(json.dumps(evidence, ensure_ascii=False).encode(), ".json")
        attachments = email.get("attachments") or []
        if len(attachments) > settings.intake_max_attachments:
            raise UnsafeReceiptError("attachment_count_limit")
        if attachments:
            # Attachments are primary evidence. Do not double-extract the matching body.
            for index, attachment in enumerate(attachments):
                if attachment.get("size", 0) > settings.intake_max_attachment_bytes:
                    raise UnsafeReceiptError("attachment_size_limit")
                attachment_id = quote(str(attachment["id"]), safe="")
                info = await provider.get(
                    f"/emails/receiving/{external_id}/attachments/{attachment_id}",
                )
                data = await provider.download(info["download_url"])
                total += len(data)
                if total > settings.intake_max_message_bytes:
                    raise UnsafeReceiptError("message_size_limit")
                await register_source(
                    snapshot.owner_id,
                    "email",
                    f"{snapshot.id}:{attachment['id']}",
                    data,
                    attachment.get("content_type") or "application/octet-stream",
                    attachment.get("filename") or f"attachment-{index}",
                    snapshot.id,
                )
        else:
            await register_source(
                snapshot.owner_id,
                "email",
                snapshot.id + ":body",
                body_text(email).encode(),
                "text/plain",
                "Email body",
                snapshot.id,
            )
        async with async_session() as session:
            async with session.begin():
                message = (
                    await session.execute(
                        select(IncomingMessage)
                        .where(
                            IncomingMessage.id == snapshot.id,
                            IncomingMessage.lease_token == token,
                        )
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if message:
                    message.authentication = email.get("authentication")
                    message.payload_path = evidence_path
                    message.status = "staged"
                    message.lease_token = None
                    message.lease_until = None
                    message.error_code = None
    except Exception as exc:
        async with async_session() as session:
            async with session.begin():
                message = (
                    await session.execute(
                        select(IncomingMessage)
                        .where(
                            IncomingMessage.id == snapshot.id,
                            IncomingMessage.lease_token == token,
                        )
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if message:
                    message.status = (
                        "quarantined"
                        if isinstance(exc, UnsafeReceiptError)
                        else "failed"
                        if message.attempts >= settings.intake_max_attempts
                        else "retry"
                    )
                    message.error_code = (
                        str(exc)
                        if isinstance(exc, (UnsafeReceiptError, ReceivingError))
                        else type(exc).__name__
                    )
                    message.next_attempt_at = now() + timedelta(
                        seconds=min(3600, 30 * 2**message.attempts)
                    )
                    message.lease_token = None
                    message.lease_until = None
        if isinstance(exc, (ReceivingCredentialsError, ReceivingError)):
            raise


async def poll_once(provider=None):
    require_config()
    provider = provider or ResendReceiving(settings.resend_receiving_api_key)
    cursor = None
    seen_cursors = set()
    scanned = 0
    try:
        for _ in range(settings.email_intake_max_pages):
            params = {"limit": "100"}
            if cursor:
                params["after"] = cursor
            page = await provider.get("/emails/receiving", params=params)
            items = page.get("data") or []
            for item in items:
                await discover(item)
                scanned += 1
            if not page.get("has_more"):
                break
            if not items or items[-1]["id"] in seen_cursors:
                raise ReceivingError("pagination_did_not_advance")
            cursor = items[-1]["id"]
            seen_cursors.add(cursor)
        else:
            raise ReceivingError("pagination_limit_reached_reconciliation_incomplete")
        async with async_session() as session:
            pending = (
                (
                    await session.execute(
                        select(IncomingMessage.id)
                        .where(
                            IncomingMessage.account == settings.email_intake_account,
                            IncomingMessage.provider == "resend",
                            IncomingMessage.status.in_(["received", "retry", "processing"]),
                        )
                        .order_by(IncomingMessage.created_at)
                        .limit(100)
                    )
                )
                .scalars()
                .all()
            )
        for message_id in pending:
            await stage_message(message_id, provider)
        await process_pending()
        async with async_session() as session:
            async with session.begin():
                await session.execute(
                    insert(IntakePollState)
                    .values(
                        account=settings.email_intake_account,
                        last_success_at=now(),
                        scanned_messages=scanned,
                    )
                    .on_conflict_do_update(
                        index_elements=["account"],
                        set_={
                            "last_success_at": now(),
                            "last_error": None,
                            "scanned_messages": scanned,
                        },
                    )
                )
        return scanned
    except Exception as exc:
        async with async_session() as session:
            async with session.begin():
                await session.execute(
                    insert(IntakePollState)
                    .values(
                        account=settings.email_intake_account,
                        scanned_messages=scanned,
                        last_error=type(exc).__name__,
                    )
                    .on_conflict_do_update(
                        index_elements=["account"],
                        set_={
                            "last_error": type(exc).__name__,
                        },
                    )
                )
        raise


async def retry_message(message_id, owner_id, actor):
    checked_owner(owner_id)
    async with async_session() as session:
        async with session.begin():
            await lock_owner(session, owner_id)
            message = (
                await session.execute(
                    select(IncomingMessage)
                    .where(
                        IncomingMessage.id == message_id,
                        IncomingMessage.owner_id == owner_id,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if not message:
                raise ValueError("message_not_found")
            if message.status == "staged":
                return {"id": message.id, "status": message.status}
            if message.lease_until and message.lease_until > now():
                raise ReceivingError("message_is_processing")
            message.status = "received"
            message.next_attempt_at = None
            message.lease_token = None
            message.lease_until = None
            message.attempts = 0
            message.operator_actions = (message.operator_actions or [])[-99:] + [
                {
                    "action": "retry",
                    "actor": actor[:100],
                    "at": now().isoformat(),
                }
            ]
        return {"id": message.id, "status": message.status}


async def refresh_scanner_signatures():
    """Maintain only the app image's scanner; no host daemon/cron or receipt egress."""
    if settings.intake_scanner_path != "/usr/bin/clamscan":
        return
    process = await asyncio.create_subprocess_exec(
        "/usr/bin/freshclam",
        "--stdout",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        env={"PATH": os.defpath, "LANG": "C.UTF-8"},
    )
    try:
        code = await asyncio.wait_for(process.wait(), 180)
    except (TimeoutError, asyncio.CancelledError) as exc:
        process.kill()
        await process.wait()
        if isinstance(exc, asyncio.CancelledError):
            raise
        code = -1
    if code:
        logger.warning("Scanner signature refresh failed; stale signatures fail closed")


async def run_forever():
    last_scanner_attempt = None
    while True:
        delay = max(60, settings.email_intake_poll_seconds)
        try:
            require_config()
            if last_scanner_attempt is None or now() - last_scanner_attempt > timedelta(days=1):
                last_scanner_attempt = now()
                await refresh_scanner_signatures()
            await poll_once()
        except asyncio.CancelledError:
            raise
        except ReceivingCredentialsError:
            logger.error("Email intake halted: receiving credential absent or lacks permission")
            return
        except ValueError:
            logger.error("Email intake halted: invalid owner, recipient, or sender configuration")
            return
        except ReceivingError as exc:
            logger.error("Email intake provider failed: %s", str(exc))
            delay = max(delay, exc.retry_after) + random.uniform(0, 10)
        except Exception as exc:
            logger.error("Email intake poll failed: %s", type(exc).__name__)
            delay += random.uniform(0, 10)
        await asyncio.sleep(delay)
