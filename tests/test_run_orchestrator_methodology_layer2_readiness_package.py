"""Run orchestrator tests for Slice 14 Layer 2 readiness package wiring."""

from __future__ import annotations

import uuid

import pytest

from idis.audit.sink import InMemoryAuditSink
from idis.models.layer2_readiness_package_materialization import (
    MethodologyLayer2ReadinessStatus,
    RunScopedLayer2ReadinessPackageShell,
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
    _vep_record,
)
from tests.test_run_methodology_external_intelligence_conflict_check_plan_service import (
    _service as _external_plan_service,
)
from tests.test_run_orchestrator_methodology_claim_materialization import _ctx


def setup_function() -> None:
    clear_run_steps_store()


def test_full_step_order_places_readiness_after_external_plan_before_extract() -> None:
    assert StepName.METHODOLOGY_LAYER2_READINESS_PACKAGE in FULL_STEPS
    assert StepName.METHODOLOGY_LAYER2_READINESS_PACKAGE in FULL_ONLY_STEPS
    assert StepName.METHODOLOGY_LAYER2_READINESS_PACKAGE not in SNAPSHOT_STEPS
    assert FULL_STEPS.index(
        StepName.METHODOLOGY_EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN
    ) < FULL_STEPS.index(StepName.METHODOLOGY_LAYER2_READINESS_PACKAGE)
    assert FULL_STEPS.index(StepName.METHODOLOGY_LAYER2_READINESS_PACKAGE) < FULL_STEPS.index(
        StepName.EXTRACT
    )


def test_readiness_step_attaches_safe_blocked_package_without_layer2_execution() -> None:
    ctx = _ctx_with_vep_and_external_plan(str(uuid.uuid4()))
    orchestrator = RunOrchestrator(
        audit_sink=InMemoryAuditSink(),
        run_steps_repo=InMemoryRunStepsRepository(TENANT_ID),
    )

    summary = orchestrator._execute_methodology_layer2_readiness_package(ctx)  # noqa: SLF001

    assert ctx.methodology_layer2_readiness_package is not None
    assert summary["construction_status"] == "completed"
    assert summary["readiness_status"] == "blocked"
    assert summary["readiness_status"] != MethodologyLayer2ReadinessStatus.READY.value
    assert "readiness/input-boundary" in str(summary)
    assert "IC debate executed" not in str(summary)
    assert "scorecard" not in str(summary)
    assert "recommendation" not in str(summary)
    assert "GO" not in str(summary)
    assert "INVEST" not in str(summary)


def test_missing_external_plan_blocks_layer2_readiness_package() -> None:
    ctx = _ctx(str(uuid.uuid4()))
    ctx.methodology_validated_evidence_package = _vep_record().model_copy(
        update={"run_id": ctx.run_id}
    )
    orchestrator = RunOrchestrator(
        audit_sink=InMemoryAuditSink(),
        run_steps_repo=InMemoryRunStepsRepository(TENANT_ID),
    )

    with pytest.raises(RunStepBlockedError) as missing_plan:
        orchestrator._execute_methodology_layer2_readiness_package(ctx)  # noqa: SLF001

    assert missing_plan.value.code == (
        "METHODOLOGY_EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN_MISSING"
    )
    assert ctx.methodology_layer2_readiness_package is None


def test_rehydrate_layer2_readiness_package_uses_safe_shell_only() -> None:
    ctx = _ctx_with_vep_and_external_plan(str(uuid.uuid4()))
    orchestrator = RunOrchestrator(
        audit_sink=InMemoryAuditSink(),
        run_steps_repo=InMemoryRunStepsRepository(TENANT_ID),
    )

    summary = orchestrator._execute_methodology_layer2_readiness_package(ctx)  # noqa: SLF001
    ctx2 = _ctx(ctx.run_id)
    orchestrator._rehydrate_methodology_layer2_readiness_package(ctx2, summary)  # noqa: SLF001

    assert isinstance(
        ctx2.methodology_layer2_readiness_package,
        RunScopedLayer2ReadinessPackageShell,
    )
    assert ctx2.methodology_layer2_readiness_package.readiness_status == (
        MethodologyLayer2ReadinessStatus.BLOCKED
    )
    assert ctx2.methodology_layer2_readiness_package.provider_check_ids
    assert not hasattr(ctx2.methodology_layer2_readiness_package, "raw")
    assert not hasattr(ctx2.methodology_layer2_readiness_package, "recommendation")


def _ctx_with_vep_and_external_plan(run_id: str):
    ctx = _ctx(run_id)
    vep = _vep_record().model_copy(update={"run_id": run_id})
    result, plans = _external_plan_service(_ExplodingConnector("sec_edgar")).run(
        tenant_id=vep.tenant_id,
        deal_id=vep.deal_id,
        run_id=vep.run_id,
        validated_evidence_packages=[vep],
    )
    assert result.status.value == "completed"
    ctx.methodology_validated_evidence_package = vep
    ctx.methodology_external_intelligence_conflict_check_plan = plans[0]
    return ctx
