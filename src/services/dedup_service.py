"""Duplicate receipt detection using perceptual image hashing."""

import io
import logging
from datetime import timedelta

import imagehash
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.expense import Expense

logger = logging.getLogger(__name__)

# Max hamming distance to consider two images as duplicates.
# pHash produces 64-bit hashes; distance <= 10 means very similar images.
HASH_DISTANCE_THRESHOLD = 10


def compute_image_hash(image_bytes: bytes) -> str:
    """Compute a perceptual hash of an image."""
    img = Image.open(io.BytesIO(image_bytes))
    return str(imagehash.phash(img))


async def find_duplicate(
    session: AsyncSession,
    user_id: int,
    image_hash: str,
    *,
    vendor: str | None = None,
    amount: float | None = None,
    currency: str | None = None,
    expense_date=None,
) -> Expense | None:
    """Check for duplicate expenses. Returns the matching expense if found.

    Checks two signals:
    1. Perceptual image hash similarity (catches re-uploads of same receipt)
    2. Same vendor + amount + date + currency (catches different photos of same receipt)
    """
    # Get recent expenses with image hashes for this user
    query = select(Expense).where(
        Expense.telegram_user_id == user_id,
        Expense.deleted_at.is_(None),
    )

    # Only check last 90 days to keep it fast
    if expense_date:
        query = query.where(Expense.date >= expense_date - timedelta(days=90))

    result = await session.execute(query)
    existing = result.scalars().all()

    # Check 1: perceptual hash similarity
    if image_hash:
        incoming_hash = imagehash.hex_to_hash(image_hash)
        for exp in existing:
            if not exp.image_hash:
                continue
            existing_hash = imagehash.hex_to_hash(exp.image_hash)
            distance = incoming_hash - existing_hash
            if distance <= HASH_DISTANCE_THRESHOLD:
                logger.info(
                    "Duplicate detected (image hash distance=%d): expense #%d",
                    distance, exp.id,
                )
                return exp

    # Check 2: content-based match (vendor + amount + date + currency)
    if vendor and amount and currency and expense_date:
        for exp in existing:
            if (
                exp.vendor
                and exp.vendor.lower() == vendor.lower()
                and float(exp.original_amount) == amount
                and exp.original_currency == currency
                and exp.date == expense_date
            ):
                logger.info("Duplicate detected (content match): expense #%d", exp.id)
                return exp

    return None
