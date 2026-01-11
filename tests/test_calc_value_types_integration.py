"""Integration tests for ValueStruct types with calc engine.

Phase POST-5.2: Tests that ValueStruct types integrate correctly with
the deterministic calculation engine and CalcSanad provenance.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from idis.models.value_structs import (
    CountValue,
    Currency,
    MonetaryValue,
    PercentageValue,
    RangeValue,
    TimeWindow,
    parse_value_struct,
    value_struct_to_dict,
)


class TestValueStructCalcIntegration:
    """Tests for ValueStruct integration with calculation inputs/outputs."""

    def test_monetary_value_decimal_precision(self) -> None:
        """Test that MonetaryValue preserves Decimal precision for calcs."""
        value = MonetaryValue(
            amount=Decimal("1234567.8901234567890123456789"),
            currency=Currency.USD,
        )
        # Decimal precision should be preserved
        assert str(value.amount) == "1234567.8901234567890123456789"

    def test_monetary_arithmetic_determinism(self) -> None:
        """Test that MonetaryValue arithmetic is deterministic."""
        v1 = MonetaryValue(amount=Decimal("100.10"), currency=Currency.USD)
        v2 = MonetaryValue(amount=Decimal("200.20"), currency=Currency.USD)

        # Arithmetic on amounts should be exact
        total = v1.amount + v2.amount
        assert total == Decimal("300.30")

        # No floating point errors
        v3 = MonetaryValue(amount=Decimal("0.1"), currency=Currency.USD)
        v4 = MonetaryValue(amount=Decimal("0.2"), currency=Currency.USD)
        assert v3.amount + v4.amount == Decimal("0.3")

    def test_percentage_for_margin_calcs(self) -> None:
        """Test PercentageValue for margin/rate calculations."""
        gross_margin = PercentageValue(value=Decimal("0.65"))
        revenue = MonetaryValue(amount=Decimal("1000000"), currency=Currency.USD)

        # Calculate gross profit
        gross_profit = revenue.amount * gross_margin.value
        assert gross_profit == Decimal("650000")

    def test_percentage_growth_rate(self) -> None:
        """Test PercentageValue for growth rates > 100%."""
        growth = PercentageValue(value=Decimal("2.5"), allow_overflow=True)
        base_revenue = MonetaryValue(amount=Decimal("1000000"), currency=Currency.USD)

        # 250% growth = 2.5x the base
        new_revenue = base_revenue.amount * (Decimal("1") + growth.value)
        assert new_revenue == Decimal("3500000")

    def test_range_for_valuation(self) -> None:
        """Test RangeValue for valuation ranges."""
        valuation_range = RangeValue(
            min_value=Decimal("10000000"),
            max_value=Decimal("15000000"),
            unit="USD",
            currency=Currency.USD,
        )

        # Midpoint calculation
        assert valuation_range.min_value is not None
        assert valuation_range.max_value is not None
        midpoint = (valuation_range.min_value + valuation_range.max_value) / 2
        assert midpoint == Decimal("12500000")

    def test_count_for_user_metrics(self) -> None:
        """Test CountValue for user/customer metrics."""
        users_start = CountValue(value=10000, unit="users", as_of=date(2024, 1, 1))
        users_end = CountValue(value=15000, unit="users", as_of=date(2024, 12, 31))

        # Growth calculation
        growth = (users_end.value - users_start.value) / users_start.value
        assert growth == 0.5  # 50% growth

    def test_time_window_for_periodic_metrics(self) -> None:
        """Test TimeWindow context for periodic metrics."""
        tw_q1 = TimeWindow(
            label="Q1 2024",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 3, 31),
        )
        revenue_q1 = MonetaryValue(
            amount=Decimal("2500000"),
            currency=Currency.USD,
            time_window=tw_q1,
        )

        assert revenue_q1.time_window is not None
        assert revenue_q1.time_window.label == "Q1 2024"

        # Annualize Q1 revenue
        annualized = revenue_q1.amount * 4
        assert annualized == Decimal("10000000")


class TestValueStructSerializationForCalc:
    """Tests for ValueStruct serialization for calc engine storage."""

    def test_serialize_preserves_precision(self) -> None:
        """Test that serialization preserves Decimal precision."""
        value = MonetaryValue(
            amount=Decimal("123456789.123456789"),
            currency=Currency.USD,
        )
        data = value_struct_to_dict(value)
        parsed = parse_value_struct(data)

        assert isinstance(parsed, MonetaryValue)
        assert parsed.amount == value.amount

    def test_serialize_for_reproducibility_hash(self) -> None:
        """Test that serialized form is stable for hashing."""
        value = MonetaryValue(
            amount=Decimal("1000000"),
            currency=Currency.USD,
            as_of=date(2024, 6, 30),
        )

        # Multiple serializations should be identical
        data1 = value_struct_to_dict(value)
        data2 = value_struct_to_dict(value)
        assert data1 == data2

    def test_serialize_complex_value_for_audit(self) -> None:
        """Test serialization of complex ValueStruct for audit trail."""
        tw = TimeWindow(
            label="FY2024",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
        )
        value = RangeValue(
            min_value=Decimal("50000000"),
            max_value=Decimal("75000000"),
            unit="USD",
            currency=Currency.USD,
            time_window=tw,
        )

        data = value_struct_to_dict(value)
        assert data["type"] == "range"
        assert data["min_value"] == "50000000"
        assert data["max_value"] == "75000000"
        assert data["time_window"]["label"] == "FY2024"


class TestValueStructInputValidation:
    """Tests for ValueStruct input validation for calc engine."""

    def test_reject_invalid_currency(self) -> None:
        """Test that invalid currency is rejected."""
        with pytest.raises(ValueError):
            MonetaryValue(amount=Decimal("1000"), currency="INVALID")  # type: ignore[arg-type]

    def test_reject_negative_monetary(self) -> None:
        """Test that negative monetary amounts work (for losses, etc)."""
        # Negative amounts are valid (e.g., net loss)
        value = MonetaryValue(amount=Decimal("-500000"), currency=Currency.USD)
        assert value.amount == Decimal("-500000")

    def test_reject_invalid_percentage_range(self) -> None:
        """Test percentage validation without overflow flag."""
        with pytest.raises(ValueError):
            PercentageValue(value=Decimal("1.5"))  # 150% without overflow

    def test_reject_inverted_range(self) -> None:
        """Test that min > max is rejected."""
        with pytest.raises(ValueError):
            RangeValue(
                min_value=Decimal("100"),
                max_value=Decimal("50"),
                unit="USD",
            )


class TestValueStructTypeDiscrimination:
    """Tests for ValueStruct type discrimination in collections."""

    def test_mixed_value_collection(self) -> None:
        """Test handling mixed ValueStruct types in a collection."""
        values = [
            MonetaryValue(amount=Decimal("1000000"), currency=Currency.USD),
            PercentageValue(value=Decimal("0.25")),
            CountValue(value=5000, unit="users"),
        ]

        # Serialize all
        serialized = [value_struct_to_dict(v) for v in values]
        assert len(serialized) == 3

        # Parse all back
        parsed = [parse_value_struct(d) for d in serialized]
        assert isinstance(parsed[0], MonetaryValue)
        assert isinstance(parsed[1], PercentageValue)
        assert isinstance(parsed[2], CountValue)

    def test_filter_by_type(self) -> None:
        """Test filtering ValueStruct collection by type."""
        values = [
            MonetaryValue(amount=Decimal("1000000"), currency=Currency.USD),
            MonetaryValue(amount=Decimal("500000"), currency=Currency.EUR),
            PercentageValue(value=Decimal("0.25")),
            CountValue(value=5000, unit="users"),
        ]

        # Filter to only monetary values
        monetary_values = [v for v in values if isinstance(v, MonetaryValue)]
        assert len(monetary_values) == 2

        # Sum USD amounts
        usd_total = sum(v.amount for v in monetary_values if v.currency == Currency.USD)
        assert usd_total == Decimal("1000000")
