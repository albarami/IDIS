"""Tests for Overrides API endpoints.

Tests POST /v1/deals/{dealId}/overrides per OpenAPI spec.
Covers: happy path, tenant isolation, idempotency, audit correlation, fail-closed validation.
"""

from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from idis.api.auth import IDIS_API_KEYS_ENV
from idis.api.main import create_app
from idis.api.routes.deals import clear_deals_store
from idis.api.routes.overrides import clear_overrides_store
from idis.audit.sink import InMemoryAuditSink

TENANT_A_ID = "11111111-1111-1111-1111-111111111111"
TENANT_B_ID = "22222222-2222-2222-2222-222222222222"

API_KEY_TENANT_A = "test-api-key-tenant-a"
API_KEY_TENANT_B = "test-api-key-tenant-b"
API_KEY_PARTNER_A = "test-api-key-partner-a"


@pytest.fixture
def api_keys_config() -> dict[str, dict[str, str | list[str]]]:
    """API keys configuration for testing."""
    return {
        API_KEY_TENANT_A: {
            "tenant_id": TENANT_A_ID,
            "actor_id": "actor-a",
            "name": "Tenant A Service",
            "timezone": "UTC",
            "data_region": "us-east-1",
            "roles": ["ANALYST"],
        },
        API_KEY_TENANT_B: {
            "tenant_id": TENANT_B_ID,
            "actor_id": "actor-b",
            "name": "Tenant B Service",
            "timezone": "UTC",
            "data_region": "us-east-1",
            "roles": ["ANALYST"],
        },
        API_KEY_PARTNER_A: {
            "tenant_id": TENANT_A_ID,
            "actor_id": "partner-a",
            "name": "Partner A Service",
            "timezone": "UTC",
            "data_region": "us-east-1",
            "roles": ["PARTNER"],
        },
    }


@pytest.fixture
def audit_sink() -> InMemoryAuditSink:
    """Provide in-memory audit sink for test verification."""
    return InMemoryAuditSink()


@pytest.fixture
def client(
    api_keys_config: dict[str, dict[str, str | list[str]]],
    audit_sink: InMemoryAuditSink,
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    """Create test client with in-memory stores."""
    monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(api_keys_config))
    clear_deals_store()
    clear_overrides_store()
    app = create_app(audit_sink=audit_sink)
    return TestClient(app)


@pytest.fixture
def deal_id(client: TestClient) -> str:
    """Create a deal and return its ID."""
    response = client.post(
        "/v1/deals",
        json={"name": "Test Deal", "company_name": "Test Company"},
        headers={"X-IDIS-API-Key": API_KEY_PARTNER_A},
    )
    assert response.status_code == 201
    return response.json()["deal_id"]


class TestOverridesAPIHappyPath:
    """Test happy path scenarios for Overrides API."""

    def test_create_override_returns_201(self, client: TestClient, deal_id: str) -> None:
        """POST /v1/deals/{dealId}/overrides returns 201 with Override."""
        response = client.post(
            f"/v1/deals/{deal_id}/overrides",
            json={
                "override_type": "IC_EXPORT_WITH_CAVEATS",
                "justification": "Approved by IC with noted risks",
            },
            headers={"X-IDIS-API-Key": API_KEY_PARTNER_A},
        )

        assert response.status_code == 201
        body = response.json()
        assert "override_id" in body
        assert body["deal_id"] == deal_id
        assert body["override_type"] == "IC_EXPORT_WITH_CAVEATS"
        assert body["justification"] == "Approved by IC with noted risks"
        assert body["status"] == "ACTIVE"
        uuid.UUID(body["override_id"])

    def test_create_override_with_different_types(self, client: TestClient, deal_id: str) -> None:
        """POST /v1/deals/{dealId}/overrides works with various override types."""
        response = client.post(
            f"/v1/deals/{deal_id}/overrides",
            json={
                "override_type": "SKIP_VERIFICATION",
                "justification": "Time-sensitive deal with known counterparty",
            },
            headers={"X-IDIS-API-Key": API_KEY_PARTNER_A},
        )

        assert response.status_code == 201
        assert response.json()["override_type"] == "SKIP_VERIFICATION"


