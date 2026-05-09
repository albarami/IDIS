"""Slice 10 run-scoped in-memory Truth Dashboard service."""

from __future__ import annotations

from idis.deliverables.truth_dashboard import TruthDashboardBuilder
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
from idis.models.sanad_materialization import (
    RunScopedSanadDefectRecord,
    RunScopedSanadDefectShell,
    RunScopedSanadGradeRecord,
    RunScopedSanadRecord,
    RunScopedSanadShell,
)
from idis.models.truth_dashboard_materialization import (
    MethodologyTruthDashboardMapping,
    MethodologyTruthDashboardReason,
    MethodologyTruthDashboardRejection,
    MethodologyTruthDashboardRunResult,
    RunScopedTruthDashboardRecord,
    deterministic_truth_dashboard_id,
    deterministic_truth_dashboard_row_id,
)
from idis.services.runs.methodology_truth_dashboard_helpers import (
    calc_ids_by_claim as build_calc_ids_by_claim,
)
from idis.services.runs.methodology_truth_dashboard_helpers import (
    candidate_for_claim,
    filter_scoped,
    has_scope_mismatch,
    rejection,
    run_result,
    sanad_confidence,
)
from idis.services.runs.methodology_truth_dashboard_helpers import (
    defects_by_claim as build_defects_by_claim,
)
from idis.services.runs.methodology_truth_dashboard_helpers import (
    evidence_by_claim as build_evidence_by_claim,
)
from idis.services.runs.methodology_truth_dashboard_helpers import (
    sanad_by_claim as build_sanad_by_claim,
)
from idis.validators.deliverable import validate_deliverable_no_free_facts

_GENERATED_AT = "1970-01-01T00:00:00+00:00"


