"""Natural-language report filter via GPT-5.4-mini tool calling.

The model sees one tool (`generate_expense_report`) with typed parameters and
decides which filters to populate from the user's free-text query.
"""

import json
import logging
from datetime import date
from decimal import Decimal

from openai import AsyncOpenAI

from src.config import settings
from src.schemas.expense import ExpenseCategory, ReportFilter

logger = logging.getLogger(__name__)

client = AsyncOpenAI(api_key=settings.openai_api_key)

REPORT_TOOL = {
    "type": "function",
    "name": "generate_expense_report",
    "description": (
        "Generate a PDF+CSV expense report. Fill only the filters the user "
        "explicitly asked for; leave everything else null. Interpret relative "
        "dates (last week, this month, yesterday, q1, etc.) using the "
        "'today' value in the system prompt. Be permissive — prefer broader "
        "matches when the user is ambiguous."
    ),
    "strict": True,
    "parameters": {
        "type": "object",
        "properties": {
            "date_from": {
                "type": ["string", "null"],
                "description": "ISO 8601 start date (inclusive)",
            },
            "date_to": {
                "type": ["string", "null"],
                "description": "ISO 8601 end date (inclusive)",
            },
            "category": {
                "type": ["string", "null"],
                "enum": [*[c.value for c in ExpenseCategory], None],
                "description": "Expense category filter",
            },
            "expense_type": {
                "type": ["string", "null"],
                "enum": ["personal", "company", None],
                "description": "Personal or company tag",
            },
            "vendor": {
                "type": ["string", "null"],
                "description": "Vendor substring match (case-insensitive)",
            },
            "location": {
                "type": ["string", "null"],
                "description": "City or country substring match",
            },
            "currency": {
                "type": ["string", "null"],
                "description": "ISO 4217 currency code (e.g. EUR, GBP)",
            },
            "amount_min": {
                "type": ["number", "null"],
                "description": "Minimum original amount",
            },
            "amount_max": {
                "type": ["number", "null"],
                "description": "Maximum original amount",
            },
            "summary": {
                "type": "string",
                "description": (
                    "Human-readable one-line description of the filters being "
                    "applied, for the user to verify. Example: 'Last 7 days, "
                    "company only'."
                ),
            },
        },
        "required": [
            "date_from", "date_to", "category", "expense_type",
            "vendor", "location", "currency", "amount_min", "amount_max",
            "summary",
        ],
        "additionalProperties": False,
    },
}


async def resolve_filter(query: str) -> tuple[ReportFilter, str]:
    """Translate a natural-language query into a ReportFilter.

    Returns (filter, human_readable_summary). Raises on malformed tool output.
    """
    today = date.today().isoformat()

    response = await client.responses.create(
        model="gpt-5.4-mini-2026-03-17",
        instructions=(
            f"Today's date is {today}. You help a user filter their expense "
            f"tracker. Always call the generate_expense_report tool exactly "
            f"once. Only set fields that the user asked for; leave others null."
        ),
        input=query,
        tools=[REPORT_TOOL],
        tool_choice="required",
    )

    for item in response.output:
        if getattr(item, "type", None) == "function_call":
            args = json.loads(item.arguments)
            logger.info("Report agent resolved: %s -> %s", query, args)
            summary = args.pop("summary", "") or ""
            # Convert dates
            if args.get("date_from"):
                args["date_from"] = date.fromisoformat(args["date_from"])
            if args.get("date_to"):
                args["date_to"] = date.fromisoformat(args["date_to"])
            # Normalize currency
            if args.get("currency"):
                args["currency"] = args["currency"].upper()
            # Decimal for amounts
            if args.get("amount_min") is not None:
                args["amount_min"] = Decimal(str(args["amount_min"]))
            if args.get("amount_max") is not None:
                args["amount_max"] = Decimal(str(args["amount_max"]))
            return ReportFilter(**{k: v for k, v in args.items() if v is not None}), summary

    raise ValueError("Model did not call generate_expense_report")
