"""JSON Schema validator with fail-closed behavior.

This module loads JSON schemas and validates JSON data against them.
All validation FAILS CLOSED - any error or uncertainty results in rejection.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema
from jsonschema import Draft202012Validator, RefResolver


@dataclass(frozen=True)
class ValidationError:
    """A single validation error."""

    code: str
    message: str
    path: str


@dataclass
class ValidationResult:
    """Result of validation - fail-closed by default."""

    passed: bool
    errors: list[ValidationError] = field(default_factory=list)
    warnings: list[ValidationError] = field(default_factory=list)

    @classmethod
    def fail(cls, errors: list[ValidationError]) -> ValidationResult:
        """Create a failed result."""
        return cls(passed=False, errors=errors)

    @classmethod
    def success(cls, warnings: list[ValidationError] | None = None) -> ValidationResult:
        """Create a successful result."""
        return cls(passed=True, warnings=warnings or [])

    @classmethod
    def fail_closed(cls, reason: str) -> ValidationResult:
        """Fail closed with a single error - used when validation cannot proceed."""
        return cls(
            passed=False,
            errors=[ValidationError(code="FAIL_CLOSED", message=reason, path="$")],
        )


class SchemaValidator:
    """Validates JSON data against JSON schemas with fail-closed behavior.

    All validation is strict:
    - Unknown properties are rejected (additionalProperties: false in schemas)
    - Missing required fields cause failure
    - Type mismatches cause failure
    - Any schema loading error causes validation to fail closed
    """

    def __init__(self, schema_dir: Path | str | None = None) -> None:
        """Initialize validator with schema directory.

        Args:
            schema_dir: Directory containing JSON schema files.
                        Defaults to project schemas/ directory.
        """
        if schema_dir is None:
            # Default to project root schemas/
            self._schema_dir = Path(__file__).parent.parent.parent.parent / "schemas"
        else:
            self._schema_dir = Path(schema_dir)

        self._schemas: dict[str, dict[str, Any]] = {}
        self._validators: dict[str, Draft202012Validator] = {}

    def _load_schema(self, schema_name: str) -> dict[str, Any] | None:
        """Load a schema by name. Returns None on any error (fail closed)."""
        if schema_name in self._schemas:
            return self._schemas[schema_name]

        schema_file = self._schema_dir / f"{schema_name}.schema.json"

        try:
            if not schema_file.exists():
                return None

            with schema_file.open("r", encoding="utf-8") as f:
                schema: dict[str, Any] = json.load(f)

            self._schemas[schema_name] = schema
            return schema
        except (json.JSONDecodeError, OSError, PermissionError):
            # Fail closed on any file/parse error
            return None

    def _get_validator(self, schema_name: str) -> Draft202012Validator | None:
        """Get a validator for a schema. Returns None on error (fail closed)."""
        if schema_name in self._validators:
            return self._validators[schema_name]

        schema = self._load_schema(schema_name)
        if schema is None:
            return None

        try:
            # Create resolver for $ref handling within schema directory
            schema_uri = f"file:///{self._schema_dir.as_posix()}/"
            resolver = RefResolver(schema_uri, schema, store={})

            # Check schema is valid
            Draft202012Validator.check_schema(schema)

            validator = Draft202012Validator(schema, resolver=resolver)
            self._validators[schema_name] = validator
            return validator
        except jsonschema.SchemaError:
            # Invalid schema - fail closed
            return None

    def validate(self, schema_name: str, data: Any) -> ValidationResult:
        """Validate data against a named schema.

        Args:
            schema_name: Name of schema (without .schema.json extension)
            data: JSON data to validate

        Returns:
            ValidationResult with pass/fail and any errors.
            FAILS CLOSED on any error loading schema or validating.
        """
        # Fail closed if data is None
        if data is None:
            return ValidationResult.fail_closed("Data is None - cannot validate")

        # Fail closed if schema cannot be loaded
        validator = self._get_validator(schema_name)
        if validator is None:
            return ValidationResult.fail_closed(
                f"Cannot load or parse schema '{schema_name}' - validation fails closed"
            )

        # Collect all validation errors
        errors: list[ValidationError] = []
        try:
            for error in validator.iter_errors(data):
                path = "$" + "".join(
                    f".{p}" if isinstance(p, str) else f"[{p}]" for p in error.absolute_path
                )
                errors.append(
                    ValidationError(
                        code=error.validator,
                        message=error.message,
                        path=path,
                    )
                )
        except Exception as e:
            # Any unexpected error during validation - fail closed
            return ValidationResult.fail_closed(f"Unexpected validation error: {e}")

        if errors:
            return ValidationResult.fail(errors)

        return ValidationResult.success()

    def validate_json_file(self, schema_name: str, json_path: Path | str) -> ValidationResult:
        """Validate a JSON file against a schema.

        Args:
            schema_name: Name of schema (without .schema.json extension)
            json_path: Path to JSON file to validate

        Returns:
            ValidationResult - FAILS CLOSED on any file/parse error.
        """
        json_path = Path(json_path)

        try:
            if not json_path.exists():
                return ValidationResult.fail_closed(f"File not found: {json_path}")

            with json_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            return ValidationResult.fail_closed(f"Invalid JSON: {e}")
        except (OSError, PermissionError) as e:
            return ValidationResult.fail_closed(f"Cannot read file: {e}")

        return self.validate(schema_name, data)

    def list_available_schemas(self) -> list[str]:
        """List all available schema names in the schema directory."""
        if not self._schema_dir.exists():
            return []

        return [p.stem.replace(".schema", "") for p in self._schema_dir.glob("*.schema.json")]
