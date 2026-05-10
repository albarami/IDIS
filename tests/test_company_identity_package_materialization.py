"""Tests for Slice 15 company identity package models."""

from __future__ import annotations

import json

from idis.models.company_identity_package_materialization import (
    MethodologyCompanyIdentityPackageConstructionStatus,
    MethodologyCompanyIdentityPackageReason,
    MethodologyCompanyIdentityStatus,
    RunScopedCompanyIdentityBlocker,
    RunScopedCompanyIdentityPackageRecord,
    RunScopedCompanyIdentityPackageSummary,
    deterministic_company_identity_id,
    deterministic_company_identity_package_id,
)
from tests.test_run_methodology_deterministic_calculation_service import (
    DEAL_ID,
    RUN_ID,
    TENANT_ID,
)


def test_deterministic_company_identity_ids_are_stable_for_explicit_company_name() -> None:
    first_identity_id = deterministic_company_identity_id(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        source_deal_id=DEAL_ID,
        company_name="  Acme Corp  ",
    )
    second_identity_id = deterministic_company_identity_id(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        source_deal_id=DEAL_ID,
        company_name="Acme Corp",
    )
    package_id = deterministic_company_identity_package_id(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        company_identity_ids=[second_identity_id, first_identity_id],
        reason_codes=[
            MethodologyCompanyIdentityPackageReason.EXPLICIT_DEAL_COMPANY_LABEL.value,
        ],
    )

    assert first_identity_id == second_identity_id
    assert package_id
    assert package_id != first_identity_id


def test_record_shell_and_summary_are_safe_and_do_not_expose_raw_company_name() -> None:
    record = _identity_record()

    shell = record.to_shell()
    run_summary = record.to_run_step_summary()
    serialized = json.dumps(run_summary, sort_keys=True)

    assert shell.identity_package_id == "identity-package-001"
    assert shell.company_identity_ids == ["company-identity-001"]
    assert shell.construction_status == (
        MethodologyCompanyIdentityPackageConstructionStatus.COMPLETED
    )
    assert shell.identity_status == MethodologyCompanyIdentityStatus.MAPPED
    assert run_summary["identity_package_ids"] == ["identity-package-001"]
    assert run_summary["company_identity_ids"] == ["company-identity-001"]
    assert run_summary["construction_status"] == "completed"
    assert run_summary["identity_status"] == "mapped"
    assert "company identity input boundary" in serialized
    assert "Acme Corp" not in serialized
    assert "company_name" not in serialized
    assert "normalized" not in serialized
    assert "raw" not in serialized
    assert "EnrichmentService" not in serialized
    assert "fetch" not in serialized
    assert "recommendation" not in serialized


def test_summary_counts_are_stable_and_sorted() -> None:
    summary = _identity_record().to_summary()

    assert summary == RunScopedCompanyIdentityPackageSummary(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        package_count=1,
        company_identity_count=1,
        blocker_count=0,
        construction_status=MethodologyCompanyIdentityPackageConstructionStatus.COMPLETED,
        identity_status=MethodologyCompanyIdentityStatus.MAPPED,
        by_reason={"explicit_deal_company_label": 1},
        by_blocker_severity={},
    )


def _identity_record() -> RunScopedCompanyIdentityPackageRecord:
    return RunScopedCompanyIdentityPackageRecord(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        identity_package_id="identity-package-001",
        source_deal_id=DEAL_ID,
        company_identity_ids=["company-identity-001"],
        construction_status=MethodologyCompanyIdentityPackageConstructionStatus.COMPLETED,
        identity_status=MethodologyCompanyIdentityStatus.MAPPED,
        reason_codes=[
            MethodologyCompanyIdentityPackageReason.EXPLICIT_DEAL_COMPANY_LABEL.value,
        ],
        blocker_ids=[],
        blockers=[],
        source_fields_present=["explicit_deal_company_label"],
        identifier_types_present=["explicit_company_label"],
    )


def _blocked_identity_record() -> RunScopedCompanyIdentityPackageRecord:
    return RunScopedCompanyIdentityPackageRecord(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        identity_package_id="identity-package-blocked-001",
        source_deal_id=DEAL_ID,
        company_identity_ids=[],
        construction_status=MethodologyCompanyIdentityPackageConstructionStatus.COMPLETED,
        identity_status=MethodologyCompanyIdentityStatus.BLOCKED,
        reason_codes=[MethodologyCompanyIdentityPackageReason.BLANK_COMPANY_NAME.value],
        blocker_ids=["company-identity-blank-company-name"],
        blockers=[
            RunScopedCompanyIdentityBlocker(
                blocker_id="company-identity-blank-company-name",
                reason=MethodologyCompanyIdentityPackageReason.BLANK_COMPANY_NAME,
                severity="blocking",
                source_artifact_type="deal_metadata",
                source_artifact_id=DEAL_ID,
            )
        ],
        source_fields_present=[],
        identifier_types_present=[],
    )
