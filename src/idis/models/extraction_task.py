"""Models for methodology-driven extraction task planning."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from idis.methodology.models import (
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

    document_id: str
    span_id: str
    evidence_tags: list[str] = Field(default_factory=list)
    locator: dict[str, Any] = Field(default_factory=dict)
    text_excerpt: str | None = None

    @field_validator("document_id", "span_id")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
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

    answer_type: str
    question_text: str
    required_evidence: list[RequiredEvidence]
    required_calculations: list[RequiredCalculation] = Field(default_factory=list)
    validation_requirements: list[str]
    report_section: str
    report_subsection: str | None = None
    methodology_type: MethodologyType
    methodology_section: str

    @field_validator("answer_type", "question_text", "report_section", "methodology_section")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

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


class ExtractionTask(ExtractionTaskBaseModel):
    """Metadata-only extraction task produced by deterministic planning."""

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
    document_id: str | None = None
    classification_id: str | None = None
    source_spans: list[SourceSpanReference] = Field(default_factory=list)
    target_fdd_category: FddDocumentCategory | None = None
    target_cdd_category: CddDocumentCategory | None = None
    required_evidence: list[RequiredEvidence]
    expected_answer_schema: ExpectedAnswerSchema
    validation_requirements: list[str]

    @property
    def source_span_ids(self) -> list[str]:
        """Return deterministic source span IDs."""
        return sorted(span.span_id for span in self.source_spans)

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

    @field_validator("extraction_task_id", "document_id", "classification_id")
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
    def _validate_status_invariants(self) -> ExtractionTask:
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
                document_id=self.document_id,
                source_span_ids=self.source_span_ids,
                target_fdd_category=self.target_fdd_category,
                target_cdd_category=self.target_cdd_category,
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


def generate_extraction_task_id(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    methodology_question_id: str,
    document_id: str | None,
    source_span_ids: list[str],
    target_fdd_category: FddDocumentCategory | None,
    target_cdd_category: CddDocumentCategory | None,
    status: ExtractionTaskStatus,
    blocker_reason: ExtractionTaskBlockerReason | None,
) -> str:
    """Generate a stable extraction task ID from task identity fields."""
    seed = {
        "tenant_id": tenant_id,
        "deal_id": deal_id,
        "run_id": run_id,
        "methodology_question_id": methodology_question_id,
        "document_id": document_id or "no_document",
        "source_span_ids": sorted(source_span_ids),
        "target_fdd_category": target_fdd_category.value if target_fdd_category else "none",
        "target_cdd_category": target_cdd_category.value if target_cdd_category else "none",
        "status": status.value,
        "blocker_reason": blocker_reason.value if blocker_reason else "none",
    }
    encoded = json.dumps(seed, sort_keys=True, separators=(",", ":"))
    return f"et_{hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:24]}"
