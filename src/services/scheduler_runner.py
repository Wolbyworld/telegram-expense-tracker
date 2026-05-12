"""APScheduler job runner for ScheduledReport delivery."""

import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from src.bot.telegram_bot import get_bot
from src.models.database import async_session
from src.models.schedule import ScheduledReport
from src.schemas.expense import ExpenseCategory, ReportFilter
from src.services import report_presets
from src.services.email_service import parse_recipients, send_report_email
from src.services.report_service import generate_csv, generate_pdf

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


def get_scheduler() -> AsyncIOScheduler | None:
    return _scheduler


async def start_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = AsyncIOScheduler(timezone="UTC")
    _scheduler.start()
    logger.info("Scheduler started")

    async with async_session() as session:
        result = await session.execute(
            select(ScheduledReport).where(ScheduledReport.is_active.is_(True))
        )
        rows = list(result.scalars().all())
        for sch in rows:
            _schedule_job(sch)
        if rows:
            await session.commit()
    logger.info("Hydrated %d active schedule(s)", len(rows))


async def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is None:
        return
    _scheduler.shutdown(wait=False)
    _scheduler = None
    logger.info("Scheduler stopped")


def _job_id(schedule_id: int) -> str:
    return f"schedule:{schedule_id}"


def _build_trigger(sch: ScheduledReport) -> CronTrigger:
    tz = ZoneInfo(sch.timezone or "UTC")
    hour, minute = sch.time_utc.hour, sch.time_utc.minute
    if sch.frequency == "daily":
        return CronTrigger(hour=hour, minute=minute, timezone=tz)
    if sch.frequency == "weekly":
        return CronTrigger(
            day_of_week=sch.day_of_week if sch.day_of_week is not None else 0,
            hour=hour,
            minute=minute,
            timezone=tz,
        )
    if sch.frequency == "monthly":
        return CronTrigger(
            day=sch.day_of_month or 1, hour=hour, minute=minute, timezone=tz
        )
    raise ValueError(f"Unknown frequency: {sch.frequency}")


def _schedule_job(sch: ScheduledReport) -> None:
    if _scheduler is None:
        return
    trigger = _build_trigger(sch)
    _scheduler.add_job(
        run_schedule,
        trigger=trigger,
        id=_job_id(sch.id),
        args=[sch.id],
        replace_existing=True,
        misfire_grace_time=600,
    )
    job = _scheduler.get_job(_job_id(sch.id))
    if job and job.next_run_time:
        sch.next_run_at = job.next_run_time.replace(tzinfo=None)
    logger.info(
        "Scheduled job %s next=%s", _job_id(sch.id),
        getattr(job, "next_run_time", None),
    )


async def sync_schedule(session, schedule_id: int) -> None:
    """Sync the scheduler with the current DB state for a single schedule."""
    if _scheduler is None:
        return
    sch = await session.get(ScheduledReport, schedule_id)
    if sch is None or not sch.is_active:
        remove(schedule_id)
        if sch is not None and not sch.is_active:
            sch.next_run_at = None
            await session.commit()
        return
    _schedule_job(sch)
    await session.commit()


def remove(schedule_id: int) -> None:
    if _scheduler is None:
        return
    try:
        _scheduler.remove_job(_job_id(schedule_id))
        logger.info("Removed job %s", _job_id(schedule_id))
    except Exception:
        pass


def _build_filter_window(sch: ScheduledReport) -> tuple[date | None, date | None]:
    """Resolve the schedule's window field, falling back to a frequency default."""
    today = datetime.now(ZoneInfo(sch.timezone or "UTC")).date()
    key = sch.window or report_presets.window_for_frequency(sch.frequency)
    return report_presets.resolve(key, today)


def _coerce_decimal(value) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _coerce_category(value) -> ExpenseCategory | None:
    if not value:
        return None
    try:
        return ExpenseCategory(str(value).capitalize())
    except ValueError:
        return None


