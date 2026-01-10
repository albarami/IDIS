"""Tests for Extraction Confidence Gate — Phase 4.2.

Required test per Traceability Matrix FC-001:
- test_low_confidence_blocked

Additional tests for comprehensive coverage:
- test_low_dhabt_blocked
- test_missing_values_fail_closed
- test_human_verified_bypasses_gate
- test_calc_engine_integration
"""

from decimal import Decimal

import pytest

from idis.calc.engine import CalcEngine, InputGradeInfo
from idis.calc.formulas.core import register_core_formulas
from idis.calc.formulas.registry import FormulaRegistry
from idis.models.calc_sanad import SanadGrade
from idis.models.deterministic_calculation import CalcType
from idis.validators.extraction_gate import (
    CONFIDENCE_THRESHOLD,
    DHABT_THRESHOLD,
    ExtractionGateBlockedError,
    ExtractionGateBlockReason,
    ExtractionGateDecision,
    ExtractionGateInput,
    ExtractionGateValidator,
    VerificationMethod,
    evaluate_extraction_gate,
    evaluate_extraction_gate_batch,
    validate_extraction_gate,
)


class TestExtractionGateThresholds:
    """Verify threshold constants match spec."""

    def test_confidence_threshold_is_decimal(self) -> None:
        """CONFIDENCE_THRESHOLD must be Decimal, not float."""
        assert isinstance(CONFIDENCE_THRESHOLD, Decimal)
        assert Decimal("0.95") == CONFIDENCE_THRESHOLD

    def test_dhabt_threshold_is_decimal(self) -> None:
        """DHABT_THRESHOLD must be Decimal, not float."""
        assert isinstance(DHABT_THRESHOLD, Decimal)
        assert Decimal("0.90") == DHABT_THRESHOLD


class TestExtractionGateLowConfidence:
    """Tests for low extraction confidence blocking."""

    def test_low_confidence_blocked(self) -> None:
        """REQUIRED TEST (FC-001): confidence=0.94, dhabt=0.99, not human-verified => blocked.

        Per Go-Live §1.4 and Data Model §7.3: extraction_confidence < 0.95 blocks calcs.
        """
        input_data = ExtractionGateInput(
            claim_id="test-claim-001",
            extraction_confidence=Decimal("0.94"),
            dhabt_score=Decimal("0.99"),
            is_human_verified=False,
        )

        decision = evaluate_extraction_gate(input_data)

        assert decision.blocked is True
        assert decision.allowed is False
        assert decision.reason == ExtractionGateBlockReason.LOW_CONFIDENCE
        assert decision.claim_id == "test-claim-001"
        assert decision.extraction_confidence == Decimal("0.94")
        assert decision.bypassed_by_human_verification is False

    def test_confidence_exactly_at_threshold_passes(self) -> None:
        """Confidence exactly at 0.95 should pass."""
        input_data = ExtractionGateInput(
            claim_id="test-claim-002",
            extraction_confidence=Decimal("0.95"),
            dhabt_score=Decimal("0.95"),
            is_human_verified=False,
        )

        decision = evaluate_extraction_gate(input_data)

        assert decision.allowed is True
        assert decision.blocked is False
        assert decision.reason is None

    def test_confidence_above_threshold_passes(self) -> None:
        """Confidence above 0.95 should pass."""
        input_data = ExtractionGateInput(
            claim_id="test-claim-003",
            extraction_confidence=Decimal("0.99"),
            dhabt_score=Decimal("0.95"),
            is_human_verified=False,
        )

        decision = evaluate_extraction_gate(input_data)

        assert decision.allowed is True
        assert decision.blocked is False

    def test_confidence_just_below_threshold_blocked(self) -> None:
        """Confidence at 0.9499... should be blocked."""
        input_data = ExtractionGateInput(
            claim_id="test-claim-004",
            extraction_confidence=Decimal("0.9499999"),
            dhabt_score=Decimal("0.99"),
            is_human_verified=False,
        )

        decision = evaluate_extraction_gate(input_data)

        assert decision.blocked is True
        assert decision.reason == ExtractionGateBlockReason.LOW_CONFIDENCE


