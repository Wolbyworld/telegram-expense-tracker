"""Synthetic PostgreSQL integration tests. Never uses production data or live APIs."""

import asyncio
import io
import os
import uuid
from datetime import datetime, timedelta

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from PIL import Image, ImageDraw
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123:synthetic")
os.environ.setdefault("OPENAI_API_KEY", "test-only")

from src.config import settings
from src.models import Base
from src.models.expense import Expense
from src.models.intake import (
    IncomingMessage,
    IntakeAttempt,
    IntakeDecision,
    IntakePollState,
    ReceiptCandidate,
)
from src.schemas.expense import ReceiptParseResult
from src.services import email_intake, expense_service, receipt_intake
from src.services.receipt_files import prepared_images

REAL_EXTRACT = receipt_intake._extract


def image_bytes():
    image = Image.new("RGB", (300, 500), "white")
    draw = ImageDraw.Draw(image)
    draw.text((30, 40), "SYNTHETIC CAFE - TEST ONLY", fill="black")
    draw.rectangle((30, 120, 180, 135), fill="black")
    draw.text((30, 300), "TOTAL EUR 12.34", fill="black")
    out = io.BytesIO()
    image.save(out, "JPEG")
    return out.getvalue()


def parsed():
    return ReceiptParseResult(
        vendor="Synthetic Cafe",
        date="2026-10-23",
        total_amount=12.34,
        currency="EUR",
        category="Food",
        confidence=0.95,
    )


@pytest_asyncio.fixture
async def db(monkeypatch, tmp_path):
    url = os.environ.get("INTAKE_TEST_DATABASE_URL", "")
    if not url:
        pytest.skip("Set INTAKE_TEST_DATABASE_URL to a disposable local PostgreSQL database")
    # Reject production URLs even if someone accidentally copies DATABASE_URL here.
    if "127.0.0.1" not in url or "/expense_intake_test" not in url:
        pytest.fail("Intake integration tests require the dedicated local test database")
    schema = "test_intake_" + uuid.uuid4().hex
    control = create_async_engine(url)
    async with control.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    await control.dispose()
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(receipt_intake, "async_session", factory)
    monkeypatch.setattr(email_intake, "async_session", factory)
    monkeypatch.setattr(settings, "receipt_storage_path", str(tmp_path))
    monkeypatch.setattr(settings, "allowed_telegram_users", "101,202")
    monkeypatch.setattr(settings, "email_intake_owner_id", 101)
    monkeypatch.setattr(settings, "email_intake_address", "receipts@synthetic.resend.app")
    monkeypatch.setattr(settings, "email_intake_senders", "owner@example.com")
    monkeypatch.setattr(settings, "email_intake_account", "synthetic-test")
    monkeypatch.setattr(settings, "resend_receiving_api_key", "synthetic-not-a-key")

    async def fake_extract(candidate):
        return (
            parsed(),
            parsed().model_dump(mode="json"),
            None if candidate.mime_type == "text/plain" else "a" * 16,
        )

    monkeypatch.setattr(receipt_intake, "_extract", fake_extract)
    yield factory
    # Test-created schemas contain synthetic data only; remove this exact schema.
    await engine.dispose()
    control = create_async_engine(url)
    async with control.begin() as connection:
        await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    await control.dispose()


async def stage(key="first", channel="email", owner=101):
    candidate = await receipt_intake.register_source(
        owner,
        channel,
        key,
        image_bytes(),
        "image/jpeg",
        "synthetic.jpg",
    )
    return await receipt_intake.process_candidate(candidate.id, owner)


@pytest.mark.asyncio
async def test_source_replay_and_company_default(db):
    first, second = await asyncio.gather(stage("stable", "telegram"), stage("stable", "telegram"))
    assert first.id == second.id
    first = await receipt_intake.process_candidate(first.id, 101)
    accepted = await receipt_intake.decide(first.id, 101, "accept", "test")
    replay = await receipt_intake.decide(first.id, 101, "accept", "test")
    assert accepted.expense_id == replay.expense_id
    async with db() as session:
        assert (await session.scalar(select(func.count()).select_from(Expense))) == 1
        assert (await session.get(Expense, accepted.expense_id)).expense_type == "company"
        assert (await session.scalar(select(func.count()).select_from(IntakeDecision))) == 1


