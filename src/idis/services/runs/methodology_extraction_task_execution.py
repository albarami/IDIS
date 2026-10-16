"""Run-scoped methodology extraction task execution adapter."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal, Protocol

from idis.models.extraction_execution import (
    MethodologyExtractionExecutionReason,
    MethodologyExtractionExecutionResult,
    MethodologyExtractionExecutionRunResult,
    MethodologyExtractionExecutionStatus,
    MethodologyTaskExecutionResult,
    MethodologyTaskExecutionStatus,
)
from idis.models.extraction_task import (
    ExtractionTaskStatus,
    PlannedExtractionTask,
    SourceSpanReference,
)
from idis.models.methodology_extraction import (
    DispatchedMethodologyCall,
    MethodologyCallAdmissionProof,
    MethodologyCallTerminalizationProof,
    MethodologyTaskExtractorRequest,
    MethodologyTaskExtractorResponse,
    PreparedMethodologyCall,
    ValidatedDispatchedMethodologyResult,
)
from idis.services.extraction.methodology_task_extractor import (
    MethodologyProviderRequestNotAccepted,
)
from idis.services.extraction.service import Extractor
from idis.services.extraction.task_executor import InMemoryMethodologyExtractionTaskExecutor

METHODOLOGY_ATTEMPT_OUTCOME_UNKNOWN = "METHODOLOGY_ATTEMPT_OUTCOME_UNKNOWN"
METHODOLOGY_PROVIDER_REQUEST_NOT_ACCEPTED = "METHODOLOGY_PROVIDER_REQUEST_NOT_ACCEPTED"
METHODOLOGY_TASK_COVERAGE_INCOMPLETE = "METHODOLOGY_TASK_COVERAGE_INCOMPLETE"


class DurableMethodologyExtractionExecutionError(RuntimeError):
    """Stable fail-closed error for the durable Step-7 coordinator."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _PhasedMethodologyExtractor(Protocol):
    def prepare(
        self, request: MethodologyTaskExtractorRequest, *, budget_policy_id: str
    ) -> PreparedMethodologyCall: ...

    def dispatch(
        self,
        prepared: PreparedMethodologyCall,
        *,
        admission: MethodologyCallAdmissionProof | None,
    ) -> DispatchedMethodologyCall: ...

    def validate_dispatched(
        self,
        dispatched: DispatchedMethodologyCall,
        *,
        request: MethodologyTaskExtractorRequest,
    ) -> ValidatedDispatchedMethodologyResult: ...

    def compute_prospective_receipt_sha256(
        self,
        dispatched: DispatchedMethodologyCall,
        *,
        validated: ValidatedDispatchedMethodologyResult,
        budget_terminal_state: Literal["CONSUMED", "NOT_BILLABLE"],
        budget_policy_id: str,
    ) -> str: ...

    def finalize(
        self,
        dispatched: DispatchedMethodologyCall,
        *,
        validated: ValidatedDispatchedMethodologyResult,
        terminalization_proof: MethodologyCallTerminalizationProof,
    ) -> MethodologyTaskExtractorResponse: ...


