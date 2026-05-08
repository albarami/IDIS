"""Run-scoped document preflight models."""

from __future__ import annotations

import json
from collections import Counter
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from idis.models.document_classification import (
    CddDocumentCategory,
    DocumentSupportStatus,
    DocumentTriageStatus,
    FddDocumentCategory,
)


class DocumentPreflightStatus(StrEnum):
    """Aggregate preflight status for a run corpus."""

    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class DocumentPreflightReason(StrEnum):
    """Stable reason code for one preflight decision."""

    READY = "READY"
    PARTIAL_SUPPORT = "PARTIAL_SUPPORT"
    UNSUPPORTED_SOURCE = "UNSUPPORTED_SOURCE"
    CONVERSION_REQUIRED = "CONVERSION_REQUIRED"
    OCR_REQUIRED = "OCR_REQUIRED"
    TOO_LARGE = "TOO_LARGE"
    ENCRYPTED_SOURCE = "ENCRYPTED_SOURCE"
    CORRUPTED_SOURCE = "CORRUPTED_SOURCE"
    UNKNOWN_PARSER_STATUS = "UNKNOWN_PARSER_STATUS"
    NO_USABLE_DOCUMENTS = "NO_USABLE_DOCUMENTS"
    SCOPE_MISMATCH = "SCOPE_MISMATCH"
    NO_INGESTED_DOCUMENTS = "NO_INGESTED_DOCUMENTS"
    MISSING_SPANS = "MISSING_SPANS"


class DocumentPreflightBaseModel(BaseModel):
    """Base model for deterministic document preflight payloads."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DocumentPreflightSpanReference(DocumentPreflightBaseModel):
    """Safe span reference for persisted run-step summaries."""

    span_id: str
    document_id: str
    locator: dict[str, Any] = Field(default_factory=dict)
    span_type: str
    content_hash: str | None = None

    @field_validator("span_id", "document_id", "span_type")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()


class RunDocumentPreflightDecision(DocumentPreflightBaseModel):
    """Preflight decision for one document in one run."""

    tenant_id: str
    deal_id: str
    run_id: str
    document_id: str
    classification_id: str
    support_status: DocumentSupportStatus
    triage_status: DocumentTriageStatus
    usable_for_methodology_extraction: bool
    reason: DocumentPreflightReason
    reason_codes: list[str]
    warning_codes: list[str] = Field(default_factory=list)
    source_spans: list[DocumentPreflightSpanReference] = Field(default_factory=list)
    fdd_category: FddDocumentCategory | None = None
    cdd_category: CddDocumentCategory | None = None
    methodology_target_areas: list[str] = Field(default_factory=list)

    @field_validator("tenant_id", "deal_id", "run_id", "document_id", "classification_id")
    @classmethod
    def _ids_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("identifier must not be blank")
        return value.strip()

    @field_validator("reason_codes", "warning_codes", "methodology_target_areas")
    @classmethod
    def _string_lists_not_blank(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("list items must not be blank")
        return cleaned


class DocumentPreflightSummary(DocumentPreflightBaseModel):
    """Aggregate preflight counts for a run."""

    tenant_id: str
    deal_id: str
    run_id: str
    total_documents: int
    usable_documents: int
    blocked_documents: int
    by_support_status: dict[str, int]
    by_triage_status: dict[str, int]
    by_reason: dict[str, int]


class DocumentPreflightResult(DocumentPreflightBaseModel):
    """Run-scoped document preflight result."""

    tenant_id: str
    deal_id: str
    run_id: str
    status: DocumentPreflightStatus
    decisions: list[RunDocumentPreflightDecision]
    summary: DocumentPreflightSummary | None = None

    @model_validator(mode="after")
    def _ensure_summary(self) -> DocumentPreflightResult:
        if self.summary is None:
            self.summary = DocumentPreflightSummary(
                tenant_id=self.tenant_id,
                deal_id=self.deal_id,
                run_id=self.run_id,
                total_documents=len(self.decisions),
                usable_documents=sum(
                    1 for decision in self.decisions if decision.usable_for_methodology_extraction
                ),
                blocked_documents=sum(
                    1
                    for decision in self.decisions
                    if not decision.usable_for_methodology_extraction
                ),
                by_support_status=_counter(
                    decision.support_status.value for decision in self.decisions
                ),
                by_triage_status=_counter(
                    decision.triage_status.value for decision in self.decisions
                ),
                by_reason=_counter(decision.reason.value for decision in self.decisions),
            )
        return self

    @property
    def eligible_document_ids(self) -> list[str]:
        """Document IDs eligible for downstream extraction."""
        return [
            decision.document_id
            for decision in self.decisions
            if decision.usable_for_methodology_extraction
        ]

    @property
    def blocked_document_ids(self) -> list[str]:
        """Document IDs blocked from downstream extraction."""
        return [
            decision.document_id
            for decision in self.decisions
            if not decision.usable_for_methodology_extraction
        ]

    def to_deterministic_json(self) -> str:
        """Serialize deterministically for tests and audit stability."""
        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )

    def to_run_step_summary(self) -> dict[str, Any]:
        """Return a safe run-step summary with no raw span text."""
        return {
            "status": self.status.value,
            "summary": self.summary.model_dump(mode="json") if self.summary else {},
            "eligible_document_ids": self.eligible_document_ids,
            "blocked_document_ids": self.blocked_document_ids,
            "classifications": [
                {
                    "classification_id": decision.classification_id,
                    "document_id": decision.document_id,
                    "support_status": decision.support_status.value,
                    "triage_status": decision.triage_status.value,
                    "reason": decision.reason.value,
                    "reason_codes": decision.reason_codes,
                    "warning_codes": decision.warning_codes,
                    "fdd_category": (
                        decision.fdd_category.value if decision.fdd_category else None
                    ),
                    "cdd_category": (
                        decision.cdd_category.value if decision.cdd_category else None
                    ),
                    "methodology_target_areas": decision.methodology_target_areas,
                    "usable_for_methodology_extraction": (
                        decision.usable_for_methodology_extraction
                    ),
                }
                for decision in self.decisions
            ],
            "source_spans_by_document_id": {
                decision.document_id: [
                    span.model_dump(mode="json") for span in decision.source_spans
                ]
                for decision in self.decisions
            },
        }


def _counter(items: list[str] | Any) -> dict[str, int]:
    return dict(sorted(Counter(items).items()))
