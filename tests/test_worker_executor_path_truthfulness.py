"""Truthfulness tests for background worker/executor path wiring."""

from __future__ import annotations

from pathlib import Path

from scripts.audit_full_system_wiring import collect_wiring_inventory

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_worker_path_is_parallel_to_api_run_path() -> None:
    """The worker path must be reported as PipelineExecutor-driven, not API-equivalent."""
    inventory = collect_wiring_inventory(REPO_ROOT)
    worker = inventory["worker_executor_path"]

    assert worker.status == "PARTIAL"
    assert any("PipelineWorker" in item for item in worker.evidence)
    assert any("PipelineExecutor" in item for item in worker.evidence)
    assert any("different execution engine" in item for item in worker.gaps)


def test_worker_executor_is_gdbs_oriented_and_not_full_orchestrator_wiring() -> None:
    """PipelineExecutor should be identified as demo/GDBS-oriented, not full run parity."""
    inventory = collect_wiring_inventory(REPO_ROOT)
    worker = inventory["worker_executor_path"]

    assert any("GDBS" in item for item in worker.evidence)
    assert any("RunOrchestrator" in item for item in worker.gaps)
    assert inventory["api_worker_path_comparison"].status == "PARTIAL"