@pytest.mark.asyncio
async def test_cross_channel_duplicate_and_explicit_override(db):
    telegram = await stage("telegram", "telegram")
    accepted = await receipt_intake.decide(telegram.id, 101, "accept", "test")
    email = await stage("forward", "email")
    assert email.status == "duplicate_candidate"
    assert email.duplicate_expense_id == accepted.expense_id
    held = await receipt_intake.decide(email.id, 101, "accept", "test")
    assert held.status == "duplicate_candidate"
    with pytest.raises(ValueError, match="override_reason_required"):
        await receipt_intake.decide(email.id, 101, "save_separate", "test")
    saved = await receipt_intake.decide(
        email.id, 101, "save_separate", "test", override_reason="Two separate purchases"
    )
    assert saved.expense_id != accepted.expense_id
    async with db() as session:
        assert (await session.scalar(select(func.count()).select_from(Expense))) == 2
        decision = (
            await session.execute(
                select(IntakeDecision).where(
                    IntakeDecision.candidate_id == email.id,
                )
            )
        ).scalar_one()
        assert decision.details["reason"] == "Two separate purchases"


@pytest.mark.asyncio
async def test_concurrent_distinct_sources_cannot_both_commit(db):
    first, second = await asyncio.gather(stage("email"), stage("cli", "cli"))
    results = await asyncio.gather(
        receipt_intake.decide(first.id, 101, "accept", "test"),
        receipt_intake.decide(second.id, 101, "accept", "test"),
    )
    assert sorted(row.status for row in results) == ["committed", "duplicate_candidate"]
    async with db() as session:
        assert await session.scalar(select(func.count()).select_from(Expense)) == 1


@pytest.mark.asyncio
async def test_pending_duplicates_and_owner_isolation(db):
    first = await stage("pending")
    second = await stage("pending-copy", "cli")
    assert second.duplicate_candidate_id == first.id
    other = await stage("other-owner", owner=202)
    assert other.status == "needs_review"
    with pytest.raises(ValueError, match="candidate_not_found"):
        await receipt_intake.decide(first.id, 202, "reject", "wrong-owner")
    await receipt_intake.decide(first.id, 101, "reject", "test")
    # Rechecking removes a rejected pending match.
    accepted = await receipt_intake.decide(second.id, 101, "accept", "test")
    assert accepted.status == "committed"


@pytest.mark.asyncio
async def test_link_duplicate_and_soft_delete_replay(db):
    first = await stage("first")
    accepted = await receipt_intake.decide(first.id, 101, "accept", "test")
    copy = await stage("copy")
    linked = await receipt_intake.decide(
        copy.id, 101, "link", "test", expense_id=accepted.expense_id
    )
    assert linked.status == "linked_duplicate"
    async with db() as session:
        await expense_service.soft_delete_expense(session, 101, accepted.expense_id)
    replay = await receipt_intake.decide(first.id, 101, "accept", "test")
    assert replay.expense_id == accepted.expense_id
    forwarded_again = await stage("new-forward")
    assert forwarded_again.duplicate_reason == "exact_file"
    assert forwarded_again.status == "duplicate_candidate"
    async with db() as session:
        assert await session.scalar(select(func.count()).select_from(Expense)) == 1


@pytest.mark.asyncio
async def test_matches_pre_intake_telegram_expense(db):
    async with db() as session:
        expense = await expense_service.create_expense(
            session,
            101,
            receipt_intake.expense_data(parsed().model_dump(), "telegram"),
            image_hash="a" * 16,
        )
    email = await stage("email")
    assert email.duplicate_expense_id == expense.id
    assert email.duplicate_reason == "existing_receipt"


@pytest.mark.asyncio
async def test_expired_lease_recovers_and_review_survives_new_session(db):
    candidate = await receipt_intake.register_source(
        101, "email", "crash", image_bytes(), "image/jpeg"
    )
    async with db() as session:
        row = await session.get(ReceiptCandidate, candidate.id)
        row.status = "processing"
        row.lease_token = "old-worker"
        row.lease_until = datetime.utcnow() - timedelta(seconds=1)
        await session.commit()
    processed = await receipt_intake.process_candidate(candidate.id, 101)
    assert processed.status == "needs_review"
    async with db() as session:
        persisted = await session.get(ReceiptCandidate, candidate.id)
        assert persisted.parsed["vendor"] == "Synthetic Cafe"
        assert persisted.lease_token is None


