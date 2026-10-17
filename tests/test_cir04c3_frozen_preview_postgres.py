"""Application-role C3 mechanics; component cases are not preview acceptance."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import DBAPIError

from idis.api.auth import IDIS_API_KEYS_ENV
from idis.api.main import create_app
from idis.audit.canonical import build_canonical_audit_event
from idis.audit.postgres_sink import PostgresAuditSink
from idis.audit.sink import AuditSinkError
from idis.idempotency.store import SqliteIdempotencyStore
from idis.models.run_step import FULL_STEPS, SNAPSHOT_STEPS, RunStep, StepName, StepStatus
from idis.persistence.db import get_admin_engine, set_tenant_local
from idis.persistence.repositories.data_room_packages import PostgresDataRoomPackagesRepository
from idis.persistence.repositories.documents import PostgresDocumentsRepository
from idis.persistence.repositories.run_steps import PostgresRunStepsRepository
from idis.persistence.repositories.runs import PostgresRunsRepository, WorkerLeaseFencedError
from idis.persistence.repositories.task7_completion import PostgresTask7CompletionPort
from idis.persistence.repositories.task7_source_freeze import (
    PostgresTask7SourceFreezeRepository,
    Task7SourceFreezeError,
    _sha256_json,
    _validated_span_locator,
    build_source_freeze_plan,
    freeze_source_package,
    validate_locked_source_snapshot,
)
from idis.pipeline import contract_registry
from idis.pipeline.task7_completion import Task7LeaseScope
from idis.pipeline.task7_lifecycle import rehydrate_strict_run
from idis.pipeline.worker import _lease_transition_audit_emitter
from idis.runtime.dependency_probes import collect_worker_claim_facts
from idis.services.data_room.package_service import create_data_room_package
from idis.services.ingestion import IngestionService
from idis.services.runs.orchestrator import RunContext, RunOrchestrator, RunStepBlockedError
from idis.storage.compliant_store import ComplianceEnforcedStore
from idis.storage.filesystem_store import FilesystemObjectStore
from idis.tools.preview_source_preparation import create_preview_preparation
from tests._cir04c2_admission_helpers import (
    SyntheticFrozenSource,
    admission_repository,
    admit_synthetic_proof,
    insert_synthetic_proof,
)
from tests.abac_seed import seed_deal_access
from tests.slice100_task3._support import build_source_fixture, contract_kwargs
from tests.slice100_task7.test_task7_restart_rehydration_red import _complete_hermetic_step
from tests.slice100_task7.test_task7_source_freeze_repository_red import _locator_identity_cases
from tests.test_cir04c3_ingestion_provenance import _scope, _upload
from tests.test_docx_parser import create_test_docx
from tests.test_pdf_parser import create_test_pdf
from tests.test_pptx_parser import create_test_pptx
from tests.test_xlsx_parser import create_test_xlsx

pytest_plugins = ("tests.test_cir04c3_inline_claim_postgres",)


def test_snapshot_sanad_missing_claim_is_rejected_by_installed_app_role_fk(
    app_engine: Engine,
    tmp_path: Path,
    record_testsuite_property: Callable[[str, object], None],
) -> None:
    """Transitive FK residual, not a claim about update_grade checking rowcount."""
    import asyncio

    from idis.persistence.repositories.claims import SanadsRepository
    from idis.pipeline.worker import PipelineWorker, WorkerLeaseConfig

    tenant, deal, uploaded, package = _real_open_native_package(app_engine, tmp_path, "pdf")
    freeze_source_package(engine=app_engine, tenant_id=tenant, deal_id=deal, package_id=package)
    run_id = str(uuid.uuid4())
    with app_engine.begin() as conn:
        PostgresRunsRepository(conn, tenant).create(
            run_id=run_id,
            deal_id=deal,
            mode="SNAPSHOT",
            source={"type": "deal_documents", "document_ids": [str(uploaded.document_id)]},
        )
    worker = PipelineWorker(
        tenant_ids=[tenant],
        lease_config=WorkerLeaseConfig(lease_seconds=60, heartbeat_seconds=10),
    )
    assert worker._uses_production_runtime
    assert asyncio.run(worker._process_queued_runs()) == 1
    with app_engine.begin() as conn:
        assert PostgresRunsRepository(conn, tenant).get(run_id)["status"] == "SUCCEEDED"
        evidence = conn.execute(
            text("SELECT evidence_id::text FROM evidence_items WHERE deal_id=:deal LIMIT 1"),
            {"deal": deal},
        ).scalar_one()
    source_before = _source_rows(app_engine, tenant)
    business_before = _snapshot_business_rows(app_engine, tenant)
    assert business_before["claims"] and business_before["evidence_items"]
    with pytest.raises(DBAPIError) as error, app_engine.begin() as conn:
        # The other parents are genuine committed rows; only this claim UUID is absent.
        SanadsRepository(conn, tenant).create(
            sanad_id=str(uuid.uuid4()),
            claim_id=str(uuid.uuid4()),
            deal_id=deal,
            primary_evidence_id=evidence,
        )
    assert error.value.orig.pgcode == "23503"
    constraint = error.value.orig.diag.constraint_name
    with app_engine.begin() as conn:
        installed = conn.execute(
            text(
                "SELECT contype,convalidated,conrelid::regclass::text,confrelid::regclass::text "
                "FROM pg_constraint WHERE conname=:name AND conrelid='sanads'::regclass"
            ),
            {"name": constraint},
        ).one()
        assert tuple(installed) == ("f", True, "sanads", "claims")
    record_testsuite_property("C3_missing_claim_installed_fk", constraint)
    assert _snapshot_business_rows(app_engine, tenant) == business_before
    assert _source_rows(app_engine, tenant) == source_before


@pytest.mark.parametrize("mutation", ["locator", "content", "parse_attempt", "delete"])
def test_snapshot_frozen_span_cannot_change_between_capture_and_complete(
    app_engine: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    record_testsuite_property: Callable[[str, object], None],
    mutation: str,
) -> None:
    """Ordinary-app immutability timing proof, not an approved-erasure race census."""
    import asyncio

    import idis.pipeline.worker as worker_module

    tenant, deal, uploaded, package = _real_open_native_package(
        app_engine,
        tmp_path,
        "pdf",
        pdf_lines=["PUBLIC SYNTHETIC PREVIEW SOURCE", "The synthetic team has twelve members."],
    )
    freeze_source_package(engine=app_engine, tenant_id=tenant, deal_id=deal, package_id=package)
    run_id = str(uuid.uuid4())
    with app_engine.begin() as conn:
        PostgresRunsRepository(conn, tenant).create(
            run_id=run_id,
            deal_id=deal,
            mode="SNAPSHOT",
            source={"type": "deal_documents", "document_ids": [str(uploaded.document_id)]},
        )
    original = worker_module.build_postgres_claimed_run_runtime
    observed: list[tuple[str, str]] = []

    def build(**kwargs: Any) -> Any:
        runtime = original(**kwargs)
        ctx = runtime.context
        guard = ctx.provider_output_guard_fn
        composition = ctx.snapshot_output_composition
        assert guard is not None and composition is not None

        def guarded(**values: Any) -> None:
            guard(**values)
            if values["step_name"] != StepName.EXTRACT or values["phase"] != "after":
                return
            draft = composition._drafts[StepName.EXTRACT]
            assert draft.claim_rows and draft.ready_summary is not None and not draft.poisoned
            assert StepName.EXTRACT not in ctx.methodology_pending_native_output_writes
            assert all(not rows for rows in _snapshot_business_rows(app_engine, tenant).values())
            before = _source_rows(app_engine, tenant)
            span = draft.claim_rows[0].primary_span_id
            assert span is not None and span in draft.allowed_span_ids
            statement = {
                "locator": (
                    "UPDATE document_spans SET locator=jsonb_set(locator,'{line}','2'::jsonb)"
                ),
                "content": "UPDATE document_spans SET content_hash=repeat('f',64)",
                "parse_attempt": "UPDATE document_spans SET parse_attempt_id=NULL",
                "delete": "DELETE FROM document_spans",
            }[mutation]
            with pytest.raises(DBAPIError) as error, app_engine.begin() as conn:
                set_tenant_local(conn, tenant)
                conn.execute(text(statement + " WHERE span_id=:span"), {"span": span})
            assert error.value.orig.pgcode == "40001"
            assert error.value.orig.diag.message_primary == "TASK7_SOURCE_FREEZE_FENCED"
            assert _source_rows(app_engine, tenant) == before
            observed.append((error.value.orig.pgcode, error.value.orig.diag.message_primary))

        ctx.provider_output_guard_fn = guarded
        return runtime

    monkeypatch.setattr(worker_module, "build_postgres_claimed_run_runtime", build)
    worker = worker_module.PipelineWorker(
        tenant_ids=[tenant],
        lease_config=worker_module.WorkerLeaseConfig(lease_seconds=60, heartbeat_seconds=10),
    )
    assert worker._uses_production_runtime
    assert asyncio.run(worker._process_queued_runs()) == 1
    assert observed == [("40001", "TASK7_SOURCE_FREEZE_FENCED")]
    with app_engine.begin() as conn:
        assert PostgresRunsRepository(conn, tenant).get(run_id)["status"] == "SUCCEEDED"
        steps = PostgresRunStepsRepository(conn, tenant).get_by_run_id(run_id)
        assert [step.step_name for step in steps] == SNAPSHOT_STEPS
        assert all(step.status == StepStatus.COMPLETED for step in steps)
        backed = conn.execute(
            text(
                "SELECT count(*) FROM claims c "
                "JOIN evidence_items e ON (e.tenant_id,e.deal_id,e.claim_id)="
                "(c.tenant_id,c.deal_id,c.claim_id) "
                "JOIN sanads s ON (s.tenant_id,s.deal_id,s.claim_id)="
                "(c.tenant_id,c.deal_id,c.claim_id) "
                "JOIN document_spans ds ON ds.span_id=e.source_span_id "
                "AND ds.tenant_id=e.tenant_id WHERE c.deal_id=:deal "
                "AND c.primary_span_id=e.source_span_id AND ds.source_binding_state='BOUND'"
            ),
            {"deal": deal},
        ).scalar_one()
        assert backed > 0
    record_testsuite_property("C3_frozen_span_" + mutation + "_backed_join_count", backed)


def _real_open_native_package(
    app_engine: Engine,
    tmp_path: Path,
    extension: str,
    *,
    pdf_lines: list[str] | None = None,
) -> tuple[str, str, Any, str]:
    # This is real production mechanics on public synthetic bytes, not a fund
    # acceptance source package and not a native canonical FULL run.
    tenant, deal = _scope(app_engine)
    phrase = "PUBLIC SYNTHETIC PREVIEW SOURCE"
    if extension == "pdf":
        data, media_type = create_test_pdf(pdf_lines or [phrase]), "application/pdf"
    elif extension == "xlsx":
        data = create_test_xlsx({"Synthetic": [[phrase]]})
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    elif extension.startswith("docx-"):
        data = (
            create_test_docx([phrase])
            if extension.endswith("paragraph")
            else create_test_docx([], tables=[[[phrase]]])
        )
        extension, media_type = (
            "docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    elif extension.startswith("pptx-"):
        data = (
            create_test_pptx([[phrase]])
            if extension.endswith("paragraph")
            else create_test_pptx([[]], tables=[(0, [[phrase]])])
        )
        extension, media_type = (
            "pptx",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        )
    elif extension == "html":
        data, media_type = f"<html><body><p>{phrase}</p></body></html>".encode(), "text/html"
    else:
        assert extension == "txt"
        data, media_type = (phrase + "\n").encode(), "text/plain"
    prep = create_preview_preparation(tenant_id=tenant, deal_id=deal, files=[(extension, data)])
    store = ComplianceEnforcedStore(inner_store=FilesystemObjectStore(base_dir=tmp_path))
    with app_engine.begin() as conn:
        service = IngestionService(compliant_store=store, db_conn=conn)
        result = _upload(
            service,
            tenant,
            deal,
            data,
            name=prep.files[0].generated_upload_name,
            metadata=prep.upload_metadata(0),
            media_type=media_type,
        )
        assert result.success
    with app_engine.begin() as conn:
        documents = PostgresDocumentsRepository(conn, tenant).list_documents_by_deal(deal)
        manifest = prep.build_manifest(documents)
        package = create_data_room_package(
            repo=PostgresDataRoomPackagesRepository(conn, tenant),
            tenant_id=tenant,
            deal_id=deal,
            created_by_actor_id="C3-test",
            created_by_actor_type="SERVICE",
            document_ids=[str(result.document_id)],
            documents=documents,
            source_manifest=manifest,
        )
    package_id = package["package_id"]
    return tenant, deal, result, package_id


def _source_rows(engine: Engine, tenant: str) -> dict[str, list[str]]:
    with engine.begin() as conn:
        set_tenant_local(conn, tenant)
        return {
            table: list(
                conn.execute(
                    text(
                        f"SELECT to_jsonb(row_value)::text FROM {table} row_value "
                        "WHERE tenant_id=CAST(:tenant AS uuid) ORDER BY to_jsonb(row_value)::text"
                    ),
                    {"tenant": tenant},
                ).scalars()
            )
            for table in (
                "data_room_packages",
                "data_room_package_files",
                "document_artifacts",
                "documents",
                "document_parse_attempts",
                "document_spans",
                "source_lineage_objects",
                "audit_events",
            )
        }


def _preview_client(
    monkeypatch: pytest.MonkeyPatch,
    tenant: str,
    deal: str,
    audit_sink: PostgresAuditSink | None = None,
) -> TestClient:
    key = "synthetic-c3-preview-api-key"
    actor = "synthetic-c3-preview-actor"
    monkeypatch.setenv(
        IDIS_API_KEYS_ENV,
        json.dumps(
            {
                key: {
                    "tenant_id": tenant,
                    "actor_id": actor,
                    "name": "C3 synthetic",
                    "timezone": "UTC",
                    "data_region": "me-south-1",
                    "roles": ["ANALYST", "ADMIN"],
                }
            }
        ),
    )
    seed_deal_access(tenant, deal, actor)
    client = TestClient(
        create_app(
            audit_sink=audit_sink or PostgresAuditSink(),
            idempotency_store=SqliteIdempotencyStore(in_memory=True),
        ),
        raise_server_exceptions=False,
    )
    client.headers["X-IDIS-API-Key"] = key
    return client


def test_api_unbound_upload_without_package_never_inserts_or_claims_run(
    app_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant, deal = _scope(app_engine)
    store = ComplianceEnforcedStore(inner_store=FilesystemObjectStore(base_dir=tmp_path))
    with app_engine.begin() as conn:
        result = _upload(
            IngestionService(compliant_store=store, db_conn=conn),
            tenant,
            deal,
            create_test_pdf(["PUBLIC SYNTHETIC UNBOUND BLOCK"]),
        )
        assert result.success
    client = _preview_client(monkeypatch, tenant, deal)
    before = _source_rows(app_engine, tenant)
    response = client.post(f"/v1/deals/{deal}/runs", json={"mode": "SNAPSHOT"})
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "SOURCE_PACKAGE_REQUIRED"
    assert response.json().get("details") is None
    after = _source_rows(app_engine, tenant)
    assert {k: v for k, v in before.items() if k != "audit_events"} == {
        k: v for k, v in after.items() if k != "audit_events"
    }
    with app_engine.begin() as conn:
        set_tenant_local(conn, tenant)
        for table in ("runs", "run_steps", "claims"):
            assert conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0


def test_api_real_native_package_is_frozen_before_closed_snapshot_execution(
    app_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from idis.api.routes import runs as routes

    tenant, deal, uploaded, package_id = _real_open_native_package(app_engine, tmp_path, "pdf")
    client = _preview_client(monkeypatch, tenant, deal)
    contexts = []
    actual_build = routes.build_run_context

    def observe_context(**kwargs: Any) -> Any:
        context = actual_build(**kwargs)
        contexts.append(context)
        # The source gate must already have committed the real freeze before
        # any execution context, run claim, extractor, or other producer exists.
        with app_engine.begin() as conn:
            set_tenant_local(conn, tenant)
            assert (
                conn.execute(
                    text("SELECT status FROM data_room_packages WHERE package_id=:package"),
                    {"package": package_id},
                ).scalar_one()
                == "SEALED"
            )
        return context

    monkeypatch.setattr(routes, "build_run_context", observe_context)
    response = client.post(f"/v1/deals/{deal}/runs", json={"mode": "SNAPSHOT"})
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "SUCCEEDED", response.text
    assert len(contexts) == 1 and contexts[0].step_boundary_uow_fn is None
    with app_engine.begin() as conn:
        set_tenant_local(conn, tenant)
        run = PostgresRunsRepository(conn, tenant).get(response.json()["run_id"])
        assert run is not None
        assert run["source"] == {
            "type": "deal_documents",
            "document_ids": [str(uploaded.document_id)],
        }
        assert (
            run["pipeline_contract_version"],
            run["pipeline_resume_state"],
            run["strict_preflight_proof_id"],
        ) == ("snapshot-v1", "NON_RESUMABLE", None)
        assert run["lease_fencing_token"] == 1
        steps = PostgresRunStepsRepository(conn, tenant).get_by_run_id(run["run_id"])
        assert steps and all(step.status.value == "COMPLETED" for step in steps)


def test_request_bound_audit_channels_share_actual_transaction_and_rollback(
    app_engine: Engine,
) -> None:
    from idis.audit.request_bound_sink import RequestBoundPostgresAuditSink

    tenant, deal = _scope(app_engine)
    event = build_canonical_audit_event(
        tenant_id=tenant,
        event_type="deal.run.claimed",
        severity="LOW",
        summary="Synthetic claim",
        actor_id="idis-pipeline-worker",
        request_id=deal,
        method="POST",
        path=f"/v1/runs/{deal}/worker-lease",
        status_code=200,
        resource_type="deal",
        resource_id=deal,
        deal_id=deal,
    )
    original = deepcopy(event)
    with pytest.raises(RuntimeError, match="SYNTHETIC_ROLLBACK"), app_engine.begin() as conn:
        set_tenant_local(conn, tenant)
        sink = RequestBoundPostgresAuditSink(
            connection=conn,
            delegate=PostgresAuditSink(),
            tenant_id=tenant,
            actor_id="actual-api-actor",
            actor_type="HUMAN",
            actor_roles=("ANALYST",),
            request_id=deal,
            method="POST",
            path=f"/v1/deals/{deal}/runs",
            status_code=202,
        )
        sink.emit(event)
        second = deepcopy(event)
        second["event_id"] = str(uuid.uuid4())
        sink.emit_in_tx(conn, second)
        rows = conn.execute(text("SELECT event FROM audit_events")).scalars().all()
        assert len(rows) == 2
        for actual in rows:
            assert actual["actor"] == {
                "actor_id": "actual-api-actor",
                "actor_type": "HUMAN",
                "roles": ["ANALYST"],
            }
            assert actual["request"] == {
                "request_id": deal,
                "method": "POST",
                "path": f"/v1/deals/{deal}/runs",
                "status_code": 202,
            }
            assert actual["payload"]["safe"]["execution_origin"] == "API_INLINE"
        with (
            app_engine.connect() as other,
            pytest.raises(AuditSinkError, match="REQUEST_AUDIT_CONNECTION_MISMATCH"),
        ):
            sink.emit_in_tx(other, event)
        assert event == original
        raise RuntimeError("SYNTHETIC_ROLLBACK")
    with app_engine.begin() as conn:
        set_tenant_local(conn, tenant)
        assert conn.execute(text("SELECT count(*) FROM audit_events")).scalar_one() == 0


def test_request_bound_observability_failure_cannot_hide_successful_sql_commit(
    app_engine: Engine,
) -> None:
    from idis.audit.request_bound_sink import RequestBoundPostgresAuditSink
    from idis.observability.runtime_signals import RUN_CLAIMED, emit_run_signal

    tenant, deal = _scope(app_engine)

    class FailAfterActualSignalInsert(PostgresAuditSink):
        def emit_in_tx(self, conn: Connection, event: dict[str, Any]) -> None:
            super().emit_in_tx(conn, event)
            raise AuditSinkError("C3_ACTUAL_SIGNAL_AUDIT_FAILURE")

    with (
        pytest.raises(AuditSinkError, match="C3_ACTUAL_SIGNAL_AUDIT_FAILURE"),
        app_engine.begin() as conn,
    ):
        set_tenant_local(conn, tenant)
        sink = RequestBoundPostgresAuditSink(
            connection=conn,
            delegate=FailAfterActualSignalInsert(),
            tenant_id=tenant,
            actor_id="actual-api-actor",
            actor_type="HUMAN",
            actor_roles=("ANALYST",),
            request_id=deal,
            method="POST",
            path=f"/v1/deals/{deal}/runs",
            status_code=202,
        )
        # This existing channel is intentionally best-effort. The request-bound
        # adapter must remember its actual durable failure for the final gate.
        emit_run_signal(sink, event_type=RUN_CLAIMED, tenant_id=tenant, details={"run_id": deal})
        assert conn.execute(text("SELECT count(*) FROM audit_events")).scalar_one() == 1
        sink.raise_if_failed()
    with app_engine.begin() as conn:
        set_tenant_local(conn, tenant)
        assert conn.execute(text("SELECT count(*) FROM audit_events")).scalar_one() == 0


@pytest.mark.parametrize(
    "case",
    [
        "SNAPSHOT",
        "FULL",
        "wrong-mode",
        "wrong-hash",
        "wrong-state",
        "proof",
        "historical-terminal",
        "incomplete",
        "unknown-strict",
        "inactive-strict",
        "mismatch-strict",
        "prior-PENDING",
        "prior-RUNNING",
        "prior-FAILED",
        "prior-COMPLETED",
    ],
)
def test_closed_legacy_sequence_allows_only_fresh_exact_initial_execution(
    app_engine: Engine, tmp_path: Path, case: str
) -> None:
    # Real app-role frozen inputs and a real exact-run claim. This tests initial
    # ordering compatibility only, not FULL methodology or canonical acceptance.
    tenant, deal, uploaded, package = _real_open_native_package(app_engine, tmp_path, "pdf")
    freeze_source_package(engine=app_engine, tenant_id=tenant, deal_id=deal, package_id=package)
    mode = "FULL" if case == "FULL" else "SNAPSHOT"
    with app_engine.begin() as conn:
        runs = PostgresRunsRepository(conn, tenant)
        run = runs.create(
            run_id=str(uuid.uuid4()),
            deal_id=deal,
            mode=mode,
            source={"type": "deal_documents", "document_ids": [str(uploaded.document_id)]},
        )
        owner = str(uuid.uuid4())
        claimed = runs.claim_queued_run_for_execution(
            run_id=run["run_id"], lease_owner_id=owner, lease_seconds=60
        )
        assert claimed is not None
        steps = PostgresRunStepsRepository(conn, tenant)
        if case.startswith("prior-"):
            steps.create(
                RunStep(
                    step_id=str(uuid.uuid4()),
                    run_id=run["run_id"],
                    tenant_id=tenant,
                    step_name=StepName.DATA_ROOM_INVENTORY_PACKAGE,
                    step_order=0,
                    status=StepStatus(case.removeprefix("prior-")),
                ),
                lease_owner_id=owner,
                lease_fencing_token=claimed["lease_fencing_token"],
            )

        def unexpected(**_kwargs: Any) -> Any:
            raise AssertionError("sequence validation must not execute a producer")

        ctx = RunContext(
            run_id=run["run_id"],
            tenant_id=tenant,
            deal_id=deal,
            mode=mode,
            documents=PostgresDocumentsRepository(conn, tenant).list_documents_by_deal(deal),
            extract_fn=unexpected,
            grade_fn=unexpected,
            pipeline_contract_version=run["pipeline_contract_version"],
            pipeline_contract_hash=run["pipeline_contract_hash"],
            pipeline_resume_state=run["pipeline_resume_state"],
            lease_owner_id=owner,
            lease_fencing_token=claimed["lease_fencing_token"],
        )
        if case == "wrong-mode":
            ctx.mode = "FULL"
        elif case == "wrong-hash":
            ctx.pipeline_contract_hash = "0" * 64
        elif case == "wrong-state":
            ctx.pipeline_resume_state = "RESUMABLE"
        elif case == "proof":
            ctx.strict_preflight_proof_id = str(uuid.uuid4())
        elif case == "historical-terminal":
            ctx.pipeline_resume_state = "HISTORICAL_TERMINAL"
        elif case == "incomplete":
            ctx.pipeline_contract_hash = None
        elif case.endswith("-strict"):
            ctx.pipeline_contract_version = (
                "unknown-strict" if case == "unknown-strict" else "full-strict-v21"
            )
            ctx.pipeline_contract_hash = (
                "0" * 64
                if case == "mismatch-strict"
                else contract_registry.FULL_STRICT_V21_CONTRACT_HASH
            )
            ctx.pipeline_resume_state = "RESUMABLE"
        before = {
            table: conn.execute(
                text(f"SELECT to_jsonb(t)::text FROM {table} t ORDER BY to_jsonb(t)::text")
            )
            .scalars()
            .all()
            for table in ("runs", "run_steps", "audit_events", "claims")
        }
        registry = contract_registry.PIPELINE_CONTRACTS
        if case == "mismatch-strict":
            # Hermetic checker-only registry copy: distinguish hash rejection
            # from global v21 inactivity. No admission, execution or activation.
            registry = dict(registry)
            registry["full-strict-v21"] = replace(
                registry["full-strict-v21"], state=contract_registry.PipelineContractState.ACTIVE
            )
        orchestrator = RunOrchestrator(
            audit_sink=PostgresAuditSink(),
            run_steps_repo=steps,
            runs_repo=runs,
            pipeline_contract_registry=registry,
        )
        if case in {"SNAPSHOT", "FULL"}:
            assert orchestrator._resolve_pipeline_step_sequence(ctx) == tuple(
                FULL_STEPS if mode == "FULL" else SNAPSHOT_STEPS
            )
        else:
            with pytest.raises(RunStepBlockedError) as caught:
                orchestrator._resolve_pipeline_step_sequence(ctx)
            assert caught.value.code == (
                "PIPELINE_CONTRACT_HASH_MISMATCH"
                if case == "mismatch-strict"
                else "PIPELINE_CONTRACT_UNAVAILABLE"
                if case in {"unknown-strict", "inactive-strict", "incomplete"}
                else "STEP_REHYDRATION_FAILED"
            )
        after = {
            table: conn.execute(
                text(f"SELECT to_jsonb(t)::text FROM {table} t ORDER BY to_jsonb(t)::text")
            )
            .scalars()
            .all()
            for table in before
        }
        assert after == before


@pytest.mark.parametrize("mode", ["SNAPSHOT", "FULL"])
@pytest.mark.parametrize(
    "missing",
    [None, "pipeline_contract_version", "pipeline_contract_hash", "pipeline_resume_state", "all"],
)
def test_worker_context_preserves_real_claim_identity_and_rejects_missing_tuple(
    app_engine: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    missing: str | None,
) -> None:
    # A component boundary, not production-worker execution: only build_run_context
    # is captured after genuine ingestion, freeze and audited app-role lease claim.
    from idis.pipeline.worker import WorkerLeaseConfig, _default_run_context_factory

    tenant, deal, uploaded, package = _real_open_native_package(app_engine, tmp_path, "pdf")
    freeze_source_package(engine=app_engine, tenant_id=tenant, deal_id=deal, package_id=package)
    with app_engine.begin() as conn:
        runs = PostgresRunsRepository(conn, tenant)
        run = runs.create(
            run_id=str(uuid.uuid4()),
            deal_id=deal,
            mode=mode,
            source={"type": "deal_documents", "document_ids": [str(uploaded.document_id)]},
        )
        claimed = runs.claim_queued_runs(
            limit=1,
            lease_owner_id=str(uuid.uuid4()),
            lease_seconds=60,
            lease_audit_emitter=_lease_transition_audit_emitter(
                audit_sink=PostgresAuditSink(),
                tenant_id=tenant,
            ),
        )
        assert len(claimed) == 1 and claimed[0]["run_id"] == run["run_id"]
    before = _source_rows(app_engine, tenant)
    observed: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "idis.services.runs.steps.build_run_context",
        lambda **kwargs: observed.append(kwargs) or kwargs,
    )
    fields = ("pipeline_contract_version", "pipeline_contract_hash", "pipeline_resume_state")
    candidate = dict(claimed[0])
    for field in fields:
        if missing in {field, "all"}:
            candidate.pop(field)
    with app_engine.begin() as conn:
        kwargs = {
            "db_conn": conn,
            "tenant_id": tenant,
            "run_data": candidate,
            "audit_sink": PostgresAuditSink(),
            "lease_config": WorkerLeaseConfig(lease_seconds=60, heartbeat_seconds=10),
        }
        if missing is None:
            _default_run_context_factory(**kwargs)
            assert len(observed) == 1
            assert tuple(observed[0][field] for field in fields) == tuple(
                claimed[0][field] for field in fields
            )
            assert observed[0]["lease_owner_id"] == claimed[0]["lease_owner_id"]
            assert observed[0]["lease_fencing_token"] == 1
            assert observed[0]["strict_preflight_proof_id"] is None
            assert [item["document_id"] for item in observed[0]["preflight_corpus"]] == [
                str(uploaded.document_id)
            ]
        else:
            with pytest.raises(RuntimeError, match="^PIPELINE_CONTRACT_UNAVAILABLE$"):
                _default_run_context_factory(**kwargs)
            assert observed == []
    assert _source_rows(app_engine, tenant) == before
    with app_engine.begin() as conn:
        current = PostgresRunsRepository(conn, tenant).get(run["run_id"])
        assert current == claimed[0]
        assert PostgresRunStepsRepository(conn, tenant).get_by_run_id(run["run_id"]) == []


@pytest.mark.parametrize(
    "case",
    [
        "SNAPSHOT",
        "FULL",
        "prior-PENDING",
        "prior-RUNNING",
        "prior-FAILED",
        "prior-COMPLETED",
        "reclaimed",
        "proof-linked",
        "historical-terminal",
        "wrong-mode",
        "mismatched-context-hash",
    ],
)
def test_worker_real_context_cannot_bypass_fresh_closed_legacy_sequence(
    app_engine: Engine,
    tmp_path: Path,
    case: str,
) -> None:
    # Actual context construction on an ordinary app connection isolates identity
    # propagation/sequence validation; it is not the provider-facade worker runtime.
    from idis.persistence.repositories.runs import _resolve_run_contract_identity
    from idis.pipeline.worker import WorkerLeaseConfig, _default_run_context_factory
    from tests.test_slice98_erasure_export_postgres import _PROOF_INSERT_SQL, _proof_insert_params

    tenant, deal, uploaded, package = _real_open_native_package(app_engine, tmp_path, "pdf")
    freeze_source_package(engine=app_engine, tenant_id=tenant, deal_id=deal, package_id=package)
    mode = "FULL" if case in {"FULL", "proof-linked"} else "SNAPSHOT"
    sink = PostgresAuditSink()
    emitter = _lease_transition_audit_emitter(audit_sink=sink, tenant_id=tenant)
    with app_engine.begin() as conn:
        runs = PostgresRunsRepository(conn, tenant)
        identity: dict[str, Any] = {}
        if case == "historical-terminal":
            # Storage-only synthetic historical initial identity, not a rewrite
            # of an existing row: installed contract immutability forbids that.
            version, contract_hash, _state, _error = _resolve_run_contract_identity(
                mode=mode,
                pipeline_contract_version=None,
                pipeline_contract_hash=None,
                pipeline_resume_state=None,
                pipeline_resume_error_code=None,
            )
            identity = {
                "pipeline_contract_version": version,
                "pipeline_contract_hash": contract_hash,
                "pipeline_resume_state": "HISTORICAL_TERMINAL",
            }
        run = runs.create(
            run_id=str(uuid.uuid4()),
            deal_id=deal,
            mode=mode,
            source={"type": "deal_documents", "document_ids": [str(uploaded.document_id)]},
            **identity,
        )
        if case == "proof-linked":
            # Synthetic historical storage-only backfill: a genuine FK row,
            # never signed operator authority, native proof or strict admission.
            proof = str(uuid.uuid4())
            conn.execute(text(_PROOF_INSERT_SQL), _proof_insert_params(tenant, deal, proof))
            conn.execute(
                text("UPDATE runs SET strict_preflight_proof_id=:proof WHERE run_id=:run"),
                {"proof": proof, "run": run["run_id"]},
            )
        claimed_rows = runs.claim_queued_runs(
            limit=1,
            lease_owner_id=str(uuid.uuid4()),
            lease_seconds=1 if case == "reclaimed" else 60,
            lease_audit_emitter=emitter,
        )
        assert len(claimed_rows) == 1
        claimed = claimed_rows[0]
        assert claimed["run_id"] == run["run_id"] and claimed["lease_fencing_token"] == 1
        if case.startswith("prior-"):
            PostgresRunStepsRepository(conn, tenant).create(
                RunStep(
                    step_id=str(uuid.uuid4()),
                    run_id=run["run_id"],
                    tenant_id=tenant,
                    step_name=StepName.DATA_ROOM_INVENTORY_PACKAGE,
                    step_order=0,
                    status=StepStatus(case.removeprefix("prior-")),
                ),
                lease_owner_id=claimed["lease_owner_id"],
                lease_fencing_token=1,
            )
    if case == "reclaimed":
        with app_engine.begin() as conn:
            runs = PostgresRunsRepository(conn, tenant)
            conn.execute(text("SELECT pg_sleep(1.1)"))
            requeued = runs.requeue_expired_worker_leases(limit=1, lease_audit_emitter=emitter)
            assert len(requeued) == 1 and requeued[0]["lease_fencing_token"] == 2
            claimed_rows = runs.claim_queued_runs(
                limit=1,
                lease_owner_id=str(uuid.uuid4()),
                lease_seconds=60,
                lease_audit_emitter=emitter,
            )
            assert len(claimed_rows) == 1
            claimed = claimed_rows[0]
            assert claimed["lease_fencing_token"] == 3
    source_before = _source_rows(app_engine, tenant)
    with app_engine.begin() as conn:
        runs = PostgresRunsRepository(conn, tenant)
        steps = PostgresRunStepsRepository(conn, tenant)
        before_run, before_steps = runs.get(run["run_id"]), steps.get_by_run_id(run["run_id"])
        candidate = dict(claimed)
        if case == "wrong-mode":
            candidate["mode"] = "FULL"
        elif case == "mismatched-context-hash":
            candidate["pipeline_contract_hash"] = "0" * 64
        ctx = _default_run_context_factory(
            db_conn=conn,
            tenant_id=tenant,
            run_data=candidate,
            audit_sink=sink,
            lease_config=WorkerLeaseConfig(lease_seconds=60, heartbeat_seconds=10),
        )
        orchestrator = RunOrchestrator(audit_sink=sink, runs_repo=runs, run_steps_repo=steps)
        if case in {"SNAPSHOT", "FULL"}:
            assert orchestrator._resolve_pipeline_step_sequence(ctx) == tuple(
                FULL_STEPS if mode == "FULL" else SNAPSHOT_STEPS
            )
        else:
            with pytest.raises(RunStepBlockedError) as caught:
                orchestrator._resolve_pipeline_step_sequence(ctx)
            assert caught.value.code == "STEP_REHYDRATION_FAILED"
        assert runs.get(run["run_id"]) == before_run
        assert steps.get_by_run_id(run["run_id"]) == before_steps
    assert _source_rows(app_engine, tenant) == source_before


def test_real_provider_facade_read_is_detached_and_rls_excludes_foreign_scope(
    app_engine: Engine,
) -> None:
    from contextlib import contextmanager

    from idis.persistence.provider_reads import (
        ProviderReadConnection,
        UnstagedProviderPersistenceError,
    )
    from idis.persistence.repositories.layer1_evidence import PostgresLayer1EvidenceRepository

    # Both scopes are genuinely inserted as NOSUPER/NOBYPASS application role.
    # This proves that fixture tenant insertion is permitted, not admin backfill.
    tenant, deal = _scope(app_engine)
    foreign_tenant, foreign_deal = _scope(app_engine)
    opened: list[Connection] = []

    @contextmanager
    def fresh_transaction() -> Any:
        with app_engine.begin() as conn:
            opened.append(conn)
            yield conn

    facade = ProviderReadConnection(connection_factory=fresh_transaction, tenant_id=tenant)
    PostgresLayer1EvidenceRepository(facade, tenant)
    set_tenant_local(facade, tenant)
    assert opened == []
    result = facade.execute(
        text(
            "SELECT deal_id::text,tenant_id::text,"
            "current_setting('transaction_read_only') AS readonly,"
            "current_setting('idis.tenant_id') AS scope FROM deals WHERE deal_id IN (:own,:foreign)"
        ),
        {"own": deal, "foreign": foreign_deal},
    )
    assert len(opened) == 1 and opened[0].closed
    assert [tuple(row) for row in result] == [(deal, tenant, "on", tenant)]
    facade.assert_no_write_attempt()
    with pytest.raises(UnstagedProviderPersistenceError):
        set_tenant_local(facade, foreign_tenant)
    assert len(opened) == 1 and all(conn.closed for conn in opened)
    with pytest.raises(UnstagedProviderPersistenceError):
        facade.assert_no_write_attempt()


def test_real_default_snapshot_worker_completes_frozen_preview_without_provider_write_escape(
    app_engine: Engine,
    tmp_path: Path,
    record_testsuite_property: Callable[[str, object], None],
) -> None:
    import asyncio

    from idis.pipeline.worker import PipelineWorker, WorkerLeaseConfig

    tenant, deal, uploaded, package = _real_open_native_package(app_engine, tmp_path, "pdf")
    freeze_source_package(engine=app_engine, tenant_id=tenant, deal_id=deal, package_id=package)
    run_id = str(uuid.uuid4())
    with app_engine.begin() as conn:
        PostgresRunsRepository(conn, tenant).create(
            run_id=run_id,
            deal_id=deal,
            mode="SNAPSHOT",
            source={"type": "deal_documents", "document_ids": [str(uploaded.document_id)]},
        )
    worker = PipelineWorker(
        tenant_ids=[tenant],
        lease_config=WorkerLeaseConfig(lease_seconds=60, heartbeat_seconds=10),
    )
    assert worker._uses_production_runtime is True
    assert asyncio.run(worker._process_queued_runs()) == 1
    with app_engine.begin() as conn:
        row = PostgresRunsRepository(conn, tenant).get(run_id)
        assert row is not None
        safe_steps = [
            dict(value)
            for value in conn.execute(
                text(
                    "SELECT step_name,status,error_code FROM run_steps "
                    "WHERE run_id=:run ORDER BY step_order"
                ),
                {"run": run_id},
            ).mappings()
        ]
        assert row["status"] == "SUCCEEDED", safe_steps
        assert safe_steps and all(step["status"] == "COMPLETED" for step in safe_steps)
        assert [step["step_name"] for step in safe_steps] == [step.value for step in SNAPSHOT_STEPS]
        # Substantive legacy output, not an empty-client success or Task7 receipt claim.
        backed = conn.execute(
            text(
                "SELECT c.claim_id,e.evidence_id,s.sanad_id FROM claims c "
                "JOIN evidence_items e ON e.claim_id=c.claim_id AND e.tenant_id=c.tenant_id "
                "JOIN sanads s ON s.claim_id=c.claim_id AND s.tenant_id=c.tenant_id "
                "JOIN document_spans ds ON ds.span_id=e.source_span_id "
                "AND ds.tenant_id=e.tenant_id "
                "WHERE c.deal_id=:deal AND c.primary_span_id=e.source_span_id "
                "AND ds.source_binding_state='BOUND'"
            ),
            {"deal": deal},
        ).all()
        assert backed
        record_testsuite_property("C3_default_worker_backed_join_count", len(backed))
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


def _snapshot_business_rows(engine: Engine, tenant: str) -> dict[str, list[str]]:
    with engine.begin() as conn:
        set_tenant_local(conn, tenant)
        return {
            table: list(
                conn.execute(
                    text(
                        f"SELECT to_jsonb(value)::text FROM {table} value "
                        "WHERE tenant_id=CAST(:tenant AS uuid) ORDER BY to_jsonb(value)::text"
                    ),
                    {"tenant": tenant},
                ).scalars()
            )
            for table in (
                "claims",
                "evidence_items",
                "sanads",
                "defects",
                "deterministic_calculations",
                "calc_sanads",
            )
        }


@pytest.mark.parametrize("case", ["commit", "observed_winner", "expired", "cancelled"])
def test_snapshot_actual_calculator_pair_and_app_role_completion_guards(
    app_engine: Engine,
    tmp_path: Path,
    case: str,
) -> None:
    """Synthetic 1000/400 numeric component: real services, freeze, UoW and SQL.

    These deliberately chosen financial inputs/grades are NOT native extraction,
    operator evidence, a fund conclusion or canonical Task7 output admission.
    """
    from decimal import Decimal

    from idis.models.deterministic_calculation import CalcType
    from idis.persistence.repositories.calculations import PostgresCalculationsRepository
    from idis.pipeline.worker import WorkerLeaseConfig, build_postgres_claimed_run_runtime
    from idis.services.claims.service import ClaimService, CreateClaimInput
    from idis.services.defects.service import CreateDefectInput, DefectService
    from idis.services.runs.execution import WorkerLeaseBoundaryCommitFailedError
    from idis.services.runs.snapshot_postgres_output_staging import PendingCalculationPair
    from idis.services.sanad.auto_grade import auto_grade_claims_for_run
    from idis.services.sanad.service import SanadService

    tenant, deal, uploaded, package = _real_open_native_package(app_engine, tmp_path, "pdf")
    freeze_source_package(engine=app_engine, tenant_id=tenant, deal_id=deal, package_id=package)
    sink = PostgresAuditSink()
    emitter = _lease_transition_audit_emitter(audit_sink=sink, tenant_id=tenant)
    with app_engine.begin() as conn:
        repo = PostgresRunsRepository(conn, tenant)
        run = repo.create(
            run_id=str(uuid.uuid4()),
            deal_id=deal,
            mode="SNAPSHOT",
            source={"type": "deal_documents", "document_ids": [str(uploaded.document_id)]},
        )
        claimed = repo.claim_queued_runs(
            limit=1, lease_owner_id=str(uuid.uuid4()), lease_seconds=60, lease_audit_emitter=emitter
        )
        assert len(claimed) == 1 and claimed[0]["run_id"] == run["run_id"]
    runtime = build_postgres_claimed_run_runtime(
        tenant_id=tenant,
        run_data=claimed[0],
        lease_config=WorkerLeaseConfig(lease_seconds=60, heartbeat_seconds=10),
        audit_sink=sink,
        connection_factory=app_engine.begin,
    )
    ctx = runtime.context
    composition = ctx.snapshot_output_composition
    assert composition is not None
    orchestrator = RunOrchestrator(
        audit_sink=sink,
        runs_repo=runtime._service._runs_repo,
        run_steps_repo=runtime._service._run_steps_repo,
    )
    assert orchestrator._resolve_pipeline_step_sequence(ctx) == tuple(SNAPSHOT_STEPS)
    draft = composition.begin_step(ctx=ctx, step_name=StepName.EXTRACT)
    span_id = ctx.documents[0]["spans"][0]["span_id"]
    claim_service = ClaimService(
        tenant,
        db_conn=composition.provider_db_conn,
        audit_sink=draft.audit_sink,
        create_writer=draft.claim_writer,
    )
    claim_ids = []
    for predicate, amount in (("revenue", "1000"), ("cogs", "400")):
        claim = claim_service.create(
            CreateClaimInput(
                deal_id=deal,
                claim_class="FINANCIAL",
                claim_text=f"Synthetic numeric component {predicate}",
                predicate=predicate,
                value={"type": "monetary", "amount": amount, "currency": "USD"},
                primary_span_id=span_id,
                corroboration={
                    "level": "AHAD",
                    "independent_chain_count": 1,
                    "extraction_confidence": "0.99",
                    "dhabt_score": "0.95",
                },
            )
        )
        claim_ids.append(claim["claim_id"])
        draft.evidence_repo.create(
            evidence_id=str(uuid.uuid4()),
            tenant_id=tenant,
            deal_id=deal,
            claim_id=claim["claim_id"],
            source_span_id=span_id,
            source_grade="A",
        )
    claim_ids.sort()
    summary = {"created_claim_ids": claim_ids, "unique_claim_count": 2}
    draft.mark_ready(summary)
    composition.seal_success(ctx=ctx, step_name=StepName.EXTRACT, result_summary=summary)
    step = orchestrator._start_step_boundary(ctx, StepName.EXTRACT, None)
    orchestrator._complete_step_boundary(ctx, step, summary)
    grade = composition.begin_step(ctx=ctx, step_name=StepName.GRADE, claim_ids=claim_ids)
    result = auto_grade_claims_for_run(
        run_id=ctx.run_id,
        tenant_id=tenant,
        deal_id=deal,
        created_claim_ids=claim_ids,
        audit_sink=grade.audit_sink,
        db_conn=composition.provider_db_conn,
        claims_repo=grade.grading_claims,
        evidence_repo=grade.evidence_repo,
        sanad_service=SanadService(
            tenant,
            db_conn=composition.provider_db_conn,
            audit_sink=grade.audit_sink,
            create_writer=grade.sanad_writer,
        ),
        defect_service=DefectService(
            tenant,
            db_conn=composition.provider_db_conn,
            audit_sink=grade.audit_sink,
            create_writer=grade.defect_writer,
        ),
    )
    assert result.graded_count == 2
    # Explicit component defect independently qualifies this typed row's real
    # 0038 deferred commit; it is not an assertion that the grader found it.
    DefectService(
        tenant,
        db_conn=composition.provider_db_conn,
        audit_sink=grade.audit_sink,
        create_writer=grade.defect_writer,
    ).create(
        CreateDefectInput(
            claim_id=claim_ids[0],
            deal_id=deal,
            defect_type="BROKEN_CHAIN",
            description="Explicit synthetic SQL component",
            cure_protocol="RECONSTRUCT_CHAIN",
        )
    )
    grade_summary = {
        "graded_count": result.graded_count,
        "failed_count": result.failed_count,
        "total_defects": result.total_defects + 1,
        "all_failed": result.all_failed,
    }
    grade.mark_ready(grade_summary)
    composition.seal_success(ctx=ctx, step_name=StepName.GRADE, result_summary=grade_summary)
    step = orchestrator._start_step_boundary(ctx, StepName.GRADE, None)
    orchestrator._complete_step_boundary(ctx, step, grade_summary)
    summary = ctx.calc_fn(
        run_id=ctx.run_id,
        tenant_id=tenant,
        deal_id=deal,
        created_claim_ids=claim_ids,
        calc_types=[CalcType.GROSS_MARGIN],
    )
    assert summary["persisted_count"] == 1 and len(summary["calc_ids"]) == 1
    composition.seal_success(ctx=ctx, step_name=StepName.CALC, result_summary=summary)
    pending = [
        write.output
        for write in ctx.methodology_pending_native_output_writes[StepName.CALC]
        if isinstance(write.output, PendingCalculationPair)
    ]
    assert len(pending) == 1 and pending[0].calculation.output.primary_value == 60
    step = orchestrator._start_step_boundary(ctx, StepName.CALC, None)
    if case == "observed_winner":
        winner_calc = pending[0].calculation.model_copy(update={"calc_id": str(uuid.uuid4())})
        winner_sanad = pending[0].calc_sanad.model_copy(
            update={"calc_sanad_id": str(uuid.uuid4()), "calc_id": winner_calc.calc_id}
        )
        with app_engine.begin() as conn:
            PostgresCalculationsRepository(conn, tenant).create(
                calculation=winner_calc, calc_sanad=winner_sanad
            )
    elif case == "expired":
        with app_engine.begin() as conn:
            set_tenant_local(conn, tenant)
            conn.execute(
                text(
                    "UPDATE runs SET lease_expires_at=statement_timestamp()-interval '1 second' "
                    "WHERE run_id=:run"
                ),
                {"run": ctx.run_id},
            )
    elif case == "cancelled":
        with app_engine.begin() as conn:
            assert PostgresRunsRepository(conn, tenant).try_cancel_active(ctx.run_id)
    before = _snapshot_business_rows(app_engine, tenant)
    if case == "commit":
        orchestrator._complete_step_boundary(ctx, step, summary)
        after = _snapshot_business_rows(app_engine, tenant)
        assert len(after["deterministic_calculations"]) == len(after["calc_sanads"]) == 1
        assert len(after["claims"]) == len(after["evidence_items"]) == len(after["sanads"]) == 2
        assert after["defects"]
        with app_engine.begin() as conn:
            rows = PostgresCalculationsRepository(conn, tenant).list_by_deal(deal)
            assert str(rows[0]["calc_id"]) == pending[0].calculation.calc_id
            assert rows[0]["reproducibility_hash"] == summary["reproducibility_hashes"][0]
            assert Decimal(rows[0]["output"]["primary_value"]) == Decimal("60")
    else:
        error = (
            WorkerLeaseBoundaryCommitFailedError
            if case == "observed_winner"
            else WorkerLeaseFencedError
        )
        with pytest.raises(error):
            orchestrator._complete_step_boundary(ctx, step, summary)
        assert _snapshot_business_rows(app_engine, tenant) == before
        with app_engine.begin() as conn:
            assert (
                PostgresRunStepsRepository(conn, tenant).get_step(ctx.run_id, StepName.CALC).status
                == StepStatus.RUNNING
            )


@pytest.mark.parametrize(
    "fault",
    ["after_guard", "second_sanad", "late_audit", "omit_writer", "omit_audit", "failure_audit"],
)
def test_snapshot_real_worker_failure_boundaries_preserve_business_preimages(
    app_engine: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    """Controlled faults test real producers/UoW, not native failure classification."""
    import asyncio

    import idis.pipeline.worker as worker_module
    from idis.services.runs.execution import WorkerLeaseBoundaryCommitFailedError
    from idis.services.sanad.service import SanadService

    tenant, deal, uploaded, package = _real_open_native_package(
        app_engine,
        tmp_path,
        "pdf",
        pdf_lines=["PUBLIC SYNTHETIC PREVIEW SOURCE", "The synthetic team has twelve members."],
    )
    freeze_source_package(engine=app_engine, tenant_id=tenant, deal_id=deal, package_id=package)
    frozen_before = _source_rows(app_engine, tenant)
    frozen_before.pop("audit_events")
    run_id = str(uuid.uuid4())
    with app_engine.begin() as conn:
        PostgresRunsRepository(conn, tenant).create(
            run_id=run_id,
            deal_id=deal,
            mode="SNAPSHOT",
            source={"type": "deal_documents", "document_ids": [str(uploaded.document_id)]},
        )
    original_builder = worker_module.build_postgres_claimed_run_runtime
    observations: list[str] = []
    extracted_preimage: dict[str, list[str]] = {}
    compositions = []

    class LateAudit(PostgresAuditSink):
        def emit_in_tx(self, conn: Connection, event: dict[str, Any]) -> None:
            super().emit_in_tx(conn, event)
            target = "sanad.integrity.failed" if fault == "failure_audit" else "sanad.graded"
            if event["event_type"] == target:
                if fault != "failure_audit":
                    assert conn.execute(text("SELECT count(*) FROM sanads")).scalar_one() > 0
                observations.append("real_late_audit_insert")
                raise AuditSinkError("C3_CONTROLLED_LATE_PRODUCER_AUDIT")

    if fault in {"late_audit", "failure_audit"}:
        monkeypatch.setattr(worker_module, "_default_worker_audit_sink", LateAudit)

    real_create = SanadService.create
    creates = []

    def corrupted_second(service: SanadService, input_data: Any) -> dict[str, Any]:
        creates.append(input_data.claim_id)
        if len(creates) == 2:
            input_data = input_data.model_copy(
                update={
                    "transmission_chain": [
                        {
                            "node_id": str(uuid.uuid4()),
                            "node_type": "CONTROLLED_INVALID_NODE",
                            "actor_type": "AGENT",
                            "actor_id": "C3-explicit-producer-fault",
                            "timestamp": "2026-10-17T00:00:00Z",
                        }
                    ]
                }
            )
        return real_create(service, input_data)

    if fault in {"second_sanad", "failure_audit"}:
        monkeypatch.setattr(SanadService, "create", corrupted_second)

    def build(**kwargs: Any) -> Any:
        runtime = original_builder(**kwargs)
        ctx = runtime.context
        composition = ctx.snapshot_output_composition
        assert composition is not None
        compositions.append(composition)
        assert runtime.native_output_contracts is composition.contracts
        assert runtime.native_output_repository_factories is composition.repository_factories
        assert ctx.stage_native_output_writes_fn is runtime.stage_native_output_writes_fn
        guard = ctx.provider_output_guard_fn
        assert guard is not None

        def guarded(**values: Any) -> None:
            guard(**values)
            step, phase = values["step_name"], values["phase"]
            if step == StepName.EXTRACT and phase == "after":
                assert all(
                    not rows for rows in _snapshot_business_rows(app_engine, tenant).values()
                )
                with app_engine.begin() as conn:
                    set_tenant_local(conn, tenant)
                    assert (
                        conn.execute(
                            text(
                                "SELECT count(*) FROM audit_events WHERE event_type IN "
                                "('claim.created','claim.evidence.created','integration.extraction.pipeline_started')"
                            )
                        ).scalar_one()
                        == 0
                    )
                observations.append("zero_early_output_and_audit")
                if fault == "after_guard":
                    raise RunStepBlockedError(
                        "PIPELINE_CONTRACT_UNAVAILABLE", "C3 controlled after-guard"
                    )
            if step == StepName.GRADE and phase == "before":
                extracted_preimage.update(_snapshot_business_rows(app_engine, tenant))
                assert len(extracted_preimage["claims"]) >= 2
                assert extracted_preimage["evidence_items"]

        ctx.provider_output_guard_fn = guarded
        if fault in {"omit_writer", "omit_audit"}:
            boundary = ctx.step_boundary_uow_fn
            assert boundary is not None

            def omit(**values: Any) -> None:
                if values["step_name"] == StepName.EXTRACT and values["boundary"] == "COMPLETE":
                    writes = values["native_output_writes"]
                    assert writes
                    omitted = next(
                        i
                        for i, write in enumerate(writes)
                        if (write.binding.sql_targets == frozenset({"audit_events"}))
                        == (fault == "omit_audit")
                    )
                    values["native_output_writes"] = writes[:omitted] + writes[omitted + 1 :]
                    observations.append("omitted_real_writer")
                boundary(**values)

            ctx.step_boundary_uow_fn = omit
        return runtime

    monkeypatch.setattr(worker_module, "build_postgres_claimed_run_runtime", build)
    worker = worker_module.PipelineWorker(
        tenant_ids=[tenant],
        lease_config=worker_module.WorkerLeaseConfig(lease_seconds=60, heartbeat_seconds=10),
    )
    if fault in {"late_audit", "omit_writer", "omit_audit", "failure_audit"}:
        with pytest.raises(WorkerLeaseBoundaryCommitFailedError) as caught:
            asyncio.run(worker._process_queued_runs())
        assert caught.value.code == "WORKER_LEASE_BOUNDARY_COMMIT_FAILED"
    else:
        assert asyncio.run(worker._process_queued_runs()) == 1
    assert "zero_early_output_and_audit" in observations
    after = _snapshot_business_rows(app_engine, tenant)
    if fault in {"after_guard", "omit_writer", "omit_audit"}:
        assert all(not rows for rows in after.values())
    else:
        assert after == extracted_preimage
    source_after = _source_rows(app_engine, tenant)
    source_after.pop("audit_events")
    assert source_after == frozen_before
    with app_engine.begin() as conn:
        row = PostgresRunsRepository(conn, tenant).get(run_id)
        assert row is not None
        steps = PostgresRunStepsRepository(conn, tenant).get_by_run_id(run_id)
        active = steps[-1]
        if fault in {"after_guard", "second_sanad"}:
            assert row["status"] == "FAILED" and active.status == StepStatus.FAILED
        else:
            # The existing lease-control policy rolls back COMPLETE and leaves
            # RUNNING until expiry/reclaim; this test does not invent terminalization.
            assert row["status"] == "RUNNING" and active.status == StepStatus.RUNNING
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM audit_events WHERE event_type IN "
                    "('sanad.created','sanad.graded','defect.created','defect.detected')"
                )
            ).scalar_one()
            == 0
        )
        critical = (
            conn.execute(
                text("SELECT event FROM audit_events WHERE event_type='sanad.integrity.failed'")
            )
            .scalars()
            .all()
        )
        if fault == "second_sanad":
            assert len(creates) == 2 and len(critical) == 1
            event = json.loads(critical[0]) if isinstance(critical[0], str) else critical[0]
            assert event["severity"] == "CRITICAL"
            assert event["payload"]["safe"]["claim_id"] == creates[1]
        else:
            assert critical == []
    if fault == "failure_audit":
        assert len(creates) == 2 and "real_late_audit_insert" in observations
        assert compositions[0]._failure_packets == {}


def test_api_late_completion_audit_rolls_back_real_steps_claim_and_webhook_outbox(
    app_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_slice97_webhook_outbox_postgres import _create_subscription

    tenant, deal, _uploaded, package = _real_open_native_package(app_engine, tmp_path, "pdf")
    freeze_source_package(engine=app_engine, tenant_id=tenant, deal_id=deal, package_id=package)
    _create_subscription(tenant, ["run.claimed", "run.completed"])
    observed = []

    class LateFailure(PostgresAuditSink):
        def emit_in_tx(self, conn: Connection, event: dict[str, Any]) -> None:
            super().emit_in_tx(conn, event)
            if event["event_type"] == "deal.run.completed":
                counts = {
                    table: conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
                    for table in ("runs", "run_steps", "webhook_delivery_attempts")
                }
                assert all(counts.values()), counts
                observed.append(counts)
                raise AuditSinkError("C3_LATE_ACTUAL_COMPLETION_AUDIT_FAILURE")

    client = _preview_client(monkeypatch, tenant, deal, LateFailure())
    response = client.post(f"/v1/deals/{deal}/runs", json={"mode": "SNAPSHOT"})
    assert response.status_code == 500 and response.json()["code"] == "AUDIT_FAILURE"
    assert len(observed) == 1
    with app_engine.begin() as conn:
        set_tenant_local(conn, tenant)
        for table in ("runs", "run_steps", "claims", "webhook_delivery_attempts"):
            assert conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0


@pytest.mark.parametrize(
    "event_type",
    [
        "worker.lease.claimed",
        "deal.run.step.data_room_inventory_package.started",
        "deal.run.step.data_room_inventory_package.completed",
    ],
)
def test_api_formal_claim_and_step_audit_failure_rolls_back_actual_sql(
    app_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, event_type: str
) -> None:
    tenant, deal, _uploaded, package = _real_open_native_package(app_engine, tmp_path, "pdf")
    freeze_source_package(engine=app_engine, tenant_id=tenant, deal_id=deal, package_id=package)
    source_before = _source_rows(app_engine, tenant)
    source_before.pop("audit_events")
    observed = []

    class FailAfterActualInsert(PostgresAuditSink):
        def emit_in_tx(self, conn: Connection, event: dict[str, Any]) -> None:
            super().emit_in_tx(conn, event)
            if event["event_type"] == event_type:
                assert conn.execute(text("SELECT count(*) FROM runs")).scalar_one() == 1
                assert (
                    conn.execute(
                        text("SELECT status,lease_owner_id,lease_fencing_token FROM runs")
                    ).one()[0]
                    == "RUNNING"
                )
                assert event["actor"]["actor_id"] == "synthetic-c3-preview-actor"
                assert event["payload"]["safe"]["execution_origin"] == "API_INLINE"
                observed.append(event["event_id"])
                raise AuditSinkError("C3_ACTUAL_FORMAL_AUDIT_FAILURE")

    client = _preview_client(monkeypatch, tenant, deal, FailAfterActualInsert())
    response = client.post(f"/v1/deals/{deal}/runs", json={"mode": "SNAPSHOT"})
    assert response.status_code == 500, response.text
    assert response.json()["code"] == "AUDIT_FAILURE"
    assert len(observed) == 1
    with app_engine.begin() as conn:
        set_tenant_local(conn, tenant)
        for table in ("runs", "run_steps", "claims", "webhook_delivery_attempts"):
            assert conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0
        assert (
            conn.execute(
                text("SELECT count(*) FROM audit_events WHERE event_id=CAST(:event AS uuid)"),
                {"event": observed[0]},
            ).scalar_one()
            == 0
        )
    source_after = _source_rows(app_engine, tenant)
    source_after.pop("audit_events")
    assert source_after == source_before


def test_api_real_lease_expiry_returns_503_without_terminal_or_step_commit(
    app_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant, deal, _uploaded, package = _real_open_native_package(app_engine, tmp_path, "pdf")
    freeze_source_package(engine=app_engine, tenant_id=tenant, deal_id=deal, package_id=package)
    client = _preview_client(monkeypatch, tenant, deal)
    actual_claim = PostgresRunsRepository.claim_queued_run_for_execution
    actual_execute = RunOrchestrator.execute
    claims = []

    def short_real_claim(self: Any, **kwargs: Any) -> Any:
        result = actual_claim(self, **{**kwargs, "lease_seconds": 1})
        assert result is not None
        claims.append((result["lease_owner_id"], result["lease_fencing_token"]))
        return result

    def after_real_expiry(self: Any, ctx: Any) -> Any:
        with app_engine.connect() as conn:
            conn.execute(text("SELECT pg_sleep(1.2)"))
        return actual_execute(self, ctx)

    monkeypatch.setattr(PostgresRunsRepository, "claim_queued_run_for_execution", short_real_claim)
    monkeypatch.setattr(RunOrchestrator, "execute", after_real_expiry)
    response = client.post(f"/v1/deals/{deal}/runs", json={"mode": "SNAPSHOT"})
    assert response.status_code == 503, response.text
    assert response.json()["code"] == "WORKER_LEASE_FENCED"
    assert len(claims) == 1 and claims[0][1] == 1
    with app_engine.begin() as conn:
        set_tenant_local(conn, tenant)
        for table in ("runs", "run_steps", "claims", "webhook_delivery_attempts"):
            assert conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0


def test_frozen_consumer_reload_requires_exact_real_bound_selection_and_excludes_staging(
    app_engine: Engine, tmp_path: Path
) -> None:
    from idis.services.runs.steps import load_document_preflight_corpus_for_deal

    tenant, deal, uploaded, package = _real_open_native_package(
        app_engine, tmp_path / "selected", "pdf"
    )
    selected = (str(uploaded.document_id),)
    with app_engine.begin() as conn, pytest.raises(ValueError, match="FROZEN_SOURCE_REQUIRED"):
        load_document_preflight_corpus_for_deal(
            db_conn=conn,
            tenant_id=tenant,
            deal_id=deal,
            selected_document_ids=selected,
            require_frozen=True,
        )

    freeze_source_package(engine=app_engine, tenant_id=tenant, deal_id=deal, package_id=package)
    store = ComplianceEnforcedStore(inner_store=FilesystemObjectStore(base_dir=tmp_path / "extra"))
    with app_engine.begin() as conn:
        extra = _upload(
            IngestionService(compliant_store=store, db_conn=conn),
            tenant,
            deal,
            b"PUBLIC SYNTHETIC UNSELECTED SOURCE",
            name="unselected.txt",
            media_type="text/plain",
        )
        assert extra.success
    with app_engine.begin() as conn:
        actual = load_document_preflight_corpus_for_deal(
            db_conn=conn,
            tenant_id=tenant,
            deal_id=deal,
            selected_document_ids=selected,
            require_frozen=True,
        )
        assert [row["document_id"] for row in actual] == list(selected)
        assert actual[0]["spans"] and all(row["content_hash"] for row in actual[0]["spans"])
        with pytest.raises(ValueError, match="FROZEN_SOURCE_REQUIRED"):
            load_document_preflight_corpus_for_deal(
                db_conn=conn,
                tenant_id=tenant,
                deal_id=deal,
                selected_document_ids=(*selected, str(extra.document_id)),
                require_frozen=True,
            )
        with pytest.raises(ValueError, match="FROZEN_SOURCE_REQUIRED"):
            load_document_preflight_corpus_for_deal(
                db_conn=conn,
                tenant_id=str(uuid.uuid4()),
                deal_id=deal,
                selected_document_ids=selected,
                require_frozen=True,
            )


@pytest.mark.parametrize("reader", ["worker", "methodology"])
def test_actual_worker_and_methodology_readers_cannot_consume_unbound_source(
    app_engine: Engine, tmp_path: Path, reader: str
) -> None:
    from idis.models.extraction_task import SourceSpanReference
    from idis.persistence.repositories.methodology_extraction import (
        MethodologyExtractionRepositoryError,
        PostgresMethodologyExtractionRepository,
    )
    from idis.pipeline.worker import (
        InvalidRunSourceMetadataError,
        InvalidRunSourceSelectionError,
        _load_worker_preflight_corpus,
    )

    tenant, deal, uploaded, package = _real_open_native_package(app_engine, tmp_path, "pdf")
    document_id = str(uploaded.document_id)
    with app_engine.begin() as conn:
        spans = PostgresDocumentsRepository(conn, tenant).list_spans_by_document(
            deal_id=deal, document_id=document_id
        )
    references = [
        SourceSpanReference(
            document_id=document_id,
            span_id=row["span_id"],
            span_type=row["span_type"],
            locator=row["locator"],
            content_hash=row["content_hash"],
        )
        for row in spans
    ]

    def load(conn: Connection) -> Any:
        if reader == "worker":
            return _load_worker_preflight_corpus(
                db_conn=conn,
                tenant_id=tenant,
                run_data={
                    "deal_id": deal,
                    "source": {"type": "deal_documents", "document_ids": [document_id]},
                },
            )
        return PostgresMethodologyExtractionRepository(
            conn, tenant
        ).load_strict_planning_source_spans(
            deal_id=deal, source_span_references_by_document_id={document_id: references}
        )

    with app_engine.begin() as conn:
        expected_error = (
            InvalidRunSourceSelectionError
            if reader == "worker"
            else MethodologyExtractionRepositoryError
        )
        with pytest.raises(expected_error):
            load(conn)
    freeze_source_package(engine=app_engine, tenant_id=tenant, deal_id=deal, package_id=package)
    with app_engine.begin() as conn:
        actual = load(conn)
        if reader == "worker":
            assert [document["document_id"] for document in actual] == [document_id]
            assert actual[0]["spans"]
            with pytest.raises(InvalidRunSourceMetadataError):
                _load_worker_preflight_corpus(
                    db_conn=conn, tenant_id=tenant, run_data={"deal_id": deal, "source": None}
                )
        else:
            assert set(actual) == {document_id}
            assert [row.text_excerpt for row in actual[document_id]] == [
                row["text_excerpt"] for row in spans
            ]


def test_methodology_execution_source_is_exact_persisted_frozen_run_selection(
    app_engine: Engine, tmp_path: Path
) -> None:
    from idis.persistence.repositories.methodology_extraction import (
        MethodologyExtractionRepositoryError,
        PostgresMethodologyExtractionRepository,
    )

    tenant, deal, uploaded, package = _real_open_native_package(app_engine, tmp_path, "pdf")
    selected = (str(uploaded.document_id),)
    with app_engine.begin() as conn:
        run = PostgresRunsRepository(conn, tenant).create(
            run_id=str(uuid.uuid4()),
            deal_id=deal,
            mode="FULL",
            source={"type": "deal_documents", "document_ids": list(selected)},
        )
        repository = PostgresMethodologyExtractionRepository(conn, tenant)
        # Storage-only legacy row: no claim, methodology, or runtime admission
        # is invented. This is the source-read prerequisite only.
        with pytest.raises(MethodologyExtractionRepositoryError):
            repository.require_frozen_run_source(deal_id=deal, run_id=run["run_id"])
    freeze_source_package(engine=app_engine, tenant_id=tenant, deal_id=deal, package_id=package)
    with app_engine.begin() as conn:
        repository = PostgresMethodologyExtractionRepository(conn, tenant)
        actual = repository.require_frozen_run_source(deal_id=deal, run_id=run["run_id"])
        assert tuple(row["document_id"] for row in actual) == selected
        with pytest.raises(MethodologyExtractionRepositoryError):
            repository.require_frozen_run_source(
                deal_id=deal, run_id=run["run_id"], exact_document_ids=(str(uuid.uuid4()),)
            )
        with pytest.raises(MethodologyExtractionRepositoryError):
            repository.require_frozen_run_source(deal_id=str(uuid.uuid4()), run_id=run["run_id"])


def _ephemeral_budget_profile(tmp_path: Path, tenant: str) -> Any:
    """Actual signed synthetic policy for constructor qualification, not activation."""
    import hashlib
    import stat

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from idis.runtime.governance_envelopes import BUDGET_SCHEMA_VERSION, sign_envelope
    from idis.runtime.secure_files import protect_path_for_current_user
    from tests.slice100_task2._support import budget_payload

    directory = tmp_path / "ephemeral-budget-constructor-only"
    directory.mkdir()
    public, policy = (directory / name for name in ("public.pem", "policy.json"))
    # Existing Task7 signed-test precedent: the ephemeral private key remains
    # in process. The operator signing tool correctly refuses repository keys.
    private_key = Ed25519PrivateKey.generate()
    public.write_bytes(
        private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    payload = {
        **budget_payload(),
        "tenant_id": tenant,
        "policy_version": "C3-synthetic-source-gate",
    }
    envelope = sign_envelope(
        schema_version=BUDGET_SCHEMA_VERSION,
        signer_id="C3-ephemeral-test-signer",
        payload=payload,
        private_key_pem=private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )
    policy.write_text(json.dumps(envelope), encoding="utf-8")
    protect_path_for_current_user(directory, directory=True)
    for path in (public, policy):
        protect_path_for_current_user(path)
        path.chmod(stat.S_IREAD)
    return SimpleNamespace(
        values={
            "IDIS_GOVERNANCE_TRUSTED_ED25519_PUBLIC_KEY_PATH": str(public),
            "IDIS_PROVIDER_BUDGET_POLICY_PATH": str(policy),
            "IDIS_GOVERNANCE_TRUSTED_SIGNER_ID": "C3-ephemeral-test-signer",
            "IDIS_PROVIDER_BUDGET_MAX_CALLS": "10",
        },
        governance_public_key_sha256=hashlib.sha256(public.read_bytes()).hexdigest(),
    )


@pytest.mark.parametrize("source_case", ["unbound", "wrong-pinned-input"])
def test_actual_methodology_input_boundary_checks_frozen_pinned_source_before_tasks(
    app_engine: Engine, tmp_path: Path, source_case: str
) -> None:
    from idis.services.runs.methodology_postgres_runtime import PostgresMethodologyExecutionBoundary

    tenant, deal, uploaded, package = _real_open_native_package(app_engine, tmp_path, "pdf")
    document_id = str(uploaded.document_id)
    with app_engine.begin() as conn:
        run = PostgresRunsRepository(conn, tenant).create(
            run_id=str(uuid.uuid4()),
            deal_id=deal,
            mode="FULL",
            source={"type": "deal_documents", "document_ids": [document_id]},
        )
    if source_case == "wrong-pinned-input":
        freeze_source_package(engine=app_engine, tenant_id=tenant, deal_id=deal, package_id=package)
    boundary = PostgresMethodologyExecutionBoundary(
        connection_factory=app_engine.begin,
        tenant_id=tenant,
        lease_owner_id=str(uuid.uuid4()),
        lease_fencing_token=1,
        audit_sink=PostgresAuditSink(),
        strict_profile=_ephemeral_budget_profile(tmp_path, tenant),
    )
    before = _source_rows(app_engine, tenant)
    # The task identifier is deliberately not an admitted task. This negative
    # proves source refusal precedes task/provider reads, not FULL admission.
    with pytest.raises(RuntimeError) as rejected:
        boundary.load_execution_inputs(
            tenant_id=tenant,
            deal_id=deal,
            run_id=run["run_id"],
            tasks=[SimpleNamespace(extraction_task_id=str(uuid.uuid4()))],
            documents=[
                {"document_id": document_id if source_case == "unbound" else str(uuid.uuid4())}
            ],
        )
    assert rejected.value.code == "METHODOLOGY_SOURCE_LOCATOR_REJECTED"
    assert _source_rows(app_engine, tenant) == before


def test_planned_graph_rehydration_requires_frozen_source_before_live_span_reads(
    app_engine: Engine, tmp_path: Path
) -> None:
    from idis.persistence.repositories.methodology_extraction import (
        MethodologyExtractionRepositoryError,
        PostgresMethodologyExtractionRepository,
    )

    tenant, deal, uploaded, package = _real_open_native_package(app_engine, tmp_path, "pdf")
    with app_engine.begin() as conn:
        run = PostgresRunsRepository(conn, tenant).create(
            run_id=str(uuid.uuid4()),
            deal_id=deal,
            mode="FULL",
            source={"type": "deal_documents", "document_ids": [str(uploaded.document_id)]},
        )
        repository = PostgresMethodologyExtractionRepository(conn, tenant)
        with pytest.raises(MethodologyExtractionRepositoryError) as rejected:
            repository._validate_exact_planned_graph(
                deal_id=deal,
                run_id=run["run_id"],
                selection_id=str(uuid.uuid4()),
                coverage={},
            )
        assert rejected.value.code == "METHODOLOGY_SOURCE_LOCATOR_REJECTED"
    freeze_source_package(engine=app_engine, tenant_id=tenant, deal_id=deal, package_id=package)
    with app_engine.begin() as conn:
        repository = PostgresMethodologyExtractionRepository(conn, tenant)
        with pytest.raises(MethodologyExtractionRepositoryError) as still_not_admitted:
            repository._validate_exact_planned_graph(
                deal_id=deal,
                run_id=run["run_id"],
                selection_id=str(uuid.uuid4()),
                coverage={},
            )
        assert still_not_admitted.value.code == "METHODOLOGY_TASK_COVERAGE_INCOMPLETE"


@pytest.mark.parametrize("operation", ["retry", "resume"])
def test_api_non_resumable_failed_preview_never_reopens_completed_checkpoints(
    app_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    from idis.api.routes import runs as routes

    tenant, deal, _uploaded, package = _real_open_native_package(app_engine, tmp_path, "pdf")
    freeze_source_package(engine=app_engine, tenant_id=tenant, deal_id=deal, package_id=package)
    client = _preview_client(monkeypatch, tenant, deal)
    actual_build = routes.build_run_context
    calls = []

    def fail_extraction(**_kwargs: Any) -> Any:
        # Deliberate producer fault after real ingestion/preflight checkpoints;
        # this does not pretend the valid PDF was empty or unreadable.
        calls.append("extract")
        raise RunStepBlockedError("C3_SYNTHETIC_EXTRACTOR_FAILURE", "Synthetic producer fault")

    def failing_context(**kwargs: Any) -> Any:
        context = actual_build(**kwargs)
        context.extract_fn = fail_extraction
        return context

    monkeypatch.setattr(routes, "build_run_context", failing_context)
    started = client.post(f"/v1/deals/{deal}/runs", json={"mode": "SNAPSHOT"})
    assert started.status_code == 202, started.text
    assert started.json()["status"] == "FAILED", started.text
    assert calls == ["extract"]
    run_id = started.json()["run_id"]

    def execution_rows() -> dict[str, list[str]]:
        with app_engine.begin() as conn:
            set_tenant_local(conn, tenant)
            return {
                table: conn.execute(
                    text(f"SELECT to_jsonb(t)::text FROM {table} t ORDER BY to_jsonb(t)::text")
                )
                .scalars()
                .all()
                for table in ("runs", "run_steps", "claims", "webhook_delivery_attempts")
            }

    before = execution_rows()
    assert any(json.loads(row)["status"] == "COMPLETED" for row in before["run_steps"])
    assert any(json.loads(row)["status"] == "FAILED" for row in before["run_steps"])
    response = client.post(f"/v1/runs/{run_id}/{operation}")
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "LEGACY_RUN_RESUME_UNSAFE"
    assert execution_rows() == before
    assert calls == ["extract"]


def test_explicit_preview_producer_uses_real_upload_rows_and_package_then_api_freeze(
    app_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from idis.services.ingestion import IngestionContext
    from idis.tools.preview_source_preparation import (
        PreviewSourceFile,
        prepare_preview_source_package,
    )

    tenant, deal = _scope(app_engine)
    marker = "PUBLIC FICTIONAL PREVIEW revenue was 11.1 million."
    data = create_test_pdf([marker])
    store = ComplianceEnforcedStore(inner_store=FilesystemObjectStore(base_dir=tmp_path))
    prepared = prepare_preview_source_package(
        engine=app_engine,
        ctx=IngestionContext(
            tenant_id=uuid.UUID(tenant), actor_id="C3-test", request_id="C3-prepare"
        ),
        deal_id=uuid.UUID(deal),
        files=(PreviewSourceFile(extension="pdf", media_type="application/pdf", data=data),),
        compliant_store=store,
        audit_sink=PostgresAuditSink(),
    )
    with app_engine.begin() as conn:
        documents = PostgresDocumentsRepository(conn, tenant).list_documents_by_deal(deal)
        assert len(documents) == 1
        document = documents[0]
        assert prepared.document_ids == (document["document_id"],)
        assert (
            document["source_metadata"]["upload_filename"]
            == prepared.preparation.files[0].generated_upload_name
        )
        assert (
            document["source_metadata"]["source_tree_fingerprint"]
            == prepared.preparation.source_tree_fingerprint
        )
        assert prepared.manifest == prepared.preparation.build_manifest(documents)
        assert conn.execute(text("SELECT count(*) FROM runs")).scalar_one() == 0
        assert tuple(
            conn.execute(
                text(
                    "SELECT status,source_binding_state FROM data_room_packages "
                    "WHERE package_id=:package"
                ),
                {"package": prepared.package_id},
            ).one()
        ) == ("OPEN", "UNBOUND")
        assert tuple(
            conn.execute(
                text(
                    "SELECT doc_id,document_id FROM data_room_package_files "
                    "WHERE package_id=:package"
                ),
                {"package": prepared.package_id},
            ).one()
        ) == (None, None)
    client = _preview_client(monkeypatch, tenant, deal)
    response = client.post(
        f"/v1/deals/{deal}/runs",
        json={
            "mode": "SNAPSHOT",
            "source": {"type": "deal_documents", "document_ids": list(prepared.document_ids)},
        },
    )
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "SUCCEEDED", response.text
    with app_engine.begin() as conn:
        set_tenant_local(conn, tenant)
        claims = conn.execute(text("SELECT primary_span_id FROM claims")).scalars().all()
        assert claims and all(claims)
        assert conn.execute(text("SELECT count(*) FROM evidence_items")).scalar_one() > 0
        assert (
            conn.execute(
                text("SELECT status FROM data_room_packages WHERE package_id=:package"),
                {"package": prepared.package_id},
            ).scalar_one()
            == "SEALED"
        )


@pytest.mark.parametrize("explicit_selection", [True, False])
def test_api_pins_exact_frozen_selection_without_silently_consuming_or_dropping_staging(
    app_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, explicit_selection: bool
) -> None:
    from idis.api.routes import runs as routes

    tenant, deal, selected, _package = _real_open_native_package(
        app_engine, tmp_path / "selected", "pdf"
    )
    store = ComplianceEnforcedStore(inner_store=FilesystemObjectStore(base_dir=tmp_path / "extra"))
    with app_engine.begin() as conn:
        extra = _upload(
            IngestionService(compliant_store=store, db_conn=conn),
            tenant,
            deal,
            b"PUBLIC UNSELECTED revenue was 999999 million.",
            name="unselected.txt",
            media_type="text/plain",
        )
        assert extra.success
    client = _preview_client(monkeypatch, tenant, deal)
    contexts = []
    actual_build = routes.build_run_context

    def observe(**kwargs: Any) -> Any:
        context = actual_build(**kwargs)
        contexts.append(context)
        return context

    monkeypatch.setattr(routes, "build_run_context", observe)
    body: dict[str, Any] = {"mode": "SNAPSHOT"}
    if explicit_selection:
        body["source"] = {"type": "deal_documents", "document_ids": [str(selected.document_id)]}
    response = client.post(f"/v1/deals/{deal}/runs", json=body)
    if not explicit_selection:
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "SOURCE_PACKAGE_REQUIRED"
        assert response.json().get("details") is None
        assert contexts == []
    else:
        assert response.status_code == 202, response.text
        assert response.json()["status"] == "SUCCEEDED", response.text
        assert len(contexts) == 1
        assert {row["document_id"] for row in contexts[0].documents} == {str(selected.document_id)}
    with app_engine.begin() as conn:
        set_tenant_local(conn, tenant)
        if explicit_selection:
            assert conn.execute(text("SELECT source FROM runs")).scalar_one() == body["source"]
            assert (
                conn.execute(
                    text(
                        "SELECT count(*) FROM evidence_items e JOIN document_spans s "
                        "ON s.span_id=e.source_span_id WHERE s.document_id=:extra"
                    ),
                    {"extra": str(extra.document_id)},
                ).scalar_one()
                == 0
            )
        else:
            assert conn.execute(text("SELECT count(*) FROM runs")).scalar_one() == 0
            assert conn.execute(text("SELECT count(*) FROM run_steps")).scalar_one() == 0
        assert (
            conn.execute(
                text("SELECT source_binding_state FROM documents WHERE document_id=:extra"),
                {"extra": str(extra.document_id)},
            ).scalar_one()
            == "UNBOUND"
        )


def test_explicit_preview_preparation_failure_rolls_back_sql_without_claiming_object_erasure(
    app_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from idis.services.ingestion import IngestionContext
    from idis.tools.preview_source_preparation import (
        PreviewSourceFile,
        PreviewSourcePreparationError,
        prepare_preview_source_package,
    )

    tenant, deal = _scope(app_engine)
    object_root = tmp_path / "owned-object-puts"
    store = ComplianceEnforcedStore(inner_store=FilesystemObjectStore(base_dir=object_root))
    actual_ingest = IngestionService.ingest_bytes
    outcomes = []

    def observe(self: Any, **kwargs: Any) -> Any:
        result = actual_ingest(self, **kwargs)
        outcomes.append(result.success)
        return result

    monkeypatch.setattr(IngestionService, "ingest_bytes", observe)
    before = _source_rows(app_engine, tenant)
    with pytest.raises(PreviewSourcePreparationError, match="PREVIEW_SOURCE_PREPARATION_REQUIRED"):
        prepare_preview_source_package(
            engine=app_engine,
            ctx=IngestionContext(
                tenant_id=uuid.UUID(tenant), actor_id="C3-test", request_id="C3-failing-prep"
            ),
            deal_id=uuid.UUID(deal),
            compliant_store=store,
            audit_sink=PostgresAuditSink(),
            files=(
                PreviewSourceFile(
                    extension="pdf",
                    media_type="application/pdf",
                    data=create_test_pdf(["PUBLIC VALID FIRST UPLOAD"]),
                ),
                PreviewSourceFile(
                    extension="pdf",
                    media_type="application/pdf",
                    data=b"%PDF-public-invalid-second-upload",
                ),
            ),
        )
    assert outcomes == [True, False]
    assert _source_rows(app_engine, tenant) == before
    # Existing object puts are not a SQL transaction and are NOT called erased.
    assert any(path.is_file() for path in object_root.rglob("*"))


def test_explicit_preview_producer_refuses_missing_real_terminal_receipt_prerequisite(
    app_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from idis.evaluation.real_example_terminal_reconciliation import (
        TerminalIngestionReconciliationError,
    )
    from idis.services.ingestion import IngestionContext
    from idis.tools.preview_source_preparation import (
        PreviewSourceFile,
        PreviewSourcePreparationError,
        prepare_preview_source_package,
    )

    tenant, deal = _scope(app_engine)
    store = ComplianceEnforcedStore(inner_store=FilesystemObjectStore(base_dir=tmp_path))
    before = _source_rows(app_engine, tenant)

    def unavailable(*_args: Any, **_kwargs: Any) -> Any:
        # Explicit receipt-producer fault; actual parser/storage still execute.
        raise TerminalIngestionReconciliationError(
            "C3_SYNTHETIC_RECEIPT_UNAVAILABLE", "Synthetic receipt-producer fault"
        )

    monkeypatch.setattr(IngestionService, "_native_pdf_terminal_metadata", unavailable)
    with pytest.raises(PreviewSourcePreparationError) as rejected:
        prepare_preview_source_package(
            engine=app_engine,
            ctx=IngestionContext(
                tenant_id=uuid.UUID(tenant), actor_id="C3-test", request_id="C3-prerequisite"
            ),
            deal_id=uuid.UUID(deal),
            compliant_store=store,
            audit_sink=PostgresAuditSink(),
            files=(
                PreviewSourceFile(
                    extension="pdf",
                    media_type="application/pdf",
                    data=create_test_pdf(["PUBLIC RECEIPT PREREQUISITE"]),
                ),
            ),
        )
    assert rejected.value.prerequisite_code == "PARSER_TERMINAL_RECEIPT_UNAVAILABLE"
    assert _source_rows(app_engine, tenant) == before


def _installed_sql_hash(value: object) -> str:
    """Read-only admin constant oracle, NOT app-role census or native execution."""
    with get_admin_engine().connect() as conn, conn.begin():
        conn.execute(text("SET TRANSACTION READ ONLY"))
        assert conn.execute(text("SHOW transaction_read_only")).scalar_one() == "on"
        return str(
            conn.execute(
                text("SELECT public.idis_task7_source_hash_jsonb_v1(CAST(:value AS jsonb))"),
                {"value": json.dumps(value, allow_nan=False)},
            ).scalar_one()
        )


@pytest.mark.parametrize(
    "extension",
    [
        "pdf",
        "txt",
        "xlsx",
        "html",
        "docx-paragraph",
        "docx-cell",
        "pptx-paragraph",
        "pptx-cell",
    ],
)
def test_real_native_upload_package_freeze_binds_exact_attempt_atomically(
    app_engine: Engine,
    tmp_path: Path,
    extension: str,
) -> None:
    tenant, deal, result, package_id = _real_open_native_package(app_engine, tmp_path, extension)
    with app_engine.begin() as conn:
        set_tenant_local(conn, tenant)
        assert tuple(
            conn.execute(
                text(
                    "SELECT source_binding_state,doc_id,document_id FROM data_room_package_files "
                    "WHERE package_id=:package"
                ),
                {"package": package_id},
            ).one()
        ) == ("UNBOUND", None, None)
        assert (
            conn.execute(
                text("SELECT parse_attempt_id FROM document_spans WHERE document_id=:document"),
                {"document": str(result.document_id)},
            ).scalar_one()
            is None
        )
        attempt_id = conn.execute(
            text(
                "SELECT parse_attempt_id FROM document_parse_attempts WHERE document_id=:document"
            ),
            {"document": str(result.document_id)},
        ).scalar_one()
    receipt = freeze_source_package(
        engine=app_engine, tenant_id=tenant, deal_id=deal, package_id=package_id
    )
    assert receipt.root_count == 6
    with app_engine.begin() as conn:
        set_tenant_local(conn, tenant)
        assert tuple(
            conn.execute(
                text(
                    "SELECT source_binding_state,doc_id::text,document_id::text "
                    "FROM data_room_package_files "
                    "WHERE package_id=:package"
                ),
                {"package": package_id},
            ).one()
        ) == (
            "BOUND",
            str(result.artifact_id),
            str(result.document_id),
        )
        assert tuple(
            conn.execute(
                text(
                    "SELECT source_binding_state,parse_attempt_id FROM document_spans "
                    "WHERE document_id=:document"
                ),
                {"document": str(result.document_id)},
            ).one()
        ) == ("BOUND", attempt_id)
        assert tuple(
            conn.execute(
                text(
                    "SELECT status,source_binding_state FROM data_room_packages "
                    "WHERE package_id=:package"
                ),
                {"package": package_id},
            ).one()
        ) == ("SEALED", "BOUND")
        span = dict(
            conn.execute(
                text(
                    "SELECT s.*,r.object_identity_sha256 FROM document_spans s "
                    "JOIN source_lineage_objects r USING(source_lineage_object_id) "
                    "WHERE s.document_id=:document"
                ),
                {"document": str(result.document_id)},
            )
            .mappings()
            .one()
        )
    raw_locator_hash = _installed_sql_hash(["span-locator-v1", span["span_type"], span["locator"]])
    assert span["object_identity_sha256"] == _installed_sql_hash(
        [
            "document-span-identity-v1",
            str(span["span_id"]),
            str(span["document_id"]),
            str(attempt_id),
            span["span_type"],
            raw_locator_hash,
            span["content_hash"],
        ]
    )
    assert (
        freeze_source_package(
            engine=app_engine, tenant_id=tenant, deal_id=deal, package_id=package_id
        )
        == receipt
    )
    before = _source_rows(app_engine, tenant)
    with pytest.raises(DBAPIError) as error, app_engine.begin() as conn:
        set_tenant_local(conn, tenant)
        conn.execute(
            text(
                "UPDATE document_spans SET locator=jsonb_set(locator,'{line}','2'::jsonb) "
                "WHERE document_id=:document"
            ),
            {"document": str(result.document_id)},
        )
    assert error.value.orig.pgcode == "40001"
    assert error.value.orig.diag.message_primary == "TASK7_SOURCE_FREEZE_FENCED"
    assert _source_rows(app_engine, tenant) == before


@pytest.mark.parametrize(("doc_type", "span_type", "locator"), _locator_identity_cases())
def test_registered_locator_hash_matches_installed_sql_constant_oracle(
    app_engine: Engine,
    doc_type: str,
    span_type: str,
    locator: dict[str, Any],
) -> None:
    # Native formats also have real app-role upload/freeze coverage. These OCR/
    # media schema fixtures only prove unchanged SQL/Python identity parity;
    # they do not manufacture native Tesseract/H2 provenance or runtime attempts.
    validated = _validated_span_locator(span_type, locator, doc_type=doc_type)
    assert _sha256_json(["span-locator-v1", span_type, validated]) == _installed_sql_hash(
        ["span-locator-v1", span_type, locator]
    )


@pytest.mark.parametrize(
    "locator",
    [
        {"page": 1, "line": 1, "source": "native"},
        {"page": "1", "line": 1},
        {"page": 1, "line": True},
    ],
)
def test_real_native_pdf_locator_tamper_is_rejected_before_freeze_without_mutation(
    app_engine: Engine,
    tmp_path: Path,
    locator: dict[str, Any],
) -> None:
    tenant, deal, result, package = _real_open_native_package(app_engine, tmp_path, "pdf")
    with app_engine.begin() as conn:
        set_tenant_local(conn, tenant)
        conn.execute(
            text(
                "UPDATE document_spans SET locator=CAST(:locator AS jsonb) "
                "WHERE document_id=:document"
            ),
            {"locator": json.dumps(locator), "document": str(result.document_id)},
        )
    before = _source_rows(app_engine, tenant)
    with pytest.raises(Task7SourceFreezeError) as error:
        freeze_source_package(engine=app_engine, tenant_id=tenant, deal_id=deal, package_id=package)
    assert error.value.code == "TASK7_SOURCE_TERMINAL_STATE_INVALID"
    assert _source_rows(app_engine, tenant) == before
    assert before["source_lineage_objects"] == []


def test_real_native_pdf_forged_span_root_fails_installed_deferred_guard(
    app_engine: Engine,
    tmp_path: Path,
) -> None:
    tenant, deal, _result, package = _real_open_native_package(app_engine, tmp_path, "pdf")
    with app_engine.begin() as conn:
        observation = PostgresTask7SourceFreezeRepository(conn, tenant).observe_source_package(
            deal_id=deal, package_id=package
        )
    before = _source_rows(app_engine, tenant)
    with pytest.raises(DBAPIError) as error, app_engine.begin() as conn:
        repo = PostgresTask7SourceFreezeRepository(conn, tenant)
        snapshot = repo.lock_source_graph(observation)
        validate_locked_source_snapshot(observation, snapshot)
        plan = build_source_freeze_plan(snapshot)
        forged = replace(
            plan,
            roots=tuple(
                replace(root, object_identity_sha256="0" * 64)
                if root.object_kind == "SPAN"
                else root
                for root in plan.roots
            ),
        )
        # Direct untrusted app-writer probe: bypass only Python reload validation,
        # never SQL. All parents and other roots came from the real native upload.
        repo._insert_roots(forged)
        repo._bind_native_rows(plan, observation)
        conn.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    assert error.value.orig.pgcode == "23514"
    assert error.value.orig.diag.message_primary == "TASK7_SOURCE_TERMINAL_STATE_INVALID"
    assert _source_rows(app_engine, tenant) == before


@pytest.mark.parametrize("completed_prefix", [0, 1])
def test_real_native_pdf_source_survives_hermetic_admission_and_restart_cut(
    app_engine: Engine,
    tmp_path: Path,
    completed_prefix: int,
) -> None:
    """Real native source; synthetic strict-proof/output mechanics, not FULL acceptance."""
    tenant, deal, _result, package = _real_open_native_package(app_engine, tmp_path, "pdf")
    frozen = freeze_source_package(
        engine=app_engine, tenant_id=tenant, deal_id=deal, package_id=package
    )
    source_before = _source_rows(app_engine, tenant)
    source_before.pop("audit_events")
    registry_before = dict(contract_registry.PIPELINE_CONTRACTS)
    # Reuse the reviewed C2 protocol fixture: corpus is this actual production-
    # frozen public PDF; other proof hashes and step outputs stay explicitly
    # synthetic. This is not real operator authority or a producer activation.
    proof = insert_synthetic_proof(
        app_engine, SyntheticFrozenSource(tenant, deal, package, frozen.manifest_sha256)
    )
    run_id, owner = str(uuid.uuid4()), str(uuid.uuid4())
    with app_engine.begin() as conn:
        run = admit_synthetic_proof(admission_repository(conn, tenant), proof, run_id)
    with app_engine.begin() as conn:
        claimed = PostgresRunsRepository(conn, tenant).claim_queued_runs(
            limit=1,
            lease_owner_id=owner,
            lease_seconds=120,
            lease_audit_emitter=_lease_transition_audit_emitter(
                audit_sink=PostgresAuditSink(),
                tenant_id=tenant,
            ),
        )
        assert len(claimed) == 1 and claimed[0]["run_id"] == run_id
    scope = Task7LeaseScope(
        tenant_id=tenant,
        deal_id=deal,
        run_id=run_id,
        pipeline_contract_version=run["pipeline_contract_version"],
        pipeline_contract_hash=run["pipeline_contract_hash"],
        pipeline_resume_state="RESUMABLE",
        lease_owner_id=owner,
        lease_fencing_token=int(claimed[0]["lease_fencing_token"]),
    )
    with app_engine.begin() as conn:
        admission_repository(conn, tenant).require_task7_admission_graph_for_run(
            deal_id=deal,
            run_id=run_id,
        )
    if completed_prefix:
        _complete_hermetic_step(SimpleNamespace(app_engine=app_engine), scope=scope, step_order=0)
    # A separate app connection models process restart and invokes the actual
    # worker source/admission graph rederivation, not cached ingestion objects.
    with app_engine.begin() as conn:
        admission_repository(conn, tenant).require_task7_admission_graph_for_run(
            deal_id=deal,
            run_id=run_id,
        )
        port = PostgresTask7CompletionPort(conn, tenant, PostgresAuditSink(), 120)
        port.establish_tenant_rls(scope=scope)
        assert port.load_resumable_run(scope=scope)["run_id"] == run_id
        steps = tuple(port.list_canonical_steps(scope=scope))
        assert len(steps) == len(contract_registry.PIPELINE_CONTRACTS["full-strict-v21"].steps)
        restarted = rehydrate_strict_run(
            tenant_id=tenant,
            run_id=run_id,
            pipeline_contract_version=scope.pipeline_contract_version,
            pipeline_contract_hash=scope.pipeline_contract_hash,
            pipeline_resume_state=scope.pipeline_resume_state,
            steps=steps,
            validate_completed=lambda step: (
                step.result_summary.get("producer_kind") == "TASK7_RESTART_PROOF"
            ),
        )
        assert restarted.completed_count == restarted.next_step_order == completed_prefix
        assert port.validate_step_predecessors(scope=scope, step=steps[completed_prefix])
    source_after = _source_rows(app_engine, tenant)
    source_after.pop("audit_events")
    assert source_after == source_before
    assert dict(contract_registry.PIPELINE_CONTRACTS) == registry_before


def _synthetic_upload_receipts(prep: Any) -> list[dict[str, Any]]:
    """Unit-only returned-row shapes; never inserted or called a frozen corpus."""
    return [
        {
            "tenant_id": prep.tenant_id,
            "deal_id": prep.deal_id,
            "document_id": str(uuid.uuid4()),
            "doc_id": str(uuid.uuid4()),
            "sha256": row.content_sha256,
            "source_metadata": {**prep.upload_metadata(row.sequence), "size_bytes": row.size_bytes},
        }
        for row in prep.files
    ]


@pytest.mark.parametrize(
    "drift",
    [
        "tenant",
        "deal",
        "hash",
        "size",
        "name",
        "fingerprint",
        "producer",
        "missing",
        "duplicate",
    ],
)
def test_preview_preparation_rejects_changed_actual_upload_receipts(drift: str) -> None:
    prep = create_preview_preparation(
        tenant_id=str(uuid.uuid4()),
        deal_id=str(uuid.uuid4()),
        files=[("txt", b"SYNTHETIC PREPARATION A"), ("txt", b"SYNTHETIC PREPARATION B")],
    )
    receipts = _synthetic_upload_receipts(prep)
    assert prep.build_manifest(receipts).company_alias == "company-999"
    changed = deepcopy(receipts)
    if drift in {"tenant", "deal"}:
        changed[0][drift + "_id"] = str(uuid.uuid4())
    elif drift == "hash":
        changed[0]["sha256"] = "0" * 64
    elif drift == "size":
        changed[0]["source_metadata"]["size_bytes"] += 1
    elif drift == "name":
        changed[0]["source_metadata"]["upload_filename"] = "company-001-document-00001.txt"
    elif drift == "fingerprint":
        changed[0]["source_metadata"]["source_tree_fingerprint"] = "0" * 64
    elif drift == "producer":
        changed[0]["source_metadata"]["source_system"] = "upload"
    elif drift == "missing":
        changed.pop()
    else:
        changed[1] = deepcopy(changed[0])
    with pytest.raises(ValueError, match="preview"):
        prep.build_manifest(changed)


def test_preview_manifest_cannot_satisfy_unchanged_two_company_acceptance_producer(
    tmp_path: Path,
) -> None:
    from idis.evaluation import real_example_source_package as acceptance

    fixture = build_source_fixture(tmp_path)
    contract = acceptance.build_two_company_source_contract(**contract_kwargs(fixture))
    company = contract.companies[0]
    prep = create_preview_preparation(
        tenant_id=str(uuid.uuid4()),
        deal_id=str(uuid.uuid4()),
        files=[
            (row.extension, contract.source_path(company.company_alias, row.sequence).read_bytes())
            for row in company.files
        ],
    )
    receipts = _synthetic_upload_receipts(prep)
    preview = prep.build_manifest(receipts)
    expected = acceptance._source_package_manifest(company, contract, receipts)
    assert preview.company_alias == "company-999" and expected.company_alias == "company-001"
    assert preview.source_tree_fingerprint != expected.source_tree_fingerprint
    assert preview.manifest_sha256 != expected.manifest_sha256
    package_id = str(uuid.uuid4())
    with pytest.raises(acceptance.SourcePackageContractError) as error:
        acceptance._validate_durable_package_header(
            record={
                "deal_id": prep.deal_id,
                "package_id": package_id,
                "file_count": preview.file_count,
                "source_manifest": preview.model_dump(mode="json"),
            },
            deal_id=prep.deal_id,
            package_id=package_id,
            manifest_payload=expected.model_dump(mode="json"),
        )
    assert error.value.code == "DATA_ROOM_DURABLE_HANDOFF_INCOMPLETE"


@pytest.mark.parametrize(
    "origin,expected_claim_audit_ok",
    [("worker", True), ("api-only", False), ("worker-plus-api", True)],
)
def test_real_app_role_probe_excludes_api_audits_even_when_request_id_equals_run_id(
    app_engine: Engine, origin: str, expected_claim_audit_ok: bool
) -> None:
    """Only test origin qualification; no canonical corpus or boundary acceptance."""
    tenant_scopes: list[str] = []
    inserted_events: list[dict[str, Any]] = []

    def actual_tenant_fixture(conn: Connection, tenant_id: str) -> None:
        # Current0038 has a real tenant FK. This fixture creates that parent in
        # the same rollback transaction; it does not fix the production probe.
        set_tenant_local(conn, tenant_id)
        tenant_scopes.append(tenant_id)
        conn.execute(
            text(
                "INSERT INTO tenants (tenant_id,name) VALUES "
                "(CAST(:tenant AS uuid),'C3 origin-only probe fixture') "
                "ON CONFLICT (tenant_id) DO NOTHING"
            ),
            {"tenant": tenant_id},
        )

    class ActualDurableSink:
        def emit_in_tx(self, conn: Connection, event: dict[str, Any]) -> None:
            sink = PostgresAuditSink()
            if origin != "api-only":
                sink.emit_in_tx(conn, event)
                inserted_events.append(event)
            if origin != "worker":
                run_id = event["payload"]["safe"]["run_id"]
                api_event = build_canonical_audit_event(
                    tenant_id=event["tenant_id"],
                    event_type=event["event_type"],
                    severity=event["severity"],
                    summary="Synthetic API-inline component-origin test",
                    actor_type="SERVICE",
                    actor_id="idis-api-inline-executor",
                    request_id=run_id,
                    method="POST",
                    path=f"/v1/runs/{run_id}/retry",
                    status_code=202,
                    resource_type=event["resource"]["resource_type"],
                    resource_id=event["resource"]["resource_id"],
                    deal_id=event["resource"].get("deal_id"),
                    safe={**event["payload"]["safe"], "execution_origin": "API_INLINE"},
                )
                sink.emit_in_tx(conn, api_event)
                inserted_events.append(api_event)
            # A real app-role SELECT proves each event was actually inserted.
            count = conn.execute(
                text("SELECT count(*) FROM audit_events WHERE tenant_id=CAST(:tenant AS uuid)"),
                {"tenant": event["tenant_id"]},
            ).scalar_one()
            assert count == len(inserted_events)

    facts = collect_worker_claim_facts(
        connection_factory=app_engine.connect,
        boundary_transaction_factory=app_engine.begin,
        tenant_setter=actual_tenant_fixture,
        runs_repository_factory=PostgresRunsRepository,
        run_steps_repository_factory=PostgresRunStepsRepository,
        run_step_factory=RunStep,
        worker_lease_fenced_error=WorkerLeaseFencedError,
        audit_sink_factory=ActualDurableSink,
        lease_audit_emitter_factory=_lease_transition_audit_emitter,
        lease_seconds=60,
        # Deliberately unproven in this origin-only test, never a fake pass.
        boundary_rollback_probe=lambda **_kwargs: (False, False),
        uuid_factory=uuid.uuid4,
    )
    assert facts.queued_row_ok and facts.claim_ok and facts.fencing_ok
    assert facts.heartbeat_ok and facts.reclaim_ok and facts.fresh_claim_ok
    assert facts.stale_write_rejected
    assert facts.claim_audit_ok is expected_claim_audit_ok
    assert facts.boundary_rollback_ok is False
    assert facts.no_residue is False  # Combined readiness is intentionally NOT proven.
    assert len(inserted_events) == (10 if origin == "worker-plus-api" else 5)
    assert tenant_scopes and len(set(tenant_scopes)) == 1
    with app_engine.begin() as conn:
        set_tenant_local(conn, tenant_scopes[0])
        for table in ("tenants", "deals", "runs", "run_steps", "audit_events"):
            assert (
                conn.execute(
                    text(f"SELECT count(*) FROM {table} WHERE tenant_id=CAST(:tenant AS uuid)"),
                    {"tenant": tenant_scopes[0]},
                ).scalar_one()
                == 0
            )


def test_freeze_selected_source_packages_distinguishes_refusal_codes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Offline unit proof that CR-02 refusal classes produce distinct safe 409 codes."""
    from idis.api.routes import runs as routes
    from idis.persistence.repositories.strict_preflight_proofs import StrictPreflightProofError
    from idis.persistence.repositories.task7_source_freeze import Task7SourceFreezeError

    tenant_id = str(uuid.uuid4())
    deal_id = str(uuid.uuid4())
    prep = create_preview_preparation(
        tenant_id=tenant_id,
        deal_id=deal_id,
        files=[("txt", b"SYNTHETIC A"), ("txt", b"SYNTHETIC B")],
    )
    receipts = _synthetic_upload_receipts(prep)
    manifest_two = prep.build_manifest(receipts).model_dump(mode="json")
    doc_1 = str(receipts[0]["document_id"])
    pkg_id = str(uuid.uuid4())

    prep_one = create_preview_preparation(
        tenant_id=tenant_id,
        deal_id=deal_id,
        files=[("txt", b"SYNTHETIC SINGLE")],
    )
    receipts_one = _synthetic_upload_receipts(prep_one)
    manifest_one = prep_one.build_manifest(receipts_one).model_dump(mode="json")
    doc_single = str(receipts_one[0]["document_id"])

    def _req(conn: Any) -> Any:
        return SimpleNamespace(
            state=SimpleNamespace(db_conn=conn),
            app=SimpleNamespace(state=SimpleNamespace()),
        )

    # 1. Missing DB / missing package -> SOURCE_PACKAGE_REQUIRED
    with pytest.raises(StrictPreflightProofError) as missing_db:
        routes._freeze_task7_selected_source_packages(
            request=_req(None),
            tenant_id=tenant_id,
            deal_id=deal_id,
            selected_document_ids=(doc_1,),
        )
    assert missing_db.value.code == "SOURCE_PACKAGE_REQUIRED"

    class _FakePackageConn:
        def __init__(self, rows: list[dict[str, Any]]) -> None:
            self._rows = [SimpleNamespace(_mapping=row) for row in rows]

        def execute(self, statement: object, params: dict[str, Any] | None = None) -> Any:
            _ = (statement, params)
            return SimpleNamespace(fetchall=lambda: list(self._rows))

    with pytest.raises(StrictPreflightProofError) as missing_pkg:
        routes._freeze_task7_selected_source_packages(
            request=_req(_FakePackageConn([])),
            tenant_id=tenant_id,
            deal_id=deal_id,
            selected_document_ids=(doc_1,),
        )
    assert missing_pkg.value.code == "SOURCE_PACKAGE_REQUIRED"

    # 2. Partial selection of a manifest-backed package -> PARTIAL_PACKAGE_SELECTION_REFUSED
    with pytest.raises(StrictPreflightProofError) as partial_sel:
        routes._freeze_task7_selected_source_packages(
            request=_req(
                _FakePackageConn([{"package_id": pkg_id, "source_manifest": manifest_two}])
            ),
            tenant_id=tenant_id,
            deal_id=deal_id,
            selected_document_ids=(doc_1,),
        )
    assert partial_sel.value.code == "PARTIAL_PACKAGE_SELECTION_REFUSED"

    # 3. Preparation prerequisite unmet (e.g. scanned PDF / OCR member)
    # -> SOURCE_PREPARATION_PREREQUISITE_UNMET
    def _raise_scanned_pdf_freeze_error(**_kwargs: object) -> None:
        raise Task7SourceFreezeError("TASK7_SOURCE_TERMINAL_STATE_INVALID")

    monkeypatch.setattr("idis.persistence.db.get_app_engine", lambda: object())
    monkeypatch.setattr(
        "idis.persistence.repositories.task7_source_freeze.freeze_source_package",
        _raise_scanned_pdf_freeze_error,
    )
    with pytest.raises(StrictPreflightProofError) as prereq_unmet:
        routes._freeze_task7_selected_source_packages(
            request=_req(
                _FakePackageConn([{"package_id": pkg_id, "source_manifest": manifest_one}])
            ),
            tenant_id=tenant_id,
            deal_id=deal_id,
            selected_document_ids=(doc_single,),
        )
    assert prereq_unmet.value.code == "SOURCE_PREPARATION_PREREQUISITE_UNMET"


