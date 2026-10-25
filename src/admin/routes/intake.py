"""Receipt review within the existing private admin panel. No public intake route."""

import secrets
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.admin.dependencies import get_db
from src.config import settings
from src.models.intake import IncomingMessage, IntakePollState, ReceiptCandidate
from src.services.receipt_files import safe_path
from src.services.receipt_intake import ReviewConflictError, decide

router = APIRouter(prefix="/intake")
templates = Jinja2Templates(directory="templates")


def owner():
    ids = settings.allowed_user_ids
    if settings.email_intake_owner_id in ids:
        return settings.email_intake_owner_id
    if len(ids) == 1:
        return next(iter(ids))
    raise HTTPException(503, "Configure an explicit intake owner")


@router.get("")
async def index(request: Request, db: AsyncSession = Depends(get_db)):
    rows = (
        (
            await db.execute(
                select(ReceiptCandidate)
                .where(
                    ReceiptCandidate.owner_id == owner(),
                )
                .order_by(ReceiptCandidate.created_at.desc())
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    messages = (
        (
            await db.execute(
                select(IncomingMessage)
                .where(
                    IncomingMessage.owner_id == owner(),
                    IncomingMessage.status.in_(["quarantined", "failed", "retry"]),
                )
                .order_by(IncomingMessage.created_at.desc())
                .limit(30)
            )
        )
        .scalars()
        .all()
    )
    poll = await db.get(IntakePollState, settings.email_intake_account)
    token = secrets.token_urlsafe(32)
    response = templates.TemplateResponse(
        request=request,
        name="admin/intake/index.html",
        context={
            "active_page": "intake",
            "candidates": rows,
            "csrf": token,
            "messages": messages,
            "poll": poll,
            "receiving_enabled": settings.email_intake_enabled,
        },
    )
    response.set_cookie(
        "intake_csrf",
        token,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
        path="/admin/intake",
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/{candidate_id}/file")
async def evidence(candidate_id: str, db: AsyncSession = Depends(get_db)):
    row = (
        await db.execute(
            select(ReceiptCandidate).where(
                ReceiptCandidate.id == candidate_id,
                ReceiptCandidate.owner_id == owner(),
            )
        )
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(404)
    # Always download untrusted originals; never render email HTML/PDF inline.
    response = FileResponse(
        safe_path(row.receipt_path),
        media_type="application/octet-stream",
        filename="receipt-"
        + row.id
        + {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
            "application/pdf": ".pdf",
            "text/plain": ".txt",
            "message/rfc822": ".eml",
        }.get(row.mime_type, ".bin"),
        content_disposition_type="attachment",
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/{candidate_id}")
async def review(
    candidate_id: str,
    request: Request,
    action: str = Form(...),
    csrf: str = Form(...),
    reason: str = Form(""),
    expense_id: int | None = Form(None),
    vendor: str = Form(""),
    receipt_date: str = Form(""),
    amount: str = Form(""),
    currency: str = Form(""),
):
    cookie = request.cookies.get("intake_csrf", "")
    if not cookie or not secrets.compare_digest(cookie, csrf):
        raise HTTPException(403, "Refresh the review page before submitting")
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).netloc != request.url.netloc:
        raise HTTPException(403, "Cross-origin review is not allowed")
    edits = {}
    for key, value in (
        ("vendor", vendor),
        ("date", receipt_date),
        ("total_amount", amount),
        ("currency", currency),
    ):
        if value.strip():
            edits[key] = value.strip()
    if action == "correct":
        if not receipt_date.strip():
            edits["date"] = None
        if not amount.strip():
            edits["total_amount"] = None
    try:
        await decide(
            candidate_id,
            owner(),
            action,
            "private-admin",
            edits=edits or None,
            expense_id=expense_id,
            override_reason=reason,
        )
    except (ValueError, ReviewConflictError):
        raise HTTPException(
            409, "Review could not be applied; check fields and duplicate details"
        ) from None
    return RedirectResponse("/admin/intake", status_code=303)
