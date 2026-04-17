from datetime import date, timedelta
from decimal import Decimal

from src.schemas.expense import ExpenseCategory
from src.utils.filters import parse_filters


def test_parse_vendor_filter():
    f = parse_filters(["vendor", "Uber"])
    assert f.vendor == "Uber"


def test_parse_category_filter():
    f = parse_filters(["category", "Food"])
    assert f.category == ExpenseCategory.FOOD


def test_parse_amount_greater_than():
    f = parse_filters(["amount", ">100"])
    assert f.amount_min == Decimal("100")
    assert f.amount_max is None


def test_parse_amount_less_than():
    f = parse_filters(["amount", "<50"])
    assert f.amount_max == Decimal("50")
    assert f.amount_min is None


def test_parse_amount_range():
    f = parse_filters(["amount", "10-200"])
    assert f.amount_min == Decimal("10")
    assert f.amount_max == Decimal("200")


def test_parse_currency_filter():
    f = parse_filters(["currency", "usd"])
    assert f.currency == "USD"


def test_parse_location_filter():
    f = parse_filters(["location", "Madrid"])
    assert f.location == "Madrid"


def test_parse_date_range():
    f = parse_filters(["2026-03-01", "to", "2026-03-31"])
    assert f.date_from == date(2026, 3, 1)
    assert f.date_to == date(2026, 3, 31)


def test_parse_last_n_days():
    f = parse_filters(["last", "7", "days"])
    expected = date.today() - timedelta(days=7)
    assert f.date_from == expected
    assert f.date_to == date.today()


def test_parse_combined_filters():
    f = parse_filters([
        "2026-03-01", "to", "2026-03-31",
        "category", "Transport",
        "vendor", "Uber",
    ])
    assert f.date_from == date(2026, 3, 1)
    assert f.date_to == date(2026, 3, 31)
    assert f.category == ExpenseCategory.TRANSPORT
    assert f.vendor == "Uber"


def test_parse_empty_args():
    f = parse_filters([])
    assert f.date_from is None
    assert f.date_to is None
    assert f.vendor is None
    assert f.category is None


def test_parse_invalid_category_ignored():
    f = parse_filters(["category", "InvalidCategory"])
    assert f.category is None
