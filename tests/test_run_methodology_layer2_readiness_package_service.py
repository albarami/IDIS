"""Tests for Slice 14 Layer 2 readiness package service."""

from __future__ import annotations

from idis.models.layer2_readiness_package_materialization import (
    MethodologyLayer2ReadinessPackageConstructionStatus,
    MethodologyLayer2ReadinessPackageReason,
    MethodologyLayer2ReadinessStatus,
)
from idis.services.runs.methodology_layer2_readiness_package import (
    InMemoryRunMethodologyLayer2ReadinessPackageService,
)
from tests.test_run_methodology_deterministic_calculation_service import (
    DEAL_ID,
    RUN_ID,
    TENANT_ID,
)
from tests.test_run_methodology_external_intelligence_conflict_check_plan_service import (
    _ExplodingConnector,
    _vep_record,
)
from tests.test_run_methodology_external_intelligence_conflict_check_plan_service import (
    _service as _external_plan_service,
)


def test_missing_required_company_identity_constructs_blocked_package_not_ready() -> None:
    vep = _vep_record()
    plan = _external_plan(vep)

    result, packages = InMemoryRunMethodologyLayer2ReadinessPackageService().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        validated_evidence_packages=[vep],
        external_intelligence_conflict_check_plans=[plan],
    )

    package = packages[0]
    summary = result.to_run_step_summary()

    assert result.construction_status == (
        MethodologyLayer2ReadinessPackageConstructionStatus.COMPLETED
    )
    assert package.construction_status == (
        MethodologyLayer2ReadinessPackageConstructionStatus.COMPLETED
    )
    assert package.readiness_status == MethodologyLayer2ReadinessStatus.BLOCKED
    assert package.readiness_status is not MethodologyLayer2ReadinessStatus.READY
    assert MethodologyLayer2ReadinessPackageReason.NO_EXECUTED_PROVIDER_CHECKS.value in (
        package.reason_codes
    )
    assert (
        MethodologyLayer2ReadinessPackageReason.EXTERNAL_INTELLIGENCE_CHECKS_PLANNED_NOT_EXECUTED.value
        in package.reason_codes
    )
    assert summary["construction_status"] == "completed"
    assert summary["readiness_status"] == "blocked"
    assert "ready" not in summary["readiness_status"]
    assert "recommendation" not in str(summary)
    assert "INVEST" not in str(summary)


def test_current_slice13_plan_only_inputs_stay_deferred_when_no_hard_blockers_exist() -> None:
    vep = _vep_record()
    plan = _external_plan(vep)

    result, packages = InMemoryRunMethodologyLayer2ReadinessPackageService().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        validated_evidence_packages=[vep],
        external_intelligence_conflict_check_plans=[plan],
        company_identity_ids=["company-identity-001"],
        enrichment_fact_ids=["enrichment-fact-001"],
    )

    package = packages[0]
    blocking_blockers = [blocker for blocker in package.blockers if blocker.severity == "blocking"]

    assert result.construction_status == (
        MethodologyLayer2ReadinessPackageConstructionStatus.COMPLETED
    )
    assert blocking_blockers == []
    assert package.readiness_status == MethodologyLayer2ReadinessStatus.DEFERRED
    assert package.readiness_status is not MethodologyLayer2ReadinessStatus.READY
    assert package.company_identity_ids == ["company-identity-001"]
    assert package.enrichment_fact_ids == ["enrichment-fact-001"]
    assert package.executed_provider_check_ids == []
    assert MethodologyLayer2ReadinessPackageReason.NO_EXECUTED_PROVIDER_CHECKS.value in (
        package.reason_codes
    )
    assert (
        MethodologyLayer2ReadinessPackageReason.EXTERNAL_INTELLIGENCE_CHECKS_PLANNED_NOT_EXECUTED.value
        in package.reason_codes
    )
    assert result.to_run_step_summary()["readiness_status"] == "deferred"


def test_missing_required_claim_or_calc_refs_constructs_blocked_package() -> None:
    vep = _vep_record()
    plan = _external_plan(vep)
    no_claims_vep = vep.model_copy(update={"claim_ids_by_disposition": {}})
    no_calcs_vep = vep.model_copy(update={"calc_ids": []})

    _, no_claim_packages = InMemoryRunMethodologyLayer2ReadinessPackageService().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        validated_evidence_packages=[no_claims_vep],
        external_intelligence_conflict_check_plans=[plan],
        company_identity_ids=["company-identity-001"],
        enrichment_fact_ids=["enrichment-fact-001"],
    )
    _, no_calc_packages = InMemoryRunMethodologyLayer2ReadinessPackageService().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        validated_evidence_packages=[no_calcs_vep],
        external_intelligence_conflict_check_plans=[plan],
        company_identity_ids=["company-identity-001"],
        enrichment_fact_ids=["enrichment-fact-001"],
    )

    no_claim_package = no_claim_packages[0]
    no_calc_package = no_calc_packages[0]

    assert no_claim_package.readiness_status == MethodologyLayer2ReadinessStatus.BLOCKED
    assert no_calc_package.readiness_status == MethodologyLayer2ReadinessStatus.BLOCKED
    assert MethodologyLayer2ReadinessPackageReason.MISSING_CLAIM_REFS.value in (
        no_claim_package.reason_codes
    )
    assert MethodologyLayer2ReadinessPackageReason.MISSING_CALC_REFS.value in (
        no_calc_package.reason_codes
    )
    assert any(blocker.severity == "blocking" for blocker in no_claim_package.blockers)
    assert any(blocker.severity == "blocking" for blocker in no_calc_package.blockers)


