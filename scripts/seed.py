"""Seed the database with sample expenses for development/testing."""

import asyncio
import sys
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from src.models import async_session
from src.models.expense import Expense


SAMPLE_EXPENSES = [
    {
        "vendor": "Restaurant El Farol",
        "description": "Dinner with colleagues",
        "category": "Food",
        "date": date.today() - timedelta(days=1),
        "original_amount": Decimal("45.50"),
        "original_currency": "EUR",
        "eur_amount": Decimal("45.50"),
        "exchange_rate": Decimal("1.000000"),
        "location_city": "Madrid",
        "location_country": "Spain",
        "source": "image",
        "confidence": Decimal("0.95"),
    },
    {
        "vendor": "Uber",
        "description": "Airport to hotel",
        "category": "Transport",
        "date": date.today() - timedelta(days=2),
        "original_amount": Decimal("22.30"),
        "original_currency": "EUR",
        "eur_amount": Decimal("22.30"),
        "exchange_rate": Decimal("1.000000"),
        "location_city": "Madrid",
        "location_country": "Spain",
        "source": "manual",
    },
    {
        "vendor": "Hotel Ritz",
        "description": "2 nights accommodation",
        "category": "Accommodation",
        "date": date.today() - timedelta(days=3),
        "original_amount": Decimal("320.00"),
        "original_currency": "EUR",
        "eur_amount": Decimal("320.00"),
        "exchange_rate": Decimal("1.000000"),
        "location_city": "Madrid",
        "location_country": "Spain",
        "source": "image",
        "confidence": Decimal("0.88"),
    },
    {
        "vendor": "Starbucks",
        "description": "Coffee and pastry",
        "category": "Food",
        "date": date.today(),
        "original_amount": Decimal("8.75"),
        "original_currency": "EUR",
        "eur_amount": Decimal("8.75"),
        "exchange_rate": Decimal("1.000000"),
        "location_city": "Madrid",
        "location_country": "Spain",
        "source": "image",
        "confidence": Decimal("0.92"),
    },
    {
        "vendor": "The Black Cab",
        "description": "Taxi to meeting",
        "category": "Transport",
        "date": date.today() - timedelta(days=5),
        "original_amount": Decimal("35.00"),
        "original_currency": "GBP",
        "eur_amount": Decimal("40.60"),
        "exchange_rate": Decimal("1.160000"),
        "location_city": "London",
        "location_country": "UK",
        "source": "manual",
    },
    {
        "vendor": "Pret A Manger",
        "description": "Lunch",
        "category": "Food",
        "date": date.today() - timedelta(days=5),
        "original_amount": Decimal("12.50"),
        "original_currency": "GBP",
        "eur_amount": Decimal("14.50"),
        "exchange_rate": Decimal("1.160000"),
        "location_city": "London",
        "location_country": "UK",
        "source": "image",
        "confidence": Decimal("0.90"),
    },
    {
        "vendor": "Metro Madrid",
        "description": "10-trip metro card",
        "category": "Transport",
        "date": date.today() - timedelta(days=1),
        "original_amount": Decimal("12.20"),
        "original_currency": "EUR",
        "eur_amount": Decimal("12.20"),
        "exchange_rate": Decimal("1.000000"),
        "location_city": "Madrid",
        "location_country": "Spain",
        "source": "manual",
    },
    {
        "vendor": "Museo del Prado",
        "description": "Museum entrance",
        "category": "Entertainment",
        "date": date.today() - timedelta(days=4),
        "original_amount": Decimal("15.00"),
        "original_currency": "EUR",
        "eur_amount": Decimal("15.00"),
        "exchange_rate": Decimal("1.000000"),
        "location_city": "Madrid",
        "location_country": "Spain",
        "source": "manual",
    },
]


async def seed(user_id: int) -> None:
    async with async_session() as session:
        for data in SAMPLE_EXPENSES:
            expense = Expense(telegram_user_id=user_id, **data)
            session.add(expense)
        await session.commit()
        print(f"Seeded {len(SAMPLE_EXPENSES)} expenses for user {user_id}")


if __name__ == "__main__":
    user_id = int(sys.argv[1]) if len(sys.argv) > 1 else 123456789
    asyncio.run(seed(user_id))
