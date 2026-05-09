"""Tests for methodology extraction execution result models."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from pydantic import ValidationError

from idis.models.extraction_execution import (
    MethodologyClaimDraft,
    MethodologyExtractionExecutionReason,
    MethodologyExtractionExecutionRunResult,
    MethodologyExtractionExecutionStatus,
    MethodologyExtractionExecutionSummary,
    MethodologyExtractionOutput,
    MethodologyTaskExecutionResult,
    MethodologyTaskExecutionStatus,
)

TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEAL_ID = "22222222-2222-2222-2222-222222222222"
RUN_ID = "33333333-3333-3333-3333-333333333333"
COVERAGE_RECORD_ID = "mc_123456789012345678901234"


def _execution_output() -> MethodologyExtractionOutput:
    return MethodologyExtractionOutput(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        extraction_task_id="et_abc123",
        methodology_id="financial_dd",
        methodology_version_id="financial_dd_v1",
        methodology_question_id="mq_financial_dd_revenue_quality_0001",
        coverage_record_id=COVERAGE_RECORD_ID,
        document_id="doc-financial-model",
        source_span_ids=["span-001"],
        answer_type="numeric",
        answer={"value": 10_000_000, "unit": "USD"},
        extraction_confidence=Decimal("0.97"),
        dhabt_score=Decimal("0.95"),
    )


def test_methodology_extraction_output_preserves_run_task_coverage_and_source_links() -> None:
    output = _execution_output()

    assert output.methodology_extraction_output_id.startswith("meo_")
    assert output.tenant_id == TENANT_ID
    assert output.deal_id == DEAL_ID
    assert output.run_id == RUN_ID
    assert output.extraction_task_id == "et_abc123"
    assert output.methodology_question_id == "mq_financial_dd_revenue_quality_0001"
    assert output.coverage_record_id == COVERAGE_RECORD_ID
    assert output.source_span_ids == ["span-001"]
    assert output.extraction_confidence == Decimal("0.97")


def test_execution_run_step_summary_excludes_answers_and_raw_text() -> None:
    task_result = MethodologyTaskExecutionResult(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        extraction_task_id="et_abc123",
        methodology_question_id="mq_financial_dd_revenue_quality_0001",
        coverage_record_id=COVERAGE_RECORD_ID,
        status=MethodologyTaskExecutionStatus.COMPLETED,
        accepted_outputs=[_execution_output()],
        rejected_outputs=[],
        reason=None,
        reason_codes=["completed"],
        source_span_ids=["span-001"],
    )
    run_result = MethodologyExtractionExecutionRunResult.from_task_results(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        task_results=[task_result],
    )

    summary = run_result.to_run_step_summary()
    serialized = json.dumps(summary, sort_keys=True)

    assert summary["status"] == "COMPLETED"
    assert summary["summary"]["accepted_output_count"] == 1
    assert summary["task_results"][0]["coverage_record_id"] == COVERAGE_RECORD_ID
    assert summary["task_results"][0]["confidence"] == "0.97"
    assert "span-001" in serialized
    assert "10000000" not in serialized
    assert "Revenue was" not in serialized
    assert "claim_text" not in serialized
    assert "text_excerpt" not in serialized


def _claim_draft() -> MethodologyClaimDraft:
    return MethodologyClaimDraft(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        extraction_task_id="et_abc123",
        methodology_id="financial_dd",
        methodology_version_id="financial_dd_v1",
        methodology_question_id="mq_financial_dd_revenue_quality_0001",
        document_id="doc-financial-model",
        source_span_ids=["span-001"],
        claim_text="Revenue was $10M in FY2024.",
        claim_class="FINANCIAL",
        predicate="revenue",
        value={"value": 10_000_000, "unit": "USD"},
        extraction_confidence=Decimal("0.97"),
        dhabt_score=Decimal("0.95"),
        future_claim_input_preview={
            "deal_id": DEAL_ID,
            "claim_class": "FINANCIAL",
            "claim_text": "Revenue was $10M in FY2024.",
            "claim_type": "primary",
            "predicate": "revenue",
            "value": {"value": 10_000_000, "unit": "USD"},
            "primary_span_id": "span-001",
            "corroboration": {
                "extraction_task_id": "et_abc123",
                "methodology_question_id": "mq_financial_dd_revenue_quality_0001",
                "extraction_confidence": "0.97",
                "dhabt_score": "0.95",
            },
        },
    )


def test_valid_methodology_claim_draft_model() -> None:
    draft = _claim_draft()

    assert draft.methodology_claim_draft_id.startswith("mcd_")
    assert draft.extraction_task_id == "et_abc123"
    assert draft.source_span_ids == ["span-001"]
    assert draft.future_claim_input_preview["corroboration"]["dhabt_score"] == "0.95"


def test_methodology_claim_draft_id_is_deterministic() -> None:
    assert _claim_draft().methodology_claim_draft_id == _claim_draft().methodology_claim_draft_id


def test_methodology_claim_draft_id_changes_when_identity_seed_changes() -> None:
    baseline = _claim_draft().methodology_claim_draft_id
    changed_payloads = []
    for key, value in [
        ("predicate", "gross_margin"),
        ("claim_text", "Revenue was $11M in FY2024."),
        ("value", {"value": 11_000_000, "unit": "USD"}),
        ("source_span_ids", ["span-002"]),
    ]:
        payload = _claim_draft().model_dump(mode="python")
        payload.pop("methodology_claim_draft_id")
        payload[key] = value
        changed_payloads.append(MethodologyClaimDraft.model_validate(payload))

    assert all(draft.methodology_claim_draft_id != baseline for draft in changed_payloads)


def test_claim_draft_rejects_blank_required_fields() -> None:
    payload = _claim_draft().model_dump(mode="python")
    payload["predicate"] = " "

    with pytest.raises(ValidationError):
        MethodologyClaimDraft.model_validate(payload)


def test_claim_draft_rejects_empty_source_spans() -> None:
    payload = _claim_draft().model_dump(mode="python")
    payload["source_span_ids"] = []

    with pytest.raises(ValidationError):
        MethodologyClaimDraft.model_validate(payload)


def test_task_execution_result_and_summary_are_deterministic() -> None:
    task_result = MethodologyTaskExecutionResult(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        extraction_task_id="et_abc123",
        status=MethodologyTaskExecutionStatus.COMPLETED,
        accepted_drafts=[_claim_draft()],
        rejected_drafts=[],
        reason=None,
        reason_codes=["completed"],
        source_span_ids=["span-001"],
    )
    summary = MethodologyExtractionExecutionSummary(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        total_tasks=1,
        executed_tasks=1,
        skipped_tasks=0,
        failed_tasks=0,
        accepted_draft_count=1,
        rejected_draft_count=0,
        by_status={"completed": 1},
        by_reason={},
    )

    assert task_result.to_deterministic_json() == task_result.to_deterministic_json()
    assert summary.to_deterministic_json() == summary.to_deterministic_json()


def test_failed_task_result_requires_reason() -> None:
    with pytest.raises(ValidationError):
        MethodologyTaskExecutionResult(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            extraction_task_id="et_abc123",
            status=MethodologyTaskExecutionStatus.FAILED,
            accepted_drafts=[],
            rejected_drafts=[],
            reason=None,
            reason_codes=["failed"],
            source_span_ids=[],
        )


def test_execution_status_and_reason_enums_cover_required_failures() -> None:
    assert MethodologyExtractionExecutionStatus.PARTIAL.value == "partial"
    assert (
        MethodologyExtractionExecutionReason.HALLUCINATED_SOURCE_REFERENCE.value
        == "hallucinated_source_reference"
    )
    assert (
        MethodologyExtractionExecutionReason.MISSING_GATE_METADATA.value == "missing_gate_metadata"
    )
