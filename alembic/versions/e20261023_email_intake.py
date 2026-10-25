"""Add durable receipt intake and review without changing existing expenses.

Revision ID: e20261023
Revises: c4d2a7e91b03
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "e20261023"
down_revision = "c4d2a7e91b03"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "incoming_messages",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("account", sa.String(100), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("sender", sa.String(320), nullable=False),
        sa.Column("recipient", sa.String(320), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("authentication", postgresql.JSONB()),
        sa.Column("operator_actions", postgresql.JSONB()),
        sa.Column("payload_path", sa.Text()),
        sa.Column("error_code", sa.String(100)),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("lease_token", sa.String(32)),
        sa.Column("lease_until", sa.DateTime()),
        sa.Column("next_attempt_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("provider", "account", "external_id"),
    )
    op.create_table(
        "receipt_candidates",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("source_key", sa.String(500), nullable=False),
        sa.Column(
            "message_id", sa.String(32), sa.ForeignKey("incoming_messages.id", ondelete="SET NULL")
        ),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("receipt_path", sa.Text(), nullable=False),
        sa.Column("image_hash", sa.String(64)),
        sa.Column("parsed", postgresql.JSONB()),
        sa.Column("raw_response", postgresql.JSONB()),
        sa.Column(
            "duplicate_expense_id", sa.Integer(), sa.ForeignKey("expenses.id", ondelete="SET NULL")
        ),
        sa.Column("duplicate_candidate_id", sa.String(32)),
        sa.Column("duplicate_reason", sa.String(100)),
        sa.Column("expense_id", sa.Integer(), sa.ForeignKey("expenses.id", ondelete="SET NULL")),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("lease_token", sa.String(32)),
        sa.Column("lease_until", sa.DateTime()),
        sa.Column("next_attempt_at", sa.DateTime()),
        sa.Column("error_code", sa.String(100)),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("owner_id", "channel", "source_key"),
    )
    op.create_index("idx_candidates_owner_status", "receipt_candidates", ["owner_id", "status"])
    op.create_index("idx_candidates_owner_hash", "receipt_candidates", ["owner_id", "sha256"])
    op.create_table(
        "intake_decisions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "candidate_id", sa.String(32), sa.ForeignKey("receipt_candidates.id"), nullable=False
        ),
        sa.Column("actor", sa.String(100), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "intake_poll_state",
        sa.Column("account", sa.String(100), primary_key=True),
        sa.Column("last_success_at", sa.DateTime()),
        sa.Column("last_error", sa.String(100)),
        sa.Column("scanned_messages", sa.Integer(), nullable=False),
    )
    op.create_table(
        "intake_attempts",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "candidate_id", sa.String(32), sa.ForeignKey("receipt_candidates.id"), nullable=False
        ),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("model_name", sa.String(100), nullable=False),
        sa.Column("parsed", postgresql.JSONB()),
        sa.Column("raw_response", postgresql.JSONB()),
        sa.Column("error_code", sa.String(100)),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime()),
    )


def downgrade():
    # Keep intake/audit data on application rollback. Destructive rollback is manual.
    raise RuntimeError(
        "Disable intake and roll back the image; preserve intake tables and audit data"
    )
