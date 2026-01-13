"""Tests for Defect model - schema alignment, fail-closed, and determinism.

Phase 3.3: Tests for Defect Pydantic model.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from idis.models.defect import (
    CureProtocol,
    Defect,
    DefectSeverity,
    DefectStatus,
    DefectType,
)
from idis.validators import SchemaValidator


class TestDefectSchemaAlignment:
    """Tests proving Defect aligns with JSON schema."""

    def test_minimal_valid_instance_passes_schema(self) -> None:
        """Minimal valid Defect passes JSON schema validation."""
        defect = Defect(
            defect_id="550e8400-e29b-41d4-a716-446655440000",
            defect_type=DefectType.BROKEN_CHAIN,
            severity=DefectSeverity.FATAL,
            description="Chain is broken at extraction step",
            cure_protocol=CureProtocol.RECONSTRUCT_CHAIN,
            status=DefectStatus.OPEN,
            affected_claim_ids=["550e8400-e29b-41d4-a716-446655440001"],
            timestamp=datetime.now(UTC),
        )

        validator = SchemaValidator()
        result = validator.validate("defect", defect.model_dump(mode="json", exclude_none=True))
        assert result.passed, f"Expected pass but got errors: {result.errors}"

    def test_fully_populated_instance_passes_schema(self) -> None:
        """Fully populated Defect passes JSON schema validation."""
        now = datetime.now(UTC)
        defect = Defect(
            defect_id="550e8400-e29b-41d4-a716-446655440000",
            tenant_id="550e8400-e29b-41d4-a716-446655440010",
            deal_id="550e8400-e29b-41d4-a716-446655440020",
            defect_type=DefectType.INCONSISTENCY,
            severity=DefectSeverity.MAJOR,
            detected_by="550e8400-e29b-41d4-a716-446655440030",
            description="Revenue figures inconsistent across documents",
            evidence_refs=[
                {"type": "span", "span_id": "span-123"},
                {"type": "span", "span_id": "span-456"},
            ],
            cure_protocol=CureProtocol.HUMAN_ARBITRATION,
            status=DefectStatus.OPEN,
            waiver_reason=None,
            waived_by=None,
            affected_claim_ids=[
                "550e8400-e29b-41d4-a716-446655440001",
                "550e8400-e29b-41d4-a716-446655440002",
            ],
            timestamp=now,
            created_at=now,
            updated_at=now,
        )

        validator = SchemaValidator()
        result = validator.validate("defect", defect.model_dump(mode="json", exclude_none=True))
        assert result.passed, f"Expected pass but got errors: {result.errors}"


class TestDefectFailClosed:
    """Tests proving fail-closed behavior of Defect."""

    def test_unknown_field_rejected(self) -> None:
        """Unknown field is rejected (extra=forbid)."""
        with pytest.raises(ValidationError) as exc_info:
            Defect(
                defect_id="550e8400-e29b-41d4-a716-446655440000",
                defect_type=DefectType.BROKEN_CHAIN,
                severity=DefectSeverity.FATAL,
                description="Chain is broken",
                cure_protocol=CureProtocol.RECONSTRUCT_CHAIN,
                status=DefectStatus.OPEN,
                affected_claim_ids=[],
                timestamp=datetime.now(UTC),
                unknown_field="should fail",  # type: ignore[call-arg]
            )

        assert "extra" in str(exc_info.value).lower() or "unknown_field" in str(exc_info.value)

    def test_invalid_defect_type_enum_rejected(self) -> None:
        """Invalid defect_type enum value is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            Defect(
                defect_id="550e8400-e29b-41d4-a716-446655440000",
                defect_type="INVALID_TYPE",  # type: ignore[arg-type]
                severity=DefectSeverity.FATAL,
                description="Test",
                cure_protocol=CureProtocol.RECONSTRUCT_CHAIN,
                status=DefectStatus.OPEN,
                affected_claim_ids=[],
                timestamp=datetime.now(UTC),
            )

        assert "defect_type" in str(exc_info.value).lower()

    def test_invalid_severity_enum_rejected(self) -> None:
        """Invalid severity enum value is rejected (e.g., CRITICAL is not allowed)."""
        with pytest.raises(ValidationError) as exc_info:
            Defect(
                defect_id="550e8400-e29b-41d4-a716-446655440000",
                defect_type=DefectType.BROKEN_CHAIN,
                severity="CRITICAL",  # type: ignore[arg-type]
                description="Test",
                cure_protocol=CureProtocol.RECONSTRUCT_CHAIN,
                status=DefectStatus.OPEN,
                affected_claim_ids=[],
                timestamp=datetime.now(UTC),
            )

        assert "severity" in str(exc_info.value).lower()

    def test_invalid_cure_protocol_enum_rejected(self) -> None:
        """Invalid cure_protocol enum value is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            Defect(
                defect_id="550e8400-e29b-41d4-a716-446655440000",
                defect_type=DefectType.BROKEN_CHAIN,
                severity=DefectSeverity.FATAL,
                description="Test",
                cure_protocol="FIX_IT",  # type: ignore[arg-type]
                status=DefectStatus.OPEN,
                affected_claim_ids=[],
                timestamp=datetime.now(UTC),
            )

        assert "cure_protocol" in str(exc_info.value).lower()

    def test_missing_required_field_rejected(self) -> None:
        """Missing required field is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            Defect(
                defect_id="550e8400-e29b-41d4-a716-446655440000",
                defect_type=DefectType.BROKEN_CHAIN,
                # missing severity
                description="Test",
                cure_protocol=CureProtocol.RECONSTRUCT_CHAIN,
                status=DefectStatus.OPEN,
                affected_claim_ids=[],
                timestamp=datetime.now(UTC),
            )  # type: ignore[call-arg]

        assert "severity" in str(exc_info.value)

    def test_empty_defect_id_rejected(self) -> None:
        """Empty defect_id is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            Defect(
                defect_id="",
                defect_type=DefectType.BROKEN_CHAIN,
                severity=DefectSeverity.FATAL,
                description="Test",
                cure_protocol=CureProtocol.RECONSTRUCT_CHAIN,
                status=DefectStatus.OPEN,
                affected_claim_ids=[],
                timestamp=datetime.now(UTC),
            )

        assert "non-empty" in str(exc_info.value).lower()

    def test_empty_description_rejected(self) -> None:
        """Empty description is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            Defect(
                defect_id="550e8400-e29b-41d4-a716-446655440000",
                defect_type=DefectType.BROKEN_CHAIN,
                severity=DefectSeverity.FATAL,
                description="",
                cure_protocol=CureProtocol.RECONSTRUCT_CHAIN,
                status=DefectStatus.OPEN,
                affected_claim_ids=[],
                timestamp=datetime.now(UTC),
            )

        assert "non-empty" in str(exc_info.value).lower()

    def test_affected_claim_ids_wrong_type_rejected(self) -> None:
        """Wrong type for affected_claim_ids is rejected."""
        with pytest.raises(ValidationError):
            Defect(
                defect_id="550e8400-e29b-41d4-a716-446655440000",
                defect_type=DefectType.BROKEN_CHAIN,
                severity=DefectSeverity.FATAL,
                description="Test",
                cure_protocol=CureProtocol.RECONSTRUCT_CHAIN,
                status=DefectStatus.OPEN,
                affected_claim_ids="should-be-a-list",  # type: ignore[arg-type]
                timestamp=datetime.now(UTC),
            )


