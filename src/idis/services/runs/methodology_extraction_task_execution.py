"""Run-scoped methodology extraction task execution adapter."""

from __future__ import annotations

from typing import Any

from idis.models.extraction_execution import (
    MethodologyExtractionExecutionReason,
    MethodologyExtractionExecutionResult,
    MethodologyExtractionExecutionRunResult,
    MethodologyExtractionExecutionStatus,
    MethodologyTaskExecutionResult,
    MethodologyTaskExecutionStatus,
)
from idis.models.extraction_task import (
    ExtractionTask,
    ExtractionTaskStatus,
    SourceSpanReference,
)
from idis.services.extraction.service import Extractor
from idis.services.extraction.task_executor import InMemoryMethodologyExtractionTaskExecutor


class InMemoryRunMethodologyExtractionTaskExecutionService:
    """Execute planned methodology extraction tasks from safe run context state."""

    def __init__(
        self,
        executor: InMemoryMethodologyExtractionTaskExecutor | None = None,
    ) -> None:
        """Initialize the run-scoped execution adapter."""
        self._executor = executor or InMemoryMethodologyExtractionTaskExecutor()

    def run(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        tasks: list[ExtractionTask],
        documents: list[dict[str, Any]],
        extractor: Extractor | None,
    ) -> tuple[MethodologyExtractionExecutionRunResult, MethodologyExtractionExecutionResult]:
        """Execute planned tasks while keeping raw source text in memory only."""
        span_index = _build_span_index(documents)
        task_results: list[MethodologyTaskExecutionResult] = []

        for task in sorted(
            tasks,
            key=lambda item: (
                item.methodology_question_id,
                item.document_id or "",
                item.extraction_task_id or "",
            ),
        ):
            executable_task = task
            if task.status == ExtractionTaskStatus.READY:
                hydrated_task = _hydrate_task_source_spans(task, span_index)
                if hydrated_task is None:
                    task_results.append(
                        _failed_task_result(
                            tenant_id=tenant_id,
                            deal_id=deal_id,
                            run_id=run_id,
                            task=task,
                            reason=MethodologyExtractionExecutionReason.SOURCE_SPAN_UNAVAILABLE,
                        )
                    )
                    continue
                executable_task = hydrated_task

            task_execution = self._executor.execute_tasks(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                tasks=[executable_task],
                extractor=extractor,
            )
            task_results.extend(
                _neutral_task_result(result) for result in task_execution.task_results
            )

        run_result = MethodologyExtractionExecutionRunResult.from_task_results(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            task_results=task_results,
        )
        execution_result = MethodologyExtractionExecutionResult(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            status=_aggregate_status(task_results),
            task_results=task_results,
            accepted_outputs=[
                output for result in task_results for output in result.accepted_outputs
            ],
            summary=run_result.summary,
        )
        return run_result, execution_result


def _build_span_index(
    documents: list[dict[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    span_index: dict[tuple[str, str], dict[str, Any]] = {}
    for document in documents:
        document_id = str(document.get("document_id") or "").strip()
        if not document_id:
            continue
        raw_spans = document.get("spans")
        if not isinstance(raw_spans, list):
            continue
        for raw_span in raw_spans:
            if not isinstance(raw_span, dict):
                continue
            span_id = str(raw_span.get("span_id") or "").strip()
            if span_id:
                span_index[(document_id, span_id)] = raw_span
    return span_index


def _hydrate_task_source_spans(
    task: ExtractionTask,
    span_index: dict[tuple[str, str], dict[str, Any]],
) -> ExtractionTask | None:
    hydrated_spans: list[SourceSpanReference] = []
    for span in task.source_spans:
        raw_span = span_index.get((span.document_id, span.span_id))
        if raw_span is None:
            return None
        raw_content_hash = _optional_string(raw_span.get("content_hash"))
        if span.content_hash is not None and raw_content_hash != span.content_hash:
            return None
        hydrated_spans.append(
            SourceSpanReference(
                document_id=span.document_id,
                span_id=span.span_id,
                evidence_tags=span.evidence_tags,
                locator=span.locator,
                span_type=span.span_type or _optional_string(raw_span.get("span_type")),
                content_hash=span.content_hash or raw_content_hash,
                text_excerpt=_optional_string(raw_span.get("text_excerpt")),
            )
        )
    return task.model_copy(update={"source_spans": hydrated_spans})


def _failed_task_result(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    task: ExtractionTask,
    reason: MethodologyExtractionExecutionReason,
) -> MethodologyTaskExecutionResult:
    return MethodologyTaskExecutionResult(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        extraction_task_id=task.extraction_task_id or "",
        methodology_question_id=task.methodology_question_id,
        coverage_record_id=task.coverage_record_id,
        status=MethodologyTaskExecutionStatus.FAILED,
        accepted_outputs=[],
        rejected_outputs=[{"reason": reason.value}],
        accepted_drafts=[],
        rejected_drafts=[],
        reason=reason,
        reason_codes=[reason.value],
        source_span_ids=task.source_span_ids,
    )


def _neutral_task_result(
    result: MethodologyTaskExecutionResult,
) -> MethodologyTaskExecutionResult:
    return result.model_copy(
        update={
            "accepted_drafts": [],
            "rejected_drafts": [],
        },
        deep=True,
    )


def _aggregate_status(
    task_results: list[MethodologyTaskExecutionResult],
) -> MethodologyExtractionExecutionStatus:
    if not task_results:
        return MethodologyExtractionExecutionStatus.FAILED
    if all(result.status == MethodologyTaskExecutionStatus.COMPLETED for result in task_results):
        return MethodologyExtractionExecutionStatus.COMPLETED
    if all(result.status == MethodologyTaskExecutionStatus.FAILED for result in task_results):
        return MethodologyExtractionExecutionStatus.FAILED
    return MethodologyExtractionExecutionStatus.PARTIAL


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()
