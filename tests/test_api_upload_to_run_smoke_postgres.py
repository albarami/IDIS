"""Postgres-backed API smoke for upload -> durable summary -> selected run."""

from __future__ import annotations

import hashlib
import json
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text

import idis.api.routes.runs as runs_route
from idis.api.auth import IDIS_API_KEYS_ENV, TenantContext
from idis.api.main import create_app
from idis.api.routes.deals import clear_deals_store
from idis.api.routes.documents import clear_document_store
from idis.audit.sink import InMemoryAuditSink
from idis.compliance.byok import BYOKPolicyRegistry, configure_key
from idis.idempotency.store import SqliteIdempotencyStore
from idis.persistence.repositories.runs import PostgresRunsRepository
from idis.pipeline.worker import _default_run_context_factory
from idis.services.ingestion import IngestionContext, IngestionService
from idis.services.runs.execution import RunExecutionResult
from idis.storage.compliant_store import ComplianceEnforcedStore
from idis.storage.filesystem_store import FilesystemObjectStore
from tests import test_ingestion_persists_documents_postgres as pg_helpers
from tests.abac_seed import seed_deal_access

pytest_plugins = ("tests.test_ingestion_persists_documents_postgres",)

FORBIDDEN_PUBLIC_TOKENS = (
    "content_b64",
    "raw_bytes",
    "raw_text",
    "parsed_text",
    "text_excerpt",
    "spans",
    "Revenue was 10M",
    "EBITDA was 2M",
)


def _configure_api_key(monkeypatch: Any) -> None:
    monkeypatch.setenv(
        IDIS_API_KEYS_ENV,
        json.dumps(
            {
                pg_helpers.API_KEY: {
                    "tenant_id": str(pg_helpers.TENANT_ID),
                    "actor_id": pg_helpers.ACTOR_ID,
                    "name": "Tenant A",
                    "timezone": "UTC",
                    "data_region": "me-south-1",
                    "roles": ["ANALYST", "ADMIN"],
                }
            }
        ),
    )


def _make_client_context() -> tuple[TestClient, InMemoryAuditSink, BYOKPolicyRegistry]:
    clear_deals_store()
    clear_document_store()
    byok_registry = BYOKPolicyRegistry()
    audit_sink = InMemoryAuditSink()
    tmpdir = tempfile.TemporaryDirectory(prefix="idis_api_smoke_pg_")
    inner_store = FilesystemObjectStore(base_dir=Path(tmpdir.name))
    compliant_store = ComplianceEnforcedStore(
        inner_store=inner_store,
        byok_registry=byok_registry,
    )
    ingestion_service = IngestionService(
        compliant_store=compliant_store,
        audit_sink=audit_sink,
    )
    app = create_app(
        audit_sink=audit_sink,
        idempotency_store=SqliteIdempotencyStore(in_memory=True),
        ingestion_service=ingestion_service,
    )
    app.state._slice22_tmpdir = tmpdir
    return TestClient(app, raise_server_exceptions=False), audit_sink, byok_registry


def _configure_byok(byok_registry: BYOKPolicyRegistry, audit_sink: InMemoryAuditSink) -> None:
    tenant_ctx = TenantContext(
        tenant_id=str(pg_helpers.TENANT_ID),
        actor_id=pg_helpers.ACTOR_ID,
        name="Tenant A",
        timezone="UTC",
        data_region="me-south-1",
    )
    configure_key(tenant_ctx, "api-smoke-pg-key", audit_sink, registry=byok_registry)


def _create_deal(client: TestClient, *, name: str) -> str:
    response = client.post(
        "/v1/deals",
        headers={"X-IDIS-API-Key": pg_helpers.API_KEY},
        json={"name": name, "company_name": "Synthetic Co"},
    )
    assert response.status_code == 201
    deal_id = str(response.json()["deal_id"])
    # Deal-scoped ABAC is deny-by-default (Slice98): grant the creating actor access through the
    # real store seam so this authorized single-tenant workflow can operate on its own deal.
    seed_deal_access(str(pg_helpers.TENANT_ID), deal_id, pg_helpers.ACTOR_ID)
    return deal_id


