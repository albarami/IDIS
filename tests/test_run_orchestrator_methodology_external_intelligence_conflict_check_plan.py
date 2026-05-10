"""Run orchestrator tests for Slice 13 external intelligence plan wiring."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from idis.audit.sink import InMemoryAuditSink
from idis.models.external_intelligence_conflict_check_plan_materialization import (
    RunScopedExternalIntelligenceConflictCheckPlanShell,
)
from idis.models.run_step import FULL_ONLY_STEPS, FULL_STEPS, SNAPSHOT_STEPS, StepName
from idis.persistence.repositories.run_steps import (
    InMemoryRunStepsRepository,
    clear_run_steps_store,
)
from idis.services.runs.orchestrator import RunOrchestrator, RunStepBlockedError
from tests.test_run_methodology_claim_materialization_service import TENANT_ID
from tests.test_run_methodology_external_intelligence_conflict_check_plan_service import (
    _ExplodingConnector,
    _service,
    _vep_record,
)
from tests.test_run_orchestrator_methodology_claim_materialization import _ctx


def setup_function() -> None:
    clear_run_steps_store()


def test_full_step_order_places_external_intelligence_plan_after_vep_before_extract() -> None:
    assert StepName.METHODOLOGY_EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN in FULL_STEPS
    assert StepName.METHODOLOGY_EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN in FULL_ONLY_STEPS
    assert StepName.METHODOLOGY_EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN not in SNAPSHOT_STEPS
    assert FULL_STEPS.index(StepName.METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE) < FULL_STEPS.index(
        StepName.METHODOLOGY_EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN
    )
    assert FULL_STEPS.index(
        StepName.METHODOLOGY_EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN
    ) < FULL_STEPS.index(StepName.EXTRACT)


def test_plan_step_attaches_record_and_safe_summary_without_live_calls() -> None:
    connector = _ExplodingConnector("sec_edgar")
    ctx = _ctx_with_vep(str(uuid.uuid4()))
    ctx.methodology_external_intelligence_conflict_check_plan_fn = _service(connector).run
    orchestrator = RunOrchestrator(
        audit_sink=InMemoryAuditSink(),
        run_steps_repo=InMemoryRunStepsRepository(TENANT_ID),
    )

    summary = orchestrator._execute_methodology_external_intelligence_conflict_check_plan(ctx)  # noqa: SLF001

    assert ctx.methodology_external_intelligence_conflict_check_plan is not None
    assert connector.fetch_count == 0
    assert summary["status"] == "completed"
    assert summary["plan_ids"]
    assert summary["summary"]["check_count"] == 1
    assert summary["summary"]["by_status"] == {"deferred": 1}
    assert "plan boundary" in str(summary)
    assert "external conflict checks executed" not in str(summary)
    assert "normalized" not in str(summary)
    assert "recommendation" not in str(summary)
    assert "GO" not in str(summary)


def test_missing_vep_blocks_external_intelligence_plan() -> None:
    ctx = _ctx(str(uuid.uuid4()))
    orchestrator = RunOrchestrator(
        audit_sink=InMemoryAuditSink(),
        run_steps_repo=InMemoryRunStepsRepository(TENANT_ID),
    )

    with pytest.raises(RunStepBlockedError) as missing_vep:
        orchestrator._execute_methodology_external_intelligence_conflict_check_plan(ctx)  # noqa: SLF001

    assert missing_vep.value.code == "METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE_MISSING"
    assert ctx.methodology_external_intelligence_conflict_check_plan is None


def test_prior_completed_empty_vep_summary_returns_completed_plan_noop() -> None:
    ctx = _ctx(str(uuid.uuid4()))
    orchestrator = RunOrchestrator(
        audit_sink=InMemoryAuditSink(),
        run_steps_repo=InMemoryRunStepsRepository(TENANT_ID),
    )
    accumulated = {
        "status": "completed",
        "package_ids": [],
        "summary": {
            "package_count": 0,
            "packaged_claim_count": 0,
            "finding_count": 0,
            "by_disposition": {},
            "by_grade": {},
            "by_dashboard_verdict": {},
            "by_finding_type": {},
            "by_reason": {},
        },
    }

    summary = orchestrator._execute_methodology_external_intelligence_conflict_check_plan(  # noqa: SLF001
        ctx,
        accumulated,
    )

    assert ctx.methodology_external_intelligence_conflict_check_plan is None
    assert summary["status"] == "completed"
    assert summary["plan_ids"] == []
    assert summary["provider_check_ids"] == []
    assert summary["summary"]["plan_count"] == 0
    assert summary["summary"]["check_count"] == 0
    assert summary["summary"]["by_status"] == {"no_op": 1}


def test_rehydrate_external_intelligence_plan_uses_safe_shell_only() -> None:
    ctx = _ctx_with_vep(str(uuid.uuid4()))
    ctx.methodology_external_intelligence_conflict_check_plan_fn = _service(
        _ExplodingConnector("sec_edgar")
    ).run
    orchestrator = RunOrchestrator(
        audit_sink=InMemoryAuditSink(),
        run_steps_repo=InMemoryRunStepsRepository(TENANT_ID),
    )

    summary = orchestrator._execute_methodology_external_intelligence_conflict_check_plan(ctx)  # noqa: SLF001
    ctx2 = _ctx(ctx.run_id)
    orchestrator._rehydrate_methodology_external_intelligence_conflict_check_plan(ctx2, summary)  # noqa: SLF001

    assert isinstance(
        ctx2.methodology_external_intelligence_conflict_check_plan,
        RunScopedExternalIntelligenceConflictCheckPlanShell,
    )
    assert ctx2.methodology_external_intelligence_conflict_check_plan.provider_ids == ["sec_edgar"]
    assert ctx2.methodology_external_intelligence_conflict_check_plan.provider_check_ids
    assert not hasattr(
        ctx2.methodology_external_intelligence_conflict_check_plan,
        "normalized",
    )


def _ctx_with_vep(run_id: str) -> Any:
    ctx = _ctx(run_id)
    ctx.methodology_validated_evidence_package = _vep_record().model_copy(update={"run_id": run_id})
    return ctx
