"""Tests for deterministic calculation reproducibility.

Phase 4.1: Hash stability tests for Calc Engine.

Tests verify:
- Same inputs → same hash
- Reorder input_claim_ids → hash unchanged
- Change one input → hash changes
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from idis.calc.engine import CalcEngine, CalcMissingInputError, InputGradeInfo
from idis.calc.formulas.core import register_core_formulas
from idis.calc.formulas.registry import FormulaRegistry
from idis.models.calc_sanad import SanadGrade
from idis.models.deterministic_calculation import CalcType


@pytest.fixture
def registry() -> FormulaRegistry:
    """Create a fresh formula registry with core formulas."""
    FormulaRegistry.reset_instance()
    reg = FormulaRegistry()
    register_core_formulas(reg)
    return reg


@pytest.fixture
def engine(registry: FormulaRegistry) -> CalcEngine:
    """Create a calc engine with the test registry."""
    return CalcEngine(registry=registry, code_version="test-1.0.0")


class TestSameInputsSameHash:
    """Test that identical inputs produce identical hashes."""

    def test_runway_same_inputs_same_hash(self, engine: CalcEngine) -> None:
        """Runway calculation with same inputs produces same hash."""
        tenant_id = "11111111-1111-1111-1111-111111111111"
        deal_id = "22222222-2222-2222-2222-222222222222"

        input_values = {
            "cash_balance": Decimal("1000000"),
            "monthly_burn_rate": Decimal("50000"),
        }
        input_grades = [
            InputGradeInfo(claim_id="claim-1", grade=SanadGrade.A),
            InputGradeInfo(claim_id="claim-2", grade=SanadGrade.B),
        ]

        result1 = engine.run(
            tenant_id=tenant_id,
            deal_id=deal_id,
            calc_type=CalcType.RUNWAY,
            input_values=input_values,
            input_grades=input_grades,
        )

        result2 = engine.run(
            tenant_id=tenant_id,
            deal_id=deal_id,
            calc_type=CalcType.RUNWAY,
            input_values=input_values,
            input_grades=input_grades,
        )

        assert result1.calculation.reproducibility_hash == result2.calculation.reproducibility_hash
        assert result1.calculation.formula_hash == result2.calculation.formula_hash

    def test_gross_margin_same_inputs_same_hash(self, engine: CalcEngine) -> None:
        """Gross margin calculation with same inputs produces same hash."""
        tenant_id = "11111111-1111-1111-1111-111111111111"
        deal_id = "22222222-2222-2222-2222-222222222222"

        input_values = {
            "revenue": Decimal("500000"),
            "cogs": Decimal("175000"),
        }
        input_grades = [
            InputGradeInfo(claim_id="claim-a", grade=SanadGrade.A),
        ]

        result1 = engine.run(
            tenant_id=tenant_id,
            deal_id=deal_id,
            calc_type=CalcType.GROSS_MARGIN,
            input_values=input_values,
            input_grades=input_grades,
        )

        result2 = engine.run(
            tenant_id=tenant_id,
            deal_id=deal_id,
            calc_type=CalcType.GROSS_MARGIN,
            input_values=input_values,
            input_grades=input_grades,
        )

        assert result1.calculation.reproducibility_hash == result2.calculation.reproducibility_hash


class TestClaimIdOrderingDoesNotAffectHash:
    """Test that reordering input_claim_ids does not change the hash."""

    def test_reorder_claim_ids_same_hash(self, engine: CalcEngine) -> None:
        """Reordering claim IDs produces the same reproducibility hash."""
        tenant_id = "11111111-1111-1111-1111-111111111111"
        deal_id = "22222222-2222-2222-2222-222222222222"

        input_values = {
            "cash_balance": Decimal("2000000"),
            "monthly_burn_rate": Decimal("100000"),
        }

        grades_order_1 = [
            InputGradeInfo(claim_id="aaaa-claim", grade=SanadGrade.A),
            InputGradeInfo(claim_id="bbbb-claim", grade=SanadGrade.B),
            InputGradeInfo(claim_id="cccc-claim", grade=SanadGrade.A),
        ]

        grades_order_2 = [
            InputGradeInfo(claim_id="cccc-claim", grade=SanadGrade.A),
            InputGradeInfo(claim_id="aaaa-claim", grade=SanadGrade.A),
            InputGradeInfo(claim_id="bbbb-claim", grade=SanadGrade.B),
        ]

        result1 = engine.run(
            tenant_id=tenant_id,
            deal_id=deal_id,
            calc_type=CalcType.RUNWAY,
            input_values=input_values,
            input_grades=grades_order_1,
        )

        result2 = engine.run(
            tenant_id=tenant_id,
            deal_id=deal_id,
            calc_type=CalcType.RUNWAY,
            input_values=input_values,
            input_grades=grades_order_2,
        )

        assert result1.calculation.reproducibility_hash == result2.calculation.reproducibility_hash
        assert sorted(result1.calculation.inputs.claim_ids) == sorted(
            result2.calculation.inputs.claim_ids
        )


class TestInputChangesAffectHash:
    """Test that changing inputs produces different hashes."""

    def test_different_input_value_different_hash(self, engine: CalcEngine) -> None:
        """Changing an input value produces a different hash."""
        tenant_id = "11111111-1111-1111-1111-111111111111"
        deal_id = "22222222-2222-2222-2222-222222222222"

        input_grades = [InputGradeInfo(claim_id="claim-1", grade=SanadGrade.A)]

        result1 = engine.run(
            tenant_id=tenant_id,
            deal_id=deal_id,
            calc_type=CalcType.RUNWAY,
            input_values={
                "cash_balance": Decimal("1000000"),
                "monthly_burn_rate": Decimal("50000"),
            },
            input_grades=input_grades,
        )

        result2 = engine.run(
            tenant_id=tenant_id,
            deal_id=deal_id,
            calc_type=CalcType.RUNWAY,
            input_values={
                "cash_balance": Decimal("1000001"),  # Changed by 1
                "monthly_burn_rate": Decimal("50000"),
            },
            input_grades=input_grades,
        )

        assert result1.calculation.reproducibility_hash != result2.calculation.reproducibility_hash

    def test_different_tenant_different_hash(self, engine: CalcEngine) -> None:
        """Different tenant_id produces different hash."""
        deal_id = "22222222-2222-2222-2222-222222222222"

        input_values = {
            "cash_balance": Decimal("1000000"),
            "monthly_burn_rate": Decimal("50000"),
        }
        input_grades = [InputGradeInfo(claim_id="claim-1", grade=SanadGrade.A)]

        result1 = engine.run(
            tenant_id="11111111-1111-1111-1111-111111111111",
            deal_id=deal_id,
            calc_type=CalcType.RUNWAY,
            input_values=input_values,
            input_grades=input_grades,
        )

        result2 = engine.run(
            tenant_id="99999999-9999-9999-9999-999999999999",
            deal_id=deal_id,
            calc_type=CalcType.RUNWAY,
            input_values=input_values,
            input_grades=input_grades,
        )

        assert result1.calculation.reproducibility_hash != result2.calculation.reproducibility_hash

    def test_different_deal_different_hash(self, engine: CalcEngine) -> None:
        """Different deal_id produces different hash."""
        tenant_id = "11111111-1111-1111-1111-111111111111"

        input_values = {
            "cash_balance": Decimal("1000000"),
            "monthly_burn_rate": Decimal("50000"),
        }
        input_grades = [InputGradeInfo(claim_id="claim-1", grade=SanadGrade.A)]

        result1 = engine.run(
            tenant_id=tenant_id,
            deal_id="22222222-2222-2222-2222-222222222222",
            calc_type=CalcType.RUNWAY,
            input_values=input_values,
            input_grades=input_grades,
        )

        result2 = engine.run(
            tenant_id=tenant_id,
            deal_id="33333333-3333-3333-3333-333333333333",
            calc_type=CalcType.RUNWAY,
            input_values=input_values,
            input_grades=input_grades,
        )

        assert result1.calculation.reproducibility_hash != result2.calculation.reproducibility_hash


class TestFormulaHashStability:
    """Test that formula hashes are stable across runs."""

    def test_formula_hash_stable(self, registry: FormulaRegistry) -> None:
        """Formula hash is deterministic for same spec."""
        spec = registry.get(CalcType.RUNWAY)
        assert spec is not None

        hash1 = spec.formula_hash
        hash2 = spec.formula_hash

        assert hash1 == hash2
        assert len(hash1) == 64  # SHA256 hex length

    def test_different_calc_types_different_formula_hash(self, registry: FormulaRegistry) -> None:
        """Different calc types have different formula hashes."""
        runway_spec = registry.get(CalcType.RUNWAY)
        gm_spec = registry.get(CalcType.GROSS_MARGIN)

        assert runway_spec is not None
        assert gm_spec is not None
        assert runway_spec.formula_hash != gm_spec.formula_hash


class TestFailClosedValidation:
    """Test that missing inputs cause fail-closed errors."""

    def test_missing_required_input_raises(self, engine: CalcEngine) -> None:
        """Missing required input raises CalcMissingInputError."""
        tenant_id = "11111111-1111-1111-1111-111111111111"
        deal_id = "22222222-2222-2222-2222-222222222222"

        with pytest.raises(CalcMissingInputError) as exc_info:
            engine.run(
                tenant_id=tenant_id,
                deal_id=deal_id,
                calc_type=CalcType.RUNWAY,
                input_values={
                    "cash_balance": Decimal("1000000"),
                    # Missing: monthly_burn_rate
                },
                input_grades=[],
            )

        assert "monthly_burn_rate" in exc_info.value.missing_inputs
        assert exc_info.value.calc_type == CalcType.RUNWAY

    def test_all_required_inputs_missing_raises(self, engine: CalcEngine) -> None:
        """All missing inputs are reported in the exception."""
        tenant_id = "11111111-1111-1111-1111-111111111111"
        deal_id = "22222222-2222-2222-2222-222222222222"

        with pytest.raises(CalcMissingInputError) as exc_info:
            engine.run(
                tenant_id=tenant_id,
                deal_id=deal_id,
                calc_type=CalcType.RUNWAY,
                input_values={},  # All missing
                input_grades=[],
            )

        assert "cash_balance" in exc_info.value.missing_inputs
        assert "monthly_burn_rate" in exc_info.value.missing_inputs


class TestDecimalOnlyArithmetic:
    """Test that all arithmetic uses Decimal (no float)."""

    def test_output_is_decimal(self, engine: CalcEngine) -> None:
        """Output primary_value is Decimal, not float."""
        tenant_id = "11111111-1111-1111-1111-111111111111"
        deal_id = "22222222-2222-2222-2222-222222222222"

        result = engine.run(
            tenant_id=tenant_id,
            deal_id=deal_id,
            calc_type=CalcType.RUNWAY,
            input_values={
                "cash_balance": Decimal("1000000"),
                "monthly_burn_rate": Decimal("50000"),
            },
            input_grades=[],
        )

        assert isinstance(result.calculation.output.primary_value, Decimal)
        assert result.calculation.output.primary_value == Decimal("20.0000")

    def test_inputs_stored_as_decimal(self, engine: CalcEngine) -> None:
        """Input values are stored as Decimal."""
        tenant_id = "11111111-1111-1111-1111-111111111111"
        deal_id = "22222222-2222-2222-2222-222222222222"

        input_values = {
            "cash_balance": Decimal("1000000.50"),
            "monthly_burn_rate": Decimal("50000.25"),
        }

        result = engine.run(
            tenant_id=tenant_id,
            deal_id=deal_id,
            calc_type=CalcType.RUNWAY,
            input_values=input_values,
            input_grades=[],
        )

        for key, value in result.calculation.inputs.values.items():
            assert isinstance(value, Decimal), f"Input {key} should be Decimal"


class TestOutputPrecision:
    """Test that output values are quantized correctly."""

    def test_output_quantized_to_4_decimal_places(self, engine: CalcEngine) -> None:
        """Output is quantized to 4 decimal places."""
        tenant_id = "11111111-1111-1111-1111-111111111111"
        deal_id = "22222222-2222-2222-2222-222222222222"

        result = engine.run(
            tenant_id=tenant_id,
            deal_id=deal_id,
            calc_type=CalcType.RUNWAY,
            input_values={
                "cash_balance": Decimal("1000000"),
                "monthly_burn_rate": Decimal("33333"),  # Results in repeating decimal
            },
            input_grades=[],
        )

        output_str = str(result.calculation.output.primary_value)
        decimal_places = len(output_str.split(".")[1]) if "." in output_str else 0
        assert decimal_places == 4
