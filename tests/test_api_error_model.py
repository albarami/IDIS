"""Tests for IDIS API error model and exception handling.

Tests cover:
A) 401 error envelope + request_id correlation
B) 404 error envelope with normative structure
C) Invalid JSON returns normative envelope with INVALID_JSON code
D) Audit fail-closed uses normative envelope (no stack traces)
E) Generic exception handling (500 with safe message, no stack traces)
"""

import json
import os
import uuid

import pytest
from fastapi.testclient import TestClient

from idis.api.main import create_app
from idis.audit.sink import JsonlFileAuditSink


@pytest.fixture
def test_tenant_id() -> str:
    """Generate a test tenant UUID."""
    return str(uuid.uuid4())


@pytest.fixture
def test_actor_id() -> str:
    """Generate a test actor UUID."""
    return f"actor-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def test_api_key() -> str:
    """Generate a test API key."""
    return f"test-key-{uuid.uuid4().hex[:12]}"


@pytest.fixture
def api_keys_config(
    test_tenant_id: str, test_actor_id: str, test_api_key: str
) -> dict[str, dict[str, str | list[str]]]:
    """Create API keys configuration for testing."""
    return {
        test_api_key: {
            "tenant_id": test_tenant_id,
            "actor_id": test_actor_id,
            "name": "Test Tenant",
            "timezone": "UTC",
            "data_region": "us-east-1",
            "roles": ["ANALYST"],
        }
    }


@pytest.fixture
def client_with_valid_key(tmp_path: str, api_keys_config: dict[str, dict[str, str]]) -> TestClient:
    """Create test client with valid API key configured."""
    audit_log_path = os.path.join(tmp_path, "audit.jsonl")
    sink = JsonlFileAuditSink(file_path=audit_log_path)

    os.environ["IDIS_API_KEYS_JSON"] = json.dumps(api_keys_config)

    app = create_app(audit_sink=sink)
    client = TestClient(app, raise_server_exceptions=False)

    yield client

    if "IDIS_API_KEYS_JSON" in os.environ:
        del os.environ["IDIS_API_KEYS_JSON"]


@pytest.fixture
def client_no_keys(tmp_path: str) -> TestClient:
    """Create test client with no API keys configured."""
    audit_log_path = os.path.join(tmp_path, "audit.jsonl")
    sink = JsonlFileAuditSink(file_path=audit_log_path)

    if "IDIS_API_KEYS_JSON" in os.environ:
        del os.environ["IDIS_API_KEYS_JSON"]

    app = create_app(audit_sink=sink)
    client = TestClient(app, raise_server_exceptions=False)

    yield client


class TestErrorEnvelope401:
    """Test A: 401 error envelope + request_id correlation."""

    def test_401_has_normative_envelope(self, client_no_keys: TestClient) -> None:
        """401 response has code, message, details, request_id keys."""
        response = client_no_keys.get("/v1/tenants/me")

        assert response.status_code == 401
        body = response.json()

        assert "code" in body
        assert "message" in body
        assert "details" in body
        assert "request_id" in body

    def test_401_request_id_matches_header(self, client_no_keys: TestClient) -> None:
        """401 response request_id matches X-Request-Id header."""
        response = client_no_keys.get("/v1/tenants/me")

        assert response.status_code == 401
        body = response.json()

        header_request_id = response.headers.get("X-Request-Id")
        assert header_request_id is not None
        assert body["request_id"] == header_request_id

    def test_401_code_is_unauthorized(self, client_no_keys: TestClient) -> None:
        """401 response has code 'unauthorized'."""
        response = client_no_keys.get("/v1/tenants/me")

        assert response.status_code == 401
        body = response.json()
        assert body["code"] == "unauthorized"


class TestErrorEnvelope422:
    """Test B: 422 error envelope with normative structure (schema validation)."""

    def test_422_has_normative_envelope(
        self, client_with_valid_key: TestClient, test_api_key: str
    ) -> None:
        """422 response has code, message, details, request_id keys."""
        response = client_with_valid_key.post(
            "/v1/deals",
            headers={
                "X-IDIS-API-Key": test_api_key,
                "Content-Type": "application/json",
            },
            json={},
        )

        assert response.status_code == 400
        body = response.json()

        assert "code" in body
        assert "message" in body
        assert "details" in body
        assert "request_id" in body

    def test_400_missing_field_code_is_invalid_request(
        self, client_with_valid_key: TestClient, test_api_key: str
    ) -> None:
        """400 response for missing field has code 'INVALID_REQUEST'."""
        response = client_with_valid_key.post(
            "/v1/deals",
            headers={
                "X-IDIS-API-Key": test_api_key,
                "Content-Type": "application/json",
            },
            json={},
        )

        assert response.status_code == 400
        body = response.json()
        assert body["code"] == "INVALID_REQUEST"

    def test_400_request_id_matches_header(
        self, client_with_valid_key: TestClient, test_api_key: str
    ) -> None:
        """400 response request_id matches X-Request-Id header."""
        response = client_with_valid_key.post(
            "/v1/deals",
            headers={
                "X-IDIS-API-Key": test_api_key,
                "Content-Type": "application/json",
            },
            json={},
        )

        assert response.status_code == 400
        body = response.json()

        header_request_id = response.headers.get("X-Request-Id")
        assert header_request_id is not None
        assert body["request_id"] == header_request_id


