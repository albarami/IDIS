"""Tests for AuditEventValidator - proves fail-closed behavior.

Tests organized by category:
1. Fail-closed regression tests (None, non-dict)
2. Positive tests (valid events pass)
3. Negative tests (invalid events fail with correct error codes)
4. Redaction policy tests
"""

from __future__ import annotations

from idis.validators import AuditEventValidator, validate_audit_event

# === Test helpers ===


def build_valid_audit_event(**overrides: object) -> dict:
    """Build a minimal valid audit event dict.

    Use overrides to customize fields for specific test cases.
    """
    base = {
        "event_id": "550e8400-e29b-41d4-a716-446655440000",
        "occurred_at": "2026-01-06T12:00:00Z",
        "tenant_id": "550e8400-e29b-41d4-a716-446655440001",
        "actor": {
            "actor_type": "HUMAN",
            "actor_id": "user@example.com",
            "roles": ["ANALYST"],
        },
        "request": {
            "request_id": "req_123456",
            "method": "POST",
            "path": "/v1/deals",
            "status_code": 201,
        },
        "resource": {
            "resource_type": "deal",
            "resource_id": "550e8400-e29b-41d4-a716-446655440002",
        },
        "event_type": "deal.created",
        "severity": "MEDIUM",
        "summary": "Created new deal: Acme Corp Series A",
    }
    base.update(overrides)
    return base


# === Fail-closed regression tests ===


class TestAuditEventFailClosed:
    """Tests proving fail-closed behavior - never raises on malformed input."""

    def test_none_data_fails_closed_without_exception(self) -> None:
        """Validator rejects None data without raising."""
        validator = AuditEventValidator()
        result = validator.validate(None)

        assert not result.passed
        assert result.errors[0].code == "FAIL_CLOSED"

    def test_non_dict_string_fails_closed_without_exception(self) -> None:
        """Validator rejects string data without raising."""
        validator = AuditEventValidator()
        result = validator.validate("not a dict")

        assert not result.passed
        assert result.errors[0].code == "FAIL_CLOSED"

    def test_non_dict_list_fails_closed_without_exception(self) -> None:
        """Validator rejects list data without raising."""
        validator = AuditEventValidator()
        result = validator.validate([])

        assert not result.passed
        assert result.errors[0].code == "FAIL_CLOSED"

    def test_non_dict_integer_fails_closed_without_exception(self) -> None:
        """Validator rejects integer data without raising."""
        validator = AuditEventValidator()
        result = validator.validate(42)

        assert not result.passed
        assert result.errors[0].code == "FAIL_CLOSED"

    def test_public_function_none_fails_without_exception(self) -> None:
        """Public validate_audit_event function handles None without raising."""
        result = validate_audit_event(None)  # type: ignore[arg-type]

        assert not result.passed
        assert result.errors[0].code == "FAIL_CLOSED"

    def test_public_function_string_fails_without_exception(self) -> None:
        """Public validate_audit_event function handles string without raising."""
        result = validate_audit_event("not a dict")  # type: ignore[arg-type]

        assert not result.passed
        assert result.errors[0].code == "FAIL_CLOSED"


class TestAuditEventPositive:
    """Positive tests - valid audit events pass."""

    def test_valid_audit_event_passes(self) -> None:
        """Minimal valid audit event passes validation."""
        validator = AuditEventValidator()
        valid_event = build_valid_audit_event()

        result = validator.validate(valid_event)
        assert result.passed, f"Expected pass but got: {result.errors}"

    def test_public_function_valid_event_passes(self) -> None:
        """Public validate_audit_event function passes valid event."""
        valid_event = build_valid_audit_event()

        result = validate_audit_event(valid_event)
        assert result.passed, f"Expected pass but got: {result.errors}"

    def test_service_actor_passes(self) -> None:
        """Service actor type passes validation."""
        validator = AuditEventValidator()
        valid_event = build_valid_audit_event(
            actor={
                "actor_type": "SERVICE",
                "actor_id": "ingestion-worker",
            },
            event_type="document.ingestion.started",
            resource={
                "resource_type": "document",
                "resource_id": "550e8400-e29b-41d4-a716-446655440002",
            },
        )

        result = validator.validate(valid_event)
        assert result.passed

    def test_complex_event_type_passes(self) -> None:
        """Multi-segment dotted event type passes."""
        validator = AuditEventValidator()
        valid_event = build_valid_audit_event(
            event_type="claim.verdict.changed",
            resource={"resource_type": "claim", "resource_id": "abc-123"},
        )

        result = validator.validate(valid_event)
        assert result.passed


