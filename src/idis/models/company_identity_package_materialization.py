"""Slice 15 in-memory run-scoped company identity package models."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from enum import StrEnum
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

COMPANY_IDENTITY_NAMESPACE = UUID("4b425c64-8fef-5c43-8cb2-2da58deef239")
COMPANY_IDENTITY_PACKAGE_NAMESPACE = UUID("d60eed56-62eb-547f-a81b-103bb51f30b2")

__all__ = [
    "MethodologyCompanyIdentityPackageConstructionStatus",
    "MethodologyCompanyIdentityPackageReason",
    "MethodologyCompanyIdentityPackageRejection",
    "MethodologyCompanyIdentityPackageRunResult",
    "MethodologyCompanyIdentityStatus",
    "RunScopedCompanyIdentityBlocker",
    "RunScopedCompanyIdentityPackageRecord",
    "RunScopedCompanyIdentityPackageShell",
    "RunScopedCompanyIdentityPackageSummary",
    "counter",
    "deterministic_company_identity_id",
    "deterministic_company_identity_package_id",
]


class MethodologyCompanyIdentityPackageConstructionStatus(StrEnum):
    """Company identity package construction status."""

    COMPLETED = "completed"
    FAILED = "failed"


class MethodologyCompanyIdentityStatus(StrEnum):
    """Run-scoped company identity mapping status."""

    MAPPED = "mapped"
    BLOCKED = "blocked"
    DEFERRED = "deferred"


class MethodologyCompanyIdentityPackageReason(StrEnum):
    """Stable Slice 15 identity package reasons."""

    EXPLICIT_DEAL_COMPANY_LABEL = "explicit_deal_company_label"
    MISSING_DEAL_METADATA = "missing_deal_metadata"
    BLANK_COMPANY_LABEL = "blank_company_label"
    TENANT_OR_DEAL_MISMATCH = "tenant_or_deal_mismatch"
    DUPLICATE_INPUT = "duplicate_input"


class CompanyIdentityPackageBaseModel(BaseModel):
    """Base model for deterministic Slice 15 data."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RunScopedCompanyIdentityBlocker(CompanyIdentityPackageBaseModel):
    """Safe company identity blocker record."""

    blocker_id: str
    reason: MethodologyCompanyIdentityPackageReason
    severity: str
    source_artifact_type: str
    source_artifact_id: str

    @field_validator("blocker_id", "severity", "source_artifact_type", "source_artifact_id")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()


class RunScopedCompanyIdentityPackageSummary(CompanyIdentityPackageBaseModel):
    """Safe aggregate summary for Slice 15 company identity packages."""

    tenant_id: str
    deal_id: str
    run_id: str
    package_count: int = Field(..., ge=0)
    company_identity_count: int = Field(..., ge=0)
    blocker_count: int = Field(..., ge=0)
    construction_status: MethodologyCompanyIdentityPackageConstructionStatus
    identity_status: MethodologyCompanyIdentityStatus
    by_reason: dict[str, int]
    by_blocker_severity: dict[str, int]

    @field_validator("tenant_id", "deal_id", "run_id")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @field_validator("by_reason", "by_blocker_severity")
    @classmethod
    def _counts_sorted(cls, value: dict[str, int]) -> dict[str, int]:
        return _sorted_counts(value)

    def to_safe_dict(self) -> dict[str, object]:
        """Return a summary-safe dictionary."""
        return {
            "package_count": self.package_count,
            "company_identity_count": self.company_identity_count,
            "blocker_count": self.blocker_count,
            "construction_status": self.construction_status.value,
            "identity_status": self.identity_status.value,
            "by_reason": dict(self.by_reason),
            "by_blocker_severity": dict(self.by_blocker_severity),
        }


