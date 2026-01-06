"""Tests for Muḥāsabah validator - fail-closed validation for agent outputs.

Tests cover:
- PASS: valid record with supported_claim_ids, confidence 0.7
- FAIL: missing required field (agent_id)
- FAIL: confidence 0.9 with empty uncertainties and empty falsifiability_tests
- FAIL: supported_claim_ids empty
- PASS: confidence 0.9 but uncertainties present
- PASS: recommendation present and falsifiability_tests present
"""

from __future__ import annotations

import uuid

from idis.validators.muhasabah import validate_muhasabah


def _make_valid_record(**overrides: object) -> dict:
    """Create a valid Muḥāsabah record with optional overrides."""
    record = {
        "agent_id": str(uuid.uuid4()),
        "output_id": str(uuid.uuid4()),
        "timestamp": "2026-01-06T12:00:00Z",
        "confidence": 0.7,
        "supported_claim_ids": [str(uuid.uuid4())],
        "supported_calc_ids": [],
        "uncertainties": [],
        "falsifiability_tests": [],
        "is_subjective": False,
    }
    record.update(overrides)
    return record


def _make_falsifiability_test() -> dict:
    """Create a valid falsifiability test object."""
    return {
        "test_description": "Verify ARR growth rate against bank statements",
        "required_evidence": "Bank statement exports for Q1-Q4",
        "pass_fail_rule": "ARR delta must match bank deposits within 5%",
    }


def _make_uncertainty() -> dict:
    """Create a valid uncertainty object."""
    return {
        "uncertainty": "Customer concentration risk not fully quantified",
        "impact": "HIGH",
        "mitigation": "Request top-10 customer revenue breakdown",
    }


class TestValidateMuhasabahPass:
    """Test cases that should PASS validation."""

    def test_valid_record_with_claim_ids_confidence_0_7(self) -> None:
        """PASS: valid record with supported_claim_ids, confidence 0.7."""
        record = _make_valid_record(confidence=0.7)
        result = validate_muhasabah(record)

        assert result.passed is True
        assert len(result.errors) == 0

    def test_confidence_0_9_with_uncertainties_present(self) -> None:
        """PASS: confidence 0.9 but uncertainties present."""
        record = _make_valid_record(
            confidence=0.9,
            uncertainties=[_make_uncertainty()],
        )
        result = validate_muhasabah(record)

        assert result.passed is True
        assert len(result.errors) == 0

    def test_confidence_0_9_with_falsifiability_tests_present(self) -> None:
        """PASS: confidence 0.9 but falsifiability_tests present (alternative to uncertainties)."""
        record = _make_valid_record(
            confidence=0.9,
            falsifiability_tests=[_make_falsifiability_test()],
        )
        result = validate_muhasabah(record)

        assert result.passed is True
        assert len(result.errors) == 0

    def test_recommendation_with_falsifiability_tests(self) -> None:
        """PASS: recommendation present and falsifiability_tests present."""
        record = _make_valid_record(
            recommendation="Proceed to IC",
            falsifiability_tests=[_make_falsifiability_test()],
        )
        result = validate_muhasabah(record)

        assert result.passed is True
        assert len(result.errors) == 0

    def test_decision_with_falsifiability_tests(self) -> None:
        """PASS: decision field present with falsifiability_tests."""
        record = _make_valid_record(
            decision="APPROVE",
            falsifiability_tests=[_make_falsifiability_test()],
        )
        result = validate_muhasabah(record)

        assert result.passed is True
        assert len(result.errors) == 0

    def test_subjective_output_empty_claim_ids(self) -> None:
        """PASS: subjective output can have empty claim_ids."""
        record = _make_valid_record(
            supported_claim_ids=[],
            is_subjective=True,
        )
        result = validate_muhasabah(record)

        assert result.passed is True


class TestValidateMuhasabahFail:
    """Test cases that should FAIL validation."""

    def test_missing_agent_id(self) -> None:
        """FAIL: missing required field (agent_id)."""
        record = _make_valid_record()
        del record["agent_id"]

        result = validate_muhasabah(record)

        assert result.passed is False
        error_codes = [e.code for e in result.errors]
        assert "MISSING_AGENT_ID" in error_codes

    def test_missing_output_id(self) -> None:
        """FAIL: missing required field (output_id)."""
        record = _make_valid_record()
        del record["output_id"]

        result = validate_muhasabah(record)

        assert result.passed is False
        error_codes = [e.code for e in result.errors]
        assert "MISSING_OUTPUT_ID" in error_codes

    def test_missing_timestamp(self) -> None:
        """FAIL: missing required field (timestamp)."""
        record = _make_valid_record()
        del record["timestamp"]

        result = validate_muhasabah(record)

        assert result.passed is False
        error_codes = [e.code for e in result.errors]
        assert "MISSING_TIMESTAMP" in error_codes

    def test_missing_confidence(self) -> None:
        """FAIL: missing required field (confidence)."""
        record = _make_valid_record()
        del record["confidence"]

        result = validate_muhasabah(record)

        assert result.passed is False
        error_codes = [e.code for e in result.errors]
        assert "MISSING_CONFIDENCE" in error_codes

    def test_confidence_0_9_empty_uncertainties_and_falsifiability(self) -> None:
        """FAIL: confidence 0.9 with empty uncertainties and empty falsifiability_tests."""
        record = _make_valid_record(
            confidence=0.9,
            uncertainties=[],
            falsifiability_tests=[],
        )

        result = validate_muhasabah(record)

        assert result.passed is False
        error_codes = [e.code for e in result.errors]
        assert "HIGH_CONFIDENCE_NO_UNCERTAINTIES" in error_codes

    def test_supported_claim_ids_empty(self) -> None:
        """FAIL: supported_claim_ids empty (non-subjective output)."""
        record = _make_valid_record(
            supported_claim_ids=[],
            supported_calc_ids=[],
            is_subjective=False,
        )

        result = validate_muhasabah(record)

        assert result.passed is False
        error_codes = [e.code for e in result.errors]
        assert "NO_SUPPORTING_REFERENCES" in error_codes

    def test_recommendation_without_falsifiability_tests(self) -> None:
        """FAIL: recommendation present but no falsifiability_tests."""
        record = _make_valid_record(
            recommendation="Proceed to IC",
            falsifiability_tests=[],
        )

        result = validate_muhasabah(record)

        assert result.passed is False
        error_codes = [e.code for e in result.errors]
        assert "RECOMMENDATION_NO_FALSIFIABILITY" in error_codes

    def test_decision_without_falsifiability_tests(self) -> None:
        """FAIL: decision field present but no falsifiability_tests."""
        record = _make_valid_record(
            decision="REJECT",
            falsifiability_tests=[],
        )

        result = validate_muhasabah(record)

        assert result.passed is False
        error_codes = [e.code for e in result.errors]
        assert "RECOMMENDATION_NO_FALSIFIABILITY" in error_codes


