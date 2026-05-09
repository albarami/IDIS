"""Tests for Slice 11 Evidence Trust Court materialization models."""

from __future__ import annotations

import json
from uuid import UUID

from idis.models.debate import DebateRole, StopReason
from idis.models.evidence_trust_court_materialization import (
    EvidenceTrustDisposition,
    EvidenceTrustIdType,
    MethodologyEvidenceTrustCourtStatus,
    RunScopedClaimTrustAssessment,
    RunScopedEvidenceTrustCourtRecord,
    RunScopedEvidenceTrustCourtRoleSummary,
    RunScopedEvidenceTrustCourtSummary,
    build_evidence_trust_alias_maps,
    deterministic_evidence_trust_court_id,
)
from idis.models.sanad import SanadGrade
from idis.models.truth_dashboard_materialization import TruthDashboardVerdict
from idis.validators.muhasabah import MuhasabahValidator
from tests.test_run_methodology_deterministic_calculation_service import (
    DEAL_ID,
    RUN_ID,
    TENANT_ID,
)


def test_deterministic_court_ids_and_aliases_cover_claims_and_calcs() -> None:
    first_id = deterministic_evidence_trust_court_id(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        claim_ids=["claim_mth_revenue", "claim_mth_cogs"],
        dashboard_id="dashboard-001",
    )
    second_id = deterministic_evidence_trust_court_id(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        claim_ids=["claim_mth_cogs", "claim_mth_revenue"],
        dashboard_id="dashboard-001",
    )
    alias_maps = build_evidence_trust_alias_maps(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        claim_ids=["claim_mth_revenue"],
        calc_ids=["calc-revenue-quality"],
    )

    assert first_id == second_id
    assert (
        alias_maps.claim_aliases["claim_mth_revenue"]
        != alias_maps.calc_aliases["calc-revenue-quality"]
    )
    assert (
        str(UUID(alias_maps.claim_aliases["claim_mth_revenue"]))
        == alias_maps.claim_aliases["claim_mth_revenue"]
    )
    assert (
        str(UUID(alias_maps.calc_aliases["calc-revenue-quality"]))
        == alias_maps.calc_aliases["calc-revenue-quality"]
    )
    assert alias_maps.resolve(alias_maps.claim_aliases["claim_mth_revenue"]) == (
        EvidenceTrustIdType.CLAIM,
        "claim_mth_revenue",
    )
    assert alias_maps.resolve(alias_maps.calc_aliases["calc-revenue-quality"]) == (
        EvidenceTrustIdType.CALC,
        "calc-revenue-quality",
    )


def test_claim_mth_and_calc_ids_pass_muhasabah_through_uuid_aliases() -> None:
    alias_maps = build_evidence_trust_alias_maps(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        claim_ids=["claim_mth_revenue"],
        calc_ids=["calc-revenue-quality"],
    )

    validation = MuhasabahValidator().validate(
        {
            "agent_id": "sanad_breaker-layer1",
            "output_id": "output-001",
            "supported_claim_ids": [alias_maps.claim_aliases["claim_mth_revenue"]],
            "supported_calc_ids": [alias_maps.calc_aliases["calc-revenue-quality"]],
            "falsifiability_tests": [
                {
                    "test_description": "Check source provenance exists",
                    "required_evidence": "source span provenance",
                    "pass_fail_rule": "all referenced spans are present",
                }
            ],
            "uncertainties": [
                {
                    "uncertainty": "Layer 1 only",
                    "impact": "LOW",
                    "mitigation": "Validated Evidence Package remains Slice 12",
                }
            ],
            "confidence": 0.8,
            "timestamp": "2026-01-01T00:00:00Z",
            "is_subjective": False,
        }
    )

    assert validation.passed


def test_record_shell_and_run_summary_exclude_unsafe_debate_and_payload_fields() -> None:
    assessment = RunScopedClaimTrustAssessment(
        claim_id="claim_mth_revenue",
        disposition=EvidenceTrustDisposition.TRUSTED,
        evidence_ids=["evidence-claim_mth_revenue"],
        source_span_ids=["span-claim_mth_revenue"],
        sanad_id="sanad-claim_mth_revenue",
        sanad_grade=SanadGrade.A,
        dashboard_verdict=TruthDashboardVerdict.CONFIRMED,
        calc_ids=["calc-revenue-quality"],
        defect_ids=[],
        reason_codes=["trusted_a_or_b_sanad", "source_provenance_verified"],
    )
    record = RunScopedEvidenceTrustCourtRecord(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        court_id="court-001",
        dashboard_id="dashboard-001",
        claim_assessments=[assessment],
        findings=[],
        role_summaries=[
            RunScopedEvidenceTrustCourtRoleSummary(
                output_id="out-001",
                agent_id="advocate-layer1",
                role=DebateRole.ADVOCATE,
                output_type="layer1_evidence_position",
                supported_claim_ids=["claim_mth_revenue"],
                supported_calc_ids=["calc-revenue-quality"],
                confidence=0.8,
                reason_codes=["muhasabah_gate_passed"],
            )
        ],
        stop_reason=StopReason.CONSENSUS,
        status="created",
    )
    summary = RunScopedEvidenceTrustCourtSummary(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        total_claims=1,
        assessed_claim_count=1,
        finding_count=0,
        rejected_count=0,
        by_disposition={EvidenceTrustDisposition.TRUSTED.value: 1},
        by_reason={"trusted_a_or_b_sanad": 1},
        by_grade={SanadGrade.A.value: 1},
        by_dashboard_verdict={TruthDashboardVerdict.CONFIRMED.value: 1},
    )

    shell = record.to_shell(summary=summary)
    summary_json = json.dumps(record.to_run_step_summary(summary=summary), sort_keys=True)
    shell_json = shell.model_dump_json()

    assert shell.court_id == "court-001"
    assert shell.claim_ids == ["claim_mth_revenue"]
    assert "calc-revenue-quality" in summary_json
    assert "claim_mth_revenue: 1000 USD" not in summary_json
    assert "AgentOutput" not in summary_json
    assert "content" not in summary_json
    assert "claim_text" not in summary_json
    assert "value_struct" not in summary_json
    assert "locator" not in summary_json
    assert "document_name" not in summary_json
    assert "grade_explanation" not in summary_json
    assert "recommendation" not in summary_json
    assert "GO" not in summary_json
    assert "alias" not in summary_json
    assert "advocate-layer1" in summary_json
    assert "content" not in shell_json
    assert "alias" not in shell_json


def test_run_result_status_uses_failed_when_only_rejections_exist() -> None:
    summary = RunScopedEvidenceTrustCourtSummary(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        total_claims=1,
        assessed_claim_count=0,
        finding_count=0,
        rejected_count=1,
        by_disposition={},
        by_reason={"truth_dashboard_shell_only": 1},
        by_grade={},
        by_dashboard_verdict={},
    )

    assert summary.aggregate_status() == MethodologyEvidenceTrustCourtStatus.FAILED
