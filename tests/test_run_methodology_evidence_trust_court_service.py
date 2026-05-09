"""Tests for Slice 11 in-memory Layer 1 Evidence Trust Court service."""

from __future__ import annotations

import json

from idis.models.calc_materialization import RunScopedCalculationShell
from idis.models.deterministic_calculation import CalcType
from idis.models.evidence_item_materialization import RunScopedEvidenceProvenanceRef
from idis.models.evidence_trust_court_materialization import (
    EvidenceTrustDisposition,
    MethodologyEvidenceTrustCourtReason,
    MethodologyEvidenceTrustCourtStatus,
)
from idis.models.sanad import SanadGrade
from idis.models.truth_dashboard_materialization import (
    RunScopedTruthDashboardRecord,
    TruthDashboardVerdict,
)
from idis.services.runs.methodology_evidence_trust_court import (
    InMemoryRunMethodologyEvidenceTrustCourtService,
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
)
from tests.test_run_methodology_truth_dashboard_service import _evidence_record


def _service() -> InMemoryRunMethodologyEvidenceTrustCourtService:
    return InMemoryRunMethodologyEvidenceTrustCourtService()


def _calc_shell(claim_id: str = "claim_mth_revenue") -> RunScopedCalculationShell:
    return RunScopedCalculationShell(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        calc_id="calc-revenue-quality",
        calc_type=CalcType.GROSS_MARGIN,
        input_claim_ids=[claim_id],
        input_sanad_ids=[f"sanad-{claim_id}"],
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


def _truth_dashboard_record(
    claim_ids: list[str],
    *,
    grades: dict[str, SanadGrade] | None = None,
    calculations: list[RunScopedCalculationShell] | None = None,
) -> RunScopedTruthDashboardRecord:
    grade_by_claim = grades or dict.fromkeys(claim_ids, SanadGrade.A)
    evidence_items = [_evidence_record(claim_id) for claim_id in claim_ids]
    _, dashboards = InMemoryRunMethodologyTruthDashboardService().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialized_claims=[_claim(claim_id, claim_id, "1000") for claim_id in claim_ids],
        evidence_items=evidence_items,
        source_provenance=[record.source_ref for record in evidence_items],
        sanads=[_sanad_record(claim_id, grade=grade_by_claim[claim_id]) for claim_id in claim_ids],
        sanad_grades=[_grade(claim_id, grade_by_claim[claim_id]) for claim_id in claim_ids],
        sanad_defects=[],
        calculations=calculations or [],
        calc_sanads=[],
    )
    return dashboards[0]


def test_trusted_claim_with_calc_uses_muhasabah_aliases_and_stores_run_scoped_ids() -> None:
    claim = _claim("claim_mth_revenue", "claim_mth_revenue", "1000")
    evidence = _evidence_record("claim_mth_revenue")
    calculation = _calc_shell()
    dashboard = _truth_dashboard_record(["claim_mth_revenue"], calculations=[calculation])

    result, courts = _service().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialized_claims=[claim],
        evidence_items=[evidence],
        source_provenance=[evidence.source_ref],
        sanads=[_sanad_record("claim_mth_revenue", grade=SanadGrade.A)],
        sanad_grades=[_grade("claim_mth_revenue", SanadGrade.A)],
        sanad_defects=[],
        calculations=[calculation],
        calc_sanads=[],
        truth_dashboards=[dashboard],
    )

    summary_json = json.dumps(result.to_run_step_summary(), sort_keys=True)

    assert result.status == MethodologyEvidenceTrustCourtStatus.COMPLETED
    assert len(courts) == 1
    assert courts[0].claim_assessments[0].disposition == EvidenceTrustDisposition.TRUSTED
    assert courts[0].claim_assessments[0].calc_ids == ["calc-revenue-quality"]
    assert courts[0].role_summaries
    assert all(
        role_summary.supported_claim_ids == ["claim_mth_revenue"]
        for role_summary in courts[0].role_summaries
    )
    assert any(
        role_summary.supported_calc_ids == ["calc-revenue-quality"]
        for role_summary in courts[0].role_summaries
    )
    assert "claim_mth_revenue" in summary_json
    assert "calc-revenue-quality" in summary_json
    assert "claim_mth_revenue: 1000 USD" not in summary_json
    assert "content" not in summary_json
    assert "alias" not in summary_json
    assert "recommendation" not in summary_json
    assert "GO" not in summary_json