@pytest.mark.asyncio
async def test_failed_extraction_retry_and_quarantine(db, monkeypatch):
    async def fail(candidate):
        raise TimeoutError("never persist private exception text")

    monkeypatch.setattr(receipt_intake, "_extract", fail)
    candidate = await stage("retry")
    assert candidate.status == "retry"
    assert candidate.error_code == "TimeoutError"
    async with db() as session:
        row = await session.get(ReceiptCandidate, candidate.id)
        row.next_attempt_at = None
        row.attempts = settings.intake_max_attempts
        await session.commit()
    failed = await receipt_intake.process_candidate(candidate.id, 101)
    assert failed.status == "failed"
    retried = await receipt_intake.decide(candidate.id, 101, "retry", "test")
    assert retried.status == "queued" and retried.attempts == 0
    from src.services.receipt_files import UnsafeReceiptError

    async def unsafe(candidate):
        raise UnsafeReceiptError("scanner_not_configured")

    monkeypatch.setattr(receipt_intake, "_extract", unsafe)
    quarantined = await receipt_intake.process_candidate(candidate.id, 101)
    assert quarantined.status == "quarantined"
    with pytest.raises(receipt_intake.ReviewConflictError):
        await receipt_intake.decide(candidate.id, 101, "accept", "test")


class FakeReceiving:
    def __init__(self):
        self.downloads = 0

    async def get(self, path, params=None):
        if path == "/emails/receiving":
            return {
                "has_more": False,
                "data": [
                    {
                        "id": "mail-1",
                        "from": "owner@example.com",
                        "to": [settings.email_intake_address],
                    },
                    {
                        "id": "bad-auth",
                        "from": "owner@example.com",
                        "to": [settings.email_intake_address],
                    },
                    {
                        "id": "unknown",
                        "from": "stranger@example.net",
                        "to": [settings.email_intake_address],
                    },
                ],
            }
        if path.endswith("/attachments/att-1"):
            return {"download_url": "https://download.resend.com/synthetic"}
        return {
            "id": path.rsplit("/", 1)[-1],
            "from": "owner@example.com",
            "to": [settings.email_intake_address],
            "text": "SYNTHETIC RECEIPT",
            "authentication": {
                "dkim": "pass",
                "dmarc": "fail" if path.endswith("bad-auth") else "pass",
            },
            "attachments": [
                {
                    "id": "att-1",
                    "filename": "receipt.jpg",
                    "content_type": "image/jpeg",
                    "size": len(image_bytes()),
                }
            ],
        }

    async def download(self, url):
        self.downloads += 1
        return image_bytes()


@pytest.mark.asyncio
async def test_poll_replay_sender_auth_and_no_autosave(db):
    provider = FakeReceiving()
    await email_intake.poll_once(provider)
    await email_intake.poll_once(provider)
    assert provider.downloads == 1
    async with db() as session:
        assert await session.scalar(select(func.count()).select_from(ReceiptCandidate)) == 2
        assert await session.scalar(select(func.count()).select_from(Expense)) == 0
        messages = (await session.execute(select(IncomingMessage))).scalars().all()
        assert {row.external_id: row.status for row in messages} == {
            "mail-1": "staged",
            "bad-auth": "quarantined",
            "unknown": "quarantined",
        }
        candidates = (await session.execute(select(ReceiptCandidate))).scalars().all()
        assert sorted(candidate.status for candidate in candidates) == [
            "duplicate_candidate",
            "needs_review",
        ]


@pytest.mark.asyncio
async def test_nonadvancing_pagination_is_visible_failure(db):
    class Broken(FakeReceiving):
        async def get(self, path, params=None):
            result = await super().get(path, params)
            result["has_more"] = True
            return result

    with pytest.raises(email_intake.ReceivingError, match="pagination_did_not_advance"):
        await email_intake.poll_once(Broken())
    async with db() as session:
        status = await session.get(IntakePollState, settings.email_intake_account)
        assert status.last_success_at is None
        assert status.last_error == "ReceivingError"


