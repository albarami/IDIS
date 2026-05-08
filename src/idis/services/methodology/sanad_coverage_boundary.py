"""Decision-only Sanad readiness and methodology coverage boundary service."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence

from idis.models.claim_materialization import ClaimMaterializationResult, DraftClaimMapping
from idis.models.extraction_execution import MethodologyExtractionExecutionResult
from idis.models.methodology_coverage import (
    MethodologyAnswer,
    MethodologyCoverageRecord,
    MethodologyCoverageStatus,
    MethodologyEvidenceLink,
)
from idis.models.sanad_coverage_boundary import (
    CoverageUpdateDecision,
    MethodologyClaimEvidenceReference,
    SanadCoverageBoundaryReason,
    SanadCoverageBoundaryResult,
    SanadCoverageBoundaryStatus,
    SanadCoverageBoundarySummary,
    SanadReadinessDecision,
)
from idis.services.methodology.coverage import InMemoryMethodologyCoverageService

_CONTEXT_QUESTION_ID = "__boundary_context__"
_MISSING_QUESTION_ID = "__missing_methodology_question_id__"


class InvalidCoverageDecisionScopeError(ValueError):
    """Raised when a coverage decision cannot be matched by full run scope."""


class SanadCoverageBoundaryService:
    """Build deterministic boundary decisions without live downstream mutation."""

    def build_decisions(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        materialization_result: ClaimMaterializationResult,
        coverage_records: Sequence[MethodologyCoverageRecord],
        evidence_references: Sequence[MethodologyClaimEvidenceReference],
        execution_result: MethodologyExtractionExecutionResult | None = None,
    ) -> SanadCoverageBoundaryResult:
        """Build Sanad readiness and coverage decisions for synthetic inputs only."""
        context_reason = _context_mismatch_reason(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            materialization_result=materialization_result,
            coverage_records=coverage_records,
            evidence_references=evidence_references,
            execution_result=execution_result,
        )
        mappings = sorted(
            materialization_result.draft_claim_mappings,
            key=lambda mapping: (
                mapping.methodology_question_id,
                mapping.claim_id,
                mapping.methodology_claim_draft_id,
            ),
        )
        if context_reason is not None:
            context_coverage_decisions = [
                _blocked_coverage_decision(
                    tenant_id=tenant_id,
                    deal_id=deal_id,
                    run_id=run_id,
                    methodology_question_id=_question_id_for_mapping(mapping),
                    claim_id=mapping.claim_id,
                    source_span_ids=mapping.source_span_ids,
                    reason=context_reason,
                    message="boundary inputs are not scoped to the same tenant, deal, and run",
                )
                for mapping in mappings
            ] or [
                _blocked_coverage_decision(
                    tenant_id=tenant_id,
                    deal_id=deal_id,
                    run_id=run_id,
                    methodology_question_id=_CONTEXT_QUESTION_ID,
                    claim_id=None,
                    source_span_ids=[],
                    reason=context_reason,
                    message="boundary context is outside the requested tenant, deal, or run",
                )
            ]
            return _result(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                total_claim_mappings=len(mappings),
                readiness_decisions=[],
                coverage_decisions=context_coverage_decisions,
            )

        duplicate_context = _duplicate_conflicting_mapping_context(mappings)
        if duplicate_context is not None:
            duplicate_question_id, duplicate_mappings = duplicate_context
            duplicate_coverage_decisions = [
                _coverage_decision(
                    tenant_id=tenant_id,
                    deal_id=deal_id,
                    run_id=run_id,
                    methodology_question_id=duplicate_question_id,
                    target_status=MethodologyCoverageStatus.BLOCKED,
                    claim_ids=[mapping.claim_id for mapping in duplicate_mappings],
                    evidence_ids=[],
                    source_span_ids=[
                        source_span_id
                        for mapping in duplicate_mappings
                        for source_span_id in mapping.source_span_ids
                    ],
                    calc_ids=[],
                    evidence_links=[
                        MethodologyEvidenceLink(claim_id=mapping.claim_id)
                        for mapping in duplicate_mappings
                    ],
                    answer=None,
                    conflict_ids=[],
                    defect_ids=[],
                    reason=SanadCoverageBoundaryReason.DUPLICATE_CONFLICTING_MAPPING,
                    message="multiple conflicting claim mappings target the same question",
                )
            ]
            return _result(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                total_claim_mappings=len(mappings),
                readiness_decisions=[],
                coverage_decisions=duplicate_coverage_decisions,
            )

        readiness_decisions: list[SanadReadinessDecision] = []
        coverage_decisions: list[CoverageUpdateDecision] = []
        for mapping in mappings:
            readiness, coverage = _decisions_for_mapping(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                mapping=mapping,
                evidence_references=evidence_references,
            )
            readiness_decisions.append(readiness)
            coverage_decisions.append(coverage)

        return _result(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            total_claim_mappings=len(mappings),
            readiness_decisions=readiness_decisions,
            coverage_decisions=coverage_decisions,
        )

    def apply_decisions_in_memory(
        self,
        *,
        coverage_service: InMemoryMethodologyCoverageService,
        coverage_records: Sequence[MethodologyCoverageRecord],
        decisions: Sequence[CoverageUpdateDecision],
    ) -> list[MethodologyCoverageRecord]:
        """Apply decisions to an explicitly injected synthetic in-memory ledger."""
        records_by_scope = {
            _coverage_scope_key(record): record for record in coverage_records
        }
        updated_records: list[MethodologyCoverageRecord] = []
        for decision in sorted(decisions, key=_coverage_scope_key):
            record = records_by_scope.get(_coverage_scope_key(decision))
            if record is None:
                raise InvalidCoverageDecisionScopeError(
                    "coverage decision does not match a full tenant/deal/run/question record"
                )
            updated_records.append(
                coverage_service.update_status(
                    record.coverage_record_id,
                    decision.target_status,
                    reason_code=decision.reason.value,
                    evidence_links=decision.evidence_links,
                    answer=decision.answer,
                    conflict_ids=decision.conflict_ids,
                    defect_ids=decision.defect_ids,
                )
            )
        return updated_records


def _decisions_for_mapping(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    mapping: DraftClaimMapping,
    evidence_references: Sequence[MethodologyClaimEvidenceReference],
) -> tuple[SanadReadinessDecision, CoverageUpdateDecision]:
    question_id = _question_id_for_mapping(mapping)
    if not mapping.methodology_question_id.strip():
        reason = SanadCoverageBoundaryReason.MISSING_METHODOLOGY_LINKAGE
        readiness = _readiness_decision(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            methodology_question_id=question_id,
            claim_id=mapping.claim_id,
            source_span_ids=mapping.source_span_ids,
            evidence_ids=[],
            calc_ids=[],
            ready=False,
            reason=reason,
            message="claim mapping is missing methodology_question_id",
        )
        coverage = _blocked_coverage_decision(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            methodology_question_id=question_id,
            claim_id=mapping.claim_id,
            source_span_ids=mapping.source_span_ids,
            reason=reason,
            message="coverage cannot be decided without methodology linkage",
        )
        return readiness, coverage

    candidates = [
        reference
        for reference in evidence_references
        if reference.methodology_question_id == mapping.methodology_question_id
        and (reference.claim_id is None or reference.claim_id == mapping.claim_id)
    ]
    scoped_candidates = [
        reference
        for reference in candidates
        if _same_scope(
            reference,
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
        )
    ]
    if candidates and not scoped_candidates:
        reason = SanadCoverageBoundaryReason.TENANT_OR_RUN_MISMATCH
        readiness = _readiness_decision(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            methodology_question_id=mapping.methodology_question_id,
            claim_id=mapping.claim_id,
            source_span_ids=mapping.source_span_ids,
            evidence_ids=[],
            calc_ids=[],
            ready=False,
            reason=reason,
            message="evidence reference is outside requested scope",
        )
        coverage = _blocked_coverage_decision(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            methodology_question_id=mapping.methodology_question_id,
            claim_id=mapping.claim_id,
            source_span_ids=mapping.source_span_ids,
            reason=reason,
            message="coverage decision blocked by scope mismatch",
        )
        return readiness, coverage

    span_ids = set(mapping.source_span_ids)
    mismatched_scoped_candidates = [
        reference
        for reference in scoped_candidates
        if reference.claim_id == mapping.claim_id and reference.source_span_id not in span_ids
    ]
    if mismatched_scoped_candidates:
        reason = SanadCoverageBoundaryReason.SOURCE_SPAN_MISMATCH
        readiness = _readiness_decision(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            methodology_question_id=mapping.methodology_question_id,
            claim_id=mapping.claim_id,
            source_span_ids=mapping.source_span_ids,
            evidence_ids=[
                reference.evidence_id for reference in mismatched_scoped_candidates
            ],
            calc_ids=[],
            ready=False,
            reason=reason,
            message="scoped evidence includes source spans outside the claim mapping",
        )
        coverage = _blocked_coverage_decision(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            methodology_question_id=mapping.methodology_question_id,
            claim_id=mapping.claim_id,
            source_span_ids=mapping.source_span_ids,
            reason=reason,
            message="coverage decision blocked by scoped source-span mismatch",
        )
        return readiness, coverage

    matching_references = [
        reference
        for reference in scoped_candidates
        if reference.source_span_id in span_ids
    ]
    if scoped_candidates and not matching_references:
        reason = SanadCoverageBoundaryReason.SOURCE_SPAN_MISMATCH
        readiness = _readiness_decision(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            methodology_question_id=mapping.methodology_question_id,
            claim_id=mapping.claim_id,
            source_span_ids=mapping.source_span_ids,
            evidence_ids=[],
            calc_ids=[],
            ready=False,
            reason=reason,
            message="evidence is not linked to one of the claim source spans",
        )
        coverage = _blocked_coverage_decision(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            methodology_question_id=mapping.methodology_question_id,
            claim_id=mapping.claim_id,
            source_span_ids=mapping.source_span_ids,
            reason=reason,
            message="coverage decision blocked by source-span mismatch",
        )
        return readiness, coverage

    if not matching_references:
        reason = SanadCoverageBoundaryReason.EVIDENCE_MISSING
        readiness = _readiness_decision(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            methodology_question_id=mapping.methodology_question_id,
            claim_id=mapping.claim_id,
            source_span_ids=mapping.source_span_ids,
            evidence_ids=[],
            calc_ids=[],
            ready=False,
            reason=reason,
            message="no synthetic evidence reference is available for this claim",
        )
        coverage = _coverage_decision(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            methodology_question_id=mapping.methodology_question_id,
            target_status=MethodologyCoverageStatus.EVIDENCE_MISSING,
            claim_ids=[mapping.claim_id],
            evidence_ids=[],
            source_span_ids=mapping.source_span_ids,
            calc_ids=[],
            evidence_links=[MethodologyEvidenceLink(claim_id=mapping.claim_id)],
            answer=None,
            conflict_ids=[],
            defect_ids=[],
            reason=reason,
            message="future coverage should record missing evidence",
        )
        return readiness, coverage

    malformed_status_reason = _malformed_status_reason(matching_references)
    if malformed_status_reason is not None:
        readiness = _readiness_decision(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            methodology_question_id=mapping.methodology_question_id,
            claim_id=mapping.claim_id,
            source_span_ids=mapping.source_span_ids,
            evidence_ids=[reference.evidence_id for reference in matching_references],
            calc_ids=[],
            ready=False,
            reason=malformed_status_reason,
            message="malformed evidence status blocks future Sanad readiness",
        )
        coverage = _blocked_coverage_decision(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            methodology_question_id=mapping.methodology_question_id,
            claim_id=mapping.claim_id,
            source_span_ids=mapping.source_span_ids,
            reason=malformed_status_reason,
            message="coverage decision blocked by malformed evidence status",
        )
        return readiness, coverage

    target_status = _target_status(matching_references)
    reason = _reason_for_target_status(target_status)
    evidence_ids = _sorted_unique(reference.evidence_id for reference in matching_references)
    calc_ids = _sorted_unique(
        calc_id for reference in matching_references for calc_id in reference.calc_ids
    )
    conflict_ids = _sorted_unique(
        conflict_id
        for reference in matching_references
        for conflict_id in reference.conflict_ids
    )
    defect_ids = _sorted_unique(
        defect_id for reference in matching_references for defect_id in reference.defect_ids
    )
    sanad_id = _first_present(reference.sanad_id for reference in matching_references)
    sanad_status = _first_present(
        reference.sanad_status for reference in matching_references
    ) or "deferred"
    is_ready = target_status not in {
        MethodologyCoverageStatus.BLOCKED,
        MethodologyCoverageStatus.EVIDENCE_MISSING,
    }
    readiness = _readiness_decision(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        methodology_question_id=mapping.methodology_question_id,
        claim_id=mapping.claim_id,
        source_span_ids=mapping.source_span_ids,
        evidence_ids=evidence_ids,
        calc_ids=calc_ids,
        sanad_id=sanad_id,
        sanad_status=sanad_status,
        ready=is_ready,
        reason=reason,
        message=(
            "claim has synthetic evidence for future Sanad creation"
            if is_ready
            else "claim evidence is not ready for future Sanad creation"
        ),
    )
    coverage = _coverage_decision(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        methodology_question_id=mapping.methodology_question_id,
        target_status=target_status,
        claim_ids=[mapping.claim_id],
        evidence_ids=evidence_ids,
        source_span_ids=mapping.source_span_ids,
        calc_ids=calc_ids,
        evidence_links=_evidence_links(mapping=mapping, references=matching_references),
        answer=_answer(mapping=mapping, references=matching_references),
        conflict_ids=conflict_ids,
        defect_ids=defect_ids,
        sanad_id=sanad_id,
        sanad_status=sanad_status,
        reason=reason,
        message="future coverage decision generated from synthetic boundary inputs",
    )
    return readiness, coverage


def _context_mismatch_reason(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    materialization_result: ClaimMaterializationResult,
    coverage_records: Sequence[MethodologyCoverageRecord],
    evidence_references: Sequence[MethodologyClaimEvidenceReference],
    execution_result: MethodologyExtractionExecutionResult | None,
) -> SanadCoverageBoundaryReason | None:
    scoped_items: list[object] = [
        materialization_result,
        *coverage_records,
        *evidence_references,
    ]
    if execution_result is not None:
        scoped_items.append(execution_result)
    if all(
        _same_scope(item, tenant_id=tenant_id, deal_id=deal_id, run_id=run_id)
        for item in scoped_items
    ):
        return None
    return SanadCoverageBoundaryReason.TENANT_OR_RUN_MISMATCH


def _duplicate_conflicting_mapping_context(
    mappings: Sequence[DraftClaimMapping],
) -> tuple[str, list[DraftClaimMapping]] | None:
    by_question: dict[str, list[DraftClaimMapping]] = {}
    for mapping in mappings:
        question_id = mapping.methodology_question_id.strip()
        if not question_id:
            continue
        existing = by_question.setdefault(question_id, [])
        existing.append(mapping)
    for question_id, question_mappings in sorted(by_question.items()):
        unique_targets = {
            (mapping.claim_id, tuple(mapping.source_span_ids))
            for mapping in question_mappings
        }
        if len(unique_targets) > 1:
            return question_id, question_mappings
    return None


def _readiness_decision(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    methodology_question_id: str,
    claim_id: str | None,
    source_span_ids: Sequence[str],
    evidence_ids: Sequence[str],
    calc_ids: Sequence[str],
    ready: bool,
    reason: SanadCoverageBoundaryReason,
    message: str,
    sanad_id: str | None = None,
    sanad_status: str = "deferred",
) -> SanadReadinessDecision:
    return SanadReadinessDecision(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        methodology_question_id=methodology_question_id,
        claim_id=claim_id,
        source_span_ids=list(source_span_ids),
        evidence_ids=list(evidence_ids),
        calc_ids=list(calc_ids),
        sanad_id=sanad_id,
        sanad_status=sanad_status,
        ready_for_future_sanad=ready,
        reason=reason,
        reason_codes=[reason.value],
        message=message,
    )


def _coverage_decision(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    methodology_question_id: str,
    target_status: MethodologyCoverageStatus,
    claim_ids: Sequence[str],
    evidence_ids: Sequence[str],
    source_span_ids: Sequence[str],
    calc_ids: Sequence[str],
    evidence_links: Sequence[MethodologyEvidenceLink],
    answer: MethodologyAnswer | None,
    conflict_ids: Sequence[str],
    defect_ids: Sequence[str],
    reason: SanadCoverageBoundaryReason,
    message: str,
    sanad_id: str | None = None,
    sanad_status: str | None = "deferred",
) -> CoverageUpdateDecision:
    return CoverageUpdateDecision(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        methodology_question_id=methodology_question_id,
        target_status=target_status,
        claim_ids=list(claim_ids),
        evidence_ids=list(evidence_ids),
        source_span_ids=list(source_span_ids),
        calc_ids=list(calc_ids),
        evidence_links=list(evidence_links),
        answer=answer,
        conflict_ids=list(conflict_ids),
        defect_ids=list(defect_ids),
        sanad_id=sanad_id,
        sanad_status=sanad_status,
        reason=reason,
        reason_codes=[reason.value],
        message=message,
    )


def _blocked_coverage_decision(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    methodology_question_id: str,
    claim_id: str | None,
    source_span_ids: Sequence[str],
    reason: SanadCoverageBoundaryReason,
    message: str,
) -> CoverageUpdateDecision:
    return _coverage_decision(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        methodology_question_id=methodology_question_id,
        target_status=MethodologyCoverageStatus.BLOCKED,
        claim_ids=[claim_id] if claim_id else [],
        evidence_ids=[],
        source_span_ids=source_span_ids,
        calc_ids=[],
        evidence_links=[MethodologyEvidenceLink(claim_id=claim_id)] if claim_id else [],
        answer=None,
        conflict_ids=[],
        defect_ids=[],
        reason=reason,
        message=message,
    )


def _result(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    total_claim_mappings: int,
    readiness_decisions: Sequence[SanadReadinessDecision],
    coverage_decisions: Sequence[CoverageUpdateDecision],
) -> SanadCoverageBoundaryResult:
    status = _aggregate_status(
        readiness_decisions=readiness_decisions,
        coverage_decisions=coverage_decisions,
    )
    summary = _summary(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        total_claim_mappings=total_claim_mappings,
        status=status,
        readiness_decisions=readiness_decisions,
        coverage_decisions=coverage_decisions,
    )
    return SanadCoverageBoundaryResult(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        status=status,
        readiness_decisions=list(readiness_decisions),
        coverage_decisions=list(coverage_decisions),
        summary=summary,
    )


def _summary(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    total_claim_mappings: int,
    status: SanadCoverageBoundaryStatus,
    readiness_decisions: Sequence[SanadReadinessDecision],
    coverage_decisions: Sequence[CoverageUpdateDecision],
) -> SanadCoverageBoundarySummary:
    by_reason = Counter(decision.reason.value for decision in coverage_decisions)
    by_coverage_status = Counter(
        decision.target_status.value for decision in coverage_decisions
    )
    return SanadCoverageBoundarySummary(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        total_claim_mappings=total_claim_mappings,
        ready_for_future_sanad_count=sum(
            1 for decision in readiness_decisions if decision.ready_for_future_sanad
        ),
        coverage_decision_count=len(coverage_decisions),
        blocked_decision_count=sum(
            1
            for decision in coverage_decisions
            if decision.target_status == MethodologyCoverageStatus.BLOCKED
        ),
        by_status={status.value: 1},
        by_reason=dict(sorted(by_reason.items())),
        by_coverage_status=dict(sorted(by_coverage_status.items())),
    )


def _aggregate_status(
    *,
    readiness_decisions: Sequence[SanadReadinessDecision],
    coverage_decisions: Sequence[CoverageUpdateDecision],
) -> SanadCoverageBoundaryStatus:
    if not readiness_decisions and not coverage_decisions:
        return SanadCoverageBoundaryStatus.COMPLETED
    blocked_count = sum(
        1
        for decision in coverage_decisions
        if decision.target_status
        in {MethodologyCoverageStatus.BLOCKED, MethodologyCoverageStatus.EVIDENCE_MISSING}
    )
    ready_count = sum(
        1 for decision in readiness_decisions if decision.ready_for_future_sanad
    )
    if ready_count and blocked_count:
        return SanadCoverageBoundaryStatus.PARTIAL
    if blocked_count:
        return SanadCoverageBoundaryStatus.FAILED
    return SanadCoverageBoundaryStatus.COMPLETED


def _target_status(
    references: Sequence[MethodologyClaimEvidenceReference],
) -> MethodologyCoverageStatus:
    priority = {
        MethodologyCoverageStatus.BLOCKED: 0,
        MethodologyCoverageStatus.EVIDENCE_MISSING: 1,
        MethodologyCoverageStatus.CONTRADICTED: 2,
        MethodologyCoverageStatus.ANSWERED: 3,
        MethodologyCoverageStatus.PARTIALLY_ANSWERED: 4,
        MethodologyCoverageStatus.EXTRACTED: 5,
    }
    sorted_references = sorted(
        references,
        key=lambda reference: priority.get(reference.target_status, 99),
    )
    return sorted_references[0].target_status


def _reason_for_target_status(
    target_status: MethodologyCoverageStatus,
) -> SanadCoverageBoundaryReason:
    if target_status == MethodologyCoverageStatus.CONTRADICTED:
        return SanadCoverageBoundaryReason.CONTRADICTED
    if target_status == MethodologyCoverageStatus.BLOCKED:
        return SanadCoverageBoundaryReason.BLOCKED
    if target_status == MethodologyCoverageStatus.EVIDENCE_MISSING:
        return SanadCoverageBoundaryReason.EVIDENCE_MISSING
    return SanadCoverageBoundaryReason.READY_FOR_FUTURE_SANAD


def _malformed_status_reason(
    references: Sequence[MethodologyClaimEvidenceReference],
) -> SanadCoverageBoundaryReason | None:
    for reference in references:
        if reference.target_status == MethodologyCoverageStatus.CONTRADICTED and not (
            reference.conflict_ids or reference.defect_ids
        ):
            return SanadCoverageBoundaryReason.BLOCKED
    return None


def _evidence_links(
    *,
    mapping: DraftClaimMapping,
    references: Sequence[MethodologyClaimEvidenceReference],
) -> list[MethodologyEvidenceLink]:
    links = [MethodologyEvidenceLink(claim_id=mapping.claim_id)]
    for reference in references:
        links.append(
            MethodologyEvidenceLink(
                evidence_id=reference.evidence_id,
                claim_id=mapping.claim_id,
            )
        )
        for calc_id in reference.calc_ids:
            links.append(MethodologyEvidenceLink(calc_id=calc_id, claim_id=mapping.claim_id))
    return sorted(
        links,
        key=lambda link: (
            link.claim_id or "",
            link.evidence_id or "",
            link.calc_id or "",
        ),
    )


def _answer(
    *,
    mapping: DraftClaimMapping,
    references: Sequence[MethodologyClaimEvidenceReference],
) -> MethodologyAnswer | None:
    answer_texts = [
        reference.answer_text.strip()
        for reference in references
        if reference.answer_text is not None and reference.answer_text.strip()
    ]
    if not answer_texts:
        return None
    return MethodologyAnswer(
        answer_text=" ".join(sorted(answer_texts)),
        claim_ids=[mapping.claim_id],
        evidence_ids=_sorted_unique(reference.evidence_id for reference in references),
        calc_ids=_sorted_unique(
            calc_id for reference in references for calc_id in reference.calc_ids
        ),
        requires_calculation=any(reference.calc_ids for reference in references),
    )


def _same_scope(
    item: object,
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
) -> bool:
    return (
        getattr(item, "tenant_id", None) == tenant_id
        and getattr(item, "deal_id", None) == deal_id
        and getattr(item, "run_id", None) == run_id
    )


def _coverage_scope_key(
    item: MethodologyCoverageRecord | CoverageUpdateDecision,
) -> tuple[str, str, str, str]:
    return (
        item.tenant_id,
        item.deal_id,
        item.run_id,
        item.methodology_question_id,
    )


def _question_id_for_mapping(mapping: DraftClaimMapping) -> str:
    return mapping.methodology_question_id.strip() or _MISSING_QUESTION_ID


def _sorted_unique(values: Iterable[str]) -> list[str]:
    return sorted(set(values))


def _first_present(values: Iterable[str | None]) -> str | None:
    for value in values:
        if value is not None:
            return value
    return None
