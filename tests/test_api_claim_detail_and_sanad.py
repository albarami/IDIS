"""Tests for IDIS API Claim Detail and Sanad Chain endpoints.

Phase 6.2: Frontend backend contracts - Claim Detail and Sanad Chain APIs.

Tests:
- GET /v1/claims/{claimId} returns 200 with correct claim body
- GET /v1/claims/{claimId}/sanad returns 200 with chain structure
- Stable ordering for sanad transmission chain
- Cross-tenant reads blocked (404)
- RBAC enforcement
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from idis.api.main import create_app
from idis.api.policy import Role
from idis.api.routes.claims import (
    clear_all_stores,
    seed_claim,
    seed_sanad,
)
from idis.api.routes.deals import clear_deals_store
from idis.audit.sink import JsonlFileAuditSink
from idis.persistence.repositories.deals import _in_memory_store as _deals_store


def _make_api_keys_json(
    tenant_id: str,
    actor_id: str | None = None,
    name: str = "Test Tenant",
    roles: list[str] | None = None,
) -> str:
    """Create a valid IDIS_API_KEYS_JSON value for testing with roles."""
    if actor_id is None:
        actor_id = f"actor-{tenant_id[:8]}"
    if roles is None:
        roles = [Role.ANALYST.value]
    return json.dumps(
        {
            "test-api-key": {
                "tenant_id": tenant_id,
                "actor_id": actor_id,
                "name": name,
                "timezone": "UTC",
                "data_region": "us-east-1",
                "roles": roles,
            }
        }
    )


def _seed_deal(tenant_id: str, deal_id: str) -> None:
    """Seed a deal into the store."""
    _deals_store[deal_id] = {
        "deal_id": deal_id,
        "tenant_id": tenant_id,
        "name": "Test Deal",
        "company_name": "Test Corp",
        "status": "NEW",
        "stage": "SEED",
        "tags": [],
        "created_at": "2026-01-10T00:00:00Z",
        "updated_at": None,
    }


def _seed_claim_with_sanad(
    tenant_id: str,
    deal_id: str,
    claim_id: str | None = None,
    sanad_id: str | None = None,
) -> tuple[str, str]:
    """Seed a claim with associated sanad. Returns (claim_id, sanad_id)."""
    if claim_id is None:
        claim_id = str(uuid.uuid4())
    if sanad_id is None:
        sanad_id = str(uuid.uuid4())

    evidence_id = str(uuid.uuid4())

    seed_claim(
        {
            "claim_id": claim_id,
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "claim_class": "FINANCIAL",
            "claim_text": "Revenue is $1M ARR",
            "claim_grade": "A",
            "claim_verdict": "VERIFIED",
            "claim_action": "NONE",
            "sanad_id": sanad_id,
            "corroboration": {"level": "MUTAWATIR", "independent_chain_count": 3},
            "defect_ids": [],
            "materiality": "HIGH",
            "ic_bound": True,
            "created_at": "2026-01-10T00:00:00Z",
        }
    )

    node_id_1 = str(uuid.uuid4())
    node_id_2 = str(uuid.uuid4())
    node_id_3 = str(uuid.uuid4())

    seed_sanad(
        {
            "sanad_id": sanad_id,
            "claim_id": claim_id,
            "deal_id": deal_id,
            "tenant_id": tenant_id,
            "primary_evidence_id": evidence_id,
            "corroborating_evidence_ids": [str(uuid.uuid4()), str(uuid.uuid4())],
            "transmission_chain": [
                {
                    "node_id": node_id_2,
                    "node_type": "EXTRACT",
                    "actor_type": "AGENT",
                    "actor_id": "extractor-v1",
                    "input_refs": [{"doc_id": evidence_id}],
                    "output_refs": [{"claim_id": claim_id}],
                    "timestamp": "2026-01-10T00:01:00Z",
                    "confidence": 0.95,
                    "dhabt_score": 0.9,
                    "verification_method": "auto",
                    "notes": None,
                },
                {
                    "node_id": node_id_1,
                    "node_type": "INGEST",
                    "actor_type": "SYSTEM",
                    "actor_id": "ingestion-service",
                    "input_refs": [],
                    "output_refs": [{"doc_id": evidence_id}],
                    "timestamp": "2026-01-10T00:00:00Z",
                    "confidence": 1.0,
                    "dhabt_score": 1.0,
                    "verification_method": "auto",
                    "notes": None,
                },
                {
                    "node_id": node_id_3,
                    "node_type": "HUMAN_VERIFY",
                    "actor_type": "HUMAN",
                    "actor_id": "analyst-001",
                    "input_refs": [{"claim_id": claim_id}],
                    "output_refs": [{"claim_id": claim_id, "verified": True}],
                    "timestamp": "2026-01-10T00:02:00Z",
                    "confidence": 1.0,
                    "dhabt_score": 1.0,
                    "verification_method": "human-verified",
                    "notes": "Verified against bank statements",
                },
            ],
            "computed": {
                "grade": "A",
                "grade_rationale": "Strong evidence chain with human verification",
                "corroboration_level": "MUTAWATIR",
                "independent_chain_count": 3,
            },
        }
    )

    return claim_id, sanad_id


@pytest.fixture(autouse=True)
def cleanup_stores() -> None:
    """Clean up stores before and after each test."""
    clear_deals_store()
    clear_all_stores()
    yield
    clear_deals_store()
    clear_all_stores()


class TestClaimDetailEndpoint:
    """Tests for GET /v1/claims/{claimId}."""

    def test_returns_200_with_correct_body(self, tmp_path: Path) -> None:
        """GET /v1/claims/{claimId} returns 200 with correct claim body."""
        tenant_id = str(uuid.uuid4())
        deal_id = str(uuid.uuid4())
        audit_log_path = tmp_path / "audit.jsonl"

        os.environ["IDIS_API_KEYS_JSON"] = _make_api_keys_json(tenant_id)
        os.environ["IDIS_AUDIT_LOG_PATH"] = str(audit_log_path)

        try:
            _seed_deal(tenant_id, deal_id)
            claim_id, _ = _seed_claim_with_sanad(tenant_id, deal_id)

            sink = JsonlFileAuditSink(file_path=str(audit_log_path))
            app = create_app(audit_sink=sink, service_region="us-east-1")
            client = TestClient(app, raise_server_exceptions=False)

            response = client.get(
                f"/v1/claims/{claim_id}",
                headers={"X-IDIS-API-Key": "test-api-key"},
            )

            assert response.status_code == 200, f"Got {response.status_code}: {response.text}"

            data = response.json()
            assert data["claim_id"] == claim_id
            assert data["deal_id"] == deal_id
            assert data["claim_class"] == "FINANCIAL"
            assert data["claim_text"] == "Revenue is $1M ARR"
            assert data["claim_grade"] == "A"
            assert data["claim_verdict"] == "VERIFIED"
            assert data["claim_action"] == "NONE"
            assert "corroboration" in data
            assert data["corroboration"]["level"] == "MUTAWATIR"
            assert data["corroboration"]["independent_chain_count"] == 3
            assert "created_at" in data

        finally:
            os.environ.pop("IDIS_API_KEYS_JSON", None)
            os.environ.pop("IDIS_AUDIT_LOG_PATH", None)

    def test_cross_tenant_read_blocked(self, tmp_path: Path) -> None:
        """Cross-tenant claim read should return 404."""
        tenant_id_1 = str(uuid.uuid4())
        tenant_id_2 = str(uuid.uuid4())
        deal_id = str(uuid.uuid4())
        audit_log_path = tmp_path / "audit.jsonl"

        _seed_deal(tenant_id_1, deal_id)
        claim_id, _ = _seed_claim_with_sanad(tenant_id_1, deal_id)

        os.environ["IDIS_API_KEYS_JSON"] = _make_api_keys_json(tenant_id_2)
        os.environ["IDIS_AUDIT_LOG_PATH"] = str(audit_log_path)

        try:
            sink = JsonlFileAuditSink(file_path=str(audit_log_path))
            app = create_app(audit_sink=sink, service_region="us-east-1")
            client = TestClient(app, raise_server_exceptions=False)

            response = client.get(
                f"/v1/claims/{claim_id}",
                headers={"X-IDIS-API-Key": "test-api-key"},
            )

            assert response.status_code == 404

        finally:
            os.environ.pop("IDIS_API_KEYS_JSON", None)
            os.environ.pop("IDIS_AUDIT_LOG_PATH", None)

    def test_claim_not_found_returns_404(self, tmp_path: Path) -> None:
        """Non-existent claim should return 404."""
        tenant_id = str(uuid.uuid4())
        claim_id = str(uuid.uuid4())
        audit_log_path = tmp_path / "audit.jsonl"

        os.environ["IDIS_API_KEYS_JSON"] = _make_api_keys_json(tenant_id)
        os.environ["IDIS_AUDIT_LOG_PATH"] = str(audit_log_path)

        try:
            sink = JsonlFileAuditSink(file_path=str(audit_log_path))
            app = create_app(audit_sink=sink, service_region="us-east-1")
            client = TestClient(app, raise_server_exceptions=False)

            response = client.get(
                f"/v1/claims/{claim_id}",
                headers={"X-IDIS-API-Key": "test-api-key"},
            )

            assert response.status_code == 404

        finally:
            os.environ.pop("IDIS_API_KEYS_JSON", None)
            os.environ.pop("IDIS_AUDIT_LOG_PATH", None)

    def test_claim_without_value_omits_value_field(self, tmp_path: Path) -> None:
        """Claim without value should omit 'value' field entirely (not null)."""
        tenant_id = str(uuid.uuid4())
        deal_id = str(uuid.uuid4())
        claim_id = str(uuid.uuid4())
        audit_log_path = tmp_path / "audit.jsonl"

        os.environ["IDIS_API_KEYS_JSON"] = _make_api_keys_json(tenant_id)
        os.environ["IDIS_AUDIT_LOG_PATH"] = str(audit_log_path)

        try:
            _seed_deal(tenant_id, deal_id)

            seed_claim(
                {
                    "claim_id": claim_id,
                    "tenant_id": tenant_id,
                    "deal_id": deal_id,
                    "claim_class": "TRACTION",
                    "claim_text": "User growth is strong",
                    "claim_grade": "B",
                    "claim_verdict": "VERIFIED",
                    "claim_action": "NONE",
                    "sanad_id": None,
                    "value": None,
                    "corroboration": {"level": "AHAD", "independent_chain_count": 1},
                    "defect_ids": [],
                    "materiality": "MEDIUM",
                    "ic_bound": False,
                    "created_at": "2026-01-10T00:00:00Z",
                }
            )

            sink = JsonlFileAuditSink(file_path=str(audit_log_path))
            app = create_app(audit_sink=sink, service_region="us-east-1")
            client = TestClient(app, raise_server_exceptions=False)

            response = client.get(
                f"/v1/claims/{claim_id}",
                headers={"X-IDIS-API-Key": "test-api-key"},
            )

            assert response.status_code == 200, f"Got {response.status_code}: {response.text}"

            data = response.json()

            assert "value" not in data, (
                "Claim.value must be omitted when None (not serialized as null) "
                "per OpenAPI spec where value is optional but not nullable"
            )

        finally:
            os.environ.pop("IDIS_API_KEYS_JSON", None)
            os.environ.pop("IDIS_AUDIT_LOG_PATH", None)


class TestSanadChainEndpoint:
    """Tests for GET /v1/claims/{claimId}/sanad."""

    def test_returns_200_with_chain_structure(self, tmp_path: Path) -> None:
        """GET /v1/claims/{claimId}/sanad returns 200 with chain structure."""
        tenant_id = str(uuid.uuid4())
        deal_id = str(uuid.uuid4())
        audit_log_path = tmp_path / "audit.jsonl"

        os.environ["IDIS_API_KEYS_JSON"] = _make_api_keys_json(tenant_id)
        os.environ["IDIS_AUDIT_LOG_PATH"] = str(audit_log_path)

        try:
            _seed_deal(tenant_id, deal_id)
            claim_id, sanad_id = _seed_claim_with_sanad(tenant_id, deal_id)

            sink = JsonlFileAuditSink(file_path=str(audit_log_path))
            app = create_app(audit_sink=sink, service_region="us-east-1")
            client = TestClient(app, raise_server_exceptions=False)

            response = client.get(
                f"/v1/claims/{claim_id}/sanad",
                headers={"X-IDIS-API-Key": "test-api-key"},
            )

            assert response.status_code == 200, f"Got {response.status_code}: {response.text}"

            data = response.json()
            assert data["sanad_id"] == sanad_id
            assert data["claim_id"] == claim_id
            assert data["deal_id"] == deal_id
            assert "primary_evidence_id" in data
            assert "corroborating_evidence_ids" in data
            assert isinstance(data["corroborating_evidence_ids"], list)
            assert "transmission_chain" in data
            assert isinstance(data["transmission_chain"], list)
            assert len(data["transmission_chain"]) == 3

            assert "computed" in data
            computed = data["computed"]
            assert computed["grade"] == "A"
            assert computed["corroboration_level"] == "MUTAWATIR"
            assert computed["independent_chain_count"] == 3

        finally:
            os.environ.pop("IDIS_API_KEYS_JSON", None)
            os.environ.pop("IDIS_AUDIT_LOG_PATH", None)

    def test_stable_ordering_for_transmission_chain(self, tmp_path: Path) -> None:
        """Transmission chain should have stable ordering (by node_id)."""
        tenant_id = str(uuid.uuid4())
        deal_id = str(uuid.uuid4())
        audit_log_path = tmp_path / "audit.jsonl"

        os.environ["IDIS_API_KEYS_JSON"] = _make_api_keys_json(tenant_id)
        os.environ["IDIS_AUDIT_LOG_PATH"] = str(audit_log_path)

        try:
            _seed_deal(tenant_id, deal_id)
            claim_id, _ = _seed_claim_with_sanad(tenant_id, deal_id)

            sink = JsonlFileAuditSink(file_path=str(audit_log_path))
            app = create_app(audit_sink=sink, service_region="us-east-1")
            client = TestClient(app, raise_server_exceptions=False)

            response1 = client.get(
                f"/v1/claims/{claim_id}/sanad",
                headers={"X-IDIS-API-Key": "test-api-key"},
            )
            response2 = client.get(
                f"/v1/claims/{claim_id}/sanad",
                headers={"X-IDIS-API-Key": "test-api-key"},
            )

            assert response1.status_code == 200
            assert response2.status_code == 200

            chain1 = response1.json()["transmission_chain"]
            chain2 = response2.json()["transmission_chain"]

            assert len(chain1) == len(chain2)
            for i, (node1, node2) in enumerate(zip(chain1, chain2, strict=True)):
                assert node1["node_id"] == node2["node_id"], f"Mismatch at index {i}"

            node_ids = [n["node_id"] for n in chain1]
            assert node_ids == sorted(node_ids), "Chain should be sorted by node_id"

        finally:
            os.environ.pop("IDIS_API_KEYS_JSON", None)
            os.environ.pop("IDIS_AUDIT_LOG_PATH", None)

    def test_cross_tenant_read_blocked(self, tmp_path: Path) -> None:
        """Cross-tenant sanad read should return 404."""
        tenant_id_1 = str(uuid.uuid4())
        tenant_id_2 = str(uuid.uuid4())
        deal_id = str(uuid.uuid4())
        audit_log_path = tmp_path / "audit.jsonl"

        _seed_deal(tenant_id_1, deal_id)
        claim_id, _ = _seed_claim_with_sanad(tenant_id_1, deal_id)

        os.environ["IDIS_API_KEYS_JSON"] = _make_api_keys_json(tenant_id_2)
        os.environ["IDIS_AUDIT_LOG_PATH"] = str(audit_log_path)

        try:
            sink = JsonlFileAuditSink(file_path=str(audit_log_path))
            app = create_app(audit_sink=sink, service_region="us-east-1")
            client = TestClient(app, raise_server_exceptions=False)

            response = client.get(
                f"/v1/claims/{claim_id}/sanad",
                headers={"X-IDIS-API-Key": "test-api-key"},
            )

            assert response.status_code == 404

        finally:
            os.environ.pop("IDIS_API_KEYS_JSON", None)
            os.environ.pop("IDIS_AUDIT_LOG_PATH", None)

    def test_claim_without_sanad_returns_404(self, tmp_path: Path) -> None:
        """Claim without sanad_id should return 404 for sanad endpoint."""
        tenant_id = str(uuid.uuid4())
        deal_id = str(uuid.uuid4())
        claim_id = str(uuid.uuid4())
        audit_log_path = tmp_path / "audit.jsonl"

        os.environ["IDIS_API_KEYS_JSON"] = _make_api_keys_json(tenant_id)
        os.environ["IDIS_AUDIT_LOG_PATH"] = str(audit_log_path)

        try:
            _seed_deal(tenant_id, deal_id)

            seed_claim(
                {
                    "claim_id": claim_id,
                    "tenant_id": tenant_id,
                    "deal_id": deal_id,
                    "claim_class": "FINANCIAL",
                    "claim_text": "Test claim without sanad",
                    "claim_grade": "D",
                    "claim_verdict": "UNVERIFIED",
                    "claim_action": "RED_FLAG",
                    "sanad_id": None,
                    "corroboration": {"level": "AHAD", "independent_chain_count": 0},
                    "defect_ids": [],
                    "materiality": "MEDIUM",
                    "ic_bound": False,
                    "created_at": "2026-01-10T00:00:00Z",
                }
            )

            sink = JsonlFileAuditSink(file_path=str(audit_log_path))
            app = create_app(audit_sink=sink, service_region="us-east-1")
            client = TestClient(app, raise_server_exceptions=False)

            response = client.get(
                f"/v1/claims/{claim_id}/sanad",
                headers={"X-IDIS-API-Key": "test-api-key"},
            )

            assert response.status_code == 404

        finally:
            os.environ.pop("IDIS_API_KEYS_JSON", None)
            os.environ.pop("IDIS_AUDIT_LOG_PATH", None)


class TestClaimAndSanadRBAC:
    """Test RBAC enforcement for claim and sanad endpoints."""

    def test_auditor_can_read_claim(self, tmp_path: Path) -> None:
        """AUDITOR role should be able to read claims."""
        tenant_id = str(uuid.uuid4())
        deal_id = str(uuid.uuid4())
        audit_log_path = tmp_path / "audit.jsonl"

        os.environ["IDIS_API_KEYS_JSON"] = _make_api_keys_json(
            tenant_id, roles=[Role.AUDITOR.value]
        )
        os.environ["IDIS_AUDIT_LOG_PATH"] = str(audit_log_path)

        try:
            _seed_deal(tenant_id, deal_id)
            claim_id, _ = _seed_claim_with_sanad(tenant_id, deal_id)

            sink = JsonlFileAuditSink(file_path=str(audit_log_path))
            app = create_app(audit_sink=sink, service_region="us-east-1")
            client = TestClient(app, raise_server_exceptions=False)

            response = client.get(
                f"/v1/claims/{claim_id}",
                headers={"X-IDIS-API-Key": "test-api-key"},
            )

            assert response.status_code == 200

        finally:
            os.environ.pop("IDIS_API_KEYS_JSON", None)
            os.environ.pop("IDIS_AUDIT_LOG_PATH", None)

    def test_auditor_can_read_sanad(self, tmp_path: Path) -> None:
        """AUDITOR role should be able to read sanad chains."""
        tenant_id = str(uuid.uuid4())
        deal_id = str(uuid.uuid4())
        audit_log_path = tmp_path / "audit.jsonl"

        os.environ["IDIS_API_KEYS_JSON"] = _make_api_keys_json(
            tenant_id, roles=[Role.AUDITOR.value]
        )
        os.environ["IDIS_AUDIT_LOG_PATH"] = str(audit_log_path)

        try:
            _seed_deal(tenant_id, deal_id)
            claim_id, _ = _seed_claim_with_sanad(tenant_id, deal_id)

            sink = JsonlFileAuditSink(file_path=str(audit_log_path))
            app = create_app(audit_sink=sink, service_region="us-east-1")
            client = TestClient(app, raise_server_exceptions=False)

            response = client.get(
                f"/v1/claims/{claim_id}/sanad",
                headers={"X-IDIS-API-Key": "test-api-key"},
            )

            assert response.status_code == 200

        finally:
            os.environ.pop("IDIS_API_KEYS_JSON", None)
            os.environ.pop("IDIS_AUDIT_LOG_PATH", None)

    def test_no_auth_returns_401_for_claim(self, tmp_path: Path) -> None:
        """Request without auth should return 401."""
        claim_id = str(uuid.uuid4())
        audit_log_path = tmp_path / "audit.jsonl"

        os.environ["IDIS_AUDIT_LOG_PATH"] = str(audit_log_path)
        os.environ.pop("IDIS_API_KEYS_JSON", None)

        try:
            sink = JsonlFileAuditSink(file_path=str(audit_log_path))
            app = create_app(audit_sink=sink, service_region="us-east-1")
            client = TestClient(app, raise_server_exceptions=False)

            response = client.get(f"/v1/claims/{claim_id}")

            assert response.status_code == 401

        finally:
            os.environ.pop("IDIS_AUDIT_LOG_PATH", None)

    def test_no_auth_returns_401_for_sanad(self, tmp_path: Path) -> None:
        """Request without auth should return 401."""
        claim_id = str(uuid.uuid4())
        audit_log_path = tmp_path / "audit.jsonl"

        os.environ["IDIS_AUDIT_LOG_PATH"] = str(audit_log_path)
        os.environ.pop("IDIS_API_KEYS_JSON", None)

        try:
            sink = JsonlFileAuditSink(file_path=str(audit_log_path))
            app = create_app(audit_sink=sink, service_region="us-east-1")
            client = TestClient(app, raise_server_exceptions=False)

            response = client.get(f"/v1/claims/{claim_id}/sanad")

            assert response.status_code == 401

        finally:
            os.environ.pop("IDIS_AUDIT_LOG_PATH", None)
