"""Tests for IDIS API tenant authentication and /v1/tenants/me endpoint."""

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from idis.api.auth import IDIS_API_KEYS_ENV
from idis.api.main import create_app


@pytest.fixture
def test_tenant_id() -> str:
    """Generate a test tenant UUID."""
    return str(uuid.uuid4())


@pytest.fixture
def test_api_key() -> str:
    """Generate a test API key."""
    return f"test-key-{uuid.uuid4().hex[:16]}"


@pytest.fixture
def test_actor_id() -> str:
    """Generate a test actor UUID."""
    return f"actor-{uuid.uuid4().hex[:8]}"


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
            "timezone": "Asia/Qatar",
            "data_region": "me-south-1",
            "roles": ["ANALYST"],
        }
    }


@pytest.fixture
def client_with_keys(
    api_keys_config: dict[str, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    """Create a test client with API keys configured."""
    monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(api_keys_config))
    app = create_app()
    return TestClient(app)


@pytest.fixture
def client_without_keys(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Create a test client without API keys configured."""
    monkeypatch.delenv(IDIS_API_KEYS_ENV, raising=False)
    app = create_app()
    return TestClient(app)


class TestUnauthorizedNoKey:
    """Test unauthorized access without API key."""

    def test_get_tenant_me_no_key_returns_401(self, client_with_keys: TestClient) -> None:
        """GET /v1/tenants/me without X-IDIS-API-Key returns 401."""
        response = client_with_keys.get("/v1/tenants/me")

        assert response.status_code == 401

    def test_get_tenant_me_no_key_returns_error_json(self, client_with_keys: TestClient) -> None:
        """Response body is Error JSON with code 'unauthorized'."""
        response = client_with_keys.get("/v1/tenants/me")

        body = response.json()
        assert body["code"] == "unauthorized"
        assert "message" in body

    def test_get_tenant_me_no_key_has_request_id(self, client_with_keys: TestClient) -> None:
        """Response includes X-Request-Id header and body.request_id matches it."""
        response = client_with_keys.get("/v1/tenants/me")

        header_request_id = response.headers.get("X-Request-Id")
        assert header_request_id is not None

        body = response.json()
        assert body.get("request_id") == header_request_id


class TestUnauthorizedBadKey:
    """Test unauthorized access with invalid API key."""

    def test_get_tenant_me_bad_key_returns_401(self, client_with_keys: TestClient) -> None:
        """GET /v1/tenants/me with bad key returns 401."""
        response = client_with_keys.get("/v1/tenants/me", headers={"X-IDIS-API-Key": "bad-key"})

        assert response.status_code == 401

    def test_get_tenant_me_bad_key_returns_error_json(self, client_with_keys: TestClient) -> None:
        """Response is Error JSON without leaking expected key."""
        response = client_with_keys.get("/v1/tenants/me", headers={"X-IDIS-API-Key": "bad-key"})

        body = response.json()
        assert body["code"] == "unauthorized"
        assert "bad-key" not in body.get("message", "")
        assert "details" not in body or body["details"] is None

    def test_get_tenant_me_bad_key_has_request_id(self, client_with_keys: TestClient) -> None:
        """Response includes request_id matching header."""
        response = client_with_keys.get("/v1/tenants/me", headers={"X-IDIS-API-Key": "bad-key"})

        header_request_id = response.headers.get("X-Request-Id")
        body = response.json()
        assert body.get("request_id") == header_request_id


class TestUnauthorizedBearerToken:
    """Test unauthorized access with Bearer token (not configured)."""

    def test_bearer_token_returns_401(self, client_with_keys: TestClient) -> None:
        """Bearer token without verifier configured returns 401."""
        response = client_with_keys.get(
            "/v1/tenants/me", headers={"Authorization": "Bearer some-jwt-token"}
        )

        assert response.status_code == 401
        body = response.json()
        assert body["code"] == "unauthorized"


class TestUnauthorizedNoRegistry:
    """Test unauthorized when no API key registry is configured."""

    def test_valid_format_key_no_registry_returns_401(
        self, client_without_keys: TestClient
    ) -> None:
        """Even valid-format key returns 401 when registry is empty."""
        response = client_without_keys.get("/v1/tenants/me", headers={"X-IDIS-API-Key": "some-key"})

        assert response.status_code == 401
        body = response.json()
        assert body["code"] == "unauthorized"


class TestAuthorized:
    """Test authorized access with valid API key."""

    def test_get_tenant_me_valid_key_returns_200(
        self,
        client_with_keys: TestClient,
        test_api_key: str,
    ) -> None:
        """GET /v1/tenants/me with valid key returns 200."""
        response = client_with_keys.get("/v1/tenants/me", headers={"X-IDIS-API-Key": test_api_key})

        assert response.status_code == 200

    def test_get_tenant_me_valid_key_returns_tenant_context(
        self,
        client_with_keys: TestClient,
        test_api_key: str,
        test_tenant_id: str,
    ) -> None:
        """Response JSON includes tenant_id/name/timezone/data_region."""
        response = client_with_keys.get("/v1/tenants/me", headers={"X-IDIS-API-Key": test_api_key})

        body = response.json()
        assert body["tenant_id"] == test_tenant_id
        assert body["name"] == "Test Tenant"
        assert body["timezone"] == "Asia/Qatar"
        assert body["data_region"] == "me-south-1"


class TestHealthNoAuth:
    """Test /health endpoint does not require authentication."""

    def test_health_without_key_returns_200(self, client_with_keys: TestClient) -> None:
        """/health should work without API key."""
        response = client_with_keys.get("/health")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