class TestExtractionGateLowDhabt:
    """Tests for low dhabt_score blocking."""

    def test_low_dhabt_blocked(self) -> None:
        """confidence=0.99, dhabt=0.89 => blocked.

        Per Go-Live §1.4: dhabt_score < 0.90 blocks calcs.
        """
        input_data = ExtractionGateInput(
            claim_id="test-claim-005",
            extraction_confidence=Decimal("0.99"),
            dhabt_score=Decimal("0.89"),
            is_human_verified=False,
        )

        decision = evaluate_extraction_gate(input_data)

        assert decision.blocked is True
        assert decision.allowed is False
        assert decision.reason == ExtractionGateBlockReason.LOW_DHABT
        assert decision.claim_id == "test-claim-005"
        assert decision.dhabt_score == Decimal("0.89")

    def test_dhabt_exactly_at_threshold_passes(self) -> None:
        """Dhabt exactly at 0.90 should pass."""
        input_data = ExtractionGateInput(
            claim_id="test-claim-006",
            extraction_confidence=Decimal("0.99"),
            dhabt_score=Decimal("0.90"),
            is_human_verified=False,
        )

        decision = evaluate_extraction_gate(input_data)

        assert decision.allowed is True
        assert decision.blocked is False

    def test_dhabt_just_below_threshold_blocked(self) -> None:
        """Dhabt at 0.8999... should be blocked."""
        input_data = ExtractionGateInput(
            claim_id="test-claim-007",
            extraction_confidence=Decimal("0.99"),
            dhabt_score=Decimal("0.8999999"),
            is_human_verified=False,
        )

        decision = evaluate_extraction_gate(input_data)

        assert decision.blocked is True
        assert decision.reason == ExtractionGateBlockReason.LOW_DHABT


class TestExtractionGateMissingValues:
    """Tests for fail-closed behavior on missing/invalid values."""

    def test_missing_values_fail_closed(self) -> None:
        """None/invalid confidence or dhabt => blocked (fail-closed)."""
        # Missing confidence
        input_data = ExtractionGateInput(
            claim_id="test-claim-008",
            extraction_confidence=None,
            dhabt_score=Decimal("0.99"),
            is_human_verified=False,
        )

        decision = evaluate_extraction_gate(input_data)

        assert decision.blocked is True
        assert decision.reason == ExtractionGateBlockReason.MISSING_CONFIDENCE

    def test_missing_dhabt_fail_closed(self) -> None:
        """Missing dhabt_score => blocked."""
        input_data = ExtractionGateInput(
            claim_id="test-claim-009",
            extraction_confidence=Decimal("0.99"),
            dhabt_score=None,
            is_human_verified=False,
        )

        decision = evaluate_extraction_gate(input_data)

        assert decision.blocked is True
        assert decision.reason == ExtractionGateBlockReason.MISSING_DHABT

    def test_both_missing_fail_closed(self) -> None:
        """Both values missing => blocked (confidence checked first)."""
        input_data = ExtractionGateInput(
            claim_id="test-claim-010",
            extraction_confidence=None,
            dhabt_score=None,
            is_human_verified=False,
        )

        decision = evaluate_extraction_gate(input_data)

        assert decision.blocked is True
        assert decision.reason == ExtractionGateBlockReason.MISSING_CONFIDENCE

    def test_invalid_confidence_out_of_range_blocked(self) -> None:
        """Confidence > 1.0 is invalid => blocked."""
        input_data = ExtractionGateInput(
            claim_id="test-claim-011",
            extraction_confidence=Decimal("1.5"),
            dhabt_score=Decimal("0.99"),
            is_human_verified=False,
        )

        decision = evaluate_extraction_gate(input_data)

        assert decision.blocked is True
        assert decision.reason == ExtractionGateBlockReason.INVALID_CONFIDENCE

    def test_negative_confidence_blocked(self) -> None:
        """Negative confidence is invalid => blocked."""
        input_data = ExtractionGateInput(
            claim_id="test-claim-012",
            extraction_confidence=Decimal("-0.5"),
            dhabt_score=Decimal("0.99"),
            is_human_verified=False,
        )

        decision = evaluate_extraction_gate(input_data)

        assert decision.blocked is True
        assert decision.reason == ExtractionGateBlockReason.INVALID_CONFIDENCE

    def test_invalid_dhabt_out_of_range_blocked(self) -> None:
        """Dhabt > 1.0 is invalid => blocked."""
        input_data = ExtractionGateInput(
            claim_id="test-claim-013",
            extraction_confidence=Decimal("0.99"),
            dhabt_score=Decimal("1.5"),
            is_human_verified=False,
        )

        decision = evaluate_extraction_gate(input_data)

        assert decision.blocked is True
        assert decision.reason == ExtractionGateBlockReason.INVALID_DHABT


