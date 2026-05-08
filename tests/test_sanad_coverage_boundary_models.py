"""Tests for Phase 2.7 Sanad and coverage boundary models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from idis.models.methodology_coverage import MethodologyCoverageStatus
from idis.models.sanad_coverage_boundary import (
    CoverageUpdateDecision,
    ICPromotionStatus,
    MethodologyClaimEvidenceReference,
    SanadCoverageBoundaryReason,
    SanadCoverageBoundaryResult,
    SanadCoverageBoundaryStatus,
    SanadCoverageBoundarySummary,
    SanadReadinessDecision,
)

TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEAL_ID = "22222222-2222-2222-2222-222222222222"
RUN_ID = "33333333-3333-3333-3333-333333333333"
QUESTION_ID = "mq_financial_dd_revenue_quality_0001"


def _evidence_ref() -> MethodologyClaimEvidenceReference:
    return MethodologyClaimEvidenceReference(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        methodology_question_id=QUESTION_ID,
        claim_id="claim-001",
        evidence_id="evidence-001",
        source_span_id="span-001",
    )


def _readiness_decision() -> SanadReadinessDecision:
    return SanadReadinessDecision(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        methodology_question_id=QUESTION_ID,
        claim_id="claim-001",
        source_span_ids=["span-001"],
        evidence_ids=["evidence-001"],
        ready_for_future_sanad=True,
        reason=SanadCoverageBoundaryReason.READY_FOR_FUTURE_SANAD,
        reason_codes=[SanadCoverageBoundaryReason.READY_FOR_FUTURE_SANAD.value],
    )


def _coverage_decision() -> CoverageUpdateDecision:
    return CoverageUpdateDecision(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        methodology_question_id=QUESTION_ID,
        target_status=MethodologyCoverageStatus.EXTRACTED,
        claim_ids=["claim-001"],
        evidence_ids=["evidence-001"],
        source_span_ids=["span-001"],
        evidence_links=[],
        reason=SanadCoverageBoundaryReason.READY_FOR_FUTURE_SANAD,
        reason_codes=[SanadCoverageBoundaryReason.READY_FOR_FUTURE_SANAD.value],
    )


def test_evidence_reference_requires_core_linkage() -> None:
    reference = _evidence_ref()

    assert reference.methodology_question_id == QUESTION_ID
    assert reference.claim_id == "claim-001"
    assert reference.evidence_id == "evidence-001"
    assert reference.source_span_id == "span-001"


def test_readiness_and_coverage_decisions_defer_ic_promotion() -> None:
    readiness = _readiness_decision()
    coverage = _coverage_decision()

    assert readiness.ic_promotion_status == ICPromotionStatus.DEFERRED_UNTIL_SANAD
    assert coverage.ic_promotion_status == ICPromotionStatus.DEFERRED_UNTIL_SANAD


def test_answered_coverage_requires_sanad_id_or_deferred_sanad_status() -> None:
    with pytest.raises(ValidationError):
        CoverageUpdateDecision(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            methodology_question_id=QUESTION_ID,
            target_status=MethodologyCoverageStatus.ANSWERED,
            claim_ids=["claim-001"],
            evidence_ids=["evidence-001"],
            source_span_ids=["span-001"],
            evidence_links=[],
            sanad_status=None,
            reason=SanadCoverageBoundaryReason.READY_FOR_FUTURE_SANAD,
            reason_codes=[SanadCoverageBoundaryReason.READY_FOR_FUTURE_SANAD.value],
        )

    decision = CoverageUpdateDecision(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        methodology_question_id=QUESTION_ID,
        target_status=MethodologyCoverageStatus.ANSWERED,
        claim_ids=["claim-001"],
        evidence_ids=["evidence-001"],
        source_span_ids=["span-001"],
        evidence_links=[],
        sanad_status="deferred",
        reason=SanadCoverageBoundaryReason.READY_FOR_FUTURE_SANAD,
        reason_codes=[SanadCoverageBoundaryReason.READY_FOR_FUTURE_SANAD.value],
    )

    assert decision.target_status == MethodologyCoverageStatus.ANSWERED
    assert decision.sanad_status == "deferred"
    assert decision.ic_promotion_status == ICPromotionStatus.DEFERRED_UNTIL_SANAD


def test_contradicted_coverage_requires_conflict_or_defect_reference() -> None:
    with pytest.raises(ValidationError):
        CoverageUpdateDecision(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            methodology_question_id=QUESTION_ID,
            target_status=MethodologyCoverageStatus.CONTRADICTED,
            claim_ids=["claim-001"],
            evidence_ids=["evidence-001"],
            source_span_ids=["span-001"],
            evidence_links=[],
            reason=SanadCoverageBoundaryReason.CONTRADICTED,
            reason_codes=[SanadCoverageBoundaryReason.CONTRADICTED.value],
        )


def test_contradicted_evidence_reference_requires_conflict_or_defect_reference() -> None:
    with pytest.raises(ValidationError):
        MethodologyClaimEvidenceReference(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            methodology_question_id=QUESTION_ID,
            claim_id="claim-001",
            evidence_id="evidence-001",
            source_span_id="span-001",
            target_status=MethodologyCoverageStatus.CONTRADICTED,
        )

    by_conflict = MethodologyClaimEvidenceReference(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        methodology_question_id=QUESTION_ID,
        claim_id="claim-001",
        evidence_id="evidence-001",
        source_span_id="span-001",
        target_status=MethodologyCoverageStatus.CONTRADICTED,
        conflict_ids=["conflict-001"],
    )
    by_defect = MethodologyClaimEvidenceReference(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        methodology_question_id=QUESTION_ID,
        claim_id="claim-001",
        evidence_id="evidence-002",
        source_span_id="span-001",
        target_status=MethodologyCoverageStatus.CONTRADICTED,
        defect_ids=["defect-001"],
    )

    assert by_conflict.conflict_ids == ["conflict-001"]
    assert by_defect.defect_ids == ["defect-001"]


def test_result_summary_serializes_deterministically() -> None:
    result = SanadCoverageBoundaryResult(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        status=SanadCoverageBoundaryStatus.COMPLETED,
        readiness_decisions=[_readiness_decision()],
        coverage_decisions=[_coverage_decision()],
        summary=SanadCoverageBoundarySummary(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            total_claim_mappings=1,
            ready_for_future_sanad_count=1,
            coverage_decision_count=1,
            blocked_decision_count=0,
            by_status={"completed": 1},
            by_reason={SanadCoverageBoundaryReason.READY_FOR_FUTURE_SANAD.value: 1},
            by_coverage_status={MethodologyCoverageStatus.EXTRACTED.value: 1},
        ),
    )

    assert result.to_deterministic_json() == result.to_deterministic_json()
