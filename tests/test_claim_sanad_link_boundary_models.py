"""Tests for Phase 2.9 Claim-Sanad link boundary models."""

from __future__ import annotations

from pydantic import ValidationError

from idis.models.claim_sanad_link_boundary import (
    ClaimPromotionStatus,
    ClaimSanadLinkApplicationMapping,
    ClaimSanadLinkApplicationResult,
    ClaimSanadLinkApplyDecision,
    ClaimSanadLinkReason,
    ClaimSanadLinkRejection,
    ClaimSanadLinkStatus,
    ClaimSanadLinkSummary,
)

TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEAL_ID = "22222222-2222-2222-2222-222222222222"
RUN_ID = "33333333-3333-3333-3333-333333333333"
QUESTION_ID = "mq_financial_dd_revenue_quality_0001"
CLAIM_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
SANAD_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def _decision() -> ClaimSanadLinkApplyDecision:
    return ClaimSanadLinkApplyDecision(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        claim_id=CLAIM_ID,
        methodology_question_id=QUESTION_ID,
        sanad_id=SANAD_ID,
        source_span_ids=["span-002", "span-001"],
        evidence_ids=["evidence-002", "evidence-001"],
    )


def test_apply_decision_defaults_to_non_promotion_and_no_coverage_update() -> None:
    decision = _decision()

    assert decision.claim_link_status == "ready_for_claim_link"
    assert decision.coverage_update_status == "not_applied"
    assert decision.claim_promotion_status == ClaimPromotionStatus.SANAD_LINKED_NOT_IC_READY
    assert decision.source_span_ids == ["span-001", "span-002"]
    assert decision.evidence_ids == ["evidence-001", "evidence-002"]


def test_application_mapping_preserves_protected_claim_fields() -> None:
    mapping = ClaimSanadLinkApplicationMapping(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        claim_id=CLAIM_ID,
        methodology_question_id=QUESTION_ID,
        sanad_id=SANAD_ID,
        source_span_ids=["span-001"],
        evidence_ids=["evidence-001"],
        claim_grade="D",
        claim_verdict="UNVERIFIED",
        claim_action="VERIFY",
        ic_bound=False,
    )

    assert mapping.sanad_id == SANAD_ID
    assert mapping.ic_bound is False
    assert mapping.claim_verdict != "VERIFIED"
    assert mapping.claim_action != "NONE"
    assert mapping.claim_grade == "D"
    assert mapping.coverage_update_status == "not_applied"


def test_application_mapping_rejects_promotion_like_fields() -> None:
    base = {
        "tenant_id": TENANT_ID,
        "deal_id": DEAL_ID,
        "run_id": RUN_ID,
        "claim_id": CLAIM_ID,
        "methodology_question_id": QUESTION_ID,
        "sanad_id": SANAD_ID,
        "source_span_ids": ["span-001"],
        "evidence_ids": ["evidence-001"],
        "claim_grade": "D",
        "claim_verdict": "UNVERIFIED",
        "claim_action": "VERIFY",
        "ic_bound": False,
    }

    for protected_update in [
        {"ic_bound": True},
        {"claim_verdict": "VERIFIED"},
        {"claim_action": "NONE"},
    ]:
        with pytest_raises_validation_error():
            ClaimSanadLinkApplicationMapping(**(base | protected_update))


def test_rejection_reason_codes_include_reason() -> None:
    rejection = ClaimSanadLinkRejection(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        claim_id=CLAIM_ID,
        methodology_question_id=QUESTION_ID,
        sanad_id=SANAD_ID,
        reason=ClaimSanadLinkReason.MISSING_SANAD_ID,
        reason_codes=[ClaimSanadLinkReason.MISSING_SANAD_ID.value],
    )

    assert rejection.reason == ClaimSanadLinkReason.MISSING_SANAD_ID


def test_result_serializes_deterministically() -> None:
    decision = _decision()
    summary = ClaimSanadLinkSummary(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        total_creation_mappings=1,
        decision_count=1,
        applied_link_count=0,
        rejected_decision_count=0,
        already_linked_count=0,
        by_status={ClaimSanadLinkStatus.COMPLETED.value: 1},
        by_reason={},
    )
    result = ClaimSanadLinkApplicationResult(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        status=ClaimSanadLinkStatus.COMPLETED,
        decisions=[decision],
        mappings=[],
        rejections=[],
        summary=summary,
    )

    assert result.to_deterministic_json() == result.to_deterministic_json()
    assert summary.to_deterministic_json() == summary.to_deterministic_json()


class pytest_raises_validation_error:
    """Tiny context manager to keep model validation tests dependency-light."""

    def __enter__(self) -> None:
        return None

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> bool:
        assert exc_type is not None
        assert issubclass(exc_type, ValidationError)
        return True