class TestValidateMuhasabahUuidValidation:
    """Test cases for UUID format validation."""

    def test_invalid_agent_id_format(self) -> None:
        """FAIL: agent_id not a valid UUID."""
        record = _make_valid_record(agent_id="not-a-uuid")

        result = validate_muhasabah(record)

        assert result.passed is False
        error_codes = [e.code for e in result.errors]
        assert "INVALID_AGENT_ID_FORMAT" in error_codes

    def test_invalid_output_id_format(self) -> None:
        """FAIL: output_id not a valid UUID."""
        record = _make_valid_record(output_id="invalid-uuid")

        result = validate_muhasabah(record)

        assert result.passed is False
        error_codes = [e.code for e in result.errors]
        assert "INVALID_OUTPUT_ID_FORMAT" in error_codes

    def test_invalid_claim_id_format(self) -> None:
        """FAIL: claim_id in supported_claim_ids not a valid UUID."""
        record = _make_valid_record(
            supported_claim_ids=["not-a-valid-uuid"],
        )

        result = validate_muhasabah(record)

        assert result.passed is False
        error_codes = [e.code for e in result.errors]
        assert "INVALID_CLAIM_ID_FORMAT" in error_codes

    def test_invalid_calc_id_format(self) -> None:
        """FAIL: calc_id in supported_calc_ids not a valid UUID."""
        record = _make_valid_record(
            supported_calc_ids=["bad-calc-id"],
        )

        result = validate_muhasabah(record)

        assert result.passed is False
        error_codes = [e.code for e in result.errors]
        assert "INVALID_CALC_ID_FORMAT" in error_codes


class TestValidateMuhasabahFailClosed:
    """Test fail-closed behavior on edge cases."""

    def test_none_input(self) -> None:
        """FAIL CLOSED: None input."""
        result = validate_muhasabah(None)  # type: ignore[arg-type]

        assert result.passed is False
        assert len(result.errors) > 0
        assert result.errors[0].code == "FAIL_CLOSED"

    def test_non_dict_input(self) -> None:
        """FAIL CLOSED: non-dict input."""
        result = validate_muhasabah("not a dict")  # type: ignore[arg-type]

        assert result.passed is False
        assert len(result.errors) > 0
        assert result.errors[0].code == "FAIL_CLOSED"

    def test_empty_dict(self) -> None:
        """FAIL: empty dict missing all required fields."""
        result = validate_muhasabah({})

        assert result.passed is False
        error_codes = [e.code for e in result.errors]
        assert "MISSING_AGENT_ID" in error_codes
        assert "MISSING_OUTPUT_ID" in error_codes
        assert "MISSING_TIMESTAMP" in error_codes
        assert "MISSING_CONFIDENCE" in error_codes


class TestValidateMuhasabahConfidenceRange:
    """Test confidence value validation."""

    def test_confidence_below_zero(self) -> None:
        """FAIL: confidence below 0."""
        record = _make_valid_record(confidence=-0.1)

        result = validate_muhasabah(record)

        assert result.passed is False
        error_codes = [e.code for e in result.errors]
        assert "CONFIDENCE_OUT_OF_RANGE" in error_codes

    def test_confidence_above_one(self) -> None:
        """FAIL: confidence above 1."""
        record = _make_valid_record(confidence=1.5)

        result = validate_muhasabah(record)

        assert result.passed is False
        error_codes = [e.code for e in result.errors]
        assert "CONFIDENCE_OUT_OF_RANGE" in error_codes

    def test_confidence_exactly_0_80_no_uncertainties(self) -> None:
        """PASS: confidence exactly 0.80 (threshold is >) does not require uncertainties."""
        record = _make_valid_record(
            confidence=0.80,
            uncertainties=[],
            falsifiability_tests=[],
        )

        result = validate_muhasabah(record)

        assert result.passed is True

    def test_confidence_0_81_requires_uncertainties_or_falsifiability(self) -> None:
        """FAIL: confidence 0.81 requires uncertainties OR falsifiability_tests."""
        record = _make_valid_record(
            confidence=0.81,
            uncertainties=[],
            falsifiability_tests=[],
        )

        result = validate_muhasabah(record)

        assert result.passed is False
        error_codes = [e.code for e in result.errors]
        assert "HIGH_CONFIDENCE_NO_UNCERTAINTIES" in error_codes
