import logging
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.expense import Expense
from src.schemas.expense import ExpenseCreate, ReportFilter

logger = logging.getLogger(__name__)


async def create_expense(
    session: AsyncSession,
    user_id: int,
    data: ExpenseCreate,
    *,
    receipt_path: str | None = None,
    raw_llm_response: dict | None = None,
    image_hash: str | None = None,
) -> Expense:
    expense = Expense(
        telegram_user_id=user_id,
        vendor=data.vendor,
        description=data.description,
        category=data.category,
        date=data.date,
        original_amount=data.original_amount,
        original_currency=data.original_currency,
        location_city=data.location_city,
        location_country=data.location_country,
        line_items=[item.model_dump() for item in data.line_items] if data.line_items else None,
        source=data.source,
        confidence=data.confidence,
        receipt_path=receipt_path,
        raw_llm_response=raw_llm_response,
        image_hash=image_hash,
    )

    # Set EUR amount if already in EUR
    if data.original_currency == "EUR":
        expense.eur_amount = data.original_amount
        expense.exchange_rate = Decimal("1.000000")

    session.add(expense)
    await session.commit()
    await session.refresh(expense)
    logger.info("Expense #%d created for user %d", expense.id, user_id)
    return expense


async def get_expense(session: AsyncSession, user_id: int, expense_id: int) -> Expense | None:
    result = await session.execute(
        select(Expense).where(
            Expense.id == expense_id,
            Expense.telegram_user_id == user_id,
            Expense.deleted_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def list_expenses(
    session: AsyncSession,
    user_id: int,
    *,
    limit: int = 20,
    offset: int = 0,
    filters: ReportFilter | None = None,
) -> list[Expense]:
    query = select(Expense).where(
        Expense.telegram_user_id == user_id,
        Expense.deleted_at.is_(None),
    )

    if filters:
        if filters.date_from:
            query = query.where(Expense.date >= filters.date_from)
        if filters.date_to:
            query = query.where(Expense.date <= filters.date_to)
        if filters.vendor:
            query = query.where(Expense.vendor.ilike(f"%{filters.vendor}%"))
        if filters.category:
            query = query.where(Expense.category == filters.category)
        if filters.location:
            query = query.where(
                Expense.location_city.ilike(f"%{filters.location}%")
                | Expense.location_country.ilike(f"%{filters.location}%")
            )
        if filters.currency:
            query = query.where(Expense.original_currency == filters.currency.upper())
        if filters.amount_min is not None:
            query = query.where(Expense.original_amount >= filters.amount_min)
        if filters.amount_max is not None:
            query = query.where(Expense.original_amount <= filters.amount_max)
        if filters.expense_type:
            query = query.where(Expense.expense_type == filters.expense_type)

    query = query.order_by(Expense.date.desc(), Expense.id.desc())
    query = query.limit(limit).offset(offset)

    result = await session.execute(query)
    return list(result.scalars().all())


async def get_total(
    session: AsyncSession,
    user_id: int,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Decimal:
    from sqlalchemy import func

    query = select(func.coalesce(func.sum(Expense.original_amount), 0)).where(
        Expense.telegram_user_id == user_id,
        Expense.deleted_at.is_(None),
    )
    if date_from:
        query = query.where(Expense.date >= date_from)
    if date_to:
        query = query.where(Expense.date <= date_to)

    result = await session.execute(query)
    return result.scalar_one()


async def soft_delete_expense(session: AsyncSession, user_id: int, expense_id: int) -> bool:
    expense = await get_expense(session, user_id, expense_id)
    if not expense:
        return False
    expense.deleted_at = datetime.utcnow()
    await session.commit()
    logger.info("Expense #%d soft-deleted for user %d", expense_id, user_id)
    return True


async def update_expense(
    session: AsyncSession, user_id: int, expense_id: int, **fields
) -> Expense | None:
    expense = await get_expense(session, user_id, expense_id)
    if not expense:
        return None
    for key, value in fields.items():
        if hasattr(expense, key):
            setattr(expense, key, value)
    await session.commit()
    await session.refresh(expense)
    return expense


async def nuke_expenses(session: AsyncSession, user_id: int) -> int:
    """Hard-delete ALL expenses for a user. For testing only."""
    from sqlalchemy import delete, func

    count_result = await session.execute(
        select(func.count()).where(Expense.telegram_user_id == user_id)
    )
    count = count_result.scalar_one()
    await session.execute(
        delete(Expense).where(Expense.telegram_user_id == user_id)
    )
    await session.commit()
    logger.warning("NUKED %d expenses for user %d", count, user_id)
    return count
