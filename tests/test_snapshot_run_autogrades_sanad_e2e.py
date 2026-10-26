"""E2E test: SNAPSHOT run auto-grades Sanad for extracted claims [P3-T01].

Full flow: create deal -> create doc -> ingest -> start SNAPSHOT run.
Asserts:
- Claims exist after extraction
- Sanads exist for those claims
- Tenant isolation: other tenant cannot read them
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from idis.api.auth import IDIS_API_KEYS_ENV
from idis.api.main import create_app
from idis.api.routes.deals import clear_deals_store
from idis.api.routes.runs import clear_runs_store
from idis.audit.postgres_sink import PostgresAuditSink
from idis.audit.sink import InMemoryAuditSink
from idis.idempotency.store import SqliteIdempotencyStore
from idis.persistence.repositories.claims import (
    InMemorySanadsRepository,
    SanadsRepository,
    clear_all_claims_stores,
)
from idis.services.ingestion import IngestionContext
from idis.storage.compliant_store import ComplianceEnforcedStore
from idis.storage.filesystem_store import FilesystemObjectStore
from idis.tools.preview_source_preparation import PreviewSourceFile, prepare_preview_source_package
from tests._cir04c3_required_frozen_preview import (
    CapturingRequiredPostgresAuditSink,
    RequiredFrozenPreview,
    required_frozen_preview,
)
from tests.abac_seed import seed_deal_access
from tests.test_pdf_parser import create_test_pdf


def _api_keys_config(*, tenant_a_id: str, tenant_b_id: str) -> str:
    """Build API keys JSON for two fresh tenants."""
    import json

    keys = {
        "key-tenant-a": {
            "tenant_id": tenant_a_id,
            "actor_id": "actor-e2e-a",
            "name": "Tenant A",
            "timezone": "UTC",
            "data_region": "me-south-1",
            "roles": ["ANALYST"],
        },
        "key-tenant-b": {
            "tenant_id": tenant_b_id,
            "actor_id": "actor-e2e-b",
            "name": "Tenant B",
            "timezone": "UTC",
            "data_region": "me-south-1",
            "roles": ["ANALYST"],
        },
    }
    return json.dumps(keys)


def _tenant_by_key(*, tenant_a_id: str, tenant_b_id: str) -> dict[str, tuple[str, str]]:
    """Map API keys to the tenant and actor inserted for this test."""
    return {
        "key-tenant-a": (tenant_a_id, "actor-e2e-a"),
        "key-tenant-b": (tenant_b_id, "actor-e2e-b"),
    }


def _make_client(monkeypatch: Any, *, tenant_a_id: str, tenant_b_id: str) -> TestClient:
    """Build a fresh TestClient with clean stores."""
    clear_deals_store()
    clear_runs_store()
    clear_all_claims_stores()

    monkeypatch.setenv(
        IDIS_API_KEYS_ENV,
        _api_keys_config(tenant_a_id=tenant_a_id, tenant_b_id=tenant_b_id),
    )
    audit_sink = InMemoryAuditSink()
    idem_store = SqliteIdempotencyStore(in_memory=True)
    app = create_app(
        audit_sink=CapturingRequiredPostgresAuditSink(audit_sink),
        idempotency_store=idem_store,
    )
    app.state.extractor_configured = True
    return TestClient(app, raise_server_exceptions=False)


def _create_deal(
    client: TestClient,
    api_key: str,
    *,
    tenant_by_key: dict[str, tuple[str, str]],
) -> str:
    """Create a deal and return its ID."""
    resp = client.post(
        "/v1/deals",
        json={"name": f"E2E Sanad Test {uuid.uuid4().hex[:8]}", "company_name": "TestCorp"},
        headers={"X-IDIS-API-Key": api_key},
    )
    assert resp.status_code in (200, 201), f"Deal creation failed: {resp.text}"
    deal_id = resp.json()["deal_id"]
    tenant_id, actor_id = tenant_by_key[api_key]
    seed_deal_access(tenant_id, deal_id, actor_id)  # the creator is authorized on their own deal
    return deal_id


def _inject_snapshot_documents(
    client: TestClient,
    deal_id: str,
    preview_db: RequiredFrozenPreview,
    root: Path,
    *,
    tenant_id: str,
    actor_id: str,
) -> None:
    """Prepare and freeze a durable PDF package so SNAPSHOT can extract claims."""
    _ = client
    prepared = prepare_preview_source_package(
        engine=preview_db.engine,
        ctx=IngestionContext(
            tenant_id=uuid.UUID(tenant_id),
            actor_id=actor_id,
            request_id=str(uuid.uuid4()),
        ),
        deal_id=uuid.UUID(deal_id),
        files=[
            PreviewSourceFile(
                "pdf",
                "application/pdf",
                create_test_pdf(
                    [
                        "FY2024 revenue reached $12.3M with 85% gross margin.",
                        "Customer count grew to 500 enterprise clients.",
                    ]
                ),
            )
        ],
        compliant_store=ComplianceEnforcedStore(inner_store=FilesystemObjectStore(base_dir=root)),
        audit_sink=PostgresAuditSink(),
    )
    preview_db.freeze(tenant_id, deal_id, prepared.package_id)


class TestSnapshotRunAutogradesSanadE2E:
    """E2E: SNAPSHOT run creates claims and auto-grades Sanads."""

    def test_snapshot_run_produces_claims_and_sanads(
        self,
        monkeypatch: Any,
        tmp_path: Path,
    ) -> None:
        """Full SNAPSHOT run creates claims and persists Sanads for them."""
        tenant_a_id = str(uuid.uuid4())
        tenant_b_id = str(uuid.uuid4())
        tenants = _tenant_by_key(tenant_a_id=tenant_a_id, tenant_b_id=tenant_b_id)
        with required_frozen_preview(monkeypatch) as preview_db:
            preview_db.add_tenant(tenant_a_id, "me-south-1")
            client = _make_client(
                monkeypatch,
                tenant_a_id=tenant_a_id,
                tenant_b_id=tenant_b_id,
            )
            deal_id = _create_deal(client, "key-tenant-a", tenant_by_key=tenants)
            _inject_snapshot_documents(
                client,
                deal_id,
                preview_db,
                tmp_path / "sanad-e2e-a",
                tenant_id=tenant_a_id,
                actor_id="actor-e2e-a",
            )

            resp = client.post(
                f"/v1/deals/{deal_id}/runs",
                json={"mode": "SNAPSHOT"},
                headers={"X-IDIS-API-Key": "key-tenant-a"},
            )
            assert resp.status_code == 202, f"Run start failed: {resp.text}"
            run_data = resp.json()
            assert run_data["status"] == "SUCCEEDED"

            # Verify claims exist
            claims_resp = client.get(
                f"/v1/deals/{deal_id}/claims",
                headers={"X-IDIS-API-Key": "key-tenant-a"},
            )
            assert claims_resp.status_code == 200
            claims = claims_resp.json()["items"]
            assert len(claims) >= 1, "Expected at least one claim"

            # Verify sanads exist for those claims
            with preview_db.engine.begin() as conn:
                sanads_repo = SanadsRepository(conn, tenant_a_id)
                sanads, _ = sanads_repo.list_by_deal(deal_id)
            assert len(sanads) >= 1, "Expected at least one Sanad"

            # Each sanad must reference a valid claim_id and correct deal
            for sanad in sanads:
                assert sanad["claim_id"], "Sanad must have a claim_id"
                assert sanad["deal_id"] == deal_id
                assert sanad["tenant_id"] == tenant_a_id

    def test_tenant_isolation_no_cross_tenant_sanads(
        self,
        monkeypatch: Any,
        tmp_path: Path,
    ) -> None:
        """Tenant B cannot see Sanads created by Tenant A's SNAPSHOT run."""
        tenant_a_id = str(uuid.uuid4())
        tenant_b_id = str(uuid.uuid4())
        tenants = _tenant_by_key(tenant_a_id=tenant_a_id, tenant_b_id=tenant_b_id)
        with required_frozen_preview(monkeypatch) as preview_db:
            preview_db.add_tenant(tenant_a_id, "me-south-1")
            preview_db.add_tenant(tenant_b_id, "me-south-1")
            client = _make_client(
                monkeypatch,
                tenant_a_id=tenant_a_id,
                tenant_b_id=tenant_b_id,
            )

            # Tenant A: create deal + run
            deal_id_a = _create_deal(client, "key-tenant-a", tenant_by_key=tenants)
            _inject_snapshot_documents(
                client,
                deal_id_a,
                preview_db,
                tmp_path / "sanad-e2e-iso",
                tenant_id=tenant_a_id,
                actor_id="actor-e2e-a",
            )

            resp_a = client.post(
                f"/v1/deals/{deal_id_a}/runs",
                json={"mode": "SNAPSHOT"},
                headers={"X-IDIS-API-Key": "key-tenant-a"},
            )
            assert resp_a.status_code == 202
            with preview_db.engine.begin() as conn:
                sanads_a, _ = SanadsRepository(conn, tenant_a_id).list_by_deal(deal_id_a)
            assert len(sanads_a) >= 1

            # Tenant B cannot see Tenant A's deal, so cross-tenant deal-scoped lists fall through to
            # the route and return empty pages under Tenant B's RLS (no existence oracle, ADR-011).
            sanads_resp_b = client.get(
                f"/v1/deals/{deal_id_a}/sanads",
                headers={"X-IDIS-API-Key": "key-tenant-b"},
            )
            assert sanads_resp_b.status_code == 200
            assert sanads_resp_b.json()["items"] == []

            # The claims list route validates deal visibility, so a cross-tenant deal is a uniform
            # 404 (same as a nonexistent deal); the sanads route above lists empty. Both leak no
            # existence.
            claims_resp_b = client.get(
                f"/v1/deals/{deal_id_a}/claims",
                headers={"X-IDIS-API-Key": "key-tenant-b"},
            )
            assert claims_resp_b.status_code == 404

            # Tenant A's sanads repo is scoped - B's repo sees nothing
            with preview_db.engine.begin() as conn:
                db_sanads_b, _ = SanadsRepository(conn, tenant_b_id).list_by_deal(deal_id_a)
            assert db_sanads_b == []
            repo_b = InMemorySanadsRepository(tenant_b_id)
            sanads_b, _ = repo_b.list_by_deal(deal_id_a)
            assert sanads_b == []
