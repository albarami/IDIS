"""Deterministic in-memory extraction task planner."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from idis.methodology.models import MethodologyQuestion, MethodologyRegistry, MethodologyType
from idis.models.document_classification import (
    CddDocumentCategory,
    DocumentClassification,
    DocumentSupportStatus,
    DocumentTriageStatus,
    FddDocumentCategory,
)
from idis.models.extraction_task import (
    ExpectedAnswerSchema,
    ExtractionTask,
    ExtractionTaskBlockerReason,
    ExtractionTaskPlanningResult,
    ExtractionTaskStatus,
    ExtractionTaskSummary,
    SourceSpanReference,
)

_READY_SUPPORT_STATUSES = {
    DocumentSupportStatus.SUPPORTED,
    DocumentSupportStatus.PARTIALLY_SUPPORTED,
}
_READY_TRIAGE_STATUSES = {
    DocumentTriageStatus.READY,
    DocumentTriageStatus.PARTIAL,
}
_SUPPORT_BLOCKERS: dict[DocumentSupportStatus, ExtractionTaskBlockerReason] = {
    DocumentSupportStatus.UNSUPPORTED: ExtractionTaskBlockerReason.UNSUPPORTED_SOURCE,
    DocumentSupportStatus.CONVERSION_REQUIRED: ExtractionTaskBlockerReason.CONVERSION_REQUIRED,
    DocumentSupportStatus.SCANNED_OR_IMAGE_ONLY: ExtractionTaskBlockerReason.OCR_REQUIRED,
    DocumentSupportStatus.ENCRYPTED: ExtractionTaskBlockerReason.ENCRYPTED_SOURCE,
    DocumentSupportStatus.CORRUPTED: ExtractionTaskBlockerReason.CORRUPTED_SOURCE,
    DocumentSupportStatus.TOO_LARGE: ExtractionTaskBlockerReason.TOO_LARGE,
    DocumentSupportStatus.UNKNOWN: ExtractionTaskBlockerReason.UNKNOWN_PARSER_STATUS,
}
_TRIAGE_BLOCKERS: dict[DocumentTriageStatus, ExtractionTaskBlockerReason] = {
    DocumentTriageStatus.UNSUPPORTED_SOURCE: ExtractionTaskBlockerReason.UNSUPPORTED_SOURCE,
    DocumentTriageStatus.CONVERSION_REQUIRED: ExtractionTaskBlockerReason.CONVERSION_REQUIRED,
    DocumentTriageStatus.OCR_REQUIRED: ExtractionTaskBlockerReason.OCR_REQUIRED,
    DocumentTriageStatus.TOO_LARGE: ExtractionTaskBlockerReason.TOO_LARGE,
    DocumentTriageStatus.UNKNOWN: ExtractionTaskBlockerReason.UNKNOWN_PARSER_STATUS,
}
_FDD_CATEGORY_ALIASES: dict[str, set[FddDocumentCategory]] = {
    "financial_statement": {
        FddDocumentCategory.FINANCIAL_SCHEDULE_MODEL,
        FddDocumentCategory.PL_SUPPORT,
        FddDocumentCategory.CASH_FLOW_SUPPORT,
        FddDocumentCategory.BALANCE_SHEET_SUPPORT,
    },
    "workbook_support": {
        FddDocumentCategory.FINANCIAL_SCHEDULE_MODEL,
        FddDocumentCategory.PL_SUPPORT,
        FddDocumentCategory.CASH_FLOW_SUPPORT,
        FddDocumentCategory.BALANCE_SHEET_SUPPORT,
    },
    "finance_model": {FddDocumentCategory.FINANCIAL_SCHEDULE_MODEL},
    "market_report": {FddDocumentCategory.MARKET_RESEARCH},
    "customer_contract": {FddDocumentCategory.CUSTOMER_CONTRACT},
}
_CDD_CATEGORY_ALIASES: dict[str, set[CddDocumentCategory]] = {
    "business_plan": {CddDocumentCategory.BUSINESS_PLAN_ASSUMPTIONS},
    "finance_model": {CddDocumentCategory.PRICING_UNIT_ECONOMICS},
    "market_report": {CddDocumentCategory.MARKET_RESEARCH},
    "customer_contract": {
        CddDocumentCategory.CUSTOMER_EVIDENCE,
        CddDocumentCategory.COMMERCIAL_CONTRACTS,
    },
    "contracts": {CddDocumentCategory.COMMERCIAL_CONTRACTS},
    "customer_report": {CddDocumentCategory.CUSTOMER_EVIDENCE},
}


class InMemoryExtractionTaskPlanner:
    """Plan extraction task metadata without executing extraction."""

    persistence_backend = "in_memory"
    external_calls_enabled = False

    def plan_tasks(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        methodology_registry: MethodologyRegistry,
        classifications: list[DocumentClassification],
        source_spans_by_document_id: dict[str, list[SourceSpanReference]],
    ) -> ExtractionTaskPlanningResult:
        """Plan deterministic extraction task metadata."""
        scoped_classifications = sorted(
            [
                classification
                for classification in classifications
                if classification.tenant_id == tenant_id and classification.deal_id == deal_id
            ],
            key=lambda classification: (
                classification.document_id,
                classification.classification_id or "",
            ),
        )

        tasks: list[ExtractionTask] = []
        for question in methodology_registry.current_version.questions:
            question_tasks = self._plan_for_question(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                question=question,
                classifications=scoped_classifications,
                source_spans_by_document_id=source_spans_by_document_id,
            )
            tasks.extend(question_tasks)

        tasks = sorted(
            tasks,
            key=lambda task: (
                task.methodology_question_id,
                task.document_id or "",
                task.extraction_task_id or "",
            ),
        )
        return ExtractionTaskPlanningResult(
            tasks=tasks,
            summary=_build_summary(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                tasks=tasks,
            ),
        )

    def _plan_for_question(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        question: MethodologyQuestion,
        classifications: list[DocumentClassification],
        source_spans_by_document_id: dict[str, list[SourceSpanReference]],
    ) -> list[ExtractionTask]:
        if not classifications:
            return [
                _blocked_task(
                    tenant_id=tenant_id,
                    deal_id=deal_id,
                    run_id=run_id,
                    question=question,
                    classification=None,
                    source_spans=[],
                    blocker_reason=ExtractionTaskBlockerReason.NO_MATCHING_CLASSIFIED_DOCUMENT,
                )
            ]

        category_matches = [
            classification
            for classification in classifications
            if _classification_matches_question(question, classification)
        ]
        if not category_matches:
            return [
                _blocked_task(
                    tenant_id=tenant_id,
                    deal_id=deal_id,
                    run_id=run_id,
                    question=question,
                    classification=None,
                    source_spans=[],
                    blocker_reason=ExtractionTaskBlockerReason.NO_MATCHING_DOCUMENT_CATEGORY,
                )
            ]

        tasks: list[ExtractionTask] = []
        for classification in category_matches:
            blocker_reason = _triage_blocker_for(classification)
            spans = sorted(
                source_spans_by_document_id.get(classification.document_id, []),
                key=lambda span: span.span_id,
            )
            if blocker_reason is None and not spans:
                blocker_reason = ExtractionTaskBlockerReason.NO_SOURCE_SPANS
            if blocker_reason is None and not _required_evidence_is_present(question, spans):
                blocker_reason = ExtractionTaskBlockerReason.REQUIRED_EVIDENCE_MISSING

            if blocker_reason is not None:
                tasks.append(
                    _blocked_task(
                        tenant_id=tenant_id,
                        deal_id=deal_id,
                        run_id=run_id,
                        question=question,
                        classification=classification,
                        source_spans=spans,
                        blocker_reason=blocker_reason,
                    )
                )
                continue

            tasks.append(
                ExtractionTask(
                    tenant_id=tenant_id,
                    deal_id=deal_id,
                    run_id=run_id,
                    status=ExtractionTaskStatus.READY,
                    methodology_id=question.methodology_id,
                    methodology_version_id=question.methodology_version_id,
                    methodology_question_id=question.methodology_question_id,
                    methodology_type=question.methodology_type,
                    methodology_section=question.section,
                    document_id=classification.document_id,
                    classification_id=classification.classification_id,
                    source_spans=spans,
                    target_fdd_category=classification.fdd_category,
                    target_cdd_category=classification.cdd_category,
                    required_evidence=question.required_evidence,
                    expected_answer_schema=build_expected_answer_schema(question),
                    validation_requirements=question.validation_requirements,
                    reason_codes=["ready"],
                )
            )
        return tasks


def build_expected_answer_schema(question: MethodologyQuestion) -> ExpectedAnswerSchema:
    """Build deterministic expected answer schema metadata from methodology."""
    return ExpectedAnswerSchema(
        answer_type=_infer_answer_type(question),
        question_text=question.question_text,
        required_evidence=question.required_evidence,
        required_calculations=question.required_calculations,
        validation_requirements=question.validation_requirements,
        report_section=question.report_mapping.report_section,
        report_subsection=question.report_mapping.report_subsection,
        methodology_type=question.methodology_type,
        methodology_section=question.section,
    )


def _blocked_task(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    question: MethodologyQuestion,
    classification: DocumentClassification | None,
    source_spans: list[SourceSpanReference],
    blocker_reason: ExtractionTaskBlockerReason,
) -> ExtractionTask:
    status = (
        ExtractionTaskStatus.EVIDENCE_MISSING
        if blocker_reason == ExtractionTaskBlockerReason.REQUIRED_EVIDENCE_MISSING
        else ExtractionTaskStatus.BLOCKED
    )
    return ExtractionTask(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        status=status,
        blocker_reason=blocker_reason,
        methodology_id=question.methodology_id,
        methodology_version_id=question.methodology_version_id,
        methodology_question_id=question.methodology_question_id,
        methodology_type=question.methodology_type,
        methodology_section=question.section,
        document_id=classification.document_id if classification else None,
        classification_id=classification.classification_id if classification else None,
        source_spans=source_spans,
        target_fdd_category=classification.fdd_category if classification else None,
        target_cdd_category=classification.cdd_category if classification else None,
        required_evidence=question.required_evidence,
        expected_answer_schema=build_expected_answer_schema(question),
        validation_requirements=question.validation_requirements,
        reason_codes=[blocker_reason.value],
    )


def _classification_matches_question(
    question: MethodologyQuestion,
    classification: DocumentClassification,
) -> bool:
    if question.methodology_type == MethodologyType.FINANCIAL_DD:
        return classification.fdd_category in _normalized_fdd_targets(question)
    return classification.cdd_category in _normalized_cdd_targets(question)


def _normalized_fdd_targets(question: MethodologyQuestion) -> set[FddDocumentCategory]:
    targets: set[FddDocumentCategory] = set()
    for target in question.target_document_categories:
        normalized = target.lower().strip()
        targets.update(_FDD_CATEGORY_ALIASES.get(normalized, set()))
        try:
            targets.add(FddDocumentCategory(normalized))
        except ValueError:
            continue
    return targets


def _normalized_cdd_targets(question: MethodologyQuestion) -> set[CddDocumentCategory]:
    targets: set[CddDocumentCategory] = set()
    for target in question.target_document_categories:
        normalized = target.lower().strip()
        targets.update(_CDD_CATEGORY_ALIASES.get(normalized, set()))
        try:
            targets.add(CddDocumentCategory(normalized))
        except ValueError:
            continue
    return targets


def _required_evidence_is_present(
    question: MethodologyQuestion,
    spans: list[SourceSpanReference],
) -> bool:
    available_counts = Counter(
        tag.lower().strip()
        for span in spans
        for tag in span.evidence_tags
    )
    return all(
        available_counts[evidence.evidence_type.lower().strip()] >= evidence.min_count
        for evidence in question.required_evidence
    )


def _triage_blocker_for(
    classification: DocumentClassification,
) -> ExtractionTaskBlockerReason | None:
    support_blocker = _SUPPORT_BLOCKERS.get(classification.support_status)
    if support_blocker is not None:
        return support_blocker
    if classification.support_status not in _READY_SUPPORT_STATUSES:
        return ExtractionTaskBlockerReason.UNKNOWN_PARSER_STATUS

    triage_blocker = _TRIAGE_BLOCKERS.get(classification.triage_status)
    if triage_blocker is not None:
        return triage_blocker
    if classification.triage_status not in _READY_TRIAGE_STATUSES:
        return ExtractionTaskBlockerReason.UNKNOWN_PARSER_STATUS
    return None


def _infer_answer_type(question: MethodologyQuestion) -> str:
    lowered = question.question_text.lower()
    if lowered.startswith(("is ", "are ", "does ", "do ", "has ", "have ")):
        return "boolean"
    if lowered.startswith(("how many", "how much")):
        return "numeric"
    if lowered.startswith(
        (
            "provide a table",
            "list the table",
            "provide schedule",
            "list schedule",
        )
    ):
        return "table"
    return "narrative"


def _build_summary(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    tasks: list[ExtractionTask],
) -> ExtractionTaskSummary:
    return ExtractionTaskSummary(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        total_tasks=len(tasks),
        by_status=_counter(task.status.value for task in tasks),
        by_blocker_reason=_counter(
            task.blocker_reason.value for task in tasks if task.blocker_reason is not None
        ),
    )


def _counter(items: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(items).items()))
