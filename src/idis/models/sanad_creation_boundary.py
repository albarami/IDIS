"""Models for Phase 2.8 synthetic Sanad creation boundary results."""

from __future__ import annotations

import json
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from idis.models.sanad_coverage_boundary import ICPromotionStatus


class SanadCreationStatus(StrEnum):
    """Aggregate Sanad creation boundary status."""

    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class SanadCreationReason(StrEnum):
    """Machine-readable Phase 2.8 creation decision reasons."""

    READY_FOR_SANAD = "ready_for_sanad"
    EVIDENCE_MISSING = "evidence_missing"
    SOURCE_SPAN_MISMATCH = "source_span_mismatch"
    MISSING_CLAIM_LINKAGE = "missing_claim_linkage"
    TENANT_OR_RUN_MISMATCH = "tenant_or_run_mismatch"
    TENANT_OR_SERVICE_MISMATCH = "tenant_or_service_mismatch"
    MALFORMED_EVIDENCE_REFERENCE = "malformed_evidence_reference"
    CONTRADICTED_EVIDENCE = "contradicted_evidence"
    CHAIN_BUILD_FAILED = "chain_build_failed"
    SANAD_CREATION_FAILED = "sanad_creation_failed"
    DUPLICATE_CONFLICTING_READINESS_DECISION = "duplicate_conflicting_readiness_decision"
    ALREADY_CREATED = "already_created"
    BLOCKED = "blocked"


class SanadCreationBoundaryBaseModel(BaseModel):
    """Base model for deterministic Phase 2.8 records."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SanadCreationMapping(SanadCreationBoundaryBaseModel):
    """Successful Sanad creation mapping produced by the boundary."""

    tenant_id: str
    deal_id: str
    run_id: str
    claim_id: str
    methodology_question_id: str
    source_span_ids: list[str]
    evidence_ids: list[str]
    primary_evidence_id: str
    corroborating_evidence_ids: list[str] = Field(default_factory=list)
    sanad_id: str
    transmission_chain_node_count: int = Field(..., ge=1)
    chain_node_types: list[str] = Field(default_factory=list)
    extraction_confidence: float = Field(..., ge=0.0, le=1.0)
    dhabt_score: float | None = Field(default=None, ge=0.0, le=1.0)
    coverage_status: str = "deferred"
    coverage_update_status: str = "not_applied"
    ic_promotion_status: ICPromotionStatus = ICPromotionStatus.DEFERRED_UNTIL_SANAD

    @field_validator(
        "tenant_id",
        "deal_id",
        "run_id",
        "claim_id",
        "methodology_question_id",
        "primary_evidence_id",
        "sanad_id",
        "coverage_status",
        "coverage_update_status",
    )
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @field_validator(
        "source_span_ids",
        "evidence_ids",
        "corroborating_evidence_ids",
        "chain_node_types",
    )
    @classmethod
    def _references_not_blank(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("reference values must not be blank")
        return sorted(set(cleaned))

    @model_validator(mode="after")
    def _validate_linkage(self) -> SanadCreationMapping:
        if not self.source_span_ids:
            raise ValueError("source_span_ids must not be empty")
        if not self.evidence_ids:
            raise ValueError("evidence_ids must not be empty")
        if self.primary_evidence_id not in self.evidence_ids:
            raise ValueError("primary_evidence_id must be included in evidence_ids")
        missing_corroborating = set(self.corroborating_evidence_ids) - set(self.evidence_ids)
        if missing_corroborating:
            raise ValueError("corroborating_evidence_ids must be included in evidence_ids")
        return self


class SanadCreationRejection(SanadCreationBoundaryBaseModel):
    """Fail-closed Sanad creation rejection emitted by the boundary."""

    tenant_id: str
    deal_id: str
    run_id: str
    claim_id: str | None = None
    methodology_question_id: str | None = None
    source_span_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    reason: SanadCreationReason
    reason_codes: list[str]
    message: str | None = None
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    ic_promotion_status: ICPromotionStatus = ICPromotionStatus.DEFERRED_UNTIL_SANAD

    @field_validator("tenant_id", "deal_id", "run_id")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @field_validator("claim_id", "methodology_question_id", "message")
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
    def _validate_reason_codes(self) -> SanadCreationRejection:
        if self.reason.value not in self.reason_codes:
            raise ValueError("reason_codes must include reason value")
        return self


class ClaimSanadLinkDecision(SanadCreationBoundaryBaseModel):
    """Metadata-only claim-to-Sanad link decision for a later explicit apply phase."""

    tenant_id: str
    deal_id: str
    run_id: str
    claim_id: str
    methodology_question_id: str
    sanad_id: str
    claim_link_status: str = "deferred"
    coverage_status: str = "deferred"
    coverage_update_status: str = "not_applied"
    ic_promotion_status: ICPromotionStatus = ICPromotionStatus.DEFERRED_UNTIL_SANAD

    @field_validator(
        "tenant_id",
        "deal_id",
        "run_id",
        "claim_id",
        "methodology_question_id",
        "sanad_id",
        "claim_link_status",
        "coverage_status",
        "coverage_update_status",
    )
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()


class SanadCreationSummary(SanadCreationBoundaryBaseModel):
    """Deterministic summary for Phase 2.8 boundary outputs."""

    tenant_id: str
    deal_id: str
    run_id: str
    total_readiness_decisions: int
    selected_decision_count: int
    created_sanad_count: int
    rejected_decision_count: int
    already_created_count: int
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


class SanadCreationResult(SanadCreationBoundaryBaseModel):
    """Top-level Phase 2.8 Sanad creation boundary result."""

    tenant_id: str
    deal_id: str
    run_id: str
    status: SanadCreationStatus
    mappings: list[SanadCreationMapping] = Field(default_factory=list)
    rejections: list[SanadCreationRejection] = Field(default_factory=list)
    claim_link_decisions: list[ClaimSanadLinkDecision] = Field(default_factory=list)
    summary: SanadCreationSummary

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
