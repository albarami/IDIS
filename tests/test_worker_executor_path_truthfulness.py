"""Truthfulness tests for background worker/executor path wiring."""

from __future__ import annotations

from pathlib import Path

from scripts.audit_full_system_wiring import collect_wiring_inventory

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_worker_path_uses_canonical_run_execution_service() -> None:
    """The worker path must use RunExecutionService, not PipelineExecutor."""
    inventory = collect_wiring_inventory(REPO_ROOT)
    worker = inventory["worker_executor_path"]

    assert worker.status == "WIRED"
    assert any("PipelineWorker" in item for item in worker.evidence)
    assert any("RunExecutionService" in item for item in worker.evidence)
    assert any("PipelineExecutor" in item for item in worker.evidence)
    assert not any("different execution engine" in item for item in worker.gaps)


def test_api_and_worker_share_canonical_execution_path() -> None:
    """API and worker should be classified as sharing the canonical path."""
    inventory = collect_wiring_inventory(REPO_ROOT)
    comparison = inventory["api_worker_path_comparison"]

    assert comparison.status == "WIRED"
    assert any("RunExecutionService" in item for item in comparison.evidence)
    assert any("RunOrchestrator" in item for item in comparison.evidence)
    assert comparison.gaps == []


def test_worker_and_api_share_persisted_document_corpus_loader() -> None:
    """Worker and API should report the same DB-backed document corpus loader."""
    inventory = collect_wiring_inventory(REPO_ROOT)
    loader = inventory["unified_run_document_loader"]
    split = inventory["api_worker_document_corpus_split"]

    assert loader.status == "PARTIAL"
    assert split.status == "PARTIAL"
    assert any("Pipeline worker context factory" in item for item in loader.evidence)
    assert any("Worker path hydrates" in item for item in split.evidence)


def test_worker_and_api_share_document_preflight_behavior() -> None:
    """Worker and API should both hydrate full corpus before DOCUMENT_PREFLIGHT."""
    inventory = collect_wiring_inventory(REPO_ROOT)
    preflight = inventory["document_preflight_run_integration"]

    assert preflight.status == "PARTIAL"
    assert any("API start-run loads full preflight corpus" in item for item in preflight.evidence)
    assert any(
        "worker context factory loads full preflight corpus" in item for item in preflight.evidence
    )


def test_worker_and_api_share_methodology_coverage_init_behavior() -> None:
    """Worker and API should both get coverage init through shared RunContext wiring."""
    inventory = collect_wiring_inventory(REPO_ROOT)
    coverage_init = inventory["methodology_coverage_init_run_integration"]

    assert coverage_init.status == "PARTIAL"
    assert any("build_run_context" in item for item in coverage_init.evidence)
    assert any("API and worker share" in item for item in coverage_init.evidence)


def test_worker_and_api_share_methodology_task_planning_behavior() -> None:
    """Worker and API should both get task planning through shared RunContext wiring."""
    inventory = collect_wiring_inventory(REPO_ROOT)
    task_planning = inventory["extraction_task_run_integration"]

    assert task_planning.status == "PARTIAL"
    assert any("METHODOLOGY_EXTRACTION_TASK_PLANNING" in item for item in task_planning.evidence)
    assert any("build_run_context" in item for item in task_planning.evidence)
    assert any("not executed" in item for item in task_planning.gaps)
