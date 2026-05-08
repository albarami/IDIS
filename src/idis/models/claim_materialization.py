"""Models for methodology claim draft materialization."""

from __future__ import annotations

import json
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ClaimMaterializationStatus(StrEnum):
    """Aggregate materialization status."""

    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class ClaimMaterializationReason(StrEnum):
    """Machine-readable draft rejection reasons."""

    STALE_OR_INVALID_DRAFT_ID = "stale_or_invalid_draft_id"
    TENANT_OR_RUN_MISMATCH = "tenant_or_run_mismatch"
    SOURCE_SPAN_METADATA_MISMATCH = "source_span_metadata_mismatch"
    CLAIM_SERVICE_CREATE_FAILED = "claim_service_create_failed"
    BELOW_CONFIDENCE_THRESHOLD = "below_confidence_threshold"
    BELOW_DHABT_THRESHOLD = "below_dhabt_threshold"
    MISSING_GATE_METADATA = "missing_gate_metadata"
    MISSING_SOURCE_SPAN = "missing_source_span"
    MISSING_METHODOLOGY_LINKAGE = "missing_methodology_linkage"
    MALFORMED_CLAIM_DRAFT = "malformed_claim_draft"
    DUPLICATE_DRAFT_ID = "duplicate_draft_id"


class ClaimMaterializationBaseModel(BaseModel):
    """Base model for deterministic materialization data."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DraftClaimMapping(ClaimMaterializationBaseModel):
    """Mapping from a methodology claim draft to a persisted claim."""

    methodology_claim_draft_id: str
    claim_id: str
    extraction_task_id: str
    methodology_question_id: str
    document_id: str
    source_span_ids: list[str]

    @field_validator(
        "methodology_claim_draft_id",
        "claim_id",
        "extraction_task_id",
        "methodology_question_id",
        "document_id",
    )
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @field_validator("source_span_ids")
    @classmethod
    def _source_span_ids_not_empty(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("source_span_ids must not be empty")
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("source_span_ids must not contain blank values")
        return sorted(set(cleaned))


class ClaimMaterializationDraftRejection(ClaimMaterializationBaseModel):
    """Rejected draft with machine-readable reason."""

    methodology_claim_draft_id: str | None = None
    reason: ClaimMaterializationReason
    reason_codes: list[str]
    message: str

    @field_validator("methodology_claim_draft_id")
    @classmethod
    def _optional_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

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
    def _reason_code_contains_reason(self) -> ClaimMaterializationDraftRejection:
        if self.reason.value not in self.reason_codes:
            raise ValueError("reason_codes must include reason value")
        return self


class MethodologyClaimMaterializationSummary(ClaimMaterializationBaseModel):
    """Deterministic summary of materialization output."""

    tenant_id: str
    deal_id: str
    run_id: str
    total_drafts: int
    created_claim_count: int
    rejected_draft_count: int
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


class ClaimMaterializationResult(ClaimMaterializationBaseModel):
    """Top-level methodology claim materialization result."""

    tenant_id: str
    deal_id: str
    run_id: str
    status: ClaimMaterializationStatus
    draft_claim_mappings: list[DraftClaimMapping] = Field(default_factory=list)
    rejected_drafts: list[ClaimMaterializationDraftRejection] = Field(default_factory=list)
    summary: MethodologyClaimMaterializationSummary

    @field_validator("tenant_id", "deal_id", "run_id")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    def to_deterministic_json(self) -> str:
        """Serialize result deterministically."""
        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )


def rejection(
    *,
    methodology_claim_draft_id: str | None,
    reason: ClaimMaterializationReason,
    message: str,
) -> ClaimMaterializationDraftRejection:
    """Build a standardized draft rejection."""
    return ClaimMaterializationDraftRejection(
        methodology_claim_draft_id=methodology_claim_draft_id,
        reason=reason,
        reason_codes=[reason.value],
        message=message,
    )