class RunScopedCompanyIdentityPackageShell(CompanyIdentityPackageBaseModel):
    """Safe resume shell for a run-scoped company identity package."""

    tenant_id: str
    deal_id: str
    run_id: str
    identity_package_id: str
    source_deal_id: str
    company_identity_ids: list[str]
    construction_status: MethodologyCompanyIdentityPackageConstructionStatus
    identity_status: MethodologyCompanyIdentityStatus
    reason_codes: list[str]
    blocker_ids: list[str]
    source_fields_present: list[str]
    identifier_types_present: list[str]
    by_reason: dict[str, int]
    by_blocker_severity: dict[str, int]

    @field_validator("tenant_id", "deal_id", "run_id", "identity_package_id", "source_deal_id")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @field_validator(
        "company_identity_ids",
        "reason_codes",
        "blocker_ids",
        "source_fields_present",
        "identifier_types_present",
    )
    @classmethod
    def _ids_sorted(cls, value: list[str]) -> list[str]:
        return _sorted_strings(value)

    @field_validator("by_reason", "by_blocker_severity")
    @classmethod
    def _counts_sorted(cls, value: dict[str, int]) -> dict[str, int]:
        return _sorted_counts(value)


class RunScopedCompanyIdentityPackageRecord(CompanyIdentityPackageBaseModel):
    """In-memory governed company identity input-boundary package."""

    tenant_id: str
    deal_id: str
    run_id: str
    identity_package_id: str
    source_deal_id: str
    company_identity_ids: list[str]
    construction_status: MethodologyCompanyIdentityPackageConstructionStatus
    identity_status: MethodologyCompanyIdentityStatus
    reason_codes: list[str]
    blocker_ids: list[str]
    blockers: list[RunScopedCompanyIdentityBlocker]
    source_fields_present: list[str]
    identifier_types_present: list[str]

    @field_validator("tenant_id", "deal_id", "run_id", "identity_package_id", "source_deal_id")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @field_validator(
        "company_identity_ids",
        "reason_codes",
        "blocker_ids",
        "source_fields_present",
        "identifier_types_present",
    )
    @classmethod
    def _ids_sorted(cls, value: list[str]) -> list[str]:
        return _sorted_strings(value)

    @model_validator(mode="after")
    def _blocker_ids_match(self) -> RunScopedCompanyIdentityPackageRecord:
        expected = _sorted_strings(blocker.blocker_id for blocker in self.blockers)
        if self.blocker_ids != expected:
            raise ValueError("blocker_ids must match blockers")
        return self

    def to_shell(self) -> RunScopedCompanyIdentityPackageShell:
        """Build a safe shell without raw company labels."""
        summary = self.to_summary()
        return RunScopedCompanyIdentityPackageShell(
            tenant_id=self.tenant_id,
            deal_id=self.deal_id,
            run_id=self.run_id,
            identity_package_id=self.identity_package_id,
            source_deal_id=self.source_deal_id,
            company_identity_ids=list(self.company_identity_ids),
            construction_status=self.construction_status,
            identity_status=self.identity_status,
            reason_codes=list(self.reason_codes),
            blocker_ids=list(self.blocker_ids),
            source_fields_present=list(self.source_fields_present),
            identifier_types_present=list(self.identifier_types_present),
            by_reason=dict(summary.by_reason),
            by_blocker_severity=dict(summary.by_blocker_severity),
        )

    def to_summary(self) -> RunScopedCompanyIdentityPackageSummary:
        """Build a summary-safe aggregate view."""
        return RunScopedCompanyIdentityPackageSummary(
            tenant_id=self.tenant_id,
            deal_id=self.deal_id,
            run_id=self.run_id,
            package_count=1,
            company_identity_count=len(self.company_identity_ids),
            blocker_count=len(self.blockers),
            construction_status=self.construction_status,
            identity_status=self.identity_status,
            by_reason=counter(self.reason_codes),
            by_blocker_severity=counter(blocker.severity for blocker in self.blockers),
        )

    def to_run_step_summary(self) -> dict[str, object]:
        """Return a safe run-step summary for the company identity boundary."""
        shell = self.to_shell()
        return {
            "construction_status": self.construction_status.value,
            "identity_status": self.identity_status.value,
            "boundary": "company identity input boundary",
            "identity_package_ids": [self.identity_package_id],
            "source_deal_ids": [self.source_deal_id],
            "company_identity_ids": shell.company_identity_ids,
            "source_fields_present": shell.source_fields_present,
            "identifier_types_present": shell.identifier_types_present,
            "reason_codes": shell.reason_codes,
            "blocker_ids": shell.blocker_ids,
            "package_shells": [shell.model_dump(mode="json")],
            "summary": self.to_summary().to_safe_dict(),
        }


