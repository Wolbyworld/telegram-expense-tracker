from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Date, Index, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from src.models.database import Base


class Expense(Base):
    __tablename__ = "expenses"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # Extracted fields
    vendor: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(100))
    date: Mapped[date] = mapped_column(Date, nullable=False)

    # Amounts
    original_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    original_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    eur_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    exchange_rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))

    # Location
    location_city: Mapped[str | None] = mapped_column(String(255))
    location_country: Mapped[str | None] = mapped_column(String(100))

    # Receipt image
    receipt_path: Mapped[str | None] = mapped_column(String(500))
    receipt_thumbnail_path: Mapped[str | None] = mapped_column(String(500))

    # Line items
    line_items: Mapped[dict | None] = mapped_column(JSONB)

    # Classification: 'personal' | 'company' | NULL (untagged)
    expense_type: Mapped[str | None] = mapped_column(String(20))

    # Metadata
    source: Mapped[str] = mapped_column(String(20), default="image")
    raw_llm_response: Mapped[dict | None] = mapped_column(JSONB)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    image_hash: Mapped[str | None] = mapped_column(String(64))

    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column()

    __table_args__ = (
        Index("idx_expenses_user_date", "telegram_user_id", "date"),
        Index("idx_expenses_category", "telegram_user_id", "category"),
        Index("idx_expenses_vendor", "telegram_user_id", "vendor"),
        Index(
            "idx_expenses_location", "telegram_user_id", "location_country", "location_city"
        ),
    )
