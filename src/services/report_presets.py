"""Windows and named presets shared by Reports and Schedules surfaces.

A `window` is a relative date range (e.g. "previous_month"). A `preset` is a
named combination of a window plus a set of report filters (e.g. "Previous
week — company expenses"). Both surfaces resolve the same set of windows
and presets so behavior stays consistent.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta


@dataclass(frozen=True)
class Window:
    key: str
    label: str

    def resolve(self, today: date) -> tuple[date | None, date | None]:
        """Return (date_from, date_to) for this window relative to `today`."""
        return _resolve_window(self.key, today)


def _resolve_window(key: str, today: date) -> tuple[date | None, date | None]:
    if key == "yesterday":
        d = today - timedelta(days=1)
        return d, d
    if key == "last_7_days":
        return today - timedelta(days=7), today - timedelta(days=1)
    if key == "last_30_days":
        return today - timedelta(days=30), today - timedelta(days=1)
    if key == "week_to_date":
        # Monday of this week through yesterday (or today if it's Monday)
        start = today - timedelta(days=today.weekday())
        end = today - timedelta(days=1) if today > start else start
        return start, end
    if key == "previous_week":
        # Previous calendar week (Mon–Sun)
        this_monday = today - timedelta(days=today.weekday())
        return this_monday - timedelta(days=7), this_monday - timedelta(days=1)
    if key == "month_to_date":
        start = today.replace(day=1)
        end = today - timedelta(days=1) if today.day > 1 else start
        return start, end
    if key == "previous_month":
        first_this = today.replace(day=1)
        last_prev = first_this - timedelta(days=1)
        first_prev = last_prev.replace(day=1)
        return first_prev, last_prev
    if key == "year_to_date":
        start = date(today.year, 1, 1)
        end = today - timedelta(days=1) if today > start else start
        return start, end
    if key == "previous_year":
        return date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
    return None, None


WINDOWS: list[Window] = [
    Window("yesterday", "Yesterday"),
    Window("last_7_days", "Last 7 days"),
    Window("last_30_days", "Last 30 days"),
    Window("week_to_date", "Week to date"),
    Window("previous_week", "Previous week"),
    Window("month_to_date", "Month to date"),
    Window("previous_month", "Previous month"),
    Window("year_to_date", "Year to date"),
    Window("previous_year", "Previous year"),
]


def window_label(key: str | None) -> str | None:
    if not key:
        return None
    for w in WINDOWS:
        if w.key == key:
            return w.label
    return None


def resolve(key: str | None, today: date | None = None) -> tuple[date | None, date | None]:
    """Resolve a window key to a (date_from, date_to) tuple."""
    if not key:
        return None, None
    return _resolve_window(key, today or date.today())


@dataclass(frozen=True)
class Preset:
    """A named one-click combination of a window and filter fields."""

    key: str
    label: str
    window: str
    filters: dict = field(default_factory=dict)


PRESETS: list[Preset] = [
    Preset(
        key="last_week_all",
        label="Previous week — all",
        window="previous_week",
        filters={},
    ),
    Preset(
        key="last_week_company",
        label="Previous week — company",
        window="previous_week",
        filters={"expense_type": "company"},
    ),
    Preset(
        key="last_month_all",
        label="Previous month — all",
        window="previous_month",
        filters={},
    ),
    Preset(
        key="last_month_company",
        label="Previous month — company",
        window="previous_month",
        filters={"expense_type": "company"},
    ),
    Preset(
        key="last_month_personal",
        label="Previous month — personal",
        window="previous_month",
        filters={"expense_type": "personal"},
    ),
    Preset(
        key="mtd_company",
        label="Month to date — company",
        window="month_to_date",
        filters={"expense_type": "company"},
    ),
    Preset(
        key="ytd_all",
        label="Year to date — all",
        window="year_to_date",
        filters={},
    ),
]


def get_preset(key: str) -> Preset | None:
    for p in PRESETS:
        if p.key == key:
            return p
    return None


def window_for_frequency(frequency: str) -> str:
    """Default window for a frequency when a schedule has no explicit window."""
    if frequency == "daily":
        return "yesterday"
    if frequency == "weekly":
        return "last_7_days"
    if frequency == "monthly":
        return "last_30_days"
    return "yesterday"
