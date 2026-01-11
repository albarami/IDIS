"""Unit tests for ValueStruct type hierarchy.

Phase POST-5.2: Tests for MonetaryValue, PercentageValue, CountValue,
DateValue, RangeValue, TextValue and parse/serialize utilities.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from idis.models.value_structs import (
    CountValue,
    Currency,
    DateValue,
    MonetaryValue,
    PercentageValue,
    RangeValue,
    SemanticTag,
    TextValue,
    TimeWindow,
    ValueStructType,
    parse_value_struct,
    value_struct_to_dict,
)


class TestMonetaryValue:
    """Tests for MonetaryValue type."""

    def test_create_basic(self) -> None:
        """Test creating a basic MonetaryValue."""
        value = MonetaryValue(
            amount=Decimal("1000000"),
            currency=Currency.USD,
        )
        assert value.type == ValueStructType.MONETARY
        assert value.amount == Decimal("1000000")
        assert value.currency == Currency.USD
        assert value.as_of is None
        assert value.time_window is None

    def test_create_with_time_window(self) -> None:
        """Test MonetaryValue with time window context."""
        tw = TimeWindow(
            label="FY2024",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
        )
        value = MonetaryValue(
            amount=Decimal("5000000.50"),
            currency=Currency.EUR,
            as_of=date(2024, 6, 30),
            time_window=tw,
        )
        assert value.amount == Decimal("5000000.50")
        assert value.time_window is not None
        assert value.time_window.label == "FY2024"

    def test_coerce_from_int(self) -> None:
        """Test amount coercion from int."""
        value = MonetaryValue(amount=1000, currency=Currency.USD)
        assert value.amount == Decimal("1000")

    def test_coerce_from_float(self) -> None:
        """Test amount coercion from float."""
        value = MonetaryValue(amount=1000.50, currency=Currency.USD)
        assert value.amount == Decimal("1000.5")

    def test_coerce_from_string(self) -> None:
        """Test amount coercion from string."""
        value = MonetaryValue(amount="1234567.89", currency=Currency.USD)
        assert value.amount == Decimal("1234567.89")

    def test_frozen_immutable(self) -> None:
        """Test that MonetaryValue is immutable."""
        from pydantic import ValidationError

        value = MonetaryValue(amount=Decimal("1000"), currency=Currency.USD)
        with pytest.raises(ValidationError):
            value.amount = Decimal("2000")  # type: ignore[misc]


class TestPercentageValue:
    """Tests for PercentageValue type."""

    def test_create_basic(self) -> None:
        """Test creating a basic PercentageValue."""
        value = PercentageValue(value=Decimal("0.25"))
        assert value.type == ValueStructType.PERCENTAGE
        assert value.value == Decimal("0.25")
        assert value.allow_overflow is False

    def test_zero_percent(self) -> None:
        """Test 0% is valid."""
        value = PercentageValue(value=Decimal("0"))
        assert value.value == Decimal("0")

    def test_hundred_percent(self) -> None:
        """Test 100% (1.0) is valid."""
        value = PercentageValue(value=Decimal("1"))
        assert value.value == Decimal("1")

    def test_reject_overflow_by_default(self) -> None:
        """Test that values > 1.0 are rejected without allow_overflow."""
        with pytest.raises(ValueError, match="exceeds 1.0"):
            PercentageValue(value=Decimal("1.5"))

    def test_allow_overflow_for_growth(self) -> None:
        """Test that growth rates > 100% work with allow_overflow."""
        value = PercentageValue(value=Decimal("2.5"), allow_overflow=True)
        assert value.value == Decimal("2.5")  # 250% growth

    def test_reject_negative(self) -> None:
        """Test that negative percentages are rejected."""
        with pytest.raises(ValueError):
            PercentageValue(value=Decimal("-0.1"))


class TestCountValue:
    """Tests for CountValue type."""

    def test_create_basic(self) -> None:
        """Test creating a basic CountValue."""
        value = CountValue(value=1000)
        assert value.type == ValueStructType.COUNT
        assert value.value == 1000
        assert value.unit is None

    def test_create_with_unit(self) -> None:
        """Test CountValue with unit label."""
        value = CountValue(value=50000, unit="users", as_of=date(2024, 12, 31))
        assert value.value == 50000
        assert value.unit == "users"

    def test_zero_count(self) -> None:
        """Test 0 is valid."""
        value = CountValue(value=0)
        assert value.value == 0

    def test_reject_negative(self) -> None:
        """Test that negative counts are rejected."""
        with pytest.raises(ValueError):
            CountValue(value=-1)


class TestDateValue:
    """Tests for DateValue type."""

    def test_create_basic(self) -> None:
        """Test creating a basic DateValue."""
        value = DateValue(value=date(2020, 3, 15))
        assert value.type == ValueStructType.DATE
        assert value.value == date(2020, 3, 15)
        assert value.label is None

    def test_create_with_label(self) -> None:
        """Test DateValue with semantic label."""
        value = DateValue(value=date(2018, 1, 1), label="founded")
        assert value.label == "founded"


class TestRangeValue:
    """Tests for RangeValue type."""

    def test_create_with_both_bounds(self) -> None:
        """Test RangeValue with both min and max."""
        value = RangeValue(
            min_value=Decimal("10000000"),
            max_value=Decimal("15000000"),
            unit="USD",
            currency=Currency.USD,
        )
        assert value.type == ValueStructType.RANGE
        assert value.min_value == Decimal("10000000")
        assert value.max_value == Decimal("15000000")

    def test_create_min_only(self) -> None:
        """Test RangeValue with min only (open-ended upper)."""
        value = RangeValue(min_value=Decimal("1000"), unit="users")
        assert value.min_value == Decimal("1000")
        assert value.max_value is None

    def test_create_max_only(self) -> None:
        """Test RangeValue with max only (open-ended lower)."""
        value = RangeValue(max_value=Decimal("5000000"), unit="USD")
        assert value.min_value is None
        assert value.max_value == Decimal("5000000")

    def test_reject_no_bounds(self) -> None:
        """Test that at least one bound is required."""
        with pytest.raises(ValueError, match="(?i)at least one"):
            RangeValue(unit="USD")

    def test_reject_invalid_bounds(self) -> None:
        """Test that min cannot exceed max."""
        with pytest.raises(ValueError, match="cannot exceed"):
            RangeValue(
                min_value=Decimal("1000"),
                max_value=Decimal("500"),
                unit="USD",
            )


class TestTextValue:
    """Tests for TextValue type."""

    def test_create_basic(self) -> None:
        """Test creating a basic TextValue."""
        value = TextValue(value="Acme Corp")
        assert value.type == ValueStructType.TEXT
        assert value.value == "Acme Corp"
        assert value.tags == []

    def test_create_with_tags(self) -> None:
        """Test TextValue with semantic tags."""
        value = TextValue(
            value="Acme Corp",
            tags=[SemanticTag.COMPANY_NAME, SemanticTag.COMPETITOR],
        )
        assert SemanticTag.COMPANY_NAME in value.tags
        assert SemanticTag.COMPETITOR in value.tags

    def test_reject_empty_string(self) -> None:
        """Test that empty string is rejected."""
        with pytest.raises(ValueError):
            TextValue(value="")


class TestTimeWindow:
    """Tests for TimeWindow type."""

    def test_create_basic(self) -> None:
        """Test creating a basic TimeWindow."""
        tw = TimeWindow(label="Q1 2025")
        assert tw.label == "Q1 2025"
        assert tw.start_date is None
        assert tw.end_date is None

    def test_create_full(self) -> None:
        """Test TimeWindow with all fields."""
        tw = TimeWindow(
            label="FY2024",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
        )
        assert tw.start_date == date(2024, 1, 1)
        assert tw.end_date == date(2024, 12, 31)


class TestParseValueStruct:
    """Tests for parse_value_struct function."""

    def test_parse_monetary(self) -> None:
        """Test parsing MonetaryValue from dict."""
        data = {
            "type": "monetary",
            "amount": "1000000",
            "currency": "USD",
        }
        value = parse_value_struct(data)
        assert isinstance(value, MonetaryValue)
        assert value.amount == Decimal("1000000")

    def test_parse_percentage(self) -> None:
        """Test parsing PercentageValue from dict."""
        data = {"type": "percentage", "value": "0.35"}
        value = parse_value_struct(data)
        assert isinstance(value, PercentageValue)
        assert value.value == Decimal("0.35")

    def test_parse_count(self) -> None:
        """Test parsing CountValue from dict."""
        data = {"type": "count", "value": 5000, "unit": "employees"}
        value = parse_value_struct(data)
        assert isinstance(value, CountValue)
        assert value.value == 5000

    def test_parse_date(self) -> None:
        """Test parsing DateValue from dict."""
        data = {"type": "date", "value": "2020-03-15", "label": "founded"}
        value = parse_value_struct(data)
        assert isinstance(value, DateValue)
        assert value.value == date(2020, 3, 15)

    def test_parse_range(self) -> None:
        """Test parsing RangeValue from dict."""
        data = {
            "type": "range",
            "min_value": "10000000",
            "max_value": "15000000",
            "unit": "USD",
        }
        value = parse_value_struct(data)
        assert isinstance(value, RangeValue)

    def test_parse_text(self) -> None:
        """Test parsing TextValue from dict."""
        data = {"type": "text", "value": "Acme Corp", "tags": ["company_name"]}
        value = parse_value_struct(data)
        assert isinstance(value, TextValue)
        assert value.value == "Acme Corp"

    def test_reject_missing_type(self) -> None:
        """Test that missing type field is rejected."""
        with pytest.raises(ValueError, match="Missing 'type' field"):
            parse_value_struct({"value": 100})

    def test_reject_unknown_type(self) -> None:
        """Test that unknown type is rejected."""
        with pytest.raises(ValueError, match="Unknown ValueStruct type"):
            parse_value_struct({"type": "unknown", "value": 100})

    def test_reject_non_dict(self) -> None:
        """Test that non-dict input is rejected."""
        with pytest.raises(ValueError, match="Expected dict"):
            parse_value_struct("not a dict")  # type: ignore[arg-type]


class TestValueStructToDict:
    """Tests for value_struct_to_dict function."""

    def test_serialize_monetary(self) -> None:
        """Test serializing MonetaryValue to dict."""
        value = MonetaryValue(amount=Decimal("1000000"), currency=Currency.USD)
        data = value_struct_to_dict(value)
        assert data["type"] == "monetary"
        assert data["amount"] == "1000000"
        assert data["currency"] == "USD"

    def test_serialize_percentage(self) -> None:
        """Test serializing PercentageValue to dict."""
        value = PercentageValue(value=Decimal("0.25"))
        data = value_struct_to_dict(value)
        assert data["type"] == "percentage"
        assert data["value"] == "0.25"

    def test_roundtrip(self) -> None:
        """Test that serialize -> parse roundtrips correctly."""
        original = MonetaryValue(
            amount=Decimal("5000000.50"),
            currency=Currency.EUR,
            as_of=date(2024, 6, 30),
        )
        data = value_struct_to_dict(original)
        parsed = parse_value_struct(data)
        assert isinstance(parsed, MonetaryValue)
        assert parsed.amount == original.amount
        assert parsed.currency == original.currency
