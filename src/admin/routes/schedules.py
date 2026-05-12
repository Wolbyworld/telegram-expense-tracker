import json
import logging
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from src.admin.dependencies import get_db
from src.config import settings
from src.services import report_presets, scheduler_runner
from src.services.email_service import parse_recipients
from src.services.scheduler_service import (
    create_schedule,
    delete_schedule,
    get_schedule,
    list_schedules,
    toggle_schedule,
    update_schedule,
)

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


def _canonicalize_email(email: str, email_enabled: bool) -> str | None:
    if not email_enabled:
        return None
    addrs = parse_recipients(email)
    return ", ".join(addrs) if addrs else None


def _parse_filters_json(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    # Whitelist + light coercion
    allowed = {"vendor", "category", "location", "currency", "amount_min",
               "amount_max", "expense_type"}
    out: dict = {}
    for k in allowed:
        v = data.get(k)
        if v in (None, ""):
            continue
        if k in ("amount_min", "amount_max"):
            try:
                out[k] = str(Decimal(str(v)))
            except (InvalidOperation, ValueError):
                continue
        else:
            out[k] = str(v)
    return out


def _filter_chips(filters: dict | None, window_key: str | None) -> list[dict]:
    """Render a flat list of (label, icon) chips for read-only display."""
    chips: list[dict] = []
    if window_key:
        label = report_presets.window_label(window_key)
        if label:
            chips.append({"icon": "calendar_month", "label": label})
    filters = filters or {}
    if filters.get("expense_type"):
        chips.append({"icon": "category", "label": str(filters["expense_type"]).capitalize()})
    if filters.get("category"):
        chips.append({"icon": "label", "label": filters["category"]})
    if filters.get("vendor"):
        chips.append({"icon": "storefront", "label": filters["vendor"]})
    if filters.get("location"):
        chips.append({"icon": "place", "label": filters["location"]})
    if filters.get("currency"):
        chips.append({"icon": "payments", "label": filters["currency"]})
    if filters.get("amount_min") or filters.get("amount_max"):
        lo = filters.get("amount_min") or "0"
        hi = filters.get("amount_max") or "∞"
        chips.append({"icon": "euro", "label": f"{lo} – {hi}"})
    return chips


def _common_context() -> dict:
    return {
        "windows": report_presets.WINDOWS,
        "presets": report_presets.PRESETS,
        "filter_chips": _filter_chips,
    }


@router.get("/schedules")
async def schedules_page(request: Request, db: AsyncSession = Depends(get_db)):
    user_id = _get_user_id()
    all_schedules = await list_schedules(db, user_id)
    active_count = sum(1 for s in all_schedules if s.is_active)

    return templates.TemplateResponse(
        request=request,
        name="admin/schedules/index.html",
        context={
            "active_page": "schedules",
            "schedules": all_schedules,
            "active_count": active_count,
            **_common_context(),
        },
    )


@router.post("/schedules")
async def create_schedule_route(
    request: Request,
    db: AsyncSession = Depends(get_db),
    frequency: str = Form(...),
    day_of_week: int | None = Form(None),
    day_of_month: int | None = Form(None),
    time_utc: str = Form("09:00"),
    timezone: str = Form("Europe/Madrid"),
    window: str = Form(""),
    filters_json: str = Form(""),
    send_telegram: bool = Form(False),
    email_enabled: bool = Form(False),
    email: str = Form(""),
):
    user_id = _get_user_id()

    data = {
        "frequency": frequency,
        "day_of_week": day_of_week if frequency == "weekly" else None,
        "day_of_month": day_of_month if frequency == "monthly" else None,
        "time_utc": time_utc,
        "timezone": timezone,
        "window": window or None,
        "send_telegram": send_telegram,
        "email": _canonicalize_email(email, email_enabled),
        "filters": _parse_filters_json(filters_json) or None,
    }

    sch = await create_schedule(db, user_id, data)
    await scheduler_runner.sync_schedule(db, sch.id)
    all_schedules = await list_schedules(db, user_id)

    return templates.TemplateResponse(
        request=request,
        name="admin/schedules/_list.html",
        context={
            "schedules": all_schedules,
            **_common_context(),
        },
    )


@router.get("/schedules/{schedule_id}/edit")
async def edit_schedule_form(
    request: Request,
    schedule_id: int,
    db: AsyncSession = Depends(get_db),
):
    user_id = _get_user_id()
    schedule = await get_schedule(db, user_id, schedule_id)
    if schedule is None:
        return Response(status_code=404)

    return templates.TemplateResponse(
        request=request,
        name="admin/schedules/_form.html",
        context={
            "schedule": schedule,
            **_common_context(),
        },
    )


@router.patch("/schedules/{schedule_id}")
async def update_schedule_route(
    request: Request,
    schedule_id: int,
    db: AsyncSession = Depends(get_db),
    frequency: str = Form(...),
    day_of_week: int | None = Form(None),
    day_of_month: int | None = Form(None),
    time_utc: str = Form("09:00"),
    timezone: str = Form("Europe/Madrid"),
    window: str = Form(""),
    send_telegram: bool = Form(False),
    email_enabled: bool = Form(False),
    email: str = Form(""),
):
    user_id = _get_user_id()

    fields = {
        "frequency": frequency,
        "day_of_week": day_of_week if frequency == "weekly" else None,
        "day_of_month": day_of_month if frequency == "monthly" else None,
        "time_utc": time_utc,
        "timezone": timezone,
        "window": window or None,
        "send_telegram": send_telegram,
        "email": _canonicalize_email(email, email_enabled),
    }

    schedule = await update_schedule(db, user_id, schedule_id, **fields)
    if schedule is None:
        return Response(status_code=404)

    await scheduler_runner.sync_schedule(db, schedule.id)
    all_schedules = await list_schedules(db, user_id)
    return templates.TemplateResponse(
        request=request,
        name="admin/schedules/_list.html",
        context={
            "schedules": all_schedules,
            **_common_context(),
        },
    )


@router.delete("/schedules/{schedule_id}")
async def delete_schedule_route(
    schedule_id: int,
    db: AsyncSession = Depends(get_db),
):
    user_id = _get_user_id()
    deleted = await delete_schedule(db, user_id, schedule_id)
    if not deleted:
        return Response(status_code=404)
    scheduler_runner.remove(schedule_id)
    return Response(status_code=200, content="")


@router.post("/schedules/{schedule_id}/toggle")
async def toggle_schedule_route(
    request: Request,
    schedule_id: int,
    db: AsyncSession = Depends(get_db),
):
    user_id = _get_user_id()
    schedule = await toggle_schedule(db, user_id, schedule_id)
    if schedule is None:
        return Response(status_code=404)

    await scheduler_runner.sync_schedule(db, schedule.id)
    all_schedules = await list_schedules(db, user_id)
    return templates.TemplateResponse(
        request=request,
        name="admin/schedules/_list.html",
        context={
            "schedules": all_schedules,
            **_common_context(),
        },
    )


@router.post("/schedules/{schedule_id}/run")
async def run_schedule_now(
    request: Request,
    schedule_id: int,
    db: AsyncSession = Depends(get_db),
):
    user_id = _get_user_id()
    schedule = await get_schedule(db, user_id, schedule_id)
    if schedule is None:
        return Response(status_code=404)

    summary = await scheduler_runner.run_schedule(schedule_id)

    if summary.get("error"):
        toast = f"Run failed: {summary['error']}"
    else:
        bits = []
        if summary.get("sent_emails"):
            bits.append(f"emailed {len(summary['recipients'])}")
        if summary.get("telegram_sent"):
            bits.append("sent to Telegram")
        toast = "Sent — " + (", ".join(bits) if bits else "no delivery method configured")

    all_schedules = await list_schedules(db, user_id)
    return templates.TemplateResponse(
        request=request,
        name="admin/schedules/_list.html",
        context={
            "schedules": all_schedules,
            "toast": toast,
            **_common_context(),
        },
    )


@router.post("/schedules/from-preset")
async def create_from_preset(
    request: Request,
    db: AsyncSession = Depends(get_db),
    preset_key: str = Form(...),
    frequency: str = Form(...),
    day_of_week: int | None = Form(None),
    day_of_month: int | None = Form(None),
    time_utc: str = Form("09:00"),
    timezone: str = Form("Europe/Madrid"),
    send_telegram: bool = Form(False),
    email_enabled: bool = Form(False),
    email: str = Form(""),
):
    """Create a schedule from a named preset (window + filters baked in)."""
    user_id = _get_user_id()
    preset = report_presets.get_preset(preset_key)
    if preset is None:
        return Response(status_code=400, content=f"Unknown preset: {preset_key}")

    data = {
        "frequency": frequency,
        "day_of_week": day_of_week if frequency == "weekly" else None,
        "day_of_month": day_of_month if frequency == "monthly" else None,
        "time_utc": time_utc,
        "timezone": timezone,
        "window": preset.window,
        "send_telegram": send_telegram,
        "email": _canonicalize_email(email, email_enabled),
        "filters": dict(preset.filters) or None,
    }
    sch = await create_schedule(db, user_id, data)
    await scheduler_runner.sync_schedule(db, sch.id)

    all_schedules = await list_schedules(db, user_id)
    return templates.TemplateResponse(
        request=request,
        name="admin/schedules/_list.html",
        context={
            "schedules": all_schedules,
            "toast": f"Schedule created from preset '{preset.label}'",
            **_common_context(),
        },
    )
