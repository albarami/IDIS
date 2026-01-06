"""Tests for SanadIntegrityValidator - proves fail-closed behavior."""

from __future__ import annotations

from idis.validators import SanadIntegrityValidator


class TestSanadIntegrityFailClosed:
    """Tests proving fail-closed behavior."""

    def test_none_data_fails_closed(self) -> None:
        """Validator rejects None data."""
        validator = SanadIntegrityValidator()
        result = validator.validate_sanad(None)

        assert not result.passed
        assert result.errors[0].code == "FAIL_CLOSED"

    def test_non_dict_fails_closed(self) -> None:
        """Validator rejects non-dict data."""
        validator = SanadIntegrityValidator()
        result = validator.validate_sanad("string data")

        assert not result.passed
        assert result.errors[0].code == "FAIL_CLOSED"


class TestSanadIntegrityPositive:
    """Positive tests - valid Sanads pass."""

    def test_valid_sanad_passes(self) -> None:
        """Valid Sanad record passes validation."""
        validator = SanadIntegrityValidator()

        valid_sanad = {
            "sanad_id": "550e8400-e29b-41d4-a716-446655440000",
            "tenant_id": "550e8400-e29b-41d4-a716-446655440001",
            "claim_id": "550e8400-e29b-41d4-a716-446655440002",
            "primary_evidence_id": "550e8400-e29b-41d4-a716-446655440003",
            "extraction_confidence": 0.98,
            "corroboration_status": "AHAD_1",
            "sanad_grade": "B",
            "transmission_chain": [
                {
                    "node_id": "550e8400-e29b-41d4-a716-446655440004",
                    "node_type": "EXTRACT",
                    "actor_type": "AGENT",
                    "actor_id": "extractor-v1",
                    "timestamp": "2026-01-06T12:00:00Z",
                }
            ],
        }

        result = validator.validate_sanad(valid_sanad)
        assert result.passed, f"Expected pass but got: {result.errors}"

    def test_valid_claim_passes(self) -> None:
        """Valid Claim record passes validation."""
        validator = SanadIntegrityValidator()

        valid_claim = {
            "claim_id": "550e8400-e29b-41d4-a716-446655440000",
            "claim_grade": "A",
            "claim_verdict": "VERIFIED",
            "claim_action": "NONE",
        }

        result = validator.validate_claim(valid_claim)
        assert result.passed