def _build_report_filter(sch: ScheduledReport) -> ReportFilter:
    filters_dict = sch.filters or {}
    date_from, date_to = _build_filter_window(sch)
    return ReportFilter(
        date_from=date_from,
        date_to=date_to,
        vendor=filters_dict.get("vendor") or None,
        category=_coerce_category(filters_dict.get("category")),
        location=filters_dict.get("location") or None,
        currency=filters_dict.get("currency") or None,
        amount_min=_coerce_decimal(filters_dict.get("amount_min")),
        amount_max=_coerce_decimal(filters_dict.get("amount_max")),
        expense_type=filters_dict.get("expense_type") or None,
    )


def _period_label(rf: ReportFilter) -> str:
    if rf.date_from and rf.date_to:
        if rf.date_from == rf.date_to:
            return rf.date_from.isoformat()
        return f"{rf.date_from.isoformat()}_{rf.date_to.isoformat()}"
    return datetime.utcnow().strftime("%Y%m%d")


async def run_schedule(schedule_id: int) -> dict:
    """Execute one scheduled report. Used by APScheduler and the Send-now route.

    Returns a dict summary: {sent_emails, telegram_sent, recipients, error}.
    """
    summary: dict = {
        "sent_emails": False,
        "telegram_sent": False,
        "recipients": [],
        "error": None,
    }

    async with async_session() as session:
        sch = await session.get(ScheduledReport, schedule_id)
        if sch is None:
            summary["error"] = "schedule not found"
            return summary

        user_id = sch.telegram_user_id
        rf = _build_report_filter(sch)
        logger.info(
            "Running schedule %d user=%d window=%s..%s",
            sch.id, user_id, rf.date_from, rf.date_to,
        )

        try:
            csv_bytes = await generate_csv(session, user_id, rf)
            pdf_bytes = await generate_pdf(session, user_id, rf)
        except Exception as exc:
            logger.exception("Schedule %d: report generation failed", sch.id)
            summary["error"] = f"report generation failed: {exc}"
            return summary

        period = _period_label(rf)
        attachments = [
            (f"expense-report-{period}.pdf", pdf_bytes, "application/pdf"),
            (f"expense-report-{period}.csv", csv_bytes, "text/csv"),
        ]
        subject = f"Expense report — {period}"
        body = (
            f"Attached: expense report for {period}.\n\n"
            f"Generated automatically by your expense tracker."
        )

        recipients = parse_recipients(sch.email)
        summary["recipients"] = recipients
        if recipients:
            try:
                await send_report_email(recipients, subject, body, attachments)
                summary["sent_emails"] = True
            except Exception as exc:
                logger.exception("Schedule %d: email send failed", sch.id)
                summary["error"] = f"email send failed: {exc}"

        if sch.send_telegram:
            bot = get_bot()
            if bot is None:
                logger.info(
                    "Schedule %d: telegram bot not registered, skipping",
                    sch.id,
                )
            else:
                try:
                    await bot.send_document(
                        chat_id=user_id,
                        document=BytesIO(pdf_bytes),
                        filename=f"expense-report-{period}.pdf",
                        caption=f"Scheduled report — {period}",
                    )
                    await bot.send_document(
                        chat_id=user_id,
                        document=BytesIO(csv_bytes),
                        filename=f"expense-report-{period}.csv",
                    )
                    summary["telegram_sent"] = True
                except Exception as exc:
                    logger.exception("Schedule %d: telegram delivery failed", sch.id)
                    if not summary["error"]:
                        summary["error"] = f"telegram failed: {exc}"

        sch.last_run_at = datetime.utcnow()
        if _scheduler is not None:
            job = _scheduler.get_job(_job_id(sch.id))
            if job and job.next_run_time:
                sch.next_run_at = job.next_run_time.replace(tzinfo=None)
        await session.commit()
        logger.info("Schedule %d run complete: %s", sch.id, summary)

    return summary