class InMemoryRunMethodologyTruthDashboardService:
    """Build run-scoped Truth Dashboard records from Slice 6-9 outputs."""

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
    ) -> tuple[MethodologyTruthDashboardRunResult, list[RunScopedTruthDashboardRecord]]:
        """Create an in-memory Truth Dashboard and safe run-step result."""
        if not materialized_claims:
            result = run_result(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                total_claims=0,
                mappings=[],
                rejections=[],
                shells=[],
            )
            return result, []

        full_claims: list[RunScopedMaterializedClaim] = []
        rejections: list[MethodologyTruthDashboardRejection] = []
        seen_claim_ids: set[str] = set()
        for claim in materialized_claims:
            if isinstance(claim, RunScopedMaterializedClaimShell):
                rejections.append(
                    rejection(
                        claim_id=claim.claim_id,
                        reason=MethodologyTruthDashboardReason.SHELL_ONLY_INPUT,
                        message="Truth Dashboard requires full materialized claims for assertions",
                    )
                )
                continue
            if claim.tenant_id != tenant_id or claim.deal_id != deal_id or claim.run_id != run_id:
                rejections.append(
                    rejection(
                        claim_id=claim.claim_id,
                        reason=MethodologyTruthDashboardReason.TENANT_OR_RUN_MISMATCH,
                        message="Claim scope does not match Truth Dashboard run scope",
                    )
                )
                continue
            if claim.claim_id is None:
                rejections.append(
                    rejection(
                        claim_id=None,
                        reason=MethodologyTruthDashboardReason.MISSING_CLAIM_ID,
                        message="Materialized claim is missing claim_id",
                    )
                )
                continue
            if claim.claim_id in seen_claim_ids:
                rejections.append(
                    rejection(
                        claim_id=claim.claim_id,
                        reason=MethodologyTruthDashboardReason.DUPLICATE_CLAIM_ROW,
                        message="Duplicate claim would create duplicate Truth Dashboard row",
                    )
                )
                continue
            seen_claim_ids.add(claim.claim_id)
            full_claims.append(claim)

        scoped_evidence_items = filter_scoped(
            records=evidence_items,
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            rejections=rejections,
        )
        scoped_sanads = filter_scoped(
            records=sanads,
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            rejections=rejections,
        )
        scoped_grades = filter_scoped(
            records=sanad_grades,
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            rejections=rejections,
        )
        scoped_defects = filter_scoped(
            records=sanad_defects,
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            rejections=rejections,
        )
        scoped_calculations = filter_scoped(
            records=calculations,
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            rejections=rejections,
        )
        filter_scoped(
            records=calc_sanads,
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            rejections=rejections,
        )

        if has_scope_mismatch(rejections):
            result = run_result(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                total_claims=len(materialized_claims),
                mappings=[],
                rejections=rejections,
                shells=[],
            )
            return result, []

        evidence_by_claim = build_evidence_by_claim(scoped_evidence_items)
        provenance_keys = {(ref.document_id, ref.source_span_id) for ref in source_provenance}
        sanad_by_claim = build_sanad_by_claim(scoped_sanads)
        grade_by_claim = {grade.claim_id: grade for grade in scoped_grades}
        defects_by_claim = build_defects_by_claim(scoped_defects)
        calc_ids_by_claim = build_calc_ids_by_claim(scoped_calculations)

        candidates = []
        for claim in sorted(full_claims, key=lambda item: item.claim_id or ""):
            candidate = candidate_for_claim(
                claim=claim,
                evidence_by_claim=evidence_by_claim,
                provenance_keys=provenance_keys,
                sanad_by_claim=sanad_by_claim,
                grade_by_claim=grade_by_claim,
                defects_by_claim=defects_by_claim,
                calc_ids_by_claim=calc_ids_by_claim,
            )
            if isinstance(candidate, MethodologyTruthDashboardRejection):
                rejections.append(candidate)
            else:
                candidates.append(candidate)

        if not candidates:
            result = run_result(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                total_claims=len(materialized_claims),
                mappings=[],
                rejections=rejections,
                shells=[],
            )
            return result, []

        dashboard_id = deterministic_truth_dashboard_id(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            claim_ids=[candidate.claim.claim_id or "" for candidate in candidates],
        )
        builder = TruthDashboardBuilder(
            deliverable_id=dashboard_id,
            tenant_id=tenant_id,
            deal_id=deal_id,
            deal_name=deal_id,
            generated_at=_GENERATED_AT,
        )
        mappings: list[MethodologyTruthDashboardMapping] = []
        for candidate in candidates:
            claim_id = candidate.claim.claim_id or ""
            builder.add_row(
                dimension=candidate.claim.claim_type.value,
                assertion=candidate.claim.claim_text,
                verdict=candidate.verdict.value,
                claim_refs=[claim_id],
                calc_refs=candidate.calc_ids,
                sanad_grade=candidate.sanad_grade.value,
                confidence=sanad_confidence(sanad_by_claim.get(claim_id)),
            )
            mappings.append(
                MethodologyTruthDashboardMapping(
                    dashboard_id=dashboard_id,
                    row_id=deterministic_truth_dashboard_row_id(
                        dashboard_id=dashboard_id,
                        claim_id=claim_id,
                        sanad_id=candidate.sanad_id,
                        evidence_ids=candidate.evidence_ids,
                        calc_ids=candidate.calc_ids,
                    ),
                    claim_id=claim_id,
                    evidence_ids=candidate.evidence_ids,
                    sanad_id=candidate.sanad_id,
                    calc_ids=candidate.calc_ids,
                    defect_ids=candidate.defect_ids,
                    sanad_grade=candidate.sanad_grade,
                    verdict=candidate.verdict,
                    methodology_question_id=candidate.claim.methodology_question_id,
                    coverage_record_id=candidate.claim.coverage_record_id,
                    extraction_task_id=candidate.claim.extraction_task_id,
                    extraction_output_id=candidate.claim.extraction_output_id,
                    status="created",
                )
            )

        dashboard = builder.build()
        validation = validate_deliverable_no_free_facts(dashboard, raise_on_failure=False)
        if not validation.passed:
            validation_rejection = rejection(
                claim_id=None,
                reason=MethodologyTruthDashboardReason.TRUTH_DASHBOARD_VALIDATION_FAILED,
                message="Truth Dashboard failed No-Free-Facts validation",
            )
            result = run_result(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                total_claims=len(materialized_claims),
                mappings=[],
                rejections=[*rejections, validation_rejection],
                shells=[],
            )
            return result, []

        record = RunScopedTruthDashboardRecord(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            dashboard_id=dashboard_id,
            dashboard=dashboard,
            row_mappings=mappings,
            status="created",
        )
        result = run_result(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            total_claims=len(materialized_claims),
            mappings=mappings,
            rejections=rejections,
            shells=[record.to_shell()],
        )
        return result, [record]