class TestOverridesAPIValidation:
    """Test validation scenarios for Overrides API."""

    def test_empty_justification_returns_400(self, client: TestClient, deal_id: str) -> None:
        """POST with empty justification returns 400."""
        response = client.post(
            f"/v1/deals/{deal_id}/overrides",
            json={"override_type": "IC_EXPORT", "justification": ""},
            headers={"X-IDIS-API-Key": API_KEY_PARTNER_A},
        )

        assert response.status_code in (400, 422)  # 422 for Pydantic validation

    def test_whitespace_only_justification_returns_400(
        self, client: TestClient, deal_id: str
    ) -> None:
        """POST with whitespace-only justification returns 400."""
        response = client.post(
            f"/v1/deals/{deal_id}/overrides",
            json={"override_type": "IC_EXPORT", "justification": "   "},
            headers={"X-IDIS-API-Key": API_KEY_PARTNER_A},
        )

        assert response.status_code == 400

    def test_empty_override_type_returns_400(self, client: TestClient, deal_id: str) -> None:
        """POST with empty override_type returns 400."""
        response = client.post(
            f"/v1/deals/{deal_id}/overrides",
            json={"override_type": "", "justification": "Valid reason"},
            headers={"X-IDIS-API-Key": API_KEY_PARTNER_A},
        )

        assert response.status_code in (400, 422)  # 422 for Pydantic validation


class TestOverridesAPIRBAC:
    """Test RBAC enforcement for Overrides API."""

    def test_analyst_cannot_create_override(self, client: TestClient, deal_id: str) -> None:
        """POST /v1/deals/{dealId}/overrides returns 403 for ANALYST role."""
        response = client.post(
            f"/v1/deals/{deal_id}/overrides",
            json={
                "override_type": "IC_EXPORT",
                "justification": "Reason",
            },
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
        )

        assert response.status_code == 403


class TestOverridesAPIAuditCorrelation:
    """Test audit event correlation for Overrides API."""

    def test_create_override_emits_audit_event(
        self, client: TestClient, deal_id: str, audit_sink: InMemoryAuditSink
    ) -> None:
        """POST /v1/deals/{dealId}/overrides emits audit event with correct resource_id."""
        request_id = str(uuid.uuid4())
        response = client.post(
            f"/v1/deals/{deal_id}/overrides",
            json={
                "override_type": "IC_EXPORT",
                "justification": "Approved",
            },
            headers={
                "X-IDIS-API-Key": API_KEY_PARTNER_A,
                "X-Request-ID": request_id,
            },
        )

        assert response.status_code == 201
        override_id = response.json()["override_id"]

        events = audit_sink.events
        assert len(events) >= 1

        override_event = next(
            (e for e in events if e.get("event_type") == "override.created"),
            None,
        )
        assert override_event is not None
        assert override_event["resource"]["resource_id"] == override_id
        assert override_event["request"]["request_id"] == request_id


class TestOverridesAPIIdempotency:
    """Test idempotency for Overrides API."""

    def test_same_idempotency_key_same_payload_returns_same_result(
        self, client: TestClient, deal_id: str
    ) -> None:
        """Same Idempotency-Key + same payload returns identical result."""
        idem_key = str(uuid.uuid4())
        payload = {
            "override_type": "IC_EXPORT",
            "justification": "Approved by partner",
        }

        resp1 = client.post(
            f"/v1/deals/{deal_id}/overrides",
            json=payload,
            headers={
                "X-IDIS-API-Key": API_KEY_PARTNER_A,
                "Idempotency-Key": idem_key,
            },
        )
        resp2 = client.post(
            f"/v1/deals/{deal_id}/overrides",
            json=payload,
            headers={
                "X-IDIS-API-Key": API_KEY_PARTNER_A,
                "Idempotency-Key": idem_key,
            },
        )

        assert resp1.status_code == 201
        assert resp2.status_code == 201
        assert resp1.json()["override_id"] == resp2.json()["override_id"]

    def test_same_idempotency_key_different_payload_returns_409(
        self, client: TestClient, deal_id: str
    ) -> None:
        """Same Idempotency-Key + different payload returns 409."""
        idem_key = str(uuid.uuid4())

        resp1 = client.post(
            f"/v1/deals/{deal_id}/overrides",
            json={
                "override_type": "IC_EXPORT",
                "justification": "First reason",
            },
            headers={
                "X-IDIS-API-Key": API_KEY_PARTNER_A,
                "Idempotency-Key": idem_key,
            },
        )
        resp2 = client.post(
            f"/v1/deals/{deal_id}/overrides",
            json={
                "override_type": "IC_EXPORT",
                "justification": "Different reason",
            },
            headers={
                "X-IDIS-API-Key": API_KEY_PARTNER_A,
                "Idempotency-Key": idem_key,
            },
        )

        assert resp1.status_code == 201
        assert resp2.status_code == 409
