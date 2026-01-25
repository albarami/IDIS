"""Tests for audit middleware (Phase 2.3).

Verifies:
A) Audit emitted on authenticated mutating /v1 request (even if request is invalid JSON)
B) Fail closed if sink write fails
C) No audit for non-mutating routes
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from idis.api.main import create_app
from idis.audit.sink import JsonlFileAuditSink
from idis.validators.audit_event_validator import validate_audit_event


def _make_api_keys_json(
    tenant_id: str, actor_id: str | None = None, name: str = "Test Tenant"
) -> str:
    """Create a valid IDIS_API_KEYS_JSON value for testing."""
    if actor_id is None:
        actor_id = f"actor-{tenant_id[:8]}"
    return json.dumps(
        {
            "test-api-key-12345": {
                "tenant_id": tenant_id,
                "actor_id": actor_id,
                "name": name,
                "timezone": "UTC",
                "data_region": "us-east-1",
                "roles": ["ANALYST"],
            }
        }
    )


class TestAuditMiddlewareEmission:
    """Test A: Audit emitted on authenticated mutating /v1 request."""

    def test_no_audit_for_client_error_response(self, tmp_path: Path) -> None:
        """Verify NO audit event is emitted for 4xx client error responses.

        For 4xx responses, no mutation occurred (request was rejected due to
        validation, not found, etc.). Audit should be skipped since there's
        nothing to record - no state change happened.

        This also prevents leaking internal audit errors for failed requests.
        """
        tenant_id = str(uuid.uuid4())
        audit_log_path = tmp_path / "audit.jsonl"
        request_id = f"req-{uuid.uuid4()}"
        invalid_body = "{"

        os.environ["IDIS_API_KEYS_JSON"] = _make_api_keys_json(tenant_id)
        os.environ["IDIS_AUDIT_LOG_PATH"] = str(audit_log_path)

        try:
            sink = JsonlFileAuditSink(str(audit_log_path))
            app = create_app(audit_sink=sink, service_region="us-east-1")
            client = TestClient(app, raise_server_exceptions=False)

            response = client.post(
                "/v1/deals",
                headers={
                    "X-IDIS-API-Key": "test-api-key-12345",
                    "X-Request-Id": request_id,
                    "Content-Type": "application/json",
                },
                content=invalid_body,
            )

            # Request should fail with 400 (invalid JSON)
            assert response.status_code == 400
            response_json = response.json()
            assert response_json["code"] == "INVALID_JSON"

            # No audit should be emitted for 4xx responses
            # (no mutation occurred, nothing to audit)
            if audit_log_path.exists():
                content = audit_log_path.read_text().strip()
                assert content == "", "No audit should be emitted for 4xx responses"

        finally:
            os.environ.pop("IDIS_API_KEYS_JSON", None)
            os.environ.pop("IDIS_AUDIT_LOG_PATH", None)

    def test_audit_emitted_on_valid_request(self, tmp_path: Path) -> None:
        """Verify audit event is emitted for valid authenticated mutation request.

        Even though there's no handler for POST /v1/deals yet (will return 404 or similar),
        the audit middleware should still emit an event.
        """
        tenant_id = str(uuid.uuid4())
        audit_log_path = tmp_path / "audit_valid.jsonl"
        request_id = f"req-{uuid.uuid4()}"
        valid_body = json.dumps({"name": "Test Deal", "company_name": "Acme Corp"})

        os.environ["IDIS_API_KEYS_JSON"] = _make_api_keys_json(tenant_id)
        os.environ["IDIS_AUDIT_LOG_PATH"] = str(audit_log_path)

        try:
            sink = JsonlFileAuditSink(str(audit_log_path))
            app = create_app(audit_sink=sink, service_region="us-east-1")
            client = TestClient(app, raise_server_exceptions=False)

            client.post(
                "/v1/deals",
                headers={
                    "X-IDIS-API-Key": "test-api-key-12345",
                    "X-Request-Id": request_id,
                    "Content-Type": "application/json",
                },
                content=valid_body,
            )

            assert audit_log_path.exists(), "Audit log file should exist"

            lines = audit_log_path.read_text().strip().split("\n")
            assert len(lines) == 1

            event = json.loads(lines[0])
            validation_result = validate_audit_event(event)
            assert validation_result.passed

            assert event["tenant_id"] == tenant_id
            assert event["event_type"] == "deal.created"

        finally:
            os.environ.pop("IDIS_API_KEYS_JSON", None)
            os.environ.pop("IDIS_AUDIT_LOG_PATH", None)


class TestAuditMiddlewareFailClosed:
    """Test B: Fail closed if sink write fails."""

    def test_fail_closed_on_sink_write_failure_for_success(self, tmp_path: Path) -> None:
        """Verify 500 AUDIT_EMIT_FAILED when sink cannot write for successful mutation.

        Test scenario:
        - Set IDIS_AUDIT_LOG_PATH to a directory (not a file)
        - POST /v1/deals with valid key and VALID JSON (to get 2xx response)
        - Expect 500 Error JSON with code="AUDIT_EMIT_FAILED"
        - No stack trace leakage in response

        Note: For 4xx responses, no audit is attempted (no mutation occurred),
        so sink failure wouldn't be triggered. This test uses valid JSON to
        trigger an actual mutation attempt that would need auditing.
        """
        tenant_id = str(uuid.uuid4())
        bad_sink_path = tmp_path / "not_a_file_but_a_dir"
        bad_sink_path.mkdir()

        os.environ["IDIS_API_KEYS_JSON"] = _make_api_keys_json(tenant_id)
        os.environ["IDIS_AUDIT_LOG_PATH"] = str(bad_sink_path)

        try:
            sink = JsonlFileAuditSink(str(bad_sink_path))
            app = create_app(audit_sink=sink, service_region="us-east-1")
            client = TestClient(app, raise_server_exceptions=False)

            # Use valid JSON to trigger a mutation that would need auditing
            response = client.post(
                "/v1/deals",
                headers={
                    "X-IDIS-API-Key": "test-api-key-12345",
                    "Content-Type": "application/json",
                },
                json={"name": "Test Deal", "company_name": "Acme Corp"},
            )

            # The mutation should succeed (201) but audit sink fails → 500
            assert response.status_code == 500
            response_json = response.json()
            assert response_json["code"] == "AUDIT_EMIT_FAILED"

            assert "traceback" not in response_json.get("message", "").lower()
            assert "exception" not in response_json.get("message", "").lower()

        finally:
            os.environ.pop("IDIS_API_KEYS_JSON", None)
            os.environ.pop("IDIS_AUDIT_LOG_PATH", None)


class TestAuditMiddlewareNoAuditForNonMutating:
    """Test C: No audit for non-mutating routes."""

    def test_no_audit_for_health_endpoint(self, tmp_path: Path) -> None:
        """Verify GET /health does not emit audit events."""
        audit_log_path = tmp_path / "audit_health.jsonl"

        os.environ["IDIS_AUDIT_LOG_PATH"] = str(audit_log_path)

        try:
            sink = JsonlFileAuditSink(str(audit_log_path))
            app = create_app(audit_sink=sink, service_region="us-east-1")
            client = TestClient(app, raise_server_exceptions=False)

            response = client.get("/health")

            assert response.status_code == 200

            if audit_log_path.exists():
                content = audit_log_path.read_text().strip()
                assert content == "", "Audit log should be empty for GET /health"

        finally:
            os.environ.pop("IDIS_AUDIT_LOG_PATH", None)

    def test_no_audit_for_get_tenants_me(self, tmp_path: Path) -> None:
        """Verify GET /v1/tenants/me does not emit audit events."""
        tenant_id = str(uuid.uuid4())
        audit_log_path = tmp_path / "audit_tenants.jsonl"

        os.environ["IDIS_API_KEYS_JSON"] = _make_api_keys_json(tenant_id)
        os.environ["IDIS_AUDIT_LOG_PATH"] = str(audit_log_path)

        try:
            sink = JsonlFileAuditSink(str(audit_log_path))
            app = create_app(audit_sink=sink, service_region="us-east-1")
            client = TestClient(app, raise_server_exceptions=False)

            response = client.get(
                "/v1/tenants/me",
                headers={"X-IDIS-API-Key": "test-api-key-12345"},
            )

            assert response.status_code == 200

            if audit_log_path.exists():
                content = audit_log_path.read_text().strip()
                assert content == "", "Audit log should be empty for GET /v1/tenants/me"

        finally:
            os.environ.pop("IDIS_API_KEYS_JSON", None)
            os.environ.pop("IDIS_AUDIT_LOG_PATH", None)

    def test_no_audit_for_unauthorized_requests(self, tmp_path: Path) -> None:
        """Verify unauthorized /v1 requests do not emit audit events (no tenant context)."""
        audit_log_path = tmp_path / "audit_unauth.jsonl"

        os.environ.pop("IDIS_API_KEYS_JSON", None)
        os.environ["IDIS_AUDIT_LOG_PATH"] = str(audit_log_path)

        try:
            sink = JsonlFileAuditSink(str(audit_log_path))
            app = create_app(audit_sink=sink, service_region="us-east-1")
            client = TestClient(app, raise_server_exceptions=False)

            response = client.post(
                "/v1/deals",
                headers={"Content-Type": "application/json"},
                content='{"name": "Test"}',
            )

            assert response.status_code == 401

            if audit_log_path.exists():
                content = audit_log_path.read_text().strip()
                assert content == "", "Audit log should be empty for unauthorized requests"

        finally:
            os.environ.pop("IDIS_AUDIT_LOG_PATH", None)


class TestAuditEventValidation:
    """Test that emitted audit events are v6.3 compliant."""

    def test_audit_event_has_required_fields(self, tmp_path: Path) -> None:
        """Verify emitted audit events have all required v6.3 fields."""
        tenant_id = str(uuid.uuid4())
        audit_log_path = tmp_path / "audit_fields.jsonl"

        os.environ["IDIS_API_KEYS_JSON"] = _make_api_keys_json(tenant_id)
        os.environ["IDIS_AUDIT_LOG_PATH"] = str(audit_log_path)

        try:
            sink = JsonlFileAuditSink(str(audit_log_path))
            app = create_app(audit_sink=sink, service_region="us-east-1")
            client = TestClient(app, raise_server_exceptions=False)

            client.post(
                "/v1/deals",
                headers={
                    "X-IDIS-API-Key": "test-api-key-12345",
                    "Content-Type": "application/json",
                    "Idempotency-Key": "idem-key-123",
                },
                content='{"name": "Test", "company_name": "Acme"}',
            )

            assert audit_log_path.exists()
            event = json.loads(audit_log_path.read_text().strip())

            required_fields = [
                "event_id",
                "occurred_at",
                "tenant_id",
                "actor",
                "request",
                "resource",
                "event_type",
                "severity",
                "summary",
            ]
            for field in required_fields:
                assert field in event, f"Missing required field: {field}"

            assert "actor_type" in event["actor"]
            assert "actor_id" in event["actor"]
            assert "request_id" in event["request"]
            assert "method" in event["request"]
            assert "path" in event["request"]
            assert "status_code" in event["request"]
            assert "resource_type" in event["resource"]
            assert "resource_id" in event["resource"]

            assert event["request"]["idempotency_key"] == "idem-key-123"

        finally:
            os.environ.pop("IDIS_API_KEYS_JSON", None)
            os.environ.pop("IDIS_AUDIT_LOG_PATH", None)


class TestAuditSink:
    """Unit tests for JsonlFileAuditSink."""

    def test_sink_creates_parent_directories(self, tmp_path: Path) -> None:
        """Verify sink creates parent directories if missing."""
        nested_path = tmp_path / "nested" / "dir" / "audit.jsonl"

        sink = JsonlFileAuditSink(str(nested_path))

        event: dict[str, Any] = {
            "event_id": str(uuid.uuid4()),
            "occurred_at": "2026-01-07T12:00:00Z",
            "tenant_id": str(uuid.uuid4()),
            "actor": {"actor_type": "SERVICE", "actor_id": "test"},
            "request": {
                "request_id": "req-1",
                "method": "POST",
                "path": "/test",
                "status_code": 200,
            },
            "resource": {"resource_type": "deal", "resource_id": str(uuid.uuid4())},
            "event_type": "deal.created",
            "severity": "MEDIUM",
            "summary": "Test event",
            "payload": {"hashes": [], "refs": []},
        }

        sink.emit(event)

        assert nested_path.exists()
        assert len(nested_path.read_text().strip().split("\n")) == 1

    def test_sink_appends_to_existing_file(self, tmp_path: Path) -> None:
        """Verify sink appends to existing file (never overwrites)."""
        audit_path = tmp_path / "audit.jsonl"
        audit_path.write_text('{"existing": "line"}\n')

        sink = JsonlFileAuditSink(str(audit_path))

        event: dict[str, Any] = {
            "event_id": str(uuid.uuid4()),
            "occurred_at": "2026-01-07T12:00:00Z",
            "tenant_id": str(uuid.uuid4()),
            "actor": {"actor_type": "SERVICE", "actor_id": "test"},
            "request": {
                "request_id": "req-1",
                "method": "POST",
                "path": "/test",
                "status_code": 200,
            },
            "resource": {"resource_type": "deal", "resource_id": str(uuid.uuid4())},
            "event_type": "deal.created",
            "severity": "MEDIUM",
            "summary": "Test event",
            "payload": {"hashes": [], "refs": []},
        }

        sink.emit(event)

        lines = audit_path.read_text().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0]) == {"existing": "line"}

    def test_sink_uses_deterministic_json_format(self, tmp_path: Path) -> None:
        """Verify sink uses sorted keys and minimal separators."""
        audit_path = tmp_path / "audit.jsonl"
        sink = JsonlFileAuditSink(str(audit_path))

        event: dict[str, Any] = {
            "z_field": "last",
            "a_field": "first",
            "event_id": str(uuid.uuid4()),
            "occurred_at": "2026-01-07T12:00:00Z",
            "tenant_id": str(uuid.uuid4()),
            "actor": {"actor_type": "SERVICE", "actor_id": "test"},
            "request": {
                "request_id": "req-1",
                "method": "POST",
                "path": "/test",
                "status_code": 200,
            },
            "resource": {"resource_type": "deal", "resource_id": str(uuid.uuid4())},
            "event_type": "deal.created",
            "severity": "MEDIUM",
            "summary": "Test event",
            "payload": {"hashes": [], "refs": []},
        }

        sink.emit(event)

        line = audit_path.read_text().strip()
        assert '"a_field":"first"' in line
        assert line.index("a_field") < line.index("z_field")
        assert " :" not in line
        assert ": " not in line
