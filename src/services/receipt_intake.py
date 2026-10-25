"""Shared, owner-serialized receipt acceptance for Telegram, email, and CLI."""

import asyncio
import io
import logging
import re
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import imagehash
from PIL import Image
from sqlalchemy import or_, select, text

from src.config import settings
from src.models import async_session
from src.models.expense import Expense
from src.models.intake import IncomingMessage, IntakeAttempt, IntakeDecision, ReceiptCandidate
from src.schemas.expense import ExpenseCreate, ReceiptParseResult
from src.services import expense_service, receipt_parser
from src.services.dedup_service import HASH_DISTANCE_THRESHOLD, compute_image_hash, find_duplicate
from src.services.image_enhance import enhance_receipt
from src.services.receipt_files import (
    UnsafeReceiptError,
    content_hash,
    prepared_images,
    safe_path,
    scan_file,
    store_private,
)

TERMINAL = {"committed", "linked_duplicate", "rejected"}
REVIEWABLE = {"needs_review", "duplicate_candidate", "quarantined", "failed"}


class ReviewConflictError(ValueError):
    pass


def now():
    return datetime.now(UTC).replace(tzinfo=None)


async def lock_owner(session, owner_id):
    # One transaction-scoped lock shared by every receipt adapter and manual creation.
    await session.execute(text("SELECT pg_advisory_xact_lock(:owner)"), {"owner": owner_id})


def checked_owner(owner_id):
    if owner_id <= 0 or (settings.allowed_user_ids and owner_id not in settings.allowed_user_ids):
        raise ValueError("owner_not_allowed")
    return owner_id


async def register_source(
    owner_id: int,
    channel: str,
    source_key: str,
    data: bytes,
    mime: str,
    filename: str = "receipt",
    message_id: str | None = None,
) -> ReceiptCandidate:
    checked_owner(owner_id)
    if channel not in {"telegram", "email", "cli"} or not 1 <= len(source_key) <= 500:
        raise ValueError("invalid_source_identity")
    if not data or len(data) > settings.intake_max_attachment_bytes:
        raise UnsafeReceiptError("attachment_size_limit")
    async with async_session() as session:
        async with session.begin():
            await lock_owner(session, owner_id)
            prior = (
                await session.execute(
                    select(ReceiptCandidate).where(
                        ReceiptCandidate.owner_id == owner_id,
                        ReceiptCandidate.channel == channel,
                        ReceiptCandidate.source_key == source_key,
                    )
                )
            ).scalar_one_or_none()
            if prior:
                if prior.sha256 != content_hash(data):
                    raise ReviewConflictError("source_identity_content_changed")
                return prior
            candidate = ReceiptCandidate(
                id=uuid.uuid4().hex,
                owner_id=owner_id,
                channel=channel,
                source_key=source_key,
                message_id=message_id,
                mime_type=mime[:100],
                filename=filename[:255],
                sha256=content_hash(data),
                receipt_path=store_private(
                    data,
                    {
                        "image/jpeg": ".jpg",
                        "image/png": ".png",
                        "image/webp": ".webp",
                        "application/pdf": ".pdf",
                        "text/plain": ".txt",
                    }.get(mime, ".bin"),
                ),
                status="queued",
                attempts=0,
            )
            session.add(candidate)
        return candidate


def expense_data(parsed: dict, channel: str) -> ExpenseCreate:
    result = ReceiptParseResult.model_validate(parsed)
    amount = Decimal(str(result.total_amount))
    if not amount.is_finite() or abs(amount) >= Decimal("10000000000"):
        raise ValueError("invalid_amount")
    if amount != amount.quantize(Decimal("0.01")):
        raise ValueError("amount_precision")
    if not re.fullmatch(r"[A-Z]{3}", result.currency.upper()):
        raise ValueError("invalid_currency")
    return ExpenseCreate(
        vendor=result.vendor,
        description=result.description,
        category=result.category,
        date=date.fromisoformat(result.date),
        original_amount=amount,
        original_currency=result.currency.upper(),
        location_city=result.location_city,
        location_country=result.location_country,
        line_items=result.line_items,
        source="image" if channel == "telegram" else channel,
        confidence=result.confidence,
    )