class TestExtractionGateHumanVerification:
    """Tests for human verification bypass."""

    def test_human_verified_bypasses_gate(self) -> None:
        """confidence=0.10, dhabt=0.10, human-verified => allowed.

        Human verification bypasses ALL extraction gate checks.
        """
        input_data = ExtractionGateInput(
            claim_id="test-claim-014",
            extraction_confidence=Decimal("0.10"),
            dhabt_score=Decimal("0.10"),
            is_human_verified=True,
        )

        decision = evaluate_extraction_gate(input_data)

        assert decision.allowed is True
        assert decision.blocked is False
        assert decision.reason is None
        assert decision.bypassed_by_human_verification is True

    def test_verification_method_human_verified_bypasses(self) -> None:
        """VerificationMethod.HUMAN_VERIFIED also bypasses gate."""
        input_data = ExtractionGateInput(
            claim_id="test-claim-015",
            extraction_confidence=Decimal("0.10"),
            dhabt_score=Decimal("0.10"),
            is_human_verified=False,
            verification_method=VerificationMethod.HUMAN_VERIFIED,
        )

        decision = evaluate_extraction_gate(input_data)

        assert decision.allowed is True
        assert decision.bypassed_by_human_verification is True

    def test_verification_method_dual_verified_bypasses(self) -> None:
        """VerificationMethod.DUAL_VERIFIED also bypasses gate."""
        input_data = ExtractionGateInput(
            claim_id="test-claim-016",
            extraction_confidence=Decimal("0.10"),
            dhabt_score=Decimal("0.10"),
            is_human_verified=False,
            verification_method=VerificationMethod.DUAL_VERIFIED,
        )

        decision = evaluate_extraction_gate(input_data)

        assert decision.allowed is True
        assert decision.bypassed_by_human_verification is True

    def test_human_verified_bypasses_missing_values(self) -> None:
        """Human verification even bypasses missing confidence/dhabt."""
        input_data = ExtractionGateInput(
            claim_id="test-claim-017",
            extraction_confidence=None,
            dhabt_score=None,
            is_human_verified=True,
        )

        decision = evaluate_extraction_gate(input_data)

        assert decision.allowed is True
        assert decision.bypassed_by_human_verification is True

    def test_system_verified_does_not_bypass(self) -> None:
        """VerificationMethod.SYSTEM_VERIFIED does NOT bypass the gate."""
        input_data = ExtractionGateInput(
            claim_id="test-claim-018",
            extraction_confidence=Decimal("0.10"),
            dhabt_score=Decimal("0.10"),
            is_human_verified=False,
            verification_method=VerificationMethod.SYSTEM_VERIFIED,
        )

        decision = evaluate_extraction_gate(input_data)

        assert decision.blocked is True
        assert decision.reason == ExtractionGateBlockReason.LOW_CONFIDENCE


class TestExtractionGateBatch:
    """Tests for batch evaluation."""

    def test_batch_evaluation_separates_allowed_and_blocked(self) -> None:
        """Batch evaluation returns separate lists for allowed/blocked."""
        inputs = [
            ExtractionGateInput(
                claim_id="allowed-1",
                extraction_confidence=Decimal("0.99"),
                dhabt_score=Decimal("0.99"),
            ),
            ExtractionGateInput(
                claim_id="blocked-1",
                extraction_confidence=Decimal("0.50"),
                dhabt_score=Decimal("0.99"),
            ),
            ExtractionGateInput(
                claim_id="allowed-2",
                extraction_confidence=Decimal("0.95"),
                dhabt_score=Decimal("0.90"),
            ),
            ExtractionGateInput(
                claim_id="blocked-2",
                extraction_confidence=Decimal("0.99"),
                dhabt_score=Decimal("0.50"),
            ),
        ]

        allowed, blocked = evaluate_extraction_gate_batch(inputs)

        assert len(allowed) == 2
        assert len(blocked) == 2
        assert {d.claim_id for d in allowed} == {"allowed-1", "allowed-2"}
        assert {d.claim_id for d in blocked} == {"blocked-1", "blocked-2"}


