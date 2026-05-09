"""Tests for Slice 10 in-memory methodology Truth Dashboard service."""

from __future__ import annotations

import json

from idis.models.calc_materialization import RunScopedCalcSanadShell, RunScopedCalculationShell
from idis.models.calc_sanad import SanadGrade as CalcSanadGrade
from idis.models.claim_materialization import RunScopedMaterializedClaimShell
from idis.models.deterministic_calculation import CalcType
from idis.models.evidence_item import EvidenceItem, SourceGrade, VerificationStatus
from idis.models.evidence_item_materialization import (
    RunScopedEvidenceItemRecord,
    RunScopedEvidenceProvenanceRef,
)
from idis.models.sanad import SanadGrade
from idis.models.truth_dashboard_materialization import (
    MethodologyTruthDashboardReason,
    MethodologyTruthDashboardStatus,
    TruthDashboardVerdict,
)
from idis.services.runs.methodology_truth_dashboard import (
    InMemoryRunMethodologyTruthDashboardService,
)
from tests.test_run_methodology_deterministic_calculation_service import (
    DEAL_ID,
    RUN_ID,
    TENANT_ID,
    _claim,
    _grade,
    _sanad_record,
    _sanad_shell,
)


def _evidence_record(
    claim_id: str,
    *,
    evidence_id: str | None = None,
    source_span_id: str | None = None,
) -> RunScopedEvidenceItemRecord:
    span_id = source_span_id or f"span-{claim_id}"
    return RunScopedEvidenceItemRecord(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        claim_id=claim_id,
        evidence_item=EvidenceItem(
            evidence_id=evidence_id or f"evidence-{claim_id}",
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            source_span_id=span_id,
            source_system="methodology_source_span",
            verification_status=VerificationStatus.UNVERIFIED,
            source_grade=SourceGrade.D,
        ),
        source_ref=RunScopedEvidenceProvenanceRef(
            document_id="doc-001",
            source_span_id=span_id,
            locator={"cell_id": "B2"},
        ),
        methodology_question_id="mq_unit_economics",
        coverage_record_id="mcr_unit_economics",
        extraction_task_id="et_unit_economics",
        extraction_output_id=f"meo_{claim_id}",
        status="created",
    )


def _service() -> InMemoryRunMethodologyTruthDashboardService:
    return InMemoryRunMethodologyTruthDashboardService()


def test_a_or_b_evidence_backed_claim_is_confirmed_without_optional_calc() -> None:
    claim = _claim("claim_mth_revenue", "claim_mth_revenue", "1000")
    evidence = _evidence_record("claim_mth_revenue")
    result, dashboards = _service().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialized_claims=[claim],
        evidence_items=[evidence],
        source_provenance=[evidence.source_ref],
        sanads=[_sanad_record("claim_mth_revenue", grade=SanadGrade.A)],
        sanad_grades=[_grade("claim_mth_revenue", SanadGrade.A)],
        sanad_defects=[],
        calculations=[],
        calc_sanads=[],
    )

    summary_json = json.dumps(result.to_run_step_summary(), sort_keys=True)

    assert result.status == MethodologyTruthDashboardStatus.COMPLETED
    assert len(dashboards) == 1
    assert dashboards[0].dashboard.rows[0].verdict == TruthDashboardVerdict.CONFIRMED.value
    assert dashboards[0].dashboard.rows[0].calc_refs == []
    assert result.summary.by_verdict == {"CONFIRMED": 1}
    assert "claim_mth_revenue: 1000 USD" not in summary_json
    assert "claim_text" not in summary_json
    assert "value_struct" not in summary_json
    assert "locator" not in summary_json
    assert "grade_explanation" not in summary_json


def test_verdicts_follow_sanad_grade_evidence_linkage_and_defects() -> None:
    claims = [
        _claim("claim_mth_unverified", "claim_mth_unverified", "1000"),
        _claim("claim_mth_refuted", "claim_mth_refuted", "1000"),
    ]
    evidence_items = [
        _evidence_record("claim_mth_unverified"),
        _evidence_record("claim_mth_refuted"),
    ]
    result, dashboards = _service().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialized_claims=claims,
        evidence_items=evidence_items,
        source_provenance=[record.source_ref for record in evidence_items],
        sanads=[
            _sanad_record("claim_mth_unverified", grade=SanadGrade.C),
            _sanad_record("claim_mth_refuted", grade=SanadGrade.D),
        ],
        sanad_grades=[
            _grade("claim_mth_unverified", SanadGrade.C),
            _grade("claim_mth_refuted", SanadGrade.D),
        ],
        sanad_defects=[],
        calculations=[],
        calc_sanads=[],
    )

    verdicts = {row.claim_refs[0]: row.verdict for row in dashboards[0].dashboard.rows}

    assert result.status == MethodologyTruthDashboardStatus.COMPLETED
    assert verdicts == {
        "claim_mth_refuted": TruthDashboardVerdict.REFUTED.value,
        "claim_mth_unverified": TruthDashboardVerdict.UNVERIFIED.value,
    }


