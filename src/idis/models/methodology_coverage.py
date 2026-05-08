"""Methodology coverage ledger models."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from idis.methodology.models import MethodologyType


class MethodologyCoverageStatus(StrEnum):
    """Coverage status for a methodology question in a deal/run."""

    NOT_STARTED = "not_started"
    EVIDENCE_MISSING = "evidence_missing"
    UNSUPPORTED_SOURCE = "unsupported_source"
    EXTRACTED = "extracted"
    PARTIALLY_ANSWERED = "partially_answered"
    ANSWERED = "answered"
    CONTRADICTED = "contradicted"
    NOT_APPLICABLE = "not_applicable"
    BLOCKED = "blocked"


class CoverageBaseModel(BaseModel):
    """Base model for deterministic coverage serialization."""

    model_config = ConfigDict(extra="forbid")


class MethodologyEvidenceLink(CoverageBaseModel):
    """Link from a methodology question to source evidence/claim."""

    evidence_id: str | None = None
    claim_id: str | None = None
    calc_id: str | None = None

    @field_validator("evidence_id", "claim_id", "calc_id")
    @classmethod
    def _optional_reference_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("reference value must not be blank")
        return cleaned

    @model_validator(mode="after")
    def _has_reference(self) -> MethodologyEvidenceLink:
        if not (self.evidence_id or self.claim_id or self.calc_id):
            raise ValueError("evidence link requires claim, evidence, or calc reference")
        return self


class MethodologyAnswer(CoverageBaseModel):
    """Structured answer/provenance for a methodology question."""

    answer_text: str
    claim_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    calc_ids: list[str] = Field(default_factory=list)
    requires_calculation: bool = False

    @field_validator("claim_ids", "evidence_ids", "calc_ids")
    @classmethod
    def _references_not_blank(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("reference values must not be blank")
        return cleaned

    @model_validator(mode="after")
    def _validate_answer(self) -> MethodologyAnswer:
        if not self.answer_text.strip():
            raise ValueError("answer_text must not be blank")
        if self.requires_calculation and not self.calc_ids:
            raise ValueError("calculation-backed answer requires calc_id")
        return self


class MethodologyCoverageRecord(CoverageBaseModel):
    """Coverage ledger record scoped to tenant/deal/run/question."""

    tenant_id: str
    deal_id: str
    run_id: str
    methodology_id: str
    methodology_version_id: str
    methodology_question_id: str
    methodology_type: MethodologyType
    section: str
    status: MethodologyCoverageStatus = MethodologyCoverageStatus.NOT_STARTED
    reason_code: str | None = None
    evidence_links: list[MethodologyEvidenceLink] = Field(default_factory=list)
    answer: MethodologyAnswer | None = None
    conflict_ids: list[str] = Field(default_factory=list)
    defect_ids: list[str] = Field(default_factory=list)

    @property
    def coverage_record_id(self) -> str:
        """Stable record ID for this scoped methodology question."""
        seed = "|".join(
            [
                self.tenant_id,
                self.deal_id,
                self.run_id,
                self.methodology_version_id,
                self.methodology_question_id,
            ]
        )
        return "mc_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]

    def to_deterministic_json(self) -> str:
        """Serialize record deterministically."""
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    @field_validator(
        "tenant_id",
        "deal_id",
        "run_id",
        "methodology_id",
        "methodology_version_id",
        "methodology_question_id",
        "section",
    )
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @field_validator("reason_code")
    @classmethod
    def _reason_code_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("reason_code must not be blank")
        return cleaned

    @field_validator("conflict_ids", "defect_ids")
    @classmethod
    def _references_not_blank(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("reference values must not be blank")
        return cleaned

    @model_validator(mode="after")
    def _validate_status_requirements(self) -> MethodologyCoverageRecord:
        if (
            self.status
            in {
                MethodologyCoverageStatus.BLOCKED,
                MethodologyCoverageStatus.EVIDENCE_MISSING,
                MethodologyCoverageStatus.UNSUPPORTED_SOURCE,
            }
            and not self.reason_code
        ):
            raise ValueError(f"{self.status.value} requires reason_code")
        if self.status == MethodologyCoverageStatus.CONTRADICTED and not (
            self.conflict_ids or self.defect_ids
        ):
            raise ValueError("contradicted requires conflict or defect reference")
        if self.status == MethodologyCoverageStatus.ANSWERED:
            has_answer_refs = self.answer is not None and bool(
                self.answer.claim_ids or self.answer.evidence_ids or self.answer.calc_ids
            )
            if not (self.evidence_links or has_answer_refs):
                raise ValueError("answered requires source claim, evidence, or calc reference")
        return self


class MethodologyCoverageSummary(CoverageBaseModel):
    """Aggregated coverage summary."""

    tenant_id: str
    deal_id: str
    run_id: str
    total_questions: int
    by_status: dict[str, int]
    by_methodology_type: dict[str, int]
    by_section: dict[str, int]
