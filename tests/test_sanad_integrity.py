"""Tests for SanadIntegrityValidator - proves fail-closed behavior.

Phase 1.3: Sanad Integrity Validator tests covering:
- Fail-closed behavior (None, non-dict inputs)
- Required field validation (sanad_id, claim_id, transmission_chain)
- Transmission node integrity
- Chain linkage validity (cycles, orphans, multiple roots)
- UUID format validation
- Defect consistency (FATAL -> grade D)
"""

from __future__ import annotations

from idis.validators import SanadIntegrityValidator, validate_sanad_integrity


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


class TestChainLinkageValidation:
    """Tests for chain linkage integrity - cycles, orphans, multiple roots."""

    def _make_base_sanad(self) -> dict:
        """Create a base valid sanad for modification in tests."""
        return {
            "sanad_id": "550e8400-e29b-41d4-a716-446655440000",
            "tenant_id": "550e8400-e29b-41d4-a716-446655440001",
            "claim_id": "550e8400-e29b-41d4-a716-446655440002",
            "primary_evidence_id": "550e8400-e29b-41d4-a716-446655440003",
            "extraction_confidence": 0.98,
            "corroboration_status": "AHAD_1",
            "sanad_grade": "B",
            "transmission_chain": [],
        }

    def test_valid_chain_with_linkage_passes(self) -> None:
        """Valid chain with root + child nodes passes."""
        validator = SanadIntegrityValidator()
        sanad = self._make_base_sanad()
        sanad["transmission_chain"] = [
            {
                "node_id": "550e8400-e29b-41d4-a716-446655440010",
                "node_type": "INGEST",
                "actor_type": "SYSTEM",
                "actor_id": "ingestion-service",
                "timestamp": "2026-01-06T10:00:00Z",
            },
            {
                "node_id": "550e8400-e29b-41d4-a716-446655440011",
                "node_type": "EXTRACT",
                "actor_type": "AGENT",
                "actor_id": "extractor-v1",
                "timestamp": "2026-01-06T10:01:00Z",
                "prev_node_id": "550e8400-e29b-41d4-a716-446655440010",
            },
        ]

        result = validator.validate_sanad(sanad)
        assert result.passed, f"Expected pass but got: {result.errors}"

    def test_orphan_parent_reference_fails(self) -> None:
        """Node referencing non-existent parent fails."""
        validator = SanadIntegrityValidator()
        sanad = self._make_base_sanad()
        sanad["transmission_chain"] = [
            {
                "node_id": "550e8400-e29b-41d4-a716-446655440010",
                "node_type": "INGEST",
                "actor_type": "SYSTEM",
                "actor_id": "ingestion-service",
                "timestamp": "2026-01-06T10:00:00Z",
            },
            {
                "node_id": "550e8400-e29b-41d4-a716-446655440011",
                "node_type": "EXTRACT",
                "actor_type": "AGENT",
                "actor_id": "extractor-v1",
                "timestamp": "2026-01-06T10:01:00Z",
                "prev_node_id": "550e8400-e29b-41d4-a716-446655440099",  # Non-existent!
            },
        ]

        result = validator.validate_sanad(sanad)
        assert not result.passed
        assert any(e.code == "SANAD_ORPHAN_REFERENCE" for e in result.errors)

    def test_cycle_in_chain_fails(self) -> None:
        """Cycle in transmission chain fails."""
        validator = SanadIntegrityValidator()
        sanad = self._make_base_sanad()
        sanad["transmission_chain"] = [
            {
                "node_id": "550e8400-e29b-41d4-a716-446655440010",
                "node_type": "INGEST",
                "actor_type": "SYSTEM",
                "actor_id": "ingestion-service",
                "timestamp": "2026-01-06T10:00:00Z",
                "prev_node_id": "550e8400-e29b-41d4-a716-446655440012",  # Creates cycle
            },
            {
                "node_id": "550e8400-e29b-41d4-a716-446655440011",
                "node_type": "EXTRACT",
                "actor_type": "AGENT",
                "actor_id": "extractor-v1",
                "timestamp": "2026-01-06T10:01:00Z",
                "prev_node_id": "550e8400-e29b-41d4-a716-446655440010",
            },
            {
                "node_id": "550e8400-e29b-41d4-a716-446655440012",
                "node_type": "NORMALIZE",
                "actor_type": "AGENT",
                "actor_id": "normalizer-v1",
                "timestamp": "2026-01-06T10:02:00Z",
                "prev_node_id": "550e8400-e29b-41d4-a716-446655440011",
            },
        ]

        result = validator.validate_sanad(sanad)
        assert not result.passed
        assert any(e.code == "SANAD_CYCLE_DETECTED" for e in result.errors)

    def test_multiple_roots_fails(self) -> None:
        """Multiple root nodes (no parent) fails."""
        validator = SanadIntegrityValidator()
        sanad = self._make_base_sanad()
        sanad["transmission_chain"] = [
            {
                "node_id": "550e8400-e29b-41d4-a716-446655440010",
                "node_type": "INGEST",
                "actor_type": "SYSTEM",
                "actor_id": "ingestion-service",
                "timestamp": "2026-01-06T10:00:00Z",
                # No prev_node_id - root
            },
            {
                "node_id": "550e8400-e29b-41d4-a716-446655440011",
                "node_type": "EXTRACT",
                "actor_type": "AGENT",
                "actor_id": "extractor-v1",
                "timestamp": "2026-01-06T10:01:00Z",
                # No prev_node_id - another root!
            },
            {
                "node_id": "550e8400-e29b-41d4-a716-446655440012",
                "node_type": "NORMALIZE",
                "actor_type": "AGENT",
                "actor_id": "normalizer-v1",
                "timestamp": "2026-01-06T10:02:00Z",
                "prev_node_id": "550e8400-e29b-41d4-a716-446655440010",
            },
        ]

        result = validator.validate_sanad(sanad)
        assert not result.passed
        assert any(e.code == "SANAD_MULTIPLE_ROOTS" for e in result.errors)

    def test_orphan_node_not_connected_to_root_fails(self) -> None:
        """Node not connected to root graph fails."""
        validator = SanadIntegrityValidator()
        sanad = self._make_base_sanad()
        sanad["transmission_chain"] = [
            {
                "node_id": "550e8400-e29b-41d4-a716-446655440010",
                "node_type": "INGEST",
                "actor_type": "SYSTEM",
                "actor_id": "ingestion-service",
                "timestamp": "2026-01-06T10:00:00Z",
            },
            {
                "node_id": "550e8400-e29b-41d4-a716-446655440011",
                "node_type": "EXTRACT",
                "actor_type": "AGENT",
                "actor_id": "extractor-v1",
                "timestamp": "2026-01-06T10:01:00Z",
                "prev_node_id": "550e8400-e29b-41d4-a716-446655440010",
            },
            {
                "node_id": "550e8400-e29b-41d4-a716-446655440012",
                "node_type": "NORMALIZE",
                "actor_type": "AGENT",
                "actor_id": "normalizer-v1",
                "timestamp": "2026-01-06T10:02:00Z",
                "prev_node_id": "550e8400-e29b-41d4-a716-446655440013",  # Points to 13
            },
            {
                "node_id": "550e8400-e29b-41d4-a716-446655440013",
                "node_type": "CALCULATE",
                "actor_type": "AGENT",
                "actor_id": "calc-v1",
                "timestamp": "2026-01-06T10:03:00Z",
                # Points to 12 - cycle but isolated
                "prev_node_id": "550e8400-e29b-41d4-a716-446655440012",
            },
        ]

        result = validator.validate_sanad(sanad)
        assert not result.passed
        has_orphan_or_cycle = any(
            e.code in ("SANAD_ORPHAN_NODE", "SANAD_CYCLE_DETECTED") for e in result.errors
        )
        assert has_orphan_or_cycle


