"""Slice 12 in-memory run-scoped Validated Evidence Package models."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from enum import StrEnum
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

VALIDATED_EVIDENCE_PACKAGE_NAMESPACE = UUID("3c132b6e-dab5-5321-9d68-68604015bf7c")

__all__ = [
    "MethodologyValidatedEvidencePackageReason",
    "MethodologyValidatedEvidencePackageRejection",
    "MethodologyValidatedEvidencePackageRunResult",
    "MethodologyValidatedEvidencePackageStatus",
    "RunScopedValidatedEvidencePackageRecord",
    "RunScopedValidatedEvidencePackageShell",
    "RunScopedValidatedEvidencePackageSummary",
    "counter",
    "deterministic_validated_evidence_package_id",
]


class MethodologyValidatedEvidencePackageStatus(StrEnum):
    """Aggregate Slice 12 Validated Evidence Package construction status."""

    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class MethodologyValidatedEvidencePackageReason(StrEnum):
    """Machine-readable Slice 12 blocker/rejection reasons."""

    MISSING_EVIDENCE_TRUST_COURT = "missing_evidence_trust_court"
    EVIDENCE_TRUST_COURT_SHELL_ONLY = "evidence_trust_court_shell_only"
    TENANT_OR_RUN_MISMATCH = "tenant_or_run_mismatch"
    MISSING_COURT_REFERENCE = "missing_court_reference"
    DUPLICATE_COURT_INPUT = "duplicate_court_input"


class ValidatedEvidencePackageBaseModel(BaseModel):
    """Base model for deterministic Slice 12 data."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RunScopedValidatedEvidencePackageSummary(ValidatedEvidencePackageBaseModel):
    """Safe aggregate summary for Slice 12."""

    tenant_id: str
    deal_id: str
    run_id: str
    package_count: int = Field(..., ge=0)
    packaged_claim_count: int = Field(..., ge=0)
    finding_count: int = Field(..., ge=0)
    by_disposition: dict[str, int]
    by_grade: dict[str, int]
    by_dashboard_verdict: dict[str, int]
    by_finding_type: dict[str, int]
    by_reason: dict[str, int]

    @field_validator("tenant_id", "deal_id", "run_id")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @field_validator(
        "by_disposition",
        "by_grade",
        "by_dashboard_verdict",
        "by_finding_type",
        "by_reason",
    )
    @classmethod
    def _counts_sorted(cls, value: dict[str, int]) -> dict[str, int]:
        if any(not key.strip() for key in value):
            raise ValueError("count keys must not be blank")
        if any(count < 0 for count in value.values()):
            raise ValueError("count values must not be negative")
        return {key.strip(): value[key] for key in sorted(value)}

    def aggregate_status(self) -> MethodologyValidatedEvidencePackageStatus:
        """Return aggregate Slice 12 construction status."""
        if self.package_count > 0:
            return MethodologyValidatedEvidencePackageStatus.COMPLETED
        if not self.by_reason:
            return MethodologyValidatedEvidencePackageStatus.COMPLETED
        return MethodologyValidatedEvidencePackageStatus.FAILED

    def to_safe_dict(self) -> dict[str, object]:
        """Return a summary-safe dictionary."""
        return {
            "package_count": self.package_count,
            "packaged_claim_count": self.packaged_claim_count,
            "finding_count": self.finding_count,
            "by_disposition": dict(self.by_disposition),
            "by_grade": dict(self.by_grade),
            "by_dashboard_verdict": dict(self.by_dashboard_verdict),
            "by_finding_type": dict(self.by_finding_type),
            "by_reason": dict(self.by_reason),
        }