class TestExtractionGateValidator:
    """Tests for the validator class interface."""

    def test_validator_returns_validation_result(self) -> None:
        """Validator.validate returns ValidationResult."""
        validator = ExtractionGateValidator()
        input_data = ExtractionGateInput(
            claim_id="test-claim-019",
            extraction_confidence=Decimal("0.50"),
            dhabt_score=Decimal("0.99"),
        )

        result = validator.validate(input_data)

        assert result.passed is False
        assert len(result.errors) == 1
        assert "EXTRACTION_GATE_LOW_CONFIDENCE" in result.errors[0].code

    def test_validator_success_result(self) -> None:
        """Validator returns success for valid inputs."""
        validator = ExtractionGateValidator()
        input_data = ExtractionGateInput(
            claim_id="test-claim-020",
            extraction_confidence=Decimal("0.99"),
            dhabt_score=Decimal("0.99"),
        )

        result = validator.validate(input_data)

        assert result.passed is True
        assert len(result.errors) == 0

    def test_validate_function_api(self) -> None:
        """validate_extraction_gate function works as public API."""
        input_data = ExtractionGateInput(
            claim_id="test-claim-021",
            extraction_confidence=Decimal("0.50"),
            dhabt_score=Decimal("0.99"),
        )

        result = validate_extraction_gate(input_data)

        assert result.passed is False


class TestExtractionGateBlockedError:
    """Tests for the ExtractionGateBlockedError exception."""

    def test_error_contains_blocked_inputs(self) -> None:
        """ExtractionGateBlockedError contains blocked decision list."""
        decision = ExtractionGateDecision(
            allowed=False,
            blocked=True,
            reason=ExtractionGateBlockReason.LOW_CONFIDENCE,
            claim_id="test-claim-022",
            extraction_confidence=Decimal("0.50"),
            dhabt_score=Decimal("0.99"),
        )

        error = ExtractionGateBlockedError([decision], calc_type="RUNWAY")

        assert len(error.blocked_inputs) == 1
        assert error.calc_type == "RUNWAY"
        assert "RUNWAY" in str(error)
        assert "test-claim-022" in str(error)

    def test_error_message_includes_reasons(self) -> None:
        """Error message includes block reasons."""
        decision = ExtractionGateDecision(
            allowed=False,
            blocked=True,
            reason=ExtractionGateBlockReason.LOW_DHABT,
            claim_id="test-claim-023",
            extraction_confidence=Decimal("0.99"),
            dhabt_score=Decimal("0.50"),
        )

        error = ExtractionGateBlockedError([decision], calc_type="GROSS_MARGIN")

        assert "LOW_DHABT" in str(error)


@pytest.fixture
def calc_engine() -> CalcEngine:
    """Create a calc engine with formulas registered for integration tests."""
    FormulaRegistry.reset_instance()
    reg = FormulaRegistry()
    register_core_formulas(reg)
    return CalcEngine(registry=reg, code_version="test-1.0.0", enforce_extraction_gate=True)


