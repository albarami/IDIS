"""Slice 7 in-memory EvidenceItem and source-provenance materialization models."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from enum import StrEnum
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from idis.models.claim_materialization import MaterializedClaimSourceRef
from idis.models.evidence_item import EvidenceItem

EVIDENCE_ITEM_NAMESPACE = UUID("2f0f08df-6ad7-5db4-a45f-0cb864b0d592")


class EvidenceItemMaterializationStatus(StrEnum):
    """Aggregate Slice 7 evidence materialization status."""

    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class EvidenceItemMaterializationReason(StrEnum):
    """Machine-readable evidence materialization rejection reasons."""

    MISSING_MATERIALIZED_CLAIMS = "missing_materialized_claims"
    MALFORMED_MATERIALIZED_CLAIM = "malformed_materialized_claim"
    MISSING_CLAIM_ID = "missing_claim_id"
    MISSING_SOURCE_REFS = "missing_source_refs"
    UNSAFE_SOURCE_REF = "unsafe_source_ref"
    DUPLICATE_CLAIM_SOURCE_REF = "duplicate_claim_source_ref"
    TENANT_OR_RUN_MISMATCH = "tenant_or_run_mismatch"


class EvidenceItemMaterializationBaseModel(BaseModel):
    """Base model for deterministic Slice 7 materialization data."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RunScopedEvidenceProvenanceRef(MaterializedClaimSourceRef):
    """Run-scoped provenance ref reusing Slice 6 source-ref safety validation."""


class RunScopedEvidenceItemShell(EvidenceItemMaterializationBaseModel):
    """Safe resume shell for a run-scoped evidence item."""

    tenant_id: str
    deal_id: str
    run_id: str
    claim_id: str
    evidence_id: str
    document_id: str
    source_span_id: str
    methodology_question_id: str
    coverage_record_id: str
    extraction_task_id: str
    extraction_output_id: str
    status: str

    @field_validator(
        "tenant_id",
        "deal_id",
        "run_id",
        "claim_id",
        "evidence_id",
        "document_id",
        "source_span_id",
        "methodology_question_id",
        "coverage_record_id",
        "extraction_task_id",
        "extraction_output_id",
        "status",
    )
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @model_validator(mode="after")
    def _source_ref_safe(self) -> RunScopedEvidenceItemShell:
        RunScopedEvidenceProvenanceRef(
            document_id=self.document_id,
            source_span_id=self.source_span_id,
            locator=None,
        )
        return self


class RunScopedEvidenceItemRecord(EvidenceItemMaterializationBaseModel):
    """In-memory governed EvidenceItem boundary produced from Slice 6 claims."""

    tenant_id: str
    deal_id: str
    run_id: str
    claim_id: str
    evidence_item: EvidenceItem
    source_ref: RunScopedEvidenceProvenanceRef
    methodology_question_id: str
    coverage_record_id: str
    extraction_task_id: str
    extraction_output_id: str
    status: str

    @field_validator(
        "tenant_id",
        "deal_id",
        "run_id",
        "claim_id",
        "methodology_question_id",
        "coverage_record_id",
        "extraction_task_id",
        "extraction_output_id",
        "status",
    )
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @model_validator(mode="after")
    def _evidence_scope_matches_record(self) -> RunScopedEvidenceItemRecord:
        if self.evidence_item.tenant_id != self.tenant_id:
            raise ValueError("evidence_item tenant_id must match record tenant_id")
        if self.evidence_item.deal_id != self.deal_id:
            raise ValueError("evidence_item deal_id must match record deal_id")
        return self

    def to_shell(self) -> RunScopedEvidenceItemShell:
        """Build a safe resume shell without raw text or locator payloads."""
        return RunScopedEvidenceItemShell(
            tenant_id=self.tenant_id,
            deal_id=self.deal_id,
            run_id=self.run_id,
            claim_id=self.claim_id,
            evidence_id=self.evidence_item.evidence_id,
            document_id=self.source_ref.document_id,
            source_span_id=self.source_ref.source_span_id,
            methodology_question_id=self.methodology_question_id,
            coverage_record_id=self.coverage_record_id,
            extraction_task_id=self.extraction_task_id,
            extraction_output_id=self.extraction_output_id,
            status=self.status,
        )


class MethodologyEvidenceItemMapping(EvidenceItemMaterializationBaseModel):
    """Summary-safe mapping from claim/source ref to evidence item."""

    claim_id: str
    evidence_id: str
    methodology_question_id: str
    coverage_record_id: str
    extraction_task_id: str
    extraction_output_id: str
    document_id: str
    source_span_id: str

    @field_validator(
        "claim_id",
        "evidence_id",
        "methodology_question_id",
        "coverage_record_id",
        "extraction_task_id",
        "extraction_output_id",
        "document_id",
        "source_span_id",
    )
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @model_validator(mode="after")
    def _source_ref_safe(self) -> MethodologyEvidenceItemMapping:
        RunScopedEvidenceProvenanceRef(
            document_id=self.document_id,
            source_span_id=self.source_span_id,
            locator=None,
        )
        return self

    @classmethod
    def from_record(cls, record: RunScopedEvidenceItemRecord) -> MethodologyEvidenceItemMapping:
        """Build a summary-safe mapping from an in-memory evidence record."""
        return cls(
            claim_id=record.claim_id,
            evidence_id=record.evidence_item.evidence_id,
            methodology_question_id=record.methodology_question_id,
            coverage_record_id=record.coverage_record_id,
            extraction_task_id=record.extraction_task_id,
            extraction_output_id=record.extraction_output_id,
            document_id=record.source_ref.document_id,
            source_span_id=record.source_ref.source_span_id,
        )


