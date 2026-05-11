"""Postgres-backed API run observability tests for Slice 23."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient

from idis.api.auth import IDIS_API_KEYS_ENV
from idis.audit.sink import InMemoryAuditSink
from idis.models.run_step import STEP_ORDER, RunStep, StepName, StepStatus
from idis.persistence.repositories.run_steps import PostgresRunStepsRepository
from idis.persistence.repositories.runs import PostgresRunsRepository
from idis.pipeline.worker import _default_run_context_factory
from idis.services.ingestion import IngestionContext, IngestionService
from idis.services.runs.execution import RunExecutionService
from idis.services.runs.orchestrator import RunOrchestrator
from idis.storage.compliant_store import ComplianceEnforcedStore
from tests import test_ingestion_persists_documents_postgres as pg_helpers
from tests.test_api_upload_to_run_smoke_postgres import (
    _assert_safe_public_payload,
    _configure_byok,
    _create_deal,
    _make_client_context,
    _upload_document,
)

pytest_plugins = ("tests.test_ingestion_persists_documents_postgres",)

TENANT_B_ID = "22222222-2222-2222-2222-222222222222"
API_KEY_TENANT_B = "test-key-tenant-b-run-observability"


def _configure_two_tenant_api_keys(monkeypatch: Any) -> None:
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
                },
                API_KEY_TENANT_B: {
                    "tenant_id": TENANT_B_ID,
                    "actor_id": "actor-run-observability-b",
                    "name": "Tenant B",
                    "timezone": "UTC",
                    "data_region": "me-south-1",
                    "roles": ["ANALYST", "ADMIN"],
                },
            }
        ),
    )


def _run_client(monkeypatch: Any) -> tuple[TestClient, InMemoryAuditSink]:
    _configure_two_tenant_api_keys(monkeypatch)
    client, audit_sink, byok_registry = _make_client_context()
    _configure_byok(byok_registry, audit_sink)
    return client, audit_sink


def test_get_run_status_exposes_selected_source_and_safe_step_summaries(
    app_engine: Any,
    clean_tables: None,
    monkeypatch: Any,
) -> None:
    """GET run status should explain the selected-document run without raw content."""
    client, audit_sink = _run_client(monkeypatch)
    deal_id = _create_deal(client, name="Slice 23 Observability Deal")
    selected_upload = _upload_document(
        client,
        deal_id=deal_id,
        filename="selected-observability.pdf",
        data=pg_helpers._pdf_bytes(),
    )
    unselected_upload = _upload_document(
        client,
        deal_id=deal_id,
        filename="unselected-observability.xlsx",
        data=pg_helpers._xlsx_bytes(),
    )
    selected_document_id = selected_upload["document_id"]
    assert selected_document_id != unselected_upload["document_id"]

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
    assert all("summary" not in step for step in run_response.json()["steps"])
    run_id = run_response.json()["run_id"]

    status_response = client.get(
        f"/v1/runs/{run_id}",
        headers={"X-IDIS-API-Key": pg_helpers.API_KEY},
    )

    assert status_response.status_code == 200
    body = status_response.json()
    assert body["run_id"] == run_id
    assert body["mode"] == "SNAPSHOT"
    assert body["source"] == {
        "type": "deal_documents",
        "document_ids": [selected_document_id],
    }
    assert body["status"] in {"SUCCEEDED", "FAILED"}
    assert body["steps"]
    assert [step["step_name"] for step in body["steps"]][:4] == [
        "DATA_ROOM_INVENTORY_PACKAGE",
        "DATA_ROOM_INGESTION_HANDOFF",
        "INGEST_CHECK",
        "DOCUMENT_PREFLIGHT",
    ]
    assert all("summary" in step for step in body["steps"])
    _assert_safe_public_payload(body)
    _assert_safe_public_payload(audit_sink.events)


def test_get_run_status_reflects_worker_updated_persisted_run(
    app_engine: Any,
    clean_tables: None,
    compliant_store: ComplianceEnforcedStore,
    monkeypatch: Any,
) -> None:
    """GET run status should read status and steps written by the worker path."""
    client, audit_sink = _run_client(monkeypatch)

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
                request_id="slice-23-worker-selected",
            ),
            deal_id=pg_helpers.DEAL_ID,
            filename="worker-observable.pdf",
            media_type="application/pdf",
            data=pg_helpers._pdf_bytes(),
            metadata={"source_system": "slice-23-worker-observability"},
        )
        unselected_result = ingestion_service.ingest_bytes(
            ctx=IngestionContext(
                tenant_id=pg_helpers.TENANT_ID,
                actor_id=pg_helpers.ACTOR_ID,
                request_id="slice-23-worker-unselected",
            ),
            deal_id=pg_helpers.DEAL_ID,
            filename="worker-unselected.xlsx",
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            data=pg_helpers._xlsx_bytes(),
            metadata={"source_system": "slice-23-worker-observability"},
        )
        assert selected_result.document_id != unselected_result.document_id
        source = {
            "type": "deal_documents",
            "document_ids": [str(selected_result.document_id)],
        }
        runs_repo = PostgresRunsRepository(conn, str(pg_helpers.TENANT_ID))
        run_id = str(uuid.uuid4())
        runs_repo.create(
            run_id=run_id,
            deal_id=str(pg_helpers.DEAL_ID),
            mode="SNAPSHOT",
            source=source,
        )
        claimed = runs_repo.claim_queued_runs(limit=10)
        assert len(claimed) == 1

        monkeypatch.setattr(
            "idis.pipeline.worker._load_worker_deal_metadata",
            lambda **_: {"company_name": "Synthetic Ingestion Deal"},
        )
        ctx = _default_run_context_factory(
            db_conn=conn,
            tenant_id=str(pg_helpers.TENANT_ID),
            run_data=claimed[0],
            audit_sink=audit_sink,
        )
        service = RunExecutionService(
            audit_sink=audit_sink,
            runs_repo=runs_repo,
            run_steps_repo=PostgresRunStepsRepository(conn, str(pg_helpers.TENANT_ID)),
        )
        service.execute(ctx)

    status_response = client.get(
        f"/v1/runs/{run_id}",
        headers={"X-IDIS-API-Key": pg_helpers.API_KEY},
    )

    assert status_response.status_code == 200
    body = status_response.json()
    assert body["status"] in {"SUCCEEDED", "FAILED"}
    assert body["mode"] == "SNAPSHOT"
    assert body["source"] == source
    assert body["steps"]
    _assert_safe_public_payload(body)


def test_get_run_status_derives_block_reason_from_step_ledger(
    app_engine: Any,
    clean_tables: None,
    monkeypatch: Any,
) -> None:
    """A failed selected run should expose a durable safe block reason."""
    client, _audit_sink = _run_client(monkeypatch)
    deal_id = _create_deal(client, name="Slice 23 Blocked Deal")
    failed_upload = _upload_document(
        client,
        deal_id=deal_id,
        filename="blocked-corrupt.pdf",
        data=b"%PDF-private corrupt bytes that must not be echoed",
    )
    assert failed_upload["parse_status"] == "FAILED"

    run_response = client.post(
        f"/v1/deals/{deal_id}/runs",
        headers={"X-IDIS-API-Key": pg_helpers.API_KEY},
        json={
            "mode": "SNAPSHOT",
            "source": {
                "type": "deal_documents",
                "document_ids": [failed_upload["document_id"]],
            },
        },
    )
    assert run_response.status_code == 202
    assert run_response.json()["block_reason"] == "NO_USABLE_DOCUMENTS"

    status_response = client.get(
        f"/v1/runs/{run_response.json()['run_id']}",
        headers={"X-IDIS-API-Key": pg_helpers.API_KEY},
    )

    assert status_response.status_code == 200
    body = status_response.json()
    assert body["status"] == "FAILED"
    assert body["block_reason"] == "NO_USABLE_DOCUMENTS"
    failed_steps = [step for step in body["steps"] if step["status"] == "FAILED"]
    assert failed_steps[-1]["error"]["code"] == "NO_USABLE_DOCUMENTS"
    assert failed_steps[-1]["error"]["message"] != (
        "No usable documents remain after document preflight"
    )
    _assert_safe_public_payload(body)


def test_get_run_status_cross_tenant_returns_404_without_existence_leak(
    app_engine: Any,
    clean_tables: None,
    monkeypatch: Any,
) -> None:
    """Cross-tenant run reads must not reveal run existence or source details."""
    client, _audit_sink = _run_client(monkeypatch)
    deal_id = _create_deal(client, name="Slice 23 Tenant Isolation Deal")
    upload = _upload_document(
        client,
        deal_id=deal_id,
        filename="tenant-isolation.pdf",
        data=pg_helpers._pdf_bytes(),
    )
    run_response = client.post(
        f"/v1/deals/{deal_id}/runs",
        headers={"X-IDIS-API-Key": pg_helpers.API_KEY},
        json={
            "mode": "SNAPSHOT",
            "source": {
                "type": "deal_documents",
                "document_ids": [upload["document_id"]],
            },
        },
    )
    assert run_response.status_code == 202
    run_id = run_response.json()["run_id"]

    cross_tenant_response = client.get(
        f"/v1/runs/{run_id}",
        headers={"X-IDIS-API-Key": API_KEY_TENANT_B},
    )

    assert cross_tenant_response.status_code == 404
    body = cross_tenant_response.json()
    assert body["code"] == "NOT_FOUND"
    encoded = json.dumps(body)
    assert run_id not in encoded
    assert upload["document_id"] not in encoded


def test_public_run_summary_sanitizer_removes_content_like_keys() -> None:
    """The public sanitizer must be a privacy filter, not JSON coercion only."""
    from idis.api.routes.runs import _safe_public_run_summary

    unsafe = {
        "document_id": "doc-safe",
        "document_count": 1,
        "content_hash": "a" * 64,
        "sha256": "b" * 64,
        "file_id": "file-private",
        "artifact_id": "artifact-private",
        "files": [{"file_id": "file-private", "relative_path": "Finance/model.xlsx"}],
        "safe_looking_base64": "U2VjcmV0IGZpbGUgY29udGVudA==",
        "safe_looking_path": "/home/acme/private/model.xlsx",
        "safe_looking_error": "ValueError: Revenue was 10M in source document.",
        "note": "U2VjcmV0IGZpbGUgY29udGVudA==",
        "location": "/home/acme/private/model.xlsx",
        "message": "ValueError: parser failed on private document",
        "text_excerpt": "Revenue was 10M.",
        "nested": {
            "span_text": "EBITDA was 2M.",
            "reason_code": "NO_USABLE_DOCUMENTS",
        },
        "local_path": "C:/secret/data-room/model.xlsx",
    }

    safe = _safe_public_run_summary(unsafe)

    assert safe == {
        "document_id": "doc-safe",
        "document_count": 1,
        "nested": {"reason_code": "NO_USABLE_DOCUMENTS"},
    }


def test_public_step_error_message_is_generic_and_capped() -> None:
    """Public errors should expose stable codes without raw exception strings."""
    from idis.api.routes.runs import _build_step_responses

    step = RunStep(
        step_id=str(uuid.uuid4()),
        run_id=str(uuid.uuid4()),
        tenant_id=str(pg_helpers.TENANT_ID),
        step_name=StepName.DOCUMENT_PREFLIGHT,
        step_order=STEP_ORDER[StepName.DOCUMENT_PREFLIGHT],
        status=StepStatus.FAILED,
        error_code="NO_USABLE_DOCUMENTS",
        error_message="No usable documents remain after document preflight: Revenue was 10M.",
        started_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        finished_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    )

    response = _build_step_responses([step])[0]

    assert response.error is not None
    assert response.error.code == "NO_USABLE_DOCUMENTS"
    assert response.error.message == "Run step failed; see error code for details."


def test_failed_step_audit_message_is_generic() -> None:
    """Audit details must not echo exception strings from failed steps."""
    from idis.persistence.repositories.run_steps import InMemoryRunStepsRepository

    audit_sink = InMemoryAuditSink()
    repo = InMemoryRunStepsRepository(str(pg_helpers.TENANT_ID))
    orchestrator = RunOrchestrator(audit_sink=audit_sink, run_steps_repo=repo)
    step = repo.create(
        RunStep(
            step_id=str(uuid.uuid4()),
            run_id=str(uuid.uuid4()),
            tenant_id=str(pg_helpers.TENANT_ID),
            step_name=StepName.DOCUMENT_PREFLIGHT,
            step_order=STEP_ORDER[StepName.DOCUMENT_PREFLIGHT],
            status=StepStatus.RUNNING,
            started_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        )
    )

    orchestrator._fail_step(  # noqa: SLF001 - regression coverage for audit hardening.
        step,
        ValueError("Revenue was 10M from /home/acme/private/model.xlsx"),
    )

    encoded = json.dumps(audit_sink.events)
    assert "Revenue was 10M" not in encoded
    assert "/home/acme/private/model.xlsx" not in encoded
    assert "Run step failed; see error code for details." in encoded


def test_persisted_run_source_projection_ignores_extra_fields_and_rejects_paths() -> None:
    """GET source projection should not leak legacy or malformed source fields."""
    from idis.api.routes.runs import _run_source_from_storage

    projected = _run_source_from_storage(
        {
            "type": "deal_documents",
            "document_ids": ["doc-safe"],
            "local_path": "/home/acme/private/data-room",
        }
    )

    assert projected is not None
    assert projected.to_storage_dict() == {
        "type": "deal_documents",
        "document_ids": ["doc-safe"],
    }
    assert (
        _run_source_from_storage(
            {
                "type": "deal_documents",
                "document_ids": ["/home/acme/private/model.xlsx"],
            }
        )
        is None
    )
