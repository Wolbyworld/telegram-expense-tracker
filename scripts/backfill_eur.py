"""Retro-convert expenses with NULL eur_amount using the current FX provider."""

import asyncio
import logging

from sqlalchemy import select

from src.models import async_session
from src.models.expense import Expense
from src.services.currency_service import convert_to_base

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("backfill_eur")


async def main() -> None:
    async with async_session() as session:
        result = await session.execute(
            select(Expense).where(
                Expense.eur_amount.is_(None),
                Expense.deleted_at.is_(None),
            ).order_by(Expense.id)
        )
        rows = list(result.scalars().all())
        logger.info("Found %d expenses needing backfill", len(rows))

        ok = 0
        skipped = 0
        for e in rows:
            res = await convert_to_base(session, e.original_amount, e.original_currency, e.date)
            if res is None:
                logger.warning("No rate for #%d (%s %s on %s)", e.id, e.original_amount, e.original_currency, e.date)
                skipped += 1
                continue
            e.eur_amount, e.exchange_rate = res
            await session.commit()
            logger.info("#%d: %s %s -> %s EUR (rate %s)", e.id, e.original_amount, e.original_currency, e.eur_amount, e.exchange_rate)
            ok += 1

    logger.info("Done. Backfilled %d, skipped %d", ok, skipped)


if __name__ == "__main__":
    asyncio.run(main())
