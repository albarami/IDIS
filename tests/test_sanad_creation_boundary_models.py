"""Tests for Phase 2.8 live Sanad creation boundary models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from idis.models.sanad_coverage_boundary import ICPromotionStatus
from idis.models.sanad_creation_boundary import (
    ClaimSanadLinkDecision,
    SanadCreationMapping,
    SanadCreationReason,
    SanadCreationRejection,
    SanadCreationResult,
    SanadCreationStatus,
    SanadCreationSummary,
)

TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEAL_ID = "22222222-2222-2222-2222-222222222222"
RUN_ID = "33333333-3333-3333-3333-333333333333"
QUESTION_ID = "mq_financial_dd_revenue_quality_0001"


def _mapping() -> SanadCreationMapping:
    return SanadCreationMapping(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        claim_id="claim-001",
        methodology_question_id=QUESTION_ID,
        source_span_ids=["span-001"],
        evidence_ids=["evidence-001", "evidence-002"],
        primary_evidence_id="evidence-001",
        corroborating_evidence_ids=["evidence-002"],
        sanad_id="44444444-4444-4444-4444-444444444444",
        transmission_chain_node_count=2,
        chain_node_types=["INGEST", "EXTRACT"],
        extraction_confidence=0.91,
        dhabt_score=0.88,
    )


def _rejection() -> SanadCreationRejection:
    return SanadCreationRejection(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        claim_id="claim-001",
        methodology_question_id=QUESTION_ID,
        source_span_ids=["span-001"],
        evidence_ids=[],
        reason=SanadCreationReason.EVIDENCE_MISSING,
        reason_codes=[SanadCreationReason.EVIDENCE_MISSING.value],
    )


def test_mapping_preserves_linkage_and_defers_ic_promotion() -> None:
    mapping = _mapping()

    assert mapping.claim_id == "claim-001"
    assert mapping.methodology_question_id == QUESTION_ID
    assert mapping.source_span_ids == ["span-001"]
    assert mapping.primary_evidence_id == "evidence-001"
    assert mapping.corroborating_evidence_ids == ["evidence-002"]
    assert mapping.ic_promotion_status == ICPromotionStatus.DEFERRED_UNTIL_SANAD
    assert mapping.coverage_status == "deferred"
    assert mapping.coverage_update_status == "not_applied"


def test_claim_link_decision_is_metadata_only() -> None:
    decision = ClaimSanadLinkDecision(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        claim_id="claim-001",
        methodology_question_id=QUESTION_ID,
        sanad_id="44444444-4444-4444-4444-444444444444",
        claim_link_status="deferred",
        coverage_status="deferred",
        coverage_update_status="not_applied",
    )

    assert decision.claim_link_status == "deferred"
    assert decision.coverage_update_status == "not_applied"
    assert decision.ic_promotion_status == ICPromotionStatus.DEFERRED_UNTIL_SANAD


def test_rejection_reason_codes_must_include_reason() -> None:
    with pytest.raises(ValidationError):
        SanadCreationRejection(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            claim_id="claim-001",
            methodology_question_id=QUESTION_ID,
            source_span_ids=["span-001"],
            evidence_ids=[],
            reason=SanadCreationReason.EVIDENCE_MISSING,
            reason_codes=["blocked"],
        )


def test_mapping_requires_created_sanad_id_and_evidence_linkage() -> None:
    data = _mapping().model_dump(mode="python")
    data["sanad_id"] = ""
    with pytest.raises(ValidationError):
        SanadCreationMapping(**data)

    data = _mapping().model_dump(mode="python")
    data["primary_evidence_id"] = ""
    with pytest.raises(ValidationError):
        SanadCreationMapping(**data)


def test_result_summary_serializes_deterministically() -> None:
    summary = SanadCreationSummary(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        total_readiness_decisions=2,
        selected_decision_count=1,
        created_sanad_count=1,
        rejected_decision_count=1,
        already_created_count=0,
        by_status={SanadCreationStatus.PARTIAL.value: 1},
        by_reason={SanadCreationReason.EVIDENCE_MISSING.value: 1},
    )
    result = SanadCreationResult(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        status=SanadCreationStatus.PARTIAL,
        mappings=[_mapping()],
        rejections=[_rejection()],
        claim_link_decisions=[
            ClaimSanadLinkDecision(
                tenant_id=TENANT_ID,
                deal_id=DEAL_ID,
                run_id=RUN_ID,
                claim_id="claim-001",
                methodology_question_id=QUESTION_ID,
                sanad_id="44444444-4444-4444-4444-444444444444",
            )
        ],
        summary=summary,
    )

    assert result.to_deterministic_json() == result.to_deterministic_json()
    assert summary.to_deterministic_json() == summary.to_deterministic_json()
