"""Tests for Slice 14 Layer 2 readiness package models."""

from __future__ import annotations

import json

from idis.models.layer2_readiness_package_materialization import (
    MethodologyLayer2ReadinessPackageConstructionStatus,
    MethodologyLayer2ReadinessPackageReason,
    MethodologyLayer2ReadinessStatus,
    RunScopedLayer2ReadinessBlocker,
    RunScopedLayer2ReadinessPackageRecord,
    RunScopedLayer2ReadinessPackageSummary,
    deterministic_layer2_readiness_package_id,
)
from tests.test_run_methodology_deterministic_calculation_service import (
    DEAL_ID,
    RUN_ID,
    TENANT_ID,
)


def test_deterministic_readiness_package_id_is_stable_under_input_ordering() -> None:
    first_id = deterministic_layer2_readiness_package_id(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        vep_package_id="vep-package-001",
        external_intelligence_plan_id="external-plan-001",
        claim_ids=["claim-b", "claim-a"],
        calc_ids=["calc-b", "calc-a"],
        reason_codes=["missing_company_identity", "layer2_execution_deferred"],
    )
    second_id = deterministic_layer2_readiness_package_id(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        vep_package_id="vep-package-001",
        external_intelligence_plan_id="external-plan-001",
        claim_ids=["claim-a", "claim-b"],
        calc_ids=["calc-a", "calc-b"],
        reason_codes=["layer2_execution_deferred", "missing_company_identity"],
    )

    assert first_id == second_id


def test_record_shell_and_summary_keep_construction_separate_from_readiness() -> None:
    record = _readiness_record()

    shell = record.to_shell()
    run_summary = record.to_run_step_summary()
    serialized = json.dumps(run_summary, sort_keys=True)

    assert shell.readiness_package_id == "layer2-readiness-package-001"
    assert shell.construction_status == (
        MethodologyLayer2ReadinessPackageConstructionStatus.COMPLETED
    )
    assert shell.readiness_status == MethodologyLayer2ReadinessStatus.DEFERRED
    assert run_summary["construction_status"] == "completed"
    assert run_summary["readiness_status"] == "deferred"
    assert run_summary["readiness_package_ids"] == ["layer2-readiness-package-001"]
    assert run_summary["source_vep_package_ids"] == ["vep-package-001"]
    assert run_summary["source_external_intelligence_plan_ids"] == ["external-plan-001"]
    assert run_summary["summary"]["blocker_count"] == 2
    assert run_summary["summary"]["by_reason"] == {
        "external_intelligence_checks_planned_not_executed": 1,
        "layer2_execution_deferred": 1,
        "missing_company_identity": 1,
        "no_executed_provider_checks": 1,
    }
    assert "readiness/input-boundary" in serialized
    assert "IC debate executed" not in serialized
    assert "scorecard" not in serialized
    assert "deliverable" not in serialized
    assert "normalized" not in serialized
    assert "raw" not in serialized
    assert "Acme Corp" not in serialized
    assert "recommendation" not in serialized
    assert "GO" not in serialized
    assert "NO-GO" not in serialized
    assert "INVEST" not in serialized
    assert "HOLD" not in serialized
    assert "DECLINE" not in serialized


def test_summary_counts_are_stable_and_sorted() -> None:
    summary = _readiness_record().to_summary()

    assert summary == RunScopedLayer2ReadinessPackageSummary(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        package_count=1,
        claim_count=2,
        calc_count=1,
        provider_check_count=2,
        executed_provider_check_count=0,
        blocker_count=2,
        construction_status=MethodologyLayer2ReadinessPackageConstructionStatus.COMPLETED,
        readiness_status=MethodologyLayer2ReadinessStatus.DEFERRED,
        by_reason={
            "external_intelligence_checks_planned_not_executed": 1,
            "layer2_execution_deferred": 1,
            "missing_company_identity": 1,
            "no_executed_provider_checks": 1,
        },
        by_blocker_severity={"blocking": 1, "deferred": 1},
    )


def _readiness_record() -> RunScopedLayer2ReadinessPackageRecord:
    return RunScopedLayer2ReadinessPackageRecord(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        readiness_package_id="layer2-readiness-package-001",
        source_vep_package_id="vep-package-001",
        source_external_intelligence_plan_id="external-plan-001",
        claim_ids=["claim-b", "claim-a"],
        calc_ids=["calc-001"],
        provider_check_ids=["check-b", "check-a"],
        executed_provider_check_ids=[],
        company_identity_ids=[],
        enrichment_fact_ids=[],
        construction_status=MethodologyLayer2ReadinessPackageConstructionStatus.COMPLETED,
        readiness_status=MethodologyLayer2ReadinessStatus.DEFERRED,
        reason_codes=[
            MethodologyLayer2ReadinessPackageReason.EXTERNAL_INTELLIGENCE_CHECKS_PLANNED_NOT_EXECUTED.value,
            MethodologyLayer2ReadinessPackageReason.NO_EXECUTED_PROVIDER_CHECKS.value,
            MethodologyLayer2ReadinessPackageReason.MISSING_COMPANY_IDENTITY.value,
            MethodologyLayer2ReadinessPackageReason.LAYER2_EXECUTION_DEFERRED.value,
        ],
        blockers=[
            RunScopedLayer2ReadinessBlocker(
                blocker_id="blocker-company-identity",
                reason=MethodologyLayer2ReadinessPackageReason.MISSING_COMPANY_IDENTITY,
                severity="blocking",
                source_artifact_type="validated_evidence_package",
                source_artifact_id="vep-package-001",
            ),
            RunScopedLayer2ReadinessBlocker(
                blocker_id="blocker-provider-execution",
                reason=MethodologyLayer2ReadinessPackageReason.NO_EXECUTED_PROVIDER_CHECKS,
                severity="deferred",
                source_artifact_type="external_intelligence_conflict_check_plan",
                source_artifact_id="external-plan-001",
            ),
        ],
    )
