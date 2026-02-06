"""E2E test: SNAPSHOT run auto-grades Sanad for extracted claims [P3-T01].

Full flow: create deal → create doc → ingest → start SNAPSHOT run.
Asserts:
- Claims exist after extraction
- Sanads exist for those claims
- Tenant isolation: other tenant cannot read them
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient

from idis.api.auth import IDIS_API_KEYS_ENV
from idis.api.main import create_app
from idis.api.routes.deals import clear_deals_store
from idis.api.routes.runs import clear_runs_store
from idis.audit.sink import InMemoryAuditSink
from idis.idempotency.store import SqliteIdempotencyStore
from idis.persistence.repositories.claims import (
    InMemorySanadsRepository,
    clear_all_claims_stores,
)

TENANT_A_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
TENANT_B_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def _api_keys_config() -> str:
    """Build API keys JSON for two tenants."""
    import json

    keys = {
        "key-tenant-a": {
            "tenant_id": TENANT_A_ID,
            "actor_id": "actor-e2e-a",
            "name": "Tenant A",
            "timezone": "UTC",
            "data_region": "me-south-1",
            "roles": ["ANALYST"],
        },
        "key-tenant-b": {
            "tenant_id": TENANT_B_ID,
            "actor_id": "actor-e2e-b",
            "name": "Tenant B",
            "timezone": "UTC",
            "data_region": "me-south-1",
            "roles": ["ANALYST"],
        },
    }
    return json.dumps(keys)


def _make_client(monkeypatch: Any) -> TestClient:
    """Build a fresh TestClient with clean stores."""
    clear_deals_store()
    clear_runs_store()
    clear_all_claims_stores()

    monkeypatch.setenv(IDIS_API_KEYS_ENV, _api_keys_config())
    audit_sink = InMemoryAuditSink()
    idem_store = SqliteIdempotencyStore(in_memory=True)
    app = create_app(audit_sink=audit_sink, idempotency_store=idem_store)
    app.state.extractor_configured = True
    return TestClient(app, raise_server_exceptions=False)


def _create_deal(client: TestClient, api_key: str) -> str:
    """Create a deal and return its ID."""
    resp = client.post(
        "/v1/deals",
        json={"name": f"E2E Sanad Test {uuid.uuid4().hex[:8]}", "company_name": "TestCorp"},
        headers={"X-IDIS-API-Key": api_key},
    )
    assert resp.status_code in (200, 201), f"Deal creation failed: {resp.text}"
    return resp.json()["deal_id"]


def _inject_snapshot_documents(
    client: TestClient,
    deal_id: str,
) -> None:
    """Inject pre-built document data so SNAPSHOT can extract claims."""
    docs = [
        {
            "document_id": str(uuid.uuid4()),
            "doc_type": "PDF",
            "document_name": "pitch_deck.pdf",
            "spans": [
                {
                    "span_id": str(uuid.uuid4()),
                    "text_excerpt": "FY2024 revenue reached $12.3M with 85% gross margin.",
                    "locator": {"page": 1, "line": 1},
                    "span_type": "PAGE_TEXT",
                },
                {
                    "span_id": str(uuid.uuid4()),
                    "text_excerpt": "Customer count grew to 500 enterprise clients.",
                    "locator": {"page": 1, "line": 2},
                    "span_type": "PAGE_TEXT",
                },
            ],
        },
    ]
    client.app.state.deal_documents = {deal_id: docs}  # type: ignore[union-attr]


class TestSnapshotRunAutogradesSanadE2E:
    """E2E: SNAPSHOT run creates claims and auto-grades Sanads."""

    def test_snapshot_run_produces_claims_and_sanads(self, monkeypatch: Any) -> None:
        """Full SNAPSHOT run creates claims and persists Sanads for them."""
        client = _make_client(monkeypatch)
        deal_id = _create_deal(client, "key-tenant-a")
        _inject_snapshot_documents(client, deal_id)

        resp = client.post(
            f"/v1/deals/{deal_id}/runs",
            json={"mode": "SNAPSHOT"},
            headers={"X-IDIS-API-Key": "key-tenant-a"},
        )
        assert resp.status_code == 202, f"Run start failed: {resp.text}"
        run_data = resp.json()
        assert run_data["status"] in ("COMPLETED", "PARTIAL")

        # Verify claims exist
        claims_resp = client.get(
            f"/v1/deals/{deal_id}/claims",
            headers={"X-IDIS-API-Key": "key-tenant-a"},
        )
        assert claims_resp.status_code == 200
        claims = claims_resp.json()["items"]
        assert len(claims) >= 1, "Expected at least one claim"

        # Verify sanads exist for those claims
        sanads_repo = InMemorySanadsRepository(TENANT_A_ID)
        sanads, _ = sanads_repo.list_by_deal(deal_id)
        assert len(sanads) >= 1, "Expected at least one Sanad"

        # Each sanad must reference a valid claim_id and correct deal
        for sanad in sanads:
            assert sanad["claim_id"], "Sanad must have a claim_id"
            assert sanad["deal_id"] == deal_id
            assert sanad["tenant_id"] == TENANT_A_ID

    def test_tenant_isolation_no_cross_tenant_sanads(self, monkeypatch: Any) -> None:
        """Tenant B cannot see Sanads created by Tenant A's SNAPSHOT run."""
        client = _make_client(monkeypatch)

        # Tenant A: create deal + run
        deal_id_a = _create_deal(client, "key-tenant-a")
        _inject_snapshot_documents(client, deal_id_a)

        resp_a = client.post(
            f"/v1/deals/{deal_id_a}/runs",
            json={"mode": "SNAPSHOT"},
            headers={"X-IDIS-API-Key": "key-tenant-a"},
        )
        assert resp_a.status_code == 202

        # Tenant B: cannot see Tenant A's sanads (404 = no existence leak)
        sanads_resp_b = client.get(
            f"/v1/deals/{deal_id_a}/sanads",
            headers={"X-IDIS-API-Key": "key-tenant-b"},
        )
        if sanads_resp_b.status_code == 200:
            assert sanads_resp_b.json()["items"] == []
        else:
            assert sanads_resp_b.status_code == 404

        # Tenant B: cannot see Tenant A's claims (404 = no existence leak)
        claims_resp_b = client.get(
            f"/v1/deals/{deal_id_a}/claims",
            headers={"X-IDIS-API-Key": "key-tenant-b"},
        )
        if claims_resp_b.status_code == 200:
            assert claims_resp_b.json()["items"] == []
        else:
            assert claims_resp_b.status_code == 404

        # Tenant A's sanads repo is scoped — B's repo sees nothing
        repo_b = InMemorySanadsRepository(TENANT_B_ID)
        sanads_b, _ = repo_b.list_by_deal(deal_id_a)
        assert sanads_b == []
