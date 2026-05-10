"""Slice 14 in-memory run-scoped Layer 2 readiness package models."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from enum import StrEnum
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

LAYER2_READINESS_PACKAGE_NAMESPACE = UUID("43ce6106-0d81-5d6c-9f4e-4ee349580f12")

__all__ = [
    "MethodologyLayer2ReadinessPackageConstructionStatus",
    "MethodologyLayer2ReadinessPackageReason",
    "MethodologyLayer2ReadinessPackageRejection",
    "MethodologyLayer2ReadinessPackageRunResult",
    "MethodologyLayer2ReadinessStatus",
    "RunScopedLayer2ReadinessBlocker",
    "RunScopedLayer2ReadinessPackageRecord",
    "RunScopedLayer2ReadinessPackageShell",
    "RunScopedLayer2ReadinessPackageSummary",
    "counter",
    "deterministic_layer2_readiness_package_id",
]


class MethodologyLayer2ReadinessPackageConstructionStatus(StrEnum):
    """Slice 14 package construction status."""

    COMPLETED = "completed"
    FAILED = "failed"


class MethodologyLayer2ReadinessStatus(StrEnum):
    """Layer 2 readiness status; separate from package construction."""

    READY = "ready"
    BLOCKED = "blocked"
    DEFERRED = "deferred"


class MethodologyLayer2ReadinessPackageReason(StrEnum):
    """Stable Slice 14 readiness and construction reasons."""

    MISSING_VALIDATED_EVIDENCE_PACKAGE = "missing_validated_evidence_package"
    MISSING_EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN = (
        "missing_external_intelligence_conflict_check_plan"
    )
    EXTERNAL_INTELLIGENCE_CHECKS_PLANNED_NOT_EXECUTED = (
        "external_intelligence_checks_planned_not_executed"
    )
    NO_EXECUTED_PROVIDER_CHECKS = "no_executed_provider_checks"
    MISSING_COMPANY_IDENTITY = "missing_company_identity"
    MISSING_ENRICHMENT_FACTS = "missing_enrichment_facts"
    MISSING_CALC_REFS = "missing_calc_refs"
    MISSING_CLAIM_REFS = "missing_claim_refs"
    TENANT_OR_RUN_MISMATCH = "tenant_or_run_mismatch"
    DUPLICATE_INPUT = "duplicate_input"
    LAYER2_EXECUTION_DEFERRED = "layer2_execution_deferred"


class Layer2ReadinessPackageBaseModel(BaseModel):
    """Base model for deterministic Slice 14 data."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RunScopedLayer2ReadinessBlocker(Layer2ReadinessPackageBaseModel):
    """Safe blocker record for Layer 2 input readiness."""

    blocker_id: str
    reason: MethodologyLayer2ReadinessPackageReason
    severity: str
    source_artifact_type: str
    source_artifact_id: str

    @field_validator("blocker_id", "severity", "source_artifact_type", "source_artifact_id")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()


class RunScopedLayer2ReadinessPackageSummary(Layer2ReadinessPackageBaseModel):
    """Safe aggregate summary for Slice 14 readiness packages."""

    tenant_id: str
    deal_id: str
    run_id: str
    package_count: int = Field(..., ge=0)
    claim_count: int = Field(..., ge=0)
    calc_count: int = Field(..., ge=0)
    provider_check_count: int = Field(..., ge=0)
    executed_provider_check_count: int = Field(..., ge=0)
    blocker_count: int = Field(..., ge=0)
    construction_status: MethodologyLayer2ReadinessPackageConstructionStatus
    readiness_status: MethodologyLayer2ReadinessStatus
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
            "claim_count": self.claim_count,
            "calc_count": self.calc_count,
            "provider_check_count": self.provider_check_count,
            "executed_provider_check_count": self.executed_provider_check_count,
            "blocker_count": self.blocker_count,
            "construction_status": self.construction_status.value,
            "readiness_status": self.readiness_status.value,
            "by_reason": dict(self.by_reason),
            "by_blocker_severity": dict(self.by_blocker_severity),
        }


