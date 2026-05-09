"""Slice 11 in-memory Layer 1 Evidence Trust Court service."""

from __future__ import annotations

from typing import Any

from idis.models.calc_materialization import (
    RunScopedCalcSanadRecord,
    RunScopedCalcSanadShell,
    RunScopedCalculationShell,
    RunScopedDeterministicCalculationRecord,
)
from idis.models.claim_materialization import (
    RunScopedMaterializedClaim,
    RunScopedMaterializedClaimShell,
)
from idis.models.evidence_item_materialization import (
    RunScopedEvidenceItemRecord,
    RunScopedEvidenceItemShell,
    RunScopedEvidenceProvenanceRef,
)
from idis.models.evidence_trust_court_materialization import (
    EvidenceTrustDisposition,
    MethodologyEvidenceTrustCourtReason,
    MethodologyEvidenceTrustCourtRejection,
    MethodologyEvidenceTrustCourtRunResult,
    MethodologyEvidenceTrustCourtStatus,
    RunScopedEvidenceTrustCourtRecord,
    RunScopedEvidenceTrustCourtSummary,
    build_evidence_trust_alias_maps,
    deterministic_evidence_trust_court_id,
)
from idis.models.sanad_materialization import (
    RunScopedSanadDefectRecord,
    RunScopedSanadDefectShell,
    RunScopedSanadGradeRecord,
    RunScopedSanadRecord,
    RunScopedSanadShell,
)
from idis.models.truth_dashboard_materialization import (
    RunScopedTruthDashboardRecord,
    RunScopedTruthDashboardShell,
)
from idis.services.runs.methodology_evidence_trust_court_helpers import (
    CourtInputBundle,
    assess_claims,
    calc_id,
    claim_id,
    role_summaries,
    run_layer1_debate,
    scope_mismatch,
    source_key,
    summary_from_court,
)


