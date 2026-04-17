import io
import logging
import os
import re
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from telegram import Update
from telegram.ext import ContextTypes

from src.bot.keyboards import (
    delete_confirmation_keyboard,
    duplicate_keyboard,
    receipt_confirmation_keyboard,
    report_period_keyboard,
)
from src.config import settings
from src.models import async_session
from src.models.expense import Expense
from src.schemas.expense import ExpenseCategory, ExpenseCreate, ReportFilter
from src.services import expense_service, receipt_parser, report_agent
from src.services.currency_service import convert_to_base
from src.services.dedup_service import compute_image_hash, find_duplicate
from src.services.image_enhance import enhance_receipt
from src.services.report_service import generate_csv, generate_pdf

from PIL import Image as PILImage


def _enhance_image(raw_bytes: bytes) -> bytes:
    """Enhance receipt image, return enhanced JPEG bytes."""
    img = PILImage.open(io.BytesIO(raw_bytes))
    enhanced = enhance_receipt(img)
    buf = io.BytesIO()
    enhanced.save(buf, "JPEG", quality=90)
    return buf.getvalue()


def _save_receipt_images(receipt_dir: str, filename: str, raw_bytes: bytes, enhanced_bytes: bytes) -> tuple[str, str]:
    """Save raw and enhanced receipt images. Returns (raw_path, enhanced_path)."""
    os.makedirs(receipt_dir, exist_ok=True)
    enhanced_dir = os.path.join(receipt_dir, "enhanced")
    os.makedirs(enhanced_dir, exist_ok=True)

    raw_path = os.path.join(receipt_dir, filename)
    enhanced_path = os.path.join(enhanced_dir, filename)

    with open(raw_path, "wb") as f:
        f.write(raw_bytes)
    with open(enhanced_path, "wb") as f:
        f.write(enhanced_bytes)

    return raw_path, enhanced_path
from src.utils.filters import parse_filters

logger = logging.getLogger(__name__)


def authorized(func):
    """Decorator to restrict access to allowed users."""

    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if settings.allowed_user_ids and user_id not in settings.allowed_user_ids:
            await update.message.reply_text("You are not authorized to use this bot.")
            return
        return await func(update, context)

    return wrapper


@authorized
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Welcome to Expense Tracker!\n\n"
        "Send me a photo of a receipt and I'll extract the expense data.\n"
        "Or use /add to manually enter an expense.\n\n"
        "Type /help for all commands."
    )


@authorized
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "*Available Commands*\n\n"
        "/add `<amount> <currency> <description>` — Manual expense\n"
        "/list `[today|this week|last N]` — List expenses\n"
        "/total `[period]` — Total for period\n"
        "/categories `[period]` — Category breakdown\n"
        "/report `[filters]` — Generate PDF+CSV report\n"
        "/export `[filters]` — CSV only export\n"
        "/delete `<id>` — Delete an expense\n"
        "/help — This message",
        parse_mode="Markdown",
    )


