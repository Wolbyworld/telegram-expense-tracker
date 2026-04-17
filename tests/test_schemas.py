from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.schemas.expense import ExpenseCategory, LineItem, ReceiptParseResult


def test_receipt_parse_result_minimal():
    result = ReceiptParseResult(
        date="2026-03-15",
        total_amount=45.50,
        currency="EUR",
        category=ExpenseCategory.FOOD,
        confidence=0.95,
    )
    assert result.total_amount == 45.50
    assert result.currency == "EUR"
    assert result.vendor is None
    assert result.line_items is None


def test_receipt_parse_result_full():
    result = ReceiptParseResult(
        vendor="Restaurant El Farol",
        date="2026-03-15",
        total_amount=45.50,
        currency="EUR",
        category=ExpenseCategory.FOOD,
        description="Dinner",
        location_city="Madrid",
        location_country="Spain",
        line_items=[
            LineItem(description="Paella", quantity=1, unit_price=18.00, total=18.00),
            LineItem(description="Wine", total=12.50),
        ],
        tax_amount=5.00,
        tip_amount=3.00,
        payment_method="credit_card",
        confidence=0.95,
    )
    assert result.vendor == "Restaurant El Farol"
    assert len(result.line_items) == 2
    assert result.line_items[0].description == "Paella"


def test_receipt_parse_result_invalid_currency_length():
    with pytest.raises(ValidationError):
        ReceiptParseResult(
            date="2026-03-15",
            total_amount=10.0,
            currency="EURO",  # Too long
            category=ExpenseCategory.OTHER,
            confidence=0.5,
        )


def test_receipt_parse_result_confidence_bounds():
    with pytest.raises(ValidationError):
        ReceiptParseResult(
            date="2026-03-15",
            total_amount=10.0,
            currency="EUR",
            category=ExpenseCategory.OTHER,
            confidence=1.5,  # Out of bounds
        )


def test_expense_category_values():
    assert ExpenseCategory.FOOD == "Food"
    assert ExpenseCategory.TRANSPORT == "Transport"
    assert ExpenseCategory.ACCOMMODATION == "Accommodation"


def test_line_item_minimal():
    item = LineItem(description="Coffee", total=3.50)
    assert item.quantity is None
    assert item.unit_price is None
    assert item.total == 3.50
