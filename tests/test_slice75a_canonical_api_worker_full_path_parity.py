"""Slice75A RED tests for canonical API/worker FULL path parity.

These tests intentionally define target behavior for Slice75A and are expected
to fail before production implementation is added.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from idis.api.auth import IDIS_API_KEYS_ENV
from idis.api.main import create_app
from idis.audit.sink import InMemoryAuditSink
from idis.models.run_step import RunStep
from idis.persistence.repositories.run_steps import InMemoryRunStepsRepository
from idis.pipeline.worker import (
    InvalidRunSourceSelectionError,
    PipelineWorker,
    _default_execution_service_factory,
    _default_run_context_factory,
)
from idis.services.runs.execution import RunExecutionResult
from tests.abac_seed import seed_deal_access

API_KEY = "slice75a-red-key"
TENANT_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
TENANT_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


@dataclass
class _RunsRepoStub:
    """Simple tenant-scoped queue repository stub for worker tests."""

    queued: list[dict[str, Any]]
    complete_calls: list[tuple[str, str, str | None]]
    claim_calls: int = 0

    def claim_queued_runs(self, *, limit: int = 10) -> list[dict[str, Any]]:
        self.claim_calls += 1
        return self.queued[:limit]

    def try_mark_running(self, run_id: str) -> bool:
        return True

    def complete(self, run_id: str, *, status: str, finished_at: str | None) -> None:
        self.complete_calls.append((run_id, status, finished_at))


class _BlockingStrictReport:
    may_proceed = False

    def model_dump(self, *, mode: str = "json") -> dict[str, Any]:
        assert mode == "json"
        return {
            "required": True,
            "may_proceed": False,
            "blocker_count": 1,
            "blocking_components": ["live_llm_model_clients"],
            "components": [],
        }


def test_worker_full_strict_gate_blocks_before_run_execution_service_execute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Worker FULL strict preflight must block before canonical execution service."""
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "1")

    conn = MagicMock()
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn

    runs_repo = _RunsRepoStub(
        queued=[
            {
                "run_id": "run-full-1",
                "tenant_id": TENANT_A,
                "deal_id": "deal-1",
                "mode": "FULL",
                "source": {"document_ids": ["doc-1"]},
            }
        ],
        complete_calls=[],
    )
    execution_service = MagicMock()
    execution_service.audit_sink = InMemoryAuditSink()

    worker = PipelineWorker(
        poll_interval=0,
        tenant_ids=[TENANT_A],
        execution_service_factory=lambda **_kwargs: execution_service,
        run_context_factory=lambda **_kwargs: MagicMock(),
    )

    with (
        patch("idis.pipeline.worker.get_app_engine", return_value=engine),
        patch("idis.pipeline.worker.get_runs_repository", return_value=runs_repo),
        patch("idis.pipeline.worker.set_tenant_local", create=True),
    ):
        asyncio.run(worker._process_queued_runs())

    assert execution_service.execute.call_count == 0
    assert any(status == "FAILED" for _run_id, status, _finished in runs_repo.complete_calls)


