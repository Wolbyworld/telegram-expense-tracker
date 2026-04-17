"""Report generation service — PDF + CSV."""

import base64
import csv
import io
import logging
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.schemas.expense import ReportFilter
from src.services.expense_service import list_expenses

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).parent.parent.parent / "templates"
_jinja_env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=True)


async def generate_csv(
    session: AsyncSession,
    user_id: int,
    filters: ReportFilter | None = None,
) -> bytes:
    """Generate a CSV report of expenses."""
    expenses = await list_expenses(session, user_id, limit=10000, filters=filters)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "id", "date", "vendor", "category", "expense_type", "description",
        "original_amount", "original_currency", "eur_amount",
        "location_city", "location_country", "source",
    ])
    for e in expenses:
        writer.writerow([
            e.id, e.date, e.vendor, e.category, e.expense_type, e.description,
            e.original_amount, e.original_currency, e.eur_amount,
            e.location_city, e.location_country, e.source,
        ])

    return output.getvalue().encode("utf-8")


def _build_template_context(expenses, filters: ReportFilter | None) -> dict:
    """Build the Jinja2 template context from expenses and filters."""
    total_eur = sum(
        (e.eur_amount or e.original_amount) for e in expenses
    )

    # Top category
    cat_counter: Counter[str] = Counter()
    cat_totals: defaultdict[str, Decimal] = defaultdict(Decimal)
    for e in expenses:
        cat = e.category or "Other"
        cat_counter[cat] += 1
        cat_totals[cat] += e.eur_amount or e.original_amount

    top_category = cat_counter.most_common(1)[0][0] if cat_counter else None

    # Category breakdown
    category_breakdown = []
    for cat, total in sorted(cat_totals.items(), key=lambda x: x[1], reverse=True):
        pct = float(total / total_eur * 100) if total_eur else 0
        category_breakdown.append({"name": cat, "total": total, "pct": pct})

    # Top location
    loc_counter: Counter[str] = Counter()
    for e in expenses:
        loc = e.location_city or e.location_country
        if loc:
            loc_counter[loc] += 1
    top_location = loc_counter.most_common(1)[0][0] if loc_counter else None

    # Currency breakdown
    cur_totals: defaultdict[str, dict] = defaultdict(lambda: {"original": Decimal(0), "eur": Decimal(0)})
    for e in expenses:
        cur_totals[e.original_currency]["original"] += e.original_amount
        cur_totals[e.original_currency]["eur"] += e.eur_amount or e.original_amount

    currency_breakdown = [
        {"currency": cur, "original_total": d["original"], "eur_total": d["eur"]}
        for cur, d in sorted(cur_totals.items())
    ]

    # Date range
    if filters and filters.date_from:
        date_from = filters.date_from.isoformat()
    elif expenses:
        date_from = min(e.date for e in expenses).isoformat()
    else:
        date_from = "—"

    if filters and filters.date_to:
        date_to = filters.date_to.isoformat()
    elif expenses:
        date_to = max(e.date for e in expenses).isoformat()
    else:
        date_to = "—"

    return {
        "expenses": expenses,
        "total_eur": total_eur,
        "base_currency": settings.base_currency,
        "top_category": top_category,
        "top_location": top_location,
        "category_breakdown": category_breakdown,
        "currency_breakdown": currency_breakdown if len(currency_breakdown) > 1 else [],
        "date_from": date_from,
        "date_to": date_to,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "generated_for": None,
    }


def _build_receipt_data(expenses) -> list[dict]:
    """Build receipt image data for the PDF appendix."""
    receipts = []
    for e in expenses:
        if not e.receipt_path:
            continue
        path = Path(e.receipt_path)
        if not path.exists():
            continue
        try:
            image_bytes = path.read_bytes()
            b64 = base64.b64encode(image_bytes).decode()
            suffix = path.suffix.lstrip(".").lower()
            mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png"}.get(suffix, "jpeg")
            receipts.append({
                "id": e.id,
                "vendor": e.vendor,
                "date": e.date,
                "amount": e.original_amount,
                "currency": e.original_currency,
                "image_uri": f"data:image/{mime};base64,{b64}",
            })
        except Exception:
            logger.warning("Could not read receipt image: %s", e.receipt_path)
    return receipts


async def generate_pdf(
    session: AsyncSession,
    user_id: int,
    filters: ReportFilter | None = None,
) -> bytes:
    """Generate a PDF report of expenses with receipt appendix using WeasyPrint."""
    from weasyprint import HTML  # Lazy import — native libs required

    expenses = await list_expenses(session, user_id, limit=10000, filters=filters)
    template = _jinja_env.get_template("report.html")

    if not expenses:
        html_str = template.render(
            expenses=[], total_eur=0, base_currency=settings.base_currency,
            top_category=None, top_location=None, category_breakdown=[],
            currency_breakdown=[], date_from="—", date_to="—",
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
            generated_for=None, receipts=[],
        )
        return HTML(string=html_str).write_pdf()

    ctx = _build_template_context(expenses, filters)
    ctx["receipts"] = _build_receipt_data(expenses)
    html_str = template.render(**ctx)
    return HTML(string=html_str).write_pdf()