def _upload_document(
    client: TestClient,
    *,
    deal_id: str,
    filename: str,
    data: bytes,
) -> dict[str, Any]:
    response = client.post(
        f"/v1/deals/{deal_id}/documents/upload",
        headers={
            "X-IDIS-API-Key": pg_helpers.API_KEY,
            "Content-Type": "application/octet-stream",
        },
        params={
            "filename": filename,
            "doc_type": "DATA_ROOM_FILE",
            "sha256": hashlib.sha256(data).hexdigest(),
            "source_system": "api-smoke",
        },
        content=data,
    )
    assert response.status_code == 201
    return response.json()


def _assert_safe_public_payload(payload: object) -> None:
    encoded = json.dumps(payload)
    for forbidden in FORBIDDEN_PUBLIC_TOKENS:
        assert forbidden not in encoded


def test_api_upload_list_get_selected_run_smoke_consumes_only_selected_document(
    app_engine: Any,
    clean_tables: None,
    monkeypatch: Any,
) -> None:
    """Public API smoke proves uploaded document IDs drive selected run context."""
    _configure_api_key(monkeypatch)
    client, audit_sink, byok_registry = _make_client_context()
    _configure_byok(byok_registry, audit_sink)
    deal_id = _create_deal(client, name="Slice 22 API Smoke Deal")

    selected_upload = _upload_document(
        client,
        deal_id=deal_id,
        filename="selected-smoke.pdf",
        data=pg_helpers._pdf_bytes(),
    )
    other_upload = _upload_document(
        client,
        deal_id=deal_id,
        filename="unselected-smoke.xlsx",
        data=pg_helpers._xlsx_bytes(),
    )
    selected_document_id = selected_upload["document_id"]
    other_document_id = other_upload["document_id"]
    assert selected_document_id != other_document_id
    _assert_safe_public_payload(selected_upload)
    _assert_safe_public_payload(other_upload)

    list_response = client.get(
        f"/v1/deals/{deal_id}/documents",
        headers={"X-IDIS-API-Key": pg_helpers.API_KEY},
    )
    assert list_response.status_code == 200
    listed = list_response.json()
    assert {item["document_id"] for item in listed["items"]} == {
        selected_document_id,
        other_document_id,
    }
    _assert_safe_public_payload(listed)

    get_response = client.get(
        f"/v1/deals/{deal_id}/documents/{selected_document_id}",
        headers={"X-IDIS-API-Key": pg_helpers.API_KEY},
    )
    assert get_response.status_code == 200
    summary = get_response.json()
    assert summary["document_id"] == selected_document_id
    _assert_safe_public_payload(summary)

    captured_preflight_corpus: list[list[dict[str, object]]] = []
    captured_documents: list[list[dict[str, object]]] = []

    class CapturingRunExecutionService:
        def __init__(self, **kwargs: object) -> None:
            self.audit_sink = kwargs["audit_sink"]

        def execute(self, ctx: object) -> RunExecutionResult:
            captured_preflight_corpus.append(ctx.preflight_corpus)  # type: ignore[attr-defined]
            captured_documents.append(ctx.documents)  # type: ignore[attr-defined]
            return RunExecutionResult(
                claimed=True,
                status="SUCCEEDED",
                finished_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            )

    monkeypatch.setattr(runs_route, "RunExecutionService", CapturingRunExecutionService)

    run_response = client.post(
        f"/v1/deals/{deal_id}/runs",
        headers={"X-IDIS-API-Key": pg_helpers.API_KEY},
        json={
            "mode": "SNAPSHOT",
            "source": {
                "type": "deal_documents",
                "document_ids": [selected_document_id],
            },
        },
    )

    assert run_response.status_code == 202
    assert [doc["document_id"] for doc in captured_preflight_corpus[0]] == [selected_document_id]
    assert [doc["document_id"] for doc in captured_documents[0]] == [selected_document_id]
    _assert_safe_public_payload(run_response.json())
    _assert_safe_public_payload(audit_sink.events)


