"""Slice 13 in-memory external intelligence conflict-check plan models."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from enum import StrEnum
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN_NAMESPACE = UUID("b3bf8f41-3355-55f4-92a9-5af5e7f271ec")

__all__ = [
    "ExternalIntelligencePlanCheckStatus",
    "MethodologyExternalIntelligenceConflictCheckPlanReason",
    "MethodologyExternalIntelligenceConflictCheckPlanRejection",
    "MethodologyExternalIntelligenceConflictCheckPlanRunResult",
    "MethodologyExternalIntelligenceConflictCheckPlanStatus",
    "RunScopedExternalIntelligenceConflictCheckPlanRecord",
    "RunScopedExternalIntelligenceConflictCheckPlanShell",
    "RunScopedExternalIntelligenceConflictCheckPlanSummary",
    "RunScopedExternalIntelligenceProviderCheck",
    "counter",
    "deterministic_external_intelligence_conflict_check_plan_id",
    "deterministic_external_intelligence_provider_check_id",
]


class MethodologyExternalIntelligenceConflictCheckPlanStatus(StrEnum):
    """Aggregate Slice 13 plan construction status."""

    COMPLETED = "completed"
    FAILED = "failed"


class ExternalIntelligencePlanCheckStatus(StrEnum):
    """Status of one provider/check plan entry."""

    PLANNED = "planned"
    BLOCKED = "blocked"
    DEFERRED = "deferred"
    UNAVAILABLE = "unavailable"
    NO_OP = "no_op"


class MethodologyExternalIntelligenceConflictCheckPlanReason(StrEnum):
    """Stable Slice 13 plan construction and provider-check reasons."""

    MISSING_VALIDATED_EVIDENCE_PACKAGE = "missing_validated_evidence_package"
    TENANT_OR_RUN_MISMATCH = "tenant_or_run_mismatch"
    DUPLICATE_VEP_INPUT = "duplicate_vep_input"
    NO_PACKAGE_IDS = "no_package_ids"
    MISSING_QUERY_IDENTIFIERS = "missing_query_identifiers"
    PROVIDER_REQUIRES_BYOL = "provider_requires_byol"
    PROVIDER_RIGHTS_BLOCKED = "provider_rights_blocked"
    PROVIDER_DEFERRED = "provider_deferred"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    LIVE_PROVIDER_CALLS_DEFERRED = "live_provider_calls_deferred"


class ExternalIntelligenceConflictCheckPlanBaseModel(BaseModel):
    """Base model for deterministic Slice 13 plan data."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RunScopedExternalIntelligenceProviderCheck(ExternalIntelligenceConflictCheckPlanBaseModel):
    """Safe provider/check plan entry; no provider fetch payloads are represented."""

    check_id: str
    provider_id: str
    check_type: str
    status: ExternalIntelligencePlanCheckStatus
    rights_class: str
    requires_byol: bool
    reason_codes: list[str]

    @field_validator("check_id", "provider_id", "check_type", "rights_class")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @field_validator("reason_codes")
    @classmethod
    def _reason_codes_sorted(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("reason_codes must not be empty")
        return _sorted_strings(value)


class RunScopedExternalIntelligenceConflictCheckPlanSummary(
    ExternalIntelligenceConflictCheckPlanBaseModel
):
    """Safe aggregate summary for Slice 13."""

    tenant_id: str
    deal_id: str
    run_id: str
    plan_count: int = Field(..., ge=0)
    check_count: int = Field(..., ge=0)
    by_status: dict[str, int]
    by_provider: dict[str, int]
    by_rights_class: dict[str, int]
    by_reason: dict[str, int]

    @field_validator("tenant_id", "deal_id", "run_id")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @field_validator("by_status", "by_provider", "by_rights_class", "by_reason")
    @classmethod
    def _counts_sorted(cls, value: dict[str, int]) -> dict[str, int]:
        return _sorted_counts(value)

    def aggregate_status(self) -> MethodologyExternalIntelligenceConflictCheckPlanStatus:
        """Return aggregate Slice 13 construction status."""
        if self.plan_count > 0:
            return MethodologyExternalIntelligenceConflictCheckPlanStatus.COMPLETED
        if self.by_status.get(ExternalIntelligencePlanCheckStatus.NO_OP.value, 0) > 0:
            return MethodologyExternalIntelligenceConflictCheckPlanStatus.COMPLETED
        if not self.by_reason:
            return MethodologyExternalIntelligenceConflictCheckPlanStatus.COMPLETED
        return MethodologyExternalIntelligenceConflictCheckPlanStatus.FAILED

    def to_safe_dict(self) -> dict[str, object]:
        """Return a summary-safe dictionary."""
        return {
            "plan_count": self.plan_count,
            "check_count": self.check_count,
            "by_status": dict(self.by_status),
            "by_provider": dict(self.by_provider),
            "by_rights_class": dict(self.by_rights_class),
            "by_reason": dict(self.by_reason),
        }


class RunScopedExternalIntelligenceConflictCheckPlanShell(
    ExternalIntelligenceConflictCheckPlanBaseModel
):
    """Safe resume shell for a run-scoped external intelligence plan."""

    tenant_id: str
    deal_id: str
    run_id: str
    plan_id: str
    package_id: str
    provider_check_ids: list[str]
    provider_ids: list[str]
    check_statuses: list[str]
    reason_codes: list[str]
    by_status: dict[str, int]
    by_provider: dict[str, int]
    by_rights_class: dict[str, int]
    by_reason: dict[str, int]
    status: MethodologyExternalIntelligenceConflictCheckPlanStatus

    @field_validator("tenant_id", "deal_id", "run_id", "plan_id", "package_id")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    @field_validator("provider_check_ids", "provider_ids", "check_statuses", "reason_codes")
    @classmethod
    def _ids_sorted(cls, value: list[str]) -> list[str]:
        return _sorted_strings(value)

    @field_validator("by_status", "by_provider", "by_rights_class", "by_reason")
    @classmethod
    def _counts_sorted(cls, value: dict[str, int]) -> dict[str, int]:
        return _sorted_counts(value)


class RunScopedExternalIntelligenceConflictCheckPlanRecord(
    ExternalIntelligenceConflictCheckPlanBaseModel
):
    """In-memory governed Slice 13 external intelligence plan boundary."""

    tenant_id: str
    deal_id: str
    run_id: str
    plan_id: str
    package_id: str
    checks: list[RunScopedExternalIntelligenceProviderCheck]
    status: MethodologyExternalIntelligenceConflictCheckPlanStatus

    @field_validator("tenant_id", "deal_id", "run_id", "plan_id", "package_id")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()

    def to_shell(self) -> RunScopedExternalIntelligenceConflictCheckPlanShell:
        """Build a safe shell without provider payloads or factual claims."""
        summary = self.to_summary()
        return RunScopedExternalIntelligenceConflictCheckPlanShell(
            tenant_id=self.tenant_id,
            deal_id=self.deal_id,
            run_id=self.run_id,
            plan_id=self.plan_id,
            package_id=self.package_id,
            provider_check_ids=[check.check_id for check in self.checks],
            provider_ids=[check.provider_id for check in self.checks],
            check_statuses=[check.status.value for check in self.checks],
            reason_codes=[reason for check in self.checks for reason in check.reason_codes],
            by_status=dict(summary.by_status),
            by_provider=dict(summary.by_provider),
            by_rights_class=dict(summary.by_rights_class),
            by_reason=dict(summary.by_reason),
            status=self.status,
        )

    def to_summary(self) -> RunScopedExternalIntelligenceConflictCheckPlanSummary:
        """Build a summary-safe aggregate view."""
        return RunScopedExternalIntelligenceConflictCheckPlanSummary(
            tenant_id=self.tenant_id,
            deal_id=self.deal_id,
            run_id=self.run_id,
            plan_count=1,
            check_count=len(self.checks),
            by_status=counter(check.status.value for check in self.checks),
            by_provider=counter(check.provider_id for check in self.checks),
            by_rights_class=counter(check.rights_class for check in self.checks),
            by_reason=counter(reason for check in self.checks for reason in check.reason_codes),
        )

    def to_run_step_summary(self) -> dict[str, object]:
        """Return a safe run-step summary for the plan boundary."""
        shell = self.to_shell()
        return {
            "status": self.status.value,
            "boundary": "external intelligence conflict-check plan boundary",
            "plan_ids": [self.plan_id],
            "package_ids": [self.package_id],
            "provider_check_ids": shell.provider_check_ids,
            "provider_ids": shell.provider_ids,
            "check_statuses": shell.check_statuses,
            "reason_codes": shell.reason_codes,
            "plan_shells": [shell.model_dump(mode="json")],
            "summary": self.to_summary().to_safe_dict(),
        }


class MethodologyExternalIntelligenceConflictCheckPlanRejection(
    ExternalIntelligenceConflictCheckPlanBaseModel
):
    """Stable reason-coded Slice 13 rejection/blocker."""

    package_id: str | None = None
    reason: MethodologyExternalIntelligenceConflictCheckPlanReason
    reason_codes: list[str]
    message: str

    @field_validator("package_id")
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
    def _reason_codes_include_reason(
        self,
    ) -> MethodologyExternalIntelligenceConflictCheckPlanRejection:
        if self.reason.value not in self.reason_codes:
            raise ValueError("reason_codes must include reason value")
        return self


class MethodologyExternalIntelligenceConflictCheckPlanRunResult(
    ExternalIntelligenceConflictCheckPlanBaseModel
):
    """Run-step-safe Slice 13 plan result."""

    tenant_id: str
    deal_id: str
    run_id: str
    status: MethodologyExternalIntelligenceConflictCheckPlanStatus
    plan_shells: list[RunScopedExternalIntelligenceConflictCheckPlanShell] = Field(
        default_factory=list
    )
    rejections: list[MethodologyExternalIntelligenceConflictCheckPlanRejection] = Field(
        default_factory=list
    )
    summary: RunScopedExternalIntelligenceConflictCheckPlanSummary

    def to_run_step_summary(self, *, status: str | None = None) -> dict[str, object]:
        """Return safe summary without provider payloads, facts, or recommendations."""
        return {
            "status": status or self.status.value,
            "boundary": "external intelligence conflict-check plan boundary",
            "plan_ids": [shell.plan_id for shell in self.plan_shells],
            "package_ids": [shell.package_id for shell in self.plan_shells],
            "provider_check_ids": sorted(
                {item for shell in self.plan_shells for item in shell.provider_check_ids}
            ),
            "provider_ids": sorted(
                {item for shell in self.plan_shells for item in shell.provider_ids}
            ),
            "check_statuses": sorted(
                {item for shell in self.plan_shells for item in shell.check_statuses}
            ),
            "reason_codes": sorted(
                {item for shell in self.plan_shells for item in shell.reason_codes}
            ),
            "plan_shells": [shell.model_dump(mode="json") for shell in self.plan_shells],
            "rejections": [
                {
                    "package_id": rejection.package_id,
                    "reason": rejection.reason.value,
                    "reason_codes": list(rejection.reason_codes),
                }
                for rejection in self.rejections
            ],
            "summary": self.summary.to_safe_dict(),
        }


def deterministic_external_intelligence_conflict_check_plan_id(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    package_id: str,
    provider_ids: list[str],
    reason_codes: list[str],
) -> str:
    """Generate a deterministic UUID v5 external intelligence plan ID."""
    return _uuid5(
        EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN_NAMESPACE,
        {
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "run_id": run_id,
            "package_id": package_id,
            "provider_ids": sorted(provider_ids),
            "reason_codes": sorted(reason_codes),
        },
    )


def deterministic_external_intelligence_provider_check_id(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    package_id: str,
    provider_id: str,
    check_type: str,
) -> str:
    """Generate a deterministic UUID v5 provider/check plan ID."""
    return _uuid5(
        EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN_NAMESPACE,
        {
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "run_id": run_id,
            "package_id": package_id,
            "provider_id": provider_id,
            "check_type": check_type,
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
