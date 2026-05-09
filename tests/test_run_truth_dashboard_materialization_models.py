"""Tests for Slice 10 run-scoped Truth Dashboard models."""

from __future__ import annotations

import json
from uuid import UUID

from idis.deliverables.truth_dashboard import TruthDashboardBuilder
from idis.models.deliverables import TruthDashboard
from idis.models.sanad import SanadGrade
from idis.models.truth_dashboard_materialization import (
    MethodologyTruthDashboardMapping,
    MethodologyTruthDashboardRunResult,
    MethodologyTruthDashboardStatus,
    MethodologyTruthDashboardSummary,
    RunScopedTruthDashboardRecord,
    TruthDashboardVerdict,
    deterministic_truth_dashboard_id,
    deterministic_truth_dashboard_row_id,
)

TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEAL_ID = "22222222-2222-2222-2222-222222222222"
RUN_ID = "33333333-3333-3333-3333-333333333333"


def _dashboard(dashboard_id: str) -> TruthDashboard:
    builder = TruthDashboardBuilder(
        deliverable_id=dashboard_id,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        deal_name=DEAL_ID,
        generated_at="1970-01-01T00:00:00+00:00",
    )
    builder.add_row(
        dimension="FINANCIAL_METRIC",
        assertion="revenue: 1000 USD",
        verdict=TruthDashboardVerdict.CONFIRMED.value,
        claim_refs=["claim_mth_revenue"],
        calc_refs=[],
        sanad_grade=SanadGrade.A.value,
        confidence=0.99,
    )
    return builder.build()


def _mapping(dashboard_id: str) -> MethodologyTruthDashboardMapping:
    return MethodologyTruthDashboardMapping(
        dashboard_id=dashboard_id,
        row_id=deterministic_truth_dashboard_row_id(
            dashboard_id=dashboard_id,
            claim_id="claim_mth_revenue",
            sanad_id="sanad-revenue",
            evidence_ids=["evidence-revenue"],
            calc_ids=[],
        ),
        claim_id="claim_mth_revenue",
        evidence_ids=["evidence-revenue"],
        sanad_id="sanad-revenue",
        calc_ids=[],
        defect_ids=[],
        sanad_grade=SanadGrade.A,
        verdict=TruthDashboardVerdict.CONFIRMED,
        methodology_question_id="mq_unit_economics",
        coverage_record_id="mcr_unit_economics",
        extraction_task_id="et_unit_economics",
        extraction_output_id="meo_revenue",
        status="created",
    )


def test_deterministic_truth_dashboard_ids_are_uuid_v5_and_stable() -> None:
    dashboard_id = deterministic_truth_dashboard_id(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        claim_ids=["claim_mth_revenue"],
    )
    repeated = deterministic_truth_dashboard_id(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        claim_ids=["claim_mth_revenue"],
    )
    changed = deterministic_truth_dashboard_id(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        claim_ids=["claim_mth_revenue", "claim_mth_cogs"],
    )
    row_id = deterministic_truth_dashboard_row_id(
        dashboard_id=dashboard_id,
        claim_id="claim_mth_revenue",
        sanad_id="sanad-revenue",
        evidence_ids=["evidence-revenue"],
        calc_ids=[],
    )

    assert dashboard_id == repeated
    assert dashboard_id != changed
    assert UUID(dashboard_id).version == 5
    assert UUID(row_id).version == 5


def test_run_scoped_truth_dashboard_record_reuses_deliverable_model_and_safe_shell() -> None:
    dashboard_id = deterministic_truth_dashboard_id(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        claim_ids=["claim_mth_revenue"],
    )
    mapping = _mapping(dashboard_id)
    record = RunScopedTruthDashboardRecord(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        dashboard_id=dashboard_id,
        dashboard=_dashboard(dashboard_id),
        row_mappings=[mapping],
        status="created",
    )

    shell = record.to_shell()

    assert isinstance(record.dashboard, TruthDashboard)
    assert shell.dashboard_id == dashboard_id
    assert shell.row_count == 1
    assert shell.claim_ids == ["claim_mth_revenue"]
    assert shell.evidence_ids == ["evidence-revenue"]
    assert "rows" not in shell.model_dump(mode="json")
    assert "assertion" not in json.dumps(shell.model_dump(mode="json"))


def test_run_step_summary_contains_safe_ids_counts_and_no_dashboard_payloads() -> None:
    dashboard_id = deterministic_truth_dashboard_id(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        claim_ids=["claim_mth_revenue"],
    )
    mapping = _mapping(dashboard_id)
    run_result = MethodologyTruthDashboardRunResult(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        status=MethodologyTruthDashboardStatus.COMPLETED,
        dashboard_mappings=[mapping],
        dashboard_shells=[],
        rejections=[],
        summary=MethodologyTruthDashboardSummary(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            total_claims=1,
            created_row_count=1,
            rejected_count=0,
            by_status={"created": 1},
            by_reason={},
            by_verdict={"CONFIRMED": 1},
            by_grade={"A": 1},
        ),
    )

    summary = run_result.to_run_step_summary()
    serialized = json.dumps(summary, sort_keys=True)

    assert dashboard_id in serialized
    assert "claim_mth_revenue" in serialized
    assert "evidence-revenue" in serialized
    assert "sanad-revenue" in serialized
    assert "dashboard_mappings" not in summary
    assert "revenue: 1000 USD" not in serialized
    assert "claim_text" not in serialized
    assert "value_struct" not in serialized
    assert "locator" not in serialized
    assert "document_name" not in serialized
    assert "C:/secret" not in serialized
    assert "grade_explanation" not in serialized
    assert "rows" not in serialized