class MethodologyCompanyIdentityPackageRejection(CompanyIdentityPackageBaseModel):
    """Stable reason-coded company identity package rejection."""

    source_artifact_id: str | None = None
    reason: MethodologyCompanyIdentityPackageReason
    reason_codes: list[str]
    message: str

    @field_validator("reason_codes")
    @classmethod
    def _reason_codes_sorted(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("reason_codes must not be empty")
        return _sorted_strings(value)


class MethodologyCompanyIdentityPackageRunResult(CompanyIdentityPackageBaseModel):
    """Result for one company identity package run."""

    tenant_id: str
    deal_id: str
    run_id: str
    construction_status: MethodologyCompanyIdentityPackageConstructionStatus
    identity_status: MethodologyCompanyIdentityStatus
    package_shells: list[RunScopedCompanyIdentityPackageShell]
    rejections: list[MethodologyCompanyIdentityPackageRejection]
    summary: RunScopedCompanyIdentityPackageSummary

    def to_run_step_summary(self) -> dict[str, object]:
        """Return a safe run-step summary."""
        identity_package_ids = [shell.identity_package_id for shell in self.package_shells]
        source_deal_ids = [shell.source_deal_id for shell in self.package_shells]
        company_identity_ids = sorted(
            {item for shell in self.package_shells for item in shell.company_identity_ids}
        )
        return {
            "construction_status": self.construction_status.value,
            "identity_status": self.identity_status.value,
            "boundary": "company identity input boundary",
            "identity_package_ids": identity_package_ids,
            "source_deal_ids": source_deal_ids,
            "company_identity_ids": company_identity_ids,
            "source_fields_present": sorted(
                {item for shell in self.package_shells for item in shell.source_fields_present}
            ),
            "identifier_types_present": sorted(
                {item for shell in self.package_shells for item in shell.identifier_types_present}
            ),
            "reason_codes": sorted(
                {item for shell in self.package_shells for item in shell.reason_codes}
                | {item for rejection in self.rejections for item in rejection.reason_codes}
            ),
            "blocker_ids": sorted(
                {item for shell in self.package_shells for item in shell.blocker_ids}
            ),
            "rejections": [rejection.model_dump(mode="json") for rejection in self.rejections],
            "package_shells": [shell.model_dump(mode="json") for shell in self.package_shells],
            "summary": self.summary.to_safe_dict(),
        }


def deterministic_company_identity_id(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    source_deal_id: str,
    company_name: str,
) -> str:
    """Return a deterministic run-scoped company identity ID."""
    return _uuid5(
        COMPANY_IDENTITY_NAMESPACE,
        {
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "run_id": run_id,
            "source_deal_id": source_deal_id,
            "company_label": _canonical_company_label(company_name),
        },
    )


def deterministic_company_identity_package_id(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    company_identity_ids: list[str],
    reason_codes: list[str],
) -> str:
    """Return a deterministic run-scoped company identity package ID."""
    return _uuid5(
        COMPANY_IDENTITY_PACKAGE_NAMESPACE,
        {
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "run_id": run_id,
            "company_identity_ids": _sorted_strings(company_identity_ids),
            "reason_codes": _sorted_strings(reason_codes),
        },
    )


def counter(values: Iterable[str]) -> dict[str, int]:
    """Return stable sorted counts for string values."""
    return _sorted_counts(dict(Counter(values)))


def _canonical_company_label(value: str) -> str:
    return " ".join(value.split()).casefold()


def _sorted_strings(values: Iterable[str]) -> list[str]:
    return sorted({str(value).strip() for value in values if str(value).strip()})


def _sorted_counts(value: dict[str, int]) -> dict[str, int]:
    return {key: int(value[key]) for key in sorted(value)}


def _uuid5(namespace: UUID, payload: dict[str, object]) -> str:
    return str(uuid5(namespace, _canonical_json(payload)))


def _canonical_json(payload: dict[str, object]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))
