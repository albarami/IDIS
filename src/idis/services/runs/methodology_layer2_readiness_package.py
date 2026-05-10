"""Slice 14 in-memory Layer 2 readiness package service."""

from __future__ import annotations

from idis.models.external_intelligence_conflict_check_plan_materialization import (
    RunScopedExternalIntelligenceConflictCheckPlanRecord,
    RunScopedExternalIntelligenceConflictCheckPlanShell,
)
from idis.models.layer2_readiness_package_materialization import (
    MethodologyLayer2ReadinessPackageConstructionStatus,
    MethodologyLayer2ReadinessPackageReason,
    MethodologyLayer2ReadinessPackageRejection,
    MethodologyLayer2ReadinessPackageRunResult,
    MethodologyLayer2ReadinessStatus,
    RunScopedLayer2ReadinessBlocker,
    RunScopedLayer2ReadinessPackageRecord,
    RunScopedLayer2ReadinessPackageSummary,
    deterministic_layer2_readiness_package_id,
)
from idis.models.validated_evidence_package_materialization import (
    RunScopedValidatedEvidencePackageRecord,
    RunScopedValidatedEvidencePackageShell,
)


class InMemoryRunMethodologyLayer2ReadinessPackageService:
    """Build run-scoped Layer 2 readiness packages from safe upstream artifacts."""

    def run(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        validated_evidence_packages: list[
            RunScopedValidatedEvidencePackageRecord | RunScopedValidatedEvidencePackageShell
        ],
        external_intelligence_conflict_check_plans: list[
            RunScopedExternalIntelligenceConflictCheckPlanRecord
            | RunScopedExternalIntelligenceConflictCheckPlanShell
        ],
        company_identity_ids: list[str] | None = None,
        enrichment_fact_ids: list[str] | None = None,
    ) -> tuple[
        MethodologyLayer2ReadinessPackageRunResult,
        list[RunScopedLayer2ReadinessPackageRecord],
    ]:
        """Run readiness package construction from safe IDs/counts/reason codes only."""
        early_rejection = self._early_rejection(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            validated_evidence_packages=validated_evidence_packages,
            external_intelligence_conflict_check_plans=external_intelligence_conflict_check_plans,
        )
        if early_rejection is not None:
            return self._failed_result(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                rejection=early_rejection,
            ), []

        package = self._build_package(
            validated_evidence_packages[0],
            external_intelligence_conflict_check_plans[0],
            company_identity_ids=company_identity_ids or [],
            enrichment_fact_ids=enrichment_fact_ids or [],
        )
        result = MethodologyLayer2ReadinessPackageRunResult(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            construction_status=package.construction_status,
            readiness_status=package.readiness_status,
            package_shells=[package.to_shell()],
            rejections=[],
            summary=package.to_summary(),
        )
        return result, [package]

    def _early_rejection(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        validated_evidence_packages: list[
            RunScopedValidatedEvidencePackageRecord | RunScopedValidatedEvidencePackageShell
        ],
        external_intelligence_conflict_check_plans: list[
            RunScopedExternalIntelligenceConflictCheckPlanRecord
            | RunScopedExternalIntelligenceConflictCheckPlanShell
        ],
    ) -> MethodologyLayer2ReadinessPackageRejection | None:
        if not validated_evidence_packages:
            return _rejection(
                MethodologyLayer2ReadinessPackageReason.MISSING_VALIDATED_EVIDENCE_PACKAGE,
                "Layer 2 readiness package requires a VEP record or shell",
            )
        if len(validated_evidence_packages) > 1:
            return _rejection(
                MethodologyLayer2ReadinessPackageReason.DUPLICATE_INPUT,
                "Layer 2 readiness package accepts one VEP input",
            )
        if not external_intelligence_conflict_check_plans:
            return _rejection(
                MethodologyLayer2ReadinessPackageReason.MISSING_EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN,
                "Layer 2 readiness package requires a Slice 13 plan record or shell",
            )
        if len(external_intelligence_conflict_check_plans) > 1:
            return _rejection(
                MethodologyLayer2ReadinessPackageReason.DUPLICATE_INPUT,
                "Layer 2 readiness package accepts one external intelligence plan input",
            )

        package = validated_evidence_packages[0]
        plan = external_intelligence_conflict_check_plans[0]
        if _scope_mismatch(package, tenant_id=tenant_id, deal_id=deal_id, run_id=run_id):
            return _rejection(
                MethodologyLayer2ReadinessPackageReason.TENANT_OR_RUN_MISMATCH,
                "Layer 2 readiness package VEP scope mismatch",
                source_artifact_id=package.package_id,
            )
        if _scope_mismatch(plan, tenant_id=tenant_id, deal_id=deal_id, run_id=run_id):
            return _rejection(
                MethodologyLayer2ReadinessPackageReason.TENANT_OR_RUN_MISMATCH,
                "Layer 2 readiness package external intelligence plan scope mismatch",
                source_artifact_id=plan.plan_id,
            )
        if plan.package_id != package.package_id:
            return _rejection(
                MethodologyLayer2ReadinessPackageReason.TENANT_OR_RUN_MISMATCH,
                "Layer 2 readiness package upstream package ID mismatch",
                source_artifact_id=plan.plan_id,
            )
        return None

    def _build_package(
        self,
        vep: RunScopedValidatedEvidencePackageRecord | RunScopedValidatedEvidencePackageShell,
        external_plan: RunScopedExternalIntelligenceConflictCheckPlanRecord
        | RunScopedExternalIntelligenceConflictCheckPlanShell,
        *,
        company_identity_ids: list[str],
        enrichment_fact_ids: list[str],
    ) -> RunScopedLayer2ReadinessPackageRecord:
        claim_ids = _claim_ids(vep)
        calc_ids = list(vep.calc_ids)
        provider_check_ids = _provider_check_ids(external_plan)
        check_statuses = _check_statuses(external_plan)
        executed_provider_check_ids: list[str] = []

        reasons: list[MethodologyLayer2ReadinessPackageReason] = [
            MethodologyLayer2ReadinessPackageReason.LAYER2_EXECUTION_DEFERRED
        ]
        blockers: list[RunScopedLayer2ReadinessBlocker] = []

        if not claim_ids:
            _add_blocker(
                blockers,
                reasons,
                reason=MethodologyLayer2ReadinessPackageReason.MISSING_CLAIM_REFS,
                severity="blocking",
                source_artifact_type="validated_evidence_package",
                source_artifact_id=vep.package_id,
            )
        if not calc_ids:
            _add_blocker(
                blockers,
                reasons,
                reason=MethodologyLayer2ReadinessPackageReason.MISSING_CALC_REFS,
                severity="blocking",
                source_artifact_type="validated_evidence_package",
                source_artifact_id=vep.package_id,
            )
        if not company_identity_ids:
            _add_blocker(
                blockers,
                reasons,
                reason=MethodologyLayer2ReadinessPackageReason.MISSING_COMPANY_IDENTITY,
                severity="blocking",
                source_artifact_type="validated_evidence_package",
                source_artifact_id=vep.package_id,
            )
        if not enrichment_fact_ids:
            _add_blocker(
                blockers,
                reasons,
                reason=MethodologyLayer2ReadinessPackageReason.MISSING_ENRICHMENT_FACTS,
                severity="deferred",
                source_artifact_type="external_intelligence_conflict_check_plan",
                source_artifact_id=external_plan.plan_id,
            )
        if not executed_provider_check_ids:
            _add_blocker(
                blockers,
                reasons,
                reason=MethodologyLayer2ReadinessPackageReason.NO_EXECUTED_PROVIDER_CHECKS,
                severity="deferred",
                source_artifact_type="external_intelligence_conflict_check_plan",
                source_artifact_id=external_plan.plan_id,
            )
        if any(
            status in {"planned", "blocked", "deferred", "unavailable", "no_op"}
            for status in check_statuses
        ):
            _add_blocker(
                blockers,
                reasons,
                reason=MethodologyLayer2ReadinessPackageReason.EXTERNAL_INTELLIGENCE_CHECKS_PLANNED_NOT_EXECUTED,
                severity="deferred",
                source_artifact_type="external_intelligence_conflict_check_plan",
                source_artifact_id=external_plan.plan_id,
            )

        readiness_status = _readiness_status(reasons=reasons, blockers=blockers)
        readiness_package_id = deterministic_layer2_readiness_package_id(
            tenant_id=vep.tenant_id,
            deal_id=vep.deal_id,
            run_id=vep.run_id,
            vep_package_id=vep.package_id,
            external_intelligence_plan_id=external_plan.plan_id,
            claim_ids=claim_ids,
            calc_ids=calc_ids,
            reason_codes=[reason.value for reason in reasons],
        )

        return RunScopedLayer2ReadinessPackageRecord(
            tenant_id=vep.tenant_id,
            deal_id=vep.deal_id,
            run_id=vep.run_id,
            readiness_package_id=readiness_package_id,
            source_vep_package_id=vep.package_id,
            source_external_intelligence_plan_id=external_plan.plan_id,
            claim_ids=claim_ids,
            calc_ids=calc_ids,
            provider_check_ids=provider_check_ids,
            executed_provider_check_ids=executed_provider_check_ids,
            company_identity_ids=company_identity_ids,
            enrichment_fact_ids=enrichment_fact_ids,
            construction_status=MethodologyLayer2ReadinessPackageConstructionStatus.COMPLETED,
            readiness_status=readiness_status,
            reason_codes=[reason.value for reason in reasons],
            blockers=blockers,
        )

    def _failed_result(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        rejection: MethodologyLayer2ReadinessPackageRejection,
    ) -> MethodologyLayer2ReadinessPackageRunResult:
        return MethodologyLayer2ReadinessPackageRunResult(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            construction_status=MethodologyLayer2ReadinessPackageConstructionStatus.FAILED,
            readiness_status=MethodologyLayer2ReadinessStatus.BLOCKED,
            package_shells=[],
            rejections=[rejection],
            summary=RunScopedLayer2ReadinessPackageSummary(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                package_count=0,
                claim_count=0,
                calc_count=0,
                provider_check_count=0,
                executed_provider_check_count=0,
                blocker_count=0,
                construction_status=MethodologyLayer2ReadinessPackageConstructionStatus.FAILED,
                readiness_status=MethodologyLayer2ReadinessStatus.BLOCKED,
                by_reason={rejection.reason.value: 1},
                by_blocker_severity={},
            ),
        )


