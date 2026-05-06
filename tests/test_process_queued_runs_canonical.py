"""Tests for manual queued-run processing safety."""

from __future__ import annotations

from pathlib import Path

from scripts import process_queued_runs


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

    import asyncio

    asyncio.run(process_queued_runs.process_all_queued())

    assert worker.calls == 2
