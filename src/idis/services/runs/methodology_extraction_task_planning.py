"""Run-scoped methodology extraction task planning adapter."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import ValidationError

from idis.methodology.models import MethodologyRegistry, MethodologyType
from idis.models.document_preflight import (
    DocumentPreflightClassificationSummaryRecord,
    DocumentPreflightRunStepSummary,
    DocumentPreflightSpanReference,
    DocumentPreflightStatus,
)
from idis.models.extraction_task import (
    ExtractionTaskPlanningRunResult,
    ExtractionTaskStatus,
    PlannedExtractionTask,
    SourceSpanReference,
)
from idis.models.methodology_coverage import (
    MethodologyCoverageRecord,
    MethodologyCoverageStatus,
)
from idis.persistence.repositories.runs import WorkerLeaseFencedError
from idis.services.extraction.task_planner import (
    InMemoryExtractionTaskPlanner,
    PlannerClassification,
    SafePreflightClassificationInput,
    StrictDurableCategorySpanAdmissionPolicy,
)

PLANNING_INPUT_INVALID = "METHODOLOGY_EXTRACTION_TASK_PLANNING_INPUT_INVALID"
TASK_COVERAGE_INCOMPLETE = "METHODOLOGY_TASK_COVERAGE_INCOMPLETE"


class _UnitOfWork(Protocol):
    def transaction(self) -> AbstractContextManager[Any]: ...


class MethodologyExtractionTaskPlanningInputError(ValueError):
    """Stable error for invalid task-planning inputs."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.code = PLANNING_INPUT_INVALID