def _claim_ids(
    vep: RunScopedValidatedEvidencePackageRecord | RunScopedValidatedEvidencePackageShell,
) -> list[str]:
    return sorted(
        {claim_id for claim_ids in vep.claim_ids_by_disposition.values() for claim_id in claim_ids}
    )


def _provider_check_ids(
    plan: RunScopedExternalIntelligenceConflictCheckPlanRecord
    | RunScopedExternalIntelligenceConflictCheckPlanShell,
) -> list[str]:
    if isinstance(plan, RunScopedExternalIntelligenceConflictCheckPlanShell):
        return list(plan.provider_check_ids)
    return sorted(check.check_id for check in plan.checks)


def _check_statuses(
    plan: RunScopedExternalIntelligenceConflictCheckPlanRecord
    | RunScopedExternalIntelligenceConflictCheckPlanShell,
) -> list[str]:
    if isinstance(plan, RunScopedExternalIntelligenceConflictCheckPlanShell):
        return list(plan.check_statuses)
    return sorted(check.status.value for check in plan.checks)


def _readiness_status(
    *,
    reasons: list[MethodologyLayer2ReadinessPackageReason],
    blockers: list[RunScopedLayer2ReadinessBlocker],
) -> MethodologyLayer2ReadinessStatus:
    if any(blocker.severity == "blocking" for blocker in blockers):
        return MethodologyLayer2ReadinessStatus.BLOCKED
    if reasons:
        return MethodologyLayer2ReadinessStatus.DEFERRED
    return MethodologyLayer2ReadinessStatus.READY