def test_api_and_worker_share_strict_readiness_helper_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """API and worker strict FULL paths should call one shared readiness helper."""
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "1")
    monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(_api_keys()))
    api_calls: list[dict[str, Any]] = []
    worker_calls: list[dict[str, Any]] = []

    def record_api_strict_call(**kwargs: Any) -> _BlockingStrictReport:
        api_calls.append(kwargs)
        return _BlockingStrictReport()

    def record_worker_strict_call(**kwargs: Any) -> _BlockingStrictReport:
        worker_calls.append(kwargs)
        return _BlockingStrictReport()

    app = create_app(audit_sink=InMemoryAuditSink(), service_region="me-south-1")
    app.state.deal_documents = {}
    client = TestClient(app)
    create_resp = client.post(
        "/v1/deals",
        json={"name": "Slice75A Strict", "company_name": "StrictCo"},
        headers={"X-IDIS-API-Key": API_KEY},
    )
    assert create_resp.status_code == 201
    deal_id = create_resp.json()["deal_id"]
    seed_deal_access(TENANT_A, deal_id, "actor-a")
    app.state.deal_documents[deal_id] = [_preflight_doc(deal_id=deal_id)]

    with patch(
        "idis.api.routes.runs.build_strict_full_live_admission_report",
        side_effect=record_api_strict_call,
    ):
        api_resp = client.post(
            f"/v1/deals/{deal_id}/runs",
            json={"mode": "FULL"},
            headers={"X-IDIS-API-Key": API_KEY},
        )
    assert api_resp.status_code == 409

    conn = MagicMock()
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn
    runs_repo = _RunsRepoStub(
        queued=[
            {
                "run_id": "run-full-2",
                "tenant_id": TENANT_A,
                "deal_id": deal_id,
                "mode": "FULL",
                "source": {"type": "deal_documents", "document_ids": ["doc-strict"]},
            }
        ],
        complete_calls=[],
    )
    worker = PipelineWorker(
        poll_interval=0,
        tenant_ids=[TENANT_A],
        execution_service_factory=lambda **_kwargs: MagicMock(
            audit_sink=InMemoryAuditSink(),
            execute=MagicMock(),
        ),
        run_context_factory=lambda **_kwargs: MagicMock(),
    )

    with (
        patch("idis.pipeline.worker.get_app_engine", return_value=engine),
        patch("idis.pipeline.worker.get_runs_repository", return_value=runs_repo),
        patch(
            "idis.pipeline.worker._load_worker_preflight_corpus",
            return_value=[_preflight_doc(deal_id=deal_id)],
        ),
        patch(
            "idis.pipeline.worker.build_strict_full_live_admission_report",
            side_effect=record_worker_strict_call,
        ),
        patch("idis.pipeline.worker.set_tenant_local", create=True),
    ):
        asyncio.run(worker._process_queued_runs())

    assert len(api_calls) == 1
    assert len(worker_calls) == 1
    api_call = api_calls[0]
    worker_call = worker_calls[0]
    assert api_call["tenant_id"] == worker_call["tenant_id"] == TENANT_A
    assert api_call["strict_dotenv_path"] == worker_call["strict_dotenv_path"]
    assert _selected_preflight_document_ids(
        api_call["preflight_corpus"]
    ) == _selected_preflight_document_ids(worker_call["preflight_corpus"])
    assert _content_safe_preflight_shape(
        api_call["preflight_corpus"]
    ) == _content_safe_preflight_shape(worker_call["preflight_corpus"])


