"""Run orchestrator tests for Slice 11 Evidence Trust Court wiring."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from idis.audit.sink import InMemoryAuditSink
from idis.models.evidence_trust_court_materialization import RunScopedEvidenceTrustCourtShell
from idis.models.run_step import FULL_ONLY_STEPS, FULL_STEPS, SNAPSHOT_STEPS, StepName
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
from tests.test_run_methodology_evidence_trust_court_service import _truth_dashboard_record
from tests.test_run_methodology_truth_dashboard_service import _evidence_record
from tests.test_run_orchestrator_methodology_claim_materialization import _ctx


def setup_function() -> None:
    clear_run_steps_store()


def _ctx_with_court_inputs(run_id: str) -> Any:
    ctx = _ctx(run_id)
    claim = _claim("claim_mth_revenue", "claim_mth_revenue", "1000").model_copy(
        update={"run_id": run_id}
    )
    evidence = _evidence_record("claim_mth_revenue").model_copy(update={"run_id": run_id})
    ctx.methodology_materialized_claims = [claim]
    ctx.methodology_evidence_items = [evidence]
    ctx.methodology_evidence_source_provenance = [evidence.source_ref]
    ctx.methodology_sanads = [
        _sanad_record("claim_mth_revenue").model_copy(update={"run_id": run_id})
    ]
    ctx.methodology_sanad_grades = [
        _grade("claim_mth_revenue").model_copy(update={"run_id": run_id})
    ]
    ctx.methodology_sanad_defects = []
    ctx.methodology_calculations = []
    ctx.methodology_calc_sanads = []
    dashboard = _truth_dashboard_record(["claim_mth_revenue"]).model_copy(update={"run_id": run_id})
    ctx.methodology_truth_dashboard = dashboard
    return ctx


def test_full_step_order_places_evidence_trust_court_after_truth_dashboard_before_extract() -> None:
    assert StepName.METHODOLOGY_EVIDENCE_TRUST_COURT in FULL_STEPS
    assert StepName.METHODOLOGY_EVIDENCE_TRUST_COURT in FULL_ONLY_STEPS
    assert StepName.METHODOLOGY_EVIDENCE_TRUST_COURT not in SNAPSHOT_STEPS
    assert FULL_STEPS.index(StepName.METHODOLOGY_TRUTH_DASHBOARD) < FULL_STEPS.index(
        StepName.METHODOLOGY_EVIDENCE_TRUST_COURT
    )
    assert FULL_STEPS.index(StepName.METHODOLOGY_EVIDENCE_TRUST_COURT) < FULL_STEPS.index(
        StepName.EXTRACT
    )
    assert FULL_STEPS.index(StepName.DEBATE) > FULL_STEPS.index(
        StepName.METHODOLOGY_EVIDENCE_TRUST_COURT
    )


def test_evidence_trust_court_step_attaches_record_and_safe_summary() -> None:
    run_id = str(uuid.uuid4())
    ctx = _ctx_with_court_inputs(run_id)
    orchestrator = RunOrchestrator(
        audit_sink=InMemoryAuditSink(),
        run_steps_repo=InMemoryRunStepsRepository(TENANT_ID),
    )

    summary = orchestrator._execute_methodology_evidence_trust_court(ctx)  # noqa: SLF001

    assert ctx.methodology_evidence_trust_court is not None
    assert summary["summary"]["assessed_claim_count"] == 1
    assert summary["summary"]["by_disposition"] == {"trusted": 1}
    assert summary["claim_ids"] == ["claim_mth_revenue"]
    assert "role_summaries" in summary
    assert "AgentOutput" not in str(summary)
    assert "content" not in summary
    assert "claim_text" not in str(summary)


def test_missing_truth_dashboard_or_shell_only_context_blocks_evidence_trust_court() -> None:
    orchestrator = RunOrchestrator(
        audit_sink=InMemoryAuditSink(),
        run_steps_repo=InMemoryRunStepsRepository(TENANT_ID),
    )
    missing_dashboard_ctx = _ctx_with_court_inputs(str(uuid.uuid4()))
    missing_dashboard_ctx.methodology_truth_dashboard = None

    with pytest.raises(RunStepBlockedError) as missing_dashboard:
        orchestrator._execute_methodology_evidence_trust_court(missing_dashboard_ctx)  # noqa: SLF001

    shell_only_ctx = _ctx_with_court_inputs(str(uuid.uuid4()))
    shell_only_ctx.methodology_truth_dashboard = (
        shell_only_ctx.methodology_truth_dashboard.to_shell()
    )
    with pytest.raises(RunStepBlockedError) as shell_only:
        orchestrator._execute_methodology_evidence_trust_court(shell_only_ctx)  # noqa: SLF001

    assert missing_dashboard.value.code == "METHODOLOGY_TRUTH_DASHBOARD_MISSING"
    assert shell_only.value.code == "METHODOLOGY_EVIDENCE_TRUST_COURT_FAILED"
    assert shell_only.value.result_summary["court_ids"] == []
    assert shell_only_ctx.methodology_evidence_trust_court is None


def test_cross_run_evidence_trust_court_input_blocks_step_with_no_court_ids() -> None:
    run_id = str(uuid.uuid4())
    ctx = _ctx_with_court_inputs(run_id)
    ctx.methodology_evidence_items = [
        *ctx.methodology_evidence_items,
        _evidence_record("claim_mth_revenue").model_copy(
            update={"run_id": "44444444-4444-4444-4444-444444444444"}
        ),
    ]
    ctx.methodology_evidence_source_provenance = [
        evidence.source_ref for evidence in ctx.methodology_evidence_items
    ]
    orchestrator = RunOrchestrator(
        audit_sink=InMemoryAuditSink(),
        run_steps_repo=InMemoryRunStepsRepository(TENANT_ID),
    )

    with pytest.raises(RunStepBlockedError) as blocked:
        orchestrator._execute_methodology_evidence_trust_court(ctx)  # noqa: SLF001

    assert blocked.value.code == "METHODOLOGY_EVIDENCE_TRUST_COURT_FAILED"
    assert blocked.value.result_summary["status"] == "failed"
    assert blocked.value.result_summary["court_ids"] == []
    assert ctx.methodology_evidence_trust_court is None


def test_rehydrate_evidence_trust_court_uses_safe_shell_only() -> None:
    run_id = str(uuid.uuid4())
    ctx = _ctx_with_court_inputs(run_id)
    orchestrator = RunOrchestrator(
        audit_sink=InMemoryAuditSink(),
        run_steps_repo=InMemoryRunStepsRepository(TENANT_ID),
    )

    summary = orchestrator._execute_methodology_evidence_trust_court(ctx)  # noqa: SLF001
    ctx2 = _ctx(run_id)
    orchestrator._rehydrate_methodology_evidence_trust_court(ctx2, summary)  # noqa: SLF001

    assert isinstance(ctx2.methodology_evidence_trust_court, RunScopedEvidenceTrustCourtShell)
    assert ctx2.methodology_evidence_trust_court.claim_ids == ["claim_mth_revenue"]