def _add_blocker(
    blockers: list[RunScopedLayer2ReadinessBlocker],
    reasons: list[MethodologyLayer2ReadinessPackageReason],
    *,
    reason: MethodologyLayer2ReadinessPackageReason,
    severity: str,
    source_artifact_type: str,
    source_artifact_id: str,
) -> None:
    if reason not in reasons:
        reasons.append(reason)
    blocker_id = f"{source_artifact_type}-{reason.value}"
    blockers.append(
        RunScopedLayer2ReadinessBlocker(
            blocker_id=blocker_id,
            reason=reason,
            severity=severity,
            source_artifact_type=source_artifact_type,
            source_artifact_id=source_artifact_id,
        )
    )


def _scope_mismatch(
    item: (
        RunScopedValidatedEvidencePackageRecord
        | RunScopedValidatedEvidencePackageShell
        | RunScopedExternalIntelligenceConflictCheckPlanRecord
        | RunScopedExternalIntelligenceConflictCheckPlanShell
    ),
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
) -> bool:
    return item.tenant_id != tenant_id or item.deal_id != deal_id or item.run_id != run_id


def _rejection(
    reason: MethodologyLayer2ReadinessPackageReason,
    message: str,
    *,
    source_artifact_id: str | None = None,
) -> MethodologyLayer2ReadinessPackageRejection:
    return MethodologyLayer2ReadinessPackageRejection(
        source_artifact_id=source_artifact_id,
        reason=reason,
        reason_codes=[reason.value],
        message=message,
    )
