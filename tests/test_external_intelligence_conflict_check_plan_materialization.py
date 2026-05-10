"""Tests for Slice 13 external intelligence conflict-check plan models."""

from __future__ import annotations

import json

from idis.models.external_intelligence_conflict_check_plan_materialization import (
    ExternalIntelligencePlanCheckStatus,
    MethodologyExternalIntelligenceConflictCheckPlanStatus,
    RunScopedExternalIntelligenceConflictCheckPlanRecord,
    RunScopedExternalIntelligenceConflictCheckPlanSummary,
    RunScopedExternalIntelligenceProviderCheck,
    deterministic_external_intelligence_conflict_check_plan_id,
)
from tests.test_run_methodology_deterministic_calculation_service import (
    DEAL_ID,
    RUN_ID,
    TENANT_ID,
)


def test_deterministic_plan_id_is_stable_under_provider_ordering() -> None:
    first_id = deterministic_external_intelligence_conflict_check_plan_id(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        package_id="package-001",
        provider_ids=["companies_house", "sec_edgar"],
        reason_codes=["missing_query_identifiers", "provider_requires_byol"],
    )
    second_id = deterministic_external_intelligence_conflict_check_plan_id(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        package_id="package-001",
        provider_ids=["sec_edgar", "companies_house"],
        reason_codes=["provider_requires_byol", "missing_query_identifiers"],
    )

    assert first_id == second_id


def test_record_shell_and_run_summary_are_plan_only_and_safe() -> None:
    record = _plan_record()

    shell = record.to_shell()
    run_summary = record.to_run_step_summary()
    serialized = json.dumps(run_summary, sort_keys=True)

    assert shell.plan_id == "external-intelligence-plan-001"
    assert shell.package_id == "package-001"
    assert shell.provider_check_ids == ["check-byol", "check-edgar"]
    assert shell.provider_ids == ["companies_house", "sec_edgar"]
    assert shell.check_statuses == ["blocked", "deferred"]
    assert run_summary["status"] == "completed"
    assert run_summary["plan_ids"] == ["external-intelligence-plan-001"]
    assert run_summary["provider_ids"] == ["companies_house", "sec_edgar"]
    assert run_summary["provider_check_ids"] == ["check-byol", "check-edgar"]
    assert run_summary["summary"]["check_count"] == 2
    assert run_summary["summary"]["by_status"] == {"blocked": 1, "deferred": 1}
    assert "plan boundary" in serialized
    assert "external conflict checks executed" not in serialized
    assert "normalized" not in serialized
    assert "raw" not in serialized
    assert "claim text" not in serialized
    assert "Document A" not in serialized
    assert "span text" not in serialized
    assert "recommendation" not in serialized
    assert "GO" not in serialized


def test_summary_counts_are_stable_and_sorted() -> None:
    record = _plan_record()

    summary = record.to_summary()

    assert summary == RunScopedExternalIntelligenceConflictCheckPlanSummary(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        plan_count=1,
        check_count=2,
        by_status={"blocked": 1, "deferred": 1},
        by_provider={"companies_house": 1, "sec_edgar": 1},
        by_rights_class={"GREEN": 2},
        by_reason={
            "live_provider_calls_deferred": 2,
            "missing_query_identifiers": 1,
            "provider_requires_byol": 1,
        },
    )
    assert summary.aggregate_status() == (
        MethodologyExternalIntelligenceConflictCheckPlanStatus.COMPLETED
    )


def _plan_record() -> RunScopedExternalIntelligenceConflictCheckPlanRecord:
    return RunScopedExternalIntelligenceConflictCheckPlanRecord(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        plan_id="external-intelligence-plan-001",
        package_id="package-001",
        checks=[
            RunScopedExternalIntelligenceProviderCheck(
                check_id="check-edgar",
                provider_id="sec_edgar",
                check_type="registry_metadata_review",
                status=ExternalIntelligencePlanCheckStatus.DEFERRED,
                rights_class="GREEN",
                requires_byol=False,
                reason_codes=["missing_query_identifiers", "live_provider_calls_deferred"],
            ),
            RunScopedExternalIntelligenceProviderCheck(
                check_id="check-byol",
                provider_id="companies_house",
                check_type="registry_metadata_review",
                status=ExternalIntelligencePlanCheckStatus.BLOCKED,
                rights_class="GREEN",
                requires_byol=True,
                reason_codes=["provider_requires_byol", "live_provider_calls_deferred"],
            ),
        ],
        status=MethodologyExternalIntelligenceConflictCheckPlanStatus.COMPLETED,
    )