async def find_matches(session, candidate, data):
    candidate.duplicate_expense_id = None
    candidate.duplicate_candidate_id = None
    candidate.duplicate_reason = None
    # Exact files include deleted provenance: a replay must never resurrect a deleted expense.
    peers = (
        (
            await session.execute(
                select(ReceiptCandidate)
                .where(
                    ReceiptCandidate.owner_id == candidate.owner_id,
                    ReceiptCandidate.id != candidate.id,
                    ReceiptCandidate.status != "rejected",
                )
                .order_by(ReceiptCandidate.created_at, ReceiptCandidate.id)
            )
        )
        .scalars()
        .all()
    )
    for peer in peers:
        # Even inconsistent extraction of body versus attachment must not silently
        # produce a second expense from the same email. A reviewer can link them,
        # reject redundant evidence, or explicitly save a separate purchase.
        same_email_evidence = bool(
            candidate.message_id
            and candidate.message_id == peer.message_id
            and (candidate.source_key == candidate.message_id + ":body")
            != (peer.source_key == peer.message_id + ":body")
            and peer.status in {"committed", "linked_duplicate"}
        )
        exact = candidate.sha256 == peer.sha256
        visual = False
        if candidate.image_hash and peer.image_hash:
            try:
                visual = (
                    imagehash.hex_to_hash(candidate.image_hash)
                    - imagehash.hex_to_hash(peer.image_hash)
                ) <= HASH_DISTANCE_THRESHOLD
            except ValueError:
                pass
        content = False
        if peer.parsed and data.vendor:
            try:
                other = expense_data(peer.parsed, peer.channel)
                content = bool(
                    other.vendor
                    and " ".join(other.vendor.casefold().split())
                    == " ".join(data.vendor.casefold().split())
                    and other.original_amount == data.original_amount
                    and other.original_currency == data.original_currency
                    and other.date == data.date
                )
            except ValueError:
                pass
        # For pending peers, only the earlier candidate blocks the later one.
        if (exact or visual or content or same_email_evidence) and (
            peer.status in {"committed", "linked_duplicate"}
            or (peer.created_at, peer.id) < (candidate.created_at, candidate.id)
        ):
            candidate.duplicate_candidate_id = peer.id
            candidate.duplicate_expense_id = peer.expense_id
            candidate.duplicate_reason = (
                "exact_file"
                if exact
                else "image"
                if visual
                else "content"
                if content
                else "same_email_evidence"
            )
            return
    duplicate = await find_duplicate(
        session,
        candidate.owner_id,
        candidate.image_hash or "",
        vendor=data.vendor,
        amount=data.original_amount,
        currency=data.original_currency,
        expense_date=data.date,
    )
    if duplicate:
        candidate.duplicate_expense_id = duplicate.id
        candidate.duplicate_reason = "existing_receipt"


async def _extract(candidate):
    path = safe_path(candidate.receipt_path)
    if candidate.channel != "telegram":
        await scan_file(path)
    if candidate.mime_type == "text/plain":
        text_body = path.read_text(encoding="utf-8")
        parsed, raw = await receipt_parser.parse_document(text=text_body)
        return parsed, raw, None
    async with prepared_images(path, candidate.mime_type) as images:
        if len(images) == 1:
            img_hash = compute_image_hash(images[0])

            # Preserve the existing enhancement/parser path for ordinary photos.
            def enhance():
                with Image.open(io.BytesIO(images[0])) as image:
                    buffer = io.BytesIO()
                    enhance_receipt(image).save(buffer, "JPEG", quality=90)
                    return buffer.getvalue()

            enhanced = await asyncio.to_thread(enhance)
            parsed, raw = await receipt_parser.parse_receipt(enhanced)
        else:
            img_hash = None  # One page is not the identity of a multi-page receipt.
            parsed, raw = await receipt_parser.parse_document(images=images)
        return parsed, raw, img_hash