@authorized
async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /add <amount> <currency> <description>."""
    if not context.args or len(context.args) < 3:
        await update.message.reply_text(
            "Usage: /add <amount> <currency> <description>\n"
            "Example: /add 45.50 EUR Taxi from airport"
        )
        return

    try:
        amount = Decimal(context.args[0])
    except InvalidOperation:
        await update.message.reply_text(f"Invalid amount: {context.args[0]}")
        return

    currency = context.args[1].upper()
    if len(currency) != 3:
        await update.message.reply_text(f"Invalid currency code: {currency}")
        return

    description = " ".join(context.args[2:])
    category = _infer_category(description)

    data = ExpenseCreate(
        description=description,
        category=category,
        date=date.today(),
        original_amount=amount,
        original_currency=currency,
        source="manual",
    )

    async with async_session() as session:
        expense = await expense_service.create_expense(session, update.effective_user.id, data)
        # Convert to base currency if not EUR
        if currency != settings.base_currency:
            result = await convert_to_base(session, amount, currency, date.today())
            if result:
                await expense_service.update_expense(
                    session, update.effective_user.id, expense.id,
                    eur_amount=result[0], exchange_rate=result[1],
                )

    await update.message.reply_text(
        f"Expense #{expense.id} saved!\n"
        f"Amount: {amount} {currency}\n"
        f"Category: {category}\n"
        f"Description: {description}"
    )


@authorized
async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /list [today|this week|last N]."""
    limit = 10
    date_from = None
    args_text = " ".join(context.args).lower() if context.args else ""

    if args_text == "today":
        date_from = date.today()
    elif args_text == "this week":
        date_from = date.today() - timedelta(days=date.today().weekday())
    elif match := re.match(r"last (\d+)", args_text):
        limit = int(match.group(1))

    filters = ReportFilter(date_from=date_from) if date_from else None

    async with async_session() as session:
        expenses = await expense_service.list_expenses(
            session, update.effective_user.id, limit=limit, filters=filters
        )

    if not expenses:
        await update.message.reply_text("No expenses found.")
        return

    lines = []
    for e in expenses:
        line = f"#{e.id} | {e.date} | {e.original_amount} {e.original_currency}"
        if e.vendor:
            line += f" | {e.vendor}"
        if e.category:
            line += f" [{e.category}]"
        lines.append(line)

    await update.message.reply_text("\n".join(lines))


@authorized
async def total_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /total [period]."""
    args_text = " ".join(context.args).lower() if context.args else "this month"

    date_from = None
    date_to = date.today()
    period_label = args_text

    if "today" in args_text:
        date_from = date.today()
    elif "this week" in args_text:
        date_from = date.today() - timedelta(days=date.today().weekday())
    elif "this month" in args_text:
        date_from = date.today().replace(day=1)
    elif match := re.match(r"last (\d+) days?", args_text):
        date_from = date.today() - timedelta(days=int(match.group(1)))

    async with async_session() as session:
        total = await expense_service.get_total(
            session, update.effective_user.id, date_from=date_from, date_to=date_to
        )

    await update.message.reply_text(f"Total ({period_label}): {total:.2f}")


@authorized
async def categories_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /categories [period]."""
    args_text = " ".join(context.args).lower() if context.args else "this month"

    date_from = None
    if "today" in args_text:
        date_from = date.today()
    elif "this week" in args_text:
        date_from = date.today() - timedelta(days=date.today().weekday())
    elif "this month" in args_text:
        date_from = date.today().replace(day=1)
    elif match := re.match(r"last (\d+) days?", args_text):
        date_from = date.today() - timedelta(days=int(match.group(1)))

    filters = ReportFilter(date_from=date_from, date_to=date.today()) if date_from else None

    async with async_session() as session:
        expenses = await expense_service.list_expenses(
            session, update.effective_user.id, limit=10000, filters=filters
        )

    if not expenses:
        await update.message.reply_text("No expenses found.")
        return

    cat_totals: defaultdict[str, Decimal] = defaultdict(Decimal)
    for e in expenses:
        cat_totals[e.category or "Other"] += e.eur_amount or e.original_amount

    grand_total = sum(cat_totals.values())
    lines = [f"*Category breakdown* ({args_text})\n"]
    for cat, total in sorted(cat_totals.items(), key=lambda x: x[1], reverse=True):
        pct = total / grand_total * 100 if grand_total else 0
        lines.append(f"  {cat}: {total:.2f} ({pct:.0f}%)")
    lines.append(f"\n*Total: {grand_total:.2f}*")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


