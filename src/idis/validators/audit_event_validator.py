"""Audit Event Validator - enforces required fields and redaction policy.

Audit events are append-only and must be emitted for every mutating operation.
This validator ensures completeness and compliance with redaction rules.
"""

from __future__ import annotations

from typing import Any

from idis.validators.schema_validator import ValidationError, ValidationResult

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
    1. All required fields must be present
    2. Event type must match taxonomy
    3. Severity must be valid
    4. Payload must not contain sensitive fields (redaction policy)
    5. Actor and request context must be complete
    """

    def __init__(self) -> None:
        """Initialize the validator."""
        pass

    def _check_redaction(self, obj: Any, path: str = "$.payload") -> list[ValidationError]:
        """Check for sensitive fields that should be redacted."""
        errors: list[ValidationError] = []

        if isinstance(obj, dict):
            for key, value in obj.items():
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

    def validate(self, data: Any) -> ValidationResult:
        """Validate an audit event.

        Args:
            data: AuditEvent JSON data

        Returns:
            ValidationResult - FAILS CLOSED on missing required fields
        """
        if data is None:
            return ValidationResult.fail_closed("Data is None - cannot validate")

        if not isinstance(data, dict):
            return ValidationResult.fail_closed("Data must be a dictionary")

        errors: list[ValidationError] = []
        warnings: list[ValidationError] = []

        # === Required top-level fields ===

        if not data.get("event_id"):
            errors.append(
                ValidationError(
                    code="MISSING_EVENT_ID",
                    message="event_id is required",
                    path="$.event_id",
                )
            )

        if not data.get("occurred_at"):
            errors.append(
                ValidationError(
                    code="MISSING_OCCURRED_AT",
                    message="occurred_at is required (server-side timestamp)",
                    path="$.occurred_at",
                )
            )

        if not data.get("tenant_id"):
            errors.append(
                ValidationError(
                    code="MISSING_TENANT_ID",
                    message="tenant_id is required for tenant isolation",
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
            # Validate event type matches taxonomy
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
        if not severity:
            errors.append(
                ValidationError(
                    code="MISSING_SEVERITY",
                    message="severity is required",
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
                    code="MISSING_ACTOR",
                    message="actor is required",
                    path="$.actor",
                )
            )
        elif not isinstance(actor, dict):
            errors.append(
                ValidationError(
                    code="INVALID_ACTOR",
                    message="actor must be an object",
                    path="$.actor",
                )
            )
        else:
            actor_type = actor.get("actor_type")
            if not actor_type:
                errors.append(
                    ValidationError(
                        code="MISSING_ACTOR_TYPE",
                        message="actor.actor_type is required",
                        path="$.actor.actor_type",
                    )
                )
            elif actor_type not in VALID_ACTOR_TYPES:
                errors.append(
                    ValidationError(
                        code="INVALID_ACTOR_TYPE",
                        message=f"Invalid actor_type: {actor_type}. Must be HUMAN or SERVICE",
                        path="$.actor.actor_type",
                    )
                )

            if not actor.get("actor_id"):
                errors.append(
                    ValidationError(
                        code="MISSING_ACTOR_ID",
                        message="actor.actor_id is required",
                        path="$.actor.actor_id",
                    )
                )

        # === Request validation ===

        request = data.get("request")
        if not request:
            errors.append(
                ValidationError(
                    code="MISSING_REQUEST",
                    message="request context is required",
                    path="$.request",
                )
            )
        elif not isinstance(request, dict):
            errors.append(
                ValidationError(
                    code="INVALID_REQUEST",
                    message="request must be an object",
                    path="$.request",
                )
            )
        else:
            if not request.get("request_id"):
                errors.append(
                    ValidationError(
                        code="MISSING_REQUEST_ID",
                        message="request.request_id is required for correlation",
                        path="$.request.request_id",
                    )
                )

            method = request.get("method")
            if not method:
                errors.append(
                    ValidationError(
                        code="MISSING_METHOD",
                        message="request.method is required",
                        path="$.request.method",
                    )
                )
            elif method not in VALID_HTTP_METHODS:
                errors.append(
                    ValidationError(
                        code="INVALID_METHOD",
                        message=f"Invalid method: {method}. Must be one of: {VALID_HTTP_METHODS}",
                        path="$.request.method",
                    )
                )

            if not request.get("path"):
                errors.append(
                    ValidationError(
                        code="MISSING_PATH",
                        message="request.path is required",
                        path="$.request.path",
                    )
                )

            status_code = request.get("status_code")
            if status_code is None:
                errors.append(
                    ValidationError(
                        code="MISSING_STATUS_CODE",
                        message="request.status_code is required",
                        path="$.request.status_code",
                    )
                )
            elif not isinstance(status_code, int) or status_code < 100 or status_code > 599:
                errors.append(
                    ValidationError(
                        code="INVALID_STATUS_CODE",
                        message=f"Invalid status_code: {status_code}. Must be 100-599",
                        path="$.request.status_code",
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
            if not resource_type:
                errors.append(
                    ValidationError(
                        code="MISSING_RESOURCE_TYPE",
                        message="resource.resource_type is required",
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
            # Treat redaction violations as errors (fail closed)
            for err in redaction_errors:
                if err.code == "REDACTION_VIOLATION":
                    errors.append(err)
                else:
                    warnings.append(err)

        if errors:
            return ValidationResult.fail(errors)

        return ValidationResult.success(warnings if warnings else None)
