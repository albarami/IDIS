"""Postgres-backed API run observability tests for Slice 23."""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text

from idis.api.auth import IDIS_API_KEYS_ENV
from idis.audit.postgres_sink import PostgresAuditSink
from idis.audit.sink import InMemoryAuditSink
from idis.models.run_step import STEP_ORDER, RunStep, StepName, StepStatus
from idis.persistence.db import set_tenant_local
from idis.persistence.repositories.documents import PostgresDocumentsRepository
from idis.persistence.repositories.runs import PostgresRunsRepository
from idis.persistence.repositories.task7_source_freeze import freeze_source_package
from idis.pipeline.worker import PipelineWorker, WorkerLeaseConfig
from idis.services.ingestion import IngestionContext
from idis.services.runs.orchestrator import RunOrchestrator, RunStepBlockedError
from idis.storage.compliant_store import ComplianceEnforcedStore
from idis.tools.preview_source_preparation import PreviewSourceFile, prepare_preview_source_package
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


class _CapturingPostgresAuditSink(PostgresAuditSink):
    """Capture safe-event assertions without replacing durable transaction writes."""

    def __init__(self, events: list[dict[str, Any]]) -> None:
        super().__init__()
        self.events = events

    def _insert_event(self, conn: Any, event: dict[str, Any]) -> None:
        super()._insert_event(conn, event)
        self.events.append(event)


def _run_client(monkeypatch: Any) -> tuple[TestClient, _CapturingPostgresAuditSink]:
    _configure_two_tenant_api_keys(monkeypatch)
    client, audit_sink, byok_registry = _make_client_context()
    _configure_byok(byok_registry, audit_sink)
    durable_sink = _CapturingPostgresAuditSink(audit_sink.events)
    client.app.state.audit_sink = durable_sink
    return client, durable_sink


def _prepare_selected_preview(
    app_engine: Any,
    compliant_store: ComplianceEnforcedStore,
    deal_id: str,
) -> dict[str, Any]:
    # Public fictional input, genuine production upload/package mechanics. It is
    # not a canonical acceptance corpus or operator-authorized exclusion.
    prepared = prepare_preview_source_package(
        engine=app_engine,
        ctx=IngestionContext(
            tenant_id=pg_helpers.TENANT_ID,
            actor_id=pg_helpers.ACTOR_ID,
            request_id=str(uuid.uuid4()),
        ),
        deal_id=uuid.UUID(deal_id),
        files=[PreviewSourceFile("pdf", "application/pdf", pg_helpers._pdf_bytes())],
        compliant_store=compliant_store,
        audit_sink=PostgresAuditSink(),
    )
    with app_engine.begin() as conn:
        row = PostgresDocumentsRepository(conn, str(pg_helpers.TENANT_ID)).get_document(
            prepared.document_ids[0]
        )
        assert row is not None
        assert (
            conn.execute(
                text("SELECT source_binding_state FROM documents WHERE document_id=:document"),
                {"document": prepared.document_ids[0]},
            ).scalar_one()
            == "UNBOUND"
        )
        row["prepared_package_id"] = prepared.package_id
        return row


