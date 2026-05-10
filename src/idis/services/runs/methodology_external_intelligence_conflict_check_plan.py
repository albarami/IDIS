"""Slice 13 in-memory external intelligence conflict-check plan service."""

from __future__ import annotations

from idis.models.external_intelligence_conflict_check_plan_materialization import (
    ExternalIntelligencePlanCheckStatus,
    MethodologyExternalIntelligenceConflictCheckPlanReason,
    MethodologyExternalIntelligenceConflictCheckPlanRejection,
    MethodologyExternalIntelligenceConflictCheckPlanRunResult,
    MethodologyExternalIntelligenceConflictCheckPlanStatus,
    RunScopedExternalIntelligenceConflictCheckPlanRecord,
    RunScopedExternalIntelligenceConflictCheckPlanSummary,
    RunScopedExternalIntelligenceProviderCheck,
    deterministic_external_intelligence_conflict_check_plan_id,
    deterministic_external_intelligence_provider_check_id,
)
from idis.models.validated_evidence_package_materialization import (
    RunScopedValidatedEvidencePackageRecord,
    RunScopedValidatedEvidencePackageShell,
)
from idis.services.enrichment.registry import EnrichmentProviderRegistry


class InMemoryRunMethodologyExternalIntelligenceConflictCheckPlanService:
    """Build run-scoped provider/check plans from safe VEP data and registry metadata."""

    def __init__(self, *, provider_registry: EnrichmentProviderRegistry | None = None) -> None:
        """Initialize the plan service with static provider metadata.

        Args:
            provider_registry: Optional provider registry. If omitted, the default registry is
                built without calling providers.
        """
        self._provider_registry = provider_registry or _default_provider_registry()

    def run(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        validated_evidence_packages: list[
            RunScopedValidatedEvidencePackageRecord | RunScopedValidatedEvidencePackageShell
        ],
    ) -> tuple[
        MethodologyExternalIntelligenceConflictCheckPlanRunResult,
        list[RunScopedExternalIntelligenceConflictCheckPlanRecord],
    ]:
        """Run plan construction and return safe run-scoped records."""
        early_rejection = self._early_rejection(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            validated_evidence_packages=validated_evidence_packages,
        )
        if early_rejection is not None:
            return self._failed_result(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                rejection=early_rejection,
            ), []

        package = validated_evidence_packages[0]
        plan = self._build_plan(package)
        result = MethodologyExternalIntelligenceConflictCheckPlanRunResult(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            status=MethodologyExternalIntelligenceConflictCheckPlanStatus.COMPLETED,
            plan_shells=[plan.to_shell()],
            rejections=[],
            summary=plan.to_summary(),
        )
        return result, [plan]

    def _early_rejection(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        validated_evidence_packages: list[
            RunScopedValidatedEvidencePackageRecord | RunScopedValidatedEvidencePackageShell
        ],
    ) -> MethodologyExternalIntelligenceConflictCheckPlanRejection | None:
        if not validated_evidence_packages:
            return _rejection(
                MethodologyExternalIntelligenceConflictCheckPlanReason.MISSING_VALIDATED_EVIDENCE_PACKAGE,
                "External intelligence conflict-check plan requires a VEP record or shell",
            )
        if len(validated_evidence_packages) > 1:
            return _rejection(
                MethodologyExternalIntelligenceConflictCheckPlanReason.DUPLICATE_VEP_INPUT,
                "External intelligence conflict-check plan accepts one VEP input",
            )
        package = validated_evidence_packages[0]
        if _scope_mismatch(
            package,
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
        ):
            return _rejection(
                MethodologyExternalIntelligenceConflictCheckPlanReason.TENANT_OR_RUN_MISMATCH,
                "External intelligence conflict-check plan VEP scope mismatch",
                package_id=package.package_id,
            )
        return None

    def _build_plan(
        self,
        package: RunScopedValidatedEvidencePackageRecord | RunScopedValidatedEvidencePackageShell,
    ) -> RunScopedExternalIntelligenceConflictCheckPlanRecord:
        checks = [
            _provider_check(
                tenant_id=package.tenant_id,
                deal_id=package.deal_id,
                run_id=package.run_id,
                package_id=package.package_id,
                provider_id=descriptor.provider_id,
                rights_class=descriptor.rights_class.value,
                requires_byol=descriptor.requires_byol,
            )
            for descriptor in sorted(
                self._provider_registry.list_providers(),
                key=lambda item: item.provider_id,
            )
        ]
        reason_codes = [reason for check in checks for reason in check.reason_codes]
        plan_id = deterministic_external_intelligence_conflict_check_plan_id(
            tenant_id=package.tenant_id,
            deal_id=package.deal_id,
            run_id=package.run_id,
            package_id=package.package_id,
            provider_ids=[check.provider_id for check in checks],
            reason_codes=reason_codes,
        )
        return RunScopedExternalIntelligenceConflictCheckPlanRecord(
            tenant_id=package.tenant_id,
            deal_id=package.deal_id,
            run_id=package.run_id,
            plan_id=plan_id,
            package_id=package.package_id,
            checks=checks,
            status=MethodologyExternalIntelligenceConflictCheckPlanStatus.COMPLETED,
        )

    def _failed_result(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        rejection: MethodologyExternalIntelligenceConflictCheckPlanRejection,
    ) -> MethodologyExternalIntelligenceConflictCheckPlanRunResult:
        return MethodologyExternalIntelligenceConflictCheckPlanRunResult(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            status=MethodologyExternalIntelligenceConflictCheckPlanStatus.FAILED,
            plan_shells=[],
            rejections=[rejection],
            summary=RunScopedExternalIntelligenceConflictCheckPlanSummary(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                plan_count=0,
                check_count=0,
                by_status={},
                by_provider={},
                by_rights_class={},
                by_reason={rejection.reason.value: 1},
            ),
        )


