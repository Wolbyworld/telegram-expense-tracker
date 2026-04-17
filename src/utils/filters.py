"""Parse report filter strings from user commands.

Handles filter syntax like:
  /report vendor Uber
  /report category Food
  /report 2026-03-01 to 2026-03-31 category Transport
  /report amount >100
"""

import re
from datetime import date, timedelta
from decimal import Decimal

from src.schemas.expense import ExpenseCategory, ReportFilter


def parse_filters(args: list[str]) -> ReportFilter:
    """Parse a list of command arguments into a ReportFilter."""
    text = " ".join(args)
    filters = ReportFilter()

    # Date range: YYYY-MM-DD to YYYY-MM-DD
    date_match = re.search(r"(\d{4}-\d{2}-\d{2})\s+to\s+(\d{4}-\d{2}-\d{2})", text)
    if date_match:
        filters.date_from = date.fromisoformat(date_match.group(1))
        filters.date_to = date.fromisoformat(date_match.group(2))
        text = text[:date_match.start()] + text[date_match.end():]

    # Quick date periods
    if "today" in text.lower():
        filters.date_from = date.today()
        filters.date_to = date.today()
    elif "this week" in text.lower():
        filters.date_from = date.today() - timedelta(days=date.today().weekday())
        filters.date_to = date.today()
    elif "this month" in text.lower():
        filters.date_from = date.today().replace(day=1)
        filters.date_to = date.today()
    elif match := re.search(r"last (\d+) days?", text.lower()):
        filters.date_from = date.today() - timedelta(days=int(match.group(1)))
        filters.date_to = date.today()

    # Vendor filter
    if match := re.search(r"vendor\s+(\S+)", text, re.IGNORECASE):
        filters.vendor = match.group(1)

    # Category filter
    if match := re.search(r"category\s+(\S+)", text, re.IGNORECASE):
        try:
            filters.category = ExpenseCategory(match.group(1))
        except ValueError:
            pass

    # Location filter
    if match := re.search(r"location\s+(\S+)", text, re.IGNORECASE):
        filters.location = match.group(1)

    # Currency filter
    if match := re.search(r"currency\s+([A-Za-z]{3})", text, re.IGNORECASE):
        filters.currency = match.group(1).upper()

    # Amount filter
    if match := re.search(r"amount\s*>\s*(\d+(?:\.\d+)?)", text):
        filters.amount_min = Decimal(match.group(1))
    elif match := re.search(r"amount\s*<\s*(\d+(?:\.\d+)?)", text):
        filters.amount_max = Decimal(match.group(1))
    elif match := re.search(r"amount\s+(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)", text):
        filters.amount_min = Decimal(match.group(1))
        filters.amount_max = Decimal(match.group(2))

    return filters