def test_get_run_status_exposes_selected_source_and_safe_step_summaries(
    app_engine: Any,
    clean_tables: None,
    monkeypatch: Any,
    compliant_store: ComplianceEnforcedStore,
) -> None:
    """GET run status should explain the selected-document run without raw content."""
    client, audit_sink = _run_client(monkeypatch)
    deal_id = _create_deal(client, name="Slice 23 Observability Deal")
    selected_upload = _prepare_selected_preview(app_engine, compliant_store, deal_id)
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
    deal_id = _create_deal(client, name="Slice 23 Worker Observability Deal")
    selected = _prepare_selected_preview(app_engine, compliant_store, deal_id)
    unselected = _upload_document(
        client,
        deal_id=deal_id,
        filename="worker-unselected.xlsx",
        data=pg_helpers._xlsx_bytes(),
    )
    assert selected["document_id"] != unselected["document_id"]
    freeze_source_package(
        engine=app_engine,
        tenant_id=str(pg_helpers.TENANT_ID),
        deal_id=deal_id,
        package_id=selected["prepared_package_id"],
    )
    source = {"type": "deal_documents", "document_ids": [selected["document_id"]]}
    run_id = str(uuid.uuid4())
    with app_engine.begin() as conn:
        runs_repo = PostgresRunsRepository(conn, str(pg_helpers.TENANT_ID))
        queued = runs_repo.create(
            run_id=run_id,
            deal_id=deal_id,
            mode="SNAPSHOT",
            source=source,
        )
        assert queued["status"] == "QUEUED" and queued["lease_owner_id"] is None
    worker = PipelineWorker(
        tenant_ids=[str(pg_helpers.TENANT_ID)],
        lease_config=WorkerLeaseConfig(lease_seconds=60, heartbeat_seconds=10),
    )
    assert worker._uses_production_runtime is True
    assert asyncio.run(worker._process_queued_runs()) == 1

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
    _assert_safe_public_payload(audit_sink.events)
    with app_engine.begin() as conn:
        set_tenant_local(conn, str(pg_helpers.TENANT_ID))
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM audit_events WHERE event_type='worker.lease.claimed' "
                    "AND event->'payload'->'safe'->>'run_id'=:run"
                ),
                {"run": run_id},
            ).scalar_one()
            == 1
        )
        assert (
            conn.execute(
                text("SELECT source_binding_state FROM documents WHERE document_id=:document"),
                {"document": unselected["document_id"]},
            ).scalar_one()
            == "UNBOUND"
        )


def test_get_run_status_derives_block_reason_from_step_ledger(
    app_engine: Any,
    clean_tables: None,
    monkeypatch: Any,
    compliant_store: ComplianceEnforcedStore,
) -> None:
    """A failed selected run should expose a durable safe block reason."""
    client, _audit_sink = _run_client(monkeypatch)
    deal_id = _create_deal(client, name="Slice 23 Blocked Deal")
    corrupt_data = b"%PDF-private corrupt bytes that must not be echoed"
    failed_response = client.post(
        f"/v1/deals/{deal_id}/documents/upload",
        headers={
            "X-IDIS-API-Key": pg_helpers.API_KEY,
            "Content-Type": "application/octet-stream",
        },
        params={
            "filename": "blocked-corrupt.pdf",
            "doc_type": "DATA_ROOM_FILE",
            "sha256": hashlib.sha256(corrupt_data).hexdigest(),
            "source_system": "api-smoke",
        },
        content=corrupt_data,
    )
    # Task 4 controlled drift: a durable failed parse returns terminal HTTP 422.
    assert failed_response.status_code == 422
    assert failed_response.json()["code"] == "DOCUMENT_UNREADABLE"
    listed_response = client.get(
        f"/v1/deals/{deal_id}/documents",
        headers={"X-IDIS-API-Key": pg_helpers.API_KEY},
    )
    assert listed_response.status_code == 200
    failed_upload = listed_response.json()["items"][0]
    assert failed_upload["parse_status"] == "FAILED"

    from idis.api.routes import runs as routes

    forbidden_calls: list[str] = []

    def forbidden_boundary(*_args: Any, **_kwargs: Any) -> Any:
        forbidden_calls.append("post-admission")
        raise AssertionError("unfrozen corrupt input must not reach a claim or producer context")

    with monkeypatch.context() as negative:
        negative.setattr(routes, "build_run_context", forbidden_boundary)
        negative.setattr(
            PostgresRunsRepository, "claim_queued_run_for_execution", forbidden_boundary
        )
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
    # AUTH-FROZEN-PREVIEWS forbids admitting this unreadable, unfrozen upload.
    # Preserve that security negative; it is not a NO_USABLE frozen-corpus proof.
    assert run_response.status_code == 409
    assert run_response.json()["code"] == "SOURCE_PACKAGE_REQUIRED"
    assert run_response.json().get("details") is None
    assert forbidden_calls == []
    with app_engine.begin() as conn:
        set_tenant_local(conn, str(pg_helpers.TENANT_ID))
        for table in ("runs", "run_steps", "claims", "webhook_delivery_attempts"):
            assert conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0

    # The original durable GET ledger/privacy oracle uses genuine frozen input
    # and an explicit controlled producer fault, not fabricated empty evidence.
    valid_upload = _prepare_selected_preview(app_engine, compliant_store, deal_id)
    actual_build = routes.build_run_context
    calls: list[str] = []

    def controlled_fault(**_kwargs: Any) -> Any:
        calls.append("extract")
        raise RunStepBlockedError(
            "C3_CONTROLLED_EXTRACT_FAILURE",
            "Private controlled test failure must not appear in public output",
        )

    def controlled_context(**kwargs: Any) -> Any:
        context = actual_build(**kwargs)
        context.extract_fn = controlled_fault
        return context

    monkeypatch.setattr(routes, "build_run_context", controlled_context)
    run_response = client.post(
        f"/v1/deals/{deal_id}/runs",
        headers={"X-IDIS-API-Key": pg_helpers.API_KEY},
        json={
            "mode": "SNAPSHOT",
            "source": {
                "type": "deal_documents",
                "document_ids": [valid_upload["document_id"]],
            },
        },
    )
    assert run_response.status_code == 202, run_response.text
    assert calls == ["extract"]
    assert run_response.json()["block_reason"] == "C3_CONTROLLED_EXTRACT_FAILURE"

    status_response = client.get(
        f"/v1/runs/{run_response.json()['run_id']}",
        headers={"X-IDIS-API-Key": pg_helpers.API_KEY},
    )

    assert status_response.status_code == 200
    body = status_response.json()
    assert body["status"] == "FAILED"
    assert body["block_reason"] == "C3_CONTROLLED_EXTRACT_FAILURE"
    failed_steps = [step for step in body["steps"] if step["status"] == "FAILED"]
    assert failed_steps[-1]["error"]["code"] == "C3_CONTROLLED_EXTRACT_FAILURE"
    assert failed_steps[-1]["error"]["message"] != (
        "No usable documents remain after document preflight"
    )
    _assert_safe_public_payload(body)
    assert "Private controlled test failure" not in json.dumps(body)
    assert any(step["status"] == "COMPLETED" for step in body["steps"])
    assert len(failed_steps) == 1
    assert failed_steps[0]["step_name"] == "EXTRACT"
    assert body["steps"][-1] == failed_steps[0]