async def process_candidate(candidate_id: str, owner_id: int) -> ReceiptCandidate:
    token = uuid.uuid4().hex
    async with async_session() as session:
        async with session.begin():
            candidate = (
                await session.execute(
                    select(ReceiptCandidate)
                    .where(
                        ReceiptCandidate.id == candidate_id,
                        ReceiptCandidate.owner_id == owner_id,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if not candidate:
                raise ValueError("candidate_not_found")
            if candidate.message_id:
                message = await session.get(IncomingMessage, candidate.message_id)
                if not message or message.status != "staged" or message.owner_id != owner_id:
                    return candidate
            if candidate.status not in {"queued", "retry", "processing"}:
                return candidate
            if (
                candidate.status == "processing"
                and candidate.lease_until
                and candidate.lease_until > now()
            ):
                return candidate
            if candidate.next_attempt_at and candidate.next_attempt_at > now():
                return candidate
            if candidate.lease_token:
                abandoned = await session.get(IntakeAttempt, candidate.lease_token)
                if abandoned and abandoned.status == "processing":
                    abandoned.status = "abandoned"
                    abandoned.error_code = "lease_expired"
                    abandoned.completed_at = now()
            candidate.status = "processing"
            candidate.lease_token = token
            candidate.lease_until = now() + timedelta(minutes=5)
            candidate.attempts += 1
            session.add(
                IntakeAttempt(
                    id=token,
                    candidate_id=candidate.id,
                    number=candidate.attempts,
                    status="processing",
                )
            )
        snapshot = candidate
    try:
        parsed, raw, img_hash = await asyncio.wait_for(_extract(snapshot), 240)
        parsed_dict = parsed.model_dump(mode="json")
        validation_error = None
        try:
            data = expense_data(parsed_dict, snapshot.channel)
        except ValueError:
            validation_error = "extraction_fields_need_review"
    except Exception as exc:
        async with async_session() as session:
            async with session.begin():
                candidate = (
                    await session.execute(
                        select(ReceiptCandidate)
                        .where(
                            ReceiptCandidate.id == candidate_id,
                            ReceiptCandidate.lease_token == token,
                        )
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if candidate:
                    attempt = await session.get(IntakeAttempt, token)
                    if attempt:
                        attempt.status = "failed"
                        attempt.error_code = type(exc).__name__
                        attempt.completed_at = now()
                    candidate.error_code = (
                        str(exc) if isinstance(exc, UnsafeReceiptError) else type(exc).__name__
                    )
                    candidate.status = (
                        "quarantined"
                        if isinstance(exc, UnsafeReceiptError)
                        else "failed"
                        if candidate.attempts >= settings.intake_max_attempts
                        else "retry"
                    )
                    candidate.next_attempt_at = now() + timedelta(
                        seconds=min(3600, 30 * 2**candidate.attempts)
                    )
                    candidate.lease_until = None
                    candidate.lease_token = None
            return candidate or snapshot
    async with async_session() as session:
        async with session.begin():
            await lock_owner(session, owner_id)
            candidate = (
                await session.execute(
                    select(ReceiptCandidate)
                    .where(
                        ReceiptCandidate.id == candidate_id,
                        ReceiptCandidate.lease_token == token,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if not candidate:
                raise ReviewConflictError("extraction_lease_lost")
            candidate.parsed = parsed_dict
            attempt = await session.get(IntakeAttempt, token)
            if attempt:
                attempt.status = "completed"
                attempt.parsed = parsed_dict
                attempt.raw_response = raw
                attempt.completed_at = now()
            candidate.raw_response = raw
            candidate.image_hash = img_hash
            candidate.error_code = validation_error
            candidate.status = "needs_review"
            candidate.lease_token = None
            candidate.lease_until = None
            if not validation_error:
                await find_matches(session, candidate, data)
                if candidate.duplicate_reason:
                    candidate.status = "duplicate_candidate"
        return candidate


async def decide(
    candidate_id: str,
    owner_id: int,
    action: str,
    actor: str,
    *,
    edits: dict | None = None,
    expense_id: int | None = None,
    override_reason: str = "",
) -> ReceiptCandidate:
    checked_owner(owner_id)
    if action not in {"accept", "save_separate", "reject", "link", "retry"}:
        raise ValueError("invalid_action")
    async with async_session() as session:
        async with session.begin():
            await lock_owner(session, owner_id)
            candidate = (
                await session.execute(
                    select(ReceiptCandidate)
                    .where(
                        ReceiptCandidate.id == candidate_id,
                        ReceiptCandidate.owner_id == owner_id,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if not candidate:
                raise ValueError("candidate_not_found")
            if candidate.status in TERMINAL:
                return candidate
            if candidate.status not in REVIEWABLE:
                raise ReviewConflictError("candidate_not_ready_for_review")
            details = {}
            if action == "retry":
                candidate.status = "queued"
                candidate.next_attempt_at = None
                candidate.attempts = 0
            elif action == "reject":
                candidate.status = "rejected"
            elif action == "link":
                expense = (
                    await session.execute(
                        select(Expense).where(
                            Expense.id == expense_id,
                            Expense.telegram_user_id == owner_id,
                            Expense.deleted_at.is_(None),
                        )
                    )
                ).scalar_one_or_none()
                if not expense:
                    raise ValueError("target_expense_not_found")
                candidate.expense_id = expense.id
                candidate.status = "linked_duplicate"
                details["expense_id"] = expense.id
            else:
                if candidate.status in {"quarantined", "failed"}:
                    raise ReviewConflictError("retry_and_validate_before_accepting")
                parsed = dict(candidate.parsed or {})
                if edits:
                    allowed = {
                        "vendor",
                        "description",
                        "category",
                        "date",
                        "total_amount",
                        "currency",
                    }
                    if set(edits) - allowed:
                        raise ValueError("unsupported_edit_field")
                    parsed.update(edits)
                    details["edits"] = edits
                data = expense_data(parsed, candidate.channel)
                await find_matches(session, candidate, data)
                if candidate.duplicate_reason and action != "save_separate":
                    candidate.status = "duplicate_candidate"
                    return candidate
                if action == "save_separate":
                    if not override_reason.strip():
                        raise ValueError("override_reason_required")
                    details["reason"] = override_reason[:1000]
                expense = await expense_service.create_expense(
                    session,
                    owner_id,
                    data,
                    receipt_path=candidate.receipt_path,
                    raw_llm_response=candidate.raw_response,
                    image_hash=candidate.image_hash,
                    commit=False,
                )
                candidate.parsed = parsed
                candidate.expense_id = expense.id
                candidate.status = "committed"
                details["expense_id"] = expense.id
            session.add(
                IntakeDecision(
                    id=uuid.uuid4().hex,
                    candidate_id=candidate.id,
                    actor=actor[:100],
                    action=action,
                    details=details,
                )
            )
        if candidate.status == "committed":
            await convert_saved_expense(candidate.expense_id, owner_id)
        return candidate


async def convert_saved_expense(expense_id: int, owner_id: int):
    """Best-effort enrichment after commit. Failure never retries receipt insertion."""
    from src.services.currency_service import convert_to_base

    try:
        async with async_session() as session:
            expense = await expense_service.get_expense(session, owner_id, expense_id)
            if not expense or expense.eur_amount is not None:
                return
            result = await convert_to_base(
                session, expense.original_amount, expense.original_currency, expense.date
            )
            if result:
                await expense_service.update_expense(
                    session, owner_id, expense_id, eur_amount=result[0], exchange_rate=result[1]
                )
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "Currency enrichment pending for expense #%d: %s", expense_id, type(exc).__name__
        )


async def process_pending(limit: int = 10):
    async with async_session() as session:
        rows = (
            await session.execute(
                select(ReceiptCandidate.id, ReceiptCandidate.owner_id)
                .where(
                    or_(
                        ReceiptCandidate.status.in_(["queued", "retry"]),
                        (ReceiptCandidate.status == "processing")
                        & (ReceiptCandidate.lease_until < now()),
                    ),
                    or_(
                        ReceiptCandidate.message_id.is_(None),
                        ReceiptCandidate.message_id.in_(
                            select(IncomingMessage.id).where(
                                IncomingMessage.status == "staged",
                            )
                        ),
                    ),
                    or_(
                        ReceiptCandidate.next_attempt_at.is_(None),
                        ReceiptCandidate.next_attempt_at <= now(),
                    ),
                )
                .order_by(ReceiptCandidate.created_at)
                .limit(limit)
            )
        ).all()
    for candidate_id, owner_id in rows:
        await process_candidate(candidate_id, owner_id)


async def run_pending_forever():
    while True:
        try:
            await process_pending()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logging.getLogger(__name__).error("Receipt retry worker failed: %s", type(exc).__name__)
        await asyncio.sleep(60)
