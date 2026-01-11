"""Tests for IDIS API Truth Dashboard endpoint.

Phase 6.2: Frontend backend contracts - Truth Dashboard API.

Tests:
- GET /v1/deals/{dealId}/truth-dashboard returns 200 with correct schema
- Summary counts match seeded data
- Stable ordering (determinism)
- Cross-tenant access blocked (404)
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
from idis.api.routes.claims import clear_all_stores, seed_claim, seed_defect
from idis.api.routes.deals import _deals_store, clear_deals_store
from idis.audit.sink import JsonlFileAuditSink


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


def _seed_test_claims(tenant_id: str, deal_id: str) -> list[str]:
    """Seed test claims with varying grades and verdicts. Returns claim IDs."""
    claims = [
        {
            "claim_id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "claim_class": "FINANCIAL",
            "claim_text": "Revenue is $1M ARR",
            "claim_grade": "A",
            "claim_verdict": "VERIFIED",
            "claim_action": "NONE",
            "corroboration": {"level": "MUTAWATIR", "independent_chain_count": 3},
            "defect_ids": [],
            "materiality": "HIGH",
            "ic_bound": True,
            "created_at": "2026-01-10T00:00:00Z",
        },
        {
            "claim_id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "claim_class": "TRACTION",
            "claim_text": "10,000 DAU",
            "claim_grade": "B",
            "claim_verdict": "INFLATED",
            "claim_action": "FLAG",
            "corroboration": {"level": "AHAD", "independent_chain_count": 1},
            "defect_ids": [],
            "materiality": "MEDIUM",
            "ic_bound": False,
            "created_at": "2026-01-10T00:01:00Z",
        },
        {
            "claim_id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "claim_class": "MARKET_SIZE",
            "claim_text": "$10B TAM",
            "claim_grade": "D",
            "claim_verdict": "UNVERIFIED",
            "claim_action": "RED_FLAG",
            "corroboration": {"level": "AHAD", "independent_chain_count": 0},
            "defect_ids": ["defect-fatal-001"],
            "materiality": "CRITICAL",
            "ic_bound": False,
            "created_at": "2026-01-10T00:02:00Z",
        },
    ]

    for claim in claims:
        seed_claim(claim)

    seed_defect(
        {
            "defect_id": "defect-fatal-001",
            "deal_id": deal_id,
            "tenant_id": tenant_id,
            "defect_type": "BROKEN_CHAIN",
            "severity": "FATAL",
            "description": "Evidence chain is broken",
            "affected_claim_ids": [claims[2]["claim_id"]],
            "created_at": "2026-01-10T00:00:00Z",
        }
    )

    return [c["claim_id"] for c in claims]


@pytest.fixture(autouse=True)
def cleanup_stores() -> None:
    """Clean up stores before and after each test."""
    clear_deals_store()
    clear_all_stores()
    yield
    clear_deals_store()
    clear_all_stores()


class TestTruthDashboardEndpoint:
    """Tests for GET /v1/deals/{dealId}/truth-dashboard."""

    def test_returns_200_with_correct_schema(self, tmp_path: Path) -> None:
        """GET /v1/deals/{dealId}/truth-dashboard returns 200 with correct schema."""
        tenant_id = str(uuid.uuid4())
        deal_id = str(uuid.uuid4())
        audit_log_path = tmp_path / "audit.jsonl"

        os.environ["IDIS_API_KEYS_JSON"] = _make_api_keys_json(tenant_id)
        os.environ["IDIS_AUDIT_LOG_PATH"] = str(audit_log_path)

        try:
            _seed_deal(tenant_id, deal_id)
            _seed_test_claims(tenant_id, deal_id)

            sink = JsonlFileAuditSink(file_path=str(audit_log_path))
            app = create_app(audit_sink=sink)
            client = TestClient(app, raise_server_exceptions=False)

            response = client.get(
                f"/v1/deals/{deal_id}/truth-dashboard",
                headers={"X-IDIS-API-Key": "test-api-key"},
            )

            assert response.status_code == 200, f"Got {response.status_code}: {response.text}"

            data = response.json()
            assert "deal_id" in data
            assert "summary" in data
            assert "claims" in data

            assert data["deal_id"] == deal_id

            summary = data["summary"]
            assert "total_claims" in summary
            assert "by_grade" in summary
            assert "by_verdict" in summary
            assert "fatal_defects" in summary

            by_grade = summary["by_grade"]
            assert "A" in by_grade
            assert "B" in by_grade
            assert "C" in by_grade
            assert "D" in by_grade

            by_verdict = summary["by_verdict"]
            assert "VERIFIED" in by_verdict
            assert "INFLATED" in by_verdict
            assert "CONTRADICTED" in by_verdict
            assert "UNVERIFIED" in by_verdict
            assert "SUBJECTIVE" in by_verdict

            claims = data["claims"]
            assert "items" in claims
            assert isinstance(claims["items"], list)

        finally:
            os.environ.pop("IDIS_API_KEYS_JSON", None)
            os.environ.pop("IDIS_AUDIT_LOG_PATH", None)

    def test_summary_counts_match_seeded_data(self, tmp_path: Path) -> None:
        """Summary counts should match the seeded claims data."""
        tenant_id = str(uuid.uuid4())
        deal_id = str(uuid.uuid4())
        audit_log_path = tmp_path / "audit.jsonl"

        os.environ["IDIS_API_KEYS_JSON"] = _make_api_keys_json(tenant_id)
        os.environ["IDIS_AUDIT_LOG_PATH"] = str(audit_log_path)

        try:
            _seed_deal(tenant_id, deal_id)
            _seed_test_claims(tenant_id, deal_id)

            sink = JsonlFileAuditSink(file_path=str(audit_log_path))
            app = create_app(audit_sink=sink)
            client = TestClient(app, raise_server_exceptions=False)

            response = client.get(
                f"/v1/deals/{deal_id}/truth-dashboard",
                headers={"X-IDIS-API-Key": "test-api-key"},
            )

            assert response.status_code == 200
            data = response.json()
            summary = data["summary"]

            assert summary["total_claims"] == 3

            assert summary["by_grade"]["A"] == 1
            assert summary["by_grade"]["B"] == 1
            assert summary["by_grade"]["C"] == 0
            assert summary["by_grade"]["D"] == 1

            assert summary["by_verdict"]["VERIFIED"] == 1
            assert summary["by_verdict"]["INFLATED"] == 1
            assert summary["by_verdict"]["CONTRADICTED"] == 0
            assert summary["by_verdict"]["UNVERIFIED"] == 1
            assert summary["by_verdict"]["SUBJECTIVE"] == 0

            assert summary["fatal_defects"] == 1

        finally:
            os.environ.pop("IDIS_API_KEYS_JSON", None)
            os.environ.pop("IDIS_AUDIT_LOG_PATH", None)

    def test_stable_ordering_determinism(self, tmp_path: Path) -> None:
        """Two calls should produce identical JSON (stable ordering)."""
        tenant_id = str(uuid.uuid4())
        deal_id = str(uuid.uuid4())
        audit_log_path = tmp_path / "audit.jsonl"

        os.environ["IDIS_API_KEYS_JSON"] = _make_api_keys_json(tenant_id)
        os.environ["IDIS_AUDIT_LOG_PATH"] = str(audit_log_path)

        try:
            _seed_deal(tenant_id, deal_id)
            _seed_test_claims(tenant_id, deal_id)

            sink = JsonlFileAuditSink(file_path=str(audit_log_path))
            app = create_app(audit_sink=sink)
            client = TestClient(app, raise_server_exceptions=False)

            response1 = client.get(
                f"/v1/deals/{deal_id}/truth-dashboard",
                headers={"X-IDIS-API-Key": "test-api-key"},
            )
            response2 = client.get(
                f"/v1/deals/{deal_id}/truth-dashboard",
                headers={"X-IDIS-API-Key": "test-api-key"},
            )

            assert response1.status_code == 200
            assert response2.status_code == 200

            data1 = response1.json()
            data2 = response2.json()

            assert data1["summary"] == data2["summary"]

            items1 = data1["claims"]["items"]
            items2 = data2["claims"]["items"]
            assert len(items1) == len(items2)
            for i, (item1, item2) in enumerate(zip(items1, items2, strict=True)):
                assert item1["claim_id"] == item2["claim_id"], f"Mismatch at index {i}"

        finally:
            os.environ.pop("IDIS_API_KEYS_JSON", None)
            os.environ.pop("IDIS_AUDIT_LOG_PATH", None)

    def test_cross_tenant_access_blocked(self, tmp_path: Path) -> None:
        """Cross-tenant access should return 404 (not 403 to avoid info leak)."""
        tenant_id_1 = str(uuid.uuid4())
        tenant_id_2 = str(uuid.uuid4())
        deal_id = str(uuid.uuid4())
        audit_log_path = tmp_path / "audit.jsonl"

        _seed_deal(tenant_id_1, deal_id)
        _seed_test_claims(tenant_id_1, deal_id)

        os.environ["IDIS_API_KEYS_JSON"] = _make_api_keys_json(tenant_id_2)
        os.environ["IDIS_AUDIT_LOG_PATH"] = str(audit_log_path)

        try:
            sink = JsonlFileAuditSink(file_path=str(audit_log_path))
            app = create_app(audit_sink=sink)
            client = TestClient(app, raise_server_exceptions=False)

            response = client.get(
                f"/v1/deals/{deal_id}/truth-dashboard",
                headers={"X-IDIS-API-Key": "test-api-key"},
            )

            assert response.status_code == 404

        finally:
            os.environ.pop("IDIS_API_KEYS_JSON", None)
            os.environ.pop("IDIS_AUDIT_LOG_PATH", None)

    def test_deal_not_found_returns_404(self, tmp_path: Path) -> None:
        """Non-existent deal should return 404."""
        tenant_id = str(uuid.uuid4())
        deal_id = str(uuid.uuid4())
        audit_log_path = tmp_path / "audit.jsonl"

        os.environ["IDIS_API_KEYS_JSON"] = _make_api_keys_json(tenant_id)
        os.environ["IDIS_AUDIT_LOG_PATH"] = str(audit_log_path)

        try:
            sink = JsonlFileAuditSink(file_path=str(audit_log_path))
            app = create_app(audit_sink=sink)
            client = TestClient(app, raise_server_exceptions=False)

            response = client.get(
                f"/v1/deals/{deal_id}/truth-dashboard",
                headers={"X-IDIS-API-Key": "test-api-key"},
            )

            assert response.status_code == 404

        finally:
            os.environ.pop("IDIS_API_KEYS_JSON", None)
            os.environ.pop("IDIS_AUDIT_LOG_PATH", None)

    def test_empty_claims_returns_zero_counts(self, tmp_path: Path) -> None:
        """Deal with no claims should return zero counts."""
        tenant_id = str(uuid.uuid4())
        deal_id = str(uuid.uuid4())
        audit_log_path = tmp_path / "audit.jsonl"

        os.environ["IDIS_API_KEYS_JSON"] = _make_api_keys_json(tenant_id)
        os.environ["IDIS_AUDIT_LOG_PATH"] = str(audit_log_path)

        try:
            _seed_deal(tenant_id, deal_id)

            sink = JsonlFileAuditSink(file_path=str(audit_log_path))
            app = create_app(audit_sink=sink)
            client = TestClient(app, raise_server_exceptions=False)

            response = client.get(
                f"/v1/deals/{deal_id}/truth-dashboard",
                headers={"X-IDIS-API-Key": "test-api-key"},
            )

            assert response.status_code == 200
            data = response.json()
            summary = data["summary"]

            assert summary["total_claims"] == 0
            assert summary["by_grade"]["A"] == 0
            assert summary["by_grade"]["B"] == 0
            assert summary["by_grade"]["C"] == 0
            assert summary["by_grade"]["D"] == 0
            assert summary["fatal_defects"] == 0
            assert len(data["claims"]["items"]) == 0

        finally:
            os.environ.pop("IDIS_API_KEYS_JSON", None)
            os.environ.pop("IDIS_AUDIT_LOG_PATH", None)


class TestTruthDashboardRBAC:
    """Test RBAC enforcement for truth dashboard endpoint."""

    def test_auditor_can_read_truth_dashboard(self, tmp_path: Path) -> None:
        """AUDITOR role should be able to read truth dashboard."""
        tenant_id = str(uuid.uuid4())
        deal_id = str(uuid.uuid4())
        audit_log_path = tmp_path / "audit.jsonl"

        os.environ["IDIS_API_KEYS_JSON"] = _make_api_keys_json(
            tenant_id, roles=[Role.AUDITOR.value]
        )
        os.environ["IDIS_AUDIT_LOG_PATH"] = str(audit_log_path)

        try:
            _seed_deal(tenant_id, deal_id)

            sink = JsonlFileAuditSink(file_path=str(audit_log_path))
            app = create_app(audit_sink=sink)
            client = TestClient(app, raise_server_exceptions=False)

            response = client.get(
                f"/v1/deals/{deal_id}/truth-dashboard",
                headers={"X-IDIS-API-Key": "test-api-key"},
            )

            assert response.status_code == 200

        finally:
            os.environ.pop("IDIS_API_KEYS_JSON", None)
            os.environ.pop("IDIS_AUDIT_LOG_PATH", None)

    def test_no_auth_returns_401(self, tmp_path: Path) -> None:
        """Request without auth should return 401."""
        deal_id = str(uuid.uuid4())
        audit_log_path = tmp_path / "audit.jsonl"

        os.environ["IDIS_AUDIT_LOG_PATH"] = str(audit_log_path)
        os.environ.pop("IDIS_API_KEYS_JSON", None)

        try:
            sink = JsonlFileAuditSink(file_path=str(audit_log_path))
            app = create_app(audit_sink=sink)
            client = TestClient(app, raise_server_exceptions=False)

            response = client.get(f"/v1/deals/{deal_id}/truth-dashboard")

            assert response.status_code == 401

        finally:
            os.environ.pop("IDIS_AUDIT_LOG_PATH", None)
