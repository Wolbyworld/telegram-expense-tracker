"""add expense_type column

Revision ID: a1b2c3d4e5f6
Revises: 3260039d9602
Create Date: 2026-04-16 00:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '3260039d9602'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('expenses', sa.Column('expense_type', sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column('expenses', 'expense_type')
