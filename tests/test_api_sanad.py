"""API tests for Sanad endpoints.

Phase 3.4 required tests per roadmap Task 3.4.
Tests sanad CRUD operations via HTTP endpoints with tenant isolation.
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
    clear_all_claims_stores,
    seed_claim_in_memory,
    seed_sanad_in_memory,
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
    app = create_app(audit_sink=sink)
    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_stores() -> None:
    """Clear in-memory stores before each test."""
    clear_all_claims_stores()
    clear_deals_in_memory_store()


class TestGetSanad:
    """Tests for GET /v1/sanads/{sanadId}."""

    def test_get_sanad_returns_200_for_existing_sanad(self, client: TestClient) -> None:
        """GET returns 200 and sanad data for existing sanad."""
        deal_id = str(uuid.uuid4())
        claim_id = str(uuid.uuid4())
        sanad_id = str(uuid.uuid4())

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

        seed_claim_in_memory(
            {
                "claim_id": claim_id,
                "tenant_id": TENANT_A_ID,
                "deal_id": deal_id,
                "claim_class": "FINANCIAL",
                "claim_text": "Revenue is $10M",
                "claim_grade": "B",
                "corroboration": {"level": "AHAD", "independent_chain_count": 1},
                "claim_verdict": "UNVERIFIED",
                "claim_action": "VERIFY",
                "sanad_id": sanad_id,
                "created_at": "2026-01-10T00:00:00Z",
            }
        )

        seed_sanad_in_memory(
            {
                "sanad_id": sanad_id,
                "tenant_id": TENANT_A_ID,
                "claim_id": claim_id,
                "deal_id": deal_id,
                "primary_evidence_id": str(uuid.uuid4()),
                "corroborating_evidence_ids": [],
                "transmission_chain": [
                    {
                        "node_id": str(uuid.uuid4()),
                        "node_type": "EXTRACTION",
                        "actor_type": "SYSTEM",
                        "actor_id": "extractor",
                        "input_refs": [],
                        "output_refs": [],
                        "timestamp": "2026-01-10T00:00:00Z",
                    }
                ],
                "computed": {
                    "grade": "B",
                    "grade_rationale": "Base B, 0 MAJOR defects",
                    "corroboration_level": "AHAD_1",
                    "independent_chain_count": 1,
                },
                "created_at": "2026-01-10T00:00:00Z",
            }
        )

        response = client.get(
            f"/v1/sanads/{sanad_id}",
            headers={"X-IDIS-API-Key": TENANT_A_KEY},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["sanad_id"] == sanad_id
        assert data["claim_id"] == claim_id
        assert data["computed"]["grade"] == "B"

    def test_get_sanad_returns_404_for_nonexistent(self, client: TestClient) -> None:
        """GET returns 404 for nonexistent sanad."""
        response = client.get(
            f"/v1/sanads/{uuid.uuid4()}",
            headers={"X-IDIS-API-Key": TENANT_A_KEY},
        )
        assert response.status_code == 404

    def test_get_sanad_returns_404_for_cross_tenant(self, client: TestClient) -> None:
        """GET returns 404 for sanad belonging to different tenant."""
        sanad_id = str(uuid.uuid4())

        seed_sanad_in_memory(
            {
                "sanad_id": sanad_id,
                "tenant_id": TENANT_B_ID,
                "claim_id": str(uuid.uuid4()),
                "deal_id": str(uuid.uuid4()),
                "primary_evidence_id": str(uuid.uuid4()),
                "corroborating_evidence_ids": [],
                "transmission_chain": [],
                "computed": {
                    "grade": "D",
                    "corroboration_level": "AHAD_1",
                    "independent_chain_count": 1,
                },
                "created_at": "2026-01-10T00:00:00Z",
            }
        )

        response = client.get(
            f"/v1/sanads/{sanad_id}",
            headers={"X-IDIS-API-Key": TENANT_A_KEY},
        )
        assert response.status_code == 404


class TestListDealSanads:
    """Tests for GET /v1/deals/{dealId}/sanads."""

    def test_list_sanads_returns_empty_for_deal_without_sanads(self, client: TestClient) -> None:
        """GET returns empty list for deal without sanads."""
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
            f"/v1/deals/{deal_id}/sanads",
            headers={"X-IDIS-API-Key": TENANT_A_KEY},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["items"] == []
        assert data.get("next_cursor") is None

    def test_list_sanads_returns_404_for_nonexistent_deal(self, client: TestClient) -> None:
        """GET returns 404 for nonexistent deal."""
        response = client.get(
            f"/v1/deals/{uuid.uuid4()}/sanads",
            headers={"X-IDIS-API-Key": TENANT_A_KEY},
        )
        assert response.status_code == 404


class TestCreateSanad:
    """Tests for POST /v1/deals/{dealId}/sanads."""

    def test_create_sanad_returns_201(
        self, client: TestClient, audit_sink: InMemoryAuditSink
    ) -> None:
        """POST creates sanad and returns 201."""
        deal_id = str(uuid.uuid4())
        claim_id = str(uuid.uuid4())

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
            f"/v1/deals/{deal_id}/sanads",
            headers={"X-IDIS-API-Key": TENANT_A_KEY},
            json={
                "claim_id": claim_id,
                "primary_evidence_id": str(uuid.uuid4()),
                "extraction_confidence": 0.95,
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert data["claim_id"] == claim_id
        assert data["deal_id"] == deal_id
        assert "sanad_id" in data
        assert data["computed"]["grade"] in ("A", "B", "C", "D")

    def test_create_sanad_returns_404_for_nonexistent_deal(self, client: TestClient) -> None:
        """POST returns 404 for nonexistent deal."""
        response = client.post(
            f"/v1/deals/{uuid.uuid4()}/sanads",
            headers={"X-IDIS-API-Key": TENANT_A_KEY},
            json={
                "claim_id": str(uuid.uuid4()),
                "primary_evidence_id": str(uuid.uuid4()),
            },
        )
        assert response.status_code == 404


class TestUpdateSanad:
    """Tests for PATCH /v1/sanads/{sanadId}."""

    def test_update_sanad_changes_corroboration(self, client: TestClient) -> None:
        """PATCH updates sanad corroboration."""
        deal_id = str(uuid.uuid4())
        claim_id = str(uuid.uuid4())
        sanad_id = str(uuid.uuid4())

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

        seed_sanad_in_memory(
            {
                "sanad_id": sanad_id,
                "tenant_id": TENANT_A_ID,
                "claim_id": claim_id,
                "deal_id": deal_id,
                "primary_evidence_id": str(uuid.uuid4()),
                "corroborating_evidence_ids": [],
                "transmission_chain": [
                    {
                        "node_id": str(uuid.uuid4()),
                        "node_type": "EXTRACTION",
                        "actor_type": "SYSTEM",
                        "actor_id": "extractor",
                        "input_refs": [],
                        "output_refs": [],
                        "timestamp": "2026-01-10T00:00:00Z",
                    }
                ],
                "computed": {
                    "grade": "B",
                    "grade_rationale": "Base B",
                    "corroboration_level": "AHAD_1",
                    "independent_chain_count": 1,
                },
                "created_at": "2026-01-10T00:00:00Z",
            }
        )

        new_evidence_ids = [str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())]

        response = client.patch(
            f"/v1/sanads/{sanad_id}",
            headers={"X-IDIS-API-Key": TENANT_A_KEY},
            json={"corroborating_evidence_ids": new_evidence_ids},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["corroborating_evidence_ids"] == new_evidence_ids

    def test_update_sanad_returns_404_for_nonexistent(self, client: TestClient) -> None:
        """PATCH returns 404 for nonexistent sanad."""
        response = client.patch(
            f"/v1/sanads/{uuid.uuid4()}",
            headers={"X-IDIS-API-Key": TENANT_A_KEY},
            json={"corroborating_evidence_ids": []},
        )
        assert response.status_code == 404


class TestSetCorroboration:
    """Tests for POST /v1/sanads/{sanadId}/corroboration."""

    def test_set_corroboration_updates_grade(self, client: TestClient) -> None:
        """POST corroboration triggers grade re-computation."""
        deal_id = str(uuid.uuid4())
        claim_id = str(uuid.uuid4())
        sanad_id = str(uuid.uuid4())

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

        seed_sanad_in_memory(
            {
                "sanad_id": sanad_id,
                "tenant_id": TENANT_A_ID,
                "claim_id": claim_id,
                "deal_id": deal_id,
                "primary_evidence_id": str(uuid.uuid4()),
                "corroborating_evidence_ids": [],
                "transmission_chain": [
                    {
                        "node_id": str(uuid.uuid4()),
                        "node_type": "EXTRACTION",
                        "actor_type": "SYSTEM",
                        "actor_id": "extractor",
                        "input_refs": [],
                        "output_refs": [],
                        "timestamp": "2026-01-10T00:00:00Z",
                    }
                ],
                "computed": {
                    "grade": "B",
                    "grade_rationale": "Base B",
                    "corroboration_level": "AHAD_1",
                    "independent_chain_count": 1,
                },
                "created_at": "2026-01-10T00:00:00Z",
            }
        )

        new_evidence_ids = [str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())]

        response = client.post(
            f"/v1/sanads/{sanad_id}/corroboration",
            headers={"X-IDIS-API-Key": TENANT_A_KEY},
            json={"corroborating_evidence_ids": new_evidence_ids},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["computed"]["corroboration_level"] == "MUTAWATIR"
        assert data["computed"]["independent_chain_count"] == 4
