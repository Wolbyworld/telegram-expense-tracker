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

    # Email
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_pass: str = ""
    email_from: str = "Expense Bot <expenses@yourdomain.com>"

    # Currency
    base_currency: str = "EUR"
    exchange_rate_api_key: str = ""

    # Storage
    receipt_storage_path: str = "/data/receipts"

    # App
    timezone: str = "Europe/Madrid"
    allowed_telegram_users: str = ""  # Comma-separated user IDs

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
