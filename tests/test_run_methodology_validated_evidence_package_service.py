"""Tests for Slice 12 in-memory Validated Evidence Package service."""

from __future__ import annotations

import json

from idis.models.debate import DebateRole, StopReason
from idis.models.evidence_trust_court_materialization import (
    EvidenceTrustDisposition,
    EvidenceTrustFindingType,
    MethodologyEvidenceTrustCourtStatus,
    RunScopedClaimTrustAssessment,
    RunScopedEvidenceTrustCourtFinding,
    RunScopedEvidenceTrustCourtRecord,
    RunScopedEvidenceTrustCourtRoleSummary,
    RunScopedEvidenceTrustCourtSummary,
)
from idis.models.sanad import SanadGrade
from idis.models.truth_dashboard_materialization import TruthDashboardVerdict
from idis.models.validated_evidence_package_materialization import (
    MethodologyValidatedEvidencePackageReason,
    MethodologyValidatedEvidencePackageStatus,
)
from idis.services.runs.methodology_validated_evidence_package import (
    InMemoryRunMethodologyValidatedEvidencePackageService,
)
from tests.test_run_methodology_deterministic_calculation_service import (
    DEAL_ID,
    RUN_ID,
    TENANT_ID,
)


def _service() -> InMemoryRunMethodologyValidatedEvidencePackageService:
    return InMemoryRunMethodologyValidatedEvidencePackageService()


def test_packages_all_dispositions_and_mixed_claim_trust_is_completed() -> None:
    result, packages = _service().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        evidence_trust_courts=[_court_record()],
    )

    package = packages[0]

    assert result.status == MethodologyValidatedEvidencePackageStatus.COMPLETED
    assert package.status == MethodologyValidatedEvidencePackageStatus.COMPLETED
    assert package.claim_ids_by_disposition == {
        EvidenceTrustDisposition.DISPUTED.value: ["claim_mth_disputed"],
        EvidenceTrustDisposition.REJECTED.value: ["claim_mth_rejected"],
        EvidenceTrustDisposition.TRUSTED.value: ["claim_mth_trusted"],
        EvidenceTrustDisposition.UNVERIFIED.value: ["claim_mth_unverified"],
    }
    assert result.to_run_step_summary()["package_ids"] == [package.package_id]


def test_preserves_layer1_metadata_and_calculations_without_recommendations() -> None:
    result, packages = _service().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        evidence_trust_courts=[_court_record()],
    )

    package = packages[0]
    summary_json = json.dumps(result.to_run_step_summary(), sort_keys=True)

    assert package.calc_ids == ["calc-margin"]
    assert package.finding_types == [
        EvidenceTrustFindingType.CONTRADICTION.value,
        EvidenceTrustFindingType.DASHBOARD_CONSISTENCY.value,
        EvidenceTrustFindingType.PROVENANCE.value,
    ]
    assert "dashboard_refuted" in package.reason_codes
    assert "source_provenance_verified" in package.reason_codes
    assert "contradiction_detected" in package.reason_codes
    assert "recommendation" not in summary_json
    assert "GO" not in summary_json
    assert "AgentOutput" not in summary_json
    assert "content" not in summary_json


def test_shell_only_court_fails_closed_without_package() -> None:
    court = _court_record()
    shell = court.to_shell(summary=_court_summary(court))

    result, packages = _service().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        evidence_trust_courts=[shell],
    )

    assert result.status == MethodologyValidatedEvidencePackageStatus.FAILED
    assert packages == []
    assert result.package_shells == []
    assert result.rejections[0].reason == (
        MethodologyValidatedEvidencePackageReason.EVIDENCE_TRUST_COURT_SHELL_ONLY
    )


def test_missing_full_court_record_fails_closed_without_package() -> None:
    result, packages = _service().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        evidence_trust_courts=[],
    )

    assert result.status == MethodologyValidatedEvidencePackageStatus.FAILED
    assert packages == []
    assert result.rejections[0].reason == (
        MethodologyValidatedEvidencePackageReason.MISSING_EVIDENCE_TRUST_COURT
    )


def test_scope_mismatch_fails_closed_without_package() -> None:
    result, packages = _service().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        evidence_trust_courts=[_court_record(tenant_id="tenant-other")],
    )

    assert result.status == MethodologyValidatedEvidencePackageStatus.FAILED
    assert packages == []
    assert result.rejections[0].reason == (
        MethodologyValidatedEvidencePackageReason.TENANT_OR_RUN_MISMATCH
    )


def test_missing_court_internal_reference_fails_closed_without_package() -> None:
    court = _court_record(
        findings=[
            RunScopedEvidenceTrustCourtFinding(
                finding_id="finding-missing-evidence",
                finding_type=EvidenceTrustFindingType.PROVENANCE,
                claim_id="claim_mth_trusted",
                evidence_ids=["evidence-not-in-assessments"],
                sanad_id="sanad-claim_mth_trusted",
                calc_ids=[],
                defect_ids=[],
                reason_codes=["missing_evidence_linkage"],
            )
        ]
    )

    result, packages = _service().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        evidence_trust_courts=[court],
    )

    assert result.status == MethodologyValidatedEvidencePackageStatus.FAILED
    assert packages == []
    assert result.rejections[0].reason == (
        MethodologyValidatedEvidencePackageReason.MISSING_COURT_REFERENCE
    )


