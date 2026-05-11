"""Tiny registry so background jobs can reach the live Telegram bot.

Both entry points (`src/main.py` for webhook, `run_polling.py` for polling)
call `set_bot(...)` once the telegram Application is built. Background code
calls `get_bot()` and gracefully no-ops if no bot is registered.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from telegram import Bot

_bot: "Bot | None" = None


def set_bot(bot: "Bot | None") -> None:
    global _bot
    _bot = bot


def get_bot() -> "Bot | None":
    return _bot