@authorized
async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /delete <id>."""
    if not context.args:
        await update.message.reply_text("Usage: /delete <expense_id>")
        return

    try:
        expense_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid expense ID.")
        return

    async with async_session() as session:
        expense = await expense_service.get_expense(
            session, update.effective_user.id, expense_id
        )

    if not expense:
        await update.message.reply_text(f"Expense #{expense_id} not found.")
        return

    await update.message.reply_text(
        f"Delete expense #{expense_id}?\n"
        f"{expense.date} | {expense.original_amount} {expense.original_currency}"
        + (f" | {expense.vendor}" if expense.vendor else ""),
        reply_markup=delete_confirmation_keyboard(expense_id),
    )


@authorized
async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /report [natural-language filters]."""
    if not context.args:
        await update.message.reply_text(
            "Choose a period for your report:",
            reply_markup=report_period_keyboard(),
        )
        return

    query = " ".join(context.args)
    try:
        filters, summary = await report_agent.resolve_filter(query)
    except Exception:
        logger.exception("report_agent failed for query: %s", query)
        await update.message.reply_text(
            "Couldn't parse that filter. Try `/report` for the keyboard picker, "
            "or rephrase (e.g. `last 7 days, only personal`).",
            parse_mode="Markdown",
        )
        return

    if summary:
        await update.message.reply_text(f"Running: {summary}")
    await _send_report(update, filters)


@authorized
async def nuke_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /nuke — delete ALL expenses."""
    from src.bot.keyboards import nuke_confirmation_keyboard

    async with async_session() as session:
        from sqlalchemy import select, func
        result = await session.execute(
            select(func.count()).where(
                Expense.telegram_user_id == update.effective_user.id
            )
        )
        count = result.scalar_one()

    if count == 0:
        await update.message.reply_text("No expenses to delete.")
        return

    await update.message.reply_text(
        f"This will permanently delete ALL {count} expenses and their data.\n"
        "Are you sure?",
        reply_markup=nuke_confirmation_keyboard(),
    )


@authorized
async def export_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /export [filters] — CSV only."""
    filters = parse_filters(context.args) if context.args else ReportFilter(
        date_from=date.today().replace(day=1), date_to=date.today()
    )

    async with async_session() as session:
        csv_bytes = await generate_csv(session, update.effective_user.id, filters)

    date_label = _date_label(filters)
    await update.message.reply_document(
        document=io.BytesIO(csv_bytes),
        filename=f"expenses-{date_label}.csv",
        caption=f"CSV export: {date_label}",
    )


