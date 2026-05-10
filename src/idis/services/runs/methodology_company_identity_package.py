"""Slice 15 in-memory company identity package service."""

from __future__ import annotations

from typing import Any

from idis.models.company_identity_package_materialization import (
    MethodologyCompanyIdentityPackageConstructionStatus,
    MethodologyCompanyIdentityPackageReason,
    MethodologyCompanyIdentityPackageRejection,
    MethodologyCompanyIdentityPackageRunResult,
    MethodologyCompanyIdentityStatus,
    RunScopedCompanyIdentityPackageRecord,
    RunScopedCompanyIdentityPackageSummary,
    deterministic_company_identity_id,
    deterministic_company_identity_package_id,
)


class InMemoryRunMethodologyCompanyIdentityPackageService:
    """Build run-scoped company identity packages from explicit deal metadata."""

    def run(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        deal_metadata: dict[str, Any] | None,
    ) -> tuple[
        MethodologyCompanyIdentityPackageRunResult,
        list[RunScopedCompanyIdentityPackageRecord],
    ]:
        """Run identity package construction from explicit deal metadata only."""
        early_rejection = self._early_rejection(
            tenant_id=tenant_id,
            deal_id=deal_id,
            deal_metadata=deal_metadata,
        )
        if early_rejection is not None:
            return self._failed_result(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                rejection=early_rejection,
            ), []

        assert deal_metadata is not None
        package = self._build_package(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            company_label=str(deal_metadata.get("company_name") or ""),
        )
        result = MethodologyCompanyIdentityPackageRunResult(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            construction_status=package.construction_status,
            identity_status=package.identity_status,
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
        deal_metadata: dict[str, Any] | None,
    ) -> MethodologyCompanyIdentityPackageRejection | None:
        if deal_metadata is None:
            return _rejection(
                MethodologyCompanyIdentityPackageReason.MISSING_DEAL_METADATA,
                "Company identity package requires explicit deal metadata",
            )
        metadata_tenant_id = deal_metadata.get("tenant_id")
        if metadata_tenant_id is not None and str(metadata_tenant_id) != tenant_id:
            return _rejection(
                MethodologyCompanyIdentityPackageReason.TENANT_OR_DEAL_MISMATCH,
                "Company identity package deal metadata tenant mismatch",
                source_artifact_id=str(deal_metadata.get("deal_id") or deal_id),
            )
        metadata_deal_id = deal_metadata.get("deal_id")
        if metadata_deal_id is not None and str(metadata_deal_id) != deal_id:
            return _rejection(
                MethodologyCompanyIdentityPackageReason.TENANT_OR_DEAL_MISMATCH,
                "Company identity package deal metadata deal mismatch",
                source_artifact_id=str(metadata_deal_id),
            )
        if not str(deal_metadata.get("company_name") or "").strip():
            return _rejection(
                MethodologyCompanyIdentityPackageReason.BLANK_COMPANY_LABEL,
                "Company identity package requires a nonblank explicit deal company label",
                source_artifact_id=deal_id,
            )
        return None

    def _build_package(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        company_label: str,
    ) -> RunScopedCompanyIdentityPackageRecord:
        reason_codes = [MethodologyCompanyIdentityPackageReason.EXPLICIT_DEAL_COMPANY_LABEL.value]
        company_identity_id = deterministic_company_identity_id(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            source_deal_id=deal_id,
            company_name=company_label,
        )
        identity_package_id = deterministic_company_identity_package_id(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            company_identity_ids=[company_identity_id],
            reason_codes=reason_codes,
        )
        return RunScopedCompanyIdentityPackageRecord(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            identity_package_id=identity_package_id,
            source_deal_id=deal_id,
            company_identity_ids=[company_identity_id],
            construction_status=MethodologyCompanyIdentityPackageConstructionStatus.COMPLETED,
            identity_status=MethodologyCompanyIdentityStatus.MAPPED,
            reason_codes=reason_codes,
            blocker_ids=[],
            blockers=[],
            source_fields_present=["explicit_deal_company_label"],
            identifier_types_present=["explicit_company_label"],
        )

    def _failed_result(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        rejection: MethodologyCompanyIdentityPackageRejection,
    ) -> MethodologyCompanyIdentityPackageRunResult:
        return MethodologyCompanyIdentityPackageRunResult(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            construction_status=MethodologyCompanyIdentityPackageConstructionStatus.FAILED,
            identity_status=MethodologyCompanyIdentityStatus.BLOCKED,
            package_shells=[],
            rejections=[rejection],
            summary=RunScopedCompanyIdentityPackageSummary(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                package_count=0,
                company_identity_count=0,
                blocker_count=0,
                construction_status=MethodologyCompanyIdentityPackageConstructionStatus.FAILED,
                identity_status=MethodologyCompanyIdentityStatus.BLOCKED,
                by_reason={rejection.reason.value: 1},
                by_blocker_severity={},
            ),
        )


def _rejection(
    reason: MethodologyCompanyIdentityPackageReason,
    message: str,
    *,
    source_artifact_id: str | None = None,
) -> MethodologyCompanyIdentityPackageRejection:
    return MethodologyCompanyIdentityPackageRejection(
        source_artifact_id=source_artifact_id,
        reason=reason,
        reason_codes=[reason.value],
        message=message,
    )