def test_dispositions_follow_grade_defects_and_dashboard_verdict() -> None:
    claims = [
        _claim("claim_mth_unverified", "claim_mth_unverified", "1000"),
        _claim("claim_mth_rejected", "claim_mth_rejected", "1000"),
        _claim("claim_mth_disputed", "claim_mth_disputed", "1000"),
    ]
    evidence_items = [
        _evidence_record("claim_mth_unverified"),
        _evidence_record("claim_mth_rejected"),
        _evidence_record("claim_mth_disputed"),
    ]
    grades = {
        "claim_mth_unverified": SanadGrade.C,
        "claim_mth_rejected": SanadGrade.D,
        "claim_mth_disputed": SanadGrade.A,
    }
    major_grade = _grade("claim_mth_disputed", SanadGrade.A).model_copy(
        update={"major_defect_count": 1, "defect_ids": ["defect-major"]}
    )
    dashboard = _truth_dashboard_record(list(grades), grades=grades)

    result, courts = _service().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialized_claims=claims,
        evidence_items=evidence_items,
        source_provenance=[record.source_ref for record in evidence_items],
        sanads=[_sanad_record(claim_id, grade=grade) for claim_id, grade in grades.items()],
        sanad_grades=[
            _grade("claim_mth_unverified", SanadGrade.C),
            _grade("claim_mth_rejected", SanadGrade.D),
            major_grade,
        ],
        sanad_defects=[],
        calculations=[],
        calc_sanads=[],
        truth_dashboards=[dashboard],
    )

    dispositions = {
        assessment.claim_id: assessment.disposition for assessment in courts[0].claim_assessments
    }

    assert result.status == MethodologyEvidenceTrustCourtStatus.COMPLETED
    assert dispositions == {
        "claim_mth_unverified": EvidenceTrustDisposition.UNVERIFIED,
        "claim_mth_rejected": EvidenceTrustDisposition.REJECTED,
        "claim_mth_disputed": EvidenceTrustDisposition.DISPUTED,
    }


def test_truth_dashboard_shell_only_fails_closed_without_court_record_or_shell() -> None:
    claim = _claim("claim_mth_revenue", "claim_mth_revenue", "1000")
    evidence = _evidence_record("claim_mth_revenue")
    dashboard_shell = _truth_dashboard_record(["claim_mth_revenue"]).to_shell()

    result, courts = _service().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialized_claims=[claim],
        evidence_items=[evidence],
        source_provenance=[evidence.source_ref],
        sanads=[_sanad_record("claim_mth_revenue")],
        sanad_grades=[_grade("claim_mth_revenue")],
        sanad_defects=[],
        calculations=[],
        calc_sanads=[],
        truth_dashboards=[dashboard_shell],
    )

    assert result.status == MethodologyEvidenceTrustCourtStatus.FAILED
    assert courts == []
    assert result.court_shells == []
    assert result.to_run_step_summary()["court_ids"] == []
    assert (
        result.rejections[0].reason
        == MethodologyEvidenceTrustCourtReason.TRUTH_DASHBOARD_SHELL_ONLY
    )


def test_missing_source_provenance_rejects_without_court_record() -> None:
    claim = _claim("claim_mth_revenue", "claim_mth_revenue", "1000")
    evidence = _evidence_record("claim_mth_revenue")
    dashboard = _truth_dashboard_record(["claim_mth_revenue"])

    result, courts = _service().run(
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
        sanads=[_sanad_record("claim_mth_revenue")],
        sanad_grades=[_grade("claim_mth_revenue")],
        sanad_defects=[],
        calculations=[],
        calc_sanads=[],
        truth_dashboards=[dashboard],
    )

    assert result.status == MethodologyEvidenceTrustCourtStatus.FAILED
    assert courts == []
    assert result.to_run_step_summary()["court_ids"] == []
    assert (
        result.rejections[0].reason == MethodologyEvidenceTrustCourtReason.MISSING_SOURCE_PROVENANCE
    )


def test_source_provenance_requires_matching_document_and_span() -> None:
    claim = _claim("claim_mth_revenue", "claim_mth_revenue", "1000")
    evidence = _evidence_record("claim_mth_revenue")
    dashboard = _truth_dashboard_record(["claim_mth_revenue"])

    result, courts = _service().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialized_claims=[claim],
        evidence_items=[evidence],
        source_provenance=[
            RunScopedEvidenceProvenanceRef(
                document_id="doc-other",
                source_span_id=evidence.source_ref.source_span_id,
                locator={"cell_id": "B2"},
            )
        ],
        sanads=[_sanad_record("claim_mth_revenue")],
        sanad_grades=[_grade("claim_mth_revenue")],
        sanad_defects=[],
        calculations=[],
        calc_sanads=[],
        truth_dashboards=[dashboard],
    )

    assert result.status == MethodologyEvidenceTrustCourtStatus.FAILED
    assert courts == []
    assert (
        result.rejections[0].reason == MethodologyEvidenceTrustCourtReason.MISSING_SOURCE_PROVENANCE
    )


