from datetime import datetime, time

from sqlalchemy import BigInteger, Boolean, SmallInteger, String, Time
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from src.models.database import Base


class ScheduledReport(Base):
    __tablename__ = "scheduled_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    frequency: Mapped[str] = mapped_column(String(20), nullable=False)
    day_of_week: Mapped[int | None] = mapped_column(SmallInteger)
    day_of_month: Mapped[int | None] = mapped_column(SmallInteger)
    time_utc: Mapped[time] = mapped_column(Time, nullable=False)
    timezone: Mapped[str] = mapped_column(String(50), default="Europe/Madrid")

    # Reporting window key (see src/services/report_presets.WINDOWS). When null,
    # the runner derives a window from `frequency` for backwards compatibility.
    window: Mapped[str | None] = mapped_column(String(30))

    email: Mapped[str | None] = mapped_column(String(255))
    send_telegram: Mapped[bool] = mapped_column(Boolean, default=True)

    filters: Mapped[dict | None] = mapped_column(JSONB)

    last_run_at: Mapped[datetime | None] = mapped_column()
    next_run_at: Mapped[datetime | None] = mapped_column()
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
