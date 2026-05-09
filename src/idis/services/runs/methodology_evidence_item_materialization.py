"""Run-scoped EvidenceItem materialization from governed Slice 6 claims."""

from __future__ import annotations

from pydantic import ValidationError

from idis.models.claim_materialization import (
    MaterializedClaimSourceRef,
    RunScopedMaterializedClaim,
    RunScopedMaterializedClaimShell,
)
from idis.models.evidence_item import EvidenceItem, SourceGrade, VerificationStatus
from idis.models.evidence_item_materialization import (
    EvidenceItemMaterializationReason,
    MethodologyEvidenceItemMapping,
    MethodologyEvidenceItemMaterializationRunResult,
    MethodologyEvidenceItemMaterializationSummary,
    MethodologyEvidenceItemRejection,
    RunScopedEvidenceItemRecord,
    RunScopedEvidenceProvenanceRef,
    aggregate_status,
    counter,
    evidence_item_source_span_id,
    generate_methodology_evidence_item_id,
)


class InMemoryRunMethodologyEvidenceItemMaterializationService:
    """Materialize Slice 6 run-scoped claims into in-memory EvidenceItems."""

    def run(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        materialized_claims: list[RunScopedMaterializedClaim | RunScopedMaterializedClaimShell],
    ) -> tuple[MethodologyEvidenceItemMaterializationRunResult, list[RunScopedEvidenceItemRecord]]:
        """Convert materialized claim source refs into governed EvidenceItem records."""
        mappings: list[MethodologyEvidenceItemMapping] = []
        rejections: list[MethodologyEvidenceItemRejection] = []
        records: list[RunScopedEvidenceItemRecord] = []
        seen_claim_source_refs: set[tuple[str, str, str, str]] = set()

        for claim in sorted(materialized_claims, key=_claim_sort_key):
            claim_id = getattr(claim, "claim_id", None)
            extraction_output_id = claim.extraction_output_id

            context_reason = _claim_context_rejection(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                claim=claim,
            )
            if context_reason is not None:
                rejections.append(
                    _rejection(
                        claim_id=claim_id,
                        extraction_output_id=extraction_output_id,
                        reason=context_reason,
                    )
                )
                continue

            if claim_id is None or not claim_id.strip():
                rejections.append(
                    _rejection(
                        claim_id=claim_id,
                        extraction_output_id=extraction_output_id,
                        reason=EvidenceItemMaterializationReason.MISSING_CLAIM_ID,
                    )
                )
                continue

            if not claim.source_refs:
                rejections.append(
                    _rejection(
                        claim_id=claim_id,
                        extraction_output_id=extraction_output_id,
                        reason=EvidenceItemMaterializationReason.MISSING_SOURCE_REFS,
                    )
                )
                continue

            for source_ref in claim.source_refs:
                dedupe_key = (
                    claim_id,
                    source_ref.document_id,
                    source_ref.source_span_id,
                    extraction_output_id,
                )
                if dedupe_key in seen_claim_source_refs:
                    rejections.append(
                        _rejection(
                            claim_id=claim_id,
                            extraction_output_id=extraction_output_id,
                            reason=EvidenceItemMaterializationReason.DUPLICATE_CLAIM_SOURCE_REF,
                        )
                    )
                    continue
                seen_claim_source_refs.add(dedupe_key)

                try:
                    record = _record_from_claim_source_ref(
                        tenant_id=tenant_id,
                        deal_id=deal_id,
                        run_id=run_id,
                        claim=claim,
                        claim_id=claim_id,
                        source_ref=source_ref,
                    )
                except _EvidenceItemMaterializationError as exc:
                    rejections.append(
                        _rejection(
                            claim_id=claim_id,
                            extraction_output_id=extraction_output_id,
                            reason=exc.reason,
                        )
                    )
                    continue

                records.append(record)
                mappings.append(MethodologyEvidenceItemMapping.from_record(record))

        return _result(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            total_claims=len(materialized_claims),
            total_source_refs=sum(len(claim.source_refs) for claim in materialized_claims),
            mappings=mappings,
            rejections=rejections,
            records=records,
        )


