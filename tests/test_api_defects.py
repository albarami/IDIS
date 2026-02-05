"""API tests for Defects endpoints.

Phase 3.4 required tests per roadmap Task 3.4.
Tests defect CRUD, waiver, and cure operations via HTTP endpoints.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from idis.api.main import create_app
from idis.api.policy import Role
from idis.audit.sink import InMemoryAuditSink, JsonlFileAuditSink
from idis.persistence.repositories.claims import (
    _defects_in_memory_store,
    clear_all_claims_stores,
)
from idis.persistence.repositories.deals import (
    clear_deals_in_memory_store,
    seed_deal_in_memory,
)

TENANT_A_KEY = "test-api-key-tenant-a"
TENANT_B_KEY = "test-api-key-tenant-b"
TENANT_A_ID = "00000000-0000-0000-0000-000000000001"
TENANT_B_ID = "00000000-0000-0000-0000-000000000002"


def _make_api_keys_json(
    tenant_id: str,
    api_key: str,
    actor_id: str | None = None,
    name: str = "Test Tenant",
    roles: list[str] | None = None,
) -> str:
    """Create a valid IDIS_API_KEYS_JSON value for testing."""
    if actor_id is None:
        actor_id = f"actor-{tenant_id[:8]}"
    if roles is None:
        roles = [Role.ANALYST.value, Role.PARTNER.value]
    return json.dumps(
        {
            api_key: {
                "tenant_id": tenant_id,
                "actor_id": actor_id,
                "name": name,
                "timezone": "UTC",
                "data_region": "us-east-1",
                "roles": roles,
            }
        }
    )


@pytest.fixture
def audit_sink() -> InMemoryAuditSink:
    """Provide in-memory audit sink for testing."""
    return InMemoryAuditSink()


@pytest.fixture
def client(
    audit_sink: InMemoryAuditSink, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    """Create test client with in-memory stores."""
    audit_log_path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("IDIS_API_KEYS_JSON", _make_api_keys_json(TENANT_A_ID, TENANT_A_KEY))
    monkeypatch.setenv("IDIS_AUDIT_LOG_PATH", str(audit_log_path))
    sink = JsonlFileAuditSink(file_path=str(audit_log_path))
    app = create_app(audit_sink=sink, service_region="us-east-1")
    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_stores() -> None:
    """Clear in-memory stores before each test."""
    clear_all_claims_stores()
    clear_deals_in_memory_store()


def seed_defect_in_memory(defect_data: dict) -> None:
    """Seed a defect into the in-memory store."""
    _defects_in_memory_store[defect_data["defect_id"]] = defect_data


class TestGetDefect:
    """Tests for GET /v1/defects/{defectId}."""

    def test_get_defect_returns_200_for_existing(self, client: TestClient) -> None:
        """GET returns 200 and defect data for existing defect."""
        defect_id = str(uuid.uuid4())

        seed_defect_in_memory(
            {
                "defect_id": defect_id,
                "tenant_id": TENANT_A_ID,
                "claim_id": str(uuid.uuid4()),
                "deal_id": str(uuid.uuid4()),
                "defect_type": "BROKEN_CHAIN",
                "severity": "FATAL",
                "description": "Evidence chain is broken",
                "cure_protocol": "RECONSTRUCT_CHAIN",
                "status": "OPEN",
                "waived": False,
                "created_at": "2026-01-10T00:00:00Z",
            }
        )

        response = client.get(
            f"/v1/defects/{defect_id}",
            headers={"X-IDIS-API-Key": TENANT_A_KEY},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["defect_id"] == defect_id
        assert data["defect_type"] == "BROKEN_CHAIN"
        assert data["severity"] == "FATAL"

    def test_get_defect_returns_404_for_nonexistent(self, client: TestClient) -> None:
        """GET returns 404 for nonexistent defect."""
        response = client.get(
            f"/v1/defects/{uuid.uuid4()}",
            headers={"X-IDIS-API-Key": TENANT_A_KEY},
        )
        assert response.status_code == 404

    def test_get_defect_returns_404_for_cross_tenant(self, client: TestClient) -> None:
        """GET returns 404 for defect belonging to different tenant."""
        defect_id = str(uuid.uuid4())

        seed_defect_in_memory(
            {
                "defect_id": defect_id,
                "tenant_id": TENANT_B_ID,
                "claim_id": str(uuid.uuid4()),
                "deal_id": str(uuid.uuid4()),
                "defect_type": "INCONSISTENCY",
                "severity": "MAJOR",
                "description": "Test defect",
                "cure_protocol": "HUMAN_ARBITRATION",
                "status": "OPEN",
                "waived": False,
                "created_at": "2026-01-10T00:00:00Z",
            }
        )

        response = client.get(
            f"/v1/defects/{defect_id}",
            headers={"X-IDIS-API-Key": TENANT_A_KEY},
        )
        assert response.status_code == 404


class TestListDealDefects:
    """Tests for GET /v1/deals/{dealId}/defects."""

    def test_list_defects_returns_empty_for_deal_without_defects(self, client: TestClient) -> None:
        """GET returns empty list for deal without defects."""
        deal_id = str(uuid.uuid4())

        seed_deal_in_memory(
            {
                "deal_id": deal_id,
                "tenant_id": TENANT_A_ID,
                "name": "Test Deal",
                "status": "ACTIVE",
                "stage": "SCREENING",
                "created_at": "2026-01-10T00:00:00Z",
            }
        )

        response = client.get(
            f"/v1/deals/{deal_id}/defects",
            headers={"X-IDIS-API-Key": TENANT_A_KEY},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["items"] == []

    def test_list_defects_returns_200_empty_for_nonexistent_deal(self, client: TestClient) -> None:
        """GET returns 200 empty for nonexistent deal (tenant isolation - no existence leak)."""
        response = client.get(
            f"/v1/deals/{uuid.uuid4()}/defects",
            headers={"X-IDIS-API-Key": TENANT_A_KEY},
        )
        # Per TI-001 tenant isolation: return 200 empty, not 404
        assert response.status_code == 200
        data = response.json()
        assert data["items"] == []


class TestCreateDefect:
    """Tests for POST /v1/deals/{dealId}/defects."""

    def test_create_defect_returns_201(self, client: TestClient) -> None:
        """POST creates defect and returns 201."""
        deal_id = str(uuid.uuid4())

        seed_deal_in_memory(
            {
                "deal_id": deal_id,
                "tenant_id": TENANT_A_ID,
                "name": "Test Deal",
                "status": "ACTIVE",
                "stage": "SCREENING",
                "created_at": "2026-01-10T00:00:00Z",
            }
        )

        response = client.post(
            f"/v1/deals/{deal_id}/defects",
            headers={"X-IDIS-API-Key": TENANT_A_KEY},
            json={
                "defect_type": "BROKEN_CHAIN",
                "description": "Evidence chain is incomplete",
                "cure_protocol": "RECONSTRUCT_CHAIN",
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert data["defect_type"] == "BROKEN_CHAIN"
        assert data["severity"] == "FATAL"
        assert data["status"] == "OPEN"
        assert data["deal_id"] == deal_id

    def test_create_defect_uses_severity_matrix(self, client: TestClient) -> None:
        """POST applies severity matrix for defect types."""
        deal_id = str(uuid.uuid4())

        seed_deal_in_memory(
            {
                "deal_id": deal_id,
                "tenant_id": TENANT_A_ID,
                "name": "Test Deal",
                "status": "ACTIVE",
                "stage": "SCREENING",
                "created_at": "2026-01-10T00:00:00Z",
            }
        )

        fatal_response = client.post(
            f"/v1/deals/{deal_id}/defects",
            headers={"X-IDIS-API-Key": TENANT_A_KEY},
            json={
                "defect_type": "CIRCULARITY",
                "description": "Circular reference detected",
                "cure_protocol": "DISCARD_CLAIM",
            },
        )
        assert fatal_response.status_code == 201
        assert fatal_response.json()["severity"] == "FATAL"

        major_response = client.post(
            f"/v1/deals/{deal_id}/defects",
            headers={"X-IDIS-API-Key": TENANT_A_KEY},
            json={
                "defect_type": "INCONSISTENCY",
                "description": "Data inconsistency found",
                "cure_protocol": "HUMAN_ARBITRATION",
            },
        )
        assert major_response.status_code == 201
        assert major_response.json()["severity"] == "MAJOR"

        minor_response = client.post(
            f"/v1/deals/{deal_id}/defects",
            headers={"X-IDIS-API-Key": TENANT_A_KEY},
            json={
                "defect_type": "STALENESS",
                "description": "Data is outdated",
                "cure_protocol": "REQUEST_SOURCE",
            },
        )
        assert minor_response.status_code == 201
        assert minor_response.json()["severity"] == "MINOR"

    def test_create_defect_returns_404_for_nonexistent_deal(self, client: TestClient) -> None:
        """POST returns 404 for nonexistent deal."""
        response = client.post(
            f"/v1/deals/{uuid.uuid4()}/defects",
            headers={"X-IDIS-API-Key": TENANT_A_KEY},
            json={
                "defect_type": "BROKEN_CHAIN",
                "description": "Test",
                "cure_protocol": "RECONSTRUCT_CHAIN",
            },
        )
        assert response.status_code == 404


class TestWaiveDefect:
    """Tests for POST /v1/defects/{defectId}/waive."""

    def test_waive_defect_requires_actor_and_reason(self, client: TestClient) -> None:
        """POST waive requires actor and reason per DEF-001."""
        defect_id = str(uuid.uuid4())

        seed_defect_in_memory(
            {
                "defect_id": defect_id,
                "tenant_id": TENANT_A_ID,
                "claim_id": str(uuid.uuid4()),
                "deal_id": str(uuid.uuid4()),
                "defect_type": "INCONSISTENCY",
                "severity": "MAJOR",
                "description": "Test defect",
                "cure_protocol": "HUMAN_ARBITRATION",
                "status": "OPEN",
                "waived": False,
                "created_at": "2026-01-10T00:00:00Z",
            }
        )

        response = client.post(
            f"/v1/defects/{defect_id}/waive",
            headers={"X-IDIS-API-Key": TENANT_A_KEY},
            json={
                "actor": "partner@example.com",
                "reason": "Accepted business risk per IC discussion",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "WAIVED"
        assert data["waived_by"] == "partner@example.com"
        assert data["waiver_reason"] == "Accepted business risk per IC discussion"

    def test_waive_defect_fails_without_actor(self, client: TestClient) -> None:
        """POST waive fails with 422 when actor is empty."""
        defect_id = str(uuid.uuid4())

        seed_defect_in_memory(
            {
                "defect_id": defect_id,
                "tenant_id": TENANT_A_ID,
                "claim_id": str(uuid.uuid4()),
                "deal_id": str(uuid.uuid4()),
                "defect_type": "INCONSISTENCY",
                "severity": "MAJOR",
                "description": "Test defect",
                "cure_protocol": "HUMAN_ARBITRATION",
                "status": "OPEN",
                "waived": False,
                "created_at": "2026-01-10T00:00:00Z",
            }
        )

        response = client.post(
            f"/v1/defects/{defect_id}/waive",
            headers={"X-IDIS-API-Key": TENANT_A_KEY},
            json={
                "actor": "",
                "reason": "Some reason",
            },
        )

        assert response.status_code == 422

    def test_waive_defect_returns_404_for_nonexistent(self, client: TestClient) -> None:
        """POST waive returns 404 for nonexistent defect."""
        response = client.post(
            f"/v1/defects/{uuid.uuid4()}/waive",
            headers={"X-IDIS-API-Key": TENANT_A_KEY},
            json={
                "actor": "partner@example.com",
                "reason": "Test reason",
            },
        )
        assert response.status_code == 404


class TestCureDefect:
    """Tests for POST /v1/defects/{defectId}/cure."""

    def test_cure_defect_requires_actor_and_reason(self, client: TestClient) -> None:
        """POST cure requires actor and reason per DEF-001."""
        defect_id = str(uuid.uuid4())

        seed_defect_in_memory(
            {
                "defect_id": defect_id,
                "tenant_id": TENANT_A_ID,
                "claim_id": str(uuid.uuid4()),
                "deal_id": str(uuid.uuid4()),
                "defect_type": "STALENESS",
                "severity": "MINOR",
                "description": "Data is outdated",
                "cure_protocol": "REQUEST_SOURCE",
                "status": "OPEN",
                "waived": False,
                "created_at": "2026-01-10T00:00:00Z",
            }
        )

        response = client.post(
            f"/v1/defects/{defect_id}/cure",
            headers={"X-IDIS-API-Key": TENANT_A_KEY},
            json={
                "actor": "analyst@example.com",
                "reason": "Obtained updated data from company",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "CURED"
        assert data["cured_by"] == "analyst@example.com"
        assert data["cured_reason"] == "Obtained updated data from company"

    def test_cure_defect_returns_404_for_nonexistent(self, client: TestClient) -> None:
        """POST cure returns 404 for nonexistent defect."""
        response = client.post(
            f"/v1/defects/{uuid.uuid4()}/cure",
            headers={"X-IDIS-API-Key": TENANT_A_KEY},
            json={
                "actor": "analyst@example.com",
                "reason": "Fixed the issue",
            },
        )
        assert response.status_code == 404


class TestDefectStateTransitions:
    """Tests for defect state machine enforcement per DEF-001."""

    def test_waive_already_waived_returns_409(self, client: TestClient) -> None:
        """Waiving an already WAIVED defect returns 409 Conflict."""
        defect_id = str(uuid.uuid4())

        seed_defect_in_memory(
            {
                "defect_id": defect_id,
                "tenant_id": TENANT_A_ID,
                "claim_id": str(uuid.uuid4()),
                "deal_id": str(uuid.uuid4()),
                "defect_type": "INCONSISTENCY",
                "severity": "MAJOR",
                "description": "Test defect",
                "cure_protocol": "HUMAN_ARBITRATION",
                "status": "WAIVED",
                "waived_by": "someone@example.com",
                "waiver_reason": "Previously waived",
                "created_at": "2026-01-10T00:00:00Z",
            }
        )

        response = client.post(
            f"/v1/defects/{defect_id}/waive",
            headers={"X-IDIS-API-Key": TENANT_A_KEY},
            json={
                "actor": "partner@example.com",
                "reason": "Try to waive again",
            },
        )

        assert response.status_code == 409
        data = response.json()
        # Error response contains the transition info in the message
        assert "DEFECT_INVALID_STATE_TRANSITION" in data.get("message", str(data))
        assert "WAIVED" in data.get("message", str(data))

    def test_cure_already_cured_returns_409(self, client: TestClient) -> None:
        """Curing an already CURED defect returns 409 Conflict."""
        defect_id = str(uuid.uuid4())

        seed_defect_in_memory(
            {
                "defect_id": defect_id,
                "tenant_id": TENANT_A_ID,
                "claim_id": str(uuid.uuid4()),
                "deal_id": str(uuid.uuid4()),
                "defect_type": "STALENESS",
                "severity": "MINOR",
                "description": "Test defect",
                "cure_protocol": "REQUEST_SOURCE",
                "status": "CURED",
                "cured_by": "someone@example.com",
                "cured_reason": "Previously cured",
                "created_at": "2026-01-10T00:00:00Z",
            }
        )

        response = client.post(
            f"/v1/defects/{defect_id}/cure",
            headers={"X-IDIS-API-Key": TENANT_A_KEY},
            json={
                "actor": "analyst@example.com",
                "reason": "Try to cure again",
            },
        )

        assert response.status_code == 409
        data = response.json()
        # Error response contains the transition info in the message
        assert "DEFECT_INVALID_STATE_TRANSITION" in data.get("message", str(data))
        assert "CURED" in data.get("message", str(data))

    def test_waive_cured_defect_returns_409(self, client: TestClient) -> None:
        """Waiving a CURED defect returns 409 Conflict."""
        defect_id = str(uuid.uuid4())

        seed_defect_in_memory(
            {
                "defect_id": defect_id,
                "tenant_id": TENANT_A_ID,
                "claim_id": str(uuid.uuid4()),
                "deal_id": str(uuid.uuid4()),
                "defect_type": "STALENESS",
                "severity": "MINOR",
                "description": "Test defect",
                "cure_protocol": "REQUEST_SOURCE",
                "status": "CURED",
                "cured_by": "someone@example.com",
                "cured_reason": "Previously cured",
                "created_at": "2026-01-10T00:00:00Z",
            }
        )

        response = client.post(
            f"/v1/defects/{defect_id}/waive",
            headers={"X-IDIS-API-Key": TENANT_A_KEY},
            json={
                "actor": "partner@example.com",
                "reason": "Try to waive cured defect",
            },
        )

        assert response.status_code == 409
        data = response.json()
        # Error response contains the transition info in the message
        assert "DEFECT_INVALID_STATE_TRANSITION" in data.get("message", str(data))
        assert "CURED" in data.get("message", str(data))