@authorized
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle receipt photos."""
    await update.message.reply_text("Processing receipt...")

    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    image_bytes = bytes(await file.download_as_bytearray())

    # Enhance image for better GPT accuracy (~100ms)
    try:
        enhanced_bytes = _enhance_image(image_bytes)
    except Exception:
        logger.warning("Image enhancement failed, using raw image")
        enhanced_bytes = image_bytes

    # Compute perceptual hash on RAW image for dedup consistency
    img_hash = compute_image_hash(image_bytes)

    # Send ENHANCED image to GPT for parsing
    try:
        parsed, raw_response = await receipt_parser.parse_receipt(enhanced_bytes)
    except Exception:
        logger.exception("Failed to parse receipt")
        await update.message.reply_text(
            "Sorry, I couldn't read this receipt. Please try again with a clearer photo."
        )
        return

    expense_date = date.fromisoformat(parsed.date)

    # Check for duplicates
    async with async_session() as session:
        dup = await find_duplicate(
            session,
            update.effective_user.id,
            img_hash,
            vendor=parsed.vendor,
            amount=parsed.total_amount,
            currency=parsed.currency,
            expense_date=expense_date,
        )

    if dup:
        # Store pending expense data in context for "save anyway"
        # Key by UUID so multiple concurrent duplicates don't overwrite each other
        dup_key = uuid.uuid4().hex
        context.user_data.setdefault("pending_expenses", {})[dup_key] = {
            "parsed": parsed,
            "raw_response": raw_response,
            "image_bytes": image_bytes,
            "enhanced_bytes": enhanced_bytes,
            "image_hash": img_hash,
            "caption": update.message.caption,
        }
        await update.message.reply_text(
            f"Possible duplicate of expense #{dup.id}:\n"
            f"{dup.date} | {dup.original_amount} {dup.original_currency}"
            + (f" | {dup.vendor}" if dup.vendor else ""),
            reply_markup=duplicate_keyboard(dup_key),
        )
        return

    # Save raw + enhanced receipt images to disk
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    receipt_filename = f"{update.effective_user.id}_{timestamp}.jpg"
    receipt_path, _ = _save_receipt_images(
        settings.receipt_storage_path, receipt_filename, image_bytes, enhanced_bytes,
    )

    data = ExpenseCreate(
        vendor=parsed.vendor,
        description=parsed.description,
        category=parsed.category,
        date=expense_date,
        original_amount=Decimal(str(parsed.total_amount)),
        original_currency=parsed.currency,
        location_city=parsed.location_city,
        location_country=parsed.location_country,
        line_items=parsed.line_items,
        source="image",
        confidence=parsed.confidence,
    )

    if update.message.caption:
        data.description = update.message.caption

    eur_amount: Decimal | None = None
    async with async_session() as session:
        expense = await expense_service.create_expense(
            session,
            update.effective_user.id,
            data,
            receipt_path=receipt_path,
            raw_llm_response=raw_response,
            image_hash=img_hash,
        )
        logger.info("Saved expense #%d: %s %s %s date=%s", expense.id, parsed.vendor, parsed.total_amount, parsed.currency, parsed.date)
        # Currency conversion
        if parsed.currency != settings.base_currency:
            result = await convert_to_base(
                session, Decimal(str(parsed.total_amount)), parsed.currency,
                expense_date,
            )
            if result:
                eur_amount = result[0]
                await expense_service.update_expense(
                    session, update.effective_user.id, expense.id,
                    eur_amount=result[0], exchange_rate=result[1],
                )

    amount_line = f"Amount: {parsed.total_amount} {parsed.currency}"
    if parsed.currency != settings.base_currency:
        if eur_amount is not None:
            amount_line += f" (≈ {eur_amount:.2f} {settings.base_currency})"
        else:
            amount_line += f" ({settings.base_currency} rate unavailable)"

    summary_lines = [
        f"*Receipt #{expense.id} saved* (confidence: {parsed.confidence:.0%})",
        "",
        f"Vendor: {parsed.vendor or 'Unknown'}",
        f"Date: {parsed.date}",
        amount_line,
        f"Category: {parsed.category}",
    ]
    if parsed.location_city or parsed.location_country:
        location = ", ".join(filter(None, [parsed.location_city, parsed.location_country]))
        summary_lines.append(f"Location: {location}")
    if parsed.line_items:
        summary_lines.append(f"Items: {len(parsed.line_items)}")

    await update.message.reply_text(
        "\n".join(summary_lines),
        parse_mode="Markdown",
        reply_markup=receipt_confirmation_keyboard(expense.id),
    )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline keyboard callbacks."""
    query = update.callback_query
    await query.answer()

    data = query.data
    action, _, param = data.partition(":")

    if action == "confirm":
        await query.edit_message_reply_markup(reply_markup=None)

    elif action in ("tag_company", "tag_personal"):
        expense_id = int(param)
        tag = "company" if action == "tag_company" else "personal"
        async with async_session() as session:
            await expense_service.update_expense(
                session, update.effective_user.id, expense_id, expense_type=tag,
            )
        await query.edit_message_reply_markup(reply_markup=None)
        label = "💼 Company" if tag == "company" else "👤 Personal"
        original = query.message.text or ""
        await query.edit_message_text(
            f"{original}\n\n_Tagged: {label}_",
            parse_mode="Markdown",
        )

    elif action == "discard":
        expense_id = int(param)
        async with async_session() as session:
            await expense_service.soft_delete_expense(
                session, update.effective_user.id, expense_id
            )
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(f"Expense #{param} discarded.")

    elif action == "edit":
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(
            f"To edit expense #{param}, use:\n/edit {param}"
        )

    elif action == "delete_yes":
        expense_id = int(param)
        async with async_session() as session:
            deleted = await expense_service.soft_delete_expense(
                session, update.effective_user.id, expense_id
            )
        await query.edit_message_reply_markup(reply_markup=None)
        if deleted:
            await query.message.reply_text(f"Expense #{param} deleted.")
        else:
            await query.message.reply_text(f"Expense #{param} not found.")

    elif action == "delete_no":
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("Deletion cancelled.")

    elif action == "report":
        await query.edit_message_reply_markup(reply_markup=None)
        filters = _period_to_filters(param)
        await _send_report(query, filters)

    elif action == "dup_save":
        await query.edit_message_reply_markup(reply_markup=None)
        pending = context.user_data.get("pending_expenses", {}).pop(param, None)
        if not pending:
            await query.message.reply_text("No pending expense to save (already handled?).")
            return
        await _save_pending_expense(query, context, pending)

    elif action == "dup_discard":
        await query.edit_message_reply_markup(reply_markup=None)
        context.user_data.get("pending_expenses", {}).pop(param, None)
        await query.message.reply_text("Duplicate discarded.")

    elif action == "nuke_yes":
        await query.edit_message_reply_markup(reply_markup=None)
        async with async_session() as session:
            count = await expense_service.nuke_expenses(
                session, update.effective_user.id
            )
        await query.message.reply_text(f"Deleted all {count} expenses.")

    elif action == "nuke_no":
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("Nuke cancelled.")


