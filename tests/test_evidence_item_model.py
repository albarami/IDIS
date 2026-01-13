"""Tests for EvidenceItem model - schema alignment, fail-closed, and determinism.

Phase 3.3: Tests for EvidenceItem Pydantic model.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from idis.models.evidence_item import (
    EvidenceItem,
    SourceGrade,
    SourceSubgrade,
    VerificationStatus,
)
from idis.validators import SchemaValidator


class TestEvidenceItemSchemaAlignment:
    """Tests proving EvidenceItem aligns with JSON schema."""

    def test_minimal_valid_instance_passes_schema(self) -> None:
        """Minimal valid EvidenceItem passes JSON schema validation."""
        evidence = EvidenceItem(
            evidence_id="550e8400-e29b-41d4-a716-446655440000",
            tenant_id="550e8400-e29b-41d4-a716-446655440001",
            deal_id="550e8400-e29b-41d4-a716-446655440002",
            source_grade=SourceGrade.A,
            verification_status=VerificationStatus.UNVERIFIED,
        )

        validator = SchemaValidator()
        result = validator.validate(
            "evidence_item", evidence.model_dump(mode="json", exclude_none=True)
        )
        assert result.passed, f"Expected pass but got errors: {result.errors}"

    def test_fully_populated_instance_passes_schema(self) -> None:
        """Fully populated EvidenceItem passes JSON schema validation."""
        now = datetime.now(UTC)
        evidence = EvidenceItem(
            evidence_id="550e8400-e29b-41d4-a716-446655440000",
            tenant_id="550e8400-e29b-41d4-a716-446655440001",
            deal_id="550e8400-e29b-41d4-a716-446655440002",
            source_span_id="550e8400-e29b-41d4-a716-446655440003",
            source_system="QuickBooks",
            upstream_origin_id="qb-tx-12345",
            retrieval_timestamp=now,
            verification_status=VerificationStatus.VERIFIED,
            source_grade=SourceGrade.B,
            source_rank_subgrade=SourceSubgrade.B_PLUS,
            rationale={"reason": "Direct bank integration", "confidence": 0.95},
            created_at=now,
            updated_at=now,
        )

        validator = SchemaValidator()
        result = validator.validate(
            "evidence_item", evidence.model_dump(mode="json", exclude_none=True)
        )
        assert result.passed, f"Expected pass but got errors: {result.errors}"


class TestEvidenceItemFailClosed:
    """Tests proving fail-closed behavior of EvidenceItem."""

    def test_unknown_field_rejected(self) -> None:
        """Unknown field is rejected (extra=forbid)."""
        with pytest.raises(ValidationError) as exc_info:
            EvidenceItem(
                evidence_id="550e8400-e29b-41d4-a716-446655440000",
                tenant_id="550e8400-e29b-41d4-a716-446655440001",
                deal_id="550e8400-e29b-41d4-a716-446655440002",
                source_grade=SourceGrade.A,
                verification_status=VerificationStatus.UNVERIFIED,
                unknown_field="should fail",  # type: ignore[call-arg]
            )

        assert "extra" in str(exc_info.value).lower() or "unknown_field" in str(exc_info.value)

    def test_invalid_source_grade_enum_rejected(self) -> None:
        """Invalid source_grade enum value is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            EvidenceItem(
                evidence_id="550e8400-e29b-41d4-a716-446655440000",
                tenant_id="550e8400-e29b-41d4-a716-446655440001",
                deal_id="550e8400-e29b-41d4-a716-446655440002",
                source_grade="X",  # type: ignore[arg-type]
                verification_status=VerificationStatus.UNVERIFIED,
            )

        assert "source_grade" in str(exc_info.value).lower()

    def test_invalid_verification_status_enum_rejected(self) -> None:
        """Invalid verification_status enum value is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            EvidenceItem(
                evidence_id="550e8400-e29b-41d4-a716-446655440000",
                tenant_id="550e8400-e29b-41d4-a716-446655440001",
                deal_id="550e8400-e29b-41d4-a716-446655440002",
                source_grade=SourceGrade.A,
                verification_status="INVALID_STATUS",  # type: ignore[arg-type]
            )

        assert "verification_status" in str(exc_info.value).lower()

    def test_missing_required_field_rejected(self) -> None:
        """Missing required field is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            EvidenceItem(
                evidence_id="550e8400-e29b-41d4-a716-446655440000",
                tenant_id="550e8400-e29b-41d4-a716-446655440001",
                # missing deal_id
                source_grade=SourceGrade.A,
                verification_status=VerificationStatus.UNVERIFIED,
            )  # type: ignore[call-arg]

        assert "deal_id" in str(exc_info.value)

    def test_empty_evidence_id_rejected(self) -> None:
        """Empty evidence_id is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            EvidenceItem(
                evidence_id="",
                tenant_id="550e8400-e29b-41d4-a716-446655440001",
                deal_id="550e8400-e29b-41d4-a716-446655440002",
                source_grade=SourceGrade.A,
                verification_status=VerificationStatus.UNVERIFIED,
            )

        assert "non-empty" in str(exc_info.value).lower()

    def test_wrong_type_for_rationale_rejected(self) -> None:
        """Wrong type for rationale is rejected."""
        with pytest.raises(ValidationError):
            EvidenceItem(
                evidence_id="550e8400-e29b-41d4-a716-446655440000",
                tenant_id="550e8400-e29b-41d4-a716-446655440001",
                deal_id="550e8400-e29b-41d4-a716-446655440002",
                source_grade=SourceGrade.A,
                verification_status=VerificationStatus.UNVERIFIED,
                rationale="should be a dict not a string",  # type: ignore[arg-type]
            )


class TestEvidenceItemDeterminism:
    """Tests proving deterministic serialization."""

    def test_identical_instances_produce_identical_canonical_dict(self) -> None:
        """Two identical instances produce identical canonical dicts."""
        now = datetime(2026, 1, 13, 12, 0, 0, tzinfo=UTC)

        evidence1 = EvidenceItem(
            evidence_id="550e8400-e29b-41d4-a716-446655440000",
            tenant_id="550e8400-e29b-41d4-a716-446655440001",
            deal_id="550e8400-e29b-41d4-a716-446655440002",
            source_grade=SourceGrade.A,
            verification_status=VerificationStatus.VERIFIED,
            created_at=now,
        )

        evidence2 = EvidenceItem(
            evidence_id="550e8400-e29b-41d4-a716-446655440000",
            tenant_id="550e8400-e29b-41d4-a716-446655440001",
            deal_id="550e8400-e29b-41d4-a716-446655440002",
            source_grade=SourceGrade.A,
            verification_status=VerificationStatus.VERIFIED,
            created_at=now,
        )

        assert evidence1.to_canonical_dict() == evidence2.to_canonical_dict()

    def test_identical_instances_produce_identical_stable_hash(self) -> None:
        """Two identical instances produce identical stable hashes."""
        now = datetime(2026, 1, 13, 12, 0, 0, tzinfo=UTC)

        evidence1 = EvidenceItem(
            evidence_id="550e8400-e29b-41d4-a716-446655440000",
            tenant_id="550e8400-e29b-41d4-a716-446655440001",
            deal_id="550e8400-e29b-41d4-a716-446655440002",
            source_grade=SourceGrade.A,
            verification_status=VerificationStatus.VERIFIED,
            created_at=now,
        )

        evidence2 = EvidenceItem(
            evidence_id="550e8400-e29b-41d4-a716-446655440000",
            tenant_id="550e8400-e29b-41d4-a716-446655440001",
            deal_id="550e8400-e29b-41d4-a716-446655440002",
            source_grade=SourceGrade.A,
            verification_status=VerificationStatus.VERIFIED,
            created_at=now,
        )

        assert evidence1.stable_hash() == evidence2.stable_hash()
        assert len(evidence1.stable_hash()) == 64  # SHA256 hex

    def test_different_instances_produce_different_stable_hash(self) -> None:
        """Different instances produce different stable hashes."""
        now = datetime(2026, 1, 13, 12, 0, 0, tzinfo=UTC)

        evidence1 = EvidenceItem(
            evidence_id="550e8400-e29b-41d4-a716-446655440000",
            tenant_id="550e8400-e29b-41d4-a716-446655440001",
            deal_id="550e8400-e29b-41d4-a716-446655440002",
            source_grade=SourceGrade.A,
            verification_status=VerificationStatus.VERIFIED,
            created_at=now,
        )

        evidence2 = EvidenceItem(
            evidence_id="550e8400-e29b-41d4-a716-446655440099",  # Different ID
            tenant_id="550e8400-e29b-41d4-a716-446655440001",
            deal_id="550e8400-e29b-41d4-a716-446655440002",
            source_grade=SourceGrade.A,
            verification_status=VerificationStatus.VERIFIED,
            created_at=now,
        )

        assert evidence1.stable_hash() != evidence2.stable_hash()


class TestEvidenceItemToDbDict:
    """Tests for database serialization."""

    def test_to_db_dict_produces_valid_dict(self) -> None:
        """to_db_dict produces a valid dictionary for database insertion."""
        now = datetime.now(UTC)
        evidence = EvidenceItem(
            evidence_id="550e8400-e29b-41d4-a716-446655440000",
            tenant_id="550e8400-e29b-41d4-a716-446655440001",
            deal_id="550e8400-e29b-41d4-a716-446655440002",
            source_grade=SourceGrade.B,
            source_rank_subgrade=SourceSubgrade.B_PLUS,
            verification_status=VerificationStatus.VERIFIED,
            created_at=now,
        )

        db_dict = evidence.to_db_dict()

        assert db_dict["evidence_id"] == "550e8400-e29b-41d4-a716-446655440000"
        assert db_dict["tenant_id"] == "550e8400-e29b-41d4-a716-446655440001"
        assert db_dict["source_grade"] == "B"
        assert db_dict["source_rank_subgrade"] == "B+"
        assert db_dict["verification_status"] == "VERIFIED"


class TestSourceGradeEnum:
    """Tests for SourceGrade enum."""

    def test_all_grades_valid(self) -> None:
        """All expected grades are valid."""
        assert SourceGrade.A.value == "A"
        assert SourceGrade.B.value == "B"
        assert SourceGrade.C.value == "C"
        assert SourceGrade.D.value == "D"


class TestVerificationStatusEnum:
    """Tests for VerificationStatus enum."""

    def test_all_statuses_valid(self) -> None:
        """All expected statuses are valid."""
        assert VerificationStatus.UNVERIFIED.value == "UNVERIFIED"
        assert VerificationStatus.VERIFIED.value == "VERIFIED"
        assert VerificationStatus.CONTRADICTED.value == "CONTRADICTED"