class TestSanadIntegrityNegative:
    """Negative tests - invalid Sanads fail."""

    def test_missing_primary_evidence_fails(self) -> None:
        """Sanad without primary evidence fails."""
        validator = SanadIntegrityValidator()

        invalid_sanad = {
            "sanad_id": "550e8400-e29b-41d4-a716-446655440000",
            "claim_id": "550e8400-e29b-41d4-a716-446655440002",
            # primary_evidence_id is MISSING
            "extraction_confidence": 0.98,
            "corroboration_status": "AHAD_1",
            "sanad_grade": "B",
            "transmission_chain": [
                {
                    "node_id": "550e8400-e29b-41d4-a716-446655440004",
                    "node_type": "EXTRACT",
                    "actor_type": "AGENT",
                    "actor_id": "extractor-v1",
                    "timestamp": "2026-01-06T12:00:00Z",
                }
            ],
        }

        result = validator.validate_sanad(invalid_sanad)
        assert not result.passed
        assert any(e.code == "MISSING_PRIMARY_EVIDENCE" for e in result.errors)

    def test_empty_transmission_chain_fails(self) -> None:
        """Sanad with empty transmission chain fails."""
        validator = SanadIntegrityValidator()

        invalid_sanad = {
            "sanad_id": "550e8400-e29b-41d4-a716-446655440000",
            "claim_id": "550e8400-e29b-41d4-a716-446655440002",
            "primary_evidence_id": "550e8400-e29b-41d4-a716-446655440003",
            "extraction_confidence": 0.98,
            "corroboration_status": "AHAD_1",
            "sanad_grade": "B",
            "transmission_chain": [],  # Empty!
        }

        result = validator.validate_sanad(invalid_sanad)
        assert not result.passed
        assert any(e.code == "EMPTY_TRANSMISSION_CHAIN" for e in result.errors)

    def test_invalid_node_type_fails(self) -> None:
        """Sanad with invalid transmission node type fails."""
        validator = SanadIntegrityValidator()

        invalid_sanad = {
            "sanad_id": "550e8400-e29b-41d4-a716-446655440000",
            "claim_id": "550e8400-e29b-41d4-a716-446655440002",
            "primary_evidence_id": "550e8400-e29b-41d4-a716-446655440003",
            "extraction_confidence": 0.98,
            "corroboration_status": "AHAD_1",
            "sanad_grade": "B",
            "transmission_chain": [
                {
                    "node_id": "550e8400-e29b-41d4-a716-446655440004",
                    "node_type": "INVALID_TYPE",  # Invalid!
                    "actor_type": "AGENT",
                    "actor_id": "extractor-v1",
                    "timestamp": "2026-01-06T12:00:00Z",
                }
            ],
        }

        result = validator.validate_sanad(invalid_sanad)
        assert not result.passed
        assert any(e.code == "INVALID_NODE_TYPE" for e in result.errors)

    def test_fatal_defect_requires_grade_d(self) -> None:
        """FATAL defect with non-D grade fails."""
        validator = SanadIntegrityValidator()

        invalid_sanad = {
            "sanad_id": "550e8400-e29b-41d4-a716-446655440000",
            "claim_id": "550e8400-e29b-41d4-a716-446655440002",
            "primary_evidence_id": "550e8400-e29b-41d4-a716-446655440003",
            "extraction_confidence": 0.98,
            "corroboration_status": "NONE",
            "sanad_grade": "B",  # Should be D due to FATAL defect
            "transmission_chain": [
                {
                    "node_id": "550e8400-e29b-41d4-a716-446655440004",
                    "node_type": "EXTRACT",
                    "actor_type": "AGENT",
                    "actor_id": "extractor-v1",
                    "timestamp": "2026-01-06T12:00:00Z",
                }
            ],
            "defects": [
                {
                    "defect_id": "550e8400-e29b-41d4-a716-446655440005",
                    "defect_type": "BROKEN_CHAIN",
                    "severity": "FATAL",  # FATAL!
                    "description": "Chain is completely broken",
                    "cure_protocol": "DISCARD_CLAIM",
                    "status": "OPEN",  # Still open
                }
            ],
        }

        result = validator.validate_sanad(invalid_sanad)
        assert not result.passed
        assert any(e.code == "GRADE_DEFECT_MISMATCH" for e in result.errors)

    def test_invalid_sanad_grade_fails(self) -> None:
        """Invalid sanad grade fails."""
        validator = SanadIntegrityValidator()

        invalid_sanad = {
            "sanad_id": "550e8400-e29b-41d4-a716-446655440000",
            "claim_id": "550e8400-e29b-41d4-a716-446655440002",
            "primary_evidence_id": "550e8400-e29b-41d4-a716-446655440003",
            "extraction_confidence": 0.98,
            "corroboration_status": "AHAD_1",
            "sanad_grade": "X",  # Invalid grade!
            "transmission_chain": [
                {
                    "node_id": "550e8400-e29b-41d4-a716-446655440004",
                    "node_type": "EXTRACT",
                    "actor_type": "AGENT",
                    "actor_id": "extractor-v1",
                    "timestamp": "2026-01-06T12:00:00Z",
                }
            ],
        }

        result = validator.validate_sanad(invalid_sanad)
        assert not result.passed
        assert any(e.code == "INVALID_SANAD_GRADE" for e in result.errors)


class TestClaimIntegrity:
    """Tests for claim grade/verdict/action consistency."""

    def test_grade_d_requires_action(self) -> None:
        """Grade D claim with NONE action fails."""
        validator = SanadIntegrityValidator()

        invalid_claim = {
            "claim_id": "550e8400-e29b-41d4-a716-446655440000",
            "claim_grade": "D",  # Low grade
            "claim_verdict": "UNVERIFIED",
            "claim_action": "NONE",  # Should not be NONE!
        }

        result = validator.validate_claim(invalid_claim)
        assert not result.passed
        assert any(e.code == "GRADE_ACTION_MISMATCH" for e in result.errors)

    def test_contradicted_verdict_requires_action(self) -> None:
        """CONTRADICTED verdict with NONE action fails."""
        validator = SanadIntegrityValidator()

        invalid_claim = {
            "claim_id": "550e8400-e29b-41d4-a716-446655440000",
            "claim_grade": "C",
            "claim_verdict": "CONTRADICTED",  # Contradicted!
            "claim_action": "NONE",  # Should have action!
        }

        result = validator.validate_claim(invalid_claim)
        assert not result.passed
        assert any(e.code == "VERDICT_ACTION_MISMATCH" for e in result.errors)

    def test_invalid_verdict_fails(self) -> None:
        """Invalid verdict value fails."""
        validator = SanadIntegrityValidator()

        invalid_claim = {
            "claim_id": "550e8400-e29b-41d4-a716-446655440000",
            "claim_grade": "A",
            "claim_verdict": "MAYBE",  # Invalid!
            "claim_action": "NONE",
        }

        result = validator.validate_claim(invalid_claim)
        assert not result.passed
        assert any(e.code == "INVALID_CLAIM_VERDICT" for e in result.errors)
