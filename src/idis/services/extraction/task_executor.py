"""Synthetic-only methodology extraction task executor."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from decimal import Decimal
from typing import Any

from idis.models.extraction_execution import (
    MethodologyClaimDraft,
    MethodologyExtractionExecutionReason,
    MethodologyExtractionExecutionResult,
    MethodologyExtractionExecutionStatus,
    MethodologyExtractionExecutionSummary,
    MethodologyTaskExecutionResult,
    MethodologyTaskExecutionStatus,
    generate_methodology_claim_draft_id,
)
from idis.models.extraction_task import ExtractionTask, ExtractionTaskStatus, SourceSpanReference
from idis.services.extraction.service import ExtractedClaimDraft, Extractor
from idis.validators.extraction_gate import (
    ExtractionGateBlockReason,
    ExtractionGateInput,
    evaluate_extraction_gate,
)


class InMemoryMethodologyExtractionTaskExecutor:
    """Execute ready extraction tasks with an injected synthetic extractor."""

    persistence_backend = "in_memory"
    external_calls_enabled = False

    def execute_tasks(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        tasks: list[ExtractionTask],
        extractor: Extractor | None,
    ) -> MethodologyExtractionExecutionResult:
        """Execute task-scoped extraction and return claim draft metadata only."""
        sorted_tasks = sorted(
            tasks,
            key=lambda task: (
                task.methodology_question_id,
                task.document_id or "",
                task.extraction_task_id or "",
            ),
        )

        task_results = [
            self._execute_task(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                task=task,
                extractor=extractor,
            )
            for task in sorted_tasks
        ]
        accepted_claim_drafts = [
            draft for result in task_results for draft in result.accepted_drafts
        ]
        summary = _build_summary(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            task_results=task_results,
        )
        return MethodologyExtractionExecutionResult(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            status=_aggregate_status(task_results),
            task_results=task_results,
            accepted_claim_drafts=accepted_claim_drafts,
            summary=summary,
        )

    def _execute_task(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        task: ExtractionTask,
        extractor: Extractor | None,
    ) -> MethodologyTaskExecutionResult:
        if task.status != ExtractionTaskStatus.READY:
            return _task_result(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                task=task,
                status=MethodologyTaskExecutionStatus.SKIPPED,
                reason=_skip_reason(task.status),
                reason_codes=_skipped_reason_codes(task),
                rejected_drafts=[],
            )

        linkage_reason = _missing_linkage_reason(task)
        if linkage_reason is not None:
            return _task_result(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                task=task,
                status=MethodologyTaskExecutionStatus.FAILED,
                reason=linkage_reason,
                rejected_drafts=[],
            )

        if not task.source_spans:
            return _task_result(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                task=task,
                status=MethodologyTaskExecutionStatus.FAILED,
                reason=MethodologyExtractionExecutionReason.NO_SOURCE_SPANS,
                rejected_drafts=[],
            )

        if extractor is None:
            return _task_result(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                task=task,
                status=MethodologyTaskExecutionStatus.FAILED,
                reason=MethodologyExtractionExecutionReason.EXTRACTOR_UNAVAILABLE,
                rejected_drafts=[],
            )

        spans = [
            _span_to_extractor_payload(span)
            for span in sorted(task.source_spans, key=lambda s: s.span_id)
        ]
        try:
            raw_drafts = extractor.extract(tenant_id=tenant_id, deal_id=deal_id, spans=spans)
        except Exception as exc:
            return _task_result(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                task=task,
                status=MethodologyTaskExecutionStatus.FAILED,
                reason=MethodologyExtractionExecutionReason.EXTRACTOR_EXCEPTION,
                rejected_drafts=[{"reason": "extractor_exception", "error": str(exc)}],
            )

        if not isinstance(raw_drafts, list):
            return _task_result(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                task=task,
                status=MethodologyTaskExecutionStatus.FAILED,
                reason=MethodologyExtractionExecutionReason.MALFORMED_EXTRACTOR_OUTPUT,
                rejected_drafts=[{"reason": "malformed_extractor_output"}],
            )

        accepted: list[MethodologyClaimDraft] = []
        rejected: list[dict[str, Any]] = []
        for raw_draft in raw_drafts:
            draft, validation_reason = _validate_and_convert_draft(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                task=task,
                raw_draft=raw_draft,
            )
            if draft is None:
                rejected.append({"reason": validation_reason.value})
                continue
            accepted.append(draft)

        execution_reason: MethodologyExtractionExecutionReason | None
        if accepted and rejected:
            status = MethodologyTaskExecutionStatus.PARTIAL
            execution_reason = None
        elif accepted:
            status = MethodologyTaskExecutionStatus.COMPLETED
            execution_reason = None
        else:
            status = MethodologyTaskExecutionStatus.FAILED
            execution_reason = _first_rejection_reason(rejected)

        return _task_result(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            task=task,
            status=status,
            reason=execution_reason,
            accepted_drafts=accepted,
            rejected_drafts=rejected,
        )


def _validate_and_convert_draft(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    task: ExtractionTask,
    raw_draft: Any,
) -> tuple[MethodologyClaimDraft | None, MethodologyExtractionExecutionReason]:
    if not isinstance(raw_draft, ExtractedClaimDraft):
        return None, MethodologyExtractionExecutionReason.MALFORMED_EXTRACTOR_OUTPUT

    if not raw_draft.span_id:
        return None, MethodologyExtractionExecutionReason.MALFORMED_EXTRACTOR_OUTPUT

    task_span_ids = set(task.source_span_ids)
    if raw_draft.span_id not in task_span_ids:
        return None, MethodologyExtractionExecutionReason.HALLUCINATED_SOURCE_REFERENCE

    if not raw_draft.claim_text.strip() or not raw_draft.claim_class.strip():
        return None, MethodologyExtractionExecutionReason.MALFORMED_EXTRACTOR_OUTPUT
    if not raw_draft.predicate or not raw_draft.predicate.strip():
        return None, MethodologyExtractionExecutionReason.MALFORMED_EXTRACTOR_OUTPUT
    if not isinstance(raw_draft.value, dict) or not raw_draft.value:
        return None, MethodologyExtractionExecutionReason.MALFORMED_EXTRACTOR_OUTPUT

    gate_decision = evaluate_extraction_gate(
        ExtractionGateInput(
            claim_id=raw_draft.span_id,
            extraction_confidence=raw_draft.extraction_confidence,
            dhabt_score=raw_draft.dhabt_score,
            is_human_verified=False,
        )
    )
    if gate_decision.blocked:
        return None, _reason_from_gate(gate_decision.reason)

    source_span_ids = [raw_draft.span_id]
    draft_id = generate_methodology_claim_draft_id(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        extraction_task_id=task.extraction_task_id or "",
        methodology_question_id=task.methodology_question_id,
        document_id=task.document_id or "",
        source_span_ids=source_span_ids,
        predicate=raw_draft.predicate,
        claim_text=raw_draft.claim_text,
        value=raw_draft.value,
    )
    draft = MethodologyClaimDraft(
        methodology_claim_draft_id=draft_id,
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        extraction_task_id=task.extraction_task_id or "",
        methodology_id=task.methodology_id,
        methodology_version_id=task.methodology_version_id,
        methodology_question_id=task.methodology_question_id,
        document_id=task.document_id or "",
        source_span_ids=source_span_ids,
        claim_text=raw_draft.claim_text,
        claim_class=raw_draft.claim_class,
        predicate=raw_draft.predicate,
        value=raw_draft.value,
        extraction_confidence=gate_decision.extraction_confidence or Decimal("0"),
        dhabt_score=gate_decision.dhabt_score or Decimal("0"),
        future_claim_input_preview=_build_future_claim_input_preview(
            task=task,
            draft=raw_draft,
            draft_id=draft_id,
            confidence=gate_decision.extraction_confidence or Decimal("0"),
            dhabt=gate_decision.dhabt_score or Decimal("0"),
        ),
    )
    return draft, MethodologyExtractionExecutionReason.MALFORMED_EXTRACTOR_OUTPUT


def _build_future_claim_input_preview(
    *,
    task: ExtractionTask,
    draft: ExtractedClaimDraft,
    draft_id: str,
    confidence: Decimal,
    dhabt: Decimal,
) -> dict[str, Any]:
    return {
        "deal_id": task.deal_id,
        "claim_class": draft.claim_class,
        "claim_text": draft.claim_text,
        "claim_type": "primary",
        "predicate": draft.predicate,
        "value": draft.value,
        "claim_grade": "D",
        "claim_verdict": "UNVERIFIED",
        "claim_action": "VERIFY",
        "materiality": "MEDIUM",
        "ic_bound": False,
        "primary_span_id": draft.span_id,
        "corroboration": {
            "methodology_claim_draft_id": draft_id,
            "extraction_task_id": task.extraction_task_id,
            "methodology_id": task.methodology_id,
            "methodology_version_id": task.methodology_version_id,
            "methodology_question_id": task.methodology_question_id,
            "document_id": task.document_id,
            "source_span_ids": [draft.span_id],
            "source_span_metadata": _source_span_metadata(task, draft.span_id),
            "extraction_confidence": str(confidence),
            "dhabt_score": str(dhabt),
        },
    }


def _task_result(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    task: ExtractionTask,
    status: MethodologyTaskExecutionStatus,
    reason: MethodologyExtractionExecutionReason | None,
    reason_codes: list[str] | None = None,
    accepted_drafts: list[MethodologyClaimDraft] | None = None,
    rejected_drafts: list[dict[str, Any]] | None = None,
) -> MethodologyTaskExecutionResult:
    return MethodologyTaskExecutionResult(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        extraction_task_id=task.extraction_task_id or "",
        status=status,
        accepted_drafts=accepted_drafts or [],
        rejected_drafts=rejected_drafts or [],
        reason=reason,
        reason_codes=reason_codes or ([reason.value] if reason else [status.value]),
        source_span_ids=task.source_span_ids,
    )


def _span_to_extractor_payload(span: SourceSpanReference) -> dict[str, Any]:
    return {
        "document_id": span.document_id,
        "span_id": span.span_id,
        "text_excerpt": span.text_excerpt or "",
        "locator": span.locator,
        "evidence_tags": span.evidence_tags,
    }


def _skip_reason(status: ExtractionTaskStatus) -> MethodologyExtractionExecutionReason:
    if status == ExtractionTaskStatus.EVIDENCE_MISSING:
        return MethodologyExtractionExecutionReason.EVIDENCE_MISSING_TASK
    if status == ExtractionTaskStatus.NOT_APPLICABLE:
        return MethodologyExtractionExecutionReason.NOT_APPLICABLE_TASK
    return MethodologyExtractionExecutionReason.BLOCKED_TASK


def _skipped_reason_codes(task: ExtractionTask) -> list[str]:
    if task.blocker_reason is not None:
        return [task.blocker_reason.value]
    return [_skip_reason(task.status).value]


def _source_span_metadata(task: ExtractionTask, span_id: str) -> list[dict[str, Any]]:
    return [
        {
            "span_id": span.span_id,
            "document_id": span.document_id,
            "locator": span.locator,
            "evidence_tags": span.evidence_tags,
        }
        for span in sorted(task.source_spans, key=lambda source_span: source_span.span_id)
        if span.span_id == span_id
    ]


def _missing_linkage_reason(
    task: ExtractionTask,
) -> MethodologyExtractionExecutionReason | None:
    required = [
        task.extraction_task_id,
        task.methodology_id,
        task.methodology_version_id,
        task.methodology_question_id,
        task.document_id,
    ]
    if any(not value or not str(value).strip() for value in required):
        return MethodologyExtractionExecutionReason.MISSING_METHODOLOGY_LINKAGE
    return None


def _reason_from_gate(
    reason: ExtractionGateBlockReason | None,
) -> MethodologyExtractionExecutionReason:
    if reason == ExtractionGateBlockReason.LOW_CONFIDENCE:
        return MethodologyExtractionExecutionReason.BELOW_CONFIDENCE_THRESHOLD
    if reason == ExtractionGateBlockReason.LOW_DHABT:
        return MethodologyExtractionExecutionReason.BELOW_DHABT_THRESHOLD
    return MethodologyExtractionExecutionReason.MISSING_GATE_METADATA


def _first_rejection_reason(
    rejected: list[dict[str, Any]],
) -> MethodologyExtractionExecutionReason:
    if not rejected:
        return MethodologyExtractionExecutionReason.MALFORMED_EXTRACTOR_OUTPUT
    reason = str(rejected[0].get("reason", "malformed_extractor_output"))
    try:
        return MethodologyExtractionExecutionReason(reason)
    except ValueError:
        return MethodologyExtractionExecutionReason.MALFORMED_EXTRACTOR_OUTPUT


def _aggregate_status(
    task_results: list[MethodologyTaskExecutionResult],
) -> MethodologyExtractionExecutionStatus:
    if not task_results:
        return MethodologyExtractionExecutionStatus.COMPLETED
    if all(result.status == MethodologyTaskExecutionStatus.COMPLETED for result in task_results):
        return MethodologyExtractionExecutionStatus.COMPLETED
    if all(result.status == MethodologyTaskExecutionStatus.FAILED for result in task_results):
        return MethodologyExtractionExecutionStatus.FAILED
    return MethodologyExtractionExecutionStatus.PARTIAL


def _build_summary(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    task_results: list[MethodologyTaskExecutionResult],
) -> MethodologyExtractionExecutionSummary:
    return MethodologyExtractionExecutionSummary(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        total_tasks=len(task_results),
        executed_tasks=sum(
            result.status
            in {
                MethodologyTaskExecutionStatus.COMPLETED,
                MethodologyTaskExecutionStatus.PARTIAL,
                MethodologyTaskExecutionStatus.FAILED,
            }
            for result in task_results
        ),
        skipped_tasks=sum(
            result.status == MethodologyTaskExecutionStatus.SKIPPED for result in task_results
        ),
        failed_tasks=sum(
            result.status == MethodologyTaskExecutionStatus.FAILED for result in task_results
        ),
        accepted_draft_count=sum(len(result.accepted_drafts) for result in task_results),
        rejected_draft_count=sum(len(result.rejected_drafts) for result in task_results),
        by_status=_counter(result.status.value for result in task_results),
        by_reason=_counter(
            reason_code
            for result in task_results
            if result.reason is not None
            for reason_code in result.reason_codes
        ),
    )


def _counter(items: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(items).items()))