def test_safe_shells_are_sufficient_and_do_not_infer_company_identity() -> None:
    vep = _vep_record()
    plan = _external_plan(vep)

    result, packages = InMemoryRunMethodologyLayer2ReadinessPackageService().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        validated_evidence_packages=[vep.to_shell()],
        external_intelligence_conflict_check_plans=[plan.to_shell()],
    )

    package = packages[0]
    summary = result.to_run_step_summary()

    assert result.construction_status == (
        MethodologyLayer2ReadinessPackageConstructionStatus.COMPLETED
    )
    assert package.source_vep_package_id == vep.package_id
    assert package.source_external_intelligence_plan_id == plan.plan_id
    assert package.company_identity_ids == []
    assert package.readiness_status == MethodologyLayer2ReadinessStatus.BLOCKED
    assert MethodologyLayer2ReadinessPackageReason.MISSING_COMPANY_IDENTITY.value in (
        package.reason_codes
    )
    assert "Acme" not in str(summary)
    assert "company_name" not in str(summary)


def test_missing_vep_fails_construction_without_package() -> None:
    plan = _external_plan(_vep_record())

    result, packages = InMemoryRunMethodologyLayer2ReadinessPackageService().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        validated_evidence_packages=[],
        external_intelligence_conflict_check_plans=[plan],
    )

    assert result.construction_status == MethodologyLayer2ReadinessPackageConstructionStatus.FAILED
    assert result.readiness_status == MethodologyLayer2ReadinessStatus.BLOCKED
    assert packages == []
    assert result.package_shells == []
    assert result.rejections[0].reason == (
        MethodologyLayer2ReadinessPackageReason.MISSING_VALIDATED_EVIDENCE_PACKAGE
    )


def test_missing_external_intelligence_plan_fails_construction_without_package() -> None:
    result, packages = InMemoryRunMethodologyLayer2ReadinessPackageService().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        validated_evidence_packages=[_vep_record()],
        external_intelligence_conflict_check_plans=[],
    )

    assert result.construction_status == MethodologyLayer2ReadinessPackageConstructionStatus.FAILED
    assert result.readiness_status == MethodologyLayer2ReadinessStatus.BLOCKED
    assert packages == []
    assert result.rejections[0].reason == (
        MethodologyLayer2ReadinessPackageReason.MISSING_EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN
    )


def test_cross_scope_external_intelligence_plan_fails_closed() -> None:
    vep = _vep_record()
    plan = _external_plan(vep).model_copy(update={"tenant_id": "tenant-other"})

    result, packages = InMemoryRunMethodologyLayer2ReadinessPackageService().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        validated_evidence_packages=[vep],
        external_intelligence_conflict_check_plans=[plan],
    )

    assert result.construction_status == MethodologyLayer2ReadinessPackageConstructionStatus.FAILED
    assert result.readiness_status == MethodologyLayer2ReadinessStatus.BLOCKED
    assert packages == []
    assert result.rejections[0].reason == (
        MethodologyLayer2ReadinessPackageReason.TENANT_OR_RUN_MISMATCH
    )


def test_service_source_has_no_layer2_or_live_provider_calls() -> None:
    import inspect

    import idis.services.runs.methodology_layer2_readiness_package as service_module

    service_source = inspect.getsource(service_module)

    assert "DebateOrchestrator" not in service_source
    assert "AnalysisEngine" not in service_source
    assert "ScoringEngine" not in service_source
    assert "DeliverablesGenerator" not in service_source
    assert "EnrichmentService" not in service_source
    assert ".enrich(" not in service_source
    assert ".fetch(" not in service_source


def _external_plan(vep):
    result, plans = _external_plan_service(_ExplodingConnector("sec_edgar")).run(
        tenant_id=vep.tenant_id,
        deal_id=vep.deal_id,
        run_id=vep.run_id,
        validated_evidence_packages=[vep],
    )
    assert result.status.value == "completed"
    return plans[0]
