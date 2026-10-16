"""Deterministic in-memory extraction task planner."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from idis.methodology.category_mapping import (
    METHODOLOGY_CDD_CATEGORY_MAP_DRIFT,
    CommercialDDCategoryMapError,
    CommercialDDQuestionCategoryMap,
    validate_commercial_dd_question_category_map,
)
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
    StrictExtractionTaskAdmission,
)
from idis.models.methodology_coverage import MethodologyCoverageRecord

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
STRICT_CATEGORY_SPAN_ADMISSION_VERSION = "strict-category-span-admission-v2"


@dataclass(frozen=True)
class SafePreflightClassificationInput:
    """Safe planner input reconstructed from DOCUMENT_PREFLIGHT run-step metadata."""

    tenant_id: str
    deal_id: str
    document_id: str
    classification_id: str | None
    fdd_category: FddDocumentCategory
    cdd_category: CddDocumentCategory
    secondary_fdd_categories: tuple[FddDocumentCategory, ...]
    secondary_cdd_categories: tuple[CddDocumentCategory, ...]
    support_status: DocumentSupportStatus
    triage_status: DocumentTriageStatus
    usable_for_methodology_extraction: bool
    methodology_target_areas: list[str] = field(default_factory=list)
    reason_codes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        for values, enum_type in (
            (self.secondary_fdd_categories, FddDocumentCategory),
            (self.secondary_cdd_categories, CddDocumentCategory),
        ):
            if not isinstance(values, tuple) or any(
                not isinstance(item, enum_type) for item in values
            ):
                raise ValueError("secondary categories must be typed tuples")
            canonical_values = [item.value for item in values]
            if canonical_values != sorted(set(canonical_values)):
                raise ValueError("secondary categories must be sorted and unique")


PlannerClassification = DocumentClassification | SafePreflightClassificationInput


@dataclass(frozen=True, slots=True)
class ExtractionTaskAdmissionMatch:
    """One deterministic category match and its optional strict provenance."""

    target_fdd_category: FddDocumentCategory | None
    target_cdd_category: CddDocumentCategory | None
    strict_admission: StrictExtractionTaskAdmission | None = None


class ExtractionTaskAdmissionPolicy(Protocol):
    """Typed policy boundary between legacy tag and strict live-span admission."""

    def match_question(
        self,
        *,
        question: MethodologyQuestion,
        classification: PlannerClassification,
    ) -> ExtractionTaskAdmissionMatch | None: ...

    def validate_spans(
        self,
        *,
        question: MethodologyQuestion,
        classification: PlannerClassification,
        spans: Sequence[SourceSpanReference],
    ) -> ExtractionTaskBlockerReason | None: ...


class LegacyEvidenceTagAdmissionPolicy:
    """Historical non-strict policy retained only for the in-memory seam."""

    def match_question(
        self,
        *,
        question: MethodologyQuestion,
        classification: PlannerClassification,
    ) -> ExtractionTaskAdmissionMatch | None:
        if not _classification_matches_question(question, classification):
            return None
        return ExtractionTaskAdmissionMatch(
            target_fdd_category=classification.fdd_category,
            target_cdd_category=classification.cdd_category,
        )

    def validate_spans(
        self,
        *,
        question: MethodologyQuestion,
        classification: PlannerClassification,
        spans: Sequence[SourceSpanReference],
    ) -> ExtractionTaskBlockerReason | None:
        _ = classification
        if not spans:
            return ExtractionTaskBlockerReason.NO_SOURCE_SPANS
        if not _required_evidence_is_present(question, list(spans)):
            return ExtractionTaskBlockerReason.REQUIRED_EVIDENCE_MISSING
        return None


class StrictDurableCategorySpanAdmissionPolicy:
    """Governed category matching over authenticated durable span identities."""

    def __init__(
        self,
        *,
        cdd_category_map_loader: Callable[[], CommercialDDQuestionCategoryMap],
    ) -> None:
        if not callable(cdd_category_map_loader):
            raise CommercialDDCategoryMapError(METHODOLOGY_CDD_CATEGORY_MAP_DRIFT)
        self._category_map_loader = cdd_category_map_loader
        self._category_map = validate_commercial_dd_question_category_map(cdd_category_map_loader())
        self._cdd_entries = {
            entry.methodology_question_id: entry for entry in self._category_map.questions
        }

    @property
    def category_map(self) -> CommercialDDQuestionCategoryMap:
        """Return the authenticated immutable map bound to this planning policy."""
        return self._category_map

    def match_question(
        self,
        *,
        question: MethodologyQuestion,
        classification: PlannerClassification,
    ) -> ExtractionTaskAdmissionMatch | None:
        if question.methodology_type is MethodologyType.COMMERCIAL_DD:
            entry = self._cdd_entries.get(question.methodology_question_id)
            if (
                question.methodology_id != self._category_map.methodology_id
                or question.methodology_version_id != self._category_map.methodology_version_id
                or entry is None
                or _methodology_question_sha256(question) != entry.methodology_question_sha256
            ):
                raise CommercialDDCategoryMapError(METHODOLOGY_CDD_CATEGORY_MAP_DRIFT)
            available = _complete_cdd_categories(classification)
            matched_values = sorted(set(entry.categories).intersection(available))
            if not matched_values:
                return None
            matched_cdd = matched_values[0]
            return ExtractionTaskAdmissionMatch(
                target_fdd_category=classification.fdd_category,
                target_cdd_category=matched_cdd,
                strict_admission=self._strict_metadata(
                    classification=classification,
                    matched_category=matched_cdd.value,
                ),
            )

        available_fdd = _complete_fdd_categories(classification)
        matched_fdd_values = sorted(_normalized_fdd_targets(question).intersection(available_fdd))
        if not matched_fdd_values:
            return None
        matched_fdd = matched_fdd_values[0]
        return ExtractionTaskAdmissionMatch(
            target_fdd_category=matched_fdd,
            target_cdd_category=classification.cdd_category,
            strict_admission=self._strict_metadata(
                classification=classification,
                matched_category=matched_fdd.value,
            ),
        )

    def validate_spans(
        self,
        *,
        question: MethodologyQuestion,
        classification: PlannerClassification,
        spans: Sequence[SourceSpanReference],
    ) -> ExtractionTaskBlockerReason | None:
        _ = question
        return self._validate_strict_spans(classification=classification, spans=spans)

    @staticmethod
    def _validate_strict_spans(
        *,
        classification: PlannerClassification,
        spans: Sequence[SourceSpanReference],
    ) -> ExtractionTaskBlockerReason | None:
        if not spans:
            return ExtractionTaskBlockerReason.NO_SOURCE_SPANS
        seen_span_ids: set[str] = set()
        for span in spans:
            if (
                span.document_id != classification.document_id
                or span.span_id in seen_span_ids
                or span.content_hash is None
                or len(span.content_hash) != 64
                or any(character not in "0123456789abcdef" for character in span.content_hash)
                or span.text_excerpt is None
                or not span.text_excerpt.strip()
                or hashlib.sha256(span.text_excerpt.encode("utf-8")).hexdigest()
                != span.content_hash
                or not isinstance(span.locator, dict)
                or not span.locator
                or span.span_type is None
                or not span.span_type.strip()
                or bool(span.evidence_tags)
            ):
                raise ValueError("strict durable source span identity is invalid")
            try:
                json.dumps(span.locator, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            except (TypeError, ValueError) as exc:
                raise ValueError("strict durable source locator is invalid") from exc
            seen_span_ids.add(span.span_id)
        return None

    def validate_rehydrated_tasks(self, tasks: Sequence[ExtractionTask]) -> None:
        """Reject stale task payloads under the current governed map/classifier."""
        current = validate_commercial_dd_question_category_map(self._category_map_loader())
        if current != self._category_map or not tasks:
            raise ValueError("strict admission mapping drift")
        for task in tasks:
            admission = task.strict_admission
            classification = _classification_from_admission(task) if admission is not None else None
            if task.methodology_type is MethodologyType.COMMERCIAL_DD:
                cdd_entry = self._cdd_entries.get(task.methodology_question_id)
                category_lineage_is_current = (
                    classification is not None
                    and task.target_fdd_category == classification.fdd_category
                    and task.target_cdd_category is not None
                    and admission is not None
                    and admission.matched_category == task.target_cdd_category.value
                    and task.target_cdd_category in _complete_cdd_categories(classification)
                    and cdd_entry is not None
                    and task.target_cdd_category in cdd_entry.categories
                )
            else:
                category_lineage_is_current = (
                    classification is not None
                    and task.target_cdd_category == classification.cdd_category
                    and task.target_fdd_category is not None
                    and admission is not None
                    and admission.matched_category == task.target_fdd_category.value
                    and task.target_fdd_category in _complete_fdd_categories(classification)
                )
            if (
                task.status is not ExtractionTaskStatus.READY
                or admission is None
                or admission.contract_version != STRICT_CATEGORY_SPAN_ADMISSION_VERSION
                or admission.cdd_category_mapping_version != current.mapping_version
                or admission.cdd_category_mapping_sha256 != current.canonical_payload_sha256
                or admission.classifier_contract_version != current.classifier_version
                or admission.classifier_reachable_categories_sha256
                != current.classifier_reachable_categories_sha256
                or not category_lineage_is_current
            ):
                raise ValueError("strict admission task payload drift")
            if classification is None:
                raise ValueError("strict admission task payload drift")
            self._validate_strict_spans(
                classification=classification,
                spans=task.source_spans,
            )

    def _strict_metadata(
        self,
        *,
        classification: PlannerClassification,
        matched_category: str,
    ) -> StrictExtractionTaskAdmission:
        return StrictExtractionTaskAdmission(
            contract_version=STRICT_CATEGORY_SPAN_ADMISSION_VERSION,
            matched_category=matched_category,
            classified_primary_fdd_category=classification.fdd_category,
            classified_primary_cdd_category=classification.cdd_category,
            classified_secondary_fdd_categories=list(classification.secondary_fdd_categories),
            classified_secondary_cdd_categories=list(classification.secondary_cdd_categories),
            cdd_category_mapping_version=self._category_map.mapping_version,
            cdd_category_mapping_sha256=self._category_map.canonical_payload_sha256,
            classifier_contract_version=self._category_map.classifier_version,
            classifier_reachable_categories_sha256=(
                self._category_map.classifier_reachable_categories_sha256
            ),
        )


class InMemoryExtractionTaskPlanner:
    """Plan extraction task metadata without executing extraction."""

    persistence_backend = "in_memory"
    external_calls_enabled = False

    def __init__(
        self,
        *,
        admission_policy: ExtractionTaskAdmissionPolicy | None = None,
    ) -> None:
        self._admission_policy = admission_policy or LegacyEvidenceTagAdmissionPolicy()

    def plan_tasks(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        methodology_registry: MethodologyRegistry,
        classifications: Sequence[PlannerClassification],
        source_spans_by_document_id: dict[str, list[SourceSpanReference]],
        coverage_records: list[MethodologyCoverageRecord] | None = None,
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
        question_records = _question_records_for_planning(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            methodology_registry=methodology_registry,
            coverage_records=coverage_records,
        )
        for question, coverage_record in question_records:
            question_tasks = self._plan_for_question(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                question=question,
                coverage_record_id=(
                    coverage_record.coverage_record_id if coverage_record is not None else None
                ),
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
        coverage_record_id: str | None,
        classifications: Sequence[PlannerClassification],
        source_spans_by_document_id: dict[str, list[SourceSpanReference]],
    ) -> list[ExtractionTask]:
        if not classifications:
            return [
                _blocked_task(
                    tenant_id=tenant_id,
                    deal_id=deal_id,
                    run_id=run_id,
                    question=question,
                    coverage_record_id=coverage_record_id,
                    classification=None,
                    source_spans=[],
                    blocker_reason=ExtractionTaskBlockerReason.NO_MATCHING_CLASSIFIED_DOCUMENT,
                )
            ]

        category_matches = [
            (classification, admission)
            for classification in classifications
            if (
                admission := self._admission_policy.match_question(
                    question=question,
                    classification=classification,
                )
            )
            is not None
        ]
        if not category_matches:
            return [
                _blocked_task(
                    tenant_id=tenant_id,
                    deal_id=deal_id,
                    run_id=run_id,
                    question=question,
                    coverage_record_id=coverage_record_id,
                    classification=None,
                    source_spans=[],
                    blocker_reason=ExtractionTaskBlockerReason.NO_MATCHING_DOCUMENT_CATEGORY,
                )
            ]

        tasks: list[ExtractionTask] = []
        for classification, admission in category_matches:
            blocker_reason = _triage_blocker_for(classification)
            spans = sorted(
                source_spans_by_document_id.get(classification.document_id, []),
                key=lambda span: span.span_id,
            )
            if blocker_reason is None:
                blocker_reason = self._admission_policy.validate_spans(
                    question=question,
                    classification=classification,
                    spans=spans,
                )

            if blocker_reason is not None:
                tasks.append(
                    _blocked_task(
                        tenant_id=tenant_id,
                        deal_id=deal_id,
                        run_id=run_id,
                        question=question,
                        coverage_record_id=coverage_record_id,
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
                    coverage_record_id=coverage_record_id,
                    document_id=classification.document_id,
                    classification_id=classification.classification_id,
                    source_spans=spans,
                    target_fdd_category=admission.target_fdd_category,
                    target_cdd_category=admission.target_cdd_category,
                    strict_admission=admission.strict_admission,
                    required_evidence=list(question.required_evidence),
                    expected_answer_schema=build_expected_answer_schema(question),
                    validation_requirements=list(question.validation_requirements),
                    reason_codes=["ready"],
                )
            )
        return tasks


def build_expected_answer_schema(question: MethodologyQuestion) -> ExpectedAnswerSchema:
    """Build deterministic expected answer schema metadata from methodology."""
    return ExpectedAnswerSchema(
        answer_type=_infer_answer_type(question),
        question_text=question.question_text,
        required_evidence=list(question.required_evidence),
        required_calculations=list(question.required_calculations),
        validation_requirements=list(question.validation_requirements),
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
    coverage_record_id: str | None,
    classification: PlannerClassification | None,
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
        coverage_record_id=coverage_record_id,
        document_id=classification.document_id if classification else None,
        classification_id=classification.classification_id if classification else None,
        source_spans=source_spans,
        target_fdd_category=classification.fdd_category if classification else None,
        target_cdd_category=classification.cdd_category if classification else None,
        required_evidence=list(question.required_evidence),
        expected_answer_schema=build_expected_answer_schema(question),
        validation_requirements=list(question.validation_requirements),
        reason_codes=[blocker_reason.value],
    )


def _classification_matches_question(
    question: MethodologyQuestion,
    classification: PlannerClassification,
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


def _complete_fdd_categories(
    classification: PlannerClassification,
) -> set[FddDocumentCategory]:
    return {classification.fdd_category, *classification.secondary_fdd_categories}


def _complete_cdd_categories(
    classification: PlannerClassification,
) -> set[CddDocumentCategory]:
    return {classification.cdd_category, *classification.secondary_cdd_categories}


def _methodology_question_sha256(question: MethodologyQuestion) -> str:
    payload = json.dumps(
        question.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _classification_from_admission(task: ExtractionTask) -> SafePreflightClassificationInput:
    admission = task.strict_admission
    if admission is None or task.document_id is None:
        raise ValueError("strict admission is absent")
    return SafePreflightClassificationInput(
        tenant_id=task.tenant_id,
        deal_id=task.deal_id,
        document_id=task.document_id,
        classification_id=task.classification_id,
        fdd_category=admission.classified_primary_fdd_category,
        cdd_category=admission.classified_primary_cdd_category,
        secondary_fdd_categories=tuple(admission.classified_secondary_fdd_categories),
        secondary_cdd_categories=tuple(admission.classified_secondary_cdd_categories),
        support_status=DocumentSupportStatus.SUPPORTED,
        triage_status=DocumentTriageStatus.READY,
        usable_for_methodology_extraction=True,
    )


def _required_evidence_is_present(
    question: MethodologyQuestion,
    spans: list[SourceSpanReference],
) -> bool:
    available_counts = Counter(tag.lower().strip() for span in spans for tag in span.evidence_tags)
    return all(
        available_counts[evidence.evidence_type.lower().strip()] >= evidence.min_count
        for evidence in question.required_evidence
    )


def _triage_blocker_for(
    classification: PlannerClassification,
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


def _question_records_for_planning(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    methodology_registry: MethodologyRegistry,
    coverage_records: list[MethodologyCoverageRecord] | None,
) -> list[tuple[MethodologyQuestion, MethodologyCoverageRecord | None]]:
    questions_by_id = {
        question.methodology_question_id: question
        for question in methodology_registry.current_version.questions
    }
    if coverage_records is None:
        return [(question, None) for question in methodology_registry.current_version.questions]
    if not coverage_records:
        raise ValueError("coverage_records must not be empty")

    seen_question_ids: set[str] = set()
    records_by_question_id: dict[str, MethodologyCoverageRecord] = {}
    for record in coverage_records:
        if record.tenant_id != tenant_id or record.deal_id != deal_id or record.run_id != run_id:
            raise ValueError("coverage record scope mismatch")
        if record.methodology_id != methodology_registry.methodology_id:
            raise ValueError("coverage record methodology_id mismatch")
        if (
            record.methodology_version_id
            != methodology_registry.current_version.methodology_version_id
        ):
            raise ValueError("coverage record methodology_version_id mismatch")
        if record.methodology_type != methodology_registry.methodology_type:
            raise ValueError("coverage record methodology_type mismatch")
        if record.methodology_question_id not in questions_by_id:
            raise ValueError("coverage record question not found in registry")
        if record.methodology_question_id in seen_question_ids:
            raise ValueError("duplicate coverage record for methodology question")
        seen_question_ids.add(record.methodology_question_id)
        records_by_question_id[record.methodology_question_id] = record

    missing_question_ids = sorted(set(questions_by_id) - set(records_by_question_id))
    if missing_question_ids:
        raise ValueError("coverage record missing for methodology question")

    return [
        (question, records_by_question_id[question.methodology_question_id])
        for question in methodology_registry.current_version.questions
    ]
