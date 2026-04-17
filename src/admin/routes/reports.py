"""Admin reports routes — NL query, preview, PDF/CSV generation."""

import logging
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from src.admin.dependencies import get_db
from src.config import settings
from src.models.expense import Expense
from src.schemas.expense import ExpenseCategory, ReportFilter
from src.services.expense_service import list_expenses

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="templates")

_user_id: int | None = None


def _get_user_id() -> int:
    global _user_id
    if _user_id is None:
        ids = settings.allowed_user_ids
        _user_id = next(iter(ids)) if ids else 0
    return _user_id


def _parse_filters(form: dict) -> ReportFilter:
    """Build a ReportFilter from submitted form data."""
    kwargs: dict = {}

    if form.get("date_from"):
        kwargs["date_from"] = date.fromisoformat(form["date_from"])
    if form.get("date_to"):
        kwargs["date_to"] = date.fromisoformat(form["date_to"])
    if form.get("expense_type") and form["expense_type"] != "all":
        kwargs["expense_type"] = form["expense_type"]
    if form.get("category"):
        kwargs["category"] = form["category"]
    if form.get("vendor"):
        kwargs["vendor"] = form["vendor"]
    if form.get("location"):
        kwargs["location"] = form["location"]
    if form.get("currency") and form["currency"] != "all":
        kwargs["currency"] = form["currency"]
    if form.get("amount_min"):
        kwargs["amount_min"] = Decimal(form["amount_min"])
    if form.get("amount_max"):
        kwargs["amount_max"] = Decimal(form["amount_max"])

    return ReportFilter(**kwargs)


async def _compute_preview(db: AsyncSession, filters: ReportFilter) -> dict:
    """Compute preview statistics for the given filters."""
    user_id = _get_user_id()
    expenses = await list_expenses(db, user_id, limit=10000, filters=filters)

    total = sum((e.eur_amount or e.original_amount) for e in expenses)
    count = len(expenses)

    cat_counter: Counter[str] = Counter()
    cat_totals: defaultdict[str, Decimal] = defaultdict(Decimal)
    for e in expenses:
        cat = e.category or "Other"
        cat_counter[cat] += 1
        cat_totals[cat] += e.eur_amount or e.original_amount

    top_category = cat_counter.most_common(1)[0][0] if cat_counter else None
    top_category_amount = cat_totals[top_category] if top_category else Decimal(0)

    # Category distribution for chart
    categories = []
    for cat, cat_total in sorted(cat_totals.items(), key=lambda x: x[1], reverse=True):
        pct = float(cat_total / total * 100) if total else 0
        categories.append({"name": cat, "total": float(cat_total), "pct": round(pct, 1)})

    return {
        "total": total,
        "count": count,
        "top_category": top_category,
        "top_category_amount": top_category_amount,
        "categories": categories,
    }


@router.get("/reports")
async def reports_page(request: Request):
    """Full reports page."""
    category_values = [c.value for c in ExpenseCategory]
    return templates.TemplateResponse(
        request=request,
        name="admin/reports/index.html",
        context={
            "active_page": "reports",
            "categories": category_values,
            "today": date.today().isoformat(),
        },
    )


@router.post("/reports/nl-query")
async def nl_query(request: Request, db: AsyncSession = Depends(get_db)):
    """Resolve natural language query into filter form fields."""
    form = await request.form()
    query_text = form.get("query", "").strip()

    if not query_text:
        return templates.TemplateResponse(
            request=request,
            name="admin/reports/_filter_form.html",
            context={
                "categories": [c.value for c in ExpenseCategory],
                "nl_error": "Please enter a query.",
            },
        )

    try:
        from src.services.report_agent import resolve_filter
        filters, summary = await resolve_filter(query_text)
    except Exception as exc:
        logger.exception("NL query resolution failed: %s", exc)
        return templates.TemplateResponse(
            request=request,
            name="admin/reports/_filter_form.html",
            context={
                "categories": [c.value for c in ExpenseCategory],
                "nl_error": f"Could not resolve query: {exc}",
            },
        )

    # Compute preview with resolved filters
    preview = await _compute_preview(db, filters)

    return templates.TemplateResponse(
        request=request,
        name="admin/reports/_filter_form.html",
        context={
            "categories": [c.value for c in ExpenseCategory],
            "filters": filters,
            "nl_summary": summary,
            "preview": preview,
        },
    )


@router.post("/reports/preview")
async def preview(request: Request, db: AsyncSession = Depends(get_db)):
    """Return preview summary partial based on current filter state."""
    form_data = await request.form()
    form = dict(form_data)
    filters = _parse_filters(form)
    preview = await _compute_preview(db, filters)

    return templates.TemplateResponse(
        request=request,
        name="admin/reports/_preview.html",
        context={"preview": preview},
    )


@router.post("/reports/generate")
async def generate_report(request: Request, db: AsyncSession = Depends(get_db)):
    """Generate and download PDF or CSV report."""
    form_data = await request.form()
    form = dict(form_data)
    fmt = form.pop("format", "pdf")
    filters = _parse_filters(form)
    user_id = _get_user_id()

    if fmt == "csv":
        from src.services.report_service import generate_csv
        data = await generate_csv(db, user_id, filters=filters)
        filename = f"expenses_{date.today().isoformat()}.csv"
        return Response(
            content=data,
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    else:
        from src.services.report_service import generate_pdf
        data = await generate_pdf(db, user_id, filters=filters)
        filename = f"expenses_{date.today().isoformat()}.pdf"
        return Response(
            content=data,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
