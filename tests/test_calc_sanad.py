"""Tests for Calc-Sanad provenance and grade derivation.

Phase 4.1: Tests for CalcSanad grade computation and tamper detection.

Tests verify:
- Provenance fields are present
- Grade derivation = min grade
- D propagation for material inputs
- Tamper detection via verify_reproducibility
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from idis.calc.engine import CalcEngine, CalcIntegrityError, InputGradeInfo
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
    """Create a calc engine with the test registry.

    Note: enforce_extraction_gate=False for Phase 4.1 tests focused on
    grade derivation. Phase 4.2 extraction gate tests are in test_extraction_gate.py.
    """
    return CalcEngine(registry=registry, code_version="test-1.0.0", enforce_extraction_gate=False)


class TestProvenanceFieldsPresent:
    """Test that all required provenance fields are populated."""

    def test_calc_sanad_has_all_fields(self, engine: CalcEngine) -> None:
        """CalcSanad contains all required provenance fields."""
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
            input_grades=[
                InputGradeInfo(claim_id="claim-1", grade=SanadGrade.A),
                InputGradeInfo(claim_id="claim-2", grade=SanadGrade.B),
            ],
        )

        sanad = result.calc_sanad

        assert sanad.calc_sanad_id is not None
        assert sanad.tenant_id == tenant_id
        assert sanad.calc_id == result.calculation.calc_id
        assert len(sanad.input_claim_ids) == 2
        assert sanad.input_min_sanad_grade is not None
        assert sanad.calc_grade is not None
        assert sanad.explanation is not None
        assert sanad.created_at is not None

    def test_calculation_has_all_fields(self, engine: CalcEngine) -> None:
        """DeterministicCalculation contains all required fields."""
        tenant_id = "11111111-1111-1111-1111-111111111111"
        deal_id = "22222222-2222-2222-2222-222222222222"

        result = engine.run(
            tenant_id=tenant_id,
            deal_id=deal_id,
            calc_type=CalcType.GROSS_MARGIN,
            input_values={
                "revenue": Decimal("500000"),
                "cogs": Decimal("175000"),
            },
            input_grades=[],
        )

        calc = result.calculation

        assert calc.calc_id is not None
        assert calc.tenant_id == tenant_id
        assert calc.deal_id == deal_id
        assert calc.calc_type == CalcType.GROSS_MARGIN
        assert calc.inputs is not None
        assert calc.formula_hash is not None
        assert len(calc.formula_hash) == 64  # SHA256
        assert calc.code_version == "test-1.0.0"
        assert calc.output is not None
        assert calc.reproducibility_hash is not None
        assert len(calc.reproducibility_hash) == 64  # SHA256


class TestGradeDerivationMinGrade:
    """Test that calc_grade is derived as minimum of input grades."""

    def test_single_grade_a_input(self, engine: CalcEngine) -> None:
        """Single grade A input → calc_grade = A."""
        result = engine.run(
            tenant_id="11111111-1111-1111-1111-111111111111",
            deal_id="22222222-2222-2222-2222-222222222222",
            calc_type=CalcType.RUNWAY,
            input_values={
                "cash_balance": Decimal("1000000"),
                "monthly_burn_rate": Decimal("50000"),
            },
            input_grades=[
                InputGradeInfo(claim_id="claim-1", grade=SanadGrade.A),
            ],
        )

        assert result.calc_sanad.input_min_sanad_grade == SanadGrade.A
        assert result.calc_sanad.calc_grade == SanadGrade.A

    def test_mixed_grades_takes_minimum(self, engine: CalcEngine) -> None:
        """Mixed grades A, B, C → calc_grade = C (minimum)."""
        result = engine.run(
            tenant_id="11111111-1111-1111-1111-111111111111",
            deal_id="22222222-2222-2222-2222-222222222222",
            calc_type=CalcType.RUNWAY,
            input_values={
                "cash_balance": Decimal("1000000"),
                "monthly_burn_rate": Decimal("50000"),
            },
            input_grades=[
                InputGradeInfo(claim_id="claim-1", grade=SanadGrade.A),
                InputGradeInfo(claim_id="claim-2", grade=SanadGrade.B),
                InputGradeInfo(claim_id="claim-3", grade=SanadGrade.C),
            ],
        )

        assert result.calc_sanad.input_min_sanad_grade == SanadGrade.C
        assert result.calc_sanad.calc_grade == SanadGrade.C

    def test_all_grade_b_inputs(self, engine: CalcEngine) -> None:
        """All grade B inputs → calc_grade = B."""
        result = engine.run(
            tenant_id="11111111-1111-1111-1111-111111111111",
            deal_id="22222222-2222-2222-2222-222222222222",
            calc_type=CalcType.RUNWAY,
            input_values={
                "cash_balance": Decimal("1000000"),
                "monthly_burn_rate": Decimal("50000"),
            },
            input_grades=[
                InputGradeInfo(claim_id="claim-1", grade=SanadGrade.B),
                InputGradeInfo(claim_id="claim-2", grade=SanadGrade.B),
            ],
        )

        assert result.calc_sanad.input_min_sanad_grade == SanadGrade.B
        assert result.calc_sanad.calc_grade == SanadGrade.B


class TestGradeDPropagation:
    """Test that grade D propagates as hard gate."""

    def test_material_grade_d_forces_calc_grade_d(self, engine: CalcEngine) -> None:
        """Any material input with grade D → calc_grade = D."""
        result = engine.run(
            tenant_id="11111111-1111-1111-1111-111111111111",
            deal_id="22222222-2222-2222-2222-222222222222",
            calc_type=CalcType.RUNWAY,
            input_values={
                "cash_balance": Decimal("1000000"),
                "monthly_burn_rate": Decimal("50000"),
            },
            input_grades=[
                InputGradeInfo(claim_id="claim-1", grade=SanadGrade.A, is_material=True),
                InputGradeInfo(claim_id="claim-2", grade=SanadGrade.D, is_material=True),
            ],
        )

        assert result.calc_sanad.calc_grade == SanadGrade.D

        has_material_grade_d = any(
            "calc_grade = D" in (entry.impact or "") for entry in result.calc_sanad.explanation
        )
        assert has_material_grade_d, "Explanation should show calc_grade = D from material inputs"

    def test_non_material_grade_d_does_not_force_d(self, engine: CalcEngine) -> None:
        """Non-material input with grade D does not force calc_grade D.

        Expected behavior:
        - input_min_sanad_grade = D (min over ALL inputs)
        - calc_grade = A (min over MATERIAL inputs only)
        - Explanation must reflect that non-material inputs are excluded
        """
        result = engine.run(
            tenant_id="11111111-1111-1111-1111-111111111111",
            deal_id="22222222-2222-2222-2222-222222222222",
            calc_type=CalcType.RUNWAY,
            input_values={
                "cash_balance": Decimal("1000000"),
                "monthly_burn_rate": Decimal("50000"),
            },
            input_grades=[
                InputGradeInfo(claim_id="claim-1", grade=SanadGrade.A, is_material=True),
                InputGradeInfo(claim_id="claim-2", grade=SanadGrade.D, is_material=False),
            ],
        )

        assert result.calc_sanad.input_min_sanad_grade == SanadGrade.D
        assert result.calc_sanad.calc_grade == SanadGrade.A

        has_non_material_excluded = any(
            "non-material" in (entry.step or "").lower()
            and "excluded" in (entry.step or "").lower()
            for entry in result.calc_sanad.explanation
        )
        assert has_non_material_excluded, (
            "Explanation should state that non-material inputs are excluded from calc_grade"
        )

    def test_multiple_grade_d_still_grade_d(self, engine: CalcEngine) -> None:
        """Multiple grade D inputs → calc_grade = D."""
        result = engine.run(
            tenant_id="11111111-1111-1111-1111-111111111111",
            deal_id="22222222-2222-2222-2222-222222222222",
            calc_type=CalcType.RUNWAY,
            input_values={
                "cash_balance": Decimal("1000000"),
                "monthly_burn_rate": Decimal("50000"),
            },
            input_grades=[
                InputGradeInfo(claim_id="claim-1", grade=SanadGrade.D),
                InputGradeInfo(claim_id="claim-2", grade=SanadGrade.D),
            ],
        )

        assert result.calc_sanad.calc_grade == SanadGrade.D


class TestNoInputGradesDefaultsToA:
    """Test behavior when no input grades are provided."""

    def test_no_input_grades_defaults_to_grade_a(self, engine: CalcEngine) -> None:
        """No input grades → calc_grade defaults to A."""
        result = engine.run(
            tenant_id="11111111-1111-1111-1111-111111111111",
            deal_id="22222222-2222-2222-2222-222222222222",
            calc_type=CalcType.RUNWAY,
            input_values={
                "cash_balance": Decimal("1000000"),
                "monthly_burn_rate": Decimal("50000"),
            },
            input_grades=[],
        )

        assert result.calc_sanad.input_min_sanad_grade == SanadGrade.A
        assert result.calc_sanad.calc_grade == SanadGrade.A


class TestTamperDetection:
    """Test that verify_reproducibility detects tampering."""

    def test_verify_reproducibility_passes_for_valid_calc(self, engine: CalcEngine) -> None:
        """verify_reproducibility passes for unmodified calculation."""
        result = engine.run(
            tenant_id="11111111-1111-1111-1111-111111111111",
            deal_id="22222222-2222-2222-2222-222222222222",
            calc_type=CalcType.RUNWAY,
            input_values={
                "cash_balance": Decimal("1000000"),
                "monthly_burn_rate": Decimal("50000"),
            },
            input_grades=[],
        )

        engine.verify_reproducibility(result.calculation)

    def test_verify_reproducibility_fails_on_output_tamper(self, engine: CalcEngine) -> None:
        """Modifying output triggers CalcIntegrityError."""
        result = engine.run(
            tenant_id="11111111-1111-1111-1111-111111111111",
            deal_id="22222222-2222-2222-2222-222222222222",
            calc_type=CalcType.RUNWAY,
            input_values={
                "cash_balance": Decimal("1000000"),
                "monthly_burn_rate": Decimal("50000"),
            },
            input_grades=[],
        )

        result.calculation.output.primary_value = Decimal("999.9999")

        with pytest.raises(CalcIntegrityError) as exc_info:
            engine.verify_reproducibility(result.calculation)

        assert exc_info.value.calc_id == result.calculation.calc_id
        assert exc_info.value.expected_hash == result.calculation.reproducibility_hash

    def test_verify_reproducibility_fails_on_input_tamper(self, engine: CalcEngine) -> None:
        """Modifying inputs triggers CalcIntegrityError."""
        result = engine.run(
            tenant_id="11111111-1111-1111-1111-111111111111",
            deal_id="22222222-2222-2222-2222-222222222222",
            calc_type=CalcType.RUNWAY,
            input_values={
                "cash_balance": Decimal("1000000"),
                "monthly_burn_rate": Decimal("50000"),
            },
            input_grades=[],
        )

        result.calculation.inputs.values["cash_balance"] = Decimal("9999999")

        with pytest.raises(CalcIntegrityError):
            engine.verify_reproducibility(result.calculation)


class TestExplanationEntries:
    """Test that explanation entries document grade derivation."""

    def test_explanation_contains_input_entries(self, engine: CalcEngine) -> None:
        """Explanation contains entries for each input claim."""
        result = engine.run(
            tenant_id="11111111-1111-1111-1111-111111111111",
            deal_id="22222222-2222-2222-2222-222222222222",
            calc_type=CalcType.RUNWAY,
            input_values={
                "cash_balance": Decimal("1000000"),
                "monthly_burn_rate": Decimal("50000"),
            },
            input_grades=[
                InputGradeInfo(claim_id="claim-1", grade=SanadGrade.A),
                InputGradeInfo(claim_id="claim-2", grade=SanadGrade.B),
            ],
        )

        explanation = result.calc_sanad.explanation
        assert len(explanation) >= 2

        claim_entries = [e for e in explanation if e.claim_id is not None]
        assert len(claim_entries) == 2

    def test_explanation_contains_final_grade_entry(self, engine: CalcEngine) -> None:
        """Explanation contains final grade determination entry."""
        result = engine.run(
            tenant_id="11111111-1111-1111-1111-111111111111",
            deal_id="22222222-2222-2222-2222-222222222222",
            calc_type=CalcType.RUNWAY,
            input_values={
                "cash_balance": Decimal("1000000"),
                "monthly_burn_rate": Decimal("50000"),
            },
            input_grades=[
                InputGradeInfo(claim_id="claim-1", grade=SanadGrade.B),
            ],
        )

        explanation = result.calc_sanad.explanation
        final_entries = [e for e in explanation if e.impact is not None]
        assert len(final_entries) >= 1


class TestSanadGradeComparison:
    """Test SanadGrade comparison operators."""

    def test_grade_ordering(self) -> None:
        """Grades are ordered A > B > C > D."""
        assert SanadGrade.A > SanadGrade.B
        assert SanadGrade.B > SanadGrade.C
        assert SanadGrade.C > SanadGrade.D

        assert SanadGrade.D < SanadGrade.C
        assert SanadGrade.C < SanadGrade.B
        assert SanadGrade.B < SanadGrade.A

    def test_min_grade_returns_worst(self) -> None:
        """min_grade returns the worst (lowest quality) grade."""
        grades = [SanadGrade.A, SanadGrade.B, SanadGrade.C]
        assert SanadGrade.min_grade(grades) == SanadGrade.C

        grades = [SanadGrade.A, SanadGrade.A]
        assert SanadGrade.min_grade(grades) == SanadGrade.A

        grades = [SanadGrade.D, SanadGrade.A]
        assert SanadGrade.min_grade(grades) == SanadGrade.D

    def test_min_grade_empty_raises(self) -> None:
        """min_grade with empty list raises ValueError."""
        with pytest.raises(ValueError, match="empty"):
            SanadGrade.min_grade([])


class TestToDbDict:
    """Test model serialization for database storage."""

    def test_calculation_to_db_dict(self, engine: CalcEngine) -> None:
        """DeterministicCalculation.to_db_dict() produces valid dict."""
        result = engine.run(
            tenant_id="11111111-1111-1111-1111-111111111111",
            deal_id="22222222-2222-2222-2222-222222222222",
            calc_type=CalcType.RUNWAY,
            input_values={
                "cash_balance": Decimal("1000000"),
                "monthly_burn_rate": Decimal("50000"),
            },
            input_grades=[],
        )

        db_dict = result.calculation.to_db_dict()

        assert db_dict["calc_id"] == result.calculation.calc_id
        assert db_dict["calc_type"] == "RUNWAY"
        assert isinstance(db_dict["inputs"], dict)
        assert isinstance(db_dict["output"], dict)
        assert db_dict["output"]["primary_value"] == "20.0000"

    def test_calc_sanad_to_db_dict(self, engine: CalcEngine) -> None:
        """CalcSanad.to_db_dict() produces valid dict."""
        result = engine.run(
            tenant_id="11111111-1111-1111-1111-111111111111",
            deal_id="22222222-2222-2222-2222-222222222222",
            calc_type=CalcType.RUNWAY,
            input_values={
                "cash_balance": Decimal("1000000"),
                "monthly_burn_rate": Decimal("50000"),
            },
            input_grades=[
                InputGradeInfo(claim_id="claim-1", grade=SanadGrade.B),
            ],
        )

        db_dict = result.calc_sanad.to_db_dict()

        assert db_dict["calc_sanad_id"] == result.calc_sanad.calc_sanad_id
        assert db_dict["calc_grade"] == "B"
        assert db_dict["input_min_sanad_grade"] == "B"
        assert isinstance(db_dict["explanation"], list)
