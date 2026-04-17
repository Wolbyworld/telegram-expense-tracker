"""Run the bot in polling mode alongside the admin panel (FastAPI/uvicorn)."""

import asyncio
import logging
import os

# WeasyPrint needs Homebrew native libs on macOS
if os.path.isdir("/opt/homebrew/lib"):
    os.environ.setdefault("DYLD_LIBRARY_PATH", "/opt/homebrew/lib")

# Signal to src/main.py: skip telegram webhook setup (polling handles it)
os.environ["POLLING_MODE"] = "1"

import uvicorn
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from src.bot.handlers import (
    add_command,
    categories_command,
    delete_command,
    export_command,
    handle_callback,
    handle_photo,
    help_command,
    list_command,
    nuke_command,
    report_command,
    start_command,
    total_command,
)
from src.config import settings

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Build telegram bot
telegram_app = Application.builder().token(settings.telegram_bot_token).build()

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


async def main():
    """Run telegram polling and uvicorn admin panel in the same event loop."""
    from src.main import app

    # Start uvicorn as an asyncio server (not in a thread)
    config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="info")
    server = uvicorn.Server(config)

    # Initialize telegram
    await telegram_app.initialize()
    await telegram_app.start()
    await telegram_app.updater.start_polling()

    logger.info("Bot polling + admin panel on :8000 started")

    try:
        await server.serve()
    finally:
        await telegram_app.updater.stop()
        await telegram_app.stop()
        await telegram_app.shutdown()


if __name__ == "__main__":
    print("Starting bot + admin panel...")
    asyncio.run(main())