def test_api_selected_run_rejects_cross_deal_document_without_creating_run(
    app_engine: Any,
    clean_tables: None,
    monkeypatch: Any,
) -> None:
    """A document_id from another deal is rejected before run creation/execution."""
    _configure_api_key(monkeypatch)
    client, audit_sink, byok_registry = _make_client_context()
    _configure_byok(byok_registry, audit_sink)
    target_deal_id = _create_deal(client, name="Slice 22 Target Deal")
    other_deal_id = _create_deal(client, name="Slice 22 Other Deal")
    other_upload = _upload_document(
        client,
        deal_id=other_deal_id,
        filename="other-deal.pdf",
        data=pg_helpers._pdf_bytes(),
    )

    class FailingRunExecutionService:
        def __init__(self, **kwargs: object) -> None:
            self.audit_sink = kwargs["audit_sink"]

        def execute(self, ctx: object) -> RunExecutionResult:
            raise AssertionError("RunExecutionService must not execute invalid source")

    monkeypatch.setattr(runs_route, "RunExecutionService", FailingRunExecutionService)

    response = client.post(
        f"/v1/deals/{target_deal_id}/runs",
        headers={"X-IDIS-API-Key": pg_helpers.API_KEY},
        json={
            "mode": "SNAPSHOT",
            "source": {
                "type": "deal_documents",
                "document_ids": [other_upload["document_id"]],
            },
        },
    )

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_RUN_SOURCE"
    assert "run_id" not in response.json()

    with app_engine.begin() as conn:
        result = conn.execute(text("SELECT count(*) FROM runs"))
        assert result.scalar_one() == 0

    missing_response = client.post(
        f"/v1/deals/{target_deal_id}/runs",
        headers={"X-IDIS-API-Key": pg_helpers.API_KEY},
        json={
            "mode": "SNAPSHOT",
            "source": {
                "type": "deal_documents",
                "document_ids": [str(uuid.uuid4())],
            },
        },
    )

    assert missing_response.status_code == 400
    assert missing_response.json()["code"] == "INVALID_RUN_SOURCE"
    assert "run_id" not in missing_response.json()

    with app_engine.begin() as conn:
        result = conn.execute(text("SELECT count(*) FROM runs"))
        assert result.scalar_one() == 0


def test_worker_claimed_persisted_run_source_filters_selected_document_context(
    app_engine: Any,
    clean_tables: None,
    compliant_store: ComplianceEnforcedStore,
    monkeypatch: Any,
) -> None:
    """Worker context must use source as persisted and claimed from the runs table."""
    audit_sink = InMemoryAuditSink()

    with app_engine.begin() as conn:
        pg_helpers._create_deal(conn)
        ingestion_service = IngestionService(
            compliant_store=compliant_store,
            audit_sink=audit_sink,
            db_conn=conn,
        )
        selected_result = ingestion_service.ingest_bytes(
            ctx=IngestionContext(
                tenant_id=pg_helpers.TENANT_ID,
                actor_id=pg_helpers.ACTOR_ID,
                request_id="slice-22-worker-selected",
            ),
            deal_id=pg_helpers.DEAL_ID,
            filename="worker-selected.pdf",
            media_type="application/pdf",
            data=pg_helpers._pdf_bytes(),
            metadata={"source_system": "slice-22-worker-smoke"},
        )
        unselected_result = ingestion_service.ingest_bytes(
            ctx=IngestionContext(
                tenant_id=pg_helpers.TENANT_ID,
                actor_id=pg_helpers.ACTOR_ID,
                request_id="slice-22-worker-unselected",
            ),
            deal_id=pg_helpers.DEAL_ID,
            filename="worker-unselected.xlsx",
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            data=pg_helpers._xlsx_bytes(),
            metadata={"source_system": "slice-22-worker-smoke"},
        )
        assert selected_result.document_id != unselected_result.document_id
        source = {
            "type": "deal_documents",
            "document_ids": [str(selected_result.document_id)],
        }
        runs_repo = PostgresRunsRepository(conn, str(pg_helpers.TENANT_ID))
        runs_repo.create(
            run_id=str(uuid.uuid4()),
            deal_id=str(pg_helpers.DEAL_ID),
            mode="SNAPSHOT",
            source=source,
        )
        claimed_runs = runs_repo.claim_queued_runs(limit=10)
        assert len(claimed_runs) == 1
        assert claimed_runs[0]["source"] == source

        monkeypatch.setattr(
            "idis.pipeline.worker._load_worker_deal_metadata",
            lambda **_: {"company_name": "Synthetic Ingestion Deal"},
        )
        ctx = _default_run_context_factory(
            db_conn=conn,
            tenant_id=str(pg_helpers.TENANT_ID),
            run_data=claimed_runs[0],
            audit_sink=audit_sink,
        )

    assert [doc["document_id"] for doc in ctx.preflight_corpus] == [
        str(selected_result.document_id)
    ]
    assert [doc["document_id"] for doc in ctx.documents] == [str(selected_result.document_id)]
    assert ctx.deal_metadata["company_name"] == "Synthetic Ingestion Deal"