class _DurableExecutionBoundary(Protocol):
    def load_execution_inputs(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        run_step_id: str,
        tasks: list[Any],
        documents: list[dict[str, Any]],
    ) -> Sequence[Any]: ...

    def load_started_attempts(
        self, *, tenant_id: str, deal_id: str, run_id: str, run_step_id: str
    ) -> Sequence[Mapping[str, Any]]: ...

    def load_committed_responses(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        run_step_id: str,
        execution_inputs: Sequence[Any],
    ) -> Mapping[str, MethodologyTaskExtractorResponse]: ...

    def load_active_budget_policy(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        run_step_id: str,
        execution_input: Any,
    ) -> Any: ...

    def mark_started_attempts_outcome_unknown(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        run_step_id: str,
        attempts: list[Mapping[str, Any]],
    ) -> None: ...

    def mark_admitted_call_outcome_unknown(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        run_step_id: str,
        execution_input: Any,
        prepared: PreparedMethodologyCall,
        admission: MethodologyCallAdmissionProof,
    ) -> None: ...

    def fail_provider_request_not_accepted(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        run_step_id: str,
        execution_input: Any,
        prepared: PreparedMethodologyCall,
        admission: MethodologyCallAdmissionProof,
        nonacceptance: MethodologyProviderRequestNotAccepted,
    ) -> None: ...

    def admit_call(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        run_step_id: str,
        execution_input: Any,
        prepared: PreparedMethodologyCall,
        verified_budget_policy: Any,
    ) -> MethodologyCallAdmissionProof: ...

    def terminalize_call(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        run_step_id: str,
        execution_input: Any,
        dispatched: DispatchedMethodologyCall,
        validated_result: ValidatedDispatchedMethodologyResult,
        prospective_receipt_sha256: str,
    ) -> MethodologyCallTerminalizationProof: ...

    def fail_received_response(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        run_step_id: str,
        execution_input: Any,
        dispatched: DispatchedMethodologyCall,
        safe_failure_code: str,
    ) -> None: ...

    def stage_exact_terminal_set(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        run_step_id: str,
        expected_task_ids: tuple[str, ...],
        responses_by_task_id: Mapping[str, MethodologyTaskExtractorResponse],
    ) -> Any: ...

    def build_execution_result(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        run_step_id: str,
        responses_by_task_id: Mapping[str, MethodologyTaskExtractorResponse],
    ) -> Any: ...


class DurableRunMethodologyExtractionTaskExecutionService:
    """Order Task-6 provider work around durable caller-owned micro-boundaries.

    The boundary owns every short transaction.  In particular, this coordinator
    invokes ``dispatch`` only after ``admit_call`` has returned and invokes the
    pure ``finalize`` phase only after ``terminalize_call`` has returned its
    committed proof.  It therefore cannot hold a database transaction across a
    provider call or expose a response before terminal persistence commits.
    """

    def __init__(
        self,
        *,
        extractor: _PhasedMethodologyExtractor,
        boundary: _DurableExecutionBoundary,
    ) -> None:
        if extractor is None or boundary is None:
            raise DurableMethodologyExtractionExecutionError(METHODOLOGY_TASK_COVERAGE_INCOMPLETE)
        self._extractor = extractor
        self._boundary = boundary

    def run(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        run_step_id: str,
        tasks: list[Any],
        documents: list[dict[str, Any]],
    ) -> tuple[Any, Any, Any]:
        """Execute the exact durable task set with no ambiguous-call retry."""

        scope = {
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "run_id": run_id,
            "run_step_id": run_step_id,
        }
        execution_inputs = list(
            self._boundary.load_execution_inputs(
                **scope,
                tasks=tasks,
                documents=documents,
            )
        )
        expected_task_ids = _exact_task_ids(tasks)
        input_task_ids = _exact_task_ids(execution_inputs)
        if not expected_task_ids or input_task_ids != expected_task_ids:
            raise DurableMethodologyExtractionExecutionError(METHODOLOGY_TASK_COVERAGE_INCOMPLETE)
        for execution_input in execution_inputs:
            _validate_execution_input_scope(
                execution_input,
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
            )

        started_attempts = list(self._boundary.load_started_attempts(**scope))
        if started_attempts:
            self._boundary.mark_started_attempts_outcome_unknown(
                **scope,
                attempts=started_attempts,
            )
            raise DurableMethodologyExtractionExecutionError(METHODOLOGY_ATTEMPT_OUTCOME_UNKNOWN)

        responses_by_task_id = dict(
            self._boundary.load_committed_responses(
                **scope,
                execution_inputs=execution_inputs,
            )
        )
        if not set(responses_by_task_id).issubset(expected_task_ids):
            raise DurableMethodologyExtractionExecutionError(METHODOLOGY_TASK_COVERAGE_INCOMPLETE)
        for execution_input in sorted(
            execution_inputs,
            key=lambda item: str(getattr(item, "extraction_task_id", "")),
        ):
            if execution_input.extraction_task_id in responses_by_task_id:
                continue
            request = execution_input.request
            verified_budget_policy = self._boundary.load_active_budget_policy(
                **scope,
                execution_input=execution_input,
            )
            budget_policy_id = str(getattr(verified_budget_policy, "policy_hash", "")).strip()
            if not budget_policy_id:
                raise DurableMethodologyExtractionExecutionError(
                    METHODOLOGY_TASK_COVERAGE_INCOMPLETE
                )
            prepared = self._extractor.prepare(
                request,
                budget_policy_id=budget_policy_id,
            )
            admission = self._boundary.admit_call(
                **scope,
                execution_input=execution_input,
                prepared=prepared,
                verified_budget_policy=verified_budget_policy,
            )
            try:
                dispatched = self._extractor.dispatch(prepared, admission=admission)
            except MethodologyProviderRequestNotAccepted as exc:
                self._boundary.fail_provider_request_not_accepted(
                    **scope,
                    execution_input=execution_input,
                    prepared=prepared,
                    admission=admission,
                    nonacceptance=exc,
                )
                raise DurableMethodologyExtractionExecutionError(
                    METHODOLOGY_PROVIDER_REQUEST_NOT_ACCEPTED
                ) from exc
            except Exception as exc:
                self._boundary.mark_admitted_call_outcome_unknown(
                    **scope,
                    execution_input=execution_input,
                    prepared=prepared,
                    admission=admission,
                )
                raise DurableMethodologyExtractionExecutionError(
                    METHODOLOGY_ATTEMPT_OUTCOME_UNKNOWN
                ) from exc
            try:
                validated = self._extractor.validate_dispatched(dispatched, request=request)
                prospective_receipt_sha256 = self._extractor.compute_prospective_receipt_sha256(
                    dispatched,
                    validated=validated,
                    budget_terminal_state="CONSUMED",
                    budget_policy_id=str(admission.budget_policy_id),
                )
            except Exception as exc:
                self._boundary.fail_received_response(
                    **scope,
                    execution_input=execution_input,
                    dispatched=dispatched,
                    safe_failure_code=str(
                        getattr(exc, "code", "METHODOLOGY_PROVIDER_RESULT_INVALID")
                    ),
                )
                raise
            terminalization_proof = self._boundary.terminalize_call(
                **scope,
                execution_input=execution_input,
                dispatched=dispatched,
                validated_result=validated,
                prospective_receipt_sha256=prospective_receipt_sha256,
            )
            response = self._extractor.finalize(
                dispatched,
                validated=validated,
                terminalization_proof=terminalization_proof,
            )
            task_id = str(execution_input.extraction_task_id)
            if task_id in responses_by_task_id:
                raise DurableMethodologyExtractionExecutionError(
                    METHODOLOGY_TASK_COVERAGE_INCOMPLETE
                )
            responses_by_task_id[task_id] = response

        staged_exact_set = self._boundary.stage_exact_terminal_set(
            **scope,
            expected_task_ids=expected_task_ids,
            responses_by_task_id=responses_by_task_id,
        )
        execution_result = self._boundary.build_execution_result(
            **scope,
            responses_by_task_id=responses_by_task_id,
        )
        return execution_result, execution_result, staged_exact_set