def _provider_check(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    package_id: str,
    provider_id: str,
    rights_class: str,
    requires_byol: bool,
) -> RunScopedExternalIntelligenceProviderCheck:
    check_type = "registry_metadata_review"
    if requires_byol:
        status = ExternalIntelligencePlanCheckStatus.BLOCKED
        reason_codes = [
            MethodologyExternalIntelligenceConflictCheckPlanReason.PROVIDER_REQUIRES_BYOL.value,
            MethodologyExternalIntelligenceConflictCheckPlanReason.LIVE_PROVIDER_CALLS_DEFERRED.value,
        ]
    else:
        status = ExternalIntelligencePlanCheckStatus.DEFERRED
        reason_codes = [
            MethodologyExternalIntelligenceConflictCheckPlanReason.MISSING_QUERY_IDENTIFIERS.value,
            MethodologyExternalIntelligenceConflictCheckPlanReason.LIVE_PROVIDER_CALLS_DEFERRED.value,
        ]
    return RunScopedExternalIntelligenceProviderCheck(
        check_id=deterministic_external_intelligence_provider_check_id(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            package_id=package_id,
            provider_id=provider_id,
            check_type=check_type,
        ),
        provider_id=provider_id,
        check_type=check_type,
        status=status,
        rights_class=rights_class,
        requires_byol=requires_byol,
        reason_codes=reason_codes,
    )


def _scope_mismatch(
    item: RunScopedValidatedEvidencePackageRecord | RunScopedValidatedEvidencePackageShell,
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
) -> bool:
    return item.tenant_id != tenant_id or item.deal_id != deal_id or item.run_id != run_id


def _rejection(
    reason: MethodologyExternalIntelligenceConflictCheckPlanReason,
    message: str,
    *,
    package_id: str | None = None,
) -> MethodologyExternalIntelligenceConflictCheckPlanRejection:
    return MethodologyExternalIntelligenceConflictCheckPlanRejection(
        package_id=package_id,
        reason=reason,
        reason_codes=[reason.value],
        message=message,
    )


def _default_provider_registry() -> EnrichmentProviderRegistry:
    from idis.services.enrichment.service import _build_default_registry

    return _build_default_registry()