class MethodologyTaskCoverageError(ValueError):
    """Fail-closed error for an incomplete selected methodology task plan."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.code = TASK_COVERAGE_INCOMPLETE


@dataclass(frozen=True)
class StagedMethodologyTaskPlanWrite:
    """Validated Step-6 rows applied only by the fenced completion UoW."""

    deal_id: str
    run_id: str
    selection_id: str
    tasks: tuple[PlannedExtractionTask, ...]
    repository_factory: Callable[[Any], Any]
    lease_owner_id: str
    lease_fencing_token: int

    def persist(
        self,
        *,
        db_conn: Any,
        lease_owner_id: str,
        lease_fencing_token: int,
    ) -> None:
        """Persist the exact plan on the boundary-owned connection."""
        if lease_owner_id != self.lease_owner_id or lease_fencing_token != self.lease_fencing_token:
            raise MethodologyTaskCoverageError("methodology task-plan write was fenced")
        repository = self.repository_factory(db_conn)
        if getattr(repository, "is_durable", False) is not True:
            raise MethodologyTaskCoverageError("methodology task repository must be durable")
        records = list(
            repository.persist_planned_tasks(
                deal_id=self.deal_id,
                run_id=self.run_id,
                selection_id=self.selection_id,
                tasks=list(self.tasks),
                lease_owner_id=lease_owner_id,
                lease_fencing_token=lease_fencing_token,
            )
        )
        persisted = [record.task if hasattr(record, "task") else record for record in records]
        if _task_identity_set(persisted) != _task_identity_set(self.tasks):
            raise MethodologyTaskCoverageError(
                "persisted methodology task set differs from the validated plan"
            )


class DurableRunMethodologyExtractionTaskPlanningService:
    """Persist executable tasks for the exact selected frozen CDD+FDD pair."""

    def __init__(
        self,
        *,
        methodology_repository: Any,
        extraction_repository: Any,
        extraction_repository_factory: Callable[[Any], Any],
        runs_repository: Any,
        unit_of_work: _UnitOfWork,
        lease_owner_id: str,
        lease_fencing_token: int,
        admission_policy: StrictDurableCategorySpanAdmissionPolicy,
    ) -> None:
        if getattr(methodology_repository, "is_durable", False) is not True:
            raise MethodologyTaskCoverageError("methodology repository must be durable")
        if getattr(extraction_repository, "is_durable", False) is not True:
            raise MethodologyTaskCoverageError("extraction repository must be durable")
        if not isinstance(admission_policy, StrictDurableCategorySpanAdmissionPolicy):
            raise MethodologyTaskCoverageError(
                "strict durable category-and-span admission policy is required"
            )
        owner_id = str(lease_owner_id).strip()
        if (
            not owner_id
            or not isinstance(lease_fencing_token, int)
            or isinstance(lease_fencing_token, bool)
            or lease_fencing_token <= 0
        ):
            raise MethodologyTaskCoverageError("an active worker lease is required")
        self._methodology_repository = methodology_repository
        self._extraction_repository = extraction_repository
        self._extraction_repository_factory = extraction_repository_factory
        self._runs_repository = runs_repository
        self._unit_of_work = unit_of_work
        self._lease_owner_id = owner_id
        self._lease_fencing_token = lease_fencing_token
        self._admission_policy = admission_policy

    def plan_for_run(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        document_preflight_summary: dict[str, Any],
    ) -> tuple[
        ExtractionTaskPlanningRunResult,
        list[PlannedExtractionTask],
        StagedMethodologyTaskPlanWrite,
    ]:
        """Validate a complete plan and return its completion-boundary write."""
        try:
            with self._unit_of_work.transaction():
                self._runs_repository.require_run_scope(
                    tenant_id=tenant_id,
                    deal_id=deal_id,
                    run_id=run_id,
                )
                self._runs_repository.require_active_worker_lease(
                    deal_id=deal_id,
                    run_id=run_id,
                    lease_owner_id=self._lease_owner_id,
                    lease_fencing_token=self._lease_fencing_token,
                )
                selection = self._methodology_repository.load_run_selection(
                    tenant_id=tenant_id,
                    deal_id=deal_id,
                    run_id=run_id,
                )
                if selection is None:
                    raise MethodologyTaskCoverageError("run methodology selection is absent")
                registries = tuple(
                    self._methodology_repository.load_selected_registries(selection=selection)
                )
                coverage_records = list(
                    self._methodology_repository.list_coverage_records(
                        deal_id=deal_id,
                        run_id=run_id,
                    )
                )
                _validate_selected_pair_and_coverage(
                    tenant_id=tenant_id,
                    deal_id=deal_id,
                    run_id=run_id,
                    selection=selection,
                    registries=registries,
                    coverage_records=coverage_records,
                )
                preflight = _strict_preflight_run_step_summary(
                    tenant_id=tenant_id,
                    deal_id=deal_id,
                    run_id=run_id,
                    document_preflight_summary=document_preflight_summary,
                )
                if preflight.status is not DocumentPreflightStatus.COMPLETED:
                    raise MethodologyExtractionTaskPlanningInputError(
                        "strict durable planning requires COMPLETED document preflight"
                    )
                classifications = _classification_inputs_from_validated_preflight_summary(
                    tenant_id=tenant_id,
                    deal_id=deal_id,
                    preflight=preflight,
                )
                safe_source_spans = _strict_source_span_references_from_validated_summary(preflight)
                live_source_spans = self._load_and_validate_live_source_spans(
                    deal_id=deal_id,
                    safe_source_spans=safe_source_spans,
                )
                planned = _plan_selected_pair(
                    tenant_id=tenant_id,
                    deal_id=deal_id,
                    run_id=run_id,
                    registries=registries,
                    coverage_records=coverage_records,
                    classifications=classifications,
                    source_spans_by_document_id=live_source_spans,
                    admission_policy=self._admission_policy,
                )
                staged_write = StagedMethodologyTaskPlanWrite(
                    deal_id=deal_id,
                    run_id=run_id,
                    selection_id=str(selection.selection_id),
                    tasks=tuple(planned),
                    repository_factory=self._extraction_repository_factory,
                    lease_owner_id=self._lease_owner_id,
                    lease_fencing_token=self._lease_fencing_token,
                )
        except (WorkerLeaseFencedError, MethodologyTaskCoverageError):
            raise
        except Exception as exc:
            raise MethodologyTaskCoverageError(
                "durable methodology task planning failed closed"
            ) from exc

        result = ExtractionTaskPlanningRunResult.from_tasks(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            tasks=planned,
        )
        return result, planned, staged_write

    def _load_and_validate_live_source_spans(
        self,
        *,
        deal_id: str,
        safe_source_spans: dict[str, list[SourceSpanReference]],
    ) -> dict[str, list[SourceSpanReference]]:
        loader = getattr(
            self._extraction_repository,
            "load_strict_planning_source_spans",
            None,
        )
        if not callable(loader):
            raise MethodologyTaskCoverageError(
                "durable methodology source-span loader is unavailable"
            )
        live_source_spans = loader(
            deal_id=deal_id,
            source_span_references_by_document_id=safe_source_spans,
        )
        return _validate_exact_live_source_spans(
            safe_source_spans=safe_source_spans,
            live_source_spans=live_source_spans,
        )


class InMemoryRunMethodologyExtractionTaskPlanningService:
    """Plan methodology extraction tasks from safe run-step state."""

    def __init__(self, planner: InMemoryExtractionTaskPlanner | None = None) -> None:
        """Initialize the run-scoped planning adapter."""
        self._planner = planner or InMemoryExtractionTaskPlanner()

    def run(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        registry: MethodologyRegistry,
        coverage_records: list[MethodologyCoverageRecord],
        document_preflight_summary: dict[str, Any],
    ) -> tuple[ExtractionTaskPlanningRunResult, list[PlannedExtractionTask]]:
        """Build extraction task metadata without executing extraction."""
        try:
            classifications = _legacy_classifications_from_preflight_summary(
                tenant_id=tenant_id,
                deal_id=deal_id,
                document_preflight_summary=document_preflight_summary,
            )
            source_spans_by_document_id = _source_spans_from_preflight_summary(
                document_preflight_summary=document_preflight_summary,
            )
            planning_result = self._planner.plan_tasks(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                methodology_registry=registry,
                classifications=classifications,
                source_spans_by_document_id=source_spans_by_document_id,
                coverage_records=coverage_records,
            )
        except MethodologyExtractionTaskPlanningInputError:
            raise
        except ValueError as exc:
            raise MethodologyExtractionTaskPlanningInputError(str(exc)) from exc

        tasks: list[PlannedExtractionTask] = list(planning_result.tasks)
        return (
            ExtractionTaskPlanningRunResult.from_tasks(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                tasks=tasks,
            ),
            tasks,
        )


def _validate_selected_pair_and_coverage(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    selection: Any,
    registries: Sequence[MethodologyRegistry],
    coverage_records: Sequence[MethodologyCoverageRecord],
) -> None:
    selection_scope = (
        str(getattr(selection, "tenant_id", "")),
        str(getattr(selection, "deal_id", "")),
        str(getattr(selection, "run_id", "")),
    )
    if selection_scope != (tenant_id, deal_id, run_id):
        raise MethodologyTaskCoverageError("run methodology selection scope mismatch")

    expected_types = {MethodologyType.COMMERCIAL_DD, MethodologyType.FINANCIAL_DD}
    selected_types = [registry.methodology_type for registry in registries]
    if len(registries) != 2 or set(selected_types) != expected_types:
        raise MethodologyTaskCoverageError(
            "selected methodologies must contain exactly one CDD and one FDD version"
        )

    questions_by_id: dict[str, Any] = {}
    question_scope: dict[str, tuple[str, str, MethodologyType]] = {}
    for registry in registries:
        for question in registry.current_version.questions:
            question_id = question.methodology_question_id
            if question_id in questions_by_id:
                raise MethodologyTaskCoverageError("selected methodology question is duplicated")
            questions_by_id[question_id] = question
            question_scope[question_id] = (
                registry.methodology_id,
                registry.current_version.methodology_version_id,
                registry.methodology_type,
            )

    expected_question_count = len(questions_by_id)
    selection_count = getattr(selection, "selected_question_count", expected_question_count)
    if selection_count != expected_question_count:
        raise MethodologyTaskCoverageError("selected question count does not match frozen pair")

    coverage_by_question: dict[str, MethodologyCoverageRecord] = {}
    for record in coverage_records:
        if (record.tenant_id, record.deal_id, record.run_id) != (tenant_id, deal_id, run_id):
            raise MethodologyTaskCoverageError("methodology coverage scope mismatch")
        question_id = record.methodology_question_id
        expected_scope = question_scope.get(question_id)
        actual_scope = (
            record.methodology_id,
            record.methodology_version_id,
            record.methodology_type,
        )
        if expected_scope is None or actual_scope != expected_scope:
            raise MethodologyTaskCoverageError(
                "methodology coverage does not bind the selected question"
            )
        if record.status is not MethodologyCoverageStatus.NOT_STARTED:
            raise MethodologyTaskCoverageError(
                "Task 6 requires every selected coverage row to be applicable and not started"
            )
        if question_id in coverage_by_question:
            raise MethodologyTaskCoverageError("methodology coverage question is duplicated")
        coverage_by_question[question_id] = record

    if set(coverage_by_question) != set(questions_by_id):
        raise MethodologyTaskCoverageError(
            "methodology coverage is incomplete for the selected frozen pair"
        )


def _plan_selected_pair(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    registries: Sequence[MethodologyRegistry],
    coverage_records: Sequence[MethodologyCoverageRecord],
    classifications: Sequence[PlannerClassification],
    source_spans_by_document_id: dict[str, list[SourceSpanReference]],
    admission_policy: StrictDurableCategorySpanAdmissionPolicy,
) -> list[PlannedExtractionTask]:
    by_type: dict[MethodologyType, list[MethodologyCoverageRecord]] = {
        methodology_type: [
            record for record in coverage_records if record.methodology_type is methodology_type
        ]
        for methodology_type in (MethodologyType.COMMERCIAL_DD, MethodologyType.FINANCIAL_DD)
    }
    ready_tasks: list[PlannedExtractionTask] = []
    planner = InMemoryExtractionTaskPlanner(admission_policy=admission_policy)
    for registry in sorted(registries, key=lambda item: item.methodology_type.value):
        result = planner.plan_tasks(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            methodology_registry=registry,
            classifications=classifications,
            source_spans_by_document_id=source_spans_by_document_id,
            coverage_records=by_type[registry.methodology_type],
        )
        ready_tasks.extend(
            task for task in result.tasks if task.status is ExtractionTaskStatus.READY
        )

    expected_question_ids = {record.methodology_question_id for record in coverage_records}
    planned_question_counts = Counter(task.methodology_question_id for task in ready_tasks)
    if set(planned_question_counts) != expected_question_ids:
        raise MethodologyTaskCoverageError(
            "every selected methodology question requires at least one ready extraction task"
        )
    if any(task.coverage_record_id is None for task in ready_tasks):
        raise MethodologyTaskCoverageError("ready task is missing durable coverage lineage")
    return sorted(
        ready_tasks,
        key=lambda task: (
            task.methodology_type.value,
            task.methodology_question_id,
            task.document_id or "",
            task.extraction_task_id or "",
        ),
    )


def _task_identity_set(tasks: Sequence[PlannedExtractionTask]) -> set[str]:
    identities = {str(task.extraction_task_id or "") for task in tasks}
    if "" in identities or len(identities) != len(tasks):
        raise MethodologyTaskCoverageError("ready task identity set is invalid")
    return identities


def _classifications_from_preflight_summary(
    *,
    tenant_id: str,
    deal_id: str,
    document_preflight_summary: dict[str, Any],
) -> list[SafePreflightClassificationInput]:
    preflight = _strict_preflight_run_step_summary(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=_nested_preflight_run_id(document_preflight_summary),
        document_preflight_summary=document_preflight_summary,
    )
    return _classification_inputs_from_validated_preflight_summary(
        tenant_id=tenant_id,
        deal_id=deal_id,
        preflight=preflight,
    )


def _classification_inputs_from_validated_preflight_summary(
    *,
    tenant_id: str,
    deal_id: str,
    preflight: DocumentPreflightRunStepSummary,
) -> list[SafePreflightClassificationInput]:
    return _classification_inputs_from_records(
        tenant_id=tenant_id,
        deal_id=deal_id,
        eligible_document_ids=set(preflight.eligible_document_ids),
        records=preflight.classifications,
    )


def _nested_preflight_run_id(document_preflight_summary: dict[str, Any]) -> str:
    nested = document_preflight_summary.get("summary")
    if type(nested) is not dict:
        raise MethodologyExtractionTaskPlanningInputError(
            "invalid document preflight run-step summary: missing nested run scope"
        )
    run_id = nested.get("run_id")
    if type(run_id) is not str:
        raise MethodologyExtractionTaskPlanningInputError(
            "invalid document preflight run-step summary: missing nested run scope"
        )
    return run_id


def _strict_preflight_run_step_summary(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    document_preflight_summary: dict[str, Any],
) -> DocumentPreflightRunStepSummary:
    if type(document_preflight_summary) is not dict:
        raise MethodologyExtractionTaskPlanningInputError(
            "document preflight run-step summary must be an exact dictionary"
        )
    try:
        preflight = DocumentPreflightRunStepSummary.model_validate(document_preflight_summary)
    except ValidationError as exc:
        raise _preflight_envelope_input_error(exc) from exc
    try:
        preflight.require_scope(tenant_id=tenant_id, deal_id=deal_id, run_id=run_id)
    except ValueError as exc:
        raise MethodologyExtractionTaskPlanningInputError(str(exc)) from exc
    for spans in preflight.source_spans_by_document_id.values():
        for span in spans:
            if span.content_hash is None:
                raise MethodologyExtractionTaskPlanningInputError(
                    "nonexcluded source span requires a content hash"
                )
            if not span.locator:
                raise MethodologyExtractionTaskPlanningInputError(
                    "invalid strict preflight source span: locator must not be empty"
                )
    return preflight


def _preflight_envelope_input_error(
    exc: ValidationError,
) -> MethodologyExtractionTaskPlanningInputError:
    errors = exc.errors(include_url=False)
    if not errors:
        return MethodologyExtractionTaskPlanningInputError(
            "invalid document preflight run-step summary: invalid persisted preflight envelope"
        )
    if any(
        error.get("loc", ()) and error["loc"][0] == "source_spans_by_document_id"
        for error in errors
    ):
        return MethodologyExtractionTaskPlanningInputError("invalid strict preflight source span")
    if any(error.get("loc", ()) and error["loc"][0] == "classifications" for error in errors):
        return MethodologyExtractionTaskPlanningInputError("invalid preflight classification")
    first = errors[0]
    message = str(first.get("msg") or "invalid persisted preflight envelope")
    if message.startswith("Value error, "):
        message = message.removeprefix("Value error, ")
    return MethodologyExtractionTaskPlanningInputError(
        f"invalid document preflight run-step summary: {message}"
    )


def _legacy_classifications_from_preflight_summary(
    *,
    tenant_id: str,
    deal_id: str,
    document_preflight_summary: dict[str, Any],
) -> list[SafePreflightClassificationInput]:
    """Decode only the historical non-strict in-memory summary shape."""
    return _classification_inputs_from_records(
        tenant_id=tenant_id,
        deal_id=deal_id,
        eligible_document_ids=set(
            _list_of_strings(document_preflight_summary, "eligible_document_ids")
        ),
        records=_legacy_preflight_classification_records(document_preflight_summary),
    )


def _strict_preflight_classification_records(
    document_preflight_summary: dict[str, Any],
) -> tuple[DocumentPreflightClassificationSummaryRecord, ...]:
    return _validated_preflight_classification_records(
        _raw_preflight_classifications(document_preflight_summary)
    )


def _legacy_preflight_classification_records(
    document_preflight_summary: dict[str, Any],
) -> tuple[DocumentPreflightClassificationSummaryRecord, ...]:
    """Apply the sole legacy shape exception: absent secondary arrays are empty."""

    payloads: list[dict[str, Any]] = []
    for raw in _raw_preflight_classifications(document_preflight_summary):
        payload = dict(raw)
        payload.setdefault("secondary_fdd_categories", [])
        payload.setdefault("secondary_cdd_categories", [])
        payloads.append(payload)
    return _validated_preflight_classification_records(payloads)


def _raw_preflight_classifications(
    document_preflight_summary: dict[str, Any],
) -> list[dict[str, Any]]:
    raw_classifications = document_preflight_summary.get("classifications")
    if not isinstance(raw_classifications, list):
        raise MethodologyExtractionTaskPlanningInputError("missing preflight classifications")
    if any(not isinstance(raw, dict) for raw in raw_classifications):
        raise MethodologyExtractionTaskPlanningInputError("invalid preflight classification")
    return raw_classifications


def _validated_preflight_classification_records(
    payloads: Sequence[dict[str, Any]],
) -> tuple[DocumentPreflightClassificationSummaryRecord, ...]:
    try:
        records = tuple(
            DocumentPreflightClassificationSummaryRecord.model_validate(payload)
            for payload in payloads
        )
    except ValidationError as exc:
        raise MethodologyExtractionTaskPlanningInputError(
            "invalid preflight classification"
        ) from exc
    identities = [(record.document_id, record.classification_id) for record in records]
    document_ids = [record.document_id for record in records]
    classification_ids = [record.classification_id for record in records]
    if identities != sorted(identities):
        raise MethodologyExtractionTaskPlanningInputError(
            "preflight classifications must be canonically ordered"
        )
    if len(document_ids) != len(set(document_ids)) or len(classification_ids) != len(
        set(classification_ids)
    ):
        raise MethodologyExtractionTaskPlanningInputError(
            "preflight classification identities must be unique"
        )
    return records


def _strict_eligible_document_ids(
    document_preflight_summary: dict[str, Any],
    records: Sequence[DocumentPreflightClassificationSummaryRecord],
) -> list[str]:
    eligible_document_ids = _strict_canonical_string_list(
        document_preflight_summary,
        "eligible_document_ids",
    )
    usable_document_ids = sorted(
        record.document_id for record in records if record.usable_for_methodology_extraction
    )
    if eligible_document_ids != usable_document_ids:
        raise MethodologyExtractionTaskPlanningInputError(
            "eligible document IDs do not match usable classifications"
        )
    return eligible_document_ids


def _classification_inputs_from_records(
    *,
    tenant_id: str,
    deal_id: str,
    eligible_document_ids: set[str],
    records: Sequence[DocumentPreflightClassificationSummaryRecord],
) -> list[SafePreflightClassificationInput]:
    classifications: list[SafePreflightClassificationInput] = []
    for record in records:
        if record.document_id not in eligible_document_ids:
            continue
        if not record.usable_for_methodology_extraction:
            continue
        if record.fdd_category is None or record.cdd_category is None:
            raise MethodologyExtractionTaskPlanningInputError(
                "usable classification requires explicit primary categories"
            )
        classifications.append(
            SafePreflightClassificationInput(
                tenant_id=tenant_id,
                deal_id=deal_id,
                document_id=record.document_id,
                classification_id=record.classification_id,
                fdd_category=record.fdd_category,
                cdd_category=record.cdd_category,
                secondary_fdd_categories=tuple(record.secondary_fdd_categories),
                secondary_cdd_categories=tuple(record.secondary_cdd_categories),
                triage_status=record.triage_status,
                support_status=record.support_status,
                usable_for_methodology_extraction=True,
                methodology_target_areas=list(record.methodology_target_areas),
                reason_codes=list(record.reason_codes),
            )
        )
    return classifications


def _source_spans_from_preflight_summary(
    *,
    document_preflight_summary: dict[str, Any],
) -> dict[str, list[SourceSpanReference]]:
    eligible_document_ids = set(
        _list_of_strings(document_preflight_summary, "eligible_document_ids")
    )
    raw_source_spans = document_preflight_summary.get("source_spans_by_document_id")
    if not isinstance(raw_source_spans, dict):
        raise MethodologyExtractionTaskPlanningInputError("missing preflight source spans")

    spans_by_document_id: dict[str, list[SourceSpanReference]] = {}
    for document_id, raw_spans in raw_source_spans.items():
        doc_id = str(document_id).strip()
        if doc_id not in eligible_document_ids:
            continue
        if not isinstance(raw_spans, list):
            raise MethodologyExtractionTaskPlanningInputError("invalid preflight source spans")
        spans_by_document_id[doc_id] = [
            _source_span_from_summary(doc_id, raw_span) for raw_span in raw_spans
        ]
    return spans_by_document_id


def _strict_source_span_references_from_preflight_summary(
    *,
    document_preflight_summary: dict[str, Any],
) -> dict[str, list[SourceSpanReference]]:
    nested = document_preflight_summary.get("summary")
    if type(nested) is not dict:
        raise MethodologyExtractionTaskPlanningInputError(
            "invalid document preflight run-step summary: missing nested scope"
        )
    preflight = _strict_preflight_run_step_summary(
        tenant_id=str(nested.get("tenant_id") or ""),
        deal_id=str(nested.get("deal_id") or ""),
        run_id=str(nested.get("run_id") or ""),
        document_preflight_summary=document_preflight_summary,
    )
    return _strict_source_span_references_from_validated_summary(preflight)


def _strict_source_span_references_from_validated_summary(
    preflight: DocumentPreflightRunStepSummary,
) -> dict[str, list[SourceSpanReference]]:
    spans_by_document_id: dict[str, list[SourceSpanReference]] = {}
    eligible_document_id_set = set(preflight.eligible_document_ids)
    for document_id in sorted(preflight.source_spans_by_document_id):
        typed_spans = preflight.source_spans_by_document_id[document_id]
        if document_id not in eligible_document_id_set:
            continue
        if not typed_spans:
            raise MethodologyExtractionTaskPlanningInputError(
                "eligible document is missing preflight source spans"
            )
        spans_by_document_id[document_id] = [
            _strict_source_span_reference(span) for span in typed_spans
        ]
    return spans_by_document_id


def _strict_safe_source_span_from_summary(
    document_id: str,
    raw_span: object,
) -> DocumentPreflightSpanReference:
    try:
        span = DocumentPreflightSpanReference.model_validate(raw_span)
    except (TypeError, ValidationError) as exc:
        raise MethodologyExtractionTaskPlanningInputError(
            "invalid strict preflight source span"
        ) from exc
    if span.document_id != document_id:
        raise MethodologyExtractionTaskPlanningInputError("source span document_id mismatch")
    return span


def _strict_source_span_reference(
    span: DocumentPreflightSpanReference,
) -> SourceSpanReference:
    return SourceSpanReference(
        document_id=span.document_id,
        span_id=span.span_id,
        evidence_tags=[],
        locator=dict(span.locator),
        span_type=span.span_type,
        content_hash=span.content_hash,
        text_excerpt=None,
    )


def _validate_exact_live_source_spans(
    *,
    safe_source_spans: dict[str, list[SourceSpanReference]],
    live_source_spans: object,
) -> dict[str, list[SourceSpanReference]]:
    if not isinstance(live_source_spans, dict) or set(live_source_spans) != set(safe_source_spans):
        raise MethodologyTaskCoverageError("durable source-span document set mismatch")
    validated: dict[str, list[SourceSpanReference]] = {}
    for document_id, safe_spans in safe_source_spans.items():
        raw_live_spans = live_source_spans.get(document_id)
        if not isinstance(raw_live_spans, list):
            raise MethodologyTaskCoverageError("durable source-span set is invalid")
        try:
            live_spans = [SourceSpanReference.model_validate(span) for span in raw_live_spans]
        except (TypeError, ValueError) as exc:
            raise MethodologyTaskCoverageError("durable source-span row is invalid") from exc
        safe_by_id = {span.span_id: span for span in safe_spans}
        live_by_id = {span.span_id: span for span in live_spans}
        if len(live_by_id) != len(live_spans) or set(live_by_id) != set(safe_by_id):
            raise MethodologyTaskCoverageError("durable source-span identity set mismatch")
        for span_id, safe_span in safe_by_id.items():
            live_span = live_by_id[span_id]
            if (
                live_span.document_id != safe_span.document_id
                or live_span.content_hash != safe_span.content_hash
                or live_span.locator != safe_span.locator
                or live_span.span_type != safe_span.span_type
            ):
                raise MethodologyTaskCoverageError("durable source-span identity drift")
        validated[document_id] = sorted(live_spans, key=lambda span: span.span_id)
    return validated


def _source_span_from_summary(
    document_id: str,
    raw_span: object,
) -> SourceSpanReference:
    if not isinstance(raw_span, dict):
        raise MethodologyExtractionTaskPlanningInputError("invalid preflight source span")
    locator_value = raw_span.get("locator")
    locator: dict[str, Any] = dict(locator_value) if isinstance(locator_value, dict) else {}
    raw_document_id = _optional_string(raw_span.get("document_id"))
    if raw_document_id is not None and raw_document_id != document_id:
        raise MethodologyExtractionTaskPlanningInputError("source span document_id mismatch")
    return SourceSpanReference(
        document_id=raw_document_id or document_id,
        span_id=_required_string(raw_span, "span_id"),
        evidence_tags=_safe_strings(raw_span.get("evidence_tags")),
        locator=locator,
        span_type=_optional_string(raw_span.get("span_type")),
        content_hash=_optional_string(raw_span.get("content_hash")),
        text_excerpt=None,
    )


def _list_of_strings(payload: dict[str, Any], key: str) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise MethodologyExtractionTaskPlanningInputError(f"missing {key}")
    return _safe_strings(value)


def _strict_canonical_string_list(
    payload: dict[str, Any],
    key: str,
) -> list[str]:
    value = payload.get(key)
    if type(value) is not list:
        raise MethodologyExtractionTaskPlanningInputError(f"missing {key}")
    if any(not isinstance(item, str) or not item or item != item.strip() for item in value):
        raise MethodologyExtractionTaskPlanningInputError(f"invalid {key}")
    if value != sorted(set(value)):
        raise MethodologyExtractionTaskPlanningInputError(f"noncanonical {key}")
    return value


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise MethodologyExtractionTaskPlanningInputError(f"missing {key}")
    return value.strip()


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _safe_strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]