async def _save_pending_expense(query, context, pending: dict) -> None:
    """Save a pending expense that was flagged as potential duplicate."""
    parsed = pending["parsed"]
    raw_response = pending["raw_response"]
    image_bytes = pending["image_bytes"]
    enhanced_bytes = pending.get("enhanced_bytes", image_bytes)
    img_hash = pending["image_hash"]
    caption = pending["caption"]

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    user_id = query.from_user.id
    receipt_filename = f"{user_id}_{timestamp}.jpg"
    receipt_path, _ = _save_receipt_images(
        settings.receipt_storage_path, receipt_filename, image_bytes, enhanced_bytes,
    )

    data = ExpenseCreate(
        vendor=parsed.vendor,
        description=caption or parsed.description,
        category=parsed.category,
        date=date.fromisoformat(parsed.date),
        original_amount=Decimal(str(parsed.total_amount)),
        original_currency=parsed.currency,
        location_city=parsed.location_city,
        location_country=parsed.location_country,
        line_items=parsed.line_items,
        source="image",
        confidence=parsed.confidence,
    )

    eur_amount: Decimal | None = None
    async with async_session() as session:
        expense = await expense_service.create_expense(
            session, user_id, data,
            receipt_path=receipt_path,
            raw_llm_response=raw_response,
            image_hash=img_hash,
        )
        logger.info("Saved dup-override expense #%d: %s %s %s date=%s", expense.id, parsed.vendor, parsed.total_amount, parsed.currency, parsed.date)
        if parsed.currency != settings.base_currency:
            result = await convert_to_base(
                session, Decimal(str(parsed.total_amount)), parsed.currency,
                date.fromisoformat(parsed.date),
            )
            if result:
                eur_amount = result[0]
                await expense_service.update_expense(
                    session, user_id, expense.id,
                    eur_amount=result[0], exchange_rate=result[1],
                )

    amount_line = f"Amount: {parsed.total_amount} {parsed.currency}"
    if parsed.currency != settings.base_currency:
        if eur_amount is not None:
            amount_line += f" (≈ {eur_amount:.2f} {settings.base_currency})"
        else:
            amount_line += f" ({settings.base_currency} rate unavailable)"

    await query.message.reply_text(
        f"*Receipt #{expense.id} saved* (duplicate override)\n\n"
        f"Vendor: {parsed.vendor or 'Unknown'}\n"
        f"Date: {parsed.date}\n"
        f"{amount_line}\n"
        f"Category: {parsed.category}",
        parse_mode="Markdown",
    )


