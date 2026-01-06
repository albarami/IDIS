"""Tests for SchemaValidator - proves fail-closed behavior."""

from __future__ import annotations

import tempfile
from pathlib import Path

from idis.validators import SchemaValidator


class TestSchemaValidatorFailClosed:
    """Tests proving fail-closed behavior of SchemaValidator."""

    def test_none_data_fails_closed(self) -> None:
        """Validator rejects None data (fail closed)."""
        validator = SchemaValidator()
        result = validator.validate("claim", None)

        assert not result.passed
        assert len(result.errors) == 1
        assert result.errors[0].code == "FAIL_CLOSED"
        assert "None" in result.errors[0].message

    def test_missing_schema_fails_closed(self) -> None:
        """Validator rejects when schema cannot be loaded (fail closed)."""
        validator = SchemaValidator()
        result = validator.validate("nonexistent_schema_xyz", {"foo": "bar"})

        assert not result.passed
        assert len(result.errors) == 1
        assert result.errors[0].code == "FAIL_CLOSED"
        assert "Cannot load" in result.errors[0].message

    def test_invalid_json_file_fails_closed(self) -> None:
        """Validator rejects invalid JSON file (fail closed)."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("{ invalid json }")
            temp_path = f.name

        try:
            validator = SchemaValidator()
            result = validator.validate_json_file("claim", temp_path)

            assert not result.passed
            assert result.errors[0].code == "FAIL_CLOSED"
            assert "Invalid JSON" in result.errors[0].message
        finally:
            Path(temp_path).unlink()

    def test_nonexistent_file_fails_closed(self) -> None:
        """Validator rejects nonexistent file (fail closed)."""
        validator = SchemaValidator()
        result = validator.validate_json_file("claim", "/nonexistent/path/file.json")

        assert not result.passed
        assert result.errors[0].code == "FAIL_CLOSED"
        assert "not found" in result.errors[0].message.lower()


class TestSchemaValidatorPositive:
    """Positive tests - valid data passes."""

    def test_valid_defect_passes(self) -> None:
        """Valid defect object passes schema validation."""
        validator = SchemaValidator()

        valid_defect = {
            "defect_id": "550e8400-e29b-41d4-a716-446655440000",
            "defect_type": "BROKEN_CHAIN",
            "severity": "FATAL",
            "description": "Chain is broken at extraction step",
            "cure_protocol": "RECONSTRUCT_CHAIN",
            "status": "OPEN",
            "affected_claim_ids": ["550e8400-e29b-41d4-a716-446655440001"],
            "timestamp": "2026-01-06T12:00:00Z",
        }

        result = validator.validate("defect", valid_defect)
        assert result.passed, f"Expected pass but got errors: {result.errors}"

    def test_valid_transmission_node_passes(self) -> None:
        """Valid transmission node passes schema validation."""
        validator = SchemaValidator()

        valid_node = {
            "node_id": "550e8400-e29b-41d4-a716-446655440000",
            "node_type": "EXTRACT",
            "actor_type": "AGENT",
            "actor_id": "extractor-v1",
            "timestamp": "2026-01-06T12:00:00Z",
            "confidence": 0.95,
        }

        result = validator.validate("transmission_node", valid_node)
        assert result.passed, f"Expected pass but got errors: {result.errors}"


class TestSchemaValidatorNegative:
    """Negative tests - invalid data fails."""

    def test_defect_missing_required_field_fails(self) -> None:
        """Defect missing required field fails validation."""
        validator = SchemaValidator()

        # Missing severity
        invalid_defect = {
            "defect_id": "550e8400-e29b-41d4-a716-446655440000",
            "defect_type": "BROKEN_CHAIN",
            "description": "Chain is broken",
            "cure_protocol": "RECONSTRUCT_CHAIN",
            "status": "OPEN",
            "affected_claim_ids": [],
            "timestamp": "2026-01-06T12:00:00Z",
        }

        result = validator.validate("defect", invalid_defect)
        assert not result.passed
        assert any(e.code == "required" for e in result.errors)

    def test_defect_invalid_enum_fails(self) -> None:
        """Defect with invalid enum value fails validation."""
        validator = SchemaValidator()

        invalid_defect = {
            "defect_id": "550e8400-e29b-41d4-a716-446655440000",
            "defect_type": "INVALID_TYPE",  # Invalid enum
            "severity": "FATAL",
            "description": "Test",
            "cure_protocol": "RECONSTRUCT_CHAIN",
            "status": "OPEN",
            "affected_claim_ids": [],
            "timestamp": "2026-01-06T12:00:00Z",
        }

        result = validator.validate("defect", invalid_defect)
        assert not result.passed
        assert any(e.code == "enum" for e in result.errors)

    def test_claim_invalid_grade_fails(self) -> None:
        """Claim with invalid grade fails validation."""
        validator = SchemaValidator()

        invalid_claim = {
            "claim_id": "550e8400-e29b-41d4-a716-446655440000",
            "tenant_id": "550e8400-e29b-41d4-a716-446655440001",
            "deal_id": "550e8400-e29b-41d4-a716-446655440002",
            "claim_class": "FINANCIAL",
            "claim_text": "ARR is $10M",
            "claim_grade": "X",  # Invalid - must be A/B/C/D
            "claim_verdict": "VERIFIED",
            "claim_action": "NONE",
            "created_at": "2026-01-06T12:00:00Z",
        }

        result = validator.validate("claim", invalid_claim)
        assert not result.passed
        assert any("claim_grade" in e.path for e in result.errors)


class TestSchemaValidatorListSchemas:
    """Test schema discovery."""

    def test_list_available_schemas(self) -> None:
        """Can list available schemas."""
        validator = SchemaValidator()
        schemas = validator.list_available_schemas()

        assert len(schemas) >= 9
        assert "claim" in schemas
        assert "sanad" in schemas
        assert "defect" in schemas
        assert "muhasabah_record" in schemas
        assert "audit_event" in schemas
