"""Tests for MuhasabahValidator - proves fail-closed behavior."""

from __future__ import annotations

from idis.validators import MuhasabahValidator


class TestMuhasabahFailClosed:
    """Tests proving fail-closed behavior."""

    def test_none_data_fails_closed(self) -> None:
        """Validator rejects None data."""
        validator = MuhasabahValidator()
        result = validator.validate(None)

        assert not result.passed
        assert result.errors[0].code == "FAIL_CLOSED"

    def test_non_dict_fails_closed(self) -> None:
        """Validator rejects non-dict data."""
        validator = MuhasabahValidator()
        result = validator.validate([1, 2, 3])

        assert not result.passed
        assert result.errors[0].code == "FAIL_CLOSED"


class TestMuhasabahPositive:
    """Positive tests - valid records pass."""

    def test_valid_muhasabah_passes(self) -> None:
        """Valid Muḥāsabah record passes validation."""
        validator = MuhasabahValidator()

        valid_record = {
            "muhasabah_id": "550e8400-e29b-41d4-a716-446655440000",
            "agent_id": "financial-analyst-v1",
            "output_id": "550e8400-e29b-41d4-a716-446655440001",
            "supported_claim_ids": ["550e8400-e29b-41d4-a716-446655440002"],
            "confidence": 0.85,
            "uncertainties": [
                {
                    "uncertainty": "Revenue recognition timing unclear",
                    "impact": "HIGH",
                    "mitigation": "Request audited financials",
                }
            ],
            "falsifiability_tests": [
                {
                    "test_description": "Verify ARR matches bank deposits",
                    "required_evidence": "Bank statements",
                    "pass_fail_rule": "Deposits within 10% of claimed ARR",
                }
            ],
            "timestamp": "2026-01-06T12:00:00Z",
        }

        result = validator.validate(valid_record)
        assert result.passed, f"Expected pass but got: {result.errors}"

    def test_subjective_without_claim_refs_passes(self) -> None:
        """Subjective output without claim refs passes."""
        validator = MuhasabahValidator()

        record = {
            "agent_id": "opinion-agent",
            "output_id": "550e8400-e29b-41d4-a716-446655440001",
            "supported_claim_ids": [],
            "confidence": 0.4,
            "is_subjective": True,
            "timestamp": "2026-01-06T12:00:00Z",
        }

        result = validator.validate(record)
        assert result.passed

    def test_low_confidence_no_falsifiability_passes(self) -> None:
        """Low confidence (<=0.50) without falsifiability tests passes."""
        validator = MuhasabahValidator()

        record = {
            "agent_id": "uncertain-agent",
            "output_id": "550e8400-e29b-41d4-a716-446655440001",
            "supported_claim_ids": ["550e8400-e29b-41d4-a716-446655440002"],
            "confidence": 0.45,
            "falsifiability_tests": [],
            "timestamp": "2026-01-06T12:00:00Z",
        }

        result = validator.validate(record)
        assert result.passed


class TestMuhasabahNegative:
    """Negative tests - invalid records fail."""

    def test_missing_agent_id_fails(self) -> None:
        """Missing agent_id fails validation."""
        validator = MuhasabahValidator()

        record = {
            "output_id": "550e8400-e29b-41d4-a716-446655440001",
            "supported_claim_ids": ["550e8400-e29b-41d4-a716-446655440002"],
            "confidence": 0.5,
            "timestamp": "2026-01-06T12:00:00Z",
        }

        result = validator.validate(record)
        assert not result.passed
        assert any(e.code == "MISSING_AGENT_ID" for e in result.errors)

    def test_non_subjective_empty_refs_fails(self) -> None:
        """Non-subjective output with empty claim refs fails."""
        validator = MuhasabahValidator()

        record = {
            "agent_id": "factual-agent",
            "output_id": "550e8400-e29b-41d4-a716-446655440001",
            "supported_claim_ids": [],  # Empty - violation!
            "confidence": 0.7,
            "timestamp": "2026-01-06T12:00:00Z",
        }

        result = validator.validate(record)
        assert not result.passed
        assert any(e.code == "NO_SUPPORTING_REFERENCES" for e in result.errors)

    def test_high_confidence_no_uncertainties_fails(self) -> None:
        """Confidence > 0.80 without uncertainties fails."""
        validator = MuhasabahValidator()

        record = {
            "agent_id": "overconfident-agent",
            "output_id": "550e8400-e29b-41d4-a716-446655440001",
            "supported_claim_ids": ["550e8400-e29b-41d4-a716-446655440002"],
            "confidence": 0.95,  # High confidence
            "uncertainties": [],  # Empty - violation!
            "falsifiability_tests": [
                {
                    "test_description": "Test",
                    "required_evidence": "Evidence",
                    "pass_fail_rule": "Rule",
                }
            ],
            "timestamp": "2026-01-06T12:00:00Z",
        }

        result = validator.validate(record)
        assert not result.passed
        assert any(e.code == "HIGH_CONFIDENCE_NO_UNCERTAINTIES" for e in result.errors)

    def test_material_confidence_no_falsifiability_fails(self) -> None:
        """Confidence > 0.50 without falsifiability tests fails."""
        validator = MuhasabahValidator()

        record = {
            "agent_id": "confident-agent",
            "output_id": "550e8400-e29b-41d4-a716-446655440001",
            "supported_claim_ids": ["550e8400-e29b-41d4-a716-446655440002"],
            "confidence": 0.75,  # > 0.50
            "falsifiability_tests": [],  # Empty - violation!
            "timestamp": "2026-01-06T12:00:00Z",
        }

        result = validator.validate(record)
        assert not result.passed
        assert any(e.code == "MATERIAL_CONFIDENCE_NO_FALSIFIABILITY" for e in result.errors)

    def test_confidence_out_of_range_fails(self) -> None:
        """Confidence outside 0-1 range fails."""
        validator = MuhasabahValidator()

        record = {
            "agent_id": "agent",
            "output_id": "550e8400-e29b-41d4-a716-446655440001",
            "supported_claim_ids": ["550e8400-e29b-41d4-a716-446655440002"],
            "confidence": 1.5,  # Invalid!
            "timestamp": "2026-01-06T12:00:00Z",
        }

        result = validator.validate(record)
        assert not result.passed
        assert any(e.code == "CONFIDENCE_OUT_OF_RANGE" for e in result.errors)

    def test_invalid_uncertainty_impact_fails(self) -> None:
        """Uncertainty with invalid impact value fails."""
        validator = MuhasabahValidator()

        record = {
            "agent_id": "agent",
            "output_id": "550e8400-e29b-41d4-a716-446655440001",
            "supported_claim_ids": ["550e8400-e29b-41d4-a716-446655440002"],
            "confidence": 0.85,
            "uncertainties": [
                {
                    "uncertainty": "Something uncertain",
                    "impact": "EXTREME",  # Invalid - must be HIGH/MEDIUM/LOW
                    "mitigation": "Do something",
                }
            ],
            "falsifiability_tests": [
                {
                    "test_description": "Test",
                    "required_evidence": "Evidence",
                    "pass_fail_rule": "Rule",
                }
            ],
            "timestamp": "2026-01-06T12:00:00Z",
        }

        result = validator.validate(record)
        assert not result.passed
        assert any(e.code == "INVALID_IMPACT_VALUE" for e in result.errors)
