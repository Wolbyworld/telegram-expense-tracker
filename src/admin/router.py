from fastapi import APIRouter

from src.admin.routes import dashboard, expenses, reports, schedules

router = APIRouter(prefix="/admin")
router.include_router(dashboard.router, tags=["dashboard"])
router.include_router(expenses.router, tags=["expenses"])
router.include_router(reports.router, tags=["reports"])
router.include_router(schedules.router, tags=["schedules"])
