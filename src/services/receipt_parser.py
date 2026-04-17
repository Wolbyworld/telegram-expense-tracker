import base64
import json
import logging

from openai import AsyncOpenAI

from src.config import settings
from src.schemas.expense import ReceiptParseResult

logger = logging.getLogger(__name__)

client = AsyncOpenAI(api_key=settings.openai_api_key)

RECEIPT_SYSTEM_PROMPT = """You are an expense receipt parser. Extract structured data from receipt images.

Rules:
- Date: use ISO 8601 (YYYY-MM-DD). If year is ambiguous, assume current year.
- Currency: use ISO 4217 codes. Infer from symbols (€=EUR, $=USD, £=GBP) or country.
- Category: classify into one of: Food, Transport, Accommodation, Entertainment, \
Shopping, Communication, Office, Health, Other.
- Location: extract city and country if visible on receipt. If not on receipt, \
infer from vendor name, language, or currency when confident.
- Line items: extract individual items when clearly readable.
- Confidence: rate 0.0-1.0 based on image quality and extraction certainty.
- If a field cannot be determined, return null for that field."""

RECEIPT_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "vendor": {"type": ["string", "null"]},
        "date": {"type": "string", "description": "ISO 8601 date"},
        "total_amount": {"type": "number"},
        "currency": {"type": "string", "minLength": 3, "maxLength": 3},
        "category": {
            "type": "string",
            "enum": [
                "Food",
                "Transport",
                "Accommodation",
                "Entertainment",
                "Shopping",
                "Communication",
                "Office",
                "Health",
                "Other",
            ],
        },
        "description": {"type": ["string", "null"]},
        "location_city": {"type": ["string", "null"]},
        "location_country": {"type": ["string", "null"]},
        "line_items": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "quantity": {"type": ["number", "null"]},
                    "unit_price": {"type": ["number", "null"]},
                    "total": {"type": "number"},
                },
                "required": ["description", "quantity", "unit_price", "total"],
                "additionalProperties": False,
            },
        },
        "tax_amount": {"type": ["number", "null"]},
        "tip_amount": {"type": ["number", "null"]},
        "payment_method": {"type": ["string", "null"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": [
        "vendor", "date", "total_amount", "currency", "category",
        "description", "location_city", "location_country", "line_items",
        "tax_amount", "tip_amount", "payment_method", "confidence",
    ],
    "additionalProperties": False,
}


async def parse_receipt(image_bytes: bytes) -> tuple[ReceiptParseResult, dict]:
    """Parse a receipt image using GPT-5.4-mini vision.

    Returns (parsed_result, raw_response_dict).
    """
    b64_image = base64.b64encode(image_bytes).decode()

    response = await client.responses.create(
        model="gpt-5.4-mini-2026-03-17",
        input=[
            {"role": "system", "content": RECEIPT_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_image",
                        "image_url": f"data:image/jpeg;base64,{b64_image}",
                    },
                    {
                        "type": "input_text",
                        "text": "Extract all expense data from this receipt.",
                    },
                ],
            },
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "receipt_extraction",
                "schema": RECEIPT_JSON_SCHEMA,
            }
        },
    )

    raw = json.loads(response.output_text)
    logger.info("Receipt parsed: vendor=%s amount=%s %s", raw.get("vendor"), raw.get("total_amount"), raw.get("currency"))
    return ReceiptParseResult.model_validate(raw), raw
