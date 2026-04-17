import csv
import io
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.admin.dependencies import get_db
from src.config import settings
from src.models.expense import Expense
from src.schemas.expense import ExpenseCategory, ReportFilter
from src.services import expense_service

router = APIRouter(prefix="/expenses")
templates = Jinja2Templates(directory="templates")

_user_id: int | None = None


def _get_user_id() -> int:
    global _user_id
    if _user_id is None:
        ids = settings.allowed_user_ids
        _user_id = next(iter(ids)) if ids else 0
    return _user_id


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _build_filters(
    date_from: str | None,
    date_to: str | None,
    category: str | None,
    expense_type: str | None,
    vendor: str | None,
) -> ReportFilter:
    cat = None
    if category and category != "all":
        try:
            cat = ExpenseCategory(category)
        except ValueError:
            pass

    exp_type = None
    if expense_type and expense_type not in ("all", ""):
        exp_type = expense_type

    return ReportFilter(
        date_from=_parse_date(date_from),
        date_to=_parse_date(date_to),
        category=cat,
        expense_type=exp_type,
        vendor=vendor if vendor else None,
    )


def _build_query(user_id: int, filters: ReportFilter):
    """Build the base filtered query (no limit/offset/ordering)."""
    query = select(Expense).where(
        Expense.telegram_user_id == user_id,
        Expense.deleted_at.is_(None),
    )
    if filters.date_from:
        query = query.where(Expense.date >= filters.date_from)
    if filters.date_to:
        query = query.where(Expense.date <= filters.date_to)
    if filters.vendor:
        query = query.where(Expense.vendor.ilike(f"%{filters.vendor}%"))
    if filters.category:
        query = query.where(Expense.category == filters.category)
    if filters.expense_type:
        query = query.where(Expense.expense_type == filters.expense_type)
    return query


SORT_COLUMNS = {
    "date": (Expense.date, Expense.id),
    "amount": (Expense.eur_amount,),
    "vendor": (Expense.vendor,),
    "category": (Expense.category,),
}


@router.get("")
async def expenses_page(request: Request, db: AsyncSession = Depends(get_db)):
    user_id = _get_user_id()

    # Total count for subtitle
    total_count = (
        await db.execute(
            select(func.count()).where(
                Expense.telegram_user_id == user_id,
                Expense.deleted_at.is_(None),
            )
        )
    ).scalar_one()

    categories = list(ExpenseCategory)

    return templates.TemplateResponse(
        request=request,
        name="admin/expenses/index.html",
        context={
            "active_page": "expenses",
            "total_count": total_count,
            "categories": categories,
        },
    )


@router.get("/table")
async def expenses_table(
    request: Request,
    db: AsyncSession = Depends(get_db),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    category: str | None = Query(None),
    expense_type: str | None = Query(None),
    vendor: str | None = Query(None),
    sort: str = Query("date"),
    dir: str = Query("desc"),
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=5, le=100),
):
    user_id = _get_user_id()
    filters = _build_filters(date_from, date_to, category, expense_type, vendor)

    # Count
    count_query = select(func.count()).select_from(
        _build_query(user_id, filters).subquery()
    )
    total = (await db.execute(count_query)).scalar_one()

    # Sorting
    sort_cols = SORT_COLUMNS.get(sort, SORT_COLUMNS["date"])
    if dir == "asc":
        order = [col.asc() for col in sort_cols]
    else:
        order = [col.desc() for col in sort_cols]

    # Fetch
    offset = (page - 1) * per_page
    data_query = (
        _build_query(user_id, filters).order_by(*order).limit(per_page).offset(offset)
    )
    expenses = (await db.execute(data_query)).scalars().all()

    total_pages = max(1, (total + per_page - 1) // per_page)

    return templates.TemplateResponse(
        request=request,
        name="admin/expenses/_table.html",
        context={
            "expenses": expenses,
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": total_pages,
            "sort": sort,
            "dir": dir,
            "date_from": date_from or "",
            "date_to": date_to or "",
            "category": category or "",
            "expense_type": expense_type or "",
            "vendor": vendor or "",
        },
    )


@router.get("/export")
async def expenses_export(
    db: AsyncSession = Depends(get_db),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    category: str | None = Query(None),
    expense_type: str | None = Query(None),
    vendor: str | None = Query(None),
):
    user_id = _get_user_id()
    filters = _build_filters(date_from, date_to, category, expense_type, vendor)

    data_query = _build_query(user_id, filters).order_by(
        Expense.date.desc(), Expense.id.desc()
    )
    expenses = (await db.execute(data_query)).scalars().all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "ID",
            "Date",
            "Vendor",
            "Category",
            "Description",
            "Amount",
            "Currency",
            "EUR Amount",
            "Type",
            "City",
            "Country",
        ]
    )
    for e in expenses:
        writer.writerow(
            [
                e.id,
                e.date.isoformat(),
                e.vendor or "",
                e.category or "",
                e.description or "",
                str(e.original_amount),
                e.original_currency,
                str(e.eur_amount) if e.eur_amount else "",
                e.expense_type or "",
                e.location_city or "",
                e.location_country or "",
            ]
        )

    output.seek(0)
    filename = f"expenses_{date.today().isoformat()}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/{expense_id}")
