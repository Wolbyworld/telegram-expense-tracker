"""Tests for receipt parser response handling (not the API call itself)."""

from src.schemas.expense import ReceiptParseResult


def test_parse_raw_gpt_response():
    """Test parsing a typical GPT-5.4-mini response."""
    raw = {
        "vendor": "Cafe Central",
        "date": "2026-04-05",
        "total_amount": 23.80,
        "currency": "EUR",
        "category": "Food",
        "description": "Coffee and cake",
        "location_city": "Vienna",
        "location_country": "Austria",
        "line_items": [
            {"description": "Cappuccino", "quantity": 2, "unit_price": 4.90, "total": 9.80},
            {"description": "Sachertorte", "quantity": 1, "unit_price": 7.50, "total": 7.50},
            {"description": "Apfelstrudel", "total": 6.50},
        ],
        "tax_amount": None,
        "tip_amount": None,
        "payment_method": "cash",
        "confidence": 0.92,
    }

    result = ReceiptParseResult.model_validate(raw)
    assert result.vendor == "Cafe Central"
    assert result.total_amount == 23.80
    assert result.currency == "EUR"
    assert result.category == "Food"
    assert result.location_city == "Vienna"
    assert len(result.line_items) == 3
    assert result.confidence == 0.92


def test_parse_minimal_gpt_response():
    """Test parsing a response with only required fields."""
    raw = {
        "vendor": None,
        "date": "2026-04-01",
        "total_amount": 15.00,
        "currency": "USD",
        "category": "Other",
        "description": None,
        "location_city": None,
        "location_country": None,
        "line_items": None,
        "tax_amount": None,
        "tip_amount": None,
        "payment_method": None,
        "confidence": 0.45,
    }

    result = ReceiptParseResult.model_validate(raw)
    assert result.vendor is None
    assert result.total_amount == 15.00
    assert result.line_items is None
    assert result.confidence == 0.45


def test_parse_multi_currency_receipt():
    """Test a receipt in a non-EUR currency."""
    raw = {
        "vendor": "Yellow Cab",
        "date": "2026-03-20",
        "total_amount": 52.30,
        "currency": "USD",
        "category": "Transport",
        "description": "JFK to Manhattan",
        "location_city": "New York",
        "location_country": "US",
        "line_items": None,
        "tax_amount": None,
        "tip_amount": 8.00,
        "payment_method": "credit_card",
        "confidence": 0.88,
    }

    result = ReceiptParseResult.model_validate(raw)
    assert result.currency == "USD"
    assert result.tip_amount == 8.00
    assert result.location_country == "US"
