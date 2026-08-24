import os
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("OPENAI_API_KEY", "test-key")

from src.schemas.expense import ExpenseCreate
from src.services.expense_service import create_expense


async def test_new_telegram_expense_defaults_to_company():
    session = MagicMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    data = ExpenseCreate(
        date=date(2026, 8, 24),
        original_amount=Decimal("12.50"),
        original_currency="EUR",
    )

    expense = await create_expense(session, 123, data)

    assert expense.expense_type == "company"
    session.add.assert_called_once_with(expense)
