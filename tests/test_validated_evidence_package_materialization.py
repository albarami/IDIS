"""Tests for Slice 12 Validated Evidence Package materialization models."""

from __future__ import annotations

import json

from idis.models.evidence_trust_court_materialization import (
    EvidenceTrustDisposition,
    EvidenceTrustFindingType,
)
from idis.models.sanad import SanadGrade
from idis.models.truth_dashboard_materialization import TruthDashboardVerdict
from idis.models.validated_evidence_package_materialization import (
    MethodologyValidatedEvidencePackageStatus,
    RunScopedValidatedEvidencePackageRecord,
    RunScopedValidatedEvidencePackageSummary,
    deterministic_validated_evidence_package_id,
)
from tests.test_run_methodology_deterministic_calculation_service import (
    DEAL_ID,
    RUN_ID,
    TENANT_ID,
)


def test_deterministic_package_id_is_stable_under_input_ordering() -> None:
    first_id = deterministic_validated_evidence_package_id(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        court_id="court-001",
        claim_ids=["claim_mth_revenue", "claim_mth_margin"],
        finding_ids=["finding-dashboard", "finding-provenance"],
    )
    second_id = deterministic_validated_evidence_package_id(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        court_id="court-001",
        claim_ids=["claim_mth_margin", "claim_mth_revenue"],
        finding_ids=["finding-provenance", "finding-dashboard"],
    )

    assert first_id == second_id


def test_record_shell_and_run_summary_are_safe_and_stably_ordered() -> None:
    record = _package_record()

    shell = record.to_shell()
    summary_json = json.dumps(record.to_run_step_summary(), sort_keys=True)
    shell_json = shell.model_dump_json()

    assert shell.package_id == "package-001"
    assert shell.claim_ids_by_disposition == {
        EvidenceTrustDisposition.DISPUTED.value: ["claim_mth_margin"],
        EvidenceTrustDisposition.TRUSTED.value: ["claim_mth_revenue"],
    }
    assert shell.evidence_ids == ["evidence-margin", "evidence-revenue"]
    assert shell.finding_types == [
        EvidenceTrustFindingType.DASHBOARD_CONSISTENCY.value,
        EvidenceTrustFindingType.PROVENANCE.value,
    ]
    assert "claim_mth_revenue: 1000 USD" not in summary_json
    assert "Document A" not in summary_json
    assert "raw span text" not in summary_json
    assert "grade explanation" not in summary_json
    assert "AgentOutput" not in summary_json
    assert "content" not in summary_json
    assert "Muhasabah narrative" not in summary_json
    assert "recommendation" not in summary_json
    assert "GO" not in summary_json
    assert "content" not in shell_json
    assert "recommendation" not in shell_json


def test_summary_counts_preserve_layer1_aggregate_metadata() -> None:
    record = _package_record()

    summary = record.to_summary()

    assert summary == RunScopedValidatedEvidencePackageSummary(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        package_count=1,
        packaged_claim_count=2,
        finding_count=2,
        by_disposition={
            EvidenceTrustDisposition.DISPUTED.value: 1,
            EvidenceTrustDisposition.TRUSTED.value: 1,
        },
        by_grade={SanadGrade.A.value: 1, SanadGrade.B.value: 1},
        by_dashboard_verdict={
            TruthDashboardVerdict.CONFIRMED.value: 1,
            TruthDashboardVerdict.DISPUTED.value: 1,
        },
        by_finding_type={
            EvidenceTrustFindingType.DASHBOARD_CONSISTENCY.value: 1,
            EvidenceTrustFindingType.PROVENANCE.value: 1,
        },
        by_reason={
            "dashboard_disputed": 1,
            "source_provenance_verified": 1,
            "trusted_a_or_b_sanad": 2,
        },
    )
    assert summary.aggregate_status() == MethodologyValidatedEvidencePackageStatus.COMPLETED


def _package_record() -> RunScopedValidatedEvidencePackageRecord:
    return RunScopedValidatedEvidencePackageRecord(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        package_id="package-001",
        court_id="court-001",
        dashboard_id="dashboard-001",
        claim_ids_by_disposition={
            EvidenceTrustDisposition.TRUSTED.value: [
                "claim_mth_revenue",
                "claim_mth_revenue",
            ],
            EvidenceTrustDisposition.DISPUTED.value: ["claim_mth_margin"],
        },
        evidence_ids=["evidence-revenue", "evidence-margin", "evidence-revenue"],
        source_span_ids=["span-revenue", "span-margin"],
        sanad_ids=["sanad-revenue", "sanad-margin"],
        defect_ids=[],
        calc_ids=["calc-margin"],
        finding_ids=["finding-provenance", "finding-dashboard"],
        finding_types=[
            EvidenceTrustFindingType.PROVENANCE.value,
            EvidenceTrustFindingType.DASHBOARD_CONSISTENCY.value,
        ],
        role_names=["skeptic", "advocate", "advocate"],
        reason_codes=[
            "trusted_a_or_b_sanad",
            "source_provenance_verified",
            "trusted_a_or_b_sanad",
            "dashboard_disputed",
        ],
        by_disposition={
            EvidenceTrustDisposition.TRUSTED.value: 1,
            EvidenceTrustDisposition.DISPUTED.value: 1,
        },
        by_grade={SanadGrade.B.value: 1, SanadGrade.A.value: 1},
        by_dashboard_verdict={
            TruthDashboardVerdict.DISPUTED.value: 1,
            TruthDashboardVerdict.CONFIRMED.value: 1,
        },
        by_finding_type={
            EvidenceTrustFindingType.PROVENANCE.value: 1,
            EvidenceTrustFindingType.DASHBOARD_CONSISTENCY.value: 1,
        },
        by_reason={
            "trusted_a_or_b_sanad": 2,
            "dashboard_disputed": 1,
            "source_provenance_verified": 1,
        },
        status=MethodologyValidatedEvidencePackageStatus.COMPLETED,
    )
