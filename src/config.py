from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    # Telegram
    telegram_bot_token: str
    telegram_webhook_url: str = "https://your-domain.example.com/webhook"
    telegram_webhook_secret: str = ""

    # OpenAI
    openai_api_key: str

    # Database
    database_url: str = "postgresql+asyncpg://expenses:expenses@localhost:5432/expenses"

    # Email (Resend)
    resend_api_key: str = ""
    email_from: str = "Expense Tracker <reports@elcano.cc>"

    # Inbound email is opt-in and never falls back to the outgoing report key.
    email_intake_enabled: bool = False
    resend_receiving_api_key: str = ""
    email_intake_account: str = "expense-tracker"
    email_intake_address: str = ""
    email_intake_senders: str = ""
    email_intake_owner_id: int = 0
    email_intake_poll_seconds: int = 90
    email_intake_max_pages: int = 100
    intake_max_attachment_bytes: int = 10 * 1024 * 1024
    intake_max_message_bytes: int = 25 * 1024 * 1024
    intake_max_attachments: int = 10
    intake_max_pdf_pages: int = 20
    intake_max_image_pixels: int = 25_000_000
    intake_max_attempts: int = 5
    # Optional executable scanner (e.g. /usr/bin/clamdscan), never a shell command.
    # Email extraction fails closed until a scanner is explicitly configured.
    intake_scanner_path: str = "/usr/bin/clamscan"

    # Currency
    base_currency: str = "EUR"
    exchange_rate_api_key: str = ""

    # Storage
    receipt_storage_path: str = "/data/receipts"

    # App
    timezone: str = "Europe/Madrid"
    allowed_telegram_users: str = ""  # Comma-separated user IDs

    # Receipt tagging
    # When True, the bot asks "Company / Personal" after each receipt before the
    # expense is tagged. When False (default), receipts are saved straight away
    # as company expenses, matching the deployed application.
    confirm_expense_type: bool = False

    @property
    def allowed_user_ids(self) -> set[int]:
        if not self.allowed_telegram_users:
            return set()
        return {int(uid.strip()) for uid in self.allowed_telegram_users.split(",")}

    @property
    def sync_database_url(self) -> str:
        """Database URL for Alembic (sync driver)."""
        return self.database_url.replace("+asyncpg", "")


settings = Settings()  # type: ignore[call-arg]
