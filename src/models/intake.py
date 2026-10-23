"""Durable source identities, receipt review, and intake audit records."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from src.models.database import Base


class IncomingMessage(Base):
    __tablename__ = "incoming_messages"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    provider: Mapped[str] = mapped_column(String(20))
    account: Mapped[str] = mapped_column(String(100))
    external_id: Mapped[str] = mapped_column(String(255))
    owner_id: Mapped[int] = mapped_column(BigInteger)
    sender: Mapped[str] = mapped_column(String(320), default="")
    recipient: Mapped[str] = mapped_column(String(320), default="")
    status: Mapped[str] = mapped_column(String(30), default="received")
    authentication: Mapped[dict | None] = mapped_column(JSONB)
    operator_actions: Mapped[list | None] = mapped_column(JSONB)
    payload_path: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(100))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_token: Mapped[str | None] = mapped_column(String(32))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
    __table_args__ = (UniqueConstraint("provider", "account", "external_id"),)


class ReceiptCandidate(Base):
    __tablename__ = "receipt_candidates"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_id: Mapped[int] = mapped_column(BigInteger)
    channel: Mapped[str] = mapped_column(String(20))
    source_key: Mapped[str] = mapped_column(String(500))
    message_id: Mapped[str | None] = mapped_column(
        ForeignKey("incoming_messages.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(30), default="queued")
    mime_type: Mapped[str] = mapped_column(String(100))
    filename: Mapped[str] = mapped_column(String(255), default="receipt")
    sha256: Mapped[str] = mapped_column(String(64))
    receipt_path: Mapped[str] = mapped_column(Text)
    image_hash: Mapped[str | None] = mapped_column(String(64))
    parsed: Mapped[dict | None] = mapped_column(JSONB)
    raw_response: Mapped[dict | None] = mapped_column(JSONB)
    duplicate_expense_id: Mapped[int | None] = mapped_column(
        ForeignKey("expenses.id", ondelete="SET NULL")
    )
    duplicate_candidate_id: Mapped[str | None] = mapped_column(String(32))
    duplicate_reason: Mapped[str | None] = mapped_column(String(100))
    expense_id: Mapped[int | None] = mapped_column(ForeignKey("expenses.id", ondelete="SET NULL"))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_token: Mapped[str | None] = mapped_column(String(32))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime)
    error_code: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
    __table_args__ = (
        UniqueConstraint("owner_id", "channel", "source_key"),
        Index("idx_candidates_owner_status", "owner_id", "status"),
        Index("idx_candidates_owner_hash", "owner_id", "sha256"),
    )


class IntakeDecision(Base):
    __tablename__ = "intake_decisions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("receipt_candidates.id"))
    actor: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(40))
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class IntakePollState(Base):
    __tablename__ = "intake_poll_state"
    account: Mapped[str] = mapped_column(String(100), primary_key=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_error: Mapped[str | None] = mapped_column(String(100))
    scanned_messages: Mapped[int] = mapped_column(Integer, default=0)


class IntakeAttempt(Base):
    __tablename__ = "intake_attempts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("receipt_candidates.id"))
    number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30))
    model_name: Mapped[str] = mapped_column(String(100), default="gpt-5.4-mini-2026-03-17")
    parsed: Mapped[dict | None] = mapped_column(JSONB)
    raw_response: Mapped[dict | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
