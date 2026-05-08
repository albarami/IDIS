"""Tests for methodology claim materialization models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from idis.models.claim_materialization import (
    ClaimMaterializationDraftRejection,
    ClaimMaterializationReason,
    ClaimMaterializationResult,
    ClaimMaterializationStatus,
    DraftClaimMapping,
    MethodologyClaimMaterializationSummary,
)

TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEAL_ID = "22222222-2222-2222-2222-222222222222"
RUN_ID = "33333333-3333-3333-3333-333333333333"


def _mapping() -> DraftClaimMapping:
    return DraftClaimMapping(
        methodology_claim_draft_id="mcd_abc123",
        claim_id="claim-001",
        extraction_task_id="et_abc123",
        methodology_question_id="mq_financial_dd_revenue_quality_0001",
        document_id="doc-financial-model",
        source_span_ids=["span-001"],
    )


def test_draft_claim_mapping_is_valid() -> None:
    mapping = _mapping()

    assert mapping.methodology_claim_draft_id == "mcd_abc123"
    assert mapping.source_span_ids == ["span-001"]


def test_mapping_rejects_blank_references() -> None:
    payload = _mapping().model_dump(mode="python")
    payload["claim_id"] = " "

    with pytest.raises(ValidationError):
        DraftClaimMapping.model_validate(payload)


def test_rejection_model_requires_machine_reason() -> None:
    rejection = ClaimMaterializationDraftRejection(
        methodology_claim_draft_id="mcd_bad",
        reason=ClaimMaterializationReason.STALE_OR_INVALID_DRAFT_ID,
        reason_codes=["stale_or_invalid_draft_id"],
        message="draft id does not match deterministic seed",
    )

    assert rejection.reason == ClaimMaterializationReason.STALE_OR_INVALID_DRAFT_ID


def test_materialization_result_summary_is_deterministic() -> None:
    summary = MethodologyClaimMaterializationSummary(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        total_drafts=2,
        created_claim_count=1,
        rejected_draft_count=1,
        by_status={"completed": 1, "rejected": 1},
        by_reason={"stale_or_invalid_draft_id": 1},
    )
    result = ClaimMaterializationResult(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        status=ClaimMaterializationStatus.PARTIAL,
        draft_claim_mappings=[_mapping()],
        rejected_drafts=[
            ClaimMaterializationDraftRejection(
                methodology_claim_draft_id="mcd_bad",
                reason=ClaimMaterializationReason.STALE_OR_INVALID_DRAFT_ID,
                reason_codes=["stale_or_invalid_draft_id"],
                message="draft id does not match deterministic seed",
            )
        ],
        summary=summary,
    )

    assert result.to_deterministic_json() == result.to_deterministic_json()
    assert result.summary.by_reason == {"stale_or_invalid_draft_id": 1}


def test_required_rejection_reasons_are_defined() -> None:
    assert ClaimMaterializationReason.STALE_OR_INVALID_DRAFT_ID.value == (
        "stale_or_invalid_draft_id"
    )
    assert ClaimMaterializationReason.TENANT_OR_RUN_MISMATCH.value == "tenant_or_run_mismatch"
    assert ClaimMaterializationReason.CLAIM_SERVICE_CREATE_FAILED.value == (
        "claim_service_create_failed"
    )
