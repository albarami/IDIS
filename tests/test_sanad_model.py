"""Tests for Sanad model - schema alignment, fail-closed, and determinism.

Phase 3.3: Tests for Sanad Pydantic model.
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
from idis.models.sanad import (
    CorroborationStatus,
    Sanad,
    SanadGrade,
)
from idis.models.transmission_node import (
    ActorType,
    NodeType,
    TransmissionNode,
)


def make_transmission_node(
    node_id: str = "550e8400-e29b-41d4-a716-446655440100",
    node_type: NodeType = NodeType.EXTRACT,
) -> TransmissionNode:
    """Helper to create a valid TransmissionNode."""
    return TransmissionNode(
        node_id=node_id,
        node_type=node_type,
        actor_type=ActorType.AGENT,
        actor_id="extractor-v1",
        timestamp=datetime.now(UTC),
        confidence=0.95,
    )


def make_defect(
    defect_id: str = "550e8400-e29b-41d4-a716-446655440200",
) -> Defect:
    """Helper to create a valid Defect."""
    return Defect(
        defect_id=defect_id,
        defect_type=DefectType.BROKEN_CHAIN,
        severity=DefectSeverity.MINOR,
        description="Minor chain issue",
        cure_protocol=CureProtocol.REQUEST_SOURCE,
        status=DefectStatus.OPEN,
        affected_claim_ids=["claim-1"],
        timestamp=datetime.now(UTC),
    )


class TestSanadSchemaAlignment:
    """Tests proving Sanad model structure aligns with schema expectations.

    Note: JSON schema validation is not used here because sanad.schema.json
    contains $ref to external URLs that cannot be resolved locally.
    The nested TransmissionNode and Defect types are validated by their own
    schema alignment tests. This test class validates model structure.
    """

    def test_minimal_valid_instance_structure(self) -> None:
        """Minimal valid Sanad has correct structure."""
        sanad = Sanad(
            sanad_id="550e8400-e29b-41d4-a716-446655440000",
            tenant_id="550e8400-e29b-41d4-a716-446655440001",
            claim_id="550e8400-e29b-41d4-a716-446655440002",
            primary_evidence_id="550e8400-e29b-41d4-a716-446655440003",
            extraction_confidence=0.95,
            corroboration_status=CorroborationStatus.NONE,
            sanad_grade=SanadGrade.A,
            transmission_chain=[make_transmission_node()],
        )

        # Verify all required fields are present
        data = sanad.model_dump(mode="json", exclude_none=True)
        assert data["sanad_id"] == "550e8400-e29b-41d4-a716-446655440000"
        assert data["tenant_id"] == "550e8400-e29b-41d4-a716-446655440001"
        assert data["claim_id"] == "550e8400-e29b-41d4-a716-446655440002"
        assert data["primary_evidence_id"] == "550e8400-e29b-41d4-a716-446655440003"
        assert data["extraction_confidence"] == 0.95
        assert data["corroboration_status"] == "NONE"
        assert data["sanad_grade"] == "A"
        assert len(data["transmission_chain"]) == 1

    def test_fully_populated_instance_structure(self) -> None:
        """Fully populated Sanad has correct structure."""
        now = datetime.now(UTC)
        sanad = Sanad(
            sanad_id="550e8400-e29b-41d4-a716-446655440000",
            tenant_id="550e8400-e29b-41d4-a716-446655440001",
            claim_id="550e8400-e29b-41d4-a716-446655440002",
            deal_id="550e8400-e29b-41d4-a716-446655440010",
            primary_evidence_id="550e8400-e29b-41d4-a716-446655440003",
            corroborating_evidence_ids=[
                "550e8400-e29b-41d4-a716-446655440004",
                "550e8400-e29b-41d4-a716-446655440005",
            ],
            extraction_confidence=0.92,
            dhabt_score=0.88,
            corroboration_status=CorroborationStatus.MUTAWATIR,
            sanad_grade=SanadGrade.B,
            grade_explanation=[
                {"step": "Primary evidence grade", "grade": "B"},
                {"step": "Corroboration boost", "adjustment": "+0"},
            ],
            transmission_chain=[
                make_transmission_node("node-1", NodeType.INGEST),
                make_transmission_node("node-2", NodeType.EXTRACT),
                make_transmission_node("node-3", NodeType.NORMALIZE),
            ],
            defects=[make_defect()],
            created_at=now,
            updated_at=now,
        )

        # Verify structure
        data = sanad.model_dump(mode="json", exclude_none=True)
        assert data["deal_id"] == "550e8400-e29b-41d4-a716-446655440010"
        assert len(data["corroborating_evidence_ids"]) == 2
        assert data["dhabt_score"] == 0.88
        assert data["corroboration_status"] == "MUTAWATIR"
        assert data["sanad_grade"] == "B"
        assert len(data["grade_explanation"]) == 2
        assert len(data["transmission_chain"]) == 3
        assert len(data["defects"]) == 1


class TestSanadFailClosed:
    """Tests proving fail-closed behavior of Sanad."""

    def test_unknown_field_rejected(self) -> None:
        """Unknown field is rejected (extra=forbid)."""
        with pytest.raises(ValidationError) as exc_info:
            Sanad(
                sanad_id="550e8400-e29b-41d4-a716-446655440000",
                tenant_id="550e8400-e29b-41d4-a716-446655440001",
                claim_id="550e8400-e29b-41d4-a716-446655440002",
                primary_evidence_id="550e8400-e29b-41d4-a716-446655440003",
                extraction_confidence=0.95,
                corroboration_status=CorroborationStatus.NONE,
                sanad_grade=SanadGrade.A,
                transmission_chain=[make_transmission_node()],
                unknown_field="should fail",  # type: ignore[call-arg]
            )

        assert "extra" in str(exc_info.value).lower() or "unknown_field" in str(exc_info.value)

    def test_invalid_sanad_grade_enum_rejected(self) -> None:
        """Invalid sanad_grade enum value is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            Sanad(
                sanad_id="550e8400-e29b-41d4-a716-446655440000",
                tenant_id="550e8400-e29b-41d4-a716-446655440001",
                claim_id="550e8400-e29b-41d4-a716-446655440002",
                primary_evidence_id="550e8400-e29b-41d4-a716-446655440003",
                extraction_confidence=0.95,
                corroboration_status=CorroborationStatus.NONE,
                sanad_grade="X",  # type: ignore[arg-type]
                transmission_chain=[make_transmission_node()],
            )

        assert "sanad_grade" in str(exc_info.value).lower()

    def test_invalid_corroboration_status_enum_rejected(self) -> None:
        """Invalid corroboration_status enum value is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            Sanad(
                sanad_id="550e8400-e29b-41d4-a716-446655440000",
                tenant_id="550e8400-e29b-41d4-a716-446655440001",
                claim_id="550e8400-e29b-41d4-a716-446655440002",
                primary_evidence_id="550e8400-e29b-41d4-a716-446655440003",
                extraction_confidence=0.95,
                corroboration_status="INVALID",  # type: ignore[arg-type]
                sanad_grade=SanadGrade.A,
                transmission_chain=[make_transmission_node()],
            )

        assert "corroboration_status" in str(exc_info.value).lower()

    def test_missing_required_field_rejected(self) -> None:
        """Missing required field is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            Sanad(
                sanad_id="550e8400-e29b-41d4-a716-446655440000",
                tenant_id="550e8400-e29b-41d4-a716-446655440001",
                # missing claim_id
                primary_evidence_id="550e8400-e29b-41d4-a716-446655440003",
                extraction_confidence=0.95,
                corroboration_status=CorroborationStatus.NONE,
                sanad_grade=SanadGrade.A,
                transmission_chain=[make_transmission_node()],
            )  # type: ignore[call-arg]

        assert "claim_id" in str(exc_info.value)

    def test_empty_sanad_id_rejected(self) -> None:
        """Empty sanad_id is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            Sanad(
                sanad_id="",
                tenant_id="550e8400-e29b-41d4-a716-446655440001",
                claim_id="550e8400-e29b-41d4-a716-446655440002",
                primary_evidence_id="550e8400-e29b-41d4-a716-446655440003",
                extraction_confidence=0.95,
                corroboration_status=CorroborationStatus.NONE,
                sanad_grade=SanadGrade.A,
                transmission_chain=[make_transmission_node()],
            )

        assert "non-empty" in str(exc_info.value).lower()

    def test_empty_transmission_chain_rejected(self) -> None:
        """Empty transmission_chain is rejected (minItems: 1)."""
        with pytest.raises(ValidationError) as exc_info:
            Sanad(
                sanad_id="550e8400-e29b-41d4-a716-446655440000",
                tenant_id="550e8400-e29b-41d4-a716-446655440001",
                claim_id="550e8400-e29b-41d4-a716-446655440002",
                primary_evidence_id="550e8400-e29b-41d4-a716-446655440003",
                extraction_confidence=0.95,
                corroboration_status=CorroborationStatus.NONE,
                sanad_grade=SanadGrade.A,
                transmission_chain=[],  # Empty - should fail
            )

        assert "at least one" in str(exc_info.value).lower()

    def test_extraction_confidence_out_of_bounds_rejected(self) -> None:
        """extraction_confidence > 1.0 is rejected."""
        with pytest.raises(ValidationError):
            Sanad(
                sanad_id="550e8400-e29b-41d4-a716-446655440000",
                tenant_id="550e8400-e29b-41d4-a716-446655440001",
                claim_id="550e8400-e29b-41d4-a716-446655440002",
                primary_evidence_id="550e8400-e29b-41d4-a716-446655440003",
                extraction_confidence=1.5,  # Out of bounds
                corroboration_status=CorroborationStatus.NONE,
                sanad_grade=SanadGrade.A,
                transmission_chain=[make_transmission_node()],
            )

    def test_dhabt_score_negative_rejected(self) -> None:
        """Negative dhabt_score is rejected."""
        with pytest.raises(ValidationError):
            Sanad(
                sanad_id="550e8400-e29b-41d4-a716-446655440000",
                tenant_id="550e8400-e29b-41d4-a716-446655440001",
                claim_id="550e8400-e29b-41d4-a716-446655440002",
                primary_evidence_id="550e8400-e29b-41d4-a716-446655440003",
                extraction_confidence=0.95,
                dhabt_score=-0.1,  # Negative
                corroboration_status=CorroborationStatus.NONE,
                sanad_grade=SanadGrade.A,
                transmission_chain=[make_transmission_node()],
            )


class TestSanadDeterminism:
    """Tests proving deterministic serialization."""

    def test_identical_instances_produce_identical_canonical_dict(self) -> None:
        """Two identical instances produce identical canonical dicts."""
        now = datetime(2026, 1, 13, 12, 0, 0, tzinfo=UTC)
        node = TransmissionNode(
            node_id="node-1",
            node_type=NodeType.EXTRACT,
            actor_type=ActorType.AGENT,
            actor_id="extractor-v1",
            timestamp=now,
        )

        sanad1 = Sanad(
            sanad_id="550e8400-e29b-41d4-a716-446655440000",
            tenant_id="550e8400-e29b-41d4-a716-446655440001",
            claim_id="550e8400-e29b-41d4-a716-446655440002",
            primary_evidence_id="550e8400-e29b-41d4-a716-446655440003",
            extraction_confidence=0.95,
            corroboration_status=CorroborationStatus.AHAD_1,
            sanad_grade=SanadGrade.B,
            transmission_chain=[node],
            created_at=now,
        )

        sanad2 = Sanad(
            sanad_id="550e8400-e29b-41d4-a716-446655440000",
            tenant_id="550e8400-e29b-41d4-a716-446655440001",
            claim_id="550e8400-e29b-41d4-a716-446655440002",
            primary_evidence_id="550e8400-e29b-41d4-a716-446655440003",
            extraction_confidence=0.95,
            corroboration_status=CorroborationStatus.AHAD_1,
            sanad_grade=SanadGrade.B,
            transmission_chain=[node],
            created_at=now,
        )

        assert sanad1.to_canonical_dict() == sanad2.to_canonical_dict()

    def test_identical_instances_produce_identical_stable_hash(self) -> None:
        """Two identical instances produce identical stable hashes."""
        now = datetime(2026, 1, 13, 12, 0, 0, tzinfo=UTC)
        node = TransmissionNode(
            node_id="node-1",
            node_type=NodeType.EXTRACT,
            actor_type=ActorType.AGENT,
            actor_id="extractor-v1",
            timestamp=now,
        )

        sanad1 = Sanad(
            sanad_id="550e8400-e29b-41d4-a716-446655440000",
            tenant_id="550e8400-e29b-41d4-a716-446655440001",
            claim_id="550e8400-e29b-41d4-a716-446655440002",
            primary_evidence_id="550e8400-e29b-41d4-a716-446655440003",
            extraction_confidence=0.95,
            corroboration_status=CorroborationStatus.NONE,
            sanad_grade=SanadGrade.A,
            transmission_chain=[node],
        )

        sanad2 = Sanad(
            sanad_id="550e8400-e29b-41d4-a716-446655440000",
            tenant_id="550e8400-e29b-41d4-a716-446655440001",
            claim_id="550e8400-e29b-41d4-a716-446655440002",
            primary_evidence_id="550e8400-e29b-41d4-a716-446655440003",
            extraction_confidence=0.95,
            corroboration_status=CorroborationStatus.NONE,
            sanad_grade=SanadGrade.A,
            transmission_chain=[node],
        )

        assert sanad1.stable_hash() == sanad2.stable_hash()
        assert len(sanad1.stable_hash()) == 64  # SHA256 hex

    def test_corroborating_evidence_ids_sorted_in_canonical_dict(self) -> None:
        """Corroborating evidence IDs are sorted for deterministic output."""
        now = datetime(2026, 1, 13, 12, 0, 0, tzinfo=UTC)
        node = TransmissionNode(
            node_id="node-1",
            node_type=NodeType.EXTRACT,
            actor_type=ActorType.AGENT,
            actor_id="extractor-v1",
            timestamp=now,
        )

        sanad1 = Sanad(
            sanad_id="550e8400-e29b-41d4-a716-446655440000",
            tenant_id="550e8400-e29b-41d4-a716-446655440001",
            claim_id="550e8400-e29b-41d4-a716-446655440002",
            primary_evidence_id="550e8400-e29b-41d4-a716-446655440003",
            corroborating_evidence_ids=["ev-b", "ev-a"],
            extraction_confidence=0.95,
            corroboration_status=CorroborationStatus.AHAD_2,
            sanad_grade=SanadGrade.A,
            transmission_chain=[node],
        )

        sanad2 = Sanad(
            sanad_id="550e8400-e29b-41d4-a716-446655440000",
            tenant_id="550e8400-e29b-41d4-a716-446655440001",
            claim_id="550e8400-e29b-41d4-a716-446655440002",
            primary_evidence_id="550e8400-e29b-41d4-a716-446655440003",
            corroborating_evidence_ids=["ev-a", "ev-b"],
            extraction_confidence=0.95,
            corroboration_status=CorroborationStatus.AHAD_2,
            sanad_grade=SanadGrade.A,
            transmission_chain=[node],
        )

        assert sanad1.stable_hash() == sanad2.stable_hash()


class TestSanadToDbDict:
    """Tests for database serialization."""

    def test_to_db_dict_produces_valid_dict(self) -> None:
        """to_db_dict produces a valid dictionary for database insertion."""
        now = datetime.now(UTC)
        node = make_transmission_node()
        defect = make_defect()

        sanad = Sanad(
            sanad_id="550e8400-e29b-41d4-a716-446655440000",
            tenant_id="550e8400-e29b-41d4-a716-446655440001",
            claim_id="550e8400-e29b-41d4-a716-446655440002",
            primary_evidence_id="550e8400-e29b-41d4-a716-446655440003",
            extraction_confidence=0.92,
            corroboration_status=CorroborationStatus.MUTAWATIR,
            sanad_grade=SanadGrade.C,
            transmission_chain=[node],
            defects=[defect],
            created_at=now,
        )

        db_dict = sanad.to_db_dict()

        assert db_dict["sanad_id"] == "550e8400-e29b-41d4-a716-446655440000"
        assert db_dict["corroboration_status"] == "MUTAWATIR"
        assert db_dict["sanad_grade"] == "C"
        assert len(db_dict["transmission_chain"]) == 1
        assert len(db_dict["defects"]) == 1
        assert db_dict["transmission_chain"][0]["node_type"] == "EXTRACT"
        assert db_dict["defects"][0]["defect_type"] == "BROKEN_CHAIN"


class TestSanadGradeComparison:
    """Tests for SanadGrade comparison operators."""

    def test_grade_ordering(self) -> None:
        """Grades are ordered A > B > C > D."""
        assert SanadGrade.A > SanadGrade.B
        assert SanadGrade.B > SanadGrade.C
        assert SanadGrade.C > SanadGrade.D

        assert SanadGrade.D < SanadGrade.C
        assert SanadGrade.C < SanadGrade.B
        assert SanadGrade.B < SanadGrade.A

    def test_grade_equality(self) -> None:
        """Same grades are equal."""
        assert SanadGrade.A == SanadGrade.A
        assert SanadGrade.B >= SanadGrade.B
        assert SanadGrade.C <= SanadGrade.C

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


class TestCorroborationStatusEnum:
    """Tests for CorroborationStatus enum."""

    def test_all_statuses_valid(self) -> None:
        """All expected corroboration statuses are valid."""
        assert CorroborationStatus.NONE.value == "NONE"
        assert CorroborationStatus.AHAD_1.value == "AHAD_1"
        assert CorroborationStatus.AHAD_2.value == "AHAD_2"
        assert CorroborationStatus.MUTAWATIR.value == "MUTAWATIR"
