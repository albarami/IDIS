"""Audit Event Validator - enforces required fields and redaction policy.

Audit events are append-only and must be emitted for every mutating operation.
This validator ensures completeness and compliance with redaction rules.

Fail-closed behavior: any missing/invalid required field => pass=False with explicit error codes.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from idis.validators.schema_validator import SchemaValidator, ValidationError, ValidationResult

# UUID regex pattern (RFC 4122 compliant)
_UUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

# Event type pattern: dotted lower-case (e.g., "deal.created", "claim.verdict.changed")
_EVENT_TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")

# Valid event type prefixes from taxonomy
VALID_EVENT_PREFIXES = {
    "deal.",
    "document.",
    "claim.",
    "sanad.",
    "defect.",
    "calc.",
    "debate.",
    "muhasabah.",
    "human_gate.",
    "override.",
    "deliverable.",
    "integration.",
    "webhook.",
    "auth.",
    "rbac.",
    "tenant.",
    "break_glass.",
    "data.",
}

VALID_SEVERITIES = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}

VALID_RESOURCE_TYPES = {
    "deal",
    "document",
    "claim",
    "sanad",
    "defect",
    "calc",
    "debate",
    "deliverable",
    "human_gate",
    "override",
    "integration",
    "webhook",
}

VALID_ACTOR_TYPES = {"HUMAN", "SERVICE"}

VALID_HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}

# Fields that should NEVER appear in audit payload (PII, sensitive data)
REDACTION_BLOCKLIST = {
    "password",
    "secret",
    "api_key",
    "apikey",
    "token",
    "access_token",
    "refresh_token",
    "ssn",
    "social_security",
    "credit_card",
    "bank_account",
    "private_key",
}


class AuditEventValidator:
    """Validates audit events for completeness and redaction compliance.

    Rules:
    1. Schema validation against schemas/audit_event.schema.json
    2. All required fields must be present
    3. UUID fields must be valid UUID format
    4. Timestamp fields must be valid ISO-8601
    5. Event type must match taxonomy (dotted lower-case)
    6. Severity must be valid
    7. Payload must not contain sensitive fields (redaction policy)
    8. Actor and request context must be complete

    FAIL CLOSED: any missing/invalid required field => pass=False with explicit error codes.
    """

    def __init__(self) -> None:
        """Initialize the validator."""
        self._schema_validator = SchemaValidator()
        self._schema_loaded: bool | None = None

    def _check_redaction(self, obj: Any, path: str = "$.payload") -> list[ValidationError]:
        """Check for sensitive fields that should be redacted."""
        errors: list[ValidationError] = []

        if isinstance(obj, dict):
            for key, value in obj.items():
                # Guard against non-str keys (fail closed)
                if not isinstance(key, str):
                    errors.append(
                        ValidationError(
                            code="AUDIT_INVALID_PAYLOAD_KEY",
                            message=f"Payload key must be a string, got: {type(key).__name__}",
                            path=path,
                        )
                    )
                    continue

                key_lower = key.lower()

                # Check if key matches blocklist
                if key_lower in REDACTION_BLOCKLIST:
                    errors.append(
                        ValidationError(
                            code="REDACTION_VIOLATION",
                            message=(
                                f"Sensitive field '{key}' must not appear in audit payload. "
                                f"Store as hash or reference instead."
                            ),
                            path=f"{path}.{key}",
                        )
                    )

                # Check for partial matches
                for blocked in REDACTION_BLOCKLIST:
                    if blocked in key_lower and key_lower != blocked:
                        errors.append(
                            ValidationError(
                                code="REDACTION_WARNING",
                                message=(
                                    f"Field '{key}' may contain sensitive data "
                                    f"(matches pattern '{blocked}'). Review for compliance."
                                ),
                                path=f"{path}.{key}",
                            )
                        )
                        break

                # Recurse
                errors.extend(self._check_redaction(value, f"{path}.{key}"))

        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                errors.extend(self._check_redaction(item, f"{path}[{i}]"))

        return errors

    def _is_valid_uuid(self, value: Any) -> bool:
        """Check if value is a valid UUID string."""
        if not isinstance(value, str):
            return False
        return bool(_UUID_PATTERN.match(value))

    def _is_valid_iso8601(self, value: Any) -> bool:
        """Check if value is a valid ISO-8601 timestamp string."""
        if not isinstance(value, str):
            return False
        is_valid = False
        try:
            # Try parsing with timezone
            if value.endswith("Z"):
                datetime.fromisoformat(value.replace("Z", "+00:00"))
            else:
                datetime.fromisoformat(value)
            is_valid = True
        except ValueError:
            pass
        return is_valid

    def _is_valid_event_type(self, value: Any) -> bool:
        """Check if value is a valid event type (dotted lower-case pattern)."""
        if not isinstance(value, str):
            return False
        return bool(_EVENT_TYPE_PATTERN.match(value))

    def validate(self, data: Any) -> ValidationResult:
        """Validate an audit event.

        Args:
            data: AuditEvent JSON data

        Returns:
            ValidationResult - FAILS CLOSED on missing required fields.
            Never raises exceptions for malformed input.
        """
        # Fail closed on non-dict input (including None)
        if data is None:
            return ValidationResult.fail(
                [
                    ValidationError(
                        code="FAIL_CLOSED",
                        message="Data is None - cannot validate",
                        path="$",
                    )
                ]
            )

        if not isinstance(data, dict):
            return ValidationResult.fail(
                [
                    ValidationError(
                        code="FAIL_CLOSED",
                        message="Data must be a dictionary",
                        path="$",
                    )
                ]
            )

        errors: list[ValidationError] = []
        warnings: list[ValidationError] = []

        # === Schema validation (fail closed if schema cannot be loaded) ===
        schema_result = self._schema_validator.validate("audit_event", data)
        if not schema_result.passed:
            # Check if it's a schema load failure
            for err in schema_result.errors:
                if err.code == "FAIL_CLOSED" and "Cannot load" in err.message:
                    return ValidationResult.fail(
                        [
                            ValidationError(
                                code="AUDIT_SCHEMA_LOAD_FAILED",
                                message="Cannot load audit_event schema - validation fails closed",
                                path="$",
                            )
                        ]
                    )
            # Schema violations - collect and continue with additional checks
            for err in schema_result.errors:
                errors.append(
                    ValidationError(
                        code="AUDIT_SCHEMA_VIOLATION",
                        message=err.message,
                        path=err.path,
                    )
                )

        # === Required top-level fields with specific validation ===

        # event_id - must be UUID
        event_id = data.get("event_id")
        if not event_id:
            errors.append(
                ValidationError(
                    code="MISSING_EVENT_ID",
                    message="event_id is required",
                    path="$.event_id",
                )
            )
        elif not self._is_valid_uuid(event_id):
            errors.append(
                ValidationError(
                    code="AUDIT_INVALID_UUID",
                    message=f"event_id must be a valid UUID, got: {event_id!r}",
                    path="$.event_id",
                )
            )

        # occurred_at - must be ISO-8601
        occurred_at = data.get("occurred_at")
        if not occurred_at:
            errors.append(
                ValidationError(
                    code="MISSING_OCCURRED_AT",
                    message="occurred_at is required (server-side timestamp)",
                    path="$.occurred_at",
                )
            )
        elif not self._is_valid_iso8601(occurred_at):
            errors.append(
                ValidationError(
                    code="AUDIT_INVALID_TIMESTAMP",
                    message=f"occurred_at must be a valid ISO-8601 timestamp, got: {occurred_at!r}",
                    path="$.occurred_at",
                )
            )

        # tenant_id - must be UUID
        tenant_id = data.get("tenant_id")
        if not tenant_id:
            errors.append(
                ValidationError(
                    code="MISSING_TENANT_ID",
                    message="tenant_id is required for tenant isolation",
                    path="$.tenant_id",
                )
            )
        elif not self._is_valid_uuid(tenant_id):
            errors.append(
                ValidationError(
                    code="AUDIT_INVALID_UUID",
                    message=f"tenant_id must be a valid UUID, got: {tenant_id!r}",
                    path="$.tenant_id",
                )
            )

        event_type = data.get("event_type")
        if not event_type:
            errors.append(
                ValidationError(
                    code="MISSING_EVENT_TYPE",
                    message="event_type is required",
                    path="$.event_type",
                )
            )
        else:
            # Validate event type format (dotted lower-case)
            if not self._is_valid_event_type(event_type):
                errors.append(
                    ValidationError(
                        code="INVALID_EVENT_TYPE",
                        message=(
                            f"Event type '{event_type}' must be dotted lower-case format "
                            f"(e.g., 'deal.created', 'claim.verdict.changed')"
                        ),
                        path="$.event_type",
                    )
                )
            else:
                # Validate event type matches taxonomy prefix
                valid_prefix = any(event_type.startswith(prefix) for prefix in VALID_EVENT_PREFIXES)
                if not valid_prefix:
                    errors.append(
                        ValidationError(
                            code="INVALID_EVENT_TYPE",
                            message=(
                                f"Event type '{event_type}' does not match known taxonomy. "
                                f"Must start with one of: {sorted(VALID_EVENT_PREFIXES)}"
                            ),
                            path="$.event_type",
                        )
                    )

        severity = data.get("severity")
        if severity is None:
            errors.append(
                ValidationError(
                    code="MISSING_SEVERITY",
                    message="severity is required",
                    path="$.severity",
                )
            )
        elif not isinstance(severity, str) or not severity:
            # Guard against unhashable types (list, dict, etc.)
            errors.append(
                ValidationError(
                    code="INVALID_SEVERITY",
                    message=f"severity must be a string, got: {type(severity).__name__}",
                    path="$.severity",
                )
            )
        elif severity not in VALID_SEVERITIES:
            errors.append(
                ValidationError(
                    code="INVALID_SEVERITY",
                    message=f"Invalid severity: {severity}. Must be one of: {VALID_SEVERITIES}",
                    path="$.severity",
                )
            )

        if not data.get("summary"):
            errors.append(
                ValidationError(
                    code="MISSING_SUMMARY",
                    message="summary is required (human-readable description)",
                    path="$.summary",
                )
            )

        # === Actor validation ===

        actor = data.get("actor")
        if not actor:
            errors.append(
                ValidationError(
                    code="AUDIT_INVALID_ACTOR",
                    message="actor is required",
                    path="$.actor",
                )
            )
        elif not isinstance(actor, dict):
            errors.append(
                ValidationError(
                    code="AUDIT_INVALID_ACTOR",
                    message="actor must be an object",
                    path="$.actor",
                )
            )
        else:
            actor_type = actor.get("actor_type")
            if not actor_type:
                errors.append(
                    ValidationError(
                        code="AUDIT_INVALID_ACTOR",
                        message="actor.actor_type is required",
                        path="$.actor.actor_type",
                    )
                )
            elif not isinstance(actor_type, str):
                # Guard against unhashable types (list, dict, etc.)
                errors.append(
                    ValidationError(
                        code="AUDIT_INVALID_ACTOR",
                        message=f"actor_type must be a string, got: {type(actor_type).__name__}",
                        path="$.actor.actor_type",
                    )
                )
            elif actor_type not in VALID_ACTOR_TYPES:
                errors.append(
                    ValidationError(
                        code="AUDIT_INVALID_ACTOR",
                        message=f"Invalid actor_type: {actor_type}. Must be HUMAN or SERVICE",
                        path="$.actor.actor_type",
                    )
                )

            if not actor.get("actor_id"):
                errors.append(
                    ValidationError(
                        code="AUDIT_INVALID_ACTOR",
                        message="actor.actor_id is required and must be non-empty",
                        path="$.actor.actor_id",
                    )
                )

        # === Request validation ===

        request = data.get("request")
        if not request:
            errors.append(
                ValidationError(
                    code="AUDIT_INVALID_REQUEST",
                    message="request context is required",
                    path="$.request",
                )
            )
        elif not isinstance(request, dict):
            errors.append(
                ValidationError(
                    code="AUDIT_INVALID_REQUEST",
                    message="request must be an object",
                    path="$.request",
                )
            )
        else:
            if not request.get("request_id"):
                errors.append(
                    ValidationError(
                        code="AUDIT_INVALID_REQUEST",
                        message="request.request_id is required for correlation",
                        path="$.request.request_id",
                    )
                )

            method = request.get("method")
            if method is not None:
                if not isinstance(method, str):
                    # Guard against unhashable types (list, dict, etc.)
                    errors.append(
                        ValidationError(
                            code="AUDIT_INVALID_REQUEST",
                            message=f"method must be a string, got: {type(method).__name__}",
                            path="$.request.method",
                        )
                    )
                elif method not in VALID_HTTP_METHODS:
                    errors.append(
                        ValidationError(
                            code="AUDIT_INVALID_REQUEST",
                            message=(
                                f"Invalid method: {method}. Must be one of: {VALID_HTTP_METHODS}"
                            ),
                            path="$.request.method",
                        )
                    )

        # === Resource validation ===

        resource = data.get("resource")
        if not resource:
            errors.append(
                ValidationError(
                    code="MISSING_RESOURCE",
                    message="resource is required",
                    path="$.resource",
                )
            )
        elif not isinstance(resource, dict):
            errors.append(
                ValidationError(
                    code="INVALID_RESOURCE",
                    message="resource must be an object",
                    path="$.resource",
                )
            )
        else:
            resource_type = resource.get("resource_type")
            if resource_type is None:
                errors.append(
                    ValidationError(
                        code="MISSING_RESOURCE_TYPE",
                        message="resource.resource_type is required",
                        path="$.resource.resource_type",
                    )
                )
            elif not isinstance(resource_type, str) or not resource_type:
                # Guard against unhashable types (list, dict, etc.)
                errors.append(
                    ValidationError(
                        code="INVALID_RESOURCE_TYPE",
                        message=(
                            f"resource_type must be a string, got: {type(resource_type).__name__}"
                        ),
                        path="$.resource.resource_type",
                    )
                )
            elif resource_type not in VALID_RESOURCE_TYPES:
                errors.append(
                    ValidationError(
                        code="INVALID_RESOURCE_TYPE",
                        message=(
                            f"Invalid resource_type: {resource_type}. "
                            f"Must be one of: {VALID_RESOURCE_TYPES}"
                        ),
                        path="$.resource.resource_type",
                    )
                )

            if not resource.get("resource_id"):
                errors.append(
                    ValidationError(
                        code="MISSING_RESOURCE_ID",
                        message="resource.resource_id is required",
                        path="$.resource.resource_id",
                    )
                )

        # === Redaction check on payload ===

        payload = data.get("payload")
        if payload:
            redaction_errors = self._check_redaction(payload)
            # Treat redaction violations and invalid keys as errors (fail closed)
            for err in redaction_errors:
                if err.code in ("REDACTION_VIOLATION", "AUDIT_INVALID_PAYLOAD_KEY"):
                    errors.append(err)
                else:
                    warnings.append(err)

        if errors:
            return ValidationResult.fail(errors)

        return ValidationResult.success(warnings if warnings else None)


# === Public function API ===


def validate_audit_event(event: dict) -> ValidationResult:
    """Validate an audit event dict against the v6.3 audit logging contract.

    This is the public entry point for audit event validation.

    Args:
        event: AuditEvent dict to validate

    Returns:
        ValidationResult - FAILS CLOSED on missing/invalid required fields.
        Never raises exceptions for malformed input.

    Error codes returned on failure:
        - FAIL_CLOSED: Non-dict input (including None)
        - AUDIT_SCHEMA_LOAD_FAILED: Cannot load audit_event schema
        - AUDIT_SCHEMA_VIOLATION: Schema validation failed
        - AUDIT_INVALID_UUID: Invalid UUID format for event_id/tenant_id
        - AUDIT_INVALID_TIMESTAMP: Invalid ISO-8601 timestamp
        - INVALID_EVENT_TYPE: Invalid event type format or taxonomy
        - AUDIT_INVALID_ACTOR: Missing/invalid actor fields
        - AUDIT_INVALID_REQUEST: Missing/invalid request fields
        - REDACTION_VIOLATION: Sensitive data in payload
    """
    validator = AuditEventValidator()
    return validator.validate(event)
