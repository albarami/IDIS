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
