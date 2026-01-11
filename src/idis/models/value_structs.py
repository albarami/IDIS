"""ValueStruct type hierarchy for typed claim and calculation values.

Phase POST-5.2: Provides strong typing for all numeric and structured values
used in claims and calculations. Replaces untyped dict with validated types.

ValueStruct Types:
- MonetaryValue: Decimal amount + currency (ISO 4217)
- PercentageValue: Decimal between 0.0 and 1.0
- CountValue: Non-negative integer
- DateValue: ISO date
- RangeValue: min/max with unit
- TextValue: string with optional semantic tags
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class ValueStructType(str, Enum):
    """Discriminator for ValueStruct subtypes."""

    MONETARY = "monetary"
    PERCENTAGE = "percentage"
    COUNT = "count"
    DATE = "date"
    RANGE = "range"
    TEXT = "text"


class Currency(str, Enum):
    """ISO 4217 currency codes (subset for VC use cases)."""

    USD = "USD"
    EUR = "EUR"
    GBP = "GBP"
    CHF = "CHF"
    JPY = "JPY"
    CNY = "CNY"
    AED = "AED"
    SAR = "SAR"
    QAR = "QAR"
    SGD = "SGD"
    HKD = "HKD"
    INR = "INR"
    BRL = "BRL"
    CAD = "CAD"
    AUD = "AUD"


class TimeWindow(BaseModel):
    """Time window for value context (e.g., FY2024, Q1 2025)."""

    label: str = Field(..., description="Human-readable label (e.g., 'FY2024', 'Q1 2025')")
    start_date: date | None = Field(default=None, description="Start of time window")
    end_date: date | None = Field(default=None, description="End of time window")

    model_config = {"frozen": True, "extra": "forbid"}


class MonetaryValue(BaseModel):
    """Monetary value with currency.

    All monetary values use Decimal for deterministic arithmetic.
    Currency is required and must be a valid ISO 4217 code.
    """

    type: Literal[ValueStructType.MONETARY] = ValueStructType.MONETARY
    amount: Decimal = Field(..., description="Monetary amount as Decimal")
    currency: Currency = Field(..., description="ISO 4217 currency code")
    as_of: date | None = Field(default=None, description="Point-in-time for the value")
    time_window: TimeWindow | None = Field(default=None, description="Time window context")

    @field_validator("amount", mode="before")
    @classmethod
    def coerce_amount(cls, v: object) -> Decimal:
        """Coerce amount to Decimal."""
        if isinstance(v, Decimal):
            return v
        if isinstance(v, (int, float, str)):
            return Decimal(str(v))
        raise ValueError(f"Cannot convert {type(v).__name__} to Decimal")

    model_config = {"frozen": True, "extra": "forbid"}


class PercentageValue(BaseModel):
    """Percentage value between 0.0 and 1.0.

    Stored as decimal (e.g., 0.25 = 25%).
    Values outside [0, 1] are rejected for standard percentages.
    Use allow_overflow=True for growth rates that can exceed 100%.
    """

    type: Literal[ValueStructType.PERCENTAGE] = ValueStructType.PERCENTAGE
    value: Decimal = Field(..., ge=Decimal("0"), description="Percentage as decimal (0.25 = 25%)")
    allow_overflow: bool = Field(
        default=False,
        description="If True, allows values > 1.0 (for growth rates)",
    )
    as_of: date | None = Field(default=None, description="Point-in-time for the value")
    time_window: TimeWindow | None = Field(default=None, description="Time window context")

    @field_validator("value", mode="before")
    @classmethod
    def coerce_value(cls, v: object) -> Decimal:
        """Coerce value to Decimal."""
        if isinstance(v, Decimal):
            return v
        if isinstance(v, (int, float, str)):
            return Decimal(str(v))
        raise ValueError(f"Cannot convert {type(v).__name__} to Decimal")

    @model_validator(mode="after")
    def validate_range(self) -> PercentageValue:
        """Validate percentage is in valid range."""
        if not self.allow_overflow and self.value > Decimal("1"):
            raise ValueError(
                f"Percentage {self.value} exceeds 1.0. Set allow_overflow=True for growth rates."
            )
        return self

    model_config = {"frozen": True, "extra": "forbid"}


class CountValue(BaseModel):
    """Integer count value (non-negative).

    Used for: user counts, customer counts, headcount, etc.
    """

    type: Literal[ValueStructType.COUNT] = ValueStructType.COUNT
    value: int = Field(..., ge=0, description="Non-negative integer count")
    unit: str | None = Field(default=None, description="Unit label (e.g., 'users', 'employees')")
    as_of: date | None = Field(default=None, description="Point-in-time for the value")

    model_config = {"frozen": True, "extra": "forbid"}


class DateValue(BaseModel):
    """Date value (ISO format).

    Used for: founding date, exit date, milestone dates, etc.
    """

    type: Literal[ValueStructType.DATE] = ValueStructType.DATE
    value: date = Field(..., description="ISO date value")
    label: str | None = Field(default=None, description="Semantic label (e.g., 'founded', 'exit')")

    model_config = {"frozen": True, "extra": "forbid"}


class RangeValue(BaseModel):
    """Range value with min/max bounds.

    Used for: valuation ranges, revenue ranges, projections, etc.
    At least one of min_value or max_value must be provided.
    """

    type: Literal[ValueStructType.RANGE] = ValueStructType.RANGE
    min_value: Decimal | None = Field(default=None, description="Minimum value (inclusive)")
    max_value: Decimal | None = Field(default=None, description="Maximum value (inclusive)")
    unit: str = Field(..., description="Unit for the range (e.g., 'USD', 'users')")
    currency: Currency | None = Field(default=None, description="Currency if monetary range")
    as_of: date | None = Field(default=None, description="Point-in-time for the range")
    time_window: TimeWindow | None = Field(default=None, description="Time window context")

    @field_validator("min_value", "max_value", mode="before")
    @classmethod
    def coerce_decimal(cls, v: object) -> Decimal | None:
        """Coerce to Decimal if not None."""
        if v is None:
            return None
        if isinstance(v, Decimal):
            return v
        if isinstance(v, (int, float, str)):
            return Decimal(str(v))
        raise ValueError(f"Cannot convert {type(v).__name__} to Decimal")

    @model_validator(mode="after")
    def validate_bounds(self) -> RangeValue:
        """Validate at least one bound is present and min <= max."""
        if self.min_value is None and self.max_value is None:
            raise ValueError("At least one of min_value or max_value must be provided")
        if (
            self.min_value is not None
            and self.max_value is not None
            and self.min_value > self.max_value
        ):
            raise ValueError(
                f"min_value ({self.min_value}) cannot exceed max_value ({self.max_value})"
            )
        return self

    model_config = {"frozen": True, "extra": "forbid"}


class SemanticTag(str, Enum):
    """Semantic tags for text values."""

    COMPANY_NAME = "company_name"
    FOUNDER_NAME = "founder_name"
    PRODUCT_NAME = "product_name"
    MARKET_SEGMENT = "market_segment"
    COMPETITOR = "competitor"
    INVESTOR = "investor"
    LOCATION = "location"
    INDUSTRY = "industry"
    OTHER = "other"


class TextValue(BaseModel):
    """Text value with optional semantic tags.

    Used for: company names, descriptions, qualitative claims, etc.
    """

    type: Literal[ValueStructType.TEXT] = ValueStructType.TEXT
    value: str = Field(..., min_length=1, description="Text content")
    tags: list[SemanticTag] = Field(
        default_factory=list,
        description="Semantic tags for the text",
    )

    model_config = {"frozen": True, "extra": "forbid"}


# Union type for all ValueStruct variants
ValueStruct = Annotated[
    MonetaryValue | PercentageValue | CountValue | DateValue | RangeValue | TextValue,
    Field(discriminator="type"),
]


def parse_value_struct(data: dict) -> ValueStruct:
    """Parse a dictionary into the appropriate ValueStruct type.

    Args:
        data: Dictionary with 'type' discriminator field.

    Returns:
        Parsed ValueStruct instance.

    Raises:
        ValueError: If type is unknown or data is invalid.
    """
    if not isinstance(data, dict):
        raise ValueError(f"Expected dict, got {type(data).__name__}")

    value_type = data.get("type")
    if value_type is None:
        raise ValueError("Missing 'type' field in ValueStruct data")

    type_map = {
        ValueStructType.MONETARY.value: MonetaryValue,
        ValueStructType.PERCENTAGE.value: PercentageValue,
        ValueStructType.COUNT.value: CountValue,
        ValueStructType.DATE.value: DateValue,
        ValueStructType.RANGE.value: RangeValue,
        ValueStructType.TEXT.value: TextValue,
        "monetary": MonetaryValue,
        "percentage": PercentageValue,
        "count": CountValue,
        "date": DateValue,
        "range": RangeValue,
        "text": TextValue,
    }

    model_class = type_map.get(value_type)
    if model_class is None:
        raise ValueError(f"Unknown ValueStruct type: {value_type}")

    return model_class.model_validate(data)  # type: ignore[no-any-return,attr-defined]


def value_struct_to_dict(value: ValueStruct) -> dict:
    """Convert a ValueStruct to a dictionary for serialization.

    Args:
        value: ValueStruct instance.

    Returns:
        Dictionary representation with proper serialization.
    """
    data = value.model_dump(mode="json")
    # Ensure type is serialized as string value
    if "type" in data and hasattr(data["type"], "value"):
        data["type"] = data["type"].value
    return data
