"""Run orchestrator tests for Slice 10 Truth Dashboard wiring."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from idis.audit.sink import InMemoryAuditSink
from idis.models.run_step import FULL_ONLY_STEPS, FULL_STEPS, SNAPSHOT_STEPS, StepName
from idis.models.truth_dashboard_materialization import RunScopedTruthDashboardShell
from idis.persistence.repositories.run_steps import (
    InMemoryRunStepsRepository,
    clear_run_steps_store,
)
from idis.services.runs.orchestrator import RunOrchestrator, RunStepBlockedError
from tests.test_run_methodology_claim_materialization_service import TENANT_ID
from tests.test_run_methodology_deterministic_calculation_service import (
    _claim,
    _grade,
    _sanad_record,
)
from tests.test_run_methodology_truth_dashboard_service import _evidence_record
from tests.test_run_orchestrator_methodology_claim_materialization import _ctx


def setup_function() -> None:
    clear_run_steps_store()


def _claim_for_run(claim_id: str, label: str, value: str, run_id: str) -> Any:
    return _claim(claim_id, label, value).model_copy(update={"run_id": run_id})


def _grade_for_run(claim_id: str, run_id: str) -> Any:
    return _grade(claim_id).model_copy(update={"run_id": run_id})


def _sanad_for_run(claim_id: str, run_id: str) -> Any:
    return _sanad_record(claim_id).model_copy(update={"run_id": run_id})


def _evidence_for_run(claim_id: str, run_id: str) -> Any:
    return _evidence_record(claim_id).model_copy(update={"run_id": run_id})


def _ctx_with_truth_inputs(run_id: str) -> Any:
    ctx = _ctx(run_id)
    claim = _claim_for_run("claim_mth_revenue", "claim_mth_revenue", "1000", run_id)
    evidence = _evidence_for_run("claim_mth_revenue", run_id)
    ctx.methodology_materialized_claims = [claim]
    ctx.methodology_evidence_items = [evidence]
    ctx.methodology_evidence_source_provenance = [evidence.source_ref]
    ctx.methodology_sanads = [_sanad_for_run("claim_mth_revenue", run_id)]
    ctx.methodology_sanad_grades = [_grade_for_run("claim_mth_revenue", run_id)]
    ctx.methodology_sanad_defects = []
    ctx.methodology_calculations = []
    ctx.methodology_calc_sanads = []
    return ctx


def test_full_step_order_places_truth_dashboard_after_calc_before_extract() -> None:
    assert StepName.METHODOLOGY_TRUTH_DASHBOARD in FULL_STEPS
    assert StepName.METHODOLOGY_TRUTH_DASHBOARD in FULL_ONLY_STEPS
    assert StepName.METHODOLOGY_TRUTH_DASHBOARD not in SNAPSHOT_STEPS
    assert FULL_STEPS.index(StepName.METHODOLOGY_DETERMINISTIC_CALCULATION) < FULL_STEPS.index(
        StepName.METHODOLOGY_TRUTH_DASHBOARD
    )
    assert FULL_STEPS.index(StepName.METHODOLOGY_TRUTH_DASHBOARD) < FULL_STEPS.index(
        StepName.EXTRACT
    )


def test_truth_dashboard_step_attaches_record_and_allows_empty_calc_context() -> None:
    run_id = str(uuid.uuid4())
    ctx = _ctx_with_truth_inputs(run_id)
    orchestrator = RunOrchestrator(
        audit_sink=InMemoryAuditSink(),
        run_steps_repo=InMemoryRunStepsRepository(TENANT_ID),
    )

    summary = orchestrator._execute_methodology_truth_dashboard(ctx)  # noqa: SLF001

    assert ctx.methodology_truth_dashboard is not None
    assert summary["summary"]["created_row_count"] == 1
    assert summary["summary"]["by_verdict"] == {"CONFIRMED": 1}
    assert summary["calc_ids"] == []
    assert "dashboard_mappings" not in summary


def test_missing_claim_evidence_sanad_or_grade_context_blocks_truth_dashboard() -> None:
    orchestrator = RunOrchestrator(
        audit_sink=InMemoryAuditSink(),
        run_steps_repo=InMemoryRunStepsRepository(TENANT_ID),
    )

    missing_claims_ctx = _ctx_with_truth_inputs(str(uuid.uuid4()))
    missing_claims_ctx.methodology_materialized_claims = None
    with pytest.raises(RunStepBlockedError) as missing_claims:
        orchestrator._execute_methodology_truth_dashboard(missing_claims_ctx)  # noqa: SLF001

    missing_evidence_ctx = _ctx_with_truth_inputs(str(uuid.uuid4()))
    missing_evidence_ctx.methodology_evidence_items = None
    with pytest.raises(RunStepBlockedError) as missing_evidence:
        orchestrator._execute_methodology_truth_dashboard(missing_evidence_ctx)  # noqa: SLF001

    missing_sanads_ctx = _ctx_with_truth_inputs(str(uuid.uuid4()))
    missing_sanads_ctx.methodology_sanads = []
    with pytest.raises(RunStepBlockedError) as missing_sanads:
        orchestrator._execute_methodology_truth_dashboard(missing_sanads_ctx)  # noqa: SLF001

    missing_grades_ctx = _ctx_with_truth_inputs(str(uuid.uuid4()))
    missing_grades_ctx.methodology_sanad_grades = None
    with pytest.raises(RunStepBlockedError) as missing_grades:
        orchestrator._execute_methodology_truth_dashboard(missing_grades_ctx)  # noqa: SLF001

    assert missing_claims.value.code == "METHODOLOGY_MATERIALIZED_CLAIMS_MISSING"
    assert missing_evidence.value.code == "METHODOLOGY_EVIDENCE_ITEMS_MISSING"
    assert missing_sanads.value.code == "METHODOLOGY_SANADS_MISSING"
    assert missing_grades.value.code == "METHODOLOGY_SANAD_GRADES_MISSING"


def test_cross_run_truth_dashboard_input_blocks_step_with_no_dashboard_ids() -> None:
    run_id = str(uuid.uuid4())
    ctx = _ctx_with_truth_inputs(run_id)
    ctx.methodology_evidence_items = [
        *ctx.methodology_evidence_items,
        _evidence_for_run("claim_mth_revenue", "44444444-4444-4444-4444-444444444444"),
    ]
    ctx.methodology_evidence_source_provenance = [
        evidence.source_ref for evidence in ctx.methodology_evidence_items
    ]
    orchestrator = RunOrchestrator(
        audit_sink=InMemoryAuditSink(),
        run_steps_repo=InMemoryRunStepsRepository(TENANT_ID),
    )

    with pytest.raises(RunStepBlockedError) as blocked:
        orchestrator._execute_methodology_truth_dashboard(ctx)  # noqa: SLF001

    assert blocked.value.code == "METHODOLOGY_TRUTH_DASHBOARD_FAILED"
    assert blocked.value.result_summary["status"] == "failed"
    assert blocked.value.result_summary["dashboard_ids"] == []
    assert ctx.methodology_truth_dashboard is None


def test_resume_skips_completed_truth_dashboard_step_and_rehydrates_safe_shell() -> None:
    run_id = str(uuid.uuid4())
    repo = InMemoryRunStepsRepository(TENANT_ID)
    orchestrator = RunOrchestrator(audit_sink=InMemoryAuditSink(), run_steps_repo=repo)
    ctx = _ctx_with_truth_inputs(run_id)

    first_summary = orchestrator._execute_methodology_truth_dashboard(ctx)  # noqa: SLF001
    step = orchestrator._start_step(ctx, StepName.METHODOLOGY_TRUTH_DASHBOARD, None)  # noqa: SLF001
    orchestrator._complete_step(step, first_summary)  # noqa: SLF001

    call_count = {"truth_dashboard": 0}

    def failing_truth_dashboard(**kwargs: Any) -> Any:
        call_count["truth_dashboard"] += 1
        raise AssertionError("completed Truth Dashboard step must not rerun")

    ctx2 = _ctx(run_id)
    ctx2.methodology_truth_dashboard_fn = failing_truth_dashboard
    result = orchestrator.execute(ctx2)

    assert result.status == "FAILED"
    assert result.error_code == "METHODOLOGY_EVIDENCE_TRUST_COURT_FAILED"
    assert call_count["truth_dashboard"] == 0
    assert isinstance(ctx2.methodology_truth_dashboard, RunScopedTruthDashboardShell)
    assert ctx2.methodology_evidence_trust_court is None
