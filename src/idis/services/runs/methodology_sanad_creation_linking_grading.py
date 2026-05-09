"""Slice 8 in-memory Sanad creation/linking/grading run service."""

from __future__ import annotations

from idis.models.claim_materialization import (
    RunScopedMaterializedClaim,
    RunScopedMaterializedClaimShell,
)
from idis.models.defect import DefectSeverity
from idis.models.evidence_item_materialization import (
    RunScopedEvidenceItemRecord,
    RunScopedEvidenceItemShell,
    RunScopedEvidenceProvenanceRef,
)
from idis.models.sanad import Sanad, SanadGrade
from idis.models.sanad_materialization import (
    MethodologySanadMapping,
    MethodologySanadMaterializationRunResult,
    MethodologySanadReason,
    MethodologySanadRejection,
    RunScopedSanadDefectRecord,
    RunScopedSanadGradeRecord,
    RunScopedSanadLinkRecord,
    RunScopedSanadRecord,
    aggregate_status,
    deterministic_sanad_id,
)
from idis.services.runs.methodology_sanad_creation_helpers import (
    DEFAULT_CONFIDENCE,
    SanadMaterializationError,
    build_transmission_chain,
    claim_for_grader,
    claim_scope_matches,
    claim_sort_key,
    corroboration_status,
    evidence_id,
    evidence_scope_matches,
    evidence_source_provenance_key,
    evidence_source_span_id,
    grade_reason_codes,
    group_evidence_by_claim,
    materialize_defects,
    rejection,
    source_for_grader,
    source_provenance_keys,
    summary,
)
from idis.services.sanad.grader import grade_sanad_v2


