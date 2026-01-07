"""Tests for the Schema Registry.

Tests cover:
1. PASS: SchemaRegistry finds schema dir and check returns pass=True
2. FAIL: temp schema dir missing required file
3. FAIL: schema file containing invalid JSON
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from idis.schemas.registry import REQUIRED_SCHEMAS, SchemaRegistry


class TestSchemaRegistryDiscovery:
    """Test schema directory discovery."""

    def test_discovers_repo_schema_dir(self) -> None:
        """SchemaRegistry discovers the repo's schemas/ directory."""
        registry = SchemaRegistry()

        assert registry.schema_dir is not None
        assert registry.schema_dir.is_dir()
        assert registry.schema_dir.name == "schemas"

    def test_respects_env_var(self, tmp_path: Path) -> None:
        """SchemaRegistry respects IDIS_SCHEMA_DIR env var."""
        schema_dir = tmp_path / "custom_schemas"
        schema_dir.mkdir()

        old_env = os.environ.get("IDIS_SCHEMA_DIR")
        try:
            os.environ["IDIS_SCHEMA_DIR"] = str(schema_dir)
            registry = SchemaRegistry()

            assert registry.schema_dir == schema_dir
        finally:
            if old_env is None:
                os.environ.pop("IDIS_SCHEMA_DIR", None)
            else:
                os.environ["IDIS_SCHEMA_DIR"] = old_env

    def test_explicit_schema_dir(self, tmp_path: Path) -> None:
        """SchemaRegistry accepts explicit schema_dir parameter."""
        schema_dir = tmp_path / "explicit_schemas"
        schema_dir.mkdir()

        registry = SchemaRegistry(schema_dir=schema_dir)
        assert registry.schema_dir == schema_dir


class TestSchemaRegistryCompleteness:
    """Test schema completeness checking."""

    def test_check_completeness_passes_in_repo(self) -> None:
        """PASS: check_completeness returns pass=True with real repo schemas."""
        registry = SchemaRegistry()
        result = registry.check_completeness()

        assert result["pass"] is True
        assert result["missing"] == []
        assert result["invalid_json"] == []
        assert result["schema_dir"] is not None

    def test_check_completeness_fails_missing_schema(self, tmp_path: Path) -> None:
        """FAIL: missing required schema file causes pass=False."""
        schema_dir = tmp_path / "incomplete_schemas"
        schema_dir.mkdir()

        # Create only some schemas, not all required ones
        partial_schemas = ["claim.schema.json", "sanad.schema.json"]
        for schema_name in partial_schemas:
            schema_path = schema_dir / schema_name
            schema_path.write_text('{"type": "object"}', encoding="utf-8")

        old_env = os.environ.get("IDIS_SCHEMA_DIR")
        try:
            os.environ["IDIS_SCHEMA_DIR"] = str(schema_dir)
            registry = SchemaRegistry()
            result = registry.check_completeness()

            assert result["pass"] is False
            assert len(result["missing"]) > 0

            # Verify specific missing schemas
            missing_set = set(result["missing"])
            expected_missing = REQUIRED_SCHEMAS - set(partial_schemas)
            assert missing_set == expected_missing
        finally:
            if old_env is None:
                os.environ.pop("IDIS_SCHEMA_DIR", None)
            else:
                os.environ["IDIS_SCHEMA_DIR"] = old_env

    def test_check_completeness_fails_invalid_json(self, tmp_path: Path) -> None:
        """FAIL: schema file with invalid JSON causes pass=False."""
        schema_dir = tmp_path / "invalid_schemas"
        schema_dir.mkdir()

        # Create all required schemas
        for schema_name in REQUIRED_SCHEMAS:
            schema_path = schema_dir / schema_name
            schema_path.write_text('{"type": "object"}', encoding="utf-8")

        # Corrupt one schema with invalid JSON
        corrupt_schema = schema_dir / "claim.schema.json"
        corrupt_schema.write_text("{ this is not valid json }", encoding="utf-8")

        old_env = os.environ.get("IDIS_SCHEMA_DIR")
        try:
            os.environ["IDIS_SCHEMA_DIR"] = str(schema_dir)
            registry = SchemaRegistry()
            result = registry.check_completeness()

            assert result["pass"] is False
            assert "claim.schema.json" in result["invalid_json"]
        finally:
            if old_env is None:
                os.environ.pop("IDIS_SCHEMA_DIR", None)
            else:
                os.environ["IDIS_SCHEMA_DIR"] = old_env

    def test_check_completeness_no_schema_dir(self, tmp_path: Path) -> None:
        """FAIL: non-existent schema dir causes all schemas to be missing."""
        old_env = os.environ.get("IDIS_SCHEMA_DIR")
        try:
            os.environ["IDIS_SCHEMA_DIR"] = str(tmp_path / "nonexistent")
            registry = SchemaRegistry()
            result = registry.check_completeness()

            assert result["pass"] is False
            assert set(result["missing"]) == REQUIRED_SCHEMAS
        finally:
            if old_env is None:
                os.environ.pop("IDIS_SCHEMA_DIR", None)
            else:
                os.environ["IDIS_SCHEMA_DIR"] = old_env