class _EvidenceItemMaterializationError(ValueError):
    def __init__(self, reason: EvidenceItemMaterializationReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


def _claim_sort_key(
    claim: RunScopedMaterializedClaim | RunScopedMaterializedClaimShell,
) -> tuple[str, str, str, str]:
    claim_id = getattr(claim, "claim_id", None) or ""
    return (
        claim_id,
        claim.extraction_output_id,
        claim.extraction_task_id,
        claim.methodology_question_id,
    )


def _claim_context_rejection(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    claim: RunScopedMaterializedClaim | RunScopedMaterializedClaimShell,
) -> EvidenceItemMaterializationReason | None:
    if claim.tenant_id != tenant_id or claim.deal_id != deal_id or claim.run_id != run_id:
        return EvidenceItemMaterializationReason.TENANT_OR_RUN_MISMATCH
    return None


def _record_from_claim_source_ref(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    claim: RunScopedMaterializedClaim | RunScopedMaterializedClaimShell,
    claim_id: str,
    source_ref: MaterializedClaimSourceRef,
) -> RunScopedEvidenceItemRecord:
    try:
        provenance_ref = RunScopedEvidenceProvenanceRef.model_validate(
            source_ref.model_dump(mode="python")
        )
        evidence_id = generate_methodology_evidence_item_id(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            claim_id=claim_id,
            extraction_output_id=claim.extraction_output_id,
            extraction_task_id=claim.extraction_task_id,
            methodology_question_id=claim.methodology_question_id,
            coverage_record_id=claim.coverage_record_id,
            source_ref=provenance_ref,
        )
        evidence_item = EvidenceItem(
            evidence_id=evidence_id,
            tenant_id=tenant_id,
            deal_id=deal_id,
            source_span_id=evidence_item_source_span_id(provenance_ref.source_span_id),
            source_system="methodology_claim_materialization",
            upstream_origin_id=provenance_ref.source_span_id,
            verification_status=VerificationStatus.UNVERIFIED,
            source_grade=SourceGrade.D,
            rationale={
                "claim_id": claim_id,
                "run_id": run_id,
                "methodology_question_id": claim.methodology_question_id,
                "coverage_record_id": claim.coverage_record_id,
                "extraction_task_id": claim.extraction_task_id,
                "extraction_output_id": claim.extraction_output_id,
                "document_id": provenance_ref.document_id,
                "source_span_id": provenance_ref.source_span_id,
                "source": "slice_7_methodology_source_provenance",
            },
        )
        return RunScopedEvidenceItemRecord(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            claim_id=claim_id,
            evidence_item=evidence_item,
            source_ref=provenance_ref,
            methodology_question_id=claim.methodology_question_id,
            coverage_record_id=claim.coverage_record_id,
            extraction_task_id=claim.extraction_task_id,
            extraction_output_id=claim.extraction_output_id,
            status="materialized_unverified",
        )
    except ValidationError as exc:
        raise _EvidenceItemMaterializationError(
            EvidenceItemMaterializationReason.UNSAFE_SOURCE_REF
        ) from exc


def _rejection(
    *,
    claim_id: str | None,
    extraction_output_id: str | None,
    reason: EvidenceItemMaterializationReason,
) -> MethodologyEvidenceItemRejection:
    return MethodologyEvidenceItemRejection(
        claim_id=claim_id,
        extraction_output_id=extraction_output_id,
        reason=reason,
        reason_codes=[reason.value],
        message=reason.value,
    )


def _result(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    total_claims: int,
    total_source_refs: int,
    mappings: list[MethodologyEvidenceItemMapping],
    rejections: list[MethodologyEvidenceItemRejection],
    records: list[RunScopedEvidenceItemRecord],
) -> tuple[MethodologyEvidenceItemMaterializationRunResult, list[RunScopedEvidenceItemRecord]]:
    summary = MethodologyEvidenceItemMaterializationSummary(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        total_claims=total_claims,
        total_source_refs=total_source_refs,
        created_evidence_count=len(mappings),
        rejected_source_ref_count=len(rejections),
        by_status=counter(["completed"] * len(mappings) + ["rejected"] * len(rejections)),
        by_reason=counter(rejection.reason.value for rejection in rejections),
    )
    return (
        MethodologyEvidenceItemMaterializationRunResult(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            status=aggregate_status(mappings=mappings, rejections=rejections),
            evidence_item_mappings=mappings,
            rejected_source_refs=rejections,
            summary=summary,
        ),
        records,
    )