def _freeze_one_package_with_error(
    monkeypatch: pytest.MonkeyPatch,
    *,
    internal_code: str,
) -> None:
    """Drive one complete-package freeze whose producer raises ``internal_code``."""
    from idis.api.routes import runs as routes
    from idis.persistence.repositories.task7_source_freeze import Task7SourceFreezeError

    tenant_id = str(uuid.uuid4())
    deal_id = str(uuid.uuid4())
    prep = create_preview_preparation(
        tenant_id=tenant_id,
        deal_id=deal_id,
        files=[("txt", b"SYNTHETIC SINGLE")],
    )
    receipts = _synthetic_upload_receipts(prep)
    manifest = prep.build_manifest(receipts).model_dump(mode="json")
    doc_id = str(receipts[0]["document_id"])
    package_id = str(uuid.uuid4())

    class _FakePackageConn:
        def execute(self, statement: object, params: dict[str, Any] | None = None) -> Any:
            _ = (statement, params)
            row = SimpleNamespace(_mapping={"package_id": package_id, "source_manifest": manifest})
            return SimpleNamespace(fetchall=lambda: [row])

    def _raise(**_kwargs: object) -> None:
        raise Task7SourceFreezeError(internal_code)

    monkeypatch.setattr("idis.persistence.db.get_app_engine", lambda: object())
    monkeypatch.setattr(
        "idis.persistence.repositories.task7_source_freeze.freeze_source_package",
        _raise,
    )
    routes._freeze_task7_selected_source_packages(
        request=SimpleNamespace(
            state=SimpleNamespace(db_conn=_FakePackageConn()),
            app=SimpleNamespace(state=SimpleNamespace()),
        ),
        tenant_id=tenant_id,
        deal_id=deal_id,
        selected_document_ids=(doc_id,),
    )


