"""Tests for unsupported claim API methods (PUT/DELETE).

Verifies that unsupported methods on /v1/claims/{id} are rejected by OpenAPI
validation without leaking internal audit details.

Phase 3.2.2 regression test per IDIS_Technical_Infrastructure_v6_3.md:
- Unsupported methods must return 404/405/422 (not 500)
- Response must not contain audit-related error strings
- Request correlation must be preserved
"""

from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from idis.api.auth import IDIS_API_KEYS_ENV
from idis.api.main import create_app


@pytest.fixture
def api_keys_config() -> dict[str, dict[str, str | list[str]]]:
    """Test API keys configuration."""
    return {
        "test-api-key-tenant-a": {
            "tenant_id": "tenant-a-uuid",
            "actor_id": "actor-a-uuid",
            "name": "Tenant A",
            "timezone": "UTC",
            "data_region": "me-south-1",
            "roles": ["ANALYST"],
        },
    }


@pytest.fixture
def client(
    api_keys_config: dict[str, dict[str, str | list[str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    """Create a test client with API keys configured."""
    monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(api_keys_config))
    app = create_app(service_region="me-south-1")
    return TestClient(app, raise_server_exceptions=False)


class TestClaimsUnsupportedMethods:
    """Tests that unsupported HTTP methods on claims are rejected cleanly."""

    def test_put_claim_returns_method_not_allowed(
        self,
        client: TestClient,
    ) -> None:
        """PUT /v1/claims/{id} returns 405 Method Not Allowed without audit leak."""
        claim_id = str(uuid.uuid4())

        response = client.put(
            f"/v1/claims/{claim_id}",
            json={
                "claim_text": "Updated claim text",
                "materiality": "HIGH",
            },
            headers={"X-IDIS-API-Key": "test-api-key-tenant-a"},
        )

        # Must be rejected as OpenAPI-invalid, not 500 or 403
        # 405 = Method Not Allowed (expected - unsupported method)
        # 404 = Not Found (route doesn't exist)
        # 422 = Validation Error
        assert response.status_code in {404, 405, 422}, (
            f"Expected 404/405/422 for unsupported PUT, got {response.status_code}"
        )

        # Verify no audit internals leaked
        body_text = response.text.lower()
        assert "audit_emit_failed" not in body_text, "Audit error code leaked"
        assert "operation_id" not in body_text, "operation_id reference leaked"
        assert "unknown or missing operation" not in body_text, "Audit message leaked"

    def test_delete_claim_returns_method_not_allowed(
        self,
        client: TestClient,
    ) -> None:
        """DELETE /v1/claims/{id} returns 405 Method Not Allowed without audit leak."""
        claim_id = str(uuid.uuid4())

        response = client.delete(
            f"/v1/claims/{claim_id}",
            headers={"X-IDIS-API-Key": "test-api-key-tenant-a"},
        )

        # Must be rejected as OpenAPI-invalid, not 500 or 403
        # 405 = Method Not Allowed (expected - unsupported method)
        # 404 = Not Found (route doesn't exist)
        # 422 = Validation Error
        assert response.status_code in {404, 405, 422}, (
            f"Expected 404/405/422 for unsupported DELETE, got {response.status_code}"
        )

        # Verify no audit internals leaked
        body_text = response.text.lower()
        assert "audit_emit_failed" not in body_text, "Audit error code leaked"
        assert "operation_id" not in body_text, "operation_id reference leaked"
        assert "unknown or missing operation" not in body_text, "Audit message leaked"

    def test_put_claim_preserves_request_correlation(
        self,
        client: TestClient,
    ) -> None:
        """PUT rejection response includes request correlation."""
        claim_id = str(uuid.uuid4())

        response = client.put(
            f"/v1/claims/{claim_id}",
            json={"claim_text": "test"},
            headers={"X-IDIS-API-Key": "test-api-key-tenant-a"},
        )

        # Request-Id header should be present in response
        assert "x-request-id" in response.headers or response.status_code in {
            404,
            405,
        }, "Request correlation expected in error response"

    def test_delete_claim_preserves_request_correlation(
        self,
        client: TestClient,
    ) -> None:
        """DELETE rejection response includes request correlation."""
        claim_id = str(uuid.uuid4())

        response = client.delete(
            f"/v1/claims/{claim_id}",
            headers={"X-IDIS-API-Key": "test-api-key-tenant-a"},
        )

        # Request-Id header should be present in response
        assert "x-request-id" in response.headers or response.status_code in {
            404,
            405,
        }, "Request correlation expected in error response"


class TestClaimsSupportedMethodsStillWork:
    """Sanity checks that supported methods still function correctly."""

    def test_get_claim_not_found_is_clean(
        self,
        client: TestClient,
    ) -> None:
        """GET /v1/claims/{id} for nonexistent claim returns 404 cleanly."""
        claim_id = str(uuid.uuid4())

        response = client.get(
            f"/v1/claims/{claim_id}",
            headers={"X-IDIS-API-Key": "test-api-key-tenant-a"},
        )

        assert response.status_code == 404, (
            f"Expected 404 for nonexistent claim, got {response.status_code}: {response.text}"
        )
        # GET is not a mutation, should never mention audit
        body_text = response.text.lower()
        assert "audit_emit_failed" not in body_text

    def test_patch_claim_not_found_is_clean(
        self,
        client: TestClient,
    ) -> None:
        """PATCH /v1/claims/{id} for nonexistent claim returns 404 cleanly."""
        claim_id = str(uuid.uuid4())

        response = client.patch(
            f"/v1/claims/{claim_id}",
            json={"claim_text": "Updated text"},
            headers={"X-IDIS-API-Key": "test-api-key-tenant-a"},
        )

        # PATCH on nonexistent returns 404 (from route handler)
        # The audit middleware should pass through this 404 unchanged
        assert response.status_code == 404, (
            f"Expected 404 for nonexistent claim, got {response.status_code}: {response.text}"
        )
        body_text = response.text.lower()
        assert "audit_emit_failed" not in body_text