class TestAuditEventNegative:
    """Negative tests - invalid audit events fail with correct error codes."""

    def test_missing_tenant_id_fails(self) -> None:
        """Missing tenant_id fails (tenant isolation required)."""
        validator = AuditEventValidator()
        event = build_valid_audit_event()
        del event["tenant_id"]

        result = validator.validate(event)
        assert not result.passed
        assert any(e.code == "MISSING_TENANT_ID" for e in result.errors)

    def test_event_id_not_uuid_fails(self) -> None:
        """event_id that is not UUID-like fails with AUDIT_INVALID_UUID."""
        validator = AuditEventValidator()
        event = build_valid_audit_event(event_id="not-a-uuid")

        result = validator.validate(event)
        assert not result.passed
        assert any(e.code == "AUDIT_INVALID_UUID" for e in result.errors)

    def test_tenant_id_not_uuid_fails(self) -> None:
        """tenant_id that is not UUID-like fails with AUDIT_INVALID_UUID."""
        validator = AuditEventValidator()
        event = build_valid_audit_event(tenant_id="bad-tenant-id")

        result = validator.validate(event)
        assert not result.passed
        assert any(e.code == "AUDIT_INVALID_UUID" for e in result.errors)

    def test_occurred_at_invalid_format_fails(self) -> None:
        """occurred_at with invalid format fails with AUDIT_INVALID_TIMESTAMP."""
        validator = AuditEventValidator()
        event = build_valid_audit_event(occurred_at="not-a-timestamp")

        result = validator.validate(event)
        assert not result.passed
        assert any(e.code == "AUDIT_INVALID_TIMESTAMP" for e in result.errors)

    def test_occurred_at_bad_date_fails(self) -> None:
        """occurred_at with bad date format fails."""
        validator = AuditEventValidator()
        event = build_valid_audit_event(occurred_at="2026-13-45T99:99:99Z")

        result = validator.validate(event)
        assert not result.passed
        assert any(e.code == "AUDIT_INVALID_TIMESTAMP" for e in result.errors)

    def test_actor_is_not_dict_fails_without_exception(self) -> None:
        """actor as string (not dict) fails with AUDIT_INVALID_ACTOR without exception."""
        validator = AuditEventValidator()
        event = build_valid_audit_event(actor="oops")

        result = validator.validate(event)
        assert not result.passed
        assert any(e.code == "AUDIT_INVALID_ACTOR" for e in result.errors)

    def test_actor_missing_actor_id_fails(self) -> None:
        """Missing actor.actor_id fails with AUDIT_INVALID_ACTOR."""
        validator = AuditEventValidator()
        event = build_valid_audit_event(actor={"actor_type": "HUMAN"})

        result = validator.validate(event)
        assert not result.passed
        assert any(e.code == "AUDIT_INVALID_ACTOR" for e in result.errors)

    def test_request_missing_request_id_fails(self) -> None:
        """Missing request.request_id fails with AUDIT_INVALID_REQUEST."""
        validator = AuditEventValidator()
        event = build_valid_audit_event(
            request={
                "method": "POST",
                "path": "/v1/deals",
                "status_code": 201,
            }
        )

        result = validator.validate(event)
        assert not result.passed
        assert any(e.code == "AUDIT_INVALID_REQUEST" for e in result.errors)

    def test_event_type_uppercase_fails(self) -> None:
        """event_type in UPPERCASE (e.g., 'BADTYPE') fails with INVALID_EVENT_TYPE."""
        validator = AuditEventValidator()
        event = build_valid_audit_event(event_type="BADTYPE")

        result = validator.validate(event)
        assert not result.passed
        assert any(e.code == "INVALID_EVENT_TYPE" for e in result.errors)

    def test_event_type_no_dot_fails(self) -> None:
        """event_type without dot (e.g., 'deal_created') fails."""
        validator = AuditEventValidator()
        event = build_valid_audit_event(event_type="deal_created")

        result = validator.validate(event)
        assert not result.passed
        assert any(e.code == "INVALID_EVENT_TYPE" for e in result.errors)

    def test_event_type_unknown_taxonomy_fails(self) -> None:
        """event_type not in known taxonomy fails."""
        validator = AuditEventValidator()
        event = build_valid_audit_event(event_type="random.invalid.event")

        result = validator.validate(event)
        assert not result.passed
        assert any(e.code == "INVALID_EVENT_TYPE" for e in result.errors)

    def test_invalid_severity_fails(self) -> None:
        """Invalid severity fails."""
        validator = AuditEventValidator()
        event = build_valid_audit_event(severity="EXTREME")

        result = validator.validate(event)
        assert not result.passed
        assert any(e.code == "INVALID_SEVERITY" for e in result.errors)


class TestAuditEventRedaction:
    """Tests for redaction policy enforcement."""

    def test_password_in_payload_fails(self) -> None:
        """Sensitive field 'password' in payload fails with REDACTION_VIOLATION."""
        validator = AuditEventValidator()
        event = build_valid_audit_event(
            event_type="auth.login.succeeded",
            payload={"password": "secret123"},
        )

        result = validator.validate(event)
        assert not result.passed
        assert any(e.code == "REDACTION_VIOLATION" for e in result.errors)

    def test_api_key_in_payload_fails(self) -> None:
        """Sensitive field 'api_key' in payload fails with REDACTION_VIOLATION."""
        validator = AuditEventValidator()
        event = build_valid_audit_event(
            event_type="webhook.created",
            resource={"resource_type": "webhook", "resource_id": "123"},
            payload={"api_key": "sk_live_abc123"},
        )

        result = validator.validate(event)
        assert not result.passed
        assert any(e.code == "REDACTION_VIOLATION" for e in result.errors)

    def test_hashed_refs_in_payload_passes(self) -> None:
        """Hashed references in payload pass (proper redaction)."""
        validator = AuditEventValidator()
        event = build_valid_audit_event(
            payload={
                "safe": {"deal_name": "Acme Corp"},
                "hashes": ["sha256:abc123..."],
                "refs": ["claim_id:550e8400..."],
            }
        )

        result = validator.validate(event)
        assert result.passed


class TestAuditEventSchemaValidation:
    """Tests for JSON schema validation."""

    def test_extra_property_fails_schema(self) -> None:
        """Extra properties not in schema fail with AUDIT_SCHEMA_VIOLATION."""
        validator = AuditEventValidator()
        event = build_valid_audit_event()
        event["unknown_field"] = "should fail"

        result = validator.validate(event)
        assert not result.passed
        assert any(e.code == "AUDIT_SCHEMA_VIOLATION" for e in result.errors)
