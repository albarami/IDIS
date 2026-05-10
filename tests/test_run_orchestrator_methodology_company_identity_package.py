"""Run orchestrator tests for Slice 15 company identity package wiring."""

from __future__ import annotations

import uuid

from idis.audit.sink import InMemoryAuditSink
from idis.models.company_identity_package_materialization import (
    MethodologyCompanyIdentityStatus,
    RunScopedCompanyIdentityPackageShell,
)
from idis.models.layer2_readiness_package_materialization import (
    MethodologyLayer2ReadinessStatus,
)
from idis.models.run_step import FULL_ONLY_STEPS, FULL_STEPS, SNAPSHOT_STEPS, StepName
from idis.persistence.repositories.run_steps import (
    InMemoryRunStepsRepository,
    clear_run_steps_store,
)
from idis.services.runs.orchestrator import RunOrchestrator
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


def test_full_step_order_places_identity_between_external_plan_and_readiness() -> None:
    assert StepName.METHODOLOGY_COMPANY_IDENTITY_PACKAGE in FULL_STEPS
    assert StepName.METHODOLOGY_COMPANY_IDENTITY_PACKAGE in FULL_ONLY_STEPS
    assert StepName.METHODOLOGY_COMPANY_IDENTITY_PACKAGE not in SNAPSHOT_STEPS
    assert FULL_STEPS.index(
        StepName.METHODOLOGY_EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN
    ) < FULL_STEPS.index(StepName.METHODOLOGY_COMPANY_IDENTITY_PACKAGE)
    assert FULL_STEPS.index(StepName.METHODOLOGY_COMPANY_IDENTITY_PACKAGE) < FULL_STEPS.index(
        StepName.METHODOLOGY_LAYER2_READINESS_PACKAGE
    )


def test_identity_step_attaches_safe_shell_without_exposing_company_name() -> None:
    ctx = _ctx_with_vep_external_plan_and_deal_metadata(str(uuid.uuid4()))
    orchestrator = RunOrchestrator(
        audit_sink=InMemoryAuditSink(),
        run_steps_repo=InMemoryRunStepsRepository(TENANT_ID),
    )

    summary = orchestrator._execute_methodology_company_identity_package(ctx)  # noqa: SLF001

    assert ctx.methodology_company_identity_package is not None
    assert summary["construction_status"] == "completed"
    assert summary["identity_status"] == "mapped"
    assert summary["company_identity_ids"]
    assert "company identity input boundary" in str(summary)
    assert "Acme Corp" not in str(summary)
    assert "company_name" not in str(summary)
    assert "EnrichmentService" not in str(summary)
    assert "fetch" not in str(summary)


def test_rehydrate_company_identity_package_uses_safe_shell_only() -> None:
    ctx = _ctx_with_vep_external_plan_and_deal_metadata(str(uuid.uuid4()))
    orchestrator = RunOrchestrator(
        audit_sink=InMemoryAuditSink(),
        run_steps_repo=InMemoryRunStepsRepository(TENANT_ID),
    )

    summary = orchestrator._execute_methodology_company_identity_package(ctx)  # noqa: SLF001
    ctx2 = _ctx(ctx.run_id)
    orchestrator._rehydrate_methodology_company_identity_package(ctx2, summary)  # noqa: SLF001

    assert isinstance(
        ctx2.methodology_company_identity_package,
        RunScopedCompanyIdentityPackageShell,
    )
    assert ctx2.methodology_company_identity_package.identity_status == (
        MethodologyCompanyIdentityStatus.MAPPED
    )
    assert ctx2.methodology_company_identity_package.company_identity_ids
    assert not hasattr(ctx2.methodology_company_identity_package, "raw")
    assert not hasattr(ctx2.methodology_company_identity_package, "company_name")


def test_layer2_readiness_receives_identity_ids_and_stays_deferred_not_blocked() -> None:
    ctx = _ctx_with_vep_external_plan_and_deal_metadata(str(uuid.uuid4()))
    orchestrator = RunOrchestrator(
        audit_sink=InMemoryAuditSink(),
        run_steps_repo=InMemoryRunStepsRepository(TENANT_ID),
    )

    identity_summary = orchestrator._execute_methodology_company_identity_package(ctx)  # noqa: SLF001
    readiness_summary = orchestrator._execute_methodology_layer2_readiness_package(  # noqa: SLF001
        ctx,
        identity_summary,
    )

    assert ctx.methodology_layer2_readiness_package is not None
    assert readiness_summary["readiness_status"] == "deferred"
    assert readiness_summary["readiness_status"] != MethodologyLayer2ReadinessStatus.BLOCKED.value
    assert readiness_summary["company_identity_ids"] == identity_summary["company_identity_ids"]
    assert "missing_company_identity" not in readiness_summary["reason_codes"]
    assert "no_executed_provider_checks" in readiness_summary["reason_codes"]
    assert "external_intelligence_checks_planned_not_executed" in readiness_summary["reason_codes"]


def _ctx_with_vep_external_plan_and_deal_metadata(run_id: str):
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
    ctx.deal_metadata = {
        "tenant_id": vep.tenant_id,
        "deal_id": vep.deal_id,
        "company_name": "Acme Corp",
    }
    return ctx