def test_worker_strict_block_metadata_is_leakage_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Worker strict block metadata should stay safe and secret-free."""
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "1")
    secret = "postgresql://user:secret@localhost:5432/idis"

    conn = MagicMock()
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn
    runs_repo = _RunsRepoStub(
        queued=[
            {
                "run_id": "run-full-3",
                "tenant_id": TENANT_A,
                "deal_id": "deal-3",
                "mode": "FULL",
                "source": {"type": "deal_documents", "document_ids": ["doc-3"]},
            }
        ],
        complete_calls=[],
    )
    run_steps_repo = InMemoryRunStepsRepository(TENANT_A)
    execution_service = MagicMock(
        audit_sink=InMemoryAuditSink(),
        execute=MagicMock(
            return_value=RunExecutionResult(
                claimed=True,
                status="SUCCEEDED",
                steps=[],
            )
        ),
    )
    worker = PipelineWorker(
        poll_interval=0,
        tenant_ids=[TENANT_A],
        execution_service_factory=lambda **_kwargs: execution_service,
        run_context_factory=lambda **_kwargs: MagicMock(),
    )

    with (
        patch("idis.pipeline.worker.get_app_engine", return_value=engine),
        patch("idis.pipeline.worker.get_runs_repository", return_value=runs_repo),
        patch("idis.pipeline.worker.get_run_steps_repository", return_value=run_steps_repo),
        patch(
            "idis.pipeline.worker._load_worker_preflight_corpus",
            return_value=[_preflight_doc(deal_id="deal-3")],
        ),
        patch("idis.pipeline.worker.set_tenant_local", create=True),
    ):
        asyncio.run(worker._process_queued_runs())

    assert execution_service.execute.call_count == 0
    assert any(status == "FAILED" for _run_id, status, _finished in runs_repo.complete_calls)
    steps: list[RunStep] = run_steps_repo.get_by_run_id("run-full-3")
    assert steps
    assert steps[-1].error_code == "STRICT_FULL_LIVE_BLOCKED"
    encoded_step = json.dumps(steps[-1].model_dump(mode="json"), sort_keys=True)
    assert secret not in encoded_step


def test_worker_missing_selected_document_fails_closed_with_invalid_run_source_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing selected docs must fail closed as INVALID_RUN_SOURCE before execution."""
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "1")

    conn = MagicMock()
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn
    runs_repo = _RunsRepoStub(
        queued=[
            {
                "run_id": "run-full-missing-source",
                "tenant_id": TENANT_A,
                "deal_id": "deal-missing-source",
                "mode": "FULL",
                "source": {"type": "deal_documents", "document_ids": ["missing-doc"]},
            }
        ],
        complete_calls=[],
    )
    run_steps_repo = InMemoryRunStepsRepository(TENANT_A)
    execution_service = MagicMock(
        audit_sink=InMemoryAuditSink(),
        execute=MagicMock(
            return_value=RunExecutionResult(
                claimed=True,
                status="SUCCEEDED",
                steps=[],
            )
        ),
    )
    worker = PipelineWorker(
        poll_interval=0,
        tenant_ids=[TENANT_A],
        execution_service_factory=lambda **_kwargs: execution_service,
        run_context_factory=lambda **_kwargs: MagicMock(),
    )
    strict_helper = MagicMock(return_value=_BlockingStrictReport())

    with (
        patch("idis.pipeline.worker.get_app_engine", return_value=engine),
        patch("idis.pipeline.worker.get_runs_repository", return_value=runs_repo),
        patch("idis.pipeline.worker.get_run_steps_repository", return_value=run_steps_repo),
        patch(
            "idis.pipeline.worker.build_strict_full_live_admission_report",
            strict_helper,
        ),
        patch("idis.pipeline.worker.set_tenant_local", create=True),
    ):
        asyncio.run(worker._process_queued_runs())

    assert execution_service.execute.call_count == 0
    assert strict_helper.call_count == 0
    assert any(status == "FAILED" for _run_id, status, _finished in runs_repo.complete_calls)
    steps = run_steps_repo.get_by_run_id("run-full-missing-source")
    assert steps
    assert steps[-1].error_code == "INVALID_RUN_SOURCE"
    encoded_step = json.dumps(steps[-1].model_dump(mode="json"), sort_keys=True)
    assert "raw_text" not in encoded_step
    assert "object_key" not in encoded_step
    assert "postgresql://" not in encoded_step.lower()
    assert "missing-doc" not in encoded_step


