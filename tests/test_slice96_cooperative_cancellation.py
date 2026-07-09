"""Slice96 Task 6 — cooperative mid-run cancellation (G7): consult cancel_requested_at.

RED-first. The orchestrator must consult ``cancel_requested_at`` at step boundaries and stop the
run BOUNDEDLY before the next expensive step, with a safe, deterministic ``RUN_CANCELLED`` ledger --
even if the run status has not (yet) flipped to CANCELLED. API and worker share this through the
single execution path (``RunExecutionService`` -> ``RunOrchestrator``). Retry/resume clears
``cancel_requested_at``, so a resumed run is never spuriously cancelled. PYTHONPATH pinned to src.
"""

from __future__ import annotations

import inspect
from collections.abc import Iterator
from typing import Any

import pytest

from idis.audit.sink import InMemoryAuditSink
from idis.models.run_step import RunStep, StepName, StepStatus
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

# Steps completed before EXTRACT so EXTRACT is the first live SNAPSHOT step and GRADE/CALC follow.
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
def _clean_store() -> Iterator[None]:
    clear_in_memory_runs_store()
    _run_steps_store.clear()  # module-global step ledger; isolate each test from cross-test state
    yield
    clear_in_memory_runs_store()
    _run_steps_store.clear()


def _steps_repo_pre_extract_completed() -> InMemoryRunStepsRepository:
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


def _recording_grade(calls: list[str]) -> Any:
    def grade(
        *,
        run_id: str,
        tenant_id: str,
        deal_id: str,
        created_claim_ids: list[str],
        audit_sink: Any,
    ) -> dict[str, Any]:
        calls.append(run_id)
        return {
            "graded_count": len(created_claim_ids),
            "failed_count": 0,
            "total_defects": 0,
            "all_failed": False,
        }

    return grade


def _recording_calc(calls: list[str]) -> Any:
    def calc(
        *,
        run_id: str,
        tenant_id: str,
        deal_id: str,
        created_claim_ids: list[str],
        calc_types: Any = None,
    ) -> dict[str, Any]:
        calls.append(run_id)
        return {"calc_ids": ["calc-001"], "reproducibility_hashes": ["hash-aaa"]}

    return calc


def _ctx(extract_fn: Any, grade_fn: Any, calc_fn: Any) -> RunContext:
    return RunContext(
        run_id=_RUN,
        tenant_id=_TENANT,
        deal_id=_DEAL,
        mode="SNAPSHOT",
        documents=_DOCUMENTS,
        extract_fn=extract_fn,
        grade_fn=grade_fn,
        calc_fn=calc_fn,
    )


def test_orchestrator_stops_before_next_step_when_cancel_requested_at_set() -> None:
    _in_memory_runs_store[_RUN] = {
        "run_id": _RUN,
        "tenant_id": _TENANT,
        "deal_id": _DEAL,
        "mode": "SNAPSHOT",
        "status": "RUNNING",
        "cancel_requested_at": None,
    }
    steps_repo = _steps_repo_pre_extract_completed()
    extract_calls: list[str] = []
    grade_calls: list[str] = []
    calc_calls: list[str] = []

    def cancelling_extract(
        *, run_id: str, tenant_id: str, deal_id: str, documents: list[dict[str, Any]]
    ) -> dict[str, Any]:
        extract_calls.append(run_id)
        # Cancellation is REQUESTED mid-run; status is NOT flipped (stays RUNNING). The orchestrator
        # must still stop at the next boundary by consulting cancel_requested_at.
        _in_memory_runs_store[run_id]["cancel_requested_at"] = "2026-05-27T00:01:00Z"
        return _extract_result()

    orchestrator = RunOrchestrator(audit_sink=InMemoryAuditSink(), run_steps_repo=steps_repo)
    result = orchestrator.execute(
        _ctx(cancelling_extract, _recording_grade(grade_calls), _recording_calc(calc_calls))
    )

    assert extract_calls == [_RUN]  # EXTRACT dispatched once
    assert grade_calls == []  # stopped BEFORE the next expensive step (GRADE)
    assert calc_calls == []  # ...and CALC
    # safe, stable, deterministic cancellation ledger:
    assert result.status == "CANCELLED"
    assert result.block_reason == "RUN_CANCELLED"
    assert result.error_code == "RUN_CANCELLED"
    assert result.error_message == "Run cancelled by lifecycle request"  # static, no private text
    ledger = {s.step_name for s in result.steps}
    assert StepName.EXTRACT in ledger  # completed up to the cancel point
    assert StepName.GRADE not in ledger and StepName.CALC not in ledger  # nothing past the boundary


