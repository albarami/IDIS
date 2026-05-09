"""Slice 11 in-memory run-scoped Evidence Trust Court models."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from enum import StrEnum
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from idis.models.debate import DebateRole, StopReason
from idis.models.evidence_trust_court_aliases import (
    EvidenceTrustAliasMaps,
    EvidenceTrustIdType,
    build_evidence_trust_alias_maps,
)
from idis.models.sanad import SanadGrade
from idis.models.truth_dashboard_materialization import TruthDashboardVerdict

EVIDENCE_TRUST_COURT_NAMESPACE = UUID("514df5da-9c30-568c-9842-00853108643f")

__all__ = [
    "EvidenceTrustAliasMaps",
    "EvidenceTrustDisposition",
    "EvidenceTrustFindingType",
    "EvidenceTrustIdType",
    "MethodologyEvidenceTrustCourtReason",
    "MethodologyEvidenceTrustCourtRejection",
    "MethodologyEvidenceTrustCourtRunResult",
    "MethodologyEvidenceTrustCourtStatus",
    "RunScopedClaimTrustAssessment",
    "RunScopedEvidenceTrustCourtFinding",
    "RunScopedEvidenceTrustCourtRecord",
    "RunScopedEvidenceTrustCourtRoleSummary",
    "RunScopedEvidenceTrustCourtShell",
    "RunScopedEvidenceTrustCourtSummary",
    "build_evidence_trust_alias_maps",
    "counter",
    "deterministic_evidence_trust_court_id",
]


class MethodologyEvidenceTrustCourtStatus(StrEnum):
    """Aggregate Slice 11 Evidence Trust Court status."""

    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class EvidenceTrustDisposition(StrEnum):
    """Layer 1 evidence-trust disposition for one claim."""

    TRUSTED = "trusted"
    DISPUTED = "disputed"
    REJECTED = "rejected"
    UNVERIFIED = "unverified"


class EvidenceTrustFindingType(StrEnum):
    """Layer 1 court finding categories."""

    PROVENANCE = "provenance"
    SANAD_DEFECT = "sanad_defect"
    CONTRADICTION = "contradiction"
    DASHBOARD_CONSISTENCY = "dashboard_consistency"
    MUHASABAH_GATE = "muhasabah_gate"


class MethodologyEvidenceTrustCourtReason(StrEnum):
    """Machine-readable Slice 11 blocker/rejection reasons."""

    MISSING_MATERIALIZED_CLAIMS = "missing_materialized_claims"
    MISSING_EVIDENCE_ITEMS = "missing_evidence_items"
    MISSING_SANADS = "missing_sanads"
    MISSING_SANAD_GRADES = "missing_sanad_grades"
    MISSING_TRUTH_DASHBOARD = "missing_truth_dashboard"
    TRUTH_DASHBOARD_SHELL_ONLY = "truth_dashboard_shell_only"
    TENANT_OR_RUN_MISMATCH = "tenant_or_run_mismatch"
    MISSING_SOURCE_PROVENANCE = "missing_source_provenance"
    MISSING_EVIDENCE_LINKAGE = "missing_evidence_linkage"
    DASHBOARD_REFUTED = "dashboard_refuted"
    MISSING_SANAD_GRADE = "missing_sanad_grade"
    MUHASABAH_GATE_REJECTED = "muhasabah_gate_rejected"
    DUPLICATE_CLAIM_INPUT = "duplicate_claim_input"


class EvidenceTrustCourtBaseModel(BaseModel):
    """Base model for deterministic Slice 11 data."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RunScopedClaimTrustAssessment(EvidenceTrustCourtBaseModel):
    """Layer 1 Evidence Trust Court assessment for one run-scoped claim."""

    claim_id: str
    disposition: EvidenceTrustDisposition
    evidence_ids: list[str]
    source_span_ids: list[str]
    sanad_id: str
    sanad_grade: SanadGrade
    dashboard_verdict: TruthDashboardVerdict
    calc_ids: list[str] = Field(default_factory=list)
    defect_ids: list[str] = Field(default_factory=list)
    reason_codes: list[str]

    @field_validator("claim_id", "sanad_id")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @field_validator("evidence_ids", "source_span_ids", "calc_ids", "defect_ids", "reason_codes")
    @classmethod
    def _ids_sorted(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("list values must not be blank")
        return sorted(set(cleaned))


class RunScopedEvidenceTrustCourtFinding(EvidenceTrustCourtBaseModel):
    """Summary-safe Layer 1 court finding without descriptions or payloads."""

    finding_id: str
    finding_type: EvidenceTrustFindingType
    claim_id: str
    evidence_ids: list[str] = Field(default_factory=list)
    sanad_id: str | None = None
    calc_ids: list[str] = Field(default_factory=list)
    defect_ids: list[str] = Field(default_factory=list)
    reason_codes: list[str]

    @field_validator("finding_id", "claim_id")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @field_validator("evidence_ids", "calc_ids", "defect_ids", "reason_codes")
    @classmethod
    def _string_list_not_blank(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("list values must not be blank")
        return sorted(set(cleaned))


class RunScopedEvidenceTrustCourtRoleSummary(EvidenceTrustCourtBaseModel):
    """Safe role-output summary that excludes message and output content."""

    output_id: str
    agent_id: str
    role: DebateRole
    output_type: str
    supported_claim_ids: list[str] = Field(default_factory=list)
    supported_calc_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(..., ge=0.0, le=1.0)
    reason_codes: list[str]

    @field_validator("output_id", "agent_id", "output_type")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @field_validator("supported_claim_ids", "supported_calc_ids", "reason_codes")
    @classmethod
    def _ids_sorted(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("list values must not be blank")
        return sorted(set(cleaned))


class RunScopedEvidenceTrustCourtShell(EvidenceTrustCourtBaseModel):
    """Safe resume shell for a run-scoped Evidence Trust Court record."""

    tenant_id: str
    deal_id: str
    run_id: str
    court_id: str
    dashboard_id: str
    claim_ids: list[str]
    evidence_ids: list[str]
    source_span_ids: list[str]
    sanad_ids: list[str]
    calc_ids: list[str]
    defect_ids: list[str]
    finding_ids: list[str]
    assessed_claim_count: int = Field(..., ge=0)
    finding_count: int = Field(..., ge=0)
    by_disposition: dict[str, int]
    by_grade: dict[str, int]
    by_dashboard_verdict: dict[str, int]
    status: str

    @field_validator("tenant_id", "deal_id", "run_id", "court_id", "dashboard_id", "status")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @field_validator(
        "claim_ids",
        "evidence_ids",
        "source_span_ids",
        "sanad_ids",
        "calc_ids",
        "defect_ids",
        "finding_ids",
    )
    @classmethod
    def _ids_sorted(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("list values must not be blank")
        return sorted(set(cleaned))


class RunScopedEvidenceTrustCourtRecord(EvidenceTrustCourtBaseModel):
    """In-memory governed Layer 1 Evidence Trust Court boundary."""

    tenant_id: str
    deal_id: str
    run_id: str
    court_id: str
    dashboard_id: str
    claim_assessments: list[RunScopedClaimTrustAssessment]
    findings: list[RunScopedEvidenceTrustCourtFinding] = Field(default_factory=list)
    role_summaries: list[RunScopedEvidenceTrustCourtRoleSummary] = Field(default_factory=list)
    stop_reason: StopReason | None = None
    status: str

    @field_validator("tenant_id", "deal_id", "run_id", "court_id", "dashboard_id", "status")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    def to_shell(
        self, *, summary: RunScopedEvidenceTrustCourtSummary
    ) -> RunScopedEvidenceTrustCourtShell:
        """Build a safe shell without factual assertions or debate content."""
        return RunScopedEvidenceTrustCourtShell(
            tenant_id=self.tenant_id,
            deal_id=self.deal_id,
            run_id=self.run_id,
            court_id=self.court_id,
            dashboard_id=self.dashboard_id,
            claim_ids=[assessment.claim_id for assessment in self.claim_assessments],
            evidence_ids=[
                evidence_id
                for assessment in self.claim_assessments
                for evidence_id in assessment.evidence_ids
            ],
            source_span_ids=[
                source_span_id
                for assessment in self.claim_assessments
                for source_span_id in assessment.source_span_ids
            ],
            sanad_ids=[assessment.sanad_id for assessment in self.claim_assessments],
            calc_ids=[
                calc_id for assessment in self.claim_assessments for calc_id in assessment.calc_ids
            ],
            defect_ids=[
                defect_id
                for assessment in self.claim_assessments
                for defect_id in assessment.defect_ids
            ],
            finding_ids=[finding.finding_id for finding in self.findings],
            assessed_claim_count=summary.assessed_claim_count,
            finding_count=summary.finding_count,
            by_disposition=dict(summary.by_disposition),
            by_grade=dict(summary.by_grade),
            by_dashboard_verdict=dict(summary.by_dashboard_verdict),
            status=self.status,
        )

    def to_run_step_summary(
        self, *, summary: RunScopedEvidenceTrustCourtSummary
    ) -> dict[str, object]:
        """Return a safe run-step summary without transcripts, payloads, or aliases."""
        shell = self.to_shell(summary=summary)
        return {
            "status": summary.aggregate_status().value,
            "court_ids": [self.court_id],
            "dashboard_ids": [self.dashboard_id],
            "claim_ids": shell.claim_ids,
            "evidence_ids": shell.evidence_ids,
            "source_span_ids": shell.source_span_ids,
            "sanad_ids": shell.sanad_ids,
            "calc_ids": shell.calc_ids,
            "defect_ids": shell.defect_ids,
            "finding_ids": shell.finding_ids,
            "court_shells": [shell.model_dump(mode="json")],
            "role_summaries": [
                role_summary.model_dump(mode="json") for role_summary in self.role_summaries
            ],
            "summary": summary.to_safe_dict(),
        }


class MethodologyEvidenceTrustCourtRejection(EvidenceTrustCourtBaseModel):
    """Stable reason-coded Slice 11 rejection/blocker."""

    claim_id: str | None = None
    reason: MethodologyEvidenceTrustCourtReason
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
    def _reason_codes_include_reason(self) -> MethodologyEvidenceTrustCourtRejection:
        if self.reason.value not in self.reason_codes:
            raise ValueError("reason_codes must include reason value")
        return self


class RunScopedEvidenceTrustCourtSummary(EvidenceTrustCourtBaseModel):
    """Safe aggregate summary for Slice 11."""

    tenant_id: str
    deal_id: str
    run_id: str
    total_claims: int = Field(..., ge=0)
    assessed_claim_count: int = Field(..., ge=0)
    finding_count: int = Field(..., ge=0)
    rejected_count: int = Field(..., ge=0)
    by_disposition: dict[str, int]
    by_reason: dict[str, int]
    by_grade: dict[str, int]
    by_dashboard_verdict: dict[str, int]

    def aggregate_status(self) -> MethodologyEvidenceTrustCourtStatus:
        """Return aggregate Slice 11 status."""
        if self.assessed_claim_count and self.rejected_count:
            return MethodologyEvidenceTrustCourtStatus.PARTIAL
        if self.rejected_count:
            return MethodologyEvidenceTrustCourtStatus.FAILED
        return MethodologyEvidenceTrustCourtStatus.COMPLETED

    def to_safe_dict(self) -> dict[str, object]:
        """Return a summary-safe dictionary."""
        return {
            "total_claims": self.total_claims,
            "assessed_claim_count": self.assessed_claim_count,
            "finding_count": self.finding_count,
            "rejected_count": self.rejected_count,
            "by_disposition": dict(self.by_disposition),
            "by_reason": dict(self.by_reason),
            "by_grade": dict(self.by_grade),
            "by_dashboard_verdict": dict(self.by_dashboard_verdict),
        }


class MethodologyEvidenceTrustCourtRunResult(EvidenceTrustCourtBaseModel):
    """Run-step-safe Slice 11 result."""

    tenant_id: str
    deal_id: str
    run_id: str
    status: MethodologyEvidenceTrustCourtStatus
    court_shells: list[RunScopedEvidenceTrustCourtShell] = Field(default_factory=list)
    role_summaries: list[RunScopedEvidenceTrustCourtRoleSummary] = Field(default_factory=list)
    rejections: list[MethodologyEvidenceTrustCourtRejection] = Field(default_factory=list)
    summary: RunScopedEvidenceTrustCourtSummary

    def to_run_step_summary(self, *, status: str | None = None) -> dict[str, object]:
        """Return safe summary without debate transcripts or payloads."""
        return {
            "status": status or self.status.value,
            "court_ids": [shell.court_id for shell in self.court_shells],
            "dashboard_ids": [shell.dashboard_id for shell in self.court_shells],
            "claim_ids": sorted(
                {claim_id for shell in self.court_shells for claim_id in shell.claim_ids}
            ),
            "evidence_ids": sorted(
                {evidence_id for shell in self.court_shells for evidence_id in shell.evidence_ids}
            ),
            "source_span_ids": sorted(
                {
                    source_span_id
                    for shell in self.court_shells
                    for source_span_id in shell.source_span_ids
                }
            ),
            "sanad_ids": sorted(
                {sanad_id for shell in self.court_shells for sanad_id in shell.sanad_ids}
            ),
            "calc_ids": sorted(
                {calc_id for shell in self.court_shells for calc_id in shell.calc_ids}
            ),
            "defect_ids": sorted(
                {defect_id for shell in self.court_shells for defect_id in shell.defect_ids}
            ),
            "finding_ids": sorted(
                {finding_id for shell in self.court_shells for finding_id in shell.finding_ids}
            ),
            "court_shells": [shell.model_dump(mode="json") for shell in self.court_shells],
            "role_summaries": [
                role_summary.model_dump(mode="json") for role_summary in self.role_summaries
            ],
            "rejections": [
                {
                    "claim_id": rejection.claim_id,
                    "reason": rejection.reason.value,
                    "reason_codes": list(rejection.reason_codes),
                }
                for rejection in self.rejections
            ],
            "summary": self.summary.to_safe_dict(),
        }


def deterministic_evidence_trust_court_id(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    claim_ids: list[str],
    dashboard_id: str,
) -> str:
    """Generate a deterministic UUID v5 Evidence Trust Court ID."""
    return _uuid5(
        EVIDENCE_TRUST_COURT_NAMESPACE,
        {
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "run_id": run_id,
            "claim_ids": sorted(claim_ids),
            "dashboard_id": dashboard_id,
        },
    )


def counter(items: Iterable[str]) -> dict[str, int]:
    """Return deterministic counts for summary fields."""
    return dict(sorted(Counter(items).items()))


def _uuid5(namespace: UUID, seed: dict[str, object]) -> str:
    return str(uuid5(namespace, _canonical_json(seed)))


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
