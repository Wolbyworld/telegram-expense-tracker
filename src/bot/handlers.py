import io
import logging
import re
from collections import defaultdict
from datetime import date, timedelta
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
from src.services import expense_service, report_agent
from src.services.currency_service import convert_to_base
from src.services.report_service import generate_csv, generate_pdf
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
        from sqlalchemy import func, select
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
    """Use the same durable receipt service as email and the operator CLI."""
    from src.services import receipt_intake

    await update.message.reply_text("Processing receipt...")
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    image_bytes = bytes(await file.download_as_bytearray())
    try:
        candidate = await receipt_intake.register_source(
            update.effective_user.id, "telegram",
            f"{update.effective_chat.id}:{update.message.message_id}",
            image_bytes, "image/jpeg", "Telegram receipt.jpg",
        )
        candidate = await receipt_intake.process_candidate(candidate.id, update.effective_user.id)
        if candidate.status == "needs_review" and not candidate.error_code:
            edits = {"description": update.message.caption} if update.message.caption else None
            candidate = await receipt_intake.decide(
                candidate.id, update.effective_user.id, "accept", "telegram", edits=edits,
            )
    except Exception:
        logger.exception("Receipt intake failed")
        await update.message.reply_text("I couldn't process this receipt. Please try again later.")
        return
    if candidate.status == "duplicate_candidate":
        match = (f"expense #{candidate.duplicate_expense_id}" if candidate.duplicate_expense_id
                 else "another pending receipt")
        await update.message.reply_text(
            f"Possible duplicate of {match}. This review is saved and survives restarts.",
            reply_markup=duplicate_keyboard(candidate.id),
        )
    elif candidate.status == "committed":
        await _reply_saved_receipt(update.message, candidate)
    elif candidate.status == "linked_duplicate":
        await update.message.reply_text(f"Already linked to expense #{candidate.expense_id}.")
    elif candidate.status == "rejected":
        await update.message.reply_text("This receipt was already discarded.")
    else:
        await update.message.reply_text(
            "This receipt needs attention. It is saved in the private Receipt inbox for review."
        )


async def _reply_saved_receipt(message, candidate):
    """Conversion is independent from receipt commit; a failure never repeats the insert."""
    parsed = candidate.parsed or {}
    async with async_session() as session:
        expense = await expense_service.get_expense(
            session, candidate.owner_id, candidate.expense_id,
        )
        if not expense:
            await message.reply_text("The saved expense has since been deleted.")
            return
        if expense.original_currency != settings.base_currency and expense.eur_amount is None:
            try:
                result = await convert_to_base(
                    session, expense.original_amount, expense.original_currency, expense.date,
                )
                if result:
                    await expense_service.update_expense(
                        session, candidate.owner_id, expense.id,
                        eur_amount=result[0], exchange_rate=result[1],
                    )
            except Exception:
                logger.warning("Currency conversion pending for expense #%d", expense.id)
    await message.reply_text(
        f"Receipt #{candidate.expense_id} saved (Company)\n\n"
        f"Vendor: {parsed.get('vendor') or 'Unknown'}\n"
        f"Date: {parsed.get('date')}\n"
        f"Amount: {parsed.get('total_amount')} {parsed.get('currency')}\n"
        f"Category: {parsed.get('category')}\n"
        f"Confidence: {float(parsed.get('confidence') or 0):.0%}",
        reply_markup=receipt_confirmation_keyboard(
            candidate.expense_id, include_type_buttons=settings.confirm_expense_type,
        ),
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

    elif action in ("dup_save", "dup_discard"):
        from src.services.receipt_intake import decide

        try:
            candidate = await decide(
                param, update.effective_user.id,
                "save_separate" if action == "dup_save" else "reject", "telegram",
                override_reason="Explicit Telegram Save anyway",
            )
        except ValueError:
            await query.message.reply_text("No pending receipt available for this account.")
            return
        await query.edit_message_reply_markup(reply_markup=None)
        if candidate.status == "committed":
            await _reply_saved_receipt(query.message, candidate)
        else:
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
        ExpenseCategory.TRANSPORT: [
            "taxi", "uber", "lyft", "bus", "train", "metro", "flight", "airport",
        ],
        ExpenseCategory.FOOD: [
            "restaurant", "lunch", "dinner", "breakfast", "coffee", "cafe", "food", "meal",
        ],
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
