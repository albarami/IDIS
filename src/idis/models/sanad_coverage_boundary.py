"""Models for Phase 2.7 Sanad readiness and coverage decision boundaries."""

from __future__ import annotations

import json
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from idis.models.methodology_coverage import (
    MethodologyAnswer,
    MethodologyCoverageStatus,
    MethodologyEvidenceLink,
)


class ICPromotionStatus(StrEnum):
    """IC promotion state emitted by the boundary."""

    DEFERRED_UNTIL_SANAD = "deferred_until_sanad"


class SanadCoverageBoundaryStatus(StrEnum):
    """Aggregate boundary status."""

    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class SanadCoverageBoundaryReason(StrEnum):
    """Machine-readable boundary decision reasons."""

    READY_FOR_FUTURE_SANAD = "ready_for_future_sanad"
    EVIDENCE_MISSING = "evidence_missing"
    TENANT_OR_RUN_MISMATCH = "tenant_or_run_mismatch"
    SOURCE_SPAN_MISMATCH = "source_span_mismatch"
    MISSING_METHODOLOGY_LINKAGE = "missing_methodology_linkage"
    DUPLICATE_CONFLICTING_MAPPING = "duplicate_conflicting_mapping"
    CONTRADICTED = "contradicted"
    BLOCKED = "blocked"


class SanadCoverageBoundaryBaseModel(BaseModel):
    """Base model for deterministic boundary records."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class MethodologyClaimEvidenceReference(SanadCoverageBoundaryBaseModel):
    """Synthetic evidence reference for a materialized methodology claim."""

    tenant_id: str
    deal_id: str
    run_id: str
    methodology_question_id: str
    evidence_id: str
    source_span_id: str
    claim_id: str | None = None
    calc_ids: list[str] = Field(default_factory=list)
    target_status: MethodologyCoverageStatus = MethodologyCoverageStatus.EXTRACTED
    answer_text: str | None = None
    conflict_ids: list[str] = Field(default_factory=list)
    defect_ids: list[str] = Field(default_factory=list)
    sanad_id: str | None = None
    sanad_status: str = "deferred"

    @field_validator(
        "tenant_id",
        "deal_id",
        "run_id",
        "methodology_question_id",
        "evidence_id",
        "source_span_id",
    )
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @field_validator("claim_id", "answer_text", "sanad_id", "sanad_status")
    @classmethod
    def _optional_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @field_validator("calc_ids", "conflict_ids", "defect_ids")
    @classmethod
    def _references_not_blank(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("reference values must not be blank")
        return sorted(set(cleaned))

    @model_validator(mode="after")
    def _validate_status_requirements(self) -> MethodologyClaimEvidenceReference:
        if self.target_status == MethodologyCoverageStatus.CONTRADICTED and not (
            self.conflict_ids or self.defect_ids
        ):
            raise ValueError("contradicted evidence requires conflict or defect reference")
        return self


class SanadReadinessDecision(SanadCoverageBoundaryBaseModel):
    """Decision describing whether a claim can later receive a Sanad chain."""

    tenant_id: str
    deal_id: str
    run_id: str
    methodology_question_id: str
    claim_id: str | None = None
    source_span_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    calc_ids: list[str] = Field(default_factory=list)
    sanad_id: str | None = None
    sanad_status: str = "deferred"
    ready_for_future_sanad: bool
    reason: SanadCoverageBoundaryReason
    reason_codes: list[str]
    message: str | None = None
    ic_promotion_status: ICPromotionStatus = ICPromotionStatus.DEFERRED_UNTIL_SANAD

    @field_validator("tenant_id", "deal_id", "run_id", "methodology_question_id")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @field_validator("claim_id", "sanad_id", "sanad_status", "message")
    @classmethod
    def _optional_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @field_validator("source_span_ids", "evidence_ids", "calc_ids", "reason_codes")
    @classmethod
    def _references_not_blank(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("reference values must not be blank")
        return sorted(set(cleaned))

    @model_validator(mode="after")
    def _validate_reason_codes(self) -> SanadReadinessDecision:
        if self.reason.value not in self.reason_codes:
            raise ValueError("reason_codes must include reason value")
        return self


class CoverageUpdateDecision(SanadCoverageBoundaryBaseModel):
    """Decision describing a future methodology coverage status update."""

    tenant_id: str
    deal_id: str
    run_id: str
    methodology_question_id: str
    target_status: MethodologyCoverageStatus
    claim_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    source_span_ids: list[str] = Field(default_factory=list)
    calc_ids: list[str] = Field(default_factory=list)
    evidence_links: list[MethodologyEvidenceLink] = Field(default_factory=list)
    answer: MethodologyAnswer | None = None
    conflict_ids: list[str] = Field(default_factory=list)
    defect_ids: list[str] = Field(default_factory=list)
    sanad_id: str | None = None
    sanad_status: str | None = "deferred"
    reason: SanadCoverageBoundaryReason
    reason_codes: list[str]
    message: str | None = None
    ic_promotion_status: ICPromotionStatus = ICPromotionStatus.DEFERRED_UNTIL_SANAD

    @field_validator("tenant_id", "deal_id", "run_id", "methodology_question_id")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @field_validator("sanad_id", "sanad_status", "message")
    @classmethod
    def _optional_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @field_validator(
        "claim_ids",
        "evidence_ids",
        "source_span_ids",
        "calc_ids",
        "conflict_ids",
        "defect_ids",
        "reason_codes",
    )
    @classmethod
    def _references_not_blank(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("reference values must not be blank")
        return sorted(set(cleaned))

    @model_validator(mode="after")
    def _validate_status_requirements(self) -> CoverageUpdateDecision:
        if self.reason.value not in self.reason_codes:
            raise ValueError("reason_codes must include reason value")
        if self.target_status == MethodologyCoverageStatus.ANSWERED and not (
            self.sanad_id or self.sanad_status == "deferred"
        ):
            raise ValueError("answered coverage requires sanad_id or deferred sanad_status")
        if self.target_status == MethodologyCoverageStatus.CONTRADICTED and not (
            self.conflict_ids or self.defect_ids
        ):
            raise ValueError("contradicted requires conflict or defect reference")
        return self


class SanadCoverageBoundarySummary(SanadCoverageBoundaryBaseModel):
    """Deterministic summary of boundary decisions."""

    tenant_id: str
    deal_id: str
    run_id: str
    total_claim_mappings: int
    ready_for_future_sanad_count: int
    coverage_decision_count: int
    blocked_decision_count: int
    by_status: dict[str, int]
    by_reason: dict[str, int]
    by_coverage_status: dict[str, int]

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


class SanadCoverageBoundaryResult(SanadCoverageBoundaryBaseModel):
    """Top-level Phase 2.7 boundary result."""

    tenant_id: str
    deal_id: str
    run_id: str
    status: SanadCoverageBoundaryStatus
    readiness_decisions: list[SanadReadinessDecision] = Field(default_factory=list)
    coverage_decisions: list[CoverageUpdateDecision] = Field(default_factory=list)
    summary: SanadCoverageBoundarySummary

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
