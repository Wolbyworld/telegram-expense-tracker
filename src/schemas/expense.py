from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field


class ExpenseCategory(StrEnum):
    FOOD = "Food"
    TRANSPORT = "Transport"
    ACCOMMODATION = "Accommodation"
    ENTERTAINMENT = "Entertainment"
    SHOPPING = "Shopping"
    COMMUNICATION = "Communication"
    OFFICE = "Office"
    HEALTH = "Health"
    OTHER = "Other"


class LineItem(BaseModel):
    description: str
    quantity: float | None = None
    unit_price: float | None = None
    total: float


class ReceiptParseResult(BaseModel):
    """Schema matching GPT-5.4-mini structured output for receipt extraction."""

    vendor: str | None = None
    date: str = Field(description="ISO 8601 date (YYYY-MM-DD)")
    total_amount: float
    currency: str = Field(min_length=3, max_length=3)
    category: ExpenseCategory
    description: str | None = None
    location_city: str | None = None
    location_country: str | None = None
    line_items: list[LineItem] | None = None
    tax_amount: float | None = None
    tip_amount: float | None = None
    payment_method: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)


class ExpenseCreate(BaseModel):
    """For creating an expense from parsed receipt or manual entry."""

    vendor: str | None = None
    description: str | None = None
    category: ExpenseCategory = ExpenseCategory.OTHER
    date: date
    original_amount: Decimal
    original_currency: str = Field(min_length=3, max_length=3)
    location_city: str | None = None
    location_country: str | None = None
    line_items: list[LineItem] | None = None
    source: str = "image"
    confidence: float | None = None


class ExpenseResponse(BaseModel):
    """Expense as returned to the user."""

    model_config = {"from_attributes": True}

    id: int
    vendor: str | None
    description: str | None
    category: str | None
    date: date
    original_amount: Decimal
    original_currency: str
    eur_amount: Decimal | None
    location_city: str | None
    location_country: str | None
    source: str


class ReportFilter(BaseModel):
    """Filters for generating expense reports."""

    date_from: date | None = None
    date_to: date | None = None
    vendor: str | None = None
    category: ExpenseCategory | None = None
    location: str | None = None
    currency: str | None = None
    amount_min: Decimal | None = None
    amount_max: Decimal | None = None
    expense_type: str | None = None  # 'personal' | 'company'
