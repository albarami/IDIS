"""Models for synthetic methodology extraction task execution."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Iterable
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class MethodologyTaskExecutionStatus(StrEnum):
    """Execution status for one methodology extraction task."""

    COMPLETED = "completed"
    PARTIAL = "partial"
    SKIPPED = "skipped"
    FAILED = "failed"


class MethodologyExtractionExecutionStatus(StrEnum):
    """Aggregate status for methodology extraction execution."""

    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class MethodologyExtractionExecutionReason(StrEnum):
    """Machine-readable execution skip/failure reasons."""

    BLOCKED_TASK = "blocked_task"
    EVIDENCE_MISSING_TASK = "evidence_missing_task"
    NOT_APPLICABLE_TASK = "not_applicable_task"
    EXTRACTOR_UNAVAILABLE = "extractor_unavailable"
    NO_SOURCE_SPANS = "no_source_spans"
    SOURCE_SPAN_UNAVAILABLE = "source_span_unavailable"
    MALFORMED_EXTRACTOR_OUTPUT = "malformed_extractor_output"
    MISSING_METHODOLOGY_LINKAGE = "missing_methodology_linkage"
    HALLUCINATED_SOURCE_REFERENCE = "hallucinated_source_reference"
    SCHEMA_VALIDATION_FAILED = "schema_validation_failed"
    BELOW_CONFIDENCE_THRESHOLD = "below_confidence_threshold"
    BELOW_DHABT_THRESHOLD = "below_dhabt_threshold"
    MISSING_GATE_METADATA = "missing_gate_metadata"
    EXTRACTOR_EXCEPTION = "extractor_exception"


class ExtractionExecutionBaseModel(BaseModel):
    """Base model for execution data."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class MethodologyClaimDraft(ExtractionExecutionBaseModel):
    """Methodology-linked claim draft metadata produced by synthetic execution."""

    methodology_claim_draft_id: str | None = None
    tenant_id: str
    deal_id: str
    run_id: str
    extraction_task_id: str
    methodology_id: str
    methodology_version_id: str
    methodology_question_id: str
    document_id: str
    source_span_ids: list[str]
    claim_text: str
    claim_class: str
    predicate: str
    value: dict[str, Any]
    extraction_confidence: Decimal
    dhabt_score: Decimal
    future_claim_input_preview: dict[str, Any]

    @field_validator(
        "tenant_id",
        "deal_id",
        "run_id",
        "extraction_task_id",
        "methodology_id",
        "methodology_version_id",
        "methodology_question_id",
        "document_id",
        "claim_text",
        "claim_class",
        "predicate",
    )
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @field_validator("methodology_claim_draft_id")
    @classmethod
    def _draft_id_format(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith("mcd_"):
            raise ValueError("methodology_claim_draft_id must start with mcd_")
        return value

    @field_validator("source_span_ids")
    @classmethod
    def _source_span_ids_not_empty(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("source_span_ids must not be empty")
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("source_span_ids must not contain blank values")
        return sorted(set(cleaned))

    @field_validator("value", "future_claim_input_preview")
    @classmethod
    def _dict_not_empty(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not value:
            raise ValueError("dict field must not be empty")
        return value

    @model_validator(mode="after")
    def _set_deterministic_draft_id(self) -> MethodologyClaimDraft:
        if not self.methodology_claim_draft_id:
            self.methodology_claim_draft_id = generate_methodology_claim_draft_id(
                tenant_id=self.tenant_id,
                deal_id=self.deal_id,
                run_id=self.run_id,
                extraction_task_id=self.extraction_task_id,
                methodology_question_id=self.methodology_question_id,
                document_id=self.document_id,
                source_span_ids=self.source_span_ids,
                predicate=self.predicate,
                claim_text=self.claim_text,
                value=self.value,
            )
        return self

    def to_deterministic_json(self) -> str:
        """Serialize deterministically for audit/testing."""
        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )


class MethodologyExtractionOutput(ExtractionExecutionBaseModel):
    """Neutral structured output produced by executing one methodology task."""

    methodology_extraction_output_id: str | None = None
    tenant_id: str
    deal_id: str
    run_id: str
    extraction_task_id: str
    methodology_id: str
    methodology_version_id: str
    methodology_question_id: str
    coverage_record_id: str | None = None
    document_id: str
    source_span_ids: list[str]
    answer_type: str
    answer: dict[str, Any]
    extraction_confidence: Decimal
    dhabt_score: Decimal

    @field_validator(
        "tenant_id",
        "deal_id",
        "run_id",
        "extraction_task_id",
        "methodology_id",
        "methodology_version_id",
        "methodology_question_id",
        "document_id",
        "answer_type",
    )
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @field_validator("methodology_extraction_output_id")
    @classmethod
    def _output_id_format(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith("meo_"):
            raise ValueError("methodology_extraction_output_id must start with meo_")
        return value

    @field_validator("coverage_record_id")
    @classmethod
    def _optional_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("field must not be blank when provided")
        return value.strip() if value is not None else None

    @field_validator("source_span_ids")
    @classmethod
    def _source_span_ids_not_empty(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("source_span_ids must not be empty")
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("source_span_ids must not contain blank values")
        return sorted(set(cleaned))

    @field_validator("answer")
    @classmethod
    def _answer_not_empty(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not value:
            raise ValueError("answer must not be empty")
        return value

    @model_validator(mode="after")
    def _set_deterministic_output_id(self) -> MethodologyExtractionOutput:
        if not self.methodology_extraction_output_id:
            self.methodology_extraction_output_id = generate_methodology_extraction_output_id(
                tenant_id=self.tenant_id,
                deal_id=self.deal_id,
                run_id=self.run_id,
                extraction_task_id=self.extraction_task_id,
                methodology_question_id=self.methodology_question_id,
                coverage_record_id=self.coverage_record_id,
                document_id=self.document_id,
                source_span_ids=self.source_span_ids,
                answer_type=self.answer_type,
                answer=self.answer,
            )
        return self

    def to_deterministic_json(self) -> str:
        """Serialize deterministically for audit/testing."""
        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )


class MethodologyTaskExecutionResult(ExtractionExecutionBaseModel):
    """Execution result for one extraction task."""

    tenant_id: str
    deal_id: str
    run_id: str
    extraction_task_id: str
    methodology_question_id: str | None = None
    coverage_record_id: str | None = None
    status: MethodologyTaskExecutionStatus
    accepted_outputs: list[MethodologyExtractionOutput] = Field(default_factory=list)
    rejected_outputs: list[dict[str, Any]] = Field(default_factory=list)
    accepted_drafts: list[MethodologyClaimDraft] = Field(default_factory=list)
    rejected_drafts: list[dict[str, Any]] = Field(default_factory=list)
    reason: MethodologyExtractionExecutionReason | None = None
    reason_codes: list[str]
    source_span_ids: list[str] = Field(default_factory=list)

    @field_validator("tenant_id", "deal_id", "run_id", "extraction_task_id")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @field_validator("reason_codes", "source_span_ids")
    @classmethod
    def _string_list_items_not_blank(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("list items must not be blank")
        return cleaned

    @model_validator(mode="after")
    def _reason_required_for_non_completed(self) -> MethodologyTaskExecutionResult:
        if (
            self.status
            in {
                MethodologyTaskExecutionStatus.FAILED,
                MethodologyTaskExecutionStatus.SKIPPED,
            }
            and self.reason is None
        ):
            raise ValueError("failed/skipped task results require reason")
        return self

    def to_deterministic_json(self) -> str:
        """Serialize deterministically for audit/testing."""
        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )


class MethodologyExtractionExecutionSummary(ExtractionExecutionBaseModel):
    """Deterministic aggregate execution summary."""

    tenant_id: str
    deal_id: str
    run_id: str
    total_tasks: int
    executed_tasks: int
    skipped_tasks: int
    failed_tasks: int
    accepted_output_count: int = 0
    rejected_output_count: int = 0
    accepted_draft_count: int
    rejected_draft_count: int
    by_status: dict[str, int]
    by_reason: dict[str, int]

    def to_deterministic_json(self) -> str:
        """Serialize deterministically for audit/testing."""
        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )


class MethodologyExtractionExecutionResult(ExtractionExecutionBaseModel):
    """Top-level synthetic methodology extraction execution result."""

    tenant_id: str
    deal_id: str
    run_id: str
    status: MethodologyExtractionExecutionStatus
    task_results: list[MethodologyTaskExecutionResult]
    accepted_outputs: list[MethodologyExtractionOutput] = Field(default_factory=list)
    accepted_claim_drafts: list[MethodologyClaimDraft] = Field(
        default_factory=list,
        exclude=True,
    )
    summary: MethodologyExtractionExecutionSummary


class MethodologyTaskExecutionRunStepSummaryRecord(ExtractionExecutionBaseModel):
    """Run-step-safe task execution summary record."""

    extraction_task_id: str
    methodology_question_id: str | None = None
    coverage_record_id: str | None = None
    status: MethodologyTaskExecutionStatus
    reason: MethodologyExtractionExecutionReason | None = None
    reason_codes: list[str]
    source_span_ids: list[str]
    accepted_output_count: int
    rejected_output_count: int
    confidence: str | None = None
    dhabt_score: str | None = None

    @classmethod
    def from_task_result(
        cls,
        result: MethodologyTaskExecutionResult,
    ) -> MethodologyTaskExecutionRunStepSummaryRecord:
        """Build a safe summary record without extracted answer content."""
        confidence, dhabt_score = _confidence_metadata(result.accepted_outputs)
        return cls(
            extraction_task_id=result.extraction_task_id,
            methodology_question_id=result.methodology_question_id,
            coverage_record_id=result.coverage_record_id,
            status=result.status,
            reason=result.reason,
            reason_codes=list(result.reason_codes),
            source_span_ids=list(result.source_span_ids),
            accepted_output_count=len(result.accepted_outputs),
            rejected_output_count=len(result.rejected_outputs),
            confidence=confidence,
            dhabt_score=dhabt_score,
        )


class MethodologyExtractionExecutionRunResult(ExtractionExecutionBaseModel):
    """Run-step-safe wrapper around methodology task execution results."""

    tenant_id: str
    deal_id: str
    run_id: str
    task_results: list[MethodologyTaskExecutionRunStepSummaryRecord]
    summary: MethodologyExtractionExecutionSummary

    @classmethod
    def from_task_results(
        cls,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        task_results: list[MethodologyTaskExecutionResult],
    ) -> MethodologyExtractionExecutionRunResult:
        """Build a run-step-safe execution result from full in-memory results."""
        return cls(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            task_results=[
                MethodologyTaskExecutionRunStepSummaryRecord.from_task_result(result)
                for result in task_results
            ],
            summary=_build_execution_summary(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                task_results=task_results,
            ),
        )

    def to_run_step_summary(self, *, status: str | None = None) -> dict[str, object]:
        """Return safe execution summary without extracted answers or raw span text."""
        return {
            "status": status or _summary_status(self.summary),
            "task_results": [result.model_dump(mode="json") for result in self.task_results],
            "summary": {
                "total_tasks": self.summary.total_tasks,
                "executed_tasks": self.summary.executed_tasks,
                "skipped_tasks": self.summary.skipped_tasks,
                "failed_tasks": self.summary.failed_tasks,
                "accepted_output_count": self.summary.accepted_output_count,
                "rejected_output_count": self.summary.rejected_output_count,
                "by_status": dict(self.summary.by_status),
                "by_reason": dict(self.summary.by_reason),
            },
        }


def generate_methodology_extraction_output_id(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    extraction_task_id: str,
    methodology_question_id: str,
    coverage_record_id: str | None,
    document_id: str,
    source_span_ids: list[str],
    answer_type: str,
    answer: dict[str, Any],
) -> str:
    """Generate a deterministic methodology extraction output ID."""
    seed = {
        "tenant_id": tenant_id,
        "deal_id": deal_id,
        "run_id": run_id,
        "extraction_task_id": extraction_task_id,
        "methodology_question_id": methodology_question_id,
        "coverage_record_id": coverage_record_id or "no_coverage_record",
        "document_id": document_id,
        "source_span_ids": sorted(source_span_ids),
        "answer_type": answer_type,
        "canonical_answer": json.dumps(answer, sort_keys=True, separators=(",", ":")),
    }
    encoded = json.dumps(seed, sort_keys=True, separators=(",", ":"))
    return f"meo_{hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:24]}"


def generate_methodology_claim_draft_id(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    extraction_task_id: str,
    methodology_question_id: str,
    document_id: str,
    source_span_ids: list[str],
    predicate: str,
    claim_text: str,
    value: dict[str, Any],
) -> str:
    """Generate a deterministic methodology claim draft ID."""
    seed = {
        "tenant_id": tenant_id,
        "deal_id": deal_id,
        "run_id": run_id,
        "extraction_task_id": extraction_task_id,
        "methodology_question_id": methodology_question_id,
        "document_id": document_id,
        "source_span_ids": sorted(source_span_ids),
        "predicate": predicate,
        "claim_text": claim_text,
        "canonical_value": json.dumps(value, sort_keys=True, separators=(",", ":")),
    }
    encoded = json.dumps(seed, sort_keys=True, separators=(",", ":"))
    return f"mcd_{hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:24]}"


def _build_execution_summary(
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
        accepted_output_count=sum(len(result.accepted_outputs) for result in task_results),
        rejected_output_count=sum(len(result.rejected_outputs) for result in task_results),
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


def _confidence_metadata(
    outputs: list[MethodologyExtractionOutput],
) -> tuple[str | None, str | None]:
    if not outputs:
        return None, None
    confidence = min(output.extraction_confidence for output in outputs)
    dhabt_score = min(output.dhabt_score for output in outputs)
    return str(confidence), str(dhabt_score)


def _summary_status(summary: MethodologyExtractionExecutionSummary) -> str:
    if summary.total_tasks == 0:
        return "FAILED"
    if summary.failed_tasks == summary.total_tasks:
        return "FAILED"
    if summary.failed_tasks > 0:
        return "PARTIAL"
    return "COMPLETED"


def _counter(items: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(items).items()))