class TestDefectDeterminism:
    """Tests proving deterministic serialization."""

    def test_identical_instances_produce_identical_canonical_dict(self) -> None:
        """Two identical instances produce identical canonical dicts."""
        now = datetime(2026, 1, 13, 12, 0, 0, tzinfo=UTC)

        defect1 = Defect(
            defect_id="550e8400-e29b-41d4-a716-446655440000",
            defect_type=DefectType.BROKEN_CHAIN,
            severity=DefectSeverity.FATAL,
            description="Chain is broken",
            cure_protocol=CureProtocol.RECONSTRUCT_CHAIN,
            status=DefectStatus.OPEN,
            affected_claim_ids=["claim-1", "claim-2"],
            timestamp=now,
        )

        defect2 = Defect(
            defect_id="550e8400-e29b-41d4-a716-446655440000",
            defect_type=DefectType.BROKEN_CHAIN,
            severity=DefectSeverity.FATAL,
            description="Chain is broken",
            cure_protocol=CureProtocol.RECONSTRUCT_CHAIN,
            status=DefectStatus.OPEN,
            affected_claim_ids=["claim-1", "claim-2"],
            timestamp=now,
        )

        assert defect1.to_canonical_dict() == defect2.to_canonical_dict()

    def test_identical_instances_produce_identical_stable_hash(self) -> None:
        """Two identical instances produce identical stable hashes."""
        now = datetime(2026, 1, 13, 12, 0, 0, tzinfo=UTC)

        defect1 = Defect(
            defect_id="550e8400-e29b-41d4-a716-446655440000",
            defect_type=DefectType.BROKEN_CHAIN,
            severity=DefectSeverity.FATAL,
            description="Chain is broken",
            cure_protocol=CureProtocol.RECONSTRUCT_CHAIN,
            status=DefectStatus.OPEN,
            affected_claim_ids=["claim-1"],
            timestamp=now,
        )

        defect2 = Defect(
            defect_id="550e8400-e29b-41d4-a716-446655440000",
            defect_type=DefectType.BROKEN_CHAIN,
            severity=DefectSeverity.FATAL,
            description="Chain is broken",
            cure_protocol=CureProtocol.RECONSTRUCT_CHAIN,
            status=DefectStatus.OPEN,
            affected_claim_ids=["claim-1"],
            timestamp=now,
        )

        assert defect1.stable_hash() == defect2.stable_hash()
        assert len(defect1.stable_hash()) == 64  # SHA256 hex

    def test_affected_claim_ids_sorted_in_canonical_dict(self) -> None:
        """Affected claim IDs are sorted for deterministic output."""
        now = datetime(2026, 1, 13, 12, 0, 0, tzinfo=UTC)

        # Create with claims in different orders
        defect1 = Defect(
            defect_id="550e8400-e29b-41d4-a716-446655440000",
            defect_type=DefectType.BROKEN_CHAIN,
            severity=DefectSeverity.FATAL,
            description="Chain is broken",
            cure_protocol=CureProtocol.RECONSTRUCT_CHAIN,
            status=DefectStatus.OPEN,
            affected_claim_ids=["claim-b", "claim-a"],
            timestamp=now,
        )

        defect2 = Defect(
            defect_id="550e8400-e29b-41d4-a716-446655440000",
            defect_type=DefectType.BROKEN_CHAIN,
            severity=DefectSeverity.FATAL,
            description="Chain is broken",
            cure_protocol=CureProtocol.RECONSTRUCT_CHAIN,
            status=DefectStatus.OPEN,
            affected_claim_ids=["claim-a", "claim-b"],
            timestamp=now,
        )

        # After canonicalization, both should produce same hash
        assert defect1.stable_hash() == defect2.stable_hash()