class MethodologyEvidenceItemRejection(EvidenceItemMaterializationBaseModel):
    """Rejected claim/source ref with machine-readable reason."""

    claim_id: str | None = None
    extraction_output_id: str | None = None
    reason: EvidenceItemMaterializationReason
    reason_codes: list[str]
    message: str

    @field_validator("claim_id", "extraction_output_id")
    @classmethod
    def _optional_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("field must not be blank")
        return value.strip() if value is not None else None

    @field_validator("reason_codes")
    @classmethod
    def _reason_codes_not_empty(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("reason_codes must not be empty")
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("reason_codes must not contain blank values")
        return sorted(set(cleaned))

    @field_validator("message")
    @classmethod
    def _message_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must not be blank")
        return value.strip()

    @model_validator(mode="after")
    def _reason_code_contains_reason(self) -> MethodologyEvidenceItemRejection:
        if self.reason.value not in self.reason_codes:
            raise ValueError("reason_codes must include reason value")
        return self


class MethodologyEvidenceItemMaterializationSummary(EvidenceItemMaterializationBaseModel):
    """Safe aggregate summary for Slice 7 evidence materialization."""

    tenant_id: str
    deal_id: str
    run_id: str
    total_claims: int
    total_source_refs: int
    created_evidence_count: int
    rejected_source_ref_count: int
    by_status: dict[str, int]
    by_reason: dict[str, int]

    @field_validator("tenant_id", "deal_id", "run_id")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    def to_deterministic_json(self) -> str:
        """Serialize summary deterministically."""
        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )


class MethodologyEvidenceItemMaterializationRunResult(EvidenceItemMaterializationBaseModel):
    """Run-step-safe result for Slice 7 evidence materialization."""

    tenant_id: str
    deal_id: str
    run_id: str
    status: EvidenceItemMaterializationStatus
    evidence_item_mappings: list[MethodologyEvidenceItemMapping] = Field(default_factory=list)
    rejected_source_refs: list[MethodologyEvidenceItemRejection] = Field(default_factory=list)
    summary: MethodologyEvidenceItemMaterializationSummary

    @field_validator("tenant_id", "deal_id", "run_id")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    def to_run_step_summary(self, *, status: str | None = None) -> dict[str, object]:
        """Return safe run-step summary without raw text, locators, values, or answers."""
        return {
            "status": status or self.status.value,
            "evidence_ids": [mapping.evidence_id for mapping in self.evidence_item_mappings],
            "claim_ids": sorted({mapping.claim_id for mapping in self.evidence_item_mappings}),
            "evidence_item_mappings": [
                mapping.model_dump(mode="json") for mapping in self.evidence_item_mappings
            ],
            "rejected_source_refs": [
                rejection.model_dump(mode="json") for rejection in self.rejected_source_refs
            ],
            "summary": {
                "total_claims": self.summary.total_claims,
                "total_source_refs": self.summary.total_source_refs,
                "created_evidence_count": self.summary.created_evidence_count,
                "rejected_source_ref_count": self.summary.rejected_source_ref_count,
                "by_status": dict(self.summary.by_status),
                "by_reason": dict(self.summary.by_reason),
            },
        }


def evidence_item_source_span_id(source_span_id: str) -> str | None:
    """Return source_span_id only when it is already a UUID string."""
    try:
        return str(UUID(source_span_id))
    except ValueError:
        return None


def generate_methodology_evidence_item_id(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    claim_id: str,
    extraction_output_id: str,
    extraction_task_id: str,
    methodology_question_id: str,
    coverage_record_id: str,
    source_ref: MaterializedClaimSourceRef,
) -> str:
    """Generate a deterministic UUID v5 ID for a run-scoped evidence item."""
    seed = {
        "tenant_id": tenant_id,
        "deal_id": deal_id,
        "run_id": run_id,
        "claim_id": claim_id,
        "extraction_output_id": extraction_output_id,
        "extraction_task_id": extraction_task_id,
        "methodology_question_id": methodology_question_id,
        "coverage_record_id": coverage_record_id,
        "document_id": source_ref.document_id,
        "source_span_id": source_ref.source_span_id,
    }
    canonical_seed = json.dumps(seed, sort_keys=True, separators=(",", ":"))
    return str(uuid5(EVIDENCE_ITEM_NAMESPACE, canonical_seed))


def aggregate_status(
    *,
    mappings: list[MethodologyEvidenceItemMapping],
    rejections: list[MethodologyEvidenceItemRejection],
) -> EvidenceItemMaterializationStatus:
    """Return aggregate status for mappings and rejections."""
    if mappings and rejections:
        return EvidenceItemMaterializationStatus.PARTIAL
    if rejections:
        return EvidenceItemMaterializationStatus.FAILED
    return EvidenceItemMaterializationStatus.COMPLETED


def counter(items: Iterable[str]) -> dict[str, int]:
    """Return deterministic counts for summary fields."""
    return dict(sorted(Counter(items).items()))
