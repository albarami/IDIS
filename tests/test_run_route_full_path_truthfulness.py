"""Truthfulness tests for API FULL/SNAPSHOT run-path wiring."""

from __future__ import annotations

from pathlib import Path

from scripts.audit_full_system_wiring import collect_wiring_inventory

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_api_run_path_uses_run_orchestrator_not_worker_executor() -> None:
    """HTTP run route must be classified as RunOrchestrator-driven."""
    inventory = collect_wiring_inventory(REPO_ROOT)
    api_run = inventory["api_run_path"]

    assert api_run.status == "WIRED"
    assert any("RunOrchestrator" in item for item in api_run.evidence)
    assert any("asyncio.to_thread(orchestrator.execute" in item for item in api_run.evidence)


def test_calc_step_is_stubbed_until_calc_engine_is_invoked_and_persisted() -> None:
    """The current CALC step must not be reported as durable CalcEngine wiring."""
    inventory = collect_wiring_inventory(REPO_ROOT)
    calc_step = inventory["calc_step"]

    assert calc_step.status == "STUBBED"
    assert any("_run_snapshot_calc" in item for item in calc_step.evidence)
    assert any("returns empty calc_ids" in item for item in calc_step.gaps)
    assert any("CalcEngine" in item for item in calc_step.gaps)


def test_full_run_outputs_call_analysis_debate_scoring_deliverables_but_have_gaps() -> None:
    """FULL mode exists, but the baseline should keep it PARTIAL due known gaps."""
    inventory = collect_wiring_inventory(REPO_ROOT)
    full_steps = inventory["full_steps"]

    assert full_steps.status == "PARTIAL"
    assert any("ENRICHMENT" in item for item in full_steps.evidence)
    assert any("DEBATE" in item for item in full_steps.evidence)
    assert any("ANALYSIS" in item for item in full_steps.evidence)
    assert any("DELIVERABLES" in item for item in full_steps.evidence)
    assert any("InMemoryAuditSink" in item for item in full_steps.gaps)
