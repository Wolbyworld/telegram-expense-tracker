"""add window column to scheduled_reports

Revision ID: c4d2a7e91b03
Revises: a1b2c3d4e5f6
Create Date: 2026-05-11 16:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c4d2a7e91b03"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "scheduled_reports",
        sa.Column("window", sa.String(length=30), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("scheduled_reports", "window")