def test_missing_source_provenance_rejects_claim_without_dashboard_record() -> None:
    claim = _claim("claim_mth_revenue", "claim_mth_revenue", "1000")
    evidence = _evidence_record("claim_mth_revenue")
    result, dashboards = _service().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialized_claims=[claim],
        evidence_items=[evidence],
        source_provenance=[
            RunScopedEvidenceProvenanceRef(
                document_id="doc-001",
                source_span_id="span-other",
                locator={"cell_id": "C3"},
            )
        ],
        sanads=[_sanad_record("claim_mth_revenue", grade=SanadGrade.A)],
        sanad_grades=[_grade("claim_mth_revenue", SanadGrade.A)],
        sanad_defects=[],
        calculations=[],
        calc_sanads=[],
    )

    assert result.status == MethodologyTruthDashboardStatus.FAILED
    assert dashboards == []
    assert result.rejections[0].reason == MethodologyTruthDashboardReason.MISSING_SOURCE_PROVENANCE
    assert result.to_run_step_summary()["dashboard_ids"] == []


def test_cross_run_evidence_sanad_and_grade_inputs_fail_closed() -> None:
    claim = _claim("claim_mth_revenue", "claim_mth_revenue", "1000")
    other_run_id = "44444444-4444-4444-4444-444444444444"
    evidence = _evidence_record("claim_mth_revenue").model_copy(update={"run_id": other_run_id})
    result, dashboards = _service().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialized_claims=[claim],
        evidence_items=[evidence],
        source_provenance=[evidence.source_ref],
        sanads=[
            _sanad_record("claim_mth_revenue", grade=SanadGrade.A).model_copy(
                update={"run_id": other_run_id}
            )
        ],
        sanad_grades=[
            _grade("claim_mth_revenue", SanadGrade.A).model_copy(update={"run_id": other_run_id})
        ],
        sanad_defects=[],
        calculations=[],
        calc_sanads=[],
    )

    assert result.status == MethodologyTruthDashboardStatus.FAILED
    assert dashboards == []
    assert any(
        rejection.reason == MethodologyTruthDashboardReason.TENANT_OR_RUN_MISMATCH
        for rejection in result.rejections
    )
    assert result.to_run_step_summary()["dashboard_ids"] == []


def test_any_cross_run_input_is_fatal_even_when_valid_row_exists() -> None:
    claim = _claim("claim_mth_revenue", "claim_mth_revenue", "1000")
    valid_evidence = _evidence_record("claim_mth_revenue")
    cross_run_evidence = _evidence_record(
        "claim_mth_revenue",
        evidence_id="evidence-cross-run",
        source_span_id="span-cross-run",
    ).model_copy(update={"run_id": "44444444-4444-4444-4444-444444444444"})

    result, dashboards = _service().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialized_claims=[claim],
        evidence_items=[valid_evidence, cross_run_evidence],
        source_provenance=[valid_evidence.source_ref, cross_run_evidence.source_ref],
        sanads=[_sanad_record("claim_mth_revenue", grade=SanadGrade.A)],
        sanad_grades=[_grade("claim_mth_revenue", SanadGrade.A)],
        sanad_defects=[],
        calculations=[],
        calc_sanads=[],
    )

    assert result.status == MethodologyTruthDashboardStatus.FAILED
    assert dashboards == []
    assert result.to_run_step_summary()["dashboard_ids"] == []
    assert result.to_run_step_summary()["summary"]["created_row_count"] == 0
    assert result.to_run_step_summary()["summary"]["by_reason"] == {"tenant_or_run_mismatch": 1}


def test_cross_run_calc_sanad_input_is_fatal_even_when_valid_row_exists() -> None:
    claim = _claim("claim_mth_revenue", "claim_mth_revenue", "1000")
    evidence = _evidence_record("claim_mth_revenue")
    result, dashboards = _service().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialized_claims=[claim],
        evidence_items=[evidence],
        source_provenance=[evidence.source_ref],
        sanads=[_sanad_record("claim_mth_revenue", grade=SanadGrade.A)],
        sanad_grades=[_grade("claim_mth_revenue", SanadGrade.A)],
        sanad_defects=[],
        calculations=[],
        calc_sanads=[
            RunScopedCalcSanadShell(
                tenant_id=TENANT_ID,
                deal_id=DEAL_ID,
                run_id="44444444-4444-4444-4444-444444444444",
                calc_id="calc-revenue-quality",
                calc_sanad_id="calc-sanad-revenue-quality",
                input_claim_ids=["claim_mth_revenue"],
                input_min_sanad_grade=CalcSanadGrade.A,
                calc_grade=CalcSanadGrade.A,
                methodology_question_id="mq_unit_economics",
                extraction_task_id="et_unit_economics",
                coverage_record_id="mcr_unit_economics",
                status="created",
            )
        ],
    )

    assert result.status == MethodologyTruthDashboardStatus.FAILED
    assert dashboards == []
    assert result.to_run_step_summary()["dashboard_ids"] == []
    assert result.to_run_step_summary()["summary"]["created_row_count"] == 0


