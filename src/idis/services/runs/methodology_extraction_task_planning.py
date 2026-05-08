"""Run-scoped methodology extraction task planning adapter."""

from __future__ import annotations

from typing import Any

from idis.methodology.models import MethodologyRegistry
from idis.models.document_classification import (
    CddDocumentCategory,
    DocumentSupportStatus,
    DocumentTriageStatus,
    FddDocumentCategory,
)
from idis.models.extraction_task import (
    ExtractionTask,
    ExtractionTaskPlanningRunResult,
    SourceSpanReference,
)
from idis.models.methodology_coverage import MethodologyCoverageRecord
from idis.services.extraction.task_planner import (
    InMemoryExtractionTaskPlanner,
    SafePreflightClassificationInput,
)

PLANNING_INPUT_INVALID = "METHODOLOGY_EXTRACTION_TASK_PLANNING_INPUT_INVALID"


class MethodologyExtractionTaskPlanningInputError(ValueError):
    """Stable error for invalid task-planning inputs."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.code = PLANNING_INPUT_INVALID


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
    ) -> tuple[ExtractionTaskPlanningRunResult, list[ExtractionTask]]:
        """Build extraction task metadata without executing extraction."""
        try:
            classifications = _classifications_from_preflight_summary(
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

        return (
            ExtractionTaskPlanningRunResult.from_tasks(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                tasks=planning_result.tasks,
            ),
            planning_result.tasks,
        )


def _classifications_from_preflight_summary(
    *,
    tenant_id: str,
    deal_id: str,
    document_preflight_summary: dict[str, Any],
) -> list[SafePreflightClassificationInput]:
    eligible_document_ids = set(
        _list_of_strings(document_preflight_summary, "eligible_document_ids")
    )
    classifications: list[SafePreflightClassificationInput] = []
    raw_classifications = document_preflight_summary.get("classifications")
    if not isinstance(raw_classifications, list):
        raise MethodologyExtractionTaskPlanningInputError("missing preflight classifications")

    for raw in raw_classifications:
        if not isinstance(raw, dict):
            raise MethodologyExtractionTaskPlanningInputError("invalid preflight classification")
        document_id = _required_string(raw, "document_id")
        if document_id not in eligible_document_ids:
            continue
        if not bool(raw.get("usable_for_methodology_extraction")):
            continue

        support_status = DocumentSupportStatus(_required_string(raw, "support_status"))
        triage_status = DocumentTriageStatus(_required_string(raw, "triage_status"))
        reason_codes = _safe_non_empty_strings(raw.get("reason_codes"), "preflight_summary")
        classifications.append(
            SafePreflightClassificationInput(
                tenant_id=tenant_id,
                deal_id=deal_id,
                document_id=document_id,
                classification_id=_required_string(raw, "classification_id"),
                fdd_category=_fdd_category(raw.get("fdd_category")),
                cdd_category=_cdd_category(raw.get("cdd_category")),
                triage_status=triage_status,
                support_status=support_status,
                usable_for_methodology_extraction=True,
                methodology_target_areas=_safe_non_empty_strings(
                    raw.get("methodology_target_areas"),
                    "preflight_summary",
                ),
                reason_codes=reason_codes,
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


def _safe_non_empty_strings(value: object, fallback: str) -> list[str]:
    cleaned = _safe_strings(value)
    return cleaned or [fallback]


def _fdd_category(value: object) -> FddDocumentCategory:
    if not isinstance(value, str) or not value.strip():
        return FddDocumentCategory.UNKNOWN
    return FddDocumentCategory(value.strip())


def _cdd_category(value: object) -> CddDocumentCategory:
    if not isinstance(value, str) or not value.strip():
        return CddDocumentCategory.UNKNOWN
    return CddDocumentCategory(value.strip())
