"""Tests for TransmissionNode model - schema alignment, fail-closed, and determinism.

Phase 3.3: Tests for TransmissionNode Pydantic model.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from idis.models.transmission_node import (
    ActorType,
    NodeType,
    TransmissionNode,
    VerificationMethod,
)
from idis.validators import SchemaValidator


class TestTransmissionNodeSchemaAlignment:
    """Tests proving TransmissionNode aligns with JSON schema."""

    def test_minimal_valid_instance_passes_schema(self) -> None:
        """Minimal valid TransmissionNode passes JSON schema validation."""
        node = TransmissionNode(
            node_id="550e8400-e29b-41d4-a716-446655440000",
            node_type=NodeType.EXTRACT,
            actor_type=ActorType.AGENT,
            actor_id="extractor-v1",
            timestamp=datetime.now(UTC),
        )

        validator = SchemaValidator()
        result = validator.validate("transmission_node", node.model_dump(mode="json"))
        assert result.passed, f"Expected pass but got errors: {result.errors}"

    def test_fully_populated_instance_passes_schema(self) -> None:
        """Fully populated TransmissionNode passes JSON schema validation."""
        now = datetime.now(UTC)
        node = TransmissionNode(
            node_id="550e8400-e29b-41d4-a716-446655440000",
            node_type=NodeType.NORMALIZE,
            actor_type=ActorType.SYSTEM,
            actor_id="normalizer-v2",
            input_refs=[{"ref_type": "span", "span_id": "span-123"}],
            output_refs=[{"ref_type": "claim", "claim_id": "claim-456"}],
            timestamp=now,
            confidence=0.95,
            dhabt_score=0.88,
            verification_method=VerificationMethod.CROSS_CHECK,
            notes="Normalized from raw extraction",
        )

        validator = SchemaValidator()
        result = validator.validate("transmission_node", node.model_dump(mode="json"))
        assert result.passed, f"Expected pass but got errors: {result.errors}"


class TestTransmissionNodeFailClosed:
    """Tests proving fail-closed behavior of TransmissionNode."""

    def test_unknown_field_rejected(self) -> None:
        """Unknown field is rejected (extra=forbid)."""
        with pytest.raises(ValidationError) as exc_info:
            TransmissionNode(
                node_id="550e8400-e29b-41d4-a716-446655440000",
                node_type=NodeType.EXTRACT,
                actor_type=ActorType.AGENT,
                actor_id="extractor-v1",
                timestamp=datetime.now(UTC),
                unknown_field="should fail",  # type: ignore[call-arg]
            )

        assert "extra" in str(exc_info.value).lower() or "unknown_field" in str(exc_info.value)

    def test_invalid_node_type_enum_rejected(self) -> None:
        """Invalid node_type enum value is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            TransmissionNode(
                node_id="550e8400-e29b-41d4-a716-446655440000",
                node_type="INVALID_TYPE",  # type: ignore[arg-type]
                actor_type=ActorType.AGENT,
                actor_id="extractor-v1",
                timestamp=datetime.now(UTC),
            )

        assert "node_type" in str(exc_info.value).lower()

    def test_invalid_actor_type_enum_rejected(self) -> None:
        """Invalid actor_type enum value is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            TransmissionNode(
                node_id="550e8400-e29b-41d4-a716-446655440000",
                node_type=NodeType.EXTRACT,
                actor_type="ROBOT",  # type: ignore[arg-type]
                actor_id="extractor-v1",
                timestamp=datetime.now(UTC),
            )

        assert "actor_type" in str(exc_info.value).lower()

    def test_missing_required_field_rejected(self) -> None:
        """Missing required field is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            TransmissionNode(
                node_id="550e8400-e29b-41d4-a716-446655440000",
                node_type=NodeType.EXTRACT,
                actor_type=ActorType.AGENT,
                # missing actor_id
                timestamp=datetime.now(UTC),
            )  # type: ignore[call-arg]

        assert "actor_id" in str(exc_info.value)

    def test_empty_node_id_rejected(self) -> None:
        """Empty node_id is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            TransmissionNode(
                node_id="",
                node_type=NodeType.EXTRACT,
                actor_type=ActorType.AGENT,
                actor_id="extractor-v1",
                timestamp=datetime.now(UTC),
            )

        assert "non-empty" in str(exc_info.value).lower()

    def test_confidence_out_of_bounds_rejected(self) -> None:
        """Confidence > 1.0 is rejected."""
        with pytest.raises(ValidationError):
            TransmissionNode(
                node_id="550e8400-e29b-41d4-a716-446655440000",
                node_type=NodeType.EXTRACT,
                actor_type=ActorType.AGENT,
                actor_id="extractor-v1",
                timestamp=datetime.now(UTC),
                confidence=1.5,  # Out of bounds
            )

    def test_dhabt_score_negative_rejected(self) -> None:
        """Negative dhabt_score is rejected."""
        with pytest.raises(ValidationError):
            TransmissionNode(
                node_id="550e8400-e29b-41d4-a716-446655440000",
                node_type=NodeType.EXTRACT,
                actor_type=ActorType.AGENT,
                actor_id="extractor-v1",
                timestamp=datetime.now(UTC),
                dhabt_score=-0.1,
            )


class TestTransmissionNodeDeterminism:
    """Tests proving deterministic serialization."""

    def test_identical_instances_produce_identical_canonical_dict(self) -> None:
        """Two identical instances produce identical canonical dicts."""
        now = datetime(2026, 1, 13, 12, 0, 0, tzinfo=UTC)

        node1 = TransmissionNode(
            node_id="550e8400-e29b-41d4-a716-446655440000",
            node_type=NodeType.EXTRACT,
            actor_type=ActorType.AGENT,
            actor_id="extractor-v1",
            timestamp=now,
            confidence=0.95,
        )

        node2 = TransmissionNode(
            node_id="550e8400-e29b-41d4-a716-446655440000",
            node_type=NodeType.EXTRACT,
            actor_type=ActorType.AGENT,
            actor_id="extractor-v1",
            timestamp=now,
            confidence=0.95,
        )

        assert node1.to_canonical_dict() == node2.to_canonical_dict()

    def test_identical_instances_produce_identical_stable_hash(self) -> None:
        """Two identical instances produce identical stable hashes."""
        now = datetime(2026, 1, 13, 12, 0, 0, tzinfo=UTC)

        node1 = TransmissionNode(
            node_id="550e8400-e29b-41d4-a716-446655440000",
            node_type=NodeType.EXTRACT,
            actor_type=ActorType.AGENT,
            actor_id="extractor-v1",
            timestamp=now,
        )

        node2 = TransmissionNode(
            node_id="550e8400-e29b-41d4-a716-446655440000",
            node_type=NodeType.EXTRACT,
            actor_type=ActorType.AGENT,
            actor_id="extractor-v1",
            timestamp=now,
        )

        assert node1.stable_hash() == node2.stable_hash()
        assert len(node1.stable_hash()) == 64  # SHA256 hex

    def test_input_refs_sorted_in_canonical_dict(self) -> None:
        """Input refs are sorted for deterministic output."""
        now = datetime(2026, 1, 13, 12, 0, 0, tzinfo=UTC)

        # Create with refs in different orders
        node1 = TransmissionNode(
            node_id="550e8400-e29b-41d4-a716-446655440000",
            node_type=NodeType.EXTRACT,
            actor_type=ActorType.AGENT,
            actor_id="extractor-v1",
            timestamp=now,
            input_refs=[{"id": "b"}, {"id": "a"}],
        )

        node2 = TransmissionNode(
            node_id="550e8400-e29b-41d4-a716-446655440000",
            node_type=NodeType.EXTRACT,
            actor_type=ActorType.AGENT,
            actor_id="extractor-v1",
            timestamp=now,
            input_refs=[{"id": "a"}, {"id": "b"}],
        )

        # After canonicalization, both should produce same hash
        assert node1.stable_hash() == node2.stable_hash()


class TestTransmissionNodeToDbDict:
    """Tests for database serialization."""

    def test_to_db_dict_produces_valid_dict(self) -> None:
        """to_db_dict produces a valid dictionary for database insertion."""
        now = datetime.now(UTC)
        node = TransmissionNode(
            node_id="550e8400-e29b-41d4-a716-446655440000",
            node_type=NodeType.HUMAN_VERIFY,
            actor_type=ActorType.HUMAN,
            actor_id="user-123",
            timestamp=now,
            verification_method=VerificationMethod.HUMAN_VERIFIED,
        )

        db_dict = node.to_db_dict()

        assert db_dict["node_id"] == "550e8400-e29b-41d4-a716-446655440000"
        assert db_dict["node_type"] == "HUMAN_VERIFY"
        assert db_dict["actor_type"] == "HUMAN"
        assert db_dict["verification_method"] == "human-verified"


class TestNodeTypeEnum:
    """Tests for NodeType enum."""

    def test_all_node_types_valid(self) -> None:
        """All expected node types are valid."""
        expected = [
            "INGEST",
            "EXTRACT",
            "NORMALIZE",
            "RECONCILE",
            "CALCULATE",
            "INFER",
            "HUMAN_VERIFY",
            "EXPORT",
        ]
        actual = [nt.value for nt in NodeType]
        assert sorted(actual) == sorted(expected)


class TestActorTypeEnum:
    """Tests for ActorType enum."""

    def test_all_actor_types_valid(self) -> None:
        """All expected actor types are valid."""
        assert ActorType.AGENT.value == "AGENT"
        assert ActorType.HUMAN.value == "HUMAN"
        assert ActorType.SYSTEM.value == "SYSTEM"