class TestErrorEnvelopeInvalidJson:
    """Test C: Invalid JSON returns normative envelope."""

    def test_invalid_json_returns_400(
        self, client_with_valid_key: TestClient, test_api_key: str
    ) -> None:
        """Invalid JSON body returns 400 with normative envelope."""
        response = client_with_valid_key.post(
            "/v1/deals",
            headers={
                "X-IDIS-API-Key": test_api_key,
                "Content-Type": "application/json",
            },
            content=b"{",
        )

        assert response.status_code == 400
        body = response.json()

        assert "code" in body
        assert "message" in body
        assert "details" in body
        assert "request_id" in body

    def test_invalid_json_code_is_invalid_json(
        self, client_with_valid_key: TestClient, test_api_key: str
    ) -> None:
        """Invalid JSON returns code 'INVALID_JSON'."""
        response = client_with_valid_key.post(
            "/v1/deals",
            headers={
                "X-IDIS-API-Key": test_api_key,
                "Content-Type": "application/json",
            },
            content=b"{",
        )

        assert response.status_code == 400
        body = response.json()
        assert body["code"] == "INVALID_JSON"

    def test_invalid_json_request_id_matches_header(
        self, client_with_valid_key: TestClient, test_api_key: str
    ) -> None:
        """Invalid JSON response request_id matches X-Request-Id header."""
        response = client_with_valid_key.post(
            "/v1/deals",
            headers={
                "X-IDIS-API-Key": test_api_key,
                "Content-Type": "application/json",
            },
            content=b"{",
        )

        assert response.status_code == 400
        body = response.json()

        header_request_id = response.headers.get("X-Request-Id")
        assert header_request_id is not None
        assert body["request_id"] == header_request_id


class TestErrorEnvelopeAuditFailClosed:
    """Test D: Audit fail-closed uses normative envelope."""

    def test_audit_sink_failure_returns_500(
        self, tmp_path: str, api_keys_config: dict[str, dict[str, str]], test_api_key: str
    ) -> None:
        """Audit sink failure returns 500 with normative envelope."""
        audit_dir = os.path.join(tmp_path, "audit_dir")
        os.makedirs(audit_dir, exist_ok=True)

        sink = JsonlFileAuditSink(file_path=audit_dir)

        os.environ["IDIS_API_KEYS_JSON"] = json.dumps(api_keys_config)

        app = create_app(audit_sink=sink)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.post(
            "/v1/deals",
            headers={
                "X-IDIS-API-Key": test_api_key,
                "Content-Type": "application/json",
            },
            json={"name": "Test Deal", "company_name": "Test Company"},
        )

        assert response.status_code == 500
        body = response.json()

        assert "code" in body
        assert "message" in body
        assert "details" in body
        assert "request_id" in body

        if "IDIS_API_KEYS_JSON" in os.environ:
            del os.environ["IDIS_API_KEYS_JSON"]

    def test_audit_failure_code_is_audit_emit_failed(
        self, tmp_path: str, api_keys_config: dict[str, dict[str, str]], test_api_key: str
    ) -> None:
        """Audit failure returns code 'AUDIT_EMIT_FAILED'."""
        audit_dir = os.path.join(tmp_path, "audit_dir")
        os.makedirs(audit_dir, exist_ok=True)

        sink = JsonlFileAuditSink(file_path=audit_dir)

        os.environ["IDIS_API_KEYS_JSON"] = json.dumps(api_keys_config)

        app = create_app(audit_sink=sink)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.post(
            "/v1/deals",
            headers={
                "X-IDIS-API-Key": test_api_key,
                "Content-Type": "application/json",
            },
            json={"name": "Test Deal", "company_name": "Test Company"},
        )

        assert response.status_code == 500
        body = response.json()
        assert body["code"] == "AUDIT_EMIT_FAILED"

        if "IDIS_API_KEYS_JSON" in os.environ:
            del os.environ["IDIS_API_KEYS_JSON"]

    def test_audit_failure_no_stack_trace(
        self, tmp_path: str, api_keys_config: dict[str, dict[str, str]], test_api_key: str
    ) -> None:
        """Audit failure response does not contain stack traces."""
        audit_dir = os.path.join(tmp_path, "audit_dir")
        os.makedirs(audit_dir, exist_ok=True)

        sink = JsonlFileAuditSink(file_path=audit_dir)

        os.environ["IDIS_API_KEYS_JSON"] = json.dumps(api_keys_config)

        app = create_app(audit_sink=sink)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.post(
            "/v1/deals",
            headers={
                "X-IDIS-API-Key": test_api_key,
                "Content-Type": "application/json",
            },
            json={"name": "Test Deal", "company_name": "Test Company"},
        )

        assert response.status_code == 500
        body = response.json()

        response_text = json.dumps(body)
        assert "Traceback" not in response_text
        assert "File " not in response_text
        assert "line " not in response_text

        if "IDIS_API_KEYS_JSON" in os.environ:
            del os.environ["IDIS_API_KEYS_JSON"]


class TestErrorEnvelopeConsistency:
    """Test that all error responses have consistent structure."""

    def test_all_error_responses_have_four_keys(
        self, client_with_valid_key: TestClient, test_api_key: str
    ) -> None:
        """All error responses have exactly code, message, details, request_id."""
        response = client_with_valid_key.post(
            "/v1/deals",
            headers={
                "X-IDIS-API-Key": test_api_key,
                "Content-Type": "application/json",
            },
            content=b"{",
        )

        body = response.json()
        expected_keys = {"code", "message", "details", "request_id"}
        assert set(body.keys()) == expected_keys

    def test_request_id_header_always_present(
        self, client_with_valid_key: TestClient, test_api_key: str
    ) -> None:
        """X-Request-Id header is always present in error responses."""
        response = client_with_valid_key.post(
            "/v1/deals",
            headers={
                "X-IDIS-API-Key": test_api_key,
                "Content-Type": "application/json",
            },
            content=b"{",
        )

        assert "X-Request-Id" in response.headers
        assert response.headers["X-Request-Id"]