@pytest.mark.asyncio
async def test_private_review_csrf_owner_and_escaping(db, monkeypatch):
    from src.admin.dependencies import get_db
    from src.admin.routes import intake

    monkeypatch.setattr(settings, "allowed_telegram_users", "101")
    candidate = await stage("web")
    async with db() as session:
        row = await session.get(ReceiptCandidate, candidate.id)
        row.filename = '<script>alert("receipt")</script>'
        await session.commit()

    async def dependency():
        async with db() as session:
            yield session

    app = FastAPI()
    app.include_router(intake.router, prefix="/admin")
    app.dependency_overrides[get_db] = dependency
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://private"
    ) as client:
        page = await client.get("/admin/intake")
        assert page.status_code == 200
        assert "&lt;script&gt;" in page.text
        rejected = await client.post(
            f"/admin/intake/{candidate.id}", data={"action": "accept", "csrf": "bad"}
        )
        assert rejected.status_code == 403
        token = client.cookies["intake_csrf"]
        applied = await client.post(
            f"/admin/intake/{candidate.id}", data={"action": "accept", "csrf": token}
        )
        assert applied.status_code == 303


@pytest.mark.asyncio
async def test_pdf_is_one_candidate_with_multiple_prepared_pages(tmp_path):
    import pypdfium2 as pdfium

    source = tmp_path / "synthetic.pdf"
    document = pdfium.PdfDocument.new()
    document.new_page(200, 300)
    document.new_page(200, 300)
    document.save(str(source))
    document.close()
    async with prepared_images(source, "application/pdf") as images:
        assert len(images) == 2
        assert all(data.startswith(b"\xff\xd8") for data in images)


def test_email_html_and_sender_auth_are_data_only():
    value = email_intake.body_text(
        {"html": '<script>bad()</script><p>Receipt 12.34 EUR</p><img src="http://private/secret">'}
    )
    assert "bad" not in value and "Receipt 12.34 EUR" in value
    assert "http" not in value
    assert not email_intake.authenticated({"headers": {"authentication-results": "dmarc=pass"}})


def test_financial_validation_and_outgoing_key_is_never_fallback(monkeypatch):
    data = parsed().model_dump()
    data["total_amount"] = 1.001
    with pytest.raises(ValueError, match="amount_precision"):
        receipt_intake.expense_data(data, "email")
    monkeypatch.setattr(settings, "resend_receiving_api_key", "")
    monkeypatch.setattr(settings, "resend_api_key", "outgoing-existing-key")
    monkeypatch.setattr(settings, "email_intake_owner_id", 101)
    monkeypatch.setattr(settings, "allowed_telegram_users", "101")
    monkeypatch.setattr(settings, "email_intake_address", "receipts@example.com")
    monkeypatch.setattr(settings, "email_intake_senders", "owner@example.com")
    with pytest.raises(
        email_intake.ReceivingCredentialsError, match="receiving_key_not_configured"
    ):
        email_intake.require_config()