class TestCalcEngineExtractionGateIntegration:
    """Tests for extraction gate integration with CalcEngine."""

    def test_calc_engine_blocks_low_confidence_input(self, calc_engine: CalcEngine) -> None:
        """CalcEngine raises ExtractionGateBlockedError for low confidence input."""
        input_grades = [
            InputGradeInfo(
                claim_id="claim-001",
                grade=SanadGrade.A,
                extraction_confidence=Decimal("0.50"),  # Below threshold
                dhabt_score=Decimal("0.99"),
                is_human_verified=False,
            ),
        ]

        with pytest.raises(ExtractionGateBlockedError) as exc_info:
            calc_engine.run(
                tenant_id="tenant-001",
                deal_id="deal-001",
                calc_type=CalcType.RUNWAY,
                input_values={
                    "cash_balance": Decimal("1000000"),
                    "monthly_burn_rate": Decimal("50000"),
                },
                input_grades=input_grades,
            )

        assert len(exc_info.value.blocked_inputs) == 1
        assert exc_info.value.blocked_inputs[0].reason == ExtractionGateBlockReason.LOW_CONFIDENCE

    def test_calc_engine_blocks_low_dhabt_input(self, calc_engine: CalcEngine) -> None:
        """CalcEngine raises ExtractionGateBlockedError for low dhabt input."""
        input_grades = [
            InputGradeInfo(
                claim_id="claim-002",
                grade=SanadGrade.A,
                extraction_confidence=Decimal("0.99"),
                dhabt_score=Decimal("0.50"),  # Below threshold
                is_human_verified=False,
            ),
        ]

        with pytest.raises(ExtractionGateBlockedError) as exc_info:
            calc_engine.run(
                tenant_id="tenant-001",
                deal_id="deal-001",
                calc_type=CalcType.RUNWAY,
                input_values={
                    "cash_balance": Decimal("1000000"),
                    "monthly_burn_rate": Decimal("50000"),
                },
                input_grades=input_grades,
            )

        assert exc_info.value.blocked_inputs[0].reason == ExtractionGateBlockReason.LOW_DHABT

    def test_calc_engine_allows_human_verified_low_confidence(
        self, calc_engine: CalcEngine
    ) -> None:
        """CalcEngine allows human-verified inputs even with low confidence."""
        input_grades = [
            InputGradeInfo(
                claim_id="claim-003",
                grade=SanadGrade.A,
                extraction_confidence=Decimal("0.50"),  # Below threshold
                dhabt_score=Decimal("0.50"),  # Below threshold
                is_human_verified=True,  # But human-verified
            ),
        ]

        # Should NOT raise - human verification bypasses gate
        result = calc_engine.run(
            tenant_id="tenant-001",
            deal_id="deal-001",
            calc_type=CalcType.RUNWAY,
            input_values={
                "cash_balance": Decimal("1000000"),
                "monthly_burn_rate": Decimal("50000"),
            },
            input_grades=input_grades,
        )

        assert result.calculation is not None
        assert result.calc_sanad is not None

    def test_calc_engine_allows_valid_inputs(self, calc_engine: CalcEngine) -> None:
        """CalcEngine allows inputs that pass the extraction gate."""
        input_grades = [
            InputGradeInfo(
                claim_id="claim-004",
                grade=SanadGrade.A,
                extraction_confidence=Decimal("0.99"),
                dhabt_score=Decimal("0.95"),
                is_human_verified=False,
            ),
        ]

        result = calc_engine.run(
            tenant_id="tenant-001",
            deal_id="deal-001",
            calc_type=CalcType.RUNWAY,
            input_values={
                "cash_balance": Decimal("1000000"),
                "monthly_burn_rate": Decimal("50000"),
            },
            input_grades=input_grades,
        )

        assert result.calculation is not None

    def test_calc_engine_gate_disabled(self) -> None:
        """CalcEngine with enforce_extraction_gate=False skips gate."""
        # Create engine with gate disabled and formulas registered
        FormulaRegistry.reset_instance()
        reg = FormulaRegistry()
        register_core_formulas(reg)
        engine = CalcEngine(registry=reg, enforce_extraction_gate=False)

        input_grades = [
            InputGradeInfo(
                claim_id="claim-005",
                grade=SanadGrade.A,
                extraction_confidence=Decimal("0.10"),  # Would normally be blocked
                dhabt_score=Decimal("0.10"),
                is_human_verified=False,
            ),
        ]

        # Should NOT raise - gate is disabled
        result = engine.run(
            tenant_id="tenant-001",
            deal_id="deal-001",
            calc_type=CalcType.RUNWAY,
            input_values={
                "cash_balance": Decimal("1000000"),
                "monthly_burn_rate": Decimal("50000"),
            },
            input_grades=input_grades,
        )

        assert result.calculation is not None

    def test_calc_engine_blocks_missing_extraction_metadata(self, calc_engine: CalcEngine) -> None:
        """CalcEngine blocks inputs with missing extraction metadata (fail-closed)."""
        input_grades = [
            InputGradeInfo(
                claim_id="claim-006",
                grade=SanadGrade.A,
                extraction_confidence=None,  # Missing
                dhabt_score=None,  # Missing
                is_human_verified=False,
            ),
        ]

        with pytest.raises(ExtractionGateBlockedError) as exc_info:
            calc_engine.run(
                tenant_id="tenant-001",
                deal_id="deal-001",
                calc_type=CalcType.RUNWAY,
                input_values={
                    "cash_balance": Decimal("1000000"),
                    "monthly_burn_rate": Decimal("50000"),
                },
                input_grades=input_grades,
            )

        assert (
            exc_info.value.blocked_inputs[0].reason == ExtractionGateBlockReason.MISSING_CONFIDENCE
        )

    def test_calc_engine_blocks_any_failing_input(self, calc_engine: CalcEngine) -> None:
        """CalcEngine blocks if ANY input fails the gate."""
        input_grades = [
            InputGradeInfo(
                claim_id="claim-007",
                grade=SanadGrade.A,
                extraction_confidence=Decimal("0.99"),
                dhabt_score=Decimal("0.99"),
                is_human_verified=False,
            ),
            InputGradeInfo(
                claim_id="claim-008",
                grade=SanadGrade.A,
                extraction_confidence=Decimal("0.50"),  # This one fails
                dhabt_score=Decimal("0.99"),
                is_human_verified=False,
            ),
        ]

        with pytest.raises(ExtractionGateBlockedError) as exc_info:
            calc_engine.run(
                tenant_id="tenant-001",
                deal_id="deal-001",
                calc_type=CalcType.RUNWAY,
                input_values={
                    "cash_balance": Decimal("1000000"),
                    "monthly_burn_rate": Decimal("50000"),
                },
                input_grades=input_grades,
            )

        # Should have 1 blocked input
        assert len(exc_info.value.blocked_inputs) == 1
        assert exc_info.value.blocked_inputs[0].claim_id == "claim-008"