def _court_record(
    *,
    tenant_id: str = TENANT_ID,
    findings: list[RunScopedEvidenceTrustCourtFinding] | None = None,
) -> RunScopedEvidenceTrustCourtRecord:
    return RunScopedEvidenceTrustCourtRecord(
        tenant_id=tenant_id,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        court_id="court-001",
        dashboard_id="dashboard-001",
        claim_assessments=[
            _assessment(
                "claim_mth_trusted",
                EvidenceTrustDisposition.TRUSTED,
                SanadGrade.A,
                TruthDashboardVerdict.CONFIRMED,
                reason_codes=["trusted_a_or_b_sanad", "source_provenance_verified"],
            ),
            _assessment(
                "claim_mth_disputed",
                EvidenceTrustDisposition.DISPUTED,
                SanadGrade.B,
                TruthDashboardVerdict.DISPUTED,
                reason_codes=["trusted_a_or_b_sanad", "dashboard_disputed"],
                calc_ids=["calc-margin"],
            ),
            _assessment(
                "claim_mth_rejected",
                EvidenceTrustDisposition.REJECTED,
                SanadGrade.A,
                TruthDashboardVerdict.REFUTED,
                reason_codes=["trusted_a_or_b_sanad", "dashboard_refuted"],
            ),
            _assessment(
                "claim_mth_unverified",
                EvidenceTrustDisposition.UNVERIFIED,
                SanadGrade.C,
                TruthDashboardVerdict.UNVERIFIED,
                reason_codes=["sanad_grade_c", "unverified_c_sanad"],
            ),
        ],
        findings=findings
        if findings is not None
        else [
            _finding(
                "finding-provenance",
                EvidenceTrustFindingType.PROVENANCE,
                "claim_mth_trusted",
                ["source_provenance_verified"],
            ),
            _finding(
                "finding-dashboard",
                EvidenceTrustFindingType.DASHBOARD_CONSISTENCY,
                "claim_mth_rejected",
                ["dashboard_refuted"],
            ),
            _finding(
                "finding-contradiction",
                EvidenceTrustFindingType.CONTRADICTION,
                "claim_mth_disputed",
                ["contradiction_detected"],
            ),
        ],
        role_summaries=[
            RunScopedEvidenceTrustCourtRoleSummary(
                output_id="out-advocate",
                agent_id="advocate-layer1",
                role=DebateRole.ADVOCATE,
                output_type="layer1_evidence_position",
                supported_claim_ids=["claim_mth_trusted", "claim_mth_disputed"],
                supported_calc_ids=["calc-margin"],
                confidence=0.82,
                reason_codes=["muhasabah_gate_passed"],
            )
        ],
        stop_reason=StopReason.MAX_ROUNDS,
        status="created",
    )


def _assessment(
    claim_id: str,
    disposition: EvidenceTrustDisposition,
    grade: SanadGrade,
    verdict: TruthDashboardVerdict,
    *,
    reason_codes: list[str],
    calc_ids: list[str] | None = None,
) -> RunScopedClaimTrustAssessment:
    return RunScopedClaimTrustAssessment(
        claim_id=claim_id,
        disposition=disposition,
        evidence_ids=[f"evidence-{claim_id}"],
        source_span_ids=[f"span-{claim_id}"],
        sanad_id=f"sanad-{claim_id}",
        sanad_grade=grade,
        dashboard_verdict=verdict,
        calc_ids=calc_ids or [],
        defect_ids=[],
        reason_codes=reason_codes,
    )


def _finding(
    finding_id: str,
    finding_type: EvidenceTrustFindingType,
    claim_id: str,
    reason_codes: list[str],
) -> RunScopedEvidenceTrustCourtFinding:
    return RunScopedEvidenceTrustCourtFinding(
        finding_id=finding_id,
        finding_type=finding_type,
        claim_id=claim_id,
        evidence_ids=[f"evidence-{claim_id}"],
        sanad_id=f"sanad-{claim_id}",
        calc_ids=[],
        defect_ids=[],
        reason_codes=reason_codes,
    )


def _court_summary(
    court: RunScopedEvidenceTrustCourtRecord,
) -> RunScopedEvidenceTrustCourtSummary:
    return RunScopedEvidenceTrustCourtSummary(
        tenant_id=court.tenant_id,
        deal_id=court.deal_id,
        run_id=court.run_id,
        total_claims=len(court.claim_assessments),
        assessed_claim_count=len(court.claim_assessments),
        finding_count=len(court.findings),
        rejected_count=0,
        by_disposition={
            EvidenceTrustDisposition.DISPUTED.value: 1,
            EvidenceTrustDisposition.REJECTED.value: 1,
            EvidenceTrustDisposition.TRUSTED.value: 1,
            EvidenceTrustDisposition.UNVERIFIED.value: 1,
        },
        by_reason={},
        by_grade={},
        by_dashboard_verdict={},
    )


assert MethodologyEvidenceTrustCourtStatus.COMPLETED.value == "completed"
