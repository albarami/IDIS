"""Models for methodology-driven extraction task planning."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Iterable, Mapping
from enum import StrEnum
from typing import Any, Generic, Self, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from idis.methodology.models import (
    FDD_FORMULA_POLICY_V2_METHODOLOGY_VERSION_ID,
    FddFormulaRequirement,
    MethodologyType,
    RequiredCalculation,
    RequiredEvidence,
)
from idis.models.document_classification import CddDocumentCategory, FddDocumentCategory


class ExtractionTaskStatus(StrEnum):
    """Planning status for an extraction task."""

    READY = "ready"
    BLOCKED = "blocked"
    EVIDENCE_MISSING = "evidence_missing"
    NOT_APPLICABLE = "not_applicable"


class ExtractionTaskBlockerReason(StrEnum):
    """Machine-readable blocker reasons for extraction task planning."""

    NO_MATCHING_CLASSIFIED_DOCUMENT = "no_matching_classified_document"
    NO_MATCHING_DOCUMENT_CATEGORY = "no_matching_document_category"
    UNSUPPORTED_SOURCE = "unsupported_source"
    CONVERSION_REQUIRED = "conversion_required"
    OCR_REQUIRED = "ocr_required"
    ENCRYPTED_SOURCE = "encrypted_source"
    CORRUPTED_SOURCE = "corrupted_source"
    TOO_LARGE = "too_large"
    UNKNOWN_PARSER_STATUS = "unknown_parser_status"
    NO_SOURCE_SPANS = "no_source_spans"
    REQUIRED_EVIDENCE_MISSING = "required_evidence_missing"


class ExtractionTaskBaseModel(BaseModel):
    """Base model for extraction task metadata."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SourceSpanReference(ExtractionTaskBaseModel):
    """Source span that can support a future extraction task."""

    # Raw source text is content-addressed and must retain its exact UTF-8 bytes.
    # Identifier/type/tag validators below still normalize their own fields.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    document_id: str
    span_id: str
    evidence_tags: list[str] = Field(default_factory=list)
    locator: dict[str, Any] = Field(default_factory=dict)
    span_type: str | None = None
    content_hash: str | None = None
    text_excerpt: str | None = None

    @field_validator("document_id", "span_id")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @field_validator("span_type", "content_hash")
    @classmethod
    def _optional_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            raise ValueError("field must not be blank when provided")
        return value.strip()

    @field_validator("evidence_tags")
    @classmethod
    def _evidence_tags_not_blank(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("evidence_tags must not contain blank values")
        return sorted(set(cleaned))


class ExpectedAnswerSchema(ExtractionTaskBaseModel):
    """Metadata describing the future extractor output shape."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    answer_type: str
    question_text: str
    required_evidence: list[RequiredEvidence]
    required_calculations: list[RequiredCalculation] = Field(default_factory=list)
    validation_requirements: list[str]
    report_section: str
    report_subsection: str | None = None
    methodology_type: MethodologyType
    methodology_section: str

    @field_validator("answer_type", "report_section", "methodology_section")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @field_validator("question_text")
    @classmethod
    def _question_text_not_blank_and_exact(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question_text must not be blank")
        return value

    @field_validator("report_subsection")
    @classmethod
    def _normalize_report_subsection(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("required_evidence", "validation_requirements")
    @classmethod
    def _non_empty_list(cls, value: list[Any]) -> list[Any]:
        if not value:
            raise ValueError("list must not be empty")
        return value

    @field_validator("validation_requirements")
    @classmethod
    def _list_items_not_blank(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("list items must not be blank")
        return cleaned


class FddPolicyV2ExpectedAnswerSchema(ExtractionTaskBaseModel):
    """Closed expected-answer metadata for the exact FDD policy-v2 version."""

    model_config = ConfigDict(
        extra="forbid", str_strip_whitespace=False, revalidate_instances="always"
    )

    answer_type: str
    question_text: str
    required_evidence: list[RequiredEvidence]
    required_calculations: list[FddFormulaRequirement]
    validation_requirements: list[str]
    report_section: str
    report_subsection: str | None = None
    methodology_type: MethodologyType
    methodology_section: str

    @field_validator("answer_type", "report_section", "methodology_section")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @field_validator("question_text")
    @classmethod
    def _question_text_not_blank_and_exact(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question_text must not be blank")
        return value

    @field_validator("report_subsection")
    @classmethod
    def _normalize_report_subsection(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("required_evidence", "validation_requirements")
    @classmethod
    def _non_empty_list(cls, value: list[object]) -> list[object]:
        if not value:
            raise ValueError("list must not be empty")
        return value

    @field_validator("validation_requirements")
    @classmethod
    def _list_items_not_blank(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("list items must not be blank")
        return cleaned

    @field_validator("required_calculations", mode="before")
    @classmethod
    def _closed_requirements(cls, value: object) -> object:
        if not isinstance(value, (list, tuple)):
            raise ValueError("policy-v2 requirements must be a sequence")
        checked: list[FddFormulaRequirement] = []
        for item in value:
            if isinstance(item, BaseModel):
                if type(item) is not FddFormulaRequirement:
                    raise ValueError("policy-v2 requirement type is not admitted")
                item = item.model_dump(mode="python", warnings="error")
            checked.append(FddFormulaRequirement.model_validate(item))
        return checked


class StrictExtractionTaskAdmission(ExtractionTaskBaseModel):
    """Content-bound strict category/classifier admission carried by a task."""

    contract_version: str
    matched_category: str
    classified_primary_fdd_category: FddDocumentCategory
    classified_primary_cdd_category: CddDocumentCategory
    classified_secondary_fdd_categories: list[FddDocumentCategory]
    classified_secondary_cdd_categories: list[CddDocumentCategory]
    cdd_category_mapping_version: str
    cdd_category_mapping_sha256: str
    classifier_contract_version: str
    classifier_reachable_categories_sha256: str

    @field_validator(
        "contract_version",
        "matched_category",
        "cdd_category_mapping_version",
        "classifier_contract_version",
    )
    @classmethod
    def _strict_admission_string_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("strict admission field must not be blank")
        return value.strip()

    @field_validator("cdd_category_mapping_sha256", "classifier_reachable_categories_sha256")
    @classmethod
    def _strict_admission_hash_is_lowercase_sha256(cls, value: str) -> str:
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError("strict admission hash must be a lowercase SHA-256")
        return value

    @field_validator(
        "classified_secondary_fdd_categories",
        "classified_secondary_cdd_categories",
    )
    @classmethod
    def _strict_admission_secondaries_are_canonical(cls, value: list[Any]) -> list[Any]:
        canonical = [item.value for item in value]
        if canonical != sorted(set(canonical)):
            raise ValueError("strict admission secondaries must be sorted and unique")
        return value


_AnswerSchemaT = TypeVar("_AnswerSchemaT", ExpectedAnswerSchema, FddPolicyV2ExpectedAnswerSchema)


class _ExtractionTask(ExtractionTaskBaseModel, Generic[_AnswerSchemaT]):
    """Shared task identity and status behavior; concrete families stay disjoint."""

    tenant_id: str
    deal_id: str
    run_id: str
    extraction_task_id: str | None = None
    status: ExtractionTaskStatus
    blocker_reason: ExtractionTaskBlockerReason | None = None
    reason_codes: list[str]
    methodology_id: str
    methodology_version_id: str
    methodology_question_id: str
    methodology_type: MethodologyType
    methodology_section: str
    coverage_record_id: str | None = None
    document_id: str | None = None
    classification_id: str | None = None
    source_spans: list[SourceSpanReference] = Field(default_factory=list)
    target_fdd_category: FddDocumentCategory | None = None
    target_cdd_category: CddDocumentCategory | None = None
    strict_admission: StrictExtractionTaskAdmission | None = None
    required_evidence: list[RequiredEvidence]
    expected_answer_schema: _AnswerSchemaT
    validation_requirements: list[str]

    @property
    def source_span_ids(self) -> list[str]:
        """Return deterministic source span IDs."""
        return sorted(span.span_id for span in self.source_spans)

    @model_validator(mode="before")
    @classmethod
    def _admit_exact_version(cls, value: object) -> object:
        if isinstance(value, Mapping):
            version = value.get("methodology_version_id")
            if cls is FddPolicyV2ExtractionTask:
                if version != FDD_FORMULA_POLICY_V2_METHODOLOGY_VERSION_ID:
                    raise ValueError("policy-v2 task requires its exact methodology version")
            elif cls is ExtractionTask and version == FDD_FORMULA_POLICY_V2_METHODOLOGY_VERSION_ID:
                raise ValueError("policy-v2 methodology requires its disjoint task model")
        return value

    @field_validator("expected_answer_schema", mode="before")
    @classmethod
    def _admit_exact_answer_schema(cls, value: object) -> object:
        expected = (
            FddPolicyV2ExpectedAnswerSchema
            if cls is FddPolicyV2ExtractionTask
            else ExpectedAnswerSchema
        )
        if isinstance(value, (ExpectedAnswerSchema, FddPolicyV2ExpectedAnswerSchema)):
            if type(value) is not expected:
                raise ValueError("expected-answer schema type is not admitted")
            for requirement in value.required_calculations:
                admitted = (
                    FddFormulaRequirement
                    if cls is FddPolicyV2ExtractionTask
                    else RequiredCalculation
                )
                if type(requirement) is not admitted:
                    raise ValueError("calculation requirement type is not admitted")
            value = value.model_dump(mode="python", warnings="error")
        elif isinstance(value, BaseModel):
            raise ValueError("expected-answer schema type is not admitted")
        return expected.model_validate(value)

    @field_validator(
        "tenant_id",
        "deal_id",
        "run_id",
        "methodology_id",
        "methodology_version_id",
        "methodology_question_id",
        "methodology_section",
    )
    @classmethod
    def _required_strings_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @field_validator("extraction_task_id", "coverage_record_id", "document_id", "classification_id")
    @classmethod
    def _optional_strings_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not value.strip():
            raise ValueError("field must not be blank when provided")
        return value.strip()

    @field_validator("extraction_task_id")
    @classmethod
    def _task_id_format(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith("et_"):
            raise ValueError("extraction_task_id must start with et_")
        return value

    @field_validator("reason_codes", "validation_requirements")
    @classmethod
    def _string_list_items_not_blank(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("list must not be empty")
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("list items must not be blank")
        return cleaned

    @field_validator("required_evidence")
    @classmethod
    def _required_evidence_not_empty(cls, value: list[RequiredEvidence]) -> list[RequiredEvidence]:
        if not value:
            raise ValueError("required_evidence must not be empty")
        return value

    @model_validator(mode="after")
    def _validate_status_invariants(self) -> Self:
        if type(self) not in {ExtractionTask, FddPolicyV2ExtractionTask}:
            raise ValueError("extraction task subclass is not admitted")
        is_policy = self.methodology_version_id == FDD_FORMULA_POLICY_V2_METHODOLOGY_VERSION_ID
        if is_policy != (type(self) is FddPolicyV2ExtractionTask):
            raise ValueError("extraction task family and methodology version disagree")
        self._admit_exact_answer_schema(self.expected_answer_schema)
        if is_policy and (
            self.methodology_id != MethodologyType.FINANCIAL_DD.value
            or self.methodology_type is not MethodologyType.FINANCIAL_DD
            or self.expected_answer_schema.methodology_type is not MethodologyType.FINANCIAL_DD
        ):
            raise ValueError("policy-v2 task requires financial methodology identity")
        if self.status == ExtractionTaskStatus.READY:
            if not self.source_spans:
                raise ValueError("ready extraction tasks require source spans")
            if self.blocker_reason is not None:
                raise ValueError("ready extraction tasks must not have blocker_reason")
            if self.document_id is None:
                raise ValueError("ready extraction tasks require document_id")
            if self.classification_id is None:
                raise ValueError("ready extraction tasks require classification_id")
        elif self.blocker_reason is None:
            raise ValueError("non-ready extraction tasks require blocker_reason")

        if not self.extraction_task_id:
            self.extraction_task_id = generate_extraction_task_id(
                tenant_id=self.tenant_id,
                deal_id=self.deal_id,
                run_id=self.run_id,
                methodology_question_id=self.methodology_question_id,
                coverage_record_id=self.coverage_record_id,
                document_id=self.document_id,
                source_span_ids=self.source_span_ids,
                target_fdd_category=self.target_fdd_category,
                target_cdd_category=self.target_cdd_category,
                strict_admission=self.strict_admission,
                status=self.status,
                blocker_reason=self.blocker_reason,
            )
        return self

    def to_deterministic_json(self) -> str:
        """Serialize the task deterministically."""
        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )


class ExtractionTask(_ExtractionTask[ExpectedAnswerSchema]):
    """Metadata-only extraction task produced by deterministic planning."""


class FddPolicyV2ExtractionTask(_ExtractionTask[FddPolicyV2ExpectedAnswerSchema]):
    """Extraction task admitted only for the exact FDD policy-v2 methodology."""


PlannedExtractionTask = ExtractionTask | FddPolicyV2ExtractionTask


def decode_extraction_task(value: object) -> PlannedExtractionTask:
    """Decode one task by its existing outer version, never by shape guessing."""
    if isinstance(value, (ExtractionTask, FddPolicyV2ExtractionTask)):
        if type(value) not in {ExtractionTask, FddPolicyV2ExtractionTask}:
            raise ValueError("extraction task type is not admitted")
        if (value.methodology_version_id == FDD_FORMULA_POLICY_V2_METHODOLOGY_VERSION_ID) != (
            type(value) is FddPolicyV2ExtractionTask
        ):
            raise ValueError("extraction task family and methodology version disagree")
        value._admit_exact_answer_schema(value.expected_answer_schema)
        value = value.model_dump(mode="python")
    elif isinstance(value, BaseModel):
        raise ValueError("extraction task type is not admitted")
    if not isinstance(value, Mapping) or type(value.get("methodology_version_id")) is not str:
        raise ValueError("extraction task requires an outer methodology version")
    if value["methodology_version_id"] == FDD_FORMULA_POLICY_V2_METHODOLOGY_VERSION_ID:
        return FddPolicyV2ExtractionTask.model_validate(value)
    return ExtractionTask.model_validate(value)


class ExtractionTaskSummary(ExtractionTaskBaseModel):
    """Deterministic summary of planned extraction tasks."""

    tenant_id: str
    deal_id: str
    run_id: str
    total_tasks: int
    by_status: dict[str, int]
    by_blocker_reason: dict[str, int]


class ExtractionTaskPlanningResult(ExtractionTaskBaseModel):
    """Planning result containing tasks and summary."""

    tasks: list[ExtractionTask]
    summary: ExtractionTaskSummary


class FddPolicyV2ExtractionTaskPlanningResult(ExtractionTaskBaseModel):
    """Planning result for the disjoint FDD policy-v2 task family."""

    tasks: list[FddPolicyV2ExtractionTask]
    summary: ExtractionTaskSummary


class ExtractionTaskRunStepSummaryRecord(ExtractionTaskBaseModel):
    """Run-step-safe extraction task summary record."""

    extraction_task_id: str
    status: ExtractionTaskStatus
    blocker_reason: ExtractionTaskBlockerReason | None = None
    coverage_record_id: str | None = None
    methodology_id: str
    methodology_version_id: str
    methodology_question_id: str
    methodology_type: MethodologyType
    document_id: str | None = None
    classification_id: str | None = None
    source_span_ids: list[str]
    source_span_count: int

    @classmethod
    def from_task(cls, task: PlannedExtractionTask) -> ExtractionTaskRunStepSummaryRecord:
        """Build a safe summary from a full in-memory extraction task."""
        if task.extraction_task_id is None:
            raise ValueError("extraction_task_id must be populated before summarizing")
        source_span_ids = task.source_span_ids
        return cls(
            extraction_task_id=task.extraction_task_id,
            status=task.status,
            blocker_reason=task.blocker_reason,
            coverage_record_id=task.coverage_record_id,
            methodology_id=task.methodology_id,
            methodology_version_id=task.methodology_version_id,
            methodology_question_id=task.methodology_question_id,
            methodology_type=task.methodology_type,
            document_id=task.document_id,
            classification_id=task.classification_id,
            source_span_ids=source_span_ids,
            source_span_count=len(source_span_ids),
        )


class ExtractionTaskPlanningRunResult(ExtractionTaskBaseModel):
    """Run-step-safe wrapper around planned extraction tasks."""

    tenant_id: str
    deal_id: str
    run_id: str
    task_ids: list[str]
    tasks: list[ExtractionTaskRunStepSummaryRecord]
    summary: ExtractionTaskSummary
    by_reason_code: dict[str, int]

    @classmethod
    def from_tasks(
        cls,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        tasks: list[PlannedExtractionTask],
    ) -> ExtractionTaskPlanningRunResult:
        """Build a run-step-safe planning result from full task records."""
        task_summaries = [ExtractionTaskRunStepSummaryRecord.from_task(task) for task in tasks]
        return cls(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            task_ids=[task.extraction_task_id for task in tasks if task.extraction_task_id],
            tasks=task_summaries,
            summary=_build_task_summary(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                tasks=tasks,
            ),
            by_reason_code=_counter(
                reason_code for task in tasks for reason_code in task.reason_codes
            ),
        )

    def to_run_step_summary(self, *, status: str = "COMPLETED") -> dict[str, object]:
        """Return a compact result_summary without registry or document text."""
        return {
            "status": status,
            "task_ids": list(self.task_ids),
            "tasks": [task.model_dump(mode="json") for task in self.tasks],
            "summary": {
                "total_tasks": self.summary.total_tasks,
                "by_status": dict(self.summary.by_status),
                "by_blocker_reason": dict(self.summary.by_blocker_reason),
                "by_reason_code": dict(self.by_reason_code),
            },
        }


def generate_extraction_task_id(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    methodology_question_id: str,
    coverage_record_id: str | None = None,
    document_id: str | None,
    source_span_ids: list[str],
    target_fdd_category: FddDocumentCategory | None,
    target_cdd_category: CddDocumentCategory | None,
    strict_admission: StrictExtractionTaskAdmission | None = None,
    status: ExtractionTaskStatus,
    blocker_reason: ExtractionTaskBlockerReason | None,
) -> str:
    """Generate a stable run-scoped task ID from task identity fields.

    ``coverage_record_id`` intentionally participates in the seed because Slice 4
    task plans are scoped to a run coverage ledger, not to global methodology
    questions alone.
    """
    seed = {
        "tenant_id": tenant_id,
        "deal_id": deal_id,
        "run_id": run_id,
        "methodology_question_id": methodology_question_id,
        "coverage_record_id": coverage_record_id or "no_coverage_record",
        "document_id": document_id or "no_document",
        "source_span_ids": sorted(source_span_ids),
        "target_fdd_category": target_fdd_category.value if target_fdd_category else "none",
        "target_cdd_category": target_cdd_category.value if target_cdd_category else "none",
        "strict_admission": (
            strict_admission.model_dump(mode="json") if strict_admission is not None else None
        ),
        "status": status.value,
        "blocker_reason": blocker_reason.value if blocker_reason else "none",
    }
    encoded = json.dumps(seed, sort_keys=True, separators=(",", ":"))
    return f"et_{hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:24]}"


def _build_task_summary(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    tasks: list[PlannedExtractionTask],
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