async def expense_detail(
    request: Request,
    expense_id: int,
    db: AsyncSession = Depends(get_db),
):
    user_id = _get_user_id()
    expense = await expense_service.get_expense(db, user_id, expense_id)
    if not expense:
        return templates.TemplateResponse(
            request=request,
            name="admin/expenses/detail.html",
            context={"expense": None},
            status_code=404,
        )
    return templates.TemplateResponse(
        request=request,
        name="admin/expenses/detail.html",
        context={"expense": expense},
    )


@router.get("/{expense_id}/edit")
async def expense_edit_row(
    request: Request,
    expense_id: int,
    db: AsyncSession = Depends(get_db),
):
    user_id = _get_user_id()
    expense = await expense_service.get_expense(db, user_id, expense_id)
    categories = list(ExpenseCategory)
    return templates.TemplateResponse(
        request=request,
        name="admin/expenses/_edit_row.html",
        context={"expense": expense, "categories": categories},
    )


@router.patch("/{expense_id}")
async def expense_update(
    request: Request,
    expense_id: int,
    db: AsyncSession = Depends(get_db),
):
    user_id = _get_user_id()

    # Support both form data and JSON (hx-vals sends as form-encoded)
    form = await request.form()

    fields: dict = {}
    if form.get("vendor"):
        fields["vendor"] = str(form["vendor"])
    if form.get("category"):
        fields["category"] = str(form["category"])
    if "description" in form:
        fields["description"] = str(form["description"]) or None
    if form.get("expense_type"):
        val = str(form["expense_type"])
        fields["expense_type"] = val if val != "none" else None

    expense = await expense_service.update_expense(db, user_id, expense_id, **fields)
    return templates.TemplateResponse(
        request=request,
        name="admin/expenses/_row.html",
        context={"expense": expense},
    )


@router.delete("/{expense_id}")
async def expense_delete(
    request: Request,
    expense_id: int,
    db: AsyncSession = Depends(get_db),
):
    user_id = _get_user_id()
    await expense_service.soft_delete_expense(db, user_id, expense_id)
    return ""


@router.get("/{expense_id}/receipt")
async def expense_receipt(
    expense_id: int,
    raw: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    user_id = _get_user_id()
    expense = await expense_service.get_expense(db, user_id, expense_id)
    if not expense or not expense.receipt_path:
        return StreamingResponse(
            iter([b""]),
            status_code=404,
        )
    # Prefer enhanced version unless ?raw=true
    if not raw:
        from pathlib import Path
        raw_path = Path(expense.receipt_path)
        enhanced_path = raw_path.parent / "enhanced" / raw_path.name
        if enhanced_path.exists():
            return FileResponse(str(enhanced_path))
    return FileResponse(expense.receipt_path)