@pytest.mark.parametrize(
    ("internal_code", "public_code"),
    [
        (
            "TASK7_PARSE_ATTEMPT_LINEAGE_UNRECONSTRUCTIBLE",
            "SOURCE_PREPARATION_PREREQUISITE_UNMET",
        ),
        ("TASK7_SOURCE_FREEZE_FENCED", "SOURCE_FREEZE_CONFLICT"),
    ],
)
def test_source_freeze_public_code_follows_error_class(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    internal_code: str,
    public_code: str,
) -> None:
    """Prerequisite freeze codes stay distinct from retryable fence conflicts."""
    from idis.api.errors import IdisHttpError
    from idis.persistence.repositories.strict_preflight_proofs import StrictPreflightProofError

    with caplog.at_level("ERROR"), pytest.raises(StrictPreflightProofError) as caught:
        _freeze_one_package_with_error(monkeypatch, internal_code=internal_code)
    assert caught.value.code == public_code
    assert internal_code not in str(caught.value)
    assert internal_code in caplog.text
    http_error = IdisHttpError(
        status_code=409,
        code=caught.value.code,
        message="Frozen source preparation or strict proof did not authorize this run",
    )
    assert http_error.details is None


def test_unknown_source_freeze_code_is_not_relabelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unmapped freeze code must surface, not become a client prerequisite."""
    from idis.persistence.repositories.task7_source_freeze import Task7SourceFreezeError

    with pytest.raises(Task7SourceFreezeError) as caught:
        _freeze_one_package_with_error(monkeypatch, internal_code="TASK7_UNMAPPED_FREEZE")
    assert caught.value.code == "TASK7_UNMAPPED_FREEZE"


def test_frozen_reload_maps_only_frozen_source_required(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Loader bugs stay 500s; only FROZEN_SOURCE_REQUIRED becomes a safe 409."""
    from idis.api.routes import runs as routes
    from idis.persistence.repositories.strict_preflight_proofs import StrictPreflightProofError

    def _required(**_kwargs: object) -> list[dict[str, Any]]:
        raise ValueError("FROZEN_SOURCE_REQUIRED")

    monkeypatch.setattr(
        "idis.services.runs.steps.load_document_preflight_corpus_for_deal",
        _required,
    )
    with caplog.at_level("ERROR"), pytest.raises(StrictPreflightProofError) as required:
        routes._reload_frozen_preflight_corpus(
            db_conn=object(),
            tenant_id=str(uuid.uuid4()),
            deal_id=str(uuid.uuid4()),
            selected_document_ids=(str(uuid.uuid4()),),
        )
    assert required.value.code == "SOURCE_PACKAGE_REQUIRED"
    assert "FROZEN_SOURCE_REQUIRED" in caplog.text
    assert "FROZEN_SOURCE_REQUIRED" not in str(required.value)

    def _bug(**_kwargs: object) -> list[dict[str, Any]]:
        raise ValueError("loader bug")

    monkeypatch.setattr(
        "idis.services.runs.steps.load_document_preflight_corpus_for_deal",
        _bug,
    )
    with pytest.raises(ValueError, match="loader bug"):
        routes._reload_frozen_preflight_corpus(
            db_conn=object(),
            tenant_id=str(uuid.uuid4()),
            deal_id=str(uuid.uuid4()),
            selected_document_ids=(str(uuid.uuid4()),),
        )


