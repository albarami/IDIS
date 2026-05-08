"""Models for Phase 2.9 synthetic Claim-Sanad link boundary results."""

from __future__ import annotations

import json
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ClaimSanadLinkStatus(StrEnum):
    """Aggregate Claim-Sanad link boundary status."""

    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class ClaimSanadLinkReason(StrEnum):
    """Machine-readable Phase 2.9 link decision reasons."""

    READY_FOR_CLAIM_LINK = "ready_for_claim_link"
    MISSING_CLAIM_ID = "missing_claim_id"
    MISSING_SANAD_ID = "missing_sanad_id"
    TENANT_OR_RUN_MISMATCH = "tenant_or_run_mismatch"
    TENANT_OR_SERVICE_MISMATCH = "tenant_or_service_mismatch"
    CLAIM_NOT_FOUND = "claim_not_found"
    SANAD_NOT_FOUND = "sanad_not_found"
    CLAIM_SANAD_SCOPE_MISMATCH = "claim_sanad_scope_mismatch"
    STALE_MAPPING = "stale_mapping"
    EXISTING_CONFLICTING_SANAD = "existing_conflicting_sanad"
    ALREADY_LINKED = "already_linked"
    SERVICE_UPDATE_FAILED = "service_update_failed"
    PROTECTED_FIELD_DRIFT = "protected_field_drift"
    BOUNDARY_VIOLATION = "boundary_violation"
    BLOCKED = "blocked"


class ClaimPromotionStatus(StrEnum):
    """Explicit non-promotion state for Sanad-linked claims."""

    SANAD_LINKED_NOT_IC_READY = "sanad_linked_not_ic_ready"
    DEFERRED_UNTIL_EXPLICIT_IC_PROMOTION = "deferred_until_explicit_ic_promotion"


class ClaimSanadLinkBoundaryBaseModel(BaseModel):
    """Base model for deterministic Phase 2.9 boundary records."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ClaimSanadLinkApplyDecision(ClaimSanadLinkBoundaryBaseModel):
    """Synthetic-only decision to apply a created Sanad to a materialized claim later."""

    tenant_id: str
    deal_id: str
    run_id: str
    claim_id: str
    methodology_question_id: str
    sanad_id: str
    source_span_ids: list[str]
    evidence_ids: list[str]
    claim_link_status: str = ClaimSanadLinkReason.READY_FOR_CLAIM_LINK.value
    coverage_update_status: str = "not_applied"
    claim_promotion_status: ClaimPromotionStatus = (
        ClaimPromotionStatus.SANAD_LINKED_NOT_IC_READY
    )

    @field_validator(
        "tenant_id",
        "deal_id",
        "run_id",
        "claim_id",
        "methodology_question_id",
        "sanad_id",
        "claim_link_status",
        "coverage_update_status",
    )
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @field_validator("source_span_ids", "evidence_ids")
    @classmethod
    def _references_not_blank(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("reference values must not be blank")
        return sorted(set(cleaned))

    @model_validator(mode="after")
    def _validate_references(self) -> ClaimSanadLinkApplyDecision:
        if not self.source_span_ids:
            raise ValueError("source_span_ids must not be empty")
        if not self.evidence_ids:
            raise ValueError("evidence_ids must not be empty")
        return self


class ClaimSanadLinkApplicationMapping(ClaimSanadLinkBoundaryBaseModel):
    """Successful ClaimService-backed claim-to-Sanad link application."""

    tenant_id: str
    deal_id: str
    run_id: str
    claim_id: str
    methodology_question_id: str
    sanad_id: str
    source_span_ids: list[str]
    evidence_ids: list[str]
    claim_grade: str
    claim_verdict: str
    claim_action: str
    ic_bound: bool = False
    coverage_update_status: str = "not_applied"
    claim_promotion_status: ClaimPromotionStatus = (
        ClaimPromotionStatus.SANAD_LINKED_NOT_IC_READY
    )

    @field_validator(
        "tenant_id",
        "deal_id",
        "run_id",
        "claim_id",
        "methodology_question_id",
        "sanad_id",
        "claim_grade",
        "claim_verdict",
        "claim_action",
        "coverage_update_status",
    )
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @field_validator("source_span_ids", "evidence_ids")
    @classmethod
    def _references_not_blank(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("reference values must not be blank")
        return sorted(set(cleaned))

    @model_validator(mode="after")
    def _validate_non_promotion(self) -> ClaimSanadLinkApplicationMapping:
        if self.ic_bound:
            raise ValueError("ic_bound must remain false")
        if self.claim_verdict == "VERIFIED":
            raise ValueError("claim_verdict must not be VERIFIED")
        if self.claim_action == "NONE":
            raise ValueError("claim_action must not be NONE")
        if not self.source_span_ids:
            raise ValueError("source_span_ids must not be empty")
        if not self.evidence_ids:
            raise ValueError("evidence_ids must not be empty")
        return self


class ClaimSanadLinkRejection(ClaimSanadLinkBoundaryBaseModel):
    """Fail-closed Claim-Sanad link rejection emitted by the boundary."""

    tenant_id: str
    deal_id: str
    run_id: str
    claim_id: str | None = None
    methodology_question_id: str | None = None
    sanad_id: str | None = None
    source_span_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    reason: ClaimSanadLinkReason
    reason_codes: list[str]
    message: str | None = None
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    coverage_update_status: str = "not_applied"
    claim_promotion_status: ClaimPromotionStatus = (
        ClaimPromotionStatus.SANAD_LINKED_NOT_IC_READY
    )

    @field_validator("tenant_id", "deal_id", "run_id", "coverage_update_status")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @field_validator("claim_id", "methodology_question_id", "sanad_id", "message")
    @classmethod
    def _optional_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @field_validator("source_span_ids", "evidence_ids", "reason_codes")
    @classmethod
    def _references_not_blank(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("reference values must not be blank")
        return sorted(set(cleaned))

    @model_validator(mode="after")
    def _validate_reason_codes(self) -> ClaimSanadLinkRejection:
        if self.reason.value not in self.reason_codes:
            raise ValueError("reason_codes must include reason value")
        return self


class ClaimSanadLinkSummary(ClaimSanadLinkBoundaryBaseModel):
    """Deterministic summary for Phase 2.9 boundary outputs."""

    tenant_id: str
    deal_id: str
    run_id: str
    total_creation_mappings: int
    decision_count: int
    applied_link_count: int
    rejected_decision_count: int
    already_linked_count: int
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


class ClaimSanadLinkApplicationResult(ClaimSanadLinkBoundaryBaseModel):
    """Top-level Phase 2.9 Claim-Sanad link boundary result."""

    tenant_id: str
    deal_id: str
    run_id: str
    status: ClaimSanadLinkStatus
    decisions: list[ClaimSanadLinkApplyDecision] = Field(default_factory=list)
    mappings: list[ClaimSanadLinkApplicationMapping] = Field(default_factory=list)
    rejections: list[ClaimSanadLinkRejection] = Field(default_factory=list)
    summary: ClaimSanadLinkSummary

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
