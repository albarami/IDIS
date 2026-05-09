"""Slice 10 in-memory run-scoped Truth Dashboard models."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from enum import StrEnum
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from idis.models.deliverables import TruthDashboard
from idis.models.sanad import SanadGrade

TRUTH_DASHBOARD_NAMESPACE = UUID("0c4f08f4-b2d4-5e6d-98b0-600f85b94134")
TRUTH_DASHBOARD_ROW_NAMESPACE = UUID("0f9a8dd2-013b-50ff-a882-8efebc1f6e10")


class MethodologyTruthDashboardStatus(StrEnum):
    """Aggregate Slice 10 Truth Dashboard status."""

    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class TruthDashboardVerdict(StrEnum):
    """Canonical Truth Dashboard row verdict."""

    CONFIRMED = "CONFIRMED"
    DISPUTED = "DISPUTED"
    UNVERIFIED = "UNVERIFIED"
    REFUTED = "REFUTED"


class MethodologyTruthDashboardReason(StrEnum):
    """Machine-readable Slice 10 rejection reasons."""

    MISSING_MATERIALIZED_CLAIMS = "missing_materialized_claims"
    MISSING_EVIDENCE_ITEMS = "missing_evidence_items"
    MISSING_SANADS = "missing_sanads"
    MISSING_SANAD_GRADES = "missing_sanad_grades"
    MISSING_SOURCE_PROVENANCE = "missing_source_provenance"
    TENANT_OR_RUN_MISMATCH = "tenant_or_run_mismatch"
    DUPLICATE_CLAIM_ROW = "duplicate_claim_row"
    MISSING_CLAIM_ID = "missing_claim_id"
    MISSING_EVIDENCE_LINKAGE = "missing_evidence_linkage"
    MISSING_SANAD_GRADE = "missing_sanad_grade"
    MISSING_SANAD = "missing_sanad"
    SHELL_ONLY_INPUT = "shell_only_input"
    TRUTH_DASHBOARD_VALIDATION_FAILED = "truth_dashboard_validation_failed"


class TruthDashboardMaterializationBaseModel(BaseModel):
    """Base model for deterministic Slice 10 data."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class MethodologyTruthDashboardMapping(TruthDashboardMaterializationBaseModel):
    """Summary-safe mapping for one Truth Dashboard row."""

    dashboard_id: str
    row_id: str
    claim_id: str
    evidence_ids: list[str]
    sanad_id: str
    calc_ids: list[str] = Field(default_factory=list)
    defect_ids: list[str] = Field(default_factory=list)
    sanad_grade: SanadGrade
    verdict: TruthDashboardVerdict
    methodology_question_id: str
    coverage_record_id: str
    extraction_task_id: str
    extraction_output_id: str
    status: str

    @field_validator(
        "dashboard_id",
        "row_id",
        "claim_id",
        "sanad_id",
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

    @field_validator("evidence_ids", "calc_ids", "defect_ids")
    @classmethod
    def _string_list_not_blank(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("list values must not be blank")
        return sorted(set(cleaned))


class RunScopedTruthDashboardShell(TruthDashboardMaterializationBaseModel):
    """Safe resume shell for a run-scoped Truth Dashboard."""

    tenant_id: str
    deal_id: str
    run_id: str
    dashboard_id: str
    row_ids: list[str]
    claim_ids: list[str]
    evidence_ids: list[str]
    sanad_ids: list[str]
    calc_ids: list[str]
    defect_ids: list[str]
    row_count: int = Field(..., ge=0)
    by_verdict: dict[str, int]
    by_grade: dict[str, int]
    status: str

    @field_validator("tenant_id", "deal_id", "run_id", "dashboard_id", "status")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @field_validator("row_ids", "claim_ids", "evidence_ids", "sanad_ids", "calc_ids", "defect_ids")
    @classmethod
    def _ids_sorted(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("list values must not be blank")
        return sorted(set(cleaned))


class RunScopedTruthDashboardRecord(TruthDashboardMaterializationBaseModel):
    """In-memory governed Truth Dashboard boundary for Slice 10."""

    tenant_id: str
    deal_id: str
    run_id: str
    dashboard_id: str
    dashboard: TruthDashboard
    row_mappings: list[MethodologyTruthDashboardMapping]
    status: str

    @model_validator(mode="after")
    def _dashboard_scope_matches_record(self) -> RunScopedTruthDashboardRecord:
        if self.dashboard.tenant_id != self.tenant_id:
            raise ValueError("dashboard tenant_id must match record tenant_id")
        if self.dashboard.deal_id != self.deal_id:
            raise ValueError("dashboard deal_id must match record deal_id")
        if self.dashboard.deliverable_id != self.dashboard_id:
            raise ValueError("dashboard deliverable_id must match dashboard_id")
        return self

    def to_shell(self) -> RunScopedTruthDashboardShell:
        """Build a safe shell without row assertions or dashboard payload."""
        return RunScopedTruthDashboardShell(
            tenant_id=self.tenant_id,
            deal_id=self.deal_id,
            run_id=self.run_id,
            dashboard_id=self.dashboard_id,
            row_ids=[mapping.row_id for mapping in self.row_mappings],
            claim_ids=[mapping.claim_id for mapping in self.row_mappings],
            evidence_ids=[
                evidence_id for mapping in self.row_mappings for evidence_id in mapping.evidence_ids
            ],
            sanad_ids=[mapping.sanad_id for mapping in self.row_mappings],
            calc_ids=[calc_id for mapping in self.row_mappings for calc_id in mapping.calc_ids],
            defect_ids=[
                defect_id for mapping in self.row_mappings for defect_id in mapping.defect_ids
            ],
            row_count=len(self.row_mappings),
            by_verdict=counter(mapping.verdict.value for mapping in self.row_mappings),
            by_grade=counter(mapping.sanad_grade.value for mapping in self.row_mappings),
            status=self.status,
        )


class MethodologyTruthDashboardRejection(TruthDashboardMaterializationBaseModel):
    """Stable reason-coded Slice 10 rejection."""

    claim_id: str | None = None
    reason: MethodologyTruthDashboardReason
    reason_codes: list[str]
    message: str

    @field_validator("claim_id")
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
    def _reason_codes_include_reason(self) -> MethodologyTruthDashboardRejection:
        if self.reason.value not in self.reason_codes:
            raise ValueError("reason_codes must include reason value")
        return self


class MethodologyTruthDashboardSummary(TruthDashboardMaterializationBaseModel):
    """Safe aggregate summary for Slice 10."""

    tenant_id: str
    deal_id: str
    run_id: str
    total_claims: int
    created_row_count: int
    rejected_count: int
    by_status: dict[str, int]
    by_reason: dict[str, int]
    by_verdict: dict[str, int]
    by_grade: dict[str, int]


class MethodologyTruthDashboardRunResult(TruthDashboardMaterializationBaseModel):
    """Run-step-safe Slice 10 result."""

    tenant_id: str
    deal_id: str
    run_id: str
    status: MethodologyTruthDashboardStatus
    dashboard_mappings: list[MethodologyTruthDashboardMapping] = Field(default_factory=list)
    dashboard_shells: list[RunScopedTruthDashboardShell] = Field(default_factory=list)
    rejections: list[MethodologyTruthDashboardRejection] = Field(default_factory=list)
    summary: MethodologyTruthDashboardSummary

    def to_run_step_summary(self, *, status: str | None = None) -> dict[str, object]:
        """Return safe summary without row assertions or dashboard payloads."""
        return {
            "status": status or self.status.value,
            "dashboard_ids": sorted(
                {mapping.dashboard_id for mapping in self.dashboard_mappings}
                | {shell.dashboard_id for shell in self.dashboard_shells}
            ),
            "row_ids": [mapping.row_id for mapping in self.dashboard_mappings],
            "claim_ids": sorted({mapping.claim_id for mapping in self.dashboard_mappings}),
            "evidence_ids": sorted(
                {
                    evidence_id
                    for mapping in self.dashboard_mappings
                    for evidence_id in mapping.evidence_ids
                }
            ),
            "sanad_ids": sorted({mapping.sanad_id for mapping in self.dashboard_mappings}),
            "calc_ids": sorted(
                {calc_id for mapping in self.dashboard_mappings for calc_id in mapping.calc_ids}
            ),
            "defect_ids": sorted(
                {
                    defect_id
                    for mapping in self.dashboard_mappings
                    for defect_id in mapping.defect_ids
                }
            ),
            "dashboard_shells": [shell.model_dump(mode="json") for shell in self.dashboard_shells],
            "rejections": [
                {
                    "claim_id": rejection.claim_id,
                    "reason": rejection.reason.value,
                    "reason_codes": list(rejection.reason_codes),
                }
                for rejection in self.rejections
            ],
            "summary": {
                "total_claims": self.summary.total_claims,
                "created_row_count": self.summary.created_row_count,
                "rejected_count": self.summary.rejected_count,
                "by_status": dict(self.summary.by_status),
                "by_reason": dict(self.summary.by_reason),
                "by_verdict": dict(self.summary.by_verdict),
                "by_grade": dict(self.summary.by_grade),
            },
        }


def deterministic_truth_dashboard_id(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    claim_ids: list[str],
) -> str:
    """Generate a deterministic UUID v5 dashboard ID."""
    return _uuid5(
        TRUTH_DASHBOARD_NAMESPACE,
        {
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "run_id": run_id,
            "claim_ids": sorted(claim_ids),
        },
    )


def deterministic_truth_dashboard_row_id(
    *,
    dashboard_id: str,
    claim_id: str,
    sanad_id: str,
    evidence_ids: list[str],
    calc_ids: list[str],
) -> str:
    """Generate a deterministic UUID v5 Truth Dashboard row ID."""
    return _uuid5(
        TRUTH_DASHBOARD_ROW_NAMESPACE,
        {
            "dashboard_id": dashboard_id,
            "claim_id": claim_id,
            "sanad_id": sanad_id,
            "evidence_ids": sorted(evidence_ids),
            "calc_ids": sorted(calc_ids),
        },
    )


def aggregate_status(
    *,
    mappings: list[MethodologyTruthDashboardMapping],
    rejections: list[MethodologyTruthDashboardRejection],
) -> MethodologyTruthDashboardStatus:
    """Return aggregate Slice 10 status."""
    if mappings and rejections:
        return MethodologyTruthDashboardStatus.PARTIAL
    if rejections:
        return MethodologyTruthDashboardStatus.FAILED
    return MethodologyTruthDashboardStatus.COMPLETED


def counter(items: Iterable[str]) -> dict[str, int]:
    """Return deterministic counts for summary fields."""
    return dict(sorted(Counter(items).items()))


def _uuid5(namespace: UUID, seed: dict[str, object]) -> str:
    return str(uuid5(namespace, _canonical_json(seed)))


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