def test_no_evidence_items_for_claimed_row_never_produces_trusted() -> None:
    claim = _claim("claim_mth_revenue", "claim_mth_revenue", "1000")
    dashboard = _truth_dashboard_record(["claim_mth_revenue"])

    result, courts = _service().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialized_claims=[claim],
        evidence_items=[],
        source_provenance=[],
        sanads=[_sanad_record("claim_mth_revenue", grade=SanadGrade.A)],
        sanad_grades=[_grade("claim_mth_revenue", SanadGrade.A)],
        sanad_defects=[],
        calculations=[],
        calc_sanads=[],
        truth_dashboards=[dashboard],
    )

    assert result.status == MethodologyEvidenceTrustCourtStatus.COMPLETED
    assert courts[0].claim_assessments[0].disposition == EvidenceTrustDisposition.REJECTED
    assert "missing_evidence_linkage" in courts[0].claim_assessments[0].reason_codes
    assert courts[0].findings
    assert courts[0].findings[0].reason_codes == ["missing_evidence_linkage"]


def test_any_cross_scope_input_is_fatal_even_when_valid_claim_exists() -> None:
    claim = _claim("claim_mth_revenue", "claim_mth_revenue", "1000")
    valid_evidence = _evidence_record("claim_mth_revenue")
    cross_run_calc = _calc_shell().model_copy(
        update={"run_id": "44444444-4444-4444-4444-444444444444"}
    )
    dashboard = _truth_dashboard_record(["claim_mth_revenue"])

    result, courts = _service().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        materialized_claims=[claim],
        evidence_items=[valid_evidence],
        source_provenance=[valid_evidence.source_ref],
        sanads=[_sanad_record("claim_mth_revenue")],
        sanad_grades=[_grade("claim_mth_revenue")],
        sanad_defects=[],
        calculations=[cross_run_calc],
        calc_sanads=[],
        truth_dashboards=[dashboard],
    )

    assert result.status == MethodologyEvidenceTrustCourtStatus.FAILED
    assert courts == []
    assert result.court_shells == []
    assert result.to_run_step_summary()["court_ids"] == []
    assert result.rejections[0].reason == MethodologyEvidenceTrustCourtReason.TENANT_OR_RUN_MISMATCH


def test_dashboard_verdict_conflict_preserves_disputed_layer_1_finding() -> None:
    claim = _claim("claim_mth_revenue", "claim_mth_revenue", "1000")
    evidence = _evidence_record("claim_mth_revenue")
    dashboard = _truth_dashboard_record(["claim_mth_revenue"])
    disputed_dashboard = dashboard.model_copy(
        update={
            "dashboard": dashboard.dashboard.model_copy(
                update={
                    "rows": [
                        dashboard.dashboard.rows[0].model_copy(
                            update={"verdict": TruthDashboardVerdict.DISPUTED.value}
                        )
                    ]
                }
            ),
            "row_mappings": [
                dashboard.row_mappings[0].model_copy(
                    update={"verdict": TruthDashboardVerdict.DISPUTED}
                )
            ],
        }
    )

    result, courts = _service().run(
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
        truth_dashboards=[disputed_dashboard],
    )

    assert result.status == MethodologyEvidenceTrustCourtStatus.COMPLETED
    assert courts[0].claim_assessments[0].disposition == EvidenceTrustDisposition.DISPUTED
    assert courts[0].findings
    assert courts[0].findings[0].claim_id == "claim_mth_revenue"


def test_refuted_dashboard_verdict_is_not_trusted_and_preserves_finding() -> None:
    claim = _claim("claim_mth_revenue", "claim_mth_revenue", "1000")
    evidence = _evidence_record("claim_mth_revenue")
    dashboard = _truth_dashboard_record(["claim_mth_revenue"])
    refuted_dashboard = dashboard.model_copy(
        update={
            "dashboard": dashboard.dashboard.model_copy(
                update={
                    "rows": [
                        dashboard.dashboard.rows[0].model_copy(
                            update={"verdict": TruthDashboardVerdict.REFUTED.value}
                        )
                    ]
                }
            ),
            "row_mappings": [
                dashboard.row_mappings[0].model_copy(
                    update={"verdict": TruthDashboardVerdict.REFUTED}
                )
            ],
        }
    )

    result, courts = _service().run(
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
        truth_dashboards=[refuted_dashboard],
    )

    assert result.status == MethodologyEvidenceTrustCourtStatus.COMPLETED
    assert courts[0].claim_assessments[0].disposition == EvidenceTrustDisposition.REJECTED
    assert "dashboard_refuted" in courts[0].claim_assessments[0].reason_codes
    assert courts[0].findings
    assert courts[0].findings[0].claim_id == "claim_mth_revenue"
    assert courts[0].findings[0].finding_type.value == "dashboard_consistency"
