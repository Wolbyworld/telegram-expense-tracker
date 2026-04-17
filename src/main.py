import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles

from src.config import settings

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Only set up telegram webhook app when NOT in polling mode
_polling_mode = os.environ.get("POLLING_MODE") == "1"
telegram_app = None

if not _polling_mode:
    from telegram import Update
    from telegram.ext import (
        Application,
        CallbackQueryHandler,
        CommandHandler,
        MessageHandler,
        filters,
    )
    from src.bot.handlers import (
        add_command, categories_command, delete_command, export_command,
        handle_callback, handle_photo, help_command, list_command,
        nuke_command, report_command, start_command, total_command,
    )

    telegram_app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .updater(None)
        .build()
    )
    telegram_app.add_handler(CommandHandler("start", start_command))
    telegram_app.add_handler(CommandHandler("help", help_command))
    telegram_app.add_handler(CommandHandler("add", add_command))
    telegram_app.add_handler(CommandHandler("list", list_command))
    telegram_app.add_handler(CommandHandler("total", total_command))
    telegram_app.add_handler(CommandHandler("delete", delete_command))
    telegram_app.add_handler(CommandHandler("report", report_command))
    telegram_app.add_handler(CommandHandler("categories", categories_command))
    telegram_app.add_handler(CommandHandler("export", export_command))
    telegram_app.add_handler(CommandHandler("nuke", nuke_command))
    telegram_app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    telegram_app.add_handler(CallbackQueryHandler(handle_callback))


@asynccontextmanager
async def lifespan(app: FastAPI):
    if telegram_app and not _polling_mode:
        await telegram_app.initialize()
        await telegram_app.start()
        webhook_url = settings.telegram_webhook_url
        await telegram_app.bot.set_webhook(
            url=webhook_url,
            secret_token=settings.telegram_webhook_secret or None,
        )
        logger.info("Telegram webhook set to %s", webhook_url)

    yield

    if telegram_app and not _polling_mode:
        await telegram_app.stop()
        await telegram_app.shutdown()


app = FastAPI(title="Expense Tracker Bot", lifespan=lifespan)

# Admin panel
from src.admin.router import router as admin_router
app.include_router(admin_router)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.post("/webhook")
async def telegram_webhook_endpoint(request: Request) -> Response:
    """Receive Telegram updates via webhook."""
    if not telegram_app:
        return Response(status_code=503)
    if settings.telegram_webhook_secret:
        token = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if token != settings.telegram_webhook_secret:
            return Response(status_code=403)

    data = await request.json()
    from telegram import Update
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return Response(status_code=200)


@app.get("/health")
async def health():
    return {"status": "ok"}