class RunScopedLayer2ReadinessPackageShell(Layer2ReadinessPackageBaseModel):
    """Safe resume shell for a run-scoped Layer 2 readiness package."""

    tenant_id: str
    deal_id: str
    run_id: str
    readiness_package_id: str
    source_vep_package_id: str
    source_external_intelligence_plan_id: str
    claim_ids: list[str]
    calc_ids: list[str]
    provider_check_ids: list[str]
    executed_provider_check_ids: list[str]
    company_identity_ids: list[str]
    enrichment_fact_ids: list[str]
    construction_status: MethodologyLayer2ReadinessPackageConstructionStatus
    readiness_status: MethodologyLayer2ReadinessStatus
    reason_codes: list[str]
    blocker_ids: list[str]
    by_reason: dict[str, int]
    by_blocker_severity: dict[str, int]

    @field_validator(
        "tenant_id",
        "deal_id",
        "run_id",
        "readiness_package_id",
        "source_vep_package_id",
        "source_external_intelligence_plan_id",
    )
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @field_validator(
        "claim_ids",
        "calc_ids",
        "provider_check_ids",
        "executed_provider_check_ids",
        "company_identity_ids",
        "enrichment_fact_ids",
        "reason_codes",
        "blocker_ids",
    )
    @classmethod
    def _ids_sorted(cls, value: list[str]) -> list[str]:
        return _sorted_strings(value)

    @field_validator("by_reason", "by_blocker_severity")
    @classmethod
    def _counts_sorted(cls, value: dict[str, int]) -> dict[str, int]:
        return _sorted_counts(value)


class RunScopedLayer2ReadinessPackageRecord(Layer2ReadinessPackageBaseModel):
    """In-memory governed Layer 2 readiness/input-boundary package."""

    tenant_id: str
    deal_id: str
    run_id: str
    readiness_package_id: str
    source_vep_package_id: str
    source_external_intelligence_plan_id: str
    claim_ids: list[str]
    calc_ids: list[str]
    provider_check_ids: list[str]
    executed_provider_check_ids: list[str]
    company_identity_ids: list[str]
    enrichment_fact_ids: list[str]
    construction_status: MethodologyLayer2ReadinessPackageConstructionStatus
    readiness_status: MethodologyLayer2ReadinessStatus
    reason_codes: list[str]
    blockers: list[RunScopedLayer2ReadinessBlocker]

    @field_validator(
        "tenant_id",
        "deal_id",
        "run_id",
        "readiness_package_id",
        "source_vep_package_id",
        "source_external_intelligence_plan_id",
    )
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @field_validator(
        "claim_ids",
        "calc_ids",
        "provider_check_ids",
        "executed_provider_check_ids",
        "company_identity_ids",
        "enrichment_fact_ids",
        "reason_codes",
    )
    @classmethod
    def _ids_sorted(cls, value: list[str]) -> list[str]:
        return _sorted_strings(value)

    def to_shell(self) -> RunScopedLayer2ReadinessPackageShell:
        """Build a safe shell without Layer 2 outputs or factual payloads."""
        summary = self.to_summary()
        return RunScopedLayer2ReadinessPackageShell(
            tenant_id=self.tenant_id,
            deal_id=self.deal_id,
            run_id=self.run_id,
            readiness_package_id=self.readiness_package_id,
            source_vep_package_id=self.source_vep_package_id,
            source_external_intelligence_plan_id=self.source_external_intelligence_plan_id,
            claim_ids=list(self.claim_ids),
            calc_ids=list(self.calc_ids),
            provider_check_ids=list(self.provider_check_ids),
            executed_provider_check_ids=list(self.executed_provider_check_ids),
            company_identity_ids=list(self.company_identity_ids),
            enrichment_fact_ids=list(self.enrichment_fact_ids),
            construction_status=self.construction_status,
            readiness_status=self.readiness_status,
            reason_codes=list(self.reason_codes),
            blocker_ids=[blocker.blocker_id for blocker in self.blockers],
            by_reason=dict(summary.by_reason),
            by_blocker_severity=dict(summary.by_blocker_severity),
        )

    def to_summary(self) -> RunScopedLayer2ReadinessPackageSummary:
        """Build a summary-safe aggregate view."""
        return RunScopedLayer2ReadinessPackageSummary(
            tenant_id=self.tenant_id,
            deal_id=self.deal_id,
            run_id=self.run_id,
            package_count=1,
            claim_count=len(self.claim_ids),
            calc_count=len(self.calc_ids),
            provider_check_count=len(self.provider_check_ids),
            executed_provider_check_count=len(self.executed_provider_check_ids),
            blocker_count=len(self.blockers),
            construction_status=self.construction_status,
            readiness_status=self.readiness_status,
            by_reason=counter(self.reason_codes),
            by_blocker_severity=counter(blocker.severity for blocker in self.blockers),
        )

    def to_run_step_summary(self) -> dict[str, object]:
        """Return a safe run-step summary for the readiness/input-boundary."""
        shell = self.to_shell()
        return {
            "construction_status": self.construction_status.value,
            "readiness_status": self.readiness_status.value,
            "boundary": "Layer 2 readiness/input-boundary",
            "readiness_package_ids": [self.readiness_package_id],
            "source_vep_package_ids": [self.source_vep_package_id],
            "source_external_intelligence_plan_ids": [self.source_external_intelligence_plan_id],
            "claim_ids": shell.claim_ids,
            "calc_ids": shell.calc_ids,
            "provider_check_ids": shell.provider_check_ids,
            "executed_provider_check_ids": shell.executed_provider_check_ids,
            "company_identity_ids": shell.company_identity_ids,
            "enrichment_fact_ids": shell.enrichment_fact_ids,
            "reason_codes": shell.reason_codes,
            "blocker_ids": shell.blocker_ids,
            "package_shells": [shell.model_dump(mode="json")],
            "summary": self.to_summary().to_safe_dict(),
        }