class RunScopedValidatedEvidencePackageShell(ValidatedEvidencePackageBaseModel):
    """Safe resume shell for a run-scoped Validated Evidence Package."""

    tenant_id: str
    deal_id: str
    run_id: str
    package_id: str
    court_id: str
    dashboard_id: str
    claim_ids_by_disposition: dict[str, list[str]]
    evidence_ids: list[str]
    source_span_ids: list[str]
    sanad_ids: list[str]
    defect_ids: list[str]
    calc_ids: list[str]
    finding_ids: list[str]
    finding_types: list[str]
    role_names: list[str]
    reason_codes: list[str]
    by_disposition: dict[str, int]
    by_grade: dict[str, int]
    by_dashboard_verdict: dict[str, int]
    by_finding_type: dict[str, int]
    by_reason: dict[str, int]
    status: MethodologyValidatedEvidencePackageStatus

    @field_validator("tenant_id", "deal_id", "run_id", "package_id", "court_id", "dashboard_id")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @field_validator(
        "evidence_ids",
        "source_span_ids",
        "sanad_ids",
        "defect_ids",
        "calc_ids",
        "finding_ids",
        "finding_types",
        "role_names",
        "reason_codes",
    )
    @classmethod
    def _ids_sorted(cls, value: list[str]) -> list[str]:
        return _sorted_strings(value)

    @field_validator("claim_ids_by_disposition")
    @classmethod
    def _claim_ids_by_disposition_sorted(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        if any(not key.strip() for key in value):
            raise ValueError("disposition keys must not be blank")
        return {key.strip(): _sorted_strings(value[key]) for key in sorted(value)}

    @field_validator(
        "by_disposition",
        "by_grade",
        "by_dashboard_verdict",
        "by_finding_type",
        "by_reason",
    )
    @classmethod
    def _counts_sorted(cls, value: dict[str, int]) -> dict[str, int]:
        return _sorted_counts(value)


class RunScopedValidatedEvidencePackageRecord(ValidatedEvidencePackageBaseModel):
    """In-memory governed Layer 1 Validated Evidence Package boundary."""

    tenant_id: str
    deal_id: str
    run_id: str
    package_id: str
    court_id: str
    dashboard_id: str
    claim_ids_by_disposition: dict[str, list[str]]
    evidence_ids: list[str]
    source_span_ids: list[str]
    sanad_ids: list[str]
    defect_ids: list[str]
    calc_ids: list[str]
    finding_ids: list[str]
    finding_types: list[str]
    role_names: list[str]
    reason_codes: list[str]
    by_disposition: dict[str, int]
    by_grade: dict[str, int]
    by_dashboard_verdict: dict[str, int]
    by_finding_type: dict[str, int]
    by_reason: dict[str, int]
    status: MethodologyValidatedEvidencePackageStatus

    @field_validator("tenant_id", "deal_id", "run_id", "package_id", "court_id", "dashboard_id")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @field_validator(
        "evidence_ids",
        "source_span_ids",
        "sanad_ids",
        "defect_ids",
        "calc_ids",
        "finding_ids",
        "finding_types",
        "role_names",
        "reason_codes",
    )
    @classmethod
    def _ids_sorted(cls, value: list[str]) -> list[str]:
        return _sorted_strings(value)

    @field_validator("claim_ids_by_disposition")
    @classmethod
    def _claim_ids_by_disposition_sorted(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        if any(not key.strip() for key in value):
            raise ValueError("disposition keys must not be blank")
        return {key.strip(): _sorted_strings(value[key]) for key in sorted(value)}

    @field_validator(
        "by_disposition",
        "by_grade",
        "by_dashboard_verdict",
        "by_finding_type",
        "by_reason",
    )
    @classmethod
    def _counts_sorted(cls, value: dict[str, int]) -> dict[str, int]:
        return _sorted_counts(value)

    def to_shell(self) -> RunScopedValidatedEvidencePackageShell:
        """Build a safe shell without factual payloads or debate content."""
        return RunScopedValidatedEvidencePackageShell(
            tenant_id=self.tenant_id,
            deal_id=self.deal_id,
            run_id=self.run_id,
            package_id=self.package_id,
            court_id=self.court_id,
            dashboard_id=self.dashboard_id,
            claim_ids_by_disposition=dict(self.claim_ids_by_disposition),
            evidence_ids=list(self.evidence_ids),
            source_span_ids=list(self.source_span_ids),
            sanad_ids=list(self.sanad_ids),
            defect_ids=list(self.defect_ids),
            calc_ids=list(self.calc_ids),
            finding_ids=list(self.finding_ids),
            finding_types=list(self.finding_types),
            role_names=list(self.role_names),
            reason_codes=list(self.reason_codes),
            by_disposition=dict(self.by_disposition),
            by_grade=dict(self.by_grade),
            by_dashboard_verdict=dict(self.by_dashboard_verdict),
            by_finding_type=dict(self.by_finding_type),
            by_reason=dict(self.by_reason),
            status=self.status,
        )

    def to_summary(self) -> RunScopedValidatedEvidencePackageSummary:
        """Build a summary-safe aggregate view."""
        return RunScopedValidatedEvidencePackageSummary(
            tenant_id=self.tenant_id,
            deal_id=self.deal_id,
            run_id=self.run_id,
            package_count=1,
            packaged_claim_count=sum(len(ids) for ids in self.claim_ids_by_disposition.values()),
            finding_count=len(self.finding_ids),
            by_disposition=dict(self.by_disposition),
            by_grade=dict(self.by_grade),
            by_dashboard_verdict=dict(self.by_dashboard_verdict),
            by_finding_type=dict(self.by_finding_type),
            by_reason=dict(self.by_reason),
        )

    def to_run_step_summary(self) -> dict[str, object]:
        """Return a safe run-step summary without payloads or recommendations."""
        shell = self.to_shell()
        return {
            "status": self.status.value,
            "package_ids": [self.package_id],
            "court_ids": [self.court_id],
            "dashboard_ids": [self.dashboard_id],
            "claim_ids_by_disposition": dict(shell.claim_ids_by_disposition),
            "evidence_ids": shell.evidence_ids,
            "source_span_ids": shell.source_span_ids,
            "sanad_ids": shell.sanad_ids,
            "defect_ids": shell.defect_ids,
            "calc_ids": shell.calc_ids,
            "finding_ids": shell.finding_ids,
            "finding_types": shell.finding_types,
            "role_names": shell.role_names,
            "reason_codes": shell.reason_codes,
            "package_shells": [shell.model_dump(mode="json")],
            "summary": self.to_summary().to_safe_dict(),
        }


class MethodologyValidatedEvidencePackageRejection(ValidatedEvidencePackageBaseModel):
    """Stable reason-coded Slice 12 rejection/blocker."""

    court_id: str | None = None
    reason: MethodologyValidatedEvidencePackageReason
    reason_codes: list[str]
    message: str

    @field_validator("court_id")
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
        return _sorted_strings(value)

    @field_validator("message")
    @classmethod
    def _message_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must not be blank")
        return value.strip()

    @model_validator(mode="after")
    def _reason_codes_include_reason(self) -> MethodologyValidatedEvidencePackageRejection:
        if self.reason.value not in self.reason_codes:
            raise ValueError("reason_codes must include reason value")
        return self


class MethodologyValidatedEvidencePackageRunResult(ValidatedEvidencePackageBaseModel):
    """Run-step-safe Slice 12 result."""

    tenant_id: str
    deal_id: str
    run_id: str
    status: MethodologyValidatedEvidencePackageStatus
    package_shells: list[RunScopedValidatedEvidencePackageShell] = Field(default_factory=list)
    rejections: list[MethodologyValidatedEvidencePackageRejection] = Field(default_factory=list)
    summary: RunScopedValidatedEvidencePackageSummary

    def to_run_step_summary(self, *, status: str | None = None) -> dict[str, object]:
        """Return safe summary without raw facts, transcripts, or recommendations."""
        return {
            "status": status or self.status.value,
            "package_ids": [shell.package_id for shell in self.package_shells],
            "court_ids": [shell.court_id for shell in self.package_shells],
            "dashboard_ids": [shell.dashboard_id for shell in self.package_shells],
            "claim_ids_by_disposition": _merge_disposition_claims(self.package_shells),
            "evidence_ids": sorted(
                {item for shell in self.package_shells for item in shell.evidence_ids}
            ),
            "source_span_ids": sorted(
                {item for shell in self.package_shells for item in shell.source_span_ids}
            ),
            "sanad_ids": sorted(
                {item for shell in self.package_shells for item in shell.sanad_ids}
            ),
            "defect_ids": sorted(
                {item for shell in self.package_shells for item in shell.defect_ids}
            ),
            "calc_ids": sorted({item for shell in self.package_shells for item in shell.calc_ids}),
            "finding_ids": sorted(
                {item for shell in self.package_shells for item in shell.finding_ids}
            ),
            "finding_types": sorted(
                {item for shell in self.package_shells for item in shell.finding_types}
            ),
            "role_names": sorted(
                {item for shell in self.package_shells for item in shell.role_names}
            ),
            "reason_codes": sorted(
                {item for shell in self.package_shells for item in shell.reason_codes}
            ),
            "package_shells": [shell.model_dump(mode="json") for shell in self.package_shells],
            "rejections": [
                {
                    "court_id": rejection.court_id,
                    "reason": rejection.reason.value,
                    "reason_codes": list(rejection.reason_codes),
                }
                for rejection in self.rejections
            ],
            "summary": self.summary.to_safe_dict(),
        }


def deterministic_validated_evidence_package_id(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    court_id: str,
    claim_ids: list[str],
    finding_ids: list[str],
) -> str:
    """Generate a deterministic UUID v5 Validated Evidence Package ID."""
    return _uuid5(
        VALIDATED_EVIDENCE_PACKAGE_NAMESPACE,
        {
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "run_id": run_id,
            "court_id": court_id,
            "claim_ids": sorted(claim_ids),
            "finding_ids": sorted(finding_ids),
        },
    )


def counter(items: Iterable[str]) -> dict[str, int]:
    """Return deterministic counts for summary fields."""
    return dict(sorted(Counter(items).items()))


def _sorted_strings(value: list[str]) -> list[str]:
    cleaned = [item.strip() for item in value]
    if any(not item for item in cleaned):
        raise ValueError("list values must not be blank")
    return sorted(set(cleaned))


def _sorted_counts(value: dict[str, int]) -> dict[str, int]:
    if any(not key.strip() for key in value):
        raise ValueError("count keys must not be blank")
    if any(count < 0 for count in value.values()):
        raise ValueError("count values must not be negative")
    return {key.strip(): value[key] for key in sorted(value)}


def _merge_disposition_claims(
    package_shells: list[RunScopedValidatedEvidencePackageShell],
) -> dict[str, list[str]]:
    disposition_claims: dict[str, set[str]] = {}
    for shell in package_shells:
        for disposition, claim_ids in shell.claim_ids_by_disposition.items():
            disposition_claims.setdefault(disposition, set()).update(claim_ids)
    return {
        disposition: sorted(claim_ids)
        for disposition, claim_ids in sorted(disposition_claims.items())
    }


def _uuid5(namespace: UUID, seed: dict[str, object]) -> str:
    return str(uuid5(namespace, _canonical_json(seed)))


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
