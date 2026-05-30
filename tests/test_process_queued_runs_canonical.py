"""Tests for manual queued-run processing safety."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from scripts import process_queued_runs

from idis.audit.sink import InMemoryAuditSink
from idis.pipeline.worker import PipelineWorker

TENANT_C = "cccccccc-cccc-cccc-cccc-cccccccccccc"


def test_manual_queued_run_script_delegates_to_canonical_worker() -> None:
    """Manual queued-run entry point must not bypass RunExecutionService."""
    script = Path("scripts/process_queued_runs.py").read_text(encoding="utf-8")

    assert "PipelineExecutor" not in script
    assert "PipelineWorker" in script


def test_manual_queued_run_script_drains_until_no_runs_remain(
    monkeypatch,
) -> None:
    """The manual processor should keep polling batches until the queue is drained."""

    class CountingWorker:
        def __init__(self, *, poll_interval: int, tenant_ids: list[str]) -> None:
            self.calls = 0

        async def _process_queued_runs(self) -> int:
            self.calls += 1
            return 1 if self.calls == 1 else 0

    worker = CountingWorker(poll_interval=0, tenant_ids=["tenant-1"])
    monkeypatch.setattr(process_queued_runs, "get_worker_tenant_ids", lambda: ["tenant-1"])
    monkeypatch.setattr(process_queued_runs, "PipelineWorker", lambda **kwargs: worker)

    asyncio.run(process_queued_runs.process_all_queued())

    assert worker.calls == 2


def test_manual_queued_run_script_remains_thin_pipeline_worker_delegate() -> None:
    """Slice75B regression: the script must not import orchestrator/executor directly."""
    script = Path("scripts/process_queued_runs.py").read_text(encoding="utf-8")

    assert "from idis.services.runs.orchestrator" not in script, (
        "process_queued_runs.py must not import RunOrchestrator directly"
    )
    assert "from idis.services.runs.execution" not in script, (
        "process_queued_runs.py must remain a PipelineWorker delegate, not import "
        "RunExecutionService directly"
    )
    assert "PipelineExecutor" not in script, (
        "process_queued_runs.py must not reference the quarantined PipelineExecutor"
    )
    assert "from idis.services.runs.lifecycle" not in script, (
        "process_queued_runs.py must not call RunLifecycleService directly; "
        "lifecycle transitions are an API/service-layer concern"
    )


def test_worker_excludes_cancelled_rows_and_runs_retried_queued_rows_via_execution_service() -> (
    None
):
    """Slice75B regression: real repo with mixed CANCELLED + retried QUEUED rows.

    Uses the real ``InMemoryRunsRepository`` and the real ``RunExecutionService`` so
    the assertions exercise the actual queue-filter and claim contracts, not stubs.
    ``RunOrchestrator`` is mocked at the ``execution`` module binding so the test
    does not actually run the pipeline. Proves the worker:
    - never passes CANCELLED rows to ``try_mark_running``
    - claims and executes retried QUEUED rows through ``RunExecutionService``
    - constructs ``RunOrchestrator`` only via ``RunExecutionService``
    """
    from idis.persistence.repositories.run_steps import InMemoryRunStepsRepository
    from idis.persistence.repositories.runs import (
        InMemoryRunsRepository,
        _in_memory_runs_store,
        clear_in_memory_runs_store,
    )
    from idis.services.runs.execution import RunExecutionService
    from idis.services.runs.orchestrator import OrchestratorResult

    clear_in_memory_runs_store()
    base_run = {
        "tenant_id": TENANT_C,
        "deal_id": "deal-retry",
        "mode": "SNAPSHOT",
        "started_at": "2026-05-27T00:00:00Z",
        "finished_at": None,
        "source": None,
        "created_at": "2026-05-27T00:00:00Z",
    }
    _in_memory_runs_store["cancelled-run-1"] = {
        **base_run,
        "run_id": "cancelled-run-1",
        "status": "CANCELLED",
    }
    _in_memory_runs_store["queued-retry-1"] = {
        **base_run,
        "run_id": "queued-retry-1",
        "status": "QUEUED",
    }

    real_repo = InMemoryRunsRepository(TENANT_C)
    original_mark_running = real_repo.try_mark_running
    mark_running_calls: list[str] = []

    def tracking_mark_running(run_id: str) -> bool:
        mark_running_calls.append(run_id)
        return original_mark_running(run_id)

    real_repo.try_mark_running = tracking_mark_running  # type: ignore[method-assign]

    real_steps_repo = InMemoryRunStepsRepository(TENANT_C)
    audit_sink = InMemoryAuditSink()

    def real_execution_service_factory(*, db_conn: Any, tenant_id: str) -> RunExecutionService:
        return RunExecutionService(
            audit_sink=audit_sink,
            runs_repo=real_repo,
            run_steps_repo=real_steps_repo,
        )

    def real_context_factory(
        *,
        db_conn: Any,
        tenant_id: str,
        run_data: dict[str, Any],
        audit_sink: Any,
    ) -> MagicMock:
        ctx = MagicMock()
        ctx.run_id = run_data["run_id"]
        ctx.tenant_id = tenant_id
        ctx.mode = run_data["mode"]
        return ctx

    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = MagicMock()

    worker = PipelineWorker(
        poll_interval=0,
        tenant_ids=[TENANT_C],
        execution_service_factory=real_execution_service_factory,
        run_context_factory=real_context_factory,
    )

    try:
        with (
            patch("idis.pipeline.worker.get_app_engine", return_value=engine),
            patch("idis.pipeline.worker.get_runs_repository", return_value=real_repo),
            patch("idis.pipeline.worker.set_tenant_local", create=True),
            patch("idis.services.runs.execution.RunOrchestrator") as orch_class_mock,
        ):
            orch_instance = MagicMock()
            orch_instance.execute.return_value = OrchestratorResult(status="SUCCEEDED", steps=[])
            orch_class_mock.return_value = orch_instance
            asyncio.run(worker._process_queued_runs())
    finally:
        clear_in_memory_runs_store()

    assert "cancelled-run-1" not in mark_running_calls, (
        "CANCELLED rows must never be passed to try_mark_running"
    )
    assert mark_running_calls == ["queued-retry-1"], (
        "exactly the retried QUEUED row should be claimed by the worker via RunExecutionService"
    )
    assert orch_class_mock.call_count == 1, (
        "RunExecutionService must instantiate RunOrchestrator exactly once for the retried run"
    )
    orch_run_ids = [
        call.args[0].run_id if call.args else call.kwargs.get("ctx").run_id
        for call in orch_instance.execute.call_args_list
    ]
    assert orch_run_ids == ["queued-retry-1"], (
        "RunOrchestrator.execute must be invoked only for the retried QUEUED run"
    )


def test_process_queued_runs_script_remains_only_a_pipeline_worker_delegate() -> None:
    """Slice75B regression: script source remains a thin PipelineWorker delegate."""
    script = Path("scripts/process_queued_runs.py").read_text(encoding="utf-8")

    assert "from idis.pipeline.worker" in script, (
        "process_queued_runs.py must keep delegating via PipelineWorker"
    )
    assert "from idis.services.runs.orchestrator" not in script
    assert "from idis.services.runs.execution" not in script
    assert "from idis.services.runs.lifecycle" not in script
    assert "PipelineExecutor" not in script