@pytest.mark.asyncio
async def test_transaction_failure_rolls_back_expense_and_source_link(db, monkeypatch):
    candidate = await stage("atomic")
    original = expense_service.create_expense

    async def fail_after_flush(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("simulated_crash_before_source_link")

    monkeypatch.setattr(expense_service, "create_expense", fail_after_flush)
    with pytest.raises(RuntimeError):
        await receipt_intake.decide(candidate.id, 101, "accept", "test")
    async with db() as session:
        assert await session.scalar(select(func.count()).select_from(Expense)) == 0
        assert (await session.get(ReceiptCandidate, candidate.id)).status == "needs_review"
    monkeypatch.setattr(expense_service, "create_expense", original)
    result = await receipt_intake.decide(candidate.id, 101, "accept", "test")
    assert result.status == "committed"


@pytest.mark.asyncio
async def test_telegram_photo_and_duplicate_callback_are_durable(db, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from src.bot import handlers

    monkeypatch.setattr(handlers, "async_session", db)
    message = SimpleNamespace(
        photo=[SimpleNamespace(file_id="synthetic")],
        message_id=11,
        caption=None,
        reply_text=AsyncMock(),
    )
    file = SimpleNamespace(download_as_bytearray=AsyncMock(return_value=image_bytes()))
    context = SimpleNamespace(
        bot=SimpleNamespace(get_file=AsyncMock(return_value=file)), user_data={}
    )
    update = SimpleNamespace(
        message=message,
        effective_user=SimpleNamespace(id=101),
        effective_chat=SimpleNamespace(id=101),
    )
    await handlers.handle_photo(update, context)
    assert "saved (Company)" in message.reply_text.call_args.args[0]
    message.message_id = 12
    await handlers.handle_photo(update, context)
    assert "Possible duplicate" in message.reply_text.call_args.args[0]
    assert context.user_data == {}
    async with db() as session:
        pending = (
            await session.execute(
                select(ReceiptCandidate).where(
                    ReceiptCandidate.status == "duplicate_candidate",
                )
            )
        ).scalar_one()
    # New context simulates a process restart: callback reads the database only.
    query = SimpleNamespace(
        data="dup_save:" + pending.id,
        answer=AsyncMock(),
        edit_message_reply_markup=AsyncMock(),
        message=message,
    )
    update.callback_query = query
    await handlers.handle_callback(update, SimpleNamespace(user_data={}))
    await handlers.handle_callback(update, SimpleNamespace(user_data={}))
    async with db() as session:
        assert await session.scalar(select(func.count()).select_from(Expense)) == 2


@pytest.mark.asyncio
async def test_actual_image_pipeline_calls_existing_parser(db, monkeypatch):
    from unittest.mock import AsyncMock

    from src.services import receipt_parser

    mock = AsyncMock(return_value=(parsed(), parsed().model_dump(mode="json")))
    monkeypatch.setattr(receipt_parser, "parse_receipt", mock)
    monkeypatch.setattr(receipt_intake, "_extract", REAL_EXTRACT)
    candidate = await stage("real-image", "telegram")
    assert candidate.status == "needs_review"
    assert mock.await_count == 1
    assert mock.call_args.args[0].startswith(b"\xff\xd8")
    async with db() as session:
        attempt = (await session.execute(select(IntakeAttempt))).scalar_one()
        assert attempt.status == "completed"
        assert attempt.parsed["total_amount"] == 12.34


@pytest.mark.asyncio
async def test_message_must_be_fully_staged_before_extraction(db):
    async with db() as session:
        row = IncomingMessage(
            id=uuid.uuid4().hex,
            provider="resend",
            account="test",
            external_id="partial",
            owner_id=101,
            sender="owner@example.com",
            recipient=settings.email_intake_address,
            status="quarantined",
            attempts=1,
        )
        session.add(row)
        await session.commit()
    candidate = await receipt_intake.register_source(
        101, "email", "partial-item", image_bytes(), "image/jpeg", message_id=row.id
    )
    untouched = await receipt_intake.process_candidate(candidate.id, 101)
    assert untouched.status == "queued"
    assert untouched.parsed is None


@pytest.mark.asyncio
async def test_cli_file_import_uses_same_review_queue(db, monkeypatch, tmp_path):
    from src import cli

    monkeypatch.setattr(cli, "async_session", db)
    source = tmp_path / "synthetic.jpg"
    source.write_bytes(image_bytes())
    dry = await cli.run(
        cli.parser().parse_args(["ingest-file", str(source), "--owner", "101", "--dry-run"])
    )
    assert dry["dry_run"]
    async with db() as session:
        assert await session.scalar(select(func.count()).select_from(ReceiptCandidate)) == 0
    args = cli.parser().parse_args(["ingest-file", str(source), "--owner", "101"])
    first = await cli.run(args)
    repeat = await cli.run(args)
    assert first["id"] == repeat["id"] and first["status"] == "needs_review"


@pytest.mark.asyncio
async def test_currency_failure_does_not_repeat_expense_insert(db, monkeypatch):
    from decimal import Decimal
    from unittest.mock import AsyncMock

    from src.services import currency_service

    candidate = await stage("foreign")
    async with db() as session:
        row = await session.get(ReceiptCandidate, candidate.id)
        row.parsed = {**row.parsed, "currency": "USD"}
        await session.commit()
    monkeypatch.setattr(currency_service, "convert_to_base", AsyncMock(side_effect=TimeoutError))
    accepted = await receipt_intake.decide(candidate.id, 101, "accept", "test")
    assert accepted.status == "committed"
    async with db() as session:
        expense = await session.get(Expense, accepted.expense_id)
        assert expense.original_currency == "USD" and expense.eur_amount is None
        assert await session.scalar(select(func.count()).select_from(Expense)) == 1

    monkeypatch.setattr(
        currency_service,
        "convert_to_base",
        AsyncMock(return_value=(Decimal("11.11"), Decimal("0.9"))),
    )
    await receipt_intake.convert_saved_expense(accepted.expense_id, 101)
    async with db() as session:
        assert (await session.get(Expense, accepted.expense_id)).eur_amount == Decimal("11.11")
        assert await session.scalar(select(func.count()).select_from(Expense)) == 1


@pytest.mark.asyncio
async def test_message_retry_requires_owner_and_keeps_audit(db):
    async with db() as session:
        row = IncomingMessage(
            id=uuid.uuid4().hex,
            provider="resend",
            account="test",
            external_id="failed-message",
            owner_id=101,
            sender="owner@example.com",
            recipient=settings.email_intake_address,
            status="failed",
            attempts=5,
        )
        session.add(row)
        await session.commit()
    with pytest.raises(ValueError, match="message_not_found"):
        await email_intake.retry_message(row.id, 202, "wrong-owner")
    result = await email_intake.retry_message(row.id, 101, "operator")
    assert result["status"] == "received"
    async with db() as session:
        message = await session.get(IncomingMessage, row.id)
        assert message.attempts == 0
        assert message.operator_actions[-1]["actor"] == "operator"


@pytest.mark.asyncio
async def test_missing_scanner_fails_closed(monkeypatch, tmp_path):
    from src.services.receipt_files import UnsafeReceiptError, scan_file

    monkeypatch.setattr(settings, "intake_scanner_path", "/not-installed/scanner")
    source = tmp_path / "synthetic.txt"
    source.write_text("Synthetic receipt")
    with pytest.raises(UnsafeReceiptError, match="scanner_unavailable"):
        await scan_file(source)


class OneReceiptEmail(FakeReceiving):
    def __init__(self, *, plain=None, html=None, filename="receipt.jpg", inline=False):
        super().__init__()
        self.plain = plain
        self.html = html
        self.filename = filename
        self.inline = inline

    async def get(self, path, params=None):
        result = await super().get(path, params)
        if path == "/emails/receiving":
            result["data"] = result["data"][:1]
        elif path == "/emails/receiving/mail-1":
            result["text"] = self.plain
            result["html"] = self.html
            if self.filename is None:
                result["attachments"] = []
            else:
                result["attachments"][0].update(
                    {
                        "filename": self.filename,
                        "content_disposition": "inline" if self.inline else "attachment",
                        "content_id": "signature-logo" if self.inline else None,
                    }
                )
        return result


@pytest.mark.asyncio
@pytest.mark.parametrize("format,filename", [("text", "signature.png"), ("html", "logo.png")])
async def test_body_receipt_is_not_skipped_for_inline_artwork(db, monkeypatch, format, filename):
    from pathlib import Path

    receipt_text = "Synthetic Cafe\n2026-10-23\nReceipt total EUR 12.34"
    provider = OneReceiptEmail(
        plain=receipt_text if format == "text" else None,
        html=f'<p>{receipt_text}</p><img src="cid:signature-logo">' if format == "html" else None,
        filename=filename,
        inline=True,
    )
    extracted_bodies = []

    async def extract(candidate):
        result = parsed()
        if candidate.mime_type == "text/plain":
            extracted_bodies.append(Path(candidate.receipt_path).read_text())
            return result, result.model_dump(mode="json"), None
        result.vendor = "Signature artwork"
        result.total_amount = 0
        result.confidence = 0.1
        return result, result.model_dump(mode="json"), "a" * 16

    monkeypatch.setattr(receipt_intake, "_extract", extract)
    await email_intake.poll_once(provider)
    await email_intake.poll_once(provider)
    assert len(extracted_bodies) == 1 and "Receipt total EUR 12.34" in extracted_bodies[0]
    async with db() as session:
        rows = (await session.execute(select(ReceiptCandidate))).scalars().all()
        assert len(rows) == 2
        body = next(row for row in rows if row.mime_type == "text/plain")
        assert body.status == "needs_review"
        assert await session.scalar(select(func.count()).select_from(Expense)) == 0
    accepted = await receipt_intake.decide(body.id, 101, "accept", "test")
    assert accepted.status == "committed"


@pytest.mark.asyncio
@pytest.mark.parametrize("first_kind", ["body", "attachment"])
@pytest.mark.parametrize("matching_vendor", [True, False])
async def test_body_and_attachment_do_not_silently_create_two_expenses(
    db,
    monkeypatch,
    first_kind,
    matching_vendor,
):
    provider = OneReceiptEmail(plain="Synthetic Cafe receipt total EUR 12.34")

    async def extract(candidate):
        result = parsed()
        if candidate.mime_type == "text/plain" and not matching_vendor:
            result.vendor = "Different extracted merchant name"
        return (
            result,
            result.model_dump(mode="json"),
            (None if candidate.mime_type == "text/plain" else "a" * 16),
        )

    monkeypatch.setattr(receipt_intake, "_extract", extract)
    await email_intake.poll_once(provider)
    async with db() as session:
        rows = (await session.execute(select(ReceiptCandidate))).scalars().all()
        body = next(row for row in rows if row.mime_type == "text/plain")
        attachment = next(row for row in rows if row.mime_type != "text/plain")
    first, second = (body, attachment) if first_kind == "body" else (attachment, body)
    accepted = await receipt_intake.decide(first.id, 101, "accept", "test")
    # A pending duplicate can block ordinary acceptance in either order; explicitly
    # choose the first evidence while keeping all later acceptance checks active.
    if accepted.status == "duplicate_candidate":
        accepted = await receipt_intake.decide(
            first.id, 101, "save_separate", "test", override_reason="Select primary evidence"
        )
    held = await receipt_intake.decide(second.id, 101, "accept", "test")
    assert held.status == "duplicate_candidate"
    assert held.duplicate_expense_id == accepted.expense_id
    if not matching_vendor:
        assert held.duplicate_reason == "same_email_evidence"
    async with db() as session:
        assert await session.scalar(select(func.count()).select_from(Expense)) == 1
    linked = await receipt_intake.decide(
        second.id, 101, "link", "test", expense_id=accepted.expense_id
    )
    assert linked.status == "linked_duplicate"
    async with db() as session:
        assert await session.scalar(select(func.count()).select_from(Expense)) == 1


@pytest.mark.asyncio
async def test_oversized_html_cannot_create_a_partial_receipt_candidate(db):
    # Before the fix, slicing inside this comment left a plausible subtotal while
    # hiding the real total after the cutoff.
    html = (
        "<p>Receipt subtotal EUR 12.34</p><!--"
        + "x" * 100_100
        + "--><p>Actual final total EUR 99.95</p>"
    )
    provider = OneReceiptEmail(html=html, filename=None)
    await email_intake.poll_once(provider)
    async with db() as session:
        assert await session.scalar(select(func.count()).select_from(ReceiptCandidate)) == 0
        assert await session.scalar(select(func.count()).select_from(Expense)) == 0
        message = (await session.execute(select(IncomingMessage))).scalar_one()
        assert message.status == "quarantined"
        assert message.error_code == "email_html_size_limit"


def test_oversized_data_uri_html_is_rejected_after_decoding():
    import base64

    from src.services.receipt_files import UnsafeReceiptError

    html = "<p>Receipt subtotal EUR 12.34</p><!--" + "x" * 100_100 + "--><p>Total EUR 99.95</p>"
    encoded = "data:text/html;base64," + base64.b64encode(html.encode()).decode()
    with pytest.raises(UnsafeReceiptError, match="email_html_size_limit"):
        email_intake.body_text({"html": encoded})


@pytest.mark.asyncio
async def test_empty_markup_does_not_block_real_attachment(db):
    provider = OneReceiptEmail(plain=" \n", html='<img src="cid:receipt">', inline=True)
    await email_intake.poll_once(provider)
    async with db() as session:
        message = (await session.execute(select(IncomingMessage))).scalar_one()
        assert message.status == "staged"
        candidate = (await session.execute(select(ReceiptCandidate))).scalar_one()
        assert candidate.mime_type == "image/jpeg" and candidate.status == "needs_review"


def test_whitespace_plain_body_uses_complete_html_alternative():
    assert "Total EUR 12.34" in email_intake.body_text(
        {
            "text": " \n",
            "html": "<p>Receipt total</p><p>Total EUR 12.34</p>",
        }
    )
