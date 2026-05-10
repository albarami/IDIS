"""Tests for Slice 15 company identity package service."""

from __future__ import annotations

from idis.models.company_identity_package_materialization import (
    MethodologyCompanyIdentityPackageConstructionStatus,
    MethodologyCompanyIdentityPackageReason,
    MethodologyCompanyIdentityStatus,
)
from idis.services.runs.methodology_company_identity_package import (
    InMemoryRunMethodologyCompanyIdentityPackageService,
)
from tests.test_run_methodology_deterministic_calculation_service import (
    DEAL_ID,
    RUN_ID,
    TENANT_ID,
)


def test_explicit_deal_company_name_creates_safe_deterministic_identity_package() -> None:
    service = InMemoryRunMethodologyCompanyIdentityPackageService()

    result, packages = service.run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        deal_metadata=_deal_metadata(company_name="Acme Corp"),
    )
    repeated_result, repeated_packages = service.run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        deal_metadata=_deal_metadata(company_name=" Acme Corp "),
    )

    package = packages[0]
    summary = result.to_run_step_summary()

    assert result.construction_status == (
        MethodologyCompanyIdentityPackageConstructionStatus.COMPLETED
    )
    assert result.identity_status == MethodologyCompanyIdentityStatus.MAPPED
    assert package.identity_status == MethodologyCompanyIdentityStatus.MAPPED
    assert len(package.company_identity_ids) == 1
    assert package.company_identity_ids == repeated_packages[0].company_identity_ids
    assert result.package_shells[0].company_identity_ids == package.company_identity_ids
    assert repeated_result.package_shells[0].company_identity_ids == package.company_identity_ids
    assert MethodologyCompanyIdentityPackageReason.EXPLICIT_DEAL_COMPANY_LABEL.value in (
        package.reason_codes
    )
    assert summary["company_identity_ids"] == package.company_identity_ids
    assert "company identity input boundary" in str(summary)
    assert "Acme Corp" not in str(summary)
    assert "company_name" not in str(summary)


def test_missing_or_blank_deal_metadata_blocks_identity_package() -> None:
    service = InMemoryRunMethodologyCompanyIdentityPackageService()

    missing_result, missing_packages = service.run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        deal_metadata=None,
    )
    blank_result, blank_packages = service.run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        deal_metadata=_deal_metadata(company_name="   "),
    )

    assert missing_packages == []
    assert missing_result.construction_status == (
        MethodologyCompanyIdentityPackageConstructionStatus.FAILED
    )
    assert missing_result.identity_status == MethodologyCompanyIdentityStatus.BLOCKED
    assert missing_result.rejections[0].reason == (
        MethodologyCompanyIdentityPackageReason.MISSING_DEAL_METADATA
    )
    assert blank_packages == []
    assert blank_result.construction_status == (
        MethodologyCompanyIdentityPackageConstructionStatus.FAILED
    )
    assert blank_result.identity_status == MethodologyCompanyIdentityStatus.BLOCKED
    assert blank_result.rejections[0].reason == (
        MethodologyCompanyIdentityPackageReason.BLANK_COMPANY_LABEL
    )


def test_cross_scope_deal_metadata_fails_closed() -> None:
    result, packages = InMemoryRunMethodologyCompanyIdentityPackageService().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        deal_metadata={
            "tenant_id": "tenant-other",
            "deal_id": DEAL_ID,
            "company_name": "Acme Corp",
        },
    )

    assert packages == []
    assert result.construction_status == MethodologyCompanyIdentityPackageConstructionStatus.FAILED
    assert result.identity_status == MethodologyCompanyIdentityStatus.BLOCKED
    assert result.rejections[0].reason == (
        MethodologyCompanyIdentityPackageReason.TENANT_OR_DEAL_MISMATCH
    )


def test_service_source_has_no_enrichment_or_layer2_execution_calls() -> None:
    import inspect

    import idis.services.runs.methodology_company_identity_package as service_module

    service_source = inspect.getsource(service_module)

    assert "EnrichmentService" not in service_source
    assert ".enrich(" not in service_source
    assert ".fetch(" not in service_source
    assert "_run_full_enrichment" not in service_source
    assert "DebateOrchestrator" not in service_source
    assert "AnalysisEngine" not in service_source
    assert "ScoringEngine" not in service_source
    assert "DeliverablesGenerator" not in service_source


def _deal_metadata(*, company_name: str) -> dict[str, object]:
    return {
        "tenant_id": TENANT_ID,
        "deal_id": DEAL_ID,
        "company_name": company_name,
        "name": "Project Alpha",
    }