def test_no_cancellation_when_cancel_not_requested() -> None:
    _in_memory_runs_store[_RUN] = {
        "run_id": _RUN,
        "tenant_id": _TENANT,
        "deal_id": _DEAL,
        "mode": "SNAPSHOT",
        "status": "RUNNING",
        "cancel_requested_at": None,  # no cancellation requested
    }
    steps_repo = _steps_repo_pre_extract_completed()
    extract_calls: list[str] = []
    grade_calls: list[str] = []
    calc_calls: list[str] = []

    def extract(
        *, run_id: str, tenant_id: str, deal_id: str, documents: list[dict[str, Any]]
    ) -> dict[str, Any]:
        extract_calls.append(run_id)
        return _extract_result()

    orchestrator = RunOrchestrator(audit_sink=InMemoryAuditSink(), run_steps_repo=steps_repo)
    result = orchestrator.execute(
        _ctx(extract, _recording_grade(grade_calls), _recording_calc(calc_calls))
    )

    assert extract_calls == [_RUN] and grade_calls == [_RUN] and calc_calls == [_RUN]  # all ran
    assert result.status != "CANCELLED"  # completed normally, not cancelled
    assert result.block_reason != "RUN_CANCELLED"


def test_execution_service_shared_path_returns_cancelled(monkeypatch: pytest.MonkeyPatch) -> None:
    # The single shared path (RunExecutionService.execute, used by BOTH API and worker) surfaces a
    # CANCELLED result when cancellation is requested mid-run.
    runs_repo = InMemoryRunsRepository(_TENANT)
    runs_repo.create(run_id=_RUN, deal_id=_DEAL, mode="SNAPSHOT")  # QUEUED -> claimable
    steps_repo = _steps_repo_pre_extract_completed()
    grade_calls: list[str] = []
    calc_calls: list[str] = []

    def cancelling_extract(
        *, run_id: str, tenant_id: str, deal_id: str, documents: list[dict[str, Any]]
    ) -> dict[str, Any]:
        _in_memory_runs_store[run_id]["cancel_requested_at"] = "2026-05-27T00:01:00Z"
        return _extract_result()

    service = RunExecutionService(
        audit_sink=InMemoryAuditSink(), runs_repo=runs_repo, run_steps_repo=steps_repo
    )
    result = service.execute(
        _ctx(cancelling_extract, _recording_grade(grade_calls), _recording_calc(calc_calls))
    )

    assert result.claimed is True
    assert result.status == "CANCELLED"
    assert grade_calls == [] and calc_calls == []  # stopped before the next expensive step
    assert _in_memory_runs_store[_RUN]["status"] == "CANCELLED"  # run finalized as CANCELLED


def test_api_and_worker_share_the_cancellation_execution_path() -> None:
    # Parity: both the API route path and the worker execute runs through the single
    # RunExecutionService, whose execute() wires the runs repo into RunOrchestrator (the source of
    # the cancellation signal) -- so cancellation behavior is identical for both.
    from idis.api.routes import runs as api_runs
    from idis.pipeline import worker as worker_mod
    from idis.services.runs import execution as execution_mod

    assert "RunExecutionService" in inspect.getsource(api_runs)
    assert "RunExecutionService" in inspect.getsource(worker_mod)
    exec_src = inspect.getsource(execution_mod.RunExecutionService.execute)
    assert "RunOrchestrator(" in exec_src and "runs_repo=" in exec_src