class TestDefectToDbDict:
    """Tests for database serialization."""

    def test_to_db_dict_produces_valid_dict(self) -> None:
        """to_db_dict produces a valid dictionary for database insertion."""
        now = datetime.now(UTC)
        defect = Defect(
            defect_id="550e8400-e29b-41d4-a716-446655440000",
            tenant_id="550e8400-e29b-41d4-a716-446655440010",
            defect_type=DefectType.STALENESS,
            severity=DefectSeverity.MINOR,
            description="Data is stale",
            cure_protocol=CureProtocol.REQUEST_SOURCE,
            status=DefectStatus.CURED,
            affected_claim_ids=["claim-1"],
            timestamp=now,
        )

        db_dict = defect.to_db_dict()

        assert db_dict["defect_id"] == "550e8400-e29b-41d4-a716-446655440000"
        assert db_dict["defect_type"] == "STALENESS"
        assert db_dict["severity"] == "MINOR"
        assert db_dict["cure_protocol"] == "REQUEST_SOURCE"
        assert db_dict["status"] == "CURED"


class TestDefectTypeEnum:
    """Tests for DefectType enum."""

    def test_all_defect_types_valid(self) -> None:
        """All expected defect types are valid."""
        expected = [
            "BROKEN_CHAIN",
            "MISSING_LINK",
            "UNKNOWN_SOURCE",
            "CONCEALMENT",
            "INCONSISTENCY",
            "ANOMALY_VS_STRONGER_SOURCES",
            "CHRONO_IMPOSSIBLE",
            "CHAIN_GRAFTING",
            "CIRCULARITY",
            "STALENESS",
            "UNIT_MISMATCH",
            "TIME_WINDOW_MISMATCH",
            "SCOPE_DRIFT",
            "IMPLAUSIBILITY",
        ]
        actual = [dt.value for dt in DefectType]
        assert sorted(actual) == sorted(expected)


class TestDefectSeverityEnum:
    """Tests for DefectSeverity enum."""

    def test_all_severities_valid(self) -> None:
        """All expected severities are valid (FATAL, MAJOR, MINOR only)."""
        assert DefectSeverity.FATAL.value == "FATAL"
        assert DefectSeverity.MAJOR.value == "MAJOR"
        assert DefectSeverity.MINOR.value == "MINOR"
        # CRITICAL is NOT a valid severity per schema
        assert len(DefectSeverity) == 3


class TestCureProtocolEnum:
    """Tests for CureProtocol enum."""

    def test_all_cure_protocols_valid(self) -> None:
        """All expected cure protocols are valid."""
        expected = [
            "REQUEST_SOURCE",
            "REQUIRE_REAUDIT",
            "HUMAN_ARBITRATION",
            "RECONSTRUCT_CHAIN",
            "DISCARD_CLAIM",
        ]
        actual = [cp.value for cp in CureProtocol]
        assert sorted(actual) == sorted(expected)
