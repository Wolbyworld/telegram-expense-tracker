from datetime import time as dt_time

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from src.admin.dependencies import get_db
from src.config import settings
from src.services.scheduler_service import (
    create_schedule,
    delete_schedule,
    get_schedule,
    list_schedules,
    toggle_schedule,
    update_schedule,
)

router = APIRouter()
templates = Jinja2Templates(directory="templates")

_user_id: int | None = None


def _get_user_id() -> int:
    global _user_id
    if _user_id is None:
        ids = settings.allowed_user_ids
        _user_id = next(iter(ids)) if ids else 0
    return _user_id


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
    expense_type: str = Form("all"),
    category: str = Form(""),
    send_telegram: bool = Form(False),
    email_enabled: bool = Form(False),
    email: str = Form(""),
):
    user_id = _get_user_id()

    filters: dict = {}
    if expense_type and expense_type != "all":
        filters["expense_type"] = expense_type
    if category:
        filters["category"] = category

    data = {
        "frequency": frequency,
        "day_of_week": day_of_week if frequency == "weekly" else None,
        "day_of_month": day_of_month if frequency == "monthly" else None,
        "time_utc": time_utc,
        "timezone": timezone,
        "send_telegram": send_telegram,
        "email": email if email_enabled and email else None,
        "filters": filters or None,
    }

    await create_schedule(db, user_id, data)
    all_schedules = await list_schedules(db, user_id)

    return templates.TemplateResponse(
        request=request,
        name="admin/schedules/_list.html",
        context={
            "schedules": all_schedules,
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
    expense_type: str = Form("all"),
    category: str = Form(""),
    send_telegram: bool = Form(False),
    email_enabled: bool = Form(False),
    email: str = Form(""),
):
    user_id = _get_user_id()

    filters: dict = {}
    if expense_type and expense_type != "all":
        filters["expense_type"] = expense_type
    if category:
        filters["category"] = category

    fields = {
        "frequency": frequency,
        "day_of_week": day_of_week if frequency == "weekly" else None,
        "day_of_month": day_of_month if frequency == "monthly" else None,
        "time_utc": time_utc,
        "timezone": timezone,
        "send_telegram": send_telegram,
        "email": email if email_enabled and email else None,
        "filters": filters or None,
    }

    schedule = await update_schedule(db, user_id, schedule_id, **fields)
    if schedule is None:
        return Response(status_code=404)

    all_schedules = await list_schedules(db, user_id)
    return templates.TemplateResponse(
        request=request,
        name="admin/schedules/_list.html",
        context={
            "schedules": all_schedules,
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
    # Return empty body so HTMX can swap-delete the card
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

    all_schedules = await list_schedules(db, user_id)
    return templates.TemplateResponse(
        request=request,
        name="admin/schedules/_list.html",
        context={
            "schedules": all_schedules,
        },
    )