def _exact_task_ids(values: list[Any]) -> tuple[str, ...]:
    identifiers = [str(getattr(value, "extraction_task_id", "")).strip() for value in values]
    if any(not identifier for identifier in identifiers) or len(identifiers) != len(
        set(identifiers)
    ):
        raise DurableMethodologyExtractionExecutionError(METHODOLOGY_TASK_COVERAGE_INCOMPLETE)
    return tuple(sorted(identifiers))


def _validate_execution_input_scope(
    execution_input: Any,
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
) -> None:
    request = getattr(execution_input, "request", None)
    question_record_id = str(getattr(execution_input, "methodology_question_record_id", ""))
    if (
        request is None
        or (request.tenant_id, request.deal_id, request.run_id) != (tenant_id, deal_id, run_id)
        or request.extraction_task_id != execution_input.extraction_task_id
        or request.methodology_question_record_id != question_record_id
        or not str(getattr(execution_input, "task_record_id", "")).strip()
    ):
        raise DurableMethodologyExtractionExecutionError(METHODOLOGY_TASK_COVERAGE_INCOMPLETE)


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
        tasks: list[PlannedExtractionTask],
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
    task: PlannedExtractionTask,
    span_index: dict[tuple[str, str], dict[str, Any]],
) -> PlannedExtractionTask | None:
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
    task: PlannedExtractionTask,
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
