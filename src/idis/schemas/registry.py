"""Schema Registry - deterministic schema discovery and completeness check.

This module provides:
1. Deterministic schema directory discovery (env var or upward search)
2. Required schema allowlist aligned to v6.3
3. Completeness check: all required schemas present and loadable
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# Required schemas aligned to v6.3 + validators
# These MUST be present for the system to function correctly
REQUIRED_SCHEMAS = frozenset(
    {
        "claim.schema.json",
        "sanad.schema.json",
        "defect.schema.json",
        "muhasabah_record.schema.json",
        "audit_event.schema.json",
        "transmission_node.schema.json",
        "calc_sanad.schema.json",
        "debate_state.schema.json",
    }
)

# Maximum upward search depth to prevent infinite loops
MAX_SEARCH_DEPTH = 10


class SchemaRegistry:
    """Schema registry for discovering and validating JSON schemas.

    Discovery order (deterministic):
    1. IDIS_SCHEMA_DIR environment variable (if set)
    2. Upward search from this file's location for a 'schemas/' directory

    Completeness check:
    - All REQUIRED_SCHEMAS must be present
    - Each schema file must be valid JSON (parseable)
    """

    def __init__(self, schema_dir: str | Path | None = None) -> None:
        """Initialize the registry.

        Args:
            schema_dir: Explicit schema directory path. If None, uses
                        env var or upward search.
        """
        if schema_dir is not None:
            self._schema_dir: Path | None = Path(schema_dir)
        else:
            self._schema_dir = self._discover_schema_dir()

    def _discover_schema_dir(self) -> Path | None:
        """Discover schema directory deterministically.

        Order:
        1. IDIS_SCHEMA_DIR env var
        2. Upward search from this file for 'schemas/' directory at repo root
        """
        # Check env var first
        env_dir = os.environ.get("IDIS_SCHEMA_DIR")
        if env_dir:
            path = Path(env_dir)
            if path.is_dir():
                return path
            return None

        # Upward search from this file's location
        # This file is at: src/idis/schemas/registry.py
        # Repo schemas are at: schemas/
        current = Path(__file__).resolve().parent
        for _ in range(MAX_SEARCH_DEPTH):
            # Check if schemas/ exists as a sibling at this level
            candidate = current / "schemas"
            if candidate.is_dir() and self._looks_like_schema_dir(candidate):
                return candidate

            parent = current.parent
            if parent == current:
                break
            current = parent

        return None

    def _looks_like_schema_dir(self, path: Path) -> bool:
        """Check if a directory looks like the schema directory.

        Returns a boolean indicating if it contains at least one .schema.json file.
        """
        if not path.is_dir():
            return False
        return any(f.is_file() and f.name.endswith(".schema.json") for f in path.iterdir())

    @property
    def schema_dir(self) -> Path | None:
        """Return the discovered schema directory."""
        return self._schema_dir

    def list_schemas(self) -> list[str]:
        """List all .schema.json files in the schema directory."""
        if self._schema_dir is None or not self._schema_dir.is_dir():
            return []

        schemas = []
        for f in self._schema_dir.iterdir():
            if f.is_file() and f.name.endswith(".schema.json"):
                schemas.append(f.name)
        return sorted(schemas)

    def load_schema(self, schema_name: str) -> tuple[dict[str, Any] | None, str | None]:
        """Load and parse a schema file.

        Args:
            schema_name: Schema filename (e.g., 'claim.schema.json')

        Returns:
            Tuple of (parsed_schema, error_message).
            If error_message is not None, parsed_schema should be ignored.
        """
        if self._schema_dir is None:
            return None, "Schema directory not found"

        schema_path = self._schema_dir / schema_name
        if not schema_path.is_file():
            return None, f"Schema file not found: {schema_name}"

        try:
            with open(schema_path, encoding="utf-8") as f:
                content = f.read()
            if not content.strip():
                return None, f"Schema file is empty: {schema_name}"
            schema = json.loads(content)
            return schema, None
        except json.JSONDecodeError as e:
            return None, f"Invalid JSON in {schema_name}: {e}"
        except OSError as e:
            return None, f"Cannot read {schema_name}: {e}"

    def check_completeness(self) -> dict[str, Any]:
        """Check schema registry completeness and loadability.

        Returns:
            Deterministic JSON report:
            {
                "pass": bool,
                "missing": [...],
                "invalid_json": [...],
                "schema_dir": "..."
            }
        """
        result: dict[str, Any] = {
            "invalid_json": [],
            "missing": [],
            "pass": True,
            "schema_dir": str(self._schema_dir) if self._schema_dir else None,
        }

        # If no schema dir found, everything is missing
        if self._schema_dir is None or not self._schema_dir.is_dir():
            result["missing"] = sorted(REQUIRED_SCHEMAS)
            result["pass"] = False
            return result

        # Check each required schema
        present_schemas = set(self.list_schemas())

        for schema_name in sorted(REQUIRED_SCHEMAS):
            if schema_name not in present_schemas:
                result["missing"].append(schema_name)
                result["pass"] = False
            else:
                # Check loadability
                _, error = self.load_schema(schema_name)
                if error is not None:
                    result["invalid_json"].append(schema_name)
                    result["pass"] = False

        # Sort lists for deterministic output
        result["missing"] = sorted(result["missing"])
        result["invalid_json"] = sorted(result["invalid_json"])

        return result