async def _send_report(source, filters: ReportFilter) -> None:
    """Generate and send PDF+CSV report. Works with Update or CallbackQuery."""
    if isinstance(source, Update):
        reply = source.message.reply_text
        send_doc = source.message.reply_document
        user_id = source.effective_user.id
    else:
        # CallbackQuery
        reply = source.message.reply_text
        send_doc = source.message.reply_document
        user_id = source.from_user.id

    await reply("Generating report...")

    async with async_session() as session:
        try:
            pdf_bytes = await generate_pdf(session, user_id, filters)
        except Exception:
            logger.exception("Failed to generate PDF")
            pdf_bytes = None

        csv_bytes = await generate_csv(session, user_id, filters)

    date_label = _date_label(filters)

    if pdf_bytes:
        await send_doc(
            document=io.BytesIO(pdf_bytes),
            filename=f"expense-report-{date_label}.pdf",
        )
    await send_doc(
        document=io.BytesIO(csv_bytes),
        filename=f"expense-report-{date_label}.csv",
        caption=f"Expense Report: {date_label}",
    )


def _period_to_filters(period: str) -> ReportFilter:
    """Convert report period keyboard selection to filters."""
    today = date.today()
    if period == "this_week":
        return ReportFilter(date_from=today - timedelta(days=today.weekday()), date_to=today)
    elif period == "prev_week":
        week_start = today - timedelta(days=today.weekday() + 7)
        return ReportFilter(date_from=week_start, date_to=week_start + timedelta(days=6))
    elif period == "this_month":
        return ReportFilter(date_from=today.replace(day=1), date_to=today)
    elif period == "prev_month":
        last_day_prev = today.replace(day=1) - timedelta(days=1)
        return ReportFilter(date_from=last_day_prev.replace(day=1), date_to=last_day_prev)
    elif period == "last_7":
        return ReportFilter(date_from=today - timedelta(days=7), date_to=today)
    elif period == "last_30":
        return ReportFilter(date_from=today - timedelta(days=30), date_to=today)
    elif period == "all_time":
        return ReportFilter()
    return ReportFilter(date_from=today.replace(day=1), date_to=today)


def _date_label(filters: ReportFilter | None) -> str:
    """Generate a date label for filenames."""
    if filters and filters.date_from and filters.date_to:
        return f"{filters.date_from}--{filters.date_to}"
    if filters and not filters.date_from and not filters.date_to:
        return "all-time"
    return date.today().strftime("%Y-%m")


def _infer_category(description: str) -> ExpenseCategory:
    """Simple keyword-based category inference for manual entries."""
    desc_lower = description.lower()
    keywords = {
        ExpenseCategory.TRANSPORT: ["taxi", "uber", "lyft", "bus", "train", "metro", "flight", "airport"],
        ExpenseCategory.FOOD: ["restaurant", "lunch", "dinner", "breakfast", "coffee", "cafe", "food", "meal"],
        ExpenseCategory.ACCOMMODATION: ["hotel", "airbnb", "hostel", "booking", "room"],
        ExpenseCategory.ENTERTAINMENT: ["cinema", "theater", "concert", "museum", "show"],
        ExpenseCategory.SHOPPING: ["shop", "store", "market", "mall", "purchase"],
        ExpenseCategory.COMMUNICATION: ["phone", "sim", "data", "internet", "wifi"],
        ExpenseCategory.OFFICE: ["office", "supplies", "printer", "paper"],
        ExpenseCategory.HEALTH: ["pharmacy", "doctor", "hospital", "medicine"],
    }
    for category, words in keywords.items():
        if any(word in desc_lower for word in words):
            return category
    return ExpenseCategory.OTHER
