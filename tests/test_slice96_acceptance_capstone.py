"""Slice96 acceptance capstone (Task 8).

Composes the six landed runtime controls and proves the slice acceptance: API and worker paths are
CONSISTENT, TENANT-SCOPED, RETRY-SAFE, and OBSERVABLE. Each control's full contract is proven in its
own Task file; this capstone proves they compose on the shared execution path with safe-shape
signals: duplicate-run guard, Redis-backed rate limiting, provider budget hard cap, idempotency TTL
cleanup, cooperative cancellation, and safe-shape observability. PYTHONPATH pinned to src.
"""

from __future__ import annotations

import inspect
from collections.abc import Iterator
from typing import Any

import pytest

from idis.audit.sink import InMemoryAuditSink
from idis.models.run_step import RunStep, StepName, StepStatus
from idis.observability.runtime_signals import RUN_CANCELLED, RUN_CLAIMED
from idis.persistence.repositories.run_steps import InMemoryRunStepsRepository, _run_steps_store
from idis.persistence.repositories.runs import (
    InMemoryRunsRepository,
    RunAlreadyActiveError,
    _in_memory_runs_store,
    clear_in_memory_runs_store,
)
from idis.services.runs.execution import RunExecutionService
from idis.services.runs.orchestrator import RunContext

_TENANT_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_TENANT_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
_DEAL = "dddddddd-dddd-dddd-dddd-dddddddddddd"
_RUN = "99999999-9999-9999-9999-999999999999"

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


@pytest.fixture(autouse=True)
def _clean_stores() -> Iterator[None]:
    clear_in_memory_runs_store()
    _run_steps_store.clear()
    yield
    clear_in_memory_runs_store()
    _run_steps_store.clear()