def test_api_and_worker_full_paths_produce_same_strict_block_ledger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """API and worker FULL should align on strict blocked outcomes for same docs."""
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "1")
    monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(_api_keys()))

    app = create_app(audit_sink=InMemoryAuditSink(), service_region="me-south-1")
    app.state.deal_documents = {}
    client = TestClient(app)

    create_resp = client.post(
        "/v1/deals",
        json={"name": "Slice75A Parity", "company_name": "ParityCo"},
        headers={"X-IDIS-API-Key": API_KEY},
    )
    assert create_resp.status_code == 201
    deal_id = create_resp.json()["deal_id"]
    seed_deal_access(TENANT_A, deal_id, "actor-a")
    app.state.deal_documents[deal_id] = [_preflight_doc(deal_id=deal_id)]

    api_resp = client.post(
        f"/v1/deals/{deal_id}/runs",
        json={"mode": "FULL"},
        headers={"X-IDIS-API-Key": API_KEY},
    )
    assert api_resp.status_code == 409
    assert api_resp.json()["code"] == "STRICT_FULL_LIVE_BLOCKED"

    conn = MagicMock()
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn
    runs_repo = _RunsRepoStub(
        queued=[
            {
                "run_id": "run-full-4",
                "tenant_id": TENANT_A,
                "deal_id": deal_id,
                "mode": "FULL",
                "source": {"type": "deal_documents", "document_ids": ["doc-strict"]},
            }
        ],
        complete_calls=[],
    )
    run_steps_repo = InMemoryRunStepsRepository(TENANT_A)
    execution_service = MagicMock(
        audit_sink=InMemoryAuditSink(),
        execute=MagicMock(
            return_value=RunExecutionResult(
                claimed=True,
                status="SUCCEEDED",
                steps=[],
            )
        ),
    )
    worker = PipelineWorker(
        poll_interval=0,
        tenant_ids=[TENANT_A],
        execution_service_factory=lambda **_kwargs: execution_service,
        run_context_factory=lambda **_kwargs: MagicMock(),
    )

    with (
        patch("idis.pipeline.worker.get_app_engine", return_value=engine),
        patch("idis.pipeline.worker.get_runs_repository", return_value=runs_repo),
        patch("idis.pipeline.worker.get_run_steps_repository", return_value=run_steps_repo),
        patch(
            "idis.pipeline.worker._load_worker_preflight_corpus",
            return_value=[_preflight_doc(deal_id=deal_id)],
        ),
        patch("idis.pipeline.worker.set_tenant_local", create=True),
    ):
        asyncio.run(worker._process_queued_runs())

    assert execution_service.execute.call_count == 0
    assert any(status == "FAILED" for _run_id, status, _finished in runs_repo.complete_calls)
    steps: list[RunStep] = run_steps_repo.get_by_run_id("run-full-4")
    assert steps
    assert steps[-1].error_code == "STRICT_FULL_LIVE_BLOCKED"


def test_worker_respects_tenant_scope_and_does_not_claim_other_tenant() -> None:
    """Worker should only poll configured tenants and never claim other tenants' rows."""
    conn = MagicMock()
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn

    repo_a = _RunsRepoStub(queued=[], complete_calls=[])
    repo_b = _RunsRepoStub(
        queued=[{"run_id": "run-b", "tenant_id": TENANT_B, "deal_id": "deal-b", "mode": "FULL"}],
        complete_calls=[],
    )

    def select_repo(_conn: Any, tenant_id: str) -> _RunsRepoStub:
        return repo_a if tenant_id == TENANT_A else repo_b

    worker = PipelineWorker(
        poll_interval=0,
        tenant_ids=[TENANT_A],
        execution_service_factory=lambda **_kwargs: MagicMock(
            audit_sink=InMemoryAuditSink(),
            execute=MagicMock(),
        ),
        run_context_factory=lambda **_kwargs: MagicMock(),
    )
    with (
        patch("idis.pipeline.worker.get_app_engine", return_value=engine),
        patch("idis.pipeline.worker.get_runs_repository", side_effect=select_repo),
        patch("idis.pipeline.worker.set_tenant_local", create=True),
    ):
        asyncio.run(worker._process_queued_runs())

    assert repo_a.claim_calls == 1
    assert repo_b.claim_calls == 0


def test_api_queue_race_returns_deterministic_run_already_claimed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """API contract: synchronous claim owner returns RUN_ALREADY_CLAIMED on race loss."""
    monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(_api_keys()))
    app = create_app(audit_sink=InMemoryAuditSink(), service_region="me-south-1")
    app.state.deal_documents = {}
    client = TestClient(app)

    create_resp = client.post(
        "/v1/deals",
        json={"name": "Slice75A Race", "company_name": "RaceCo"},
        headers={"X-IDIS-API-Key": API_KEY},
    )
    assert create_resp.status_code == 201
    deal_id = create_resp.json()["deal_id"]
    seed_deal_access(TENANT_A, deal_id, "actor-a")
    app.state.deal_documents[deal_id] = [_preflight_doc(deal_id=deal_id)]

    with patch(
        "idis.services.runs.execution.RunExecutionService.execute",
        return_value=RunExecutionResult(claimed=False, status="NOT_CLAIMED"),
    ):
        run_resp = client.post(
            f"/v1/deals/{deal_id}/runs",
            json={"mode": "FULL"},
            headers={"X-IDIS-API-Key": API_KEY},
        )

    assert run_resp.status_code == 409
    body = run_resp.json()
    assert body["code"] == "RUN_ALREADY_CLAIMED"