class MethodologyLayer2ReadinessPackageRejection(Layer2ReadinessPackageBaseModel):
    """Stable reason-coded Slice 14 construction rejection."""

    source_artifact_id: str | None = None
    reason: MethodologyLayer2ReadinessPackageReason
    reason_codes: list[str]
    message: str

    @field_validator("source_artifact_id")
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
    def _reason_codes_include_reason(self) -> MethodologyLayer2ReadinessPackageRejection:
        if self.reason.value not in self.reason_codes:
            raise ValueError("reason_codes must include reason value")
        return self


class MethodologyLayer2ReadinessPackageRunResult(Layer2ReadinessPackageBaseModel):
    """Run-step-safe Slice 14 readiness package result."""

    tenant_id: str
    deal_id: str
    run_id: str
    construction_status: MethodologyLayer2ReadinessPackageConstructionStatus
    readiness_status: MethodologyLayer2ReadinessStatus
    package_shells: list[RunScopedLayer2ReadinessPackageShell] = Field(default_factory=list)
    rejections: list[MethodologyLayer2ReadinessPackageRejection] = Field(default_factory=list)
    summary: RunScopedLayer2ReadinessPackageSummary

    def to_run_step_summary(self) -> dict[str, object]:
        """Return safe summary without Layer 2 execution outputs."""
        return {
            "construction_status": self.construction_status.value,
            "readiness_status": self.readiness_status.value,
            "boundary": "Layer 2 readiness/input-boundary",
            "readiness_package_ids": [shell.readiness_package_id for shell in self.package_shells],
            "source_vep_package_ids": [
                shell.source_vep_package_id for shell in self.package_shells
            ],
            "source_external_intelligence_plan_ids": [
                shell.source_external_intelligence_plan_id for shell in self.package_shells
            ],
            "claim_ids": sorted(
                {item for shell in self.package_shells for item in shell.claim_ids}
            ),
            "calc_ids": sorted({item for shell in self.package_shells for item in shell.calc_ids}),
            "provider_check_ids": sorted(
                {item for shell in self.package_shells for item in shell.provider_check_ids}
            ),
            "executed_provider_check_ids": sorted(
                {
                    item
                    for shell in self.package_shells
                    for item in shell.executed_provider_check_ids
                }
            ),
            "company_identity_ids": sorted(
                {item for shell in self.package_shells for item in shell.company_identity_ids}
            ),
            "enrichment_fact_ids": sorted(
                {item for shell in self.package_shells for item in shell.enrichment_fact_ids}
            ),
            "reason_codes": sorted(
                {item for shell in self.package_shells for item in shell.reason_codes}
            ),
            "blocker_ids": sorted(
                {item for shell in self.package_shells for item in shell.blocker_ids}
            ),
            "package_shells": [shell.model_dump(mode="json") for shell in self.package_shells],
            "rejections": [
                {
                    "source_artifact_id": rejection.source_artifact_id,
                    "reason": rejection.reason.value,
                    "reason_codes": list(rejection.reason_codes),
                }
                for rejection in self.rejections
            ],
            "summary": self.summary.to_safe_dict(),
        }


def deterministic_layer2_readiness_package_id(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    vep_package_id: str,
    external_intelligence_plan_id: str,
    claim_ids: list[str],
    calc_ids: list[str],
    reason_codes: list[str],
) -> str:
    """Generate a deterministic UUID v5 Layer 2 readiness package ID."""
    return _uuid5(
        LAYER2_READINESS_PACKAGE_NAMESPACE,
        {
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "run_id": run_id,
            "vep_package_id": vep_package_id,
            "external_intelligence_plan_id": external_intelligence_plan_id,
            "claim_ids": sorted(claim_ids),
            "calc_ids": sorted(calc_ids),
            "reason_codes": sorted(reason_codes),
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


def _uuid5(namespace: UUID, seed: dict[str, object]) -> str:
    return str(uuid5(namespace, _canonical_json(seed)))


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
