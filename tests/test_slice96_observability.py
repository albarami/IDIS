"""Slice96 Task 7 — safe-shape observability for runtime controls (G5).

RED-first. Adds/reuses safe observability signals (IDs / counts / stable codes only) for: run
claim, mid-run cancel stop, rate-limit denials, provider-budget denials, idempotency cleanup, and
queued-run count. Every signal is asserted to exist AND to be safe-shape (no prompts, claim text,
transcripts, provider payloads, secrets, env values, or paths). Signals are best-effort and reuse
the existing audit sink. PYTHONPATH pinned to this worktree's src.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from idis.audit.sink import InMemoryAuditSink
from idis.models.run_step import RunStep, StepName, StepStatus
from idis.observability.runtime_signals import (
    IDEMPOTENCY_CLEANUP,
    RATE_LIMIT_DENIED,
    RUN_CANCELLED,
    RUN_CLAIMED,
    RUN_QUEUE_OBSERVED,
    emit_run_signal,
)
from idis.persistence.repositories.run_steps import InMemoryRunStepsRepository, _run_steps_store
from idis.persistence.repositories.runs import (
    InMemoryRunsRepository,
    _in_memory_runs_store,
    clear_in_memory_runs_store,
)
from idis.services.runs.execution import RunExecutionService
from idis.services.runs.orchestrator import RunContext, RunOrchestrator

_TENANT = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_DEAL = "dddddddd-dddd-dddd-dddd-dddddddddddd"
_RUN = "99999999-9999-9999-9999-999999999999"

_FORBIDDEN_SUBSTRINGS = (
    "prompt",
    "secret",
    "api_key",
    "apikey",
    "token",
    "password",
    "private_key",
    "bearer",
    "sk-",
    "transcript",
    "claim_text",
)


def _assert_safe_shape(event: dict[str, Any]) -> None:
    """Assert an observability event carries IDs/counts/codes only -- no private text or paths."""
    blob = json.dumps(event).lower()
    for bad in _FORBIDDEN_SUBSTRINGS:
        assert bad not in blob, f"observability event leaked forbidden token {bad!r}: {event}"
    safe = event.get("payload", {}).get("safe", {})
    assert isinstance(safe, dict)
    for key, value in safe.items():
        assert isinstance(key, str)
        assert isinstance(value, (str, int, float, bool, type(None))), f"non-scalar {key}={value!r}"
        if isinstance(value, str):
            assert len(value) <= 128  # IDs / codes only, never free text
            assert "/" not in value and "\\" not in value  # no paths


@pytest.fixture(autouse=True)
def _clean_stores() -> Iterator[None]:
    clear_in_memory_runs_store()
    _run_steps_store.clear()
    yield
    clear_in_memory_runs_store()
    _run_steps_store.clear()


# --- the reusable helper ---


def test_emit_run_signal_shape_and_safe() -> None:
    sink = InMemoryAuditSink()
    emit_run_signal(
        sink,
        event_type=RUN_CLAIMED,
        tenant_id=_TENANT,
        details={"run_id": _RUN, "mode": "SNAPSHOT"},
    )
    assert len(sink.events) == 1
    event = sink.events[0]
    assert event["event_type"] == "run.claimed"
    assert event["tenant_id"] == _TENANT
    assert event["payload"]["safe"] == {"run_id": _RUN, "mode": "SNAPSHOT"}
    _assert_safe_shape(event)


def test_emit_run_signal_none_sink_is_noop() -> None:
    emit_run_signal(None, event_type=RUN_CLAIMED, tenant_id=_TENANT, details={"run_id": _RUN})


def test_emit_run_signal_is_best_effort_on_failure() -> None:
    class _BoomSink:
        def emit(self, event: dict[str, Any]) -> None:
            raise RuntimeError("sink down")

    emit_run_signal(
        _BoomSink(), event_type=RUN_CLAIMED, tenant_id=_TENANT, details={"run_id": _RUN}
    )


# --- run lifecycle harness (mirrors the Slice75b/Task6 pattern) ---

_PRE_EXTRACT_STEPS = [
    StepName.DATA_ROOM_INVENTORY_PACKAGE,
    StepName.DATA_ROOM_INGESTION_HANDOFF,
    StepName.INGEST_CHECK,
    StepName.DOCUMENT_PREFLIGHT,
    StepName.METHODOLOGY_COVERAGE_INIT,
]
_DOCUMENTS = [
    {
        "document_id": "doc-001",
        "doc_type": "PDF",
        "document_name": "test.pdf",
        "spans": [
            {
                "span_id": "span-001",
                "text_excerpt": "Revenue was $5M.",
                "locator": {"page": 1},
                "span_type": "PAGE_TEXT",
            }
        ],
    }
]


def _steps_repo() -> InMemoryRunStepsRepository:
    repo = InMemoryRunStepsRepository(_TENANT)
    for order, step_name in enumerate(_PRE_EXTRACT_STEPS):
        repo.create(
            RunStep(
                step_id=f"00000000-0000-0000-0000-0000000000{order:02d}",
                run_id=_RUN,
                tenant_id=_TENANT,
                step_name=step_name,
                step_order=order,
                status=StepStatus.COMPLETED,
                started_at="2026-05-27T00:00:00Z",
                finished_at="2026-05-27T00:00:01Z",
                result_summary={},
            )
        )
    return repo


def _extract_result() -> dict[str, Any]:
    return {
        "status": "COMPLETED",
        "created_claim_ids": ["claim-001"],
        "chunk_count": 1,
        "unique_claim_count": 1,
        "conflict_count": 0,
    }


def _grade(**_: Any) -> dict[str, Any]:
    return {"graded_count": 1, "failed_count": 0, "total_defects": 0, "all_failed": False}


def _calc(**_: Any) -> dict[str, Any]:
    return {"calc_ids": ["calc-001"], "reproducibility_hashes": ["hash-aaa"]}


def _ctx(extract_fn: Any) -> RunContext:
    return RunContext(
        run_id=_RUN,
        tenant_id=_TENANT,
        deal_id=_DEAL,
        mode="SNAPSHOT",
        documents=_DOCUMENTS,
        extract_fn=extract_fn,
        grade_fn=_grade,
        calc_fn=_calc,
    )


def _events_of(sink: InMemoryAuditSink, event_type: str) -> list[dict[str, Any]]:
    return [e for e in sink.events if e.get("event_type") == event_type]


def test_run_claim_emits_safe_signal_on_shared_execution_path() -> None:
    sink = InMemoryAuditSink()
    runs_repo = InMemoryRunsRepository(_TENANT)
    runs_repo.create(run_id=_RUN, deal_id=_DEAL, mode="SNAPSHOT")  # QUEUED

    def extract(**_: Any) -> dict[str, Any]:
        return _extract_result()

    service = RunExecutionService(
        audit_sink=sink, runs_repo=runs_repo, run_steps_repo=_steps_repo()
    )
    result = service.execute(_ctx(extract))

    assert result.claimed is True
    claimed = _events_of(sink, RUN_CLAIMED)
    assert len(claimed) == 1  # claim observed once on the shared path (API + worker)
    assert claimed[0]["payload"]["safe"]["run_id"] == _RUN
    _assert_safe_shape(claimed[0])


def test_mid_run_cancel_emits_safe_signal() -> None:
    _in_memory_runs_store[_RUN] = {
        "run_id": _RUN,
        "tenant_id": _TENANT,
        "deal_id": _DEAL,
        "mode": "SNAPSHOT",
        "status": "RUNNING",
        "cancel_requested_at": None,
    }
    sink = InMemoryAuditSink()

    def cancelling_extract(**_: Any) -> dict[str, Any]:
        _in_memory_runs_store[_RUN]["cancel_requested_at"] = "2026-05-27T00:01:00Z"
        return _extract_result()

    orchestrator = RunOrchestrator(audit_sink=sink, run_steps_repo=_steps_repo())
    result = orchestrator.execute(_ctx(cancelling_extract))

    assert result.status == "CANCELLED"
    cancelled = _events_of(sink, RUN_CANCELLED)
    assert len(cancelled) == 1  # the stop is observable
    assert cancelled[0]["payload"]["safe"]["code"] == "RUN_CANCELLED"
    _assert_safe_shape(cancelled[0])


# --- rate-limit denial ---


def test_rate_limit_denial_emits_safe_signal() -> None:
    from idis.api.middleware.rate_limit import RateLimitMiddleware

    denied_decision = SimpleNamespace(
        allowed=False,
        tier=SimpleNamespace(value="USER"),
        limit_rpm=600,
        retry_after_seconds=1,
        remaining_tokens=0,
    )
    limiter = SimpleNamespace(check=lambda tenant_id, tier: denied_decision)
    sink = InMemoryAuditSink()

    async def _noop_app(scope: Any, receive: Any, send: Any) -> None:  # pragma: no cover
        return None

    mw = RateLimitMiddleware(_noop_app, limiter=limiter)

    from starlette.requests import Request

    async def _receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def _call_next(_req: Request) -> Any:  # pragma: no cover - denied before call_next
        raise AssertionError("call_next must not run on a rate-limit denial")

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/deals",
        "query_string": b"",
        "headers": [],
        "app": SimpleNamespace(state=SimpleNamespace(audit_sink=sink)),
        "state": {
            "tenant_context": SimpleNamespace(
                tenant_id=_TENANT, actor_id="actor-1", roles=frozenset()
            ),
            "request_id": "req-1",
        },
    }
    import asyncio

    response = asyncio.run(mw.dispatch(Request(scope, _receive), _call_next))
    assert response.status_code == 429

    denials = _events_of(sink, RATE_LIMIT_DENIED)
    assert len(denials) == 1
    safe = denials[0]["payload"]["safe"]
    assert safe["code"] == "RATE_LIMIT_EXCEEDED" and safe["tier"] == "USER"
    _assert_safe_shape(denials[0])


# --- idempotency cleanup outcome ---


def test_idempotency_cleanup_emits_safe_signal() -> None:
    from idis.api.middleware.idempotency import IdempotencyMiddleware
    from idis.idempotency.store import IdempotencyRecord, ScopeKey, SqliteIdempotencyStore

    store = SqliteIdempotencyStore(in_memory=True)
    old = (datetime.now(UTC) - timedelta(days=60)).isoformat().replace("+00:00", "Z")
    store.put(
        ScopeKey(_TENANT, "actor-1", "POST", "startRun", "old-key"),
        IdempotencyRecord(
            payload_sha256="sha256:abc",
            status_code=202,
            media_type="application/json",
            body_bytes=b"{}",
            created_at=old,
        ),
    )
    sink = InMemoryAuditSink()

    async def _noop_app(scope: Any, receive: Any, send: Any) -> None:  # pragma: no cover
        return None

    mw = IdempotencyMiddleware(_noop_app, cleanup_interval_seconds=0.0)
    mw._maybe_cleanup(_TENANT, store, audit_sink=sink)

    cleaned = _events_of(sink, IDEMPOTENCY_CLEANUP)
    assert len(cleaned) == 1
    assert cleaned[0]["payload"]["safe"]["deleted_count"] == 1  # expired row reclaimed
    _assert_safe_shape(cleaned[0])


# --- queue depth / queued-run count ---


def test_count_queued_runs_and_queue_signal_is_safe() -> None:
    repo = InMemoryRunsRepository(_TENANT)
    repo.create(run_id=_RUN, deal_id=_DEAL, mode="SNAPSHOT")  # QUEUED
    repo.create(
        run_id="88888888-8888-8888-8888-888888888888",
        deal_id="cccccccc-cccc-cccc-cccc-cccccccccccc",
        mode="FULL",
    )  # QUEUED (different deal)
    assert repo.count_queued_runs() == 2

    sink = InMemoryAuditSink()
    emit_run_signal(
        sink,
        event_type=RUN_QUEUE_OBSERVED,
        tenant_id=_TENANT,
        details={"queued_count": repo.count_queued_runs()},
    )
    observed = _events_of(sink, RUN_QUEUE_OBSERVED)
    assert len(observed) == 1 and observed[0]["payload"]["safe"]["queued_count"] == 2
    _assert_safe_shape(observed[0])


def test_worker_poll_wires_queue_depth_signal() -> None:
    from idis.pipeline import worker as worker_mod

    src = inspect.getsource(worker_mod.PipelineWorker._process_queued_runs)
    assert "count_queued_runs" in src or "RUN_QUEUE_OBSERVED" in src or "run.queue.observed" in src
    assert "emit_run_signal" in src


# --- provider-budget denial (already observable via the failed-step audit) ---


def test_provider_budget_denial_is_audited_safely() -> None:
    from idis.providers.budget import ProviderBudgetExceededError

    sink = InMemoryAuditSink()
    orchestrator = RunOrchestrator(
        audit_sink=sink, run_steps_repo=InMemoryRunStepsRepository(_TENANT)
    )
    orchestrator._origin_deal_id = _DEAL
    step = RunStep(
        step_id="00000000-0000-0000-0000-0000000000ff",
        run_id=_RUN,
        tenant_id=_TENANT,
        step_name=StepName.EXTRACT,
        step_order=99,
        status=StepStatus.RUNNING,
        started_at="2026-05-27T00:00:00Z",
    )
    orchestrator._steps_repo.create(step)
    orchestrator._fail_step(
        step, ProviderBudgetExceededError(provider="anthropic", tenant_id=_TENANT, limit=1, used=1)
    )

    failed = [e for e in sink.events if str(e.get("event_type", "")).endswith(".failed")]
    assert len(failed) == 1
    assert failed[0]["payload"]["safe"]["error_code"] == "PROVIDER_BUDGET_EXCEEDED"
    _assert_safe_shape(failed[0])


def test_retry_resume_cancel_requests_are_mapped_to_audit_events() -> None:
    from idis.api.middleware.audit import OPERATION_ID_TO_EVENT_TYPE

    assert OPERATION_ID_TO_EVENT_TYPE["cancelRun"][0] == "deal.run.cancelled"
    assert OPERATION_ID_TO_EVENT_TYPE["retryRun"][0] == "deal.run.requeued"
    assert OPERATION_ID_TO_EVENT_TYPE["resumeRun"][0] == "deal.run.requeued"
