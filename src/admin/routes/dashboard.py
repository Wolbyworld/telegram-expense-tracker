from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.admin.dependencies import get_db
from src.config import settings
from src.models.expense import Expense

router = APIRouter()
templates = Jinja2Templates(directory="templates")

# Use the first allowed user as the single-user ID
_user_id: int | None = None


def _get_user_id() -> int:
    global _user_id
    if _user_id is None:
        ids = settings.allowed_user_ids
        _user_id = next(iter(ids)) if ids else 0
    return _user_id


@router.get("/")
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    user_id = _get_user_id()
    today = date.today()
    month_start = today.replace(day=1)
    week_start = today - timedelta(days=today.weekday())

    base = select(Expense).where(
        Expense.telegram_user_id == user_id,
        Expense.deleted_at.is_(None),
    )

    # Stats
    month_total = (await db.execute(
        select(func.coalesce(func.sum(Expense.eur_amount), 0))
        .where(Expense.telegram_user_id == user_id, Expense.deleted_at.is_(None), Expense.date >= month_start)
    )).scalar_one()

    week_total = (await db.execute(
        select(func.coalesce(func.sum(Expense.eur_amount), 0))
        .where(Expense.telegram_user_id == user_id, Expense.deleted_at.is_(None), Expense.date >= week_start)
    )).scalar_one()

    expense_count = (await db.execute(
        select(func.count())
        .where(Expense.telegram_user_id == user_id, Expense.deleted_at.is_(None), Expense.date >= month_start)
    )).scalar_one()

    avg_expense = month_total / expense_count if expense_count else Decimal(0)

    # Recent expenses
    recent = (await db.execute(
        base.order_by(Expense.date.desc(), Expense.id.desc()).limit(10)
    )).scalars().all()

    # Category breakdown
    cat_rows = (await db.execute(
        select(Expense.category, func.sum(Expense.eur_amount).label("total"))
        .where(Expense.telegram_user_id == user_id, Expense.deleted_at.is_(None), Expense.date >= month_start)
        .group_by(Expense.category)
        .order_by(func.sum(Expense.eur_amount).desc())
    )).all()
    categories = [{"name": r[0] or "Other", "total": float(r[1] or 0)} for r in cat_rows]
    cat_total = sum(c["total"] for c in categories)
    for c in categories:
        c["pct"] = round(c["total"] / cat_total * 100) if cat_total else 0

    # Currency breakdown
    cur_rows = (await db.execute(
        select(Expense.original_currency, func.sum(Expense.eur_amount).label("eur_total"))
        .where(Expense.telegram_user_id == user_id, Expense.deleted_at.is_(None), Expense.date >= month_start)
        .group_by(Expense.original_currency)
        .order_by(func.sum(Expense.eur_amount).desc())
    )).all()
    currencies = [{"currency": r[0], "eur_total": float(r[1] or 0)} for r in cur_rows]

    # Monthly trend (last 6 months)
    monthly = []
    for i in range(5, -1, -1):
        m_start = (today.replace(day=1) - timedelta(days=30 * i)).replace(day=1)
        if i > 0:
            m_end = (m_start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        else:
            m_end = today
        m_total = (await db.execute(
            select(func.coalesce(func.sum(Expense.eur_amount), 0))
            .where(Expense.telegram_user_id == user_id, Expense.deleted_at.is_(None),
                   Expense.date >= m_start, Expense.date <= m_end)
        )).scalar_one()
        monthly.append({"month": m_start.strftime("%b"), "total": float(m_total)})

    return templates.TemplateResponse(
        request=request,
        name="admin/dashboard.html",
        context={
            "active_page": "dashboard",
            "month_total": month_total,
            "week_total": week_total,
            "expense_count": expense_count,
            "avg_expense": avg_expense,
            "recent": recent,
            "categories": categories,
            "currencies": currencies,
            "monthly": monthly,
        },
    )