class TestExtractionGateDecisionConsistency:
    """Tests for ExtractionGateDecision dataclass consistency."""

    def test_allowed_and_blocked_must_be_opposite(self) -> None:
        """Decision allowed and blocked must be opposite values."""
        with pytest.raises(ValueError):
            ExtractionGateDecision(
                allowed=True,
                blocked=True,  # Invalid - same as allowed
                reason=None,
                claim_id="test",
                extraction_confidence=Decimal("0.99"),
                dhabt_score=Decimal("0.99"),
            )

    def test_decision_is_frozen(self) -> None:
        """ExtractionGateDecision should be frozen (immutable)."""
        decision = ExtractionGateDecision(
            allowed=True,
            blocked=False,
            reason=None,
            claim_id="test",
            extraction_confidence=Decimal("0.99"),
            dhabt_score=Decimal("0.99"),
        )

        with pytest.raises(AttributeError):
            decision.allowed = False  # type: ignore[misc]


class TestNoFloatArithmetic:
    """Verify no float arithmetic is used in the gate path."""

    def test_thresholds_are_decimal(self) -> None:
        """Thresholds must be Decimal, not float."""
        assert isinstance(CONFIDENCE_THRESHOLD, Decimal)
        assert isinstance(DHABT_THRESHOLD, Decimal)
        assert not isinstance(CONFIDENCE_THRESHOLD, float)
        assert not isinstance(DHABT_THRESHOLD, float)

    def test_decision_stores_decimal_values(self) -> None:
        """Decision stores Decimal values, not floats."""
        input_data = ExtractionGateInput(
            claim_id="test",
            extraction_confidence=Decimal("0.96"),
            dhabt_score=Decimal("0.91"),
        )

        decision = evaluate_extraction_gate(input_data)

        assert isinstance(decision.extraction_confidence, Decimal)
        assert isinstance(decision.dhabt_score, Decimal)