def test_get_run_status_cross_tenant_returns_404_without_existence_leak(
    app_engine: Any,
    clean_tables: None,
    monkeypatch: Any,
    compliant_store: ComplianceEnforcedStore,
) -> None:
    """Cross-tenant run reads must not reveal run existence or source details."""
    client, _audit_sink = _run_client(monkeypatch)
    deal_id = _create_deal(client, name="Slice 23 Tenant Isolation Deal")
    upload = _prepare_selected_preview(app_engine, compliant_store, deal_id)
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


def test_public_run_summary_sanitizer_removes_private_analysis_payloads() -> None:
    """Public run status must not expose private Layer 2 claim text."""
    from idis.api.routes.runs import _safe_public_run_summary

    unsafe = {
        "status": "completed",
        "agent_count": 8,
        "_analysis_bundle": {
            "reports": [
                {
                    "analysis_sections": {
                        "investment_thesis_evidence": {
                            "content": (
                                "Upside: slice27_pdf_confidential_revenue_marker "
                                "states revenue was 11.1 million."
                            )
                        }
                    }
                }
            ]
        },
        "_analysis_context": {
            "claim_registry": {
                "claim-001": {
                    "claim_text": (
                        "slice27_pdf_confidential_revenue_marker states revenue was 11.1 million."
                    )
                }
            }
        },
        "_scorecard": {
            "dimension_scores": {
                "market": {
                    "rationale": (
                        "slice27_pdf_confidential_revenue_marker shows qualified pipeline growth."
                    )
                }
            }
        },
        "message": "slice27_pdf_confidential_revenue_marker should not leak",
    }

    safe = _safe_public_run_summary(unsafe)
    encoded = json.dumps(safe, default=str)

    assert safe == {"status": "completed", "agent_count": 8}
    assert "slice27_pdf_confidential_revenue_marker" not in encoded
    assert "_analysis_bundle" not in encoded
    assert "_analysis_context" not in encoded
    assert "_scorecard" not in encoded


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
