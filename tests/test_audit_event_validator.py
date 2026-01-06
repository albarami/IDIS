"""Tests for AuditEventValidator - proves fail-closed behavior."""

from __future__ import annotations

from idis.validators import AuditEventValidator


class TestAuditEventFailClosed:
    """Tests proving fail-closed behavior."""

    def test_none_data_fails_closed(self) -> None:
        """Validator rejects None data."""
        validator = AuditEventValidator()
        result = validator.validate(None)

        assert not result.passed
        assert result.errors[0].code == "FAIL_CLOSED"

    def test_non_dict_fails_closed(self) -> None:
        """Validator rejects non-dict data."""
        validator = AuditEventValidator()
        result = validator.validate([])

        assert not result.passed
        assert result.errors[0].code == "FAIL_CLOSED"


class TestAuditEventPositive:
    """Positive tests - valid audit events pass."""

    def test_valid_audit_event_passes(self) -> None:
        """Valid audit event passes validation."""
        validator = AuditEventValidator()

        valid_event = {
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

        result = validator.validate(valid_event)
        assert result.passed, f"Expected pass but got: {result.errors}"

    def test_service_actor_passes(self) -> None:
        """Service actor type passes validation."""
        validator = AuditEventValidator()

        valid_event = {
            "event_id": "550e8400-e29b-41d4-a716-446655440000",
            "occurred_at": "2026-01-06T12:00:00Z",
            "tenant_id": "550e8400-e29b-41d4-a716-446655440001",
            "actor": {
                "actor_type": "SERVICE",
                "actor_id": "ingestion-worker",
            },
            "request": {
                "request_id": "req_123456",
                "method": "POST",
                "path": "/v1/documents/ingest",
                "status_code": 202,
            },
            "resource": {
                "resource_type": "document",
                "resource_id": "550e8400-e29b-41d4-a716-446655440002",
            },
            "event_type": "document.ingestion.started",
            "severity": "LOW",
            "summary": "Started document ingestion",
        }

        result = validator.validate(valid_event)
        assert result.passed


class TestAuditEventNegative:
    """Negative tests - invalid audit events fail."""

    def test_missing_event_id_fails(self) -> None:
        """Missing event_id fails."""
        validator = AuditEventValidator()

        invalid_event = {
            "occurred_at": "2026-01-06T12:00:00Z",
            "tenant_id": "550e8400-e29b-41d4-a716-446655440001",
            "actor": {"actor_type": "HUMAN", "actor_id": "user@example.com"},
            "request": {
                "request_id": "req_123",
                "method": "POST",
                "path": "/v1/deals",
                "status_code": 201,
            },
            "resource": {"resource_type": "deal", "resource_id": "123"},
            "event_type": "deal.created",
            "severity": "MEDIUM",
            "summary": "Created deal",
        }

        result = validator.validate(invalid_event)
        assert not result.passed
        assert any(e.code == "MISSING_EVENT_ID" for e in result.errors)

    def test_missing_tenant_id_fails(self) -> None:
        """Missing tenant_id fails (tenant isolation required)."""
        validator = AuditEventValidator()

        invalid_event = {
            "event_id": "550e8400-e29b-41d4-a716-446655440000",
            "occurred_at": "2026-01-06T12:00:00Z",
            # tenant_id MISSING
            "actor": {"actor_type": "HUMAN", "actor_id": "user@example.com"},
            "request": {
                "request_id": "req_123",
                "method": "POST",
                "path": "/v1/deals",
                "status_code": 201,
            },
            "resource": {"resource_type": "deal", "resource_id": "123"},
            "event_type": "deal.created",
            "severity": "MEDIUM",
            "summary": "Created deal",
        }

        result = validator.validate(invalid_event)
        assert not result.passed
        assert any(e.code == "MISSING_TENANT_ID" for e in result.errors)

    def test_invalid_event_type_fails(self) -> None:
        """Invalid event type (not in taxonomy) fails."""
        validator = AuditEventValidator()

        invalid_event = {
            "event_id": "550e8400-e29b-41d4-a716-446655440000",
            "occurred_at": "2026-01-06T12:00:00Z",
            "tenant_id": "550e8400-e29b-41d4-a716-446655440001",
            "actor": {"actor_type": "HUMAN", "actor_id": "user@example.com"},
            "request": {
                "request_id": "req_123",
                "method": "POST",
                "path": "/v1/foo",
                "status_code": 201,
            },
            "resource": {"resource_type": "deal", "resource_id": "123"},
            "event_type": "random.invalid.event",  # Not in taxonomy!
            "severity": "MEDIUM",
            "summary": "Did something",
        }

        result = validator.validate(invalid_event)
        assert not result.passed
        assert any(e.code == "INVALID_EVENT_TYPE" for e in result.errors)

    def test_invalid_severity_fails(self) -> None:
        """Invalid severity fails."""
        validator = AuditEventValidator()

        invalid_event = {
            "event_id": "550e8400-e29b-41d4-a716-446655440000",
            "occurred_at": "2026-01-06T12:00:00Z",
            "tenant_id": "550e8400-e29b-41d4-a716-446655440001",
            "actor": {"actor_type": "HUMAN", "actor_id": "user@example.com"},
            "request": {
                "request_id": "req_123",
                "method": "POST",
                "path": "/v1/deals",
                "status_code": 201,
            },
            "resource": {"resource_type": "deal", "resource_id": "123"},
            "event_type": "deal.created",
            "severity": "EXTREME",  # Invalid!
            "summary": "Created deal",
        }

        result = validator.validate(invalid_event)
        assert not result.passed
        assert any(e.code == "INVALID_SEVERITY" for e in result.errors)

    def test_missing_request_id_fails(self) -> None:
        """Missing request.request_id fails (required for correlation)."""
        validator = AuditEventValidator()

        invalid_event = {
            "event_id": "550e8400-e29b-41d4-a716-446655440000",
            "occurred_at": "2026-01-06T12:00:00Z",
            "tenant_id": "550e8400-e29b-41d4-a716-446655440001",
            "actor": {"actor_type": "HUMAN", "actor_id": "user@example.com"},
            "request": {
                # request_id MISSING
                "method": "POST",
                "path": "/v1/deals",
                "status_code": 201,
            },
            "resource": {"resource_type": "deal", "resource_id": "123"},
            "event_type": "deal.created",
            "severity": "MEDIUM",
            "summary": "Created deal",
        }

        result = validator.validate(invalid_event)
        assert not result.passed
        assert any(e.code == "MISSING_REQUEST_ID" for e in result.errors)


class TestAuditEventRedaction:
    """Tests for redaction policy enforcement."""

    def test_password_in_payload_fails(self) -> None:
        """Sensitive field 'password' in payload fails."""
        validator = AuditEventValidator()

        invalid_event = {
            "event_id": "550e8400-e29b-41d4-a716-446655440000",
            "occurred_at": "2026-01-06T12:00:00Z",
            "tenant_id": "550e8400-e29b-41d4-a716-446655440001",
            "actor": {"actor_type": "HUMAN", "actor_id": "user@example.com"},
            "request": {
                "request_id": "req_123",
                "method": "POST",
                "path": "/v1/auth",
                "status_code": 200,
            },
            "resource": {"resource_type": "deal", "resource_id": "123"},
            "event_type": "auth.login.succeeded",
            "severity": "LOW",
            "summary": "User logged in",
            "payload": {
                "password": "secret123",  # VIOLATION!
            },
        }

        result = validator.validate(invalid_event)
        assert not result.passed
        assert any(e.code == "REDACTION_VIOLATION" for e in result.errors)

    def test_api_key_in_payload_fails(self) -> None:
        """Sensitive field 'api_key' in payload fails."""
        validator = AuditEventValidator()

        invalid_event = {
            "event_id": "550e8400-e29b-41d4-a716-446655440000",
            "occurred_at": "2026-01-06T12:00:00Z",
            "tenant_id": "550e8400-e29b-41d4-a716-446655440001",
            "actor": {"actor_type": "SERVICE", "actor_id": "webhook-service"},
            "request": {
                "request_id": "req_123",
                "method": "POST",
                "path": "/v1/webhooks",
                "status_code": 201,
            },
            "resource": {"resource_type": "webhook", "resource_id": "123"},
            "event_type": "webhook.created",
            "severity": "MEDIUM",
            "summary": "Created webhook",
            "payload": {
                "api_key": "sk_live_abc123",  # VIOLATION!
            },
        }

        result = validator.validate(invalid_event)
        assert not result.passed
        assert any(e.code == "REDACTION_VIOLATION" for e in result.errors)

    def test_hashed_refs_in_payload_passes(self) -> None:
        """Hashed references in payload pass (proper redaction)."""
        validator = AuditEventValidator()

        valid_event = {
            "event_id": "550e8400-e29b-41d4-a716-446655440000",
            "occurred_at": "2026-01-06T12:00:00Z",
            "tenant_id": "550e8400-e29b-41d4-a716-446655440001",
            "actor": {"actor_type": "HUMAN", "actor_id": "user@example.com"},
            "request": {
                "request_id": "req_123",
                "method": "POST",
                "path": "/v1/deals",
                "status_code": 201,
            },
            "resource": {"resource_type": "deal", "resource_id": "123"},
            "event_type": "deal.created",
            "severity": "MEDIUM",
            "summary": "Created deal",
            "payload": {
                "safe": {"deal_name": "Acme Corp"},
                "hashes": ["sha256:abc123..."],
                "refs": ["claim_id:550e8400..."],
            },
        }

        result = validator.validate(valid_event)
        assert result.passed