class InMemoryRunMethodologySanadCreationLinkingGradingService:
    """Create, link, and grade run-scoped Sanads without durable persistence."""

    def run(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        materialized_claims: list[RunScopedMaterializedClaim | RunScopedMaterializedClaimShell],
        evidence_items: list[RunScopedEvidenceItemRecord | RunScopedEvidenceItemShell],
        source_provenance: list[RunScopedEvidenceProvenanceRef],
    ) -> tuple[
        MethodologySanadMaterializationRunResult,
        list[RunScopedSanadRecord],
        list[RunScopedSanadLinkRecord],
        list[RunScopedSanadGradeRecord],
        list[RunScopedSanadDefectRecord],
    ]:
        """Run Slice 8 Sanad creation/linking/grading in memory."""
        rejections: list[MethodologySanadRejection] = []
        records: list[RunScopedSanadRecord] = []
        links: list[RunScopedSanadLinkRecord] = []
        grades: list[RunScopedSanadGradeRecord] = []
        defects: list[RunScopedSanadDefectRecord] = []

        if not materialized_claims and not evidence_items and not source_provenance:
            return self._empty_result(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                total_claims=0,
                total_evidence_items=0,
                rejections=[],
            )

        if not materialized_claims:
            rejections.append(
                rejection(
                    reason=MethodologySanadReason.MISSING_MATERIALIZED_CLAIMS,
                    message="materialized claims are required for Slice 8 Sanad creation",
                )
            )
            return self._empty_result(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                total_claims=0,
                total_evidence_items=len(evidence_items),
                rejections=rejections,
            )

        if not evidence_items:
            rejections.append(
                rejection(
                    reason=MethodologySanadReason.MISSING_EVIDENCE_ITEMS,
                    message="evidence items are required for Slice 8 Sanad creation",
                )
            )
            return self._empty_result(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                total_claims=len(materialized_claims),
                total_evidence_items=0,
                rejections=rejections,
            )

        if not source_provenance:
            rejections.append(
                rejection(
                    reason=MethodologySanadReason.MISSING_SOURCE_PROVENANCE,
                    message="source provenance is required for Slice 8 Sanad creation",
                )
            )
            return self._empty_result(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                total_claims=len(materialized_claims),
                total_evidence_items=len(evidence_items),
                rejections=rejections,
            )

        evidence_by_claim = group_evidence_by_claim(evidence_items)
        provenance_keys = source_provenance_keys(source_provenance)
        seen_inputs: set[tuple[str, tuple[str, ...], str]] = set()

        for claim in sorted(materialized_claims, key=claim_sort_key):
            claim_id = claim.claim_id
            if not claim_id:
                rejections.append(
                    rejection(
                        reason=MethodologySanadReason.MISSING_CLAIM_ID,
                        message="materialized claim is missing claim_id",
                    )
                )
                continue

            if not claim_scope_matches(
                claim,
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
            ):
                rejections.append(
                    rejection(
                        claim_id=claim_id,
                        reason=MethodologySanadReason.TENANT_OR_RUN_MISMATCH,
                        message="materialized claim scope does not match run scope",
                    )
                )
                continue

            claim_evidence = evidence_by_claim.get(claim_id, [])
            if not claim_evidence:
                rejections.append(
                    rejection(
                        claim_id=claim_id,
                        reason=MethodologySanadReason.MISSING_CLAIM_EVIDENCE,
                        message="no evidence items matched materialized claim",
                    )
                )
                continue

            if not all(
                evidence_scope_matches(
                    evidence,
                    tenant_id=tenant_id,
                    deal_id=deal_id,
                    run_id=run_id,
                )
                for evidence in claim_evidence
            ):
                rejections.append(
                    rejection(
                        claim_id=claim_id,
                        reason=MethodologySanadReason.TENANT_OR_RUN_MISMATCH,
                        message="evidence item scope does not match run scope",
                    )
                )
                continue

            evidence_ids = sorted(evidence_id(evidence) for evidence in claim_evidence)
            source_span_ids = sorted(
                {
                    source_span_id
                    for evidence in claim_evidence
                    if (source_span_id := evidence_source_span_id(evidence))
                }
            )
            if not all(
                evidence_source_provenance_key(evidence) in provenance_keys
                for evidence in claim_evidence
            ):
                rejections.append(
                    rejection(
                        claim_id=claim_id,
                        reason=MethodologySanadReason.MISSING_SOURCE_PROVENANCE,
                        message="evidence source span is not backed by Slice 7 provenance",
                    )
                )
                continue
            input_key = (claim_id, tuple(evidence_ids), claim.extraction_output_id)
            if input_key in seen_inputs:
                continue
            seen_inputs.add(input_key)

            try:
                record, link, grade, record_defects = self._materialize_claim_sanad(
                    tenant_id=tenant_id,
                    deal_id=deal_id,
                    run_id=run_id,
                    claim=claim,
                    evidence_items=claim_evidence,
                    evidence_ids=evidence_ids,
                    source_span_ids=source_span_ids,
                )
            except SanadMaterializationError as exc:
                rejections.append(
                    rejection(
                        claim_id=claim_id,
                        reason=exc.reason,
                        message=exc.message,
                    )
                )
                continue

            records.append(record)
            links.append(link)
            grades.append(grade)
            defects.extend(record_defects)

        mappings = [
            MethodologySanadMapping.from_record(
                record,
                defect_ids=[
                    defect.defect.defect_id
                    for defect in defects
                    if defect.sanad_id == record.sanad.sanad_id
                ],
            )
            for record in records
        ]
        defect_shells = [defect.to_shell() for defect in defects]
        status = aggregate_status(mappings=mappings, rejections=rejections)
        run_result = MethodologySanadMaterializationRunResult(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            status=status,
            sanad_mappings=mappings,
            claim_sanad_links=links,
            grade_records=grades,
            defect_shells=defect_shells,
            rejections=rejections,
            summary=summary(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                total_claims=len(materialized_claims),
                total_evidence_items=len(evidence_items),
                mappings=mappings,
                links=links,
                grades=grades,
                defects=defects,
                rejections=rejections,
            ),
        )
        return run_result, records, links, grades, defects

    def _materialize_claim_sanad(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        claim: RunScopedMaterializedClaim | RunScopedMaterializedClaimShell,
        evidence_items: list[RunScopedEvidenceItemRecord | RunScopedEvidenceItemShell],
        evidence_ids: list[str],
        source_span_ids: list[str],
    ) -> tuple[
        RunScopedSanadRecord,
        RunScopedSanadLinkRecord,
        RunScopedSanadGradeRecord,
        list[RunScopedSanadDefectRecord],
    ]:
        claim_id = claim.claim_id
        if claim_id is None:
            raise SanadMaterializationError(
                MethodologySanadReason.MISSING_CLAIM_ID,
                "materialized claim is missing claim_id",
            )
        sanad_id = deterministic_sanad_id(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            claim_id=claim_id,
            evidence_ids=evidence_ids,
            source_span_ids=source_span_ids,
            extraction_output_id=claim.extraction_output_id,
            extraction_task_id=claim.extraction_task_id,
            methodology_question_id=claim.methodology_question_id,
            coverage_record_id=claim.coverage_record_id,
        )
        transmission_chain = build_transmission_chain(
            sanad_id=sanad_id,
            claim_id=claim_id,
            evidence_ids=evidence_ids,
            source_span_ids=source_span_ids,
        )
        sources = [source_for_grader(evidence) for evidence in evidence_items]
        sanad_grader_input = {
            "sanad_id": sanad_id,
            "claim_id": claim_id,
            "primary_evidence_id": evidence_ids[0],
            "corroborating_evidence_ids": evidence_ids[1:],
            "transmission_chain": [node.model_dump(mode="json") for node in transmission_chain],
            "primary_source": sources[0] if sources else {},
            "extraction_confidence": DEFAULT_CONFIDENCE,
            "dhabt_score": DEFAULT_CONFIDENCE,
        }
        try:
            grade_result = grade_sanad_v2(
                sanad=sanad_grader_input,
                sources=sources,
                claim=claim_for_grader(claim),
                evidence_ids=set(evidence_ids),
            )
        except Exception as exc:  # noqa: BLE001 - fail closed on third-party grader errors.
            raise SanadMaterializationError(
                MethodologySanadReason.GRADING_FAILED,
                "Sanad grading failed closed",
            ) from exc

        record_defects = materialize_defects(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            sanad_id=sanad_id,
            claim_id=claim_id,
            evidence_ids=evidence_ids,
            grader_defects=list(grade_result.all_defects),
        )
        sanad_grade = SanadGrade(str(grade_result.grade))
        defect_payloads = [record.defect for record in record_defects]
        sanad = Sanad(
            sanad_id=sanad_id,
            tenant_id=tenant_id,
            deal_id=deal_id,
            claim_id=claim_id,
            primary_evidence_id=evidence_ids[0],
            corroborating_evidence_ids=evidence_ids[1:],
            extraction_confidence=DEFAULT_CONFIDENCE,
            dhabt_score=DEFAULT_CONFIDENCE,
            corroboration_status=corroboration_status(len(evidence_ids)),
            sanad_grade=sanad_grade,
            grade_explanation=[grade_result.explanation.to_dict()],
            transmission_chain=transmission_chain,
            defects=defect_payloads,
        )
        record = RunScopedSanadRecord(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            claim_id=claim_id,
            sanad=sanad,
            evidence_ids=evidence_ids,
            source_span_ids=source_span_ids,
            methodology_question_id=claim.methodology_question_id,
            coverage_record_id=claim.coverage_record_id,
            extraction_task_id=claim.extraction_task_id,
            extraction_output_id=claim.extraction_output_id,
            status="created_linked_graded",
        )
        link = RunScopedSanadLinkRecord(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            claim_id=claim_id,
            sanad_id=sanad_id,
            evidence_ids=evidence_ids,
            source_span_ids=source_span_ids,
            claim_link_status="linked_run_scoped",
        )
        grade = RunScopedSanadGradeRecord(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            claim_id=claim_id,
            sanad_id=sanad_id,
            sanad_grade=sanad_grade,
            grade_reason_codes=grade_reason_codes(grade_result),
            defect_ids=[record.defect.defect_id for record in record_defects],
            fatal_defect_count=sum(
                1 for record in record_defects if record.defect.severity == DefectSeverity.FATAL
            ),
            major_defect_count=sum(
                1 for record in record_defects if record.defect.severity == DefectSeverity.MAJOR
            ),
            minor_defect_count=sum(
                1 for record in record_defects if record.defect.severity == DefectSeverity.MINOR
            ),
        )
        return record, link, grade, record_defects

    def _empty_result(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        total_claims: int,
        total_evidence_items: int,
        rejections: list[MethodologySanadRejection],
    ) -> tuple[
        MethodologySanadMaterializationRunResult,
        list[RunScopedSanadRecord],
        list[RunScopedSanadLinkRecord],
        list[RunScopedSanadGradeRecord],
        list[RunScopedSanadDefectRecord],
    ]:
        run_result = MethodologySanadMaterializationRunResult(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            status=aggregate_status(mappings=[], rejections=rejections),
            sanad_mappings=[],
            claim_sanad_links=[],
            grade_records=[],
            defect_shells=[],
            rejections=rejections,
            summary=summary(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                total_claims=total_claims,
                total_evidence_items=total_evidence_items,
                mappings=[],
                links=[],
                grades=[],
                defects=[],
                rejections=rejections,
            ),
        )
        return run_result, [], [], [], []