def test_worker_uses_durable_audit_sink_when_db_configured_or_explicit_blocker() -> None:
    """Worker default service should not silently use in-memory audit in DB mode."""
    service = _default_execution_service_factory(
        db_conn=MagicMock(),
        tenant_id=TENANT_A,
    )
    assert not isinstance(service.audit_sink, InMemoryAuditSink)


def test_worker_default_audit_setup_fails_closed_when_durable_sink_unavailable() -> None:
    """Worker default service must not silently fall back to in-memory audit."""
    with (
        patch(
            "idis.audit.postgres_sink.PostgresAuditSink",
            side_effect=RuntimeError("postgres audit unavailable"),
        ),
        pytest.raises(RuntimeError, match="WORKER_AUDIT_SINK_UNAVAILABLE"),
    ):
        _default_execution_service_factory(
            db_conn=MagicMock(),
            tenant_id=TENANT_A,
        )


def test_worker_default_run_context_factory_uses_validated_preflight_source_path() -> None:
    """Worker context build must reuse selected-source fail-closed validation."""
    build_run_context = MagicMock(return_value=MagicMock())
    with (
        patch(
            "idis.services.runs.steps.load_document_preflight_corpus_for_deal",
            return_value=[_preflight_doc(deal_id="deal-context")],
        ),
        patch("idis.services.runs.steps.build_run_context", build_run_context),
        pytest.raises(InvalidRunSourceSelectionError),
    ):
        _default_run_context_factory(
            db_conn=MagicMock(),
            tenant_id=TENANT_A,
            run_data={
                "run_id": "run-context-missing-source",
                "deal_id": "deal-context",
                "mode": "FULL",
                "source": {"type": "deal_documents", "document_ids": ["missing-doc"]},
            },
            audit_sink=MagicMock(),
        )

    assert build_run_context.call_count == 0


def test_pipeline_executor_is_not_public_production_execution_path() -> None:
    """Legacy PipelineExecutor should not remain publicly exported for production use."""
    import idis.pipeline as pipeline_module

    assert "PipelineExecutor" not in getattr(pipeline_module, "__all__", [])
    assert not hasattr(pipeline_module, "PipelineExecutor")


def _api_keys() -> dict[str, dict[str, Any]]:
    return {
        API_KEY: {
            "tenant_id": TENANT_A,
            "actor_id": "actor-a",
            "name": "Tenant A",
            "timezone": "UTC",
            "data_region": "me-south-1",
            "roles": ["ANALYST"],
        }
    }


def _preflight_doc(*, deal_id: str) -> dict[str, Any]:
    return {
        "tenant_id": TENANT_A,
        "deal_id": deal_id,
        "document_id": "doc-strict",
        "doc_id": "artifact-doc-strict",
        "doc_type": "DOCX",
        "parse_status": "PARSED",
        "document_name": "doc-strict.docx",
        "sha256": "a" * 64,
        "uri": "deals/doc-strict.docx",
        "metadata": {},
        "source_metadata": {},
        "spans": [
            {
                "span_id": "span-strict",
                "tenant_id": TENANT_A,
                "deal_id": deal_id,
                "document_id": "doc-strict",
                "span_type": "PARAGRAPH",
                "locator": {"paragraph": 1},
                "text_excerpt": "Highly sensitive raw revenue sentence",
                "content_hash": "b" * 64,
            }
        ],
    }


def _selected_preflight_document_ids(preflight_corpus: Any) -> list[str]:
    corpus = preflight_corpus or []
    return sorted(str(doc.get("document_id", "")) for doc in corpus)


def _content_safe_preflight_shape(preflight_corpus: Any) -> list[dict[str, Any]]:
    corpus = preflight_corpus or []
    return [
        {
            "document_id": doc.get("document_id"),
            "doc_id": doc.get("doc_id"),
            "doc_type": doc.get("doc_type"),
            "parse_status": doc.get("parse_status"),
            "document_name": doc.get("document_name"),
            "span_ids": sorted(str(span.get("span_id", "")) for span in (doc.get("spans") or [])),
        }
        for doc in corpus
    ]