def test_api_partial_package_selection_returns_distinct_409_code(
    app_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Current-limitation diagnostic for RUN_STATE input-support mapping item 3.

    Selecting one document from a two-document package returns 409
    PARTIAL_PACKAGE_SELECTION_REFUSED. Subset selection remains a documented
    obligation; this refusal is not completed input support.
    """
    tenant, deal = _scope(app_engine)
    pdf_a = create_test_pdf(["PUBLIC SYNTHETIC PARTIAL DOC A"])
    pdf_b = create_test_pdf(["PUBLIC SYNTHETIC PARTIAL DOC B"])
    prep = create_preview_preparation(
        tenant_id=tenant,
        deal_id=deal,
        files=[("pdf", pdf_a), ("pdf", pdf_b)],
    )
    store = ComplianceEnforcedStore(inner_store=FilesystemObjectStore(base_dir=tmp_path))
    uploaded_ids: list[str] = []
    with app_engine.begin() as conn:
        service = IngestionService(compliant_store=store, db_conn=conn)
        for idx, data in enumerate((pdf_a, pdf_b)):
            res = _upload(
                service,
                tenant,
                deal,
                data,
                name=prep.files[idx].generated_upload_name,
                metadata=prep.upload_metadata(idx),
                media_type="application/pdf",
            )
            assert res.success
            uploaded_ids.append(str(res.document_id))
    with app_engine.begin() as conn:
        documents = PostgresDocumentsRepository(conn, tenant).list_documents_by_deal(deal)
        manifest = prep.build_manifest(documents)
        create_data_room_package(
            repo=PostgresDataRoomPackagesRepository(conn, tenant),
            tenant_id=tenant,
            deal_id=deal,
            created_by_actor_id="C3-test",
            created_by_actor_type="SERVICE",
            document_ids=uploaded_ids,
            documents=documents,
            source_manifest=manifest,
        )
    client = _preview_client(monkeypatch, tenant, deal)
    before = _source_rows(app_engine, tenant)
    response = client.post(
        f"/v1/deals/{deal}/runs",
        json={
            "mode": "SNAPSHOT",
            "source": {"type": "deal_documents", "document_ids": [uploaded_ids[0]]},
        },
    )
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["code"] == "PARTIAL_PACKAGE_SELECTION_REFUSED"
    assert body["message"] == "Frozen source preparation or strict proof did not authorize this run"
    assert body.get("details") is None
    after = _source_rows(app_engine, tenant)
    assert {k: v for k, v in before.items() if k != "audit_events"} == {
        k: v for k, v in after.items() if k != "audit_events"
    }
    with app_engine.begin() as conn:
        set_tenant_local(conn, tenant)
        assert conn.execute(text("SELECT count(*) FROM runs")).scalar_one() == 0


def test_api_scanned_pdf_member_returns_preparation_prerequisite_unmet_409(
    app_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A packaged scanned PDF is refused with 409 SOURCE_PREPARATION_PREREQUISITE_UNMET."""
    from tests.test_tesseract_ocr_adapter import _create_image_text_pdf

    tenant, deal = _scope(app_engine)
    scanned_bytes = _create_image_text_pdf("PUBLIC SYNTHETIC OCR PAGE")
    prep = create_preview_preparation(
        tenant_id=tenant,
        deal_id=deal,
        files=[("pdf", scanned_bytes)],
    )
    store = ComplianceEnforcedStore(inner_store=FilesystemObjectStore(base_dir=tmp_path))
    with app_engine.begin() as conn:
        service = IngestionService(compliant_store=store, db_conn=conn)
        res = _upload(
            service,
            tenant,
            deal,
            scanned_bytes,
            name=prep.files[0].generated_upload_name,
            metadata=prep.upload_metadata(0),
            media_type="application/pdf",
        )
    with app_engine.begin() as conn:
        documents = PostgresDocumentsRepository(conn, tenant).list_documents_by_deal(
            deal, parsed_only=False
        )
        manifest = prep.build_manifest(documents)
        package = create_data_room_package(
            repo=PostgresDataRoomPackagesRepository(conn, tenant),
            tenant_id=tenant,
            deal_id=deal,
            created_by_actor_id="C3-test",
            created_by_actor_type="SERVICE",
            document_ids=[str(res.document_id)],
            documents=documents,
            source_manifest=manifest,
        )
    client = _preview_client(monkeypatch, tenant, deal)
    before = _source_rows(app_engine, tenant)
    response = client.post(f"/v1/deals/{deal}/runs", json={"mode": "SNAPSHOT"})
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["code"] == "SOURCE_PREPARATION_PREREQUISITE_UNMET"
    assert body["message"] == "Frozen source preparation or strict proof did not authorize this run"
    assert body.get("details") is None
    assert prep.files[0].generated_upload_name not in response.text
    after = _source_rows(app_engine, tenant)
    assert {k: v for k, v in before.items() if k != "audit_events"} == {
        k: v for k, v in after.items() if k != "audit_events"
    }
    with app_engine.begin() as conn:
        set_tenant_local(conn, tenant)
        assert tuple(
            conn.execute(
                text(
                    "SELECT status, source_binding_state FROM data_room_packages "
                    "WHERE package_id=:package"
                ),
                {"package": package["package_id"]},
            ).one()
        ) == ("OPEN", "UNBOUND")
        assert conn.execute(text("SELECT count(*) FROM runs")).scalar_one() == 0


def test_api_reuses_sealed_package_after_run_already_active_conflict(
    app_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A package sealed before a RUN_ALREADY_ACTIVE 409 remains sealed and is reused on retry."""
    from idis.persistence.repositories.runs import RunAlreadyActiveError

    tenant, deal, uploaded, package_id = _real_open_native_package(app_engine, tmp_path, "pdf")
    client = _preview_client(monkeypatch, tenant, deal)

    original_create = PostgresRunsRepository.create
    fail_once = True

    def conflict_after_freeze(self: PostgresRunsRepository, **kwargs: Any) -> dict[str, Any]:
        nonlocal fail_once
        if fail_once:
            fail_once = False
            raise RunAlreadyActiveError(deal)
        return original_create(self, **kwargs)

    monkeypatch.setattr(PostgresRunsRepository, "create", conflict_after_freeze)

    first = client.post(f"/v1/deals/{deal}/runs", json={"mode": "SNAPSHOT"})
    assert first.status_code == 409, first.text
    assert first.json()["code"] == "RUN_ALREADY_ACTIVE"

    with app_engine.begin() as conn:
        set_tenant_local(conn, tenant)
        assert tuple(
            conn.execute(
                text(
                    "SELECT status, source_binding_state FROM data_room_packages "
                    "WHERE package_id=:package"
                ),
                {"package": package_id},
            ).one()
        ) == ("SEALED", "BOUND")
        assert conn.execute(text("SELECT count(*) FROM runs")).scalar_one() == 0
        sealed_lineage_rows = list(
            conn.execute(
                text(
                    "SELECT source_lineage_object_id::text FROM source_lineage_objects "
                    "WHERE corpus_receipt_id=CAST(:package AS uuid) "
                    "ORDER BY source_lineage_object_id::text"
                ),
                {"package": package_id},
            ).scalars()
        )
        assert len(sealed_lineage_rows) == 6

    second = client.post(
        f"/v1/deals/{deal}/runs",
        json={
            "mode": "SNAPSHOT",
            "source": {"type": "deal_documents", "document_ids": [str(uploaded.document_id)]},
        },
    )
    assert second.status_code == 202, second.text
    assert second.json()["status"] == "SUCCEEDED", second.text

    with app_engine.begin() as conn:
        set_tenant_local(conn, tenant)
        assert conn.execute(text("SELECT count(*) FROM runs")).scalar_one() == 1
        reused_lineage_rows = list(
            conn.execute(
                text(
                    "SELECT source_lineage_object_id::text FROM source_lineage_objects "
                    "WHERE corpus_receipt_id=CAST(:package AS uuid) "
                    "ORDER BY source_lineage_object_id::text"
                ),
                {"package": package_id},
            ).scalars()
        )
        assert reused_lineage_rows == sealed_lineage_rows
