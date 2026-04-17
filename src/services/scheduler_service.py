"""Scheduled report management service.

CRUD operations for ScheduledReport records.
APScheduler job execution will be added in a later phase.
"""

import logging
from datetime import time as dt_time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.schedule import ScheduledReport

logger = logging.getLogger(__name__)


async def create_schedule(
    session: AsyncSession,
    user_id: int,
    data: dict,
) -> ScheduledReport:
    """Create a new scheduled report."""
    schedule = ScheduledReport(
        telegram_user_id=user_id,
        frequency=data["frequency"],
        day_of_week=data.get("day_of_week"),
        day_of_month=data.get("day_of_month"),
        time_utc=data["time_utc"] if isinstance(data["time_utc"], dt_time) else dt_time.fromisoformat(data["time_utc"]),
        timezone=data.get("timezone", "Europe/Madrid"),
        email=data.get("email") or None,
        send_telegram=data.get("send_telegram", True),
        filters=data.get("filters"),
        is_active=True,
    )
    session.add(schedule)
    await session.commit()
    await session.refresh(schedule)
    logger.info("Created schedule %d for user %d", schedule.id, user_id)
    return schedule


async def list_schedules(
    session: AsyncSession,
    user_id: int,
) -> list[ScheduledReport]:
    """List all scheduled reports for a user."""
    result = await session.execute(
        select(ScheduledReport)
        .where(ScheduledReport.telegram_user_id == user_id)
        .order_by(ScheduledReport.created_at.desc())
    )
    return list(result.scalars().all())


async def get_schedule(
    session: AsyncSession,
    user_id: int,
    schedule_id: int,
) -> ScheduledReport | None:
    """Get a single scheduled report."""
    result = await session.execute(
        select(ScheduledReport).where(
            ScheduledReport.id == schedule_id,
            ScheduledReport.telegram_user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def update_schedule(
    session: AsyncSession,
    user_id: int,
    schedule_id: int,
    **fields,
) -> ScheduledReport | None:
    """Update fields on a scheduled report."""
    schedule = await get_schedule(session, user_id, schedule_id)
    if schedule is None:
        return None

    for key, value in fields.items():
        if key == "time_utc" and isinstance(value, str):
            value = dt_time.fromisoformat(value)
        if hasattr(schedule, key):
            setattr(schedule, key, value)

    await session.commit()
    await session.refresh(schedule)
    logger.info("Updated schedule %d", schedule_id)
    return schedule


async def delete_schedule(
    session: AsyncSession,
    user_id: int,
    schedule_id: int,
) -> bool:
    """Hard-delete a scheduled report. Returns True if found and deleted."""
    schedule = await get_schedule(session, user_id, schedule_id)
    if schedule is None:
        return False

    await session.delete(schedule)
    await session.commit()
    logger.info("Deleted schedule %d", schedule_id)
    return True


async def toggle_schedule(
    session: AsyncSession,
    user_id: int,
    schedule_id: int,
) -> ScheduledReport | None:
    """Flip is_active on a scheduled report."""
    schedule = await get_schedule(session, user_id, schedule_id)
    if schedule is None:
        return None

    schedule.is_active = not schedule.is_active
    await session.commit()
    await session.refresh(schedule)
    logger.info("Toggled schedule %d -> is_active=%s", schedule_id, schedule.is_active)
    return schedule