class InMemoryRunMethodologyEvidenceTrustCourtService:
    """Build run-scoped Layer 1 Evidence Trust Court records from Slice 6-10 outputs."""

    def run(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        materialized_claims: list[RunScopedMaterializedClaim | RunScopedMaterializedClaimShell],
        evidence_items: list[RunScopedEvidenceItemRecord | RunScopedEvidenceItemShell],
        source_provenance: list[RunScopedEvidenceProvenanceRef],
        sanads: list[RunScopedSanadRecord | RunScopedSanadShell],
        sanad_grades: list[RunScopedSanadGradeRecord],
        sanad_defects: list[RunScopedSanadDefectRecord | RunScopedSanadDefectShell],
        calculations: list[RunScopedDeterministicCalculationRecord | RunScopedCalculationShell],
        calc_sanads: list[RunScopedCalcSanadRecord | RunScopedCalcSanadShell],
        truth_dashboards: list[RunScopedTruthDashboardRecord | RunScopedTruthDashboardShell],
    ) -> tuple[MethodologyEvidenceTrustCourtRunResult, list[RunScopedEvidenceTrustCourtRecord]]:
        """Run the Layer 1 Evidence Trust Court and return safe run-scoped records."""
        early_rejection = self._early_rejection(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            scoped_inputs=[
                *materialized_claims,
                *evidence_items,
                *sanads,
                *sanad_grades,
                *sanad_defects,
                *calculations,
                *calc_sanads,
                *truth_dashboards,
            ],
            truth_dashboards=truth_dashboards,
        )
        if early_rejection is not None:
            return self._failed_result(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                total_claims=len(materialized_claims),
                rejection=early_rejection,
            ), []

        dashboard = truth_dashboards[0]
        if not isinstance(dashboard, RunScopedTruthDashboardRecord):
            rejection = _rejection(
                MethodologyEvidenceTrustCourtReason.TRUTH_DASHBOARD_SHELL_ONLY,
                "Truth Dashboard shell cannot support factual court assertions",
            )
            return self._failed_result(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                total_claims=len(materialized_claims),
                rejection=rejection,
            ), []

        bundle = CourtInputBundle(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            materialized_claims=materialized_claims,
            evidence_items=evidence_items,
            source_provenance=source_provenance,
            sanads=sanads,
            sanad_grades=sanad_grades,
            sanad_defects=sanad_defects,
            calculations=calculations,
            calc_sanads=calc_sanads,
            truth_dashboard=dashboard,
        )
        validation_rejection = self._validation_rejection(bundle)
        if validation_rejection is not None:
            return self._failed_result(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                total_claims=len(materialized_claims),
                rejection=validation_rejection,
            ), []

        court = self._build_court(bundle)
        summary = summary_from_court(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            total_claims=len(materialized_claims),
            court=court,
            rejections=[],
        )
        result = MethodologyEvidenceTrustCourtRunResult(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            status=summary.aggregate_status(),
            court_shells=[court.to_shell(summary=summary)],
            role_summaries=list(court.role_summaries),
            rejections=[],
            summary=summary,
        )
        return result, [court]

    def _early_rejection(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        scoped_inputs: list[Any],
        truth_dashboards: list[RunScopedTruthDashboardRecord | RunScopedTruthDashboardShell],
    ) -> MethodologyEvidenceTrustCourtRejection | None:
        if any(
            scope_mismatch(item, tenant_id=tenant_id, deal_id=deal_id, run_id=run_id)
            for item in scoped_inputs
        ):
            return _rejection(
                MethodologyEvidenceTrustCourtReason.TENANT_OR_RUN_MISMATCH,
                "Evidence Trust Court input scope mismatch",
            )
        if not truth_dashboards:
            return _rejection(
                MethodologyEvidenceTrustCourtReason.MISSING_TRUTH_DASHBOARD,
                "Evidence Trust Court requires a full Truth Dashboard record",
            )
        return None

    def _validation_rejection(
        self,
        bundle: CourtInputBundle,
    ) -> MethodologyEvidenceTrustCourtRejection | None:
        provenance_keys = {
            (ref.document_id, ref.source_span_id) for ref in bundle.source_provenance
        }
        evidence_keys = {source_key(record) for record in bundle.evidence_items}
        if not evidence_keys.issubset(provenance_keys):
            return _rejection(
                MethodologyEvidenceTrustCourtReason.MISSING_SOURCE_PROVENANCE,
                "Evidence item document_id/source_span_id is missing from source provenance",
            )

        claim_ids = [claim_id(claim) for claim in bundle.materialized_claims]
        if len(claim_ids) != len(set(claim_ids)):
            return _rejection(
                MethodologyEvidenceTrustCourtReason.DUPLICATE_CLAIM_INPUT,
                "Duplicate claim input for Evidence Trust Court",
            )

        grade_by_claim = {grade.claim_id: grade for grade in bundle.sanad_grades}
        missing_grade = next((item for item in claim_ids if item not in grade_by_claim), None)
        if missing_grade is not None:
            return _rejection(
                MethodologyEvidenceTrustCourtReason.MISSING_SANAD_GRADE,
                "Evidence Trust Court requires a Sanad grade for every claim",
                claim_id=missing_grade,
            )
        return None

    def _build_court(self, bundle: CourtInputBundle) -> RunScopedEvidenceTrustCourtRecord:
        claim_ids = sorted(claim_id(claim) for claim in bundle.materialized_claims)
        calc_ids = sorted(calc_id(calc) for calc in bundle.calculations)
        alias_maps = build_evidence_trust_alias_maps(
            tenant_id=bundle.tenant_id,
            deal_id=bundle.deal_id,
            run_id=bundle.run_id,
            claim_ids=claim_ids,
            calc_ids=calc_ids,
        )
        court_id = deterministic_evidence_trust_court_id(
            tenant_id=bundle.tenant_id,
            deal_id=bundle.deal_id,
            run_id=bundle.run_id,
            claim_ids=claim_ids,
            dashboard_id=bundle.truth_dashboard.dashboard_id,
        )
        assessments, findings = assess_claims(court_id=court_id, bundle=bundle)
        final_state = run_layer1_debate(
            bundle=bundle,
            alias_maps=alias_maps,
            critical_defect_detected=any(
                assessment.disposition == EvidenceTrustDisposition.REJECTED
                for assessment in assessments
            ),
        )
        return RunScopedEvidenceTrustCourtRecord(
            tenant_id=bundle.tenant_id,
            deal_id=bundle.deal_id,
            run_id=bundle.run_id,
            court_id=court_id,
            dashboard_id=bundle.truth_dashboard.dashboard_id,
            claim_assessments=assessments,
            findings=findings,
            role_summaries=role_summaries(final_state, alias_maps),
            stop_reason=final_state.stop_reason,
            status="created",
        )

    def _failed_result(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        total_claims: int,
        rejection: MethodologyEvidenceTrustCourtRejection,
    ) -> MethodologyEvidenceTrustCourtRunResult:
        summary = RunScopedEvidenceTrustCourtSummary(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            total_claims=total_claims,
            assessed_claim_count=0,
            finding_count=0,
            rejected_count=1,
            by_disposition={},
            by_reason={rejection.reason.value: 1},
            by_grade={},
            by_dashboard_verdict={},
        )
        return MethodologyEvidenceTrustCourtRunResult(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            status=MethodologyEvidenceTrustCourtStatus.FAILED,
            court_shells=[],
            role_summaries=[],
            rejections=[rejection],
            summary=summary,
        )


def _rejection(
    reason: MethodologyEvidenceTrustCourtReason,
    message: str,
    *,
    claim_id: str | None = None,
) -> MethodologyEvidenceTrustCourtRejection:
    return MethodologyEvidenceTrustCourtRejection(
        claim_id=claim_id,
        reason=reason,
        reason_codes=[reason.value],
        message=message,
    )
