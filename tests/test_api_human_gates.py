"""Tests for Human Gates API endpoints.

Tests GET/POST /v1/deals/{dealId}/human-gates per OpenAPI spec.
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
from idis.api.routes.human_gates import clear_human_gates_store, create_test_gate
from idis.audit.sink import InMemoryAuditSink

TENANT_A_ID = "11111111-1111-1111-1111-111111111111"
TENANT_B_ID = "22222222-2222-2222-2222-222222222222"

API_KEY_TENANT_A = "test-api-key-tenant-a"
API_KEY_TENANT_B = "test-api-key-tenant-b"


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
    clear_human_gates_store()
    app = create_app(audit_sink=audit_sink)
    return TestClient(app)


@pytest.fixture
def deal_id(client: TestClient) -> str:
    """Create a deal and return its ID."""
    response = client.post(
        "/v1/deals",
        json={"name": "Test Deal", "company_name": "Test Company"},
        headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
    )
    assert response.status_code == 201
    return response.json()["deal_id"]


@pytest.fixture
def gate_id(deal_id: str) -> str:
    """Create a test gate and return its ID."""
    gate_id = str(uuid.uuid4())
    create_test_gate(
        gate_id=gate_id,
        tenant_id=TENANT_A_ID,
        deal_id=deal_id,
        gate_type="CLAIM_VERIFICATION",
    )
    return gate_id


class TestHumanGatesAPIHappyPath:
    """Test happy path scenarios for Human Gates API."""

    def test_list_human_gates_returns_empty_initially(
        self, client: TestClient, deal_id: str
    ) -> None:
        """GET /v1/deals/{dealId}/human-gates returns empty list initially."""
        response = client.get(
            f"/v1/deals/{deal_id}/human-gates",
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
        )

        assert response.status_code == 200
        body = response.json()
        assert "items" in body
        assert body["items"] == []

    def test_list_human_gates_includes_created_gate(
        self, client: TestClient, deal_id: str, gate_id: str
    ) -> None:
        """GET /v1/deals/{dealId}/human-gates includes test gate."""
        response = client.get(
            f"/v1/deals/{deal_id}/human-gates",
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
        )

        assert response.status_code == 200
        body = response.json()
        assert len(body["items"]) == 1
        assert body["items"][0]["gate_id"] == gate_id
        assert body["items"][0]["status"] == "PENDING"

    def test_submit_human_gate_action_returns_201(
        self, client: TestClient, deal_id: str, gate_id: str
    ) -> None:
        """POST /v1/deals/{dealId}/human-gates returns 201 with HumanGateAction."""
        response = client.post(
            f"/v1/deals/{deal_id}/human-gates",
            json={"gate_id": gate_id, "action": "APPROVE", "notes": "Looks good"},
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
        )

        assert response.status_code == 201
        body = response.json()
        assert "action_id" in body
        assert body["gate_id"] == gate_id
        assert body["action"] == "APPROVE"
        uuid.UUID(body["action_id"])

    def test_submit_reject_action(self, client: TestClient, deal_id: str, gate_id: str) -> None:
        """POST with REJECT action works correctly."""
        response = client.post(
            f"/v1/deals/{deal_id}/human-gates",
            json={"gate_id": gate_id, "action": "REJECT", "notes": "Issues found"},
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
        )

        assert response.status_code == 201
        assert response.json()["action"] == "REJECT"

    def test_submit_correct_action(self, client: TestClient, deal_id: str, gate_id: str) -> None:
        """POST with CORRECT action works correctly."""
        response = client.post(
            f"/v1/deals/{deal_id}/human-gates",
            json={"gate_id": gate_id, "action": "CORRECT"},
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
        )

        assert response.status_code == 201
        assert response.json()["action"] == "CORRECT"


class TestHumanGatesAPITenantIsolation:
    """Test tenant isolation for Human Gates API."""

    def test_cross_tenant_list_returns_empty(
        self, client: TestClient, deal_id: str, gate_id: str
    ) -> None:
        """GET /v1/deals/{dealId}/human-gates returns empty for cross-tenant."""
        response = client.get(
            f"/v1/deals/{deal_id}/human-gates",
            headers={"X-IDIS-API-Key": API_KEY_TENANT_B},
        )

        assert response.status_code == 200
        assert response.json()["items"] == []

    def test_cross_tenant_submit_action_returns_404(
        self, client: TestClient, deal_id: str, gate_id: str
    ) -> None:
        """POST /v1/deals/{dealId}/human-gates returns 404 for cross-tenant gate."""
        response = client.post(
            f"/v1/deals/{deal_id}/human-gates",
            json={"gate_id": gate_id, "action": "APPROVE"},
            headers={"X-IDIS-API-Key": API_KEY_TENANT_B},
        )

        assert response.status_code == 404


class TestHumanGatesAPIValidation:
    """Test validation scenarios for Human Gates API."""

    def test_invalid_action_returns_400(
        self, client: TestClient, deal_id: str, gate_id: str
    ) -> None:
        """POST with invalid action returns 400."""
        response = client.post(
            f"/v1/deals/{deal_id}/human-gates",
            json={"gate_id": gate_id, "action": "INVALID"},
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
        )

        assert response.status_code == 400
        body = response.json()
        assert body["code"] == "INVALID_REQUEST"

    def test_nonexistent_gate_returns_404(self, client: TestClient, deal_id: str) -> None:
        """POST with nonexistent gate_id returns 404."""
        fake_gate_id = str(uuid.uuid4())
        response = client.post(
            f"/v1/deals/{deal_id}/human-gates",
            json={"gate_id": fake_gate_id, "action": "APPROVE"},
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
        )

        assert response.status_code == 404

    def test_limit_out_of_range_returns_400(self, client: TestClient, deal_id: str) -> None:
        """GET with limit > 200 returns 400."""
        response = client.get(
            f"/v1/deals/{deal_id}/human-gates?limit=500",
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
        )

        assert response.status_code == 400
        body = response.json()
        assert body["code"] == "INVALID_LIMIT"

    def test_missing_gate_id_returns_400(self, client: TestClient, deal_id: str) -> None:
        """POST with missing gate_id field returns 400."""
        response = client.post(
            f"/v1/deals/{deal_id}/human-gates",
            json={"action": "APPROVE"},
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
        )

        assert response.status_code == 400
        body = response.json()
        assert body["code"] == "INVALID_REQUEST"
        assert "request_id" in body

    def test_missing_action_returns_400(
        self, client: TestClient, deal_id: str, gate_id: str
    ) -> None:
        """POST with missing action field returns 400."""
        response = client.post(
            f"/v1/deals/{deal_id}/human-gates",
            json={"gate_id": gate_id},
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
        )

        assert response.status_code == 400
        body = response.json()
        assert body["code"] == "INVALID_REQUEST"
        assert "request_id" in body

    def test_invalid_cursor_returns_400(self, client: TestClient, deal_id: str) -> None:
        """GET with invalid cursor returns 400."""
        response = client.get(
            f"/v1/deals/{deal_id}/human-gates?cursor=not-a-timestamp",
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
        )

        assert response.status_code == 400
        body = response.json()
        assert body["code"] == "INVALID_CURSOR"
        assert "request_id" in body


class TestHumanGatesAPIAuditCorrelation:
    """Test audit event correlation for Human Gates API."""

    def test_submit_action_emits_audit_event(
        self, client: TestClient, deal_id: str, gate_id: str, audit_sink: InMemoryAuditSink
    ) -> None:
        """POST /v1/deals/{dealId}/human-gates emits audit event with correct resource_id."""
        request_id = str(uuid.uuid4())
        response = client.post(
            f"/v1/deals/{deal_id}/human-gates",
            json={"gate_id": gate_id, "action": "APPROVE"},
            headers={
                "X-IDIS-API-Key": API_KEY_TENANT_A,
                "X-Request-ID": request_id,
            },
        )

        assert response.status_code == 201
        action_id = response.json()["action_id"]

        events = audit_sink.events
        assert len(events) >= 1

        gate_event = next(
            (e for e in events if e.get("event_type") == "human_gate.action.submitted"),
            None,
        )
        assert gate_event is not None
        assert gate_event["resource"]["resource_id"] == action_id
        assert gate_event["request"]["request_id"] == request_id


class TestHumanGatesAPIIdempotency:
    """Test idempotency for Human Gates API."""

    def test_same_idempotency_key_same_payload_returns_same_result(
        self, client: TestClient, deal_id: str, gate_id: str
    ) -> None:
        """Same Idempotency-Key + same payload returns identical result."""
        idem_key = str(uuid.uuid4())
        payload = {"gate_id": gate_id, "action": "APPROVE"}

        resp1 = client.post(
            f"/v1/deals/{deal_id}/human-gates",
            json=payload,
            headers={
                "X-IDIS-API-Key": API_KEY_TENANT_A,
                "Idempotency-Key": idem_key,
            },
        )
        resp2 = client.post(
            f"/v1/deals/{deal_id}/human-gates",
            json=payload,
            headers={
                "X-IDIS-API-Key": API_KEY_TENANT_A,
                "Idempotency-Key": idem_key,
            },
        )

        assert resp1.status_code == 201
        assert resp2.status_code == 201
        assert resp1.json()["action_id"] == resp2.json()["action_id"]

    def test_same_idempotency_key_different_payload_returns_409(
        self, client: TestClient, deal_id: str, gate_id: str
    ) -> None:
        """Same Idempotency-Key + different payload returns 409."""
        idem_key = str(uuid.uuid4())

        resp1 = client.post(
            f"/v1/deals/{deal_id}/human-gates",
            json={"gate_id": gate_id, "action": "APPROVE"},
            headers={
                "X-IDIS-API-Key": API_KEY_TENANT_A,
                "Idempotency-Key": idem_key,
            },
        )
        resp2 = client.post(
            f"/v1/deals/{deal_id}/human-gates",
            json={"gate_id": gate_id, "action": "REJECT"},
            headers={
                "X-IDIS-API-Key": API_KEY_TENANT_A,
                "Idempotency-Key": idem_key,
            },
        )

        assert resp1.status_code == 201
        assert resp2.status_code == 409
