"""Currency conversion service with exchange rate caching."""

import logging
from datetime import date, timedelta
from decimal import Decimal

import aiohttp
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.models.exchange_rate import ExchangeRate

logger = logging.getLogger(__name__)

FRANKFURTER_API_URL = "https://api.frankfurter.dev/v1"


async def _fetch_rate_from_api(
    from_currency: str, to_currency: str, rate_date: date
) -> Decimal | None:
    """Fetch a single exchange rate from Frankfurter (ECB-backed, no key)."""
    url = f"{FRANKFURTER_API_URL}/{rate_date.isoformat()}"
    params = {"base": from_currency, "symbols": to_currency}

    try:
        async with aiohttp.ClientSession() as http:
            async with http.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    logger.warning("Frankfurter API returned %d for %s->%s on %s", resp.status, from_currency, to_currency, rate_date)
                    return None
                data = await resp.json()
                rate = (data.get("rates") or {}).get(to_currency)
                if rate is not None:
                    return Decimal(str(rate))
                logger.warning("Frankfurter returned no rate for %s->%s on %s: %s", from_currency, to_currency, rate_date, data)
                return None
    except Exception:
        logger.exception("Failed to fetch exchange rate from Frankfurter")
        return None


async def get_exchange_rate(
    session: AsyncSession,
    from_currency: str,
    to_currency: str,
    rate_date: date,
) -> Decimal | None:
    """Get exchange rate, checking cache first then fetching from API."""
    if from_currency == to_currency:
        return Decimal("1.000000")

    # Check cache
    result = await session.execute(
        select(ExchangeRate).where(
            ExchangeRate.base_currency == from_currency,
            ExchangeRate.target_currency == to_currency,
            ExchangeRate.rate_date == rate_date,
        )
    )
    cached = result.scalar_one_or_none()
    if cached:
        return cached.rate

    # Fetch from API
    rate = await _fetch_rate_from_api(from_currency, to_currency, rate_date)
    if rate is not None:
        # Cache the rate
        entry = ExchangeRate(
            base_currency=from_currency,
            target_currency=to_currency,
            rate=rate,
            rate_date=rate_date,
        )
        session.add(entry)
        await session.commit()
        return rate

    # Fallback: try nearest available date (up to 7 days back)
    for days_back in range(1, 8):
        fallback_date = rate_date - timedelta(days=days_back)
        result = await session.execute(
            select(ExchangeRate).where(
                ExchangeRate.base_currency == from_currency,
                ExchangeRate.target_currency == to_currency,
                ExchangeRate.rate_date == fallback_date,
            )
        )
        cached = result.scalar_one_or_none()
        if cached:
            logger.info("Using fallback rate from %s for %s->%s", fallback_date, from_currency, to_currency)
            return cached.rate

    logger.warning("No exchange rate found: %s -> %s on %s", from_currency, to_currency, rate_date)
    return None


async def convert_to_base(
    session: AsyncSession,
    amount: Decimal,
    from_currency: str,
    rate_date: date,
    base_currency: str | None = None,
) -> tuple[Decimal, Decimal] | None:
    """Convert amount to base currency. Returns (converted_amount, rate) or None."""
    base = base_currency or settings.base_currency
    rate = await get_exchange_rate(session, from_currency, base, rate_date)
    if rate is None:
        return None
    converted = (amount * rate).quantize(Decimal("0.01"))
    return converted, rate
