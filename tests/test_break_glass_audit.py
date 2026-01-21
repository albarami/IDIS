"""Tests for break-glass audit event compliance.

Validates that break_glass.used audit events:
- Are schema-valid against audit_event.schema.json
- Use only safe/hashes/refs in payload (no raw justification)
- Have CRITICAL severity
- Fail-closed if audit emission fails
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from idis.api.break_glass import (
    MIN_JUSTIFICATION_LENGTH,
    BreakGlassToken,
    create_break_glass_token,
    emit_break_glass_audit_event,
    validate_actor_binding,
    validate_break_glass_token,
)
from idis.api.errors import IdisHttpError
from idis.validators.audit_event_validator import validate_audit_event


@pytest.fixture
def break_glass_secret() -> str:
    """Set up break-glass secret for tests."""
    secret = "test-break-glass-secret-key-32chars!"
    os.environ["IDIS_BREAK_GLASS_SECRET"] = secret
    yield secret
    os.environ.pop("IDIS_BREAK_GLASS_SECRET", None)


TEST_TENANT_ID = "11111111-1111-1111-1111-111111111111"
TEST_DEAL_ID = "22222222-2222-2222-2222-222222222222"
TEST_ACTOR_ID = "admin-user-123"


@pytest.fixture
def valid_token(break_glass_secret: str) -> BreakGlassToken:
    """Create a valid break-glass token for testing."""
    justification = "Emergency access required for critical production incident investigation"
    token_str = create_break_glass_token(
        actor_id=TEST_ACTOR_ID,
        tenant_id=TEST_TENANT_ID,
        justification=justification,
        deal_id=TEST_DEAL_ID,
        duration_seconds=900,
    )
    validation = validate_break_glass_token(
        token_str,
        expected_tenant_id=TEST_TENANT_ID,
        expected_deal_id=TEST_DEAL_ID,
    )
    assert validation.valid
    assert validation.token is not None
    return validation.token


@pytest.fixture
def mock_request() -> MagicMock:
    """Create a mock FastAPI request."""
    request = MagicMock()
    request.state.request_id = str(uuid.uuid4())
    request.state.db_conn = None
    request.state.break_glass_audit_emitted = False
    request.method = "GET"
    request.url.path = f"/v1/deals/{TEST_DEAL_ID}"
    request.client.host = "127.0.0.1"
    request.headers.get.return_value = "TestAgent/1.0"
    return request


class TestBreakGlassAuditPayload:
    """Test audit event payload structure."""

    def test_audit_event_has_only_safe_hashes_refs(
        self, valid_token: BreakGlassToken, mock_request: MagicMock
    ) -> None:
        """Payload must contain only safe, hashes, refs - no raw justification."""
        captured_event: dict[str, Any] = {}

        def capture_emit(event: dict[str, Any]) -> None:
            captured_event.update(event)

        with patch("idis.audit.sink.JsonlFileAuditSink") as mock_sink_class:
            mock_sink = MagicMock()
            mock_sink.emit = capture_emit
            mock_sink_class.return_value = mock_sink

            emit_break_glass_audit_event(
                request=mock_request,
                token=valid_token,
                resource_type="deal",
                resource_id=TEST_DEAL_ID,
                operation_id="getDeal",
            )

        payload = captured_event.get("payload", {})

        # Must have safe, hashes, refs
        assert "safe" in payload
        assert "hashes" in payload
        assert "refs" in payload

        # Must NOT have break_glass key or raw justification text anywhere
        assert "break_glass" not in payload
        # Check that raw justification text is not present (only length/hash allowed)
        # "justification_len" key is OK, raw text is not
        payload_str = str(payload)
        assert "Emergency access" not in payload_str
        assert "critical production incident" not in payload_str

        # Check safe contains expected fields
        safe = payload["safe"]
        assert "scope" in safe
        assert "expires_at" in safe
        assert "justification_len" in safe

        # Check hashes contains token and justification hashes
        hashes = payload["hashes"]
        assert any("token_sha256" in h for h in hashes)
        assert any("justification_sha256" in h for h in hashes)

    def test_audit_event_validates_against_schema(
        self, valid_token: BreakGlassToken, mock_request: MagicMock
    ) -> None:
        """Audit event must pass schema validation."""
        captured_event: dict[str, Any] = {}

        def capture_emit(event: dict[str, Any]) -> None:
            captured_event.update(event)

        with patch("idis.audit.sink.JsonlFileAuditSink") as mock_sink_class:
            mock_sink = MagicMock()
            mock_sink.emit = capture_emit
            mock_sink_class.return_value = mock_sink

            emit_break_glass_audit_event(
                request=mock_request,
                token=valid_token,
                resource_type="deal",
                resource_id=TEST_DEAL_ID,
                operation_id="getDeal",
            )

        # Validate against schema
        validation_result = validate_audit_event(captured_event)
        assert validation_result.passed, f"Validation failed: {validation_result.errors}"

    def test_audit_event_has_critical_severity(
        self, valid_token: BreakGlassToken, mock_request: MagicMock
    ) -> None:
        """Break-glass audit events must have CRITICAL severity."""
        captured_event: dict[str, Any] = {}

        def capture_emit(event: dict[str, Any]) -> None:
            captured_event.update(event)

        with patch("idis.audit.sink.JsonlFileAuditSink") as mock_sink_class:
            mock_sink = MagicMock()
            mock_sink.emit = capture_emit
            mock_sink_class.return_value = mock_sink

            emit_break_glass_audit_event(
                request=mock_request,
                token=valid_token,
                resource_type="deal",
                resource_id=TEST_DEAL_ID,
                operation_id="getDeal",
            )

        assert captured_event["severity"] == "CRITICAL"
        assert captured_event["event_type"] == "break_glass.used"

    def test_raw_justification_never_in_event(
        self, valid_token: BreakGlassToken, mock_request: MagicMock
    ) -> None:
        """Raw justification text must never appear anywhere in audit event."""
        captured_event: dict[str, Any] = {}

        def capture_emit(event: dict[str, Any]) -> None:
            captured_event.update(event)

        with patch("idis.audit.sink.JsonlFileAuditSink") as mock_sink_class:
            mock_sink = MagicMock()
            mock_sink.emit = capture_emit
            mock_sink_class.return_value = mock_sink

            emit_break_glass_audit_event(
                request=mock_request,
                token=valid_token,
                resource_type="deal",
                resource_id=TEST_DEAL_ID,
                operation_id="getDeal",
            )

        # Convert entire event to string and check justification is not present
        event_str = json.dumps(captured_event)
        assert valid_token.justification not in event_str
        assert "Emergency access" not in event_str

    def test_exactly_one_audit_event_emitted(
        self, valid_token: BreakGlassToken, mock_request: MagicMock
    ) -> None:
        """Break-glass must emit exactly one audit event."""
        emit_count = 0

        def count_emit(event: dict[str, Any]) -> None:
            nonlocal emit_count
            emit_count += 1

        with patch("idis.audit.sink.JsonlFileAuditSink") as mock_sink_class:
            mock_sink = MagicMock()
            mock_sink.emit = count_emit
            mock_sink_class.return_value = mock_sink

            emit_break_glass_audit_event(
                request=mock_request,
                token=valid_token,
                resource_type="deal",
                resource_id=TEST_DEAL_ID,
                operation_id="getDeal",
            )

        assert emit_count == 1


class TestBreakGlassFailClosed:
    """Test fail-closed behavior."""

    def test_audit_sink_failure_denies_access(
        self, valid_token: BreakGlassToken, mock_request: MagicMock
    ) -> None:
        """If audit emission fails, access must be denied."""
        from idis.audit.sink import AuditSinkError

        with patch("idis.audit.sink.JsonlFileAuditSink") as mock_sink_class:
            mock_sink = MagicMock()
            mock_sink.emit.side_effect = AuditSinkError("Sink unavailable")
            mock_sink_class.return_value = mock_sink

            with pytest.raises(IdisHttpError) as exc_info:
                emit_break_glass_audit_event(
                    request=mock_request,
                    token=valid_token,
                    resource_type="deal",
                    resource_id=TEST_DEAL_ID,
                    operation_id="getDeal",
                )

            assert exc_info.value.status_code == 500
            assert exc_info.value.code == "audit_emit_failed"


class TestJustificationValidation:
    """Test justification requirements."""

    def test_justification_minimum_length_is_20(self) -> None:
        """Justification must be at least 20 characters."""
        assert MIN_JUSTIFICATION_LENGTH == 20

    def test_empty_justification_denied(self, break_glass_secret: str) -> None:
        """Empty justification must be denied."""
        with pytest.raises(IdisHttpError) as exc_info:
            create_break_glass_token(
                actor_id="admin",
                tenant_id="tenant",
                justification="",
            )

        assert exc_info.value.status_code == 400
        assert exc_info.value.code == "invalid_justification"

    def test_short_justification_denied(self, break_glass_secret: str) -> None:
        """Justification shorter than 20 chars must be denied."""
        with pytest.raises(IdisHttpError) as exc_info:
            create_break_glass_token(
                actor_id="admin",
                tenant_id="tenant",
                justification="too short",  # 9 chars
            )

        assert exc_info.value.status_code == 400
        assert exc_info.value.code == "invalid_justification"

    def test_whitespace_only_justification_denied(self, break_glass_secret: str) -> None:
        """Whitespace-only justification must be denied."""
        with pytest.raises(IdisHttpError) as exc_info:
            create_break_glass_token(
                actor_id="admin",
                tenant_id="tenant",
                justification="                    ",  # 20 spaces
            )

        assert exc_info.value.status_code == 400
        assert exc_info.value.code == "invalid_justification"

    def test_valid_justification_accepted(self, break_glass_secret: str) -> None:
        """Valid 20+ char justification must be accepted."""
        token = create_break_glass_token(
            actor_id="admin",
            tenant_id="tenant",
            justification="This is a valid justification with enough characters",
        )
        assert token is not None
        assert len(token) > 0


class TestActorBinding:
    """Test actor binding validation."""

    def test_matching_actor_allowed(self, valid_token: BreakGlassToken) -> None:
        """Token with matching actor must be allowed."""
        assert validate_actor_binding(valid_token, "admin-user-123")

    def test_mismatched_actor_denied(self, valid_token: BreakGlassToken) -> None:
        """Token with mismatched actor must be denied."""
        assert not validate_actor_binding(valid_token, "different-user-456")

    def test_actor_mismatch_case_sensitive(self, valid_token: BreakGlassToken) -> None:
        """Actor comparison must be case-sensitive."""
        assert not validate_actor_binding(valid_token, "ADMIN-USER-123")
        assert not validate_actor_binding(valid_token, "Admin-User-123")


class TestJustificationHashing:
    """Test that justification is properly hashed."""

    def test_justification_hash_in_payload(
        self, valid_token: BreakGlassToken, mock_request: MagicMock
    ) -> None:
        """Justification hash must be in payload.hashes."""
        captured_event: dict[str, Any] = {}

        def capture_emit(event: dict[str, Any]) -> None:
            captured_event.update(event)

        with patch("idis.audit.sink.JsonlFileAuditSink") as mock_sink_class:
            mock_sink = MagicMock()
            mock_sink.emit = capture_emit
            mock_sink_class.return_value = mock_sink

            emit_break_glass_audit_event(
                request=mock_request,
                token=valid_token,
                resource_type="deal",
                resource_id=TEST_DEAL_ID,
                operation_id="getDeal",
            )

        hashes = captured_event["payload"]["hashes"]
        justification_hashes = [h for h in hashes if "justification_sha256" in h]
        assert len(justification_hashes) == 1

        # Verify hash is correct
        expected_hash = hashlib.sha256(valid_token.justification.encode("utf-8")).hexdigest()
        assert f"justification_sha256:{expected_hash}" in hashes