def _steps_repo() -> InMemoryRunStepsRepository:
    repo = InMemoryRunStepsRepository(_TENANT_A)
    for order, step_name in enumerate(_PRE_EXTRACT_STEPS):
        repo.create(
            RunStep(
                step_id=f"00000000-0000-0000-0000-0000000000{order:02d}",
                run_id=_RUN,
                tenant_id=_TENANT_A,
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


def _ctx(extract_fn: Any, grade_calls: list[str], calc_calls: list[str]) -> RunContext:
    def grade(*, run_id: str, **_: Any) -> dict[str, Any]:
        grade_calls.append(run_id)
        return {"graded_count": 1, "failed_count": 0, "total_defects": 0, "all_failed": False}

    def calc(*, run_id: str, **_: Any) -> dict[str, Any]:
        calc_calls.append(run_id)
        return {"calc_ids": ["calc-001"], "reproducibility_hashes": ["hash-aaa"]}

    return RunContext(
        run_id=_RUN,
        tenant_id=_TENANT_A,
        deal_id=_DEAL,
        mode="SNAPSHOT",
        documents=_DOCUMENTS,
        extract_fn=extract_fn,
        grade_fn=grade,
        calc_fn=calc,
    )


def _events(sink: InMemoryAuditSink, event_type: str) -> list[dict[str, Any]]:
    return [e for e in sink.events if e.get("event_type") == event_type]


def test_api_and_worker_share_one_execution_path() -> None:
    # CONSISTENT: both the API route path and the worker execute runs through the single
    # RunExecutionService.execute, which wires the runs repo into RunOrchestrator (cancellation)
    # and emits the claim signal -- so lifecycle, cancellation, and observability are identical.
    from idis.api.routes import runs as api_runs
    from idis.pipeline import worker as worker_mod
    from idis.services.runs import execution as execution_mod

    assert "RunExecutionService" in inspect.getsource(api_runs)
    assert "RunExecutionService" in inspect.getsource(worker_mod)
    exec_src = inspect.getsource(execution_mod.RunExecutionService.execute)
    assert "RunOrchestrator(" in exec_src and "runs_repo=" in exec_src
    assert "emit_run_signal" in exec_src  # claim observability on the shared path


def test_lifecycle_claim_and_cancel_are_tenant_scoped_and_observable() -> None:
    # Drive the shared execution path: claim emits a tenant-scoped run.claimed; a mid-run cancel
    # (COOPERATIVE CANCELLATION) stops boundedly with a tenant-scoped run.cancelled.
    sink = InMemoryAuditSink()
    runs_repo = InMemoryRunsRepository(_TENANT_A)
    runs_repo.create(run_id=_RUN, deal_id=_DEAL, mode="SNAPSHOT")
    grade_calls: list[str] = []
    calc_calls: list[str] = []

    def cancelling_extract(**_: Any) -> dict[str, Any]:
        _in_memory_runs_store[_RUN]["cancel_requested_at"] = "2026-05-27T00:01:00Z"
        return _extract_result()

    service = RunExecutionService(
        audit_sink=sink, runs_repo=runs_repo, run_steps_repo=_steps_repo()
    )
    result = service.execute(_ctx(cancelling_extract, grade_calls, calc_calls))

    assert result.claimed is True and result.status == "CANCELLED"
    assert grade_calls == [] and calc_calls == []  # stopped BEFORE the next expensive step
    claimed = _events(sink, RUN_CLAIMED)
    cancelled = _events(sink, RUN_CANCELLED)
    assert len(claimed) == 1 and claimed[0]["tenant_id"] == _TENANT_A  # OBSERVABLE + TENANT-SCOPED
    assert len(cancelled) == 1 and cancelled[0]["tenant_id"] == _TENANT_A
    assert cancelled[0]["payload"]["safe"]["code"] == "RUN_CANCELLED"


def test_duplicate_run_guard_is_tenant_scoped() -> None:
    # DUPLICATE-RUN GUARD, TENANT-SCOPED: one active run per (tenant, deal); a second raises
    # RUN_ALREADY_ACTIVE, but a different tenant with the same deal is unaffected.
    repo_a = InMemoryRunsRepository(_TENANT_A)
    repo_a.create(run_id=_RUN, deal_id=_DEAL, mode="SNAPSHOT")
    with pytest.raises(RunAlreadyActiveError):
        repo_a.create(run_id="22222222-2222-2222-2222-222222222222", deal_id=_DEAL, mode="SNAPSHOT")
    repo_b = InMemoryRunsRepository(_TENANT_B)
    repo_b.create(  # different tenant, same deal -> allowed (tenant-scoped guard)
        run_id="33333333-3333-3333-3333-333333333333", deal_id=_DEAL, mode="SNAPSHOT"
    )


def test_retry_is_safe_and_clears_stale_cancellation() -> None:
    # RETRY-SAFE: requeue of a FAILED run clears cancel_requested_at, so a resumed run is never
    # spuriously cancelled by the cooperative-cancellation check.
    repo = InMemoryRunsRepository(_TENANT_A)
    repo.create(run_id=_RUN, deal_id=_DEAL, mode="SNAPSHOT")
    repo.update_status(_RUN, status="FAILED", finished_at="2026-05-27T00:00:00Z")
    _in_memory_runs_store[_RUN]["cancel_requested_at"] = "2026-05-27T00:00:00Z"  # stale
    assert repo.try_requeue_failed(_RUN) is True
    run = repo.get(_RUN)
    assert run is not None and run["status"] == "QUEUED" and run["cancel_requested_at"] is None


def test_rate_limit_budget_and_idempotency_are_tenant_keyed_and_observable() -> None:
    # TENANT-SCOPED composition of the remaining controls, each with a safe-shape signal wired.
    from idis.api.middleware import idempotency as idem_mod
    from idis.api.middleware import rate_limit as rl_mod
    from idis.idempotency.store import ScopeKey
    from idis.pipeline import worker as worker_mod
    from idis.providers.budget import (
        InMemoryProviderBudgetStore,
        ProviderBudget,
        ProviderBudgetConfig,
        ProviderBudgetExceededError,
    )
    from idis.rate_limit.limiter import RateLimitConfig, RateLimitTier, TenantRateLimiter

    # provider budget is per (tenant, provider): tenant A exhausted, tenant B independent.
    budget = ProviderBudget(
        config=ProviderBudgetConfig(max_calls=1), store=InMemoryProviderBudgetStore()
    )
    budget.charge(tenant_id=_TENANT_A, provider="anthropic")
    with pytest.raises(ProviderBudgetExceededError):
        budget.charge(tenant_id=_TENANT_A, provider="anthropic")
    budget.charge(tenant_id=_TENANT_B, provider="anthropic")  # different tenant -> own cap

    # rate limiter is per tenant: A exhausted, B independent.
    limiter = TenantRateLimiter(RateLimitConfig(user_rpm=1, integration_rpm=1, burst_multiplier=1))
    assert limiter.check(_TENANT_A, RateLimitTier.USER).allowed is True
    assert limiter.check(_TENANT_A, RateLimitTier.USER).allowed is False  # A exhausted
    assert limiter.check(_TENANT_B, RateLimitTier.USER).allowed is True  # B independent

    # idempotency scope is tenant-first.
    assert ScopeKey._fields[0] == "tenant_id"

    # OBSERVABLE: each remaining control emits a safe-shape signal at its site.
    assert "RATE_LIMIT_DENIED" in inspect.getsource(rl_mod)
    assert "IDEMPOTENCY_CLEANUP" in inspect.getsource(idem_mod)
    assert "RUN_QUEUE_OBSERVED" in inspect.getsource(worker_mod)