def test_calc_linkage_is_supplemental_and_is_recorded_when_present() -> None:
    claim = _claim("claim_mth_revenue", "claim_mth_revenue", "1000")
    evidence = _evidence_record("claim_mth_revenue")
    result, dashboards = _service().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialized_claims=[claim],
        evidence_items=[evidence],
        source_provenance=[evidence.source_ref],
        sanads=[_sanad_record("claim_mth_revenue", grade=SanadGrade.A)],
        sanad_grades=[_grade("claim_mth_revenue", SanadGrade.A)],
        sanad_defects=[],
        calculations=[
            RunScopedCalculationShell(
                tenant_id=TENANT_ID,
                deal_id=DEAL_ID,
                run_id=RUN_ID,
                calc_id="calc-revenue-quality",
                calc_type=CalcType.GROSS_MARGIN,
                input_claim_ids=["claim_mth_revenue"],
                input_sanad_ids=["sanad-claim_mth_revenue"],
                formula_hash="formula-hash",
                reproducibility_hash="repro-hash",
                output_primary_value="60.0000",
                output_unit="percent",
                output_currency=None,
                methodology_question_id="mq_unit_economics",
                extraction_task_id="et_unit_economics",
                coverage_record_id="mcr_unit_economics",
                status="created",
            )
        ],
        calc_sanads=[],
    )

    summary = result.to_run_step_summary()

    assert result.status == MethodologyTruthDashboardStatus.COMPLETED
    assert dashboards[0].dashboard.rows[0].verdict == TruthDashboardVerdict.CONFIRMED.value
    assert dashboards[0].dashboard.rows[0].calc_refs == ["calc-revenue-quality"]
    assert summary["calc_ids"] == ["calc-revenue-quality"]


def test_fatal_and_major_defects_override_a_b_grade_verdicts() -> None:
    claims = [
        _claim("claim_mth_fatal", "claim_mth_fatal", "1000"),
        _claim("claim_mth_major", "claim_mth_major", "1000"),
    ]
    evidence_items = [_evidence_record("claim_mth_fatal"), _evidence_record("claim_mth_major")]
    fatal_grade = _grade("claim_mth_fatal", SanadGrade.A).model_copy(
        update={"fatal_defect_count": 1, "defect_ids": ["defect-fatal"]}
    )
    major_grade = _grade("claim_mth_major", SanadGrade.A).model_copy(
        update={"major_defect_count": 1, "defect_ids": ["defect-major"]}
    )
    result, dashboards = _service().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialized_claims=claims,
        evidence_items=evidence_items,
        source_provenance=[record.source_ref for record in evidence_items],
        sanads=[_sanad_record("claim_mth_fatal"), _sanad_record("claim_mth_major")],
        sanad_grades=[fatal_grade, major_grade],
        sanad_defects=[],
        calculations=[],
        calc_sanads=[],
    )

    verdicts = {row.claim_refs[0]: row.verdict for row in dashboards[0].dashboard.rows}

    assert result.status == MethodologyTruthDashboardStatus.COMPLETED
    assert verdicts["claim_mth_fatal"] == TruthDashboardVerdict.REFUTED.value
    assert verdicts["claim_mth_major"] == TruthDashboardVerdict.DISPUTED.value


def test_shell_only_claims_do_not_fabricate_truth_assertions() -> None:
    claim = _claim("claim_mth_revenue", "claim_mth_revenue", "1000")
    evidence = _evidence_record("claim_mth_revenue")
    result, dashboards = _service().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialized_claims=[
            RunScopedMaterializedClaimShell(
                claim_id="claim_mth_revenue",
                tenant_id=TENANT_ID,
                deal_id=DEAL_ID,
                run_id=RUN_ID,
                source_refs=list(claim.source_refs),
                methodology_question_id=claim.methodology_question_id,
                coverage_record_id=claim.coverage_record_id,
                extraction_task_id=claim.extraction_task_id,
                extraction_output_id=claim.extraction_output_id,
                status=claim.status,
            )
        ],
        evidence_items=[evidence],
        source_provenance=[evidence.source_ref],
        sanads=[_sanad_shell("claim_mth_revenue", grade=SanadGrade.A)],
        sanad_grades=[_grade("claim_mth_revenue", SanadGrade.A)],
        sanad_defects=[],
        calculations=[],
        calc_sanads=[],
    )

    serialized = json.dumps(result.to_run_step_summary(), sort_keys=True)

    assert result.status == MethodologyTruthDashboardStatus.FAILED
    assert dashboards == []
    assert result.rejections[0].reason == MethodologyTruthDashboardReason.SHELL_ONLY_INPUT
    assert "claim_mth_revenue: 1000 USD" not in serialized