class TestSchemaRegistryListSchemas:
    """Test schema listing functionality."""

    def test_list_schemas_returns_sorted(self) -> None:
        """list_schemas returns sorted list of schema files."""
        registry = SchemaRegistry()
        schemas = registry.list_schemas()

        assert len(schemas) > 0
        assert schemas == sorted(schemas)
        assert all(s.endswith(".schema.json") for s in schemas)

    def test_list_schemas_empty_dir(self, tmp_path: Path) -> None:
        """list_schemas returns empty list for empty directory."""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        registry = SchemaRegistry(schema_dir=empty_dir)
        schemas = registry.list_schemas()

        assert schemas == []


class TestSchemaRegistryLoadSchema:
    """Test schema loading functionality."""

    def test_load_schema_success(self) -> None:
        """load_schema successfully loads and parses a schema."""
        registry = SchemaRegistry()
        schema, error = registry.load_schema("claim.schema.json")

        assert error is None
        assert schema is not None
        assert isinstance(schema, dict)

    def test_load_schema_not_found(self) -> None:
        """load_schema returns error for missing schema."""
        registry = SchemaRegistry()
        schema, error = registry.load_schema("nonexistent.schema.json")

        assert schema is None
        assert error is not None
        assert "not found" in error.lower()

    def test_load_schema_invalid_json(self, tmp_path: Path) -> None:
        """load_schema returns error for invalid JSON."""
        schema_dir = tmp_path / "schemas"
        schema_dir.mkdir()
        bad_schema = schema_dir / "bad.schema.json"
        bad_schema.write_text("not json", encoding="utf-8")

        registry = SchemaRegistry(schema_dir=schema_dir)
        schema, error = registry.load_schema("bad.schema.json")

        assert schema is None
        assert error is not None
        assert "Invalid JSON" in error


class TestSchemaCheckCli:
    """Test the schemas check CLI command."""

    def test_schemas_check_via_cli(self, capsys: pytest.CaptureFixture[str]) -> None:
        """schemas check command outputs valid JSON with pass=True."""
        from idis.cli import main

        exit_code = main(["schemas", "check"])
        captured = capsys.readouterr()

        output = json.loads(captured.out)

        assert exit_code == 0
        assert output["pass"] is True
        assert output["missing"] == []
        assert output["invalid_json"] == []

    def test_schemas_check_deterministic_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        """schemas check output has sorted keys for deterministic diffs."""
        from idis.cli import main

        main(["schemas", "check"])
        captured = capsys.readouterr()

        # Parse and re-serialize with sort_keys to verify
        output = json.loads(captured.out)
        expected_serialization = json.dumps(output, sort_keys=True, indent=2)

        assert captured.out.strip() == expected_serialization.strip()