class TestUUIDValidation:
    """Tests for UUID format validation."""

    def test_invalid_sanad_id_format_fails(self) -> None:
        """Invalid sanad_id UUID format fails."""
        validator = SanadIntegrityValidator()
        sanad = {
            "sanad_id": "not-a-valid-uuid",  # Invalid!
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

        result = validator.validate_sanad(sanad)
        assert not result.passed
        assert any(e.code == "INVALID_UUID_FORMAT" for e in result.errors)

    def test_invalid_node_id_format_fails(self) -> None:
        """Invalid node_id UUID format fails."""
        validator = SanadIntegrityValidator()
        sanad = {
            "sanad_id": "550e8400-e29b-41d4-a716-446655440000",
            "claim_id": "550e8400-e29b-41d4-a716-446655440002",
            "primary_evidence_id": "550e8400-e29b-41d4-a716-446655440003",
            "extraction_confidence": 0.98,
            "corroboration_status": "AHAD_1",
            "sanad_grade": "B",
            "transmission_chain": [
                {
                    "node_id": "invalid-node-id-123",  # Invalid!
                    "node_type": "EXTRACT",
                    "actor_type": "AGENT",
                    "actor_id": "extractor-v1",
                    "timestamp": "2026-01-06T12:00:00Z",
                }
            ],
        }

        result = validator.validate_sanad(sanad)
        assert not result.passed
        assert any(e.code == "INVALID_NODE_ID_FORMAT" for e in result.errors)

    def test_missing_node_required_field_fails(self) -> None:
        """Transmission node missing required field (e.g., actor_id) fails."""
        validator = SanadIntegrityValidator()
        sanad = {
            "sanad_id": "550e8400-e29b-41d4-a716-446655440000",
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
                    # actor_id is MISSING
                    "timestamp": "2026-01-06T12:00:00Z",
                }
            ],
        }

        result = validator.validate_sanad(sanad)
        assert not result.passed
        assert any(e.code == "MISSING_ACTOR_ID" for e in result.errors)


class TestPublicAPIFunction:
    """Tests for the public validate_sanad_integrity function."""

    def test_validate_sanad_integrity_passes_valid(self) -> None:
        """Public API function passes valid sanad."""
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

        result = validate_sanad_integrity(valid_sanad)
        assert result.passed, f"Expected pass but got: {result.errors}"

    def test_validate_sanad_integrity_fails_missing_sanad_id(self) -> None:
        """Public API function fails on missing sanad_id."""
        invalid_sanad = {
            # sanad_id is MISSING
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

        result = validate_sanad_integrity(invalid_sanad)
        assert not result.passed
        assert any(e.code == "MISSING_SANAD_ID" for e in result.errors)

    def test_validate_sanad_integrity_fails_closed_on_none(self) -> None:
        """Public API function fails closed on None input."""
        result = validate_sanad_integrity(None)  # type: ignore[arg-type]
        assert not result.passed
        assert result.errors[0].code == "FAIL_CLOSED"

    def test_validate_sanad_integrity_fails_closed_on_non_dict(self) -> None:
        """Public API function fails closed on non-dict input."""
        result = validate_sanad_integrity([1, 2, 3])  # type: ignore[arg-type]
        assert not result.passed
        assert result.errors[0].code == "FAIL_CLOSED"
