"""Tests for Slice 6 run-scoped methodology claim materialization."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from idis.models.claim_materialization import (
    ClaimMaterializationReason,
    ClaimMaterializationStatus,
    MaterializedClaimType,
)
from idis.models.extraction_execution import (
    MethodologyClaimDraft,
    MethodologyExtractionExecutionReason,
    MethodologyExtractionExecutionResult,
    MethodologyExtractionExecutionStatus,
    MethodologyExtractionExecutionSummary,
    MethodologyExtractionOutput,
    MethodologyTaskExecutionResult,
    MethodologyTaskExecutionStatus,
)
from idis.services.runs.methodology_claim_materialization import (
    InMemoryRunMethodologyClaimMaterializationService,
)

TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEAL_ID = "22222222-2222-2222-2222-222222222222"
RUN_ID = "33333333-3333-3333-3333-333333333333"


def _output(
    *,
    output_id: str | None = None,
    tenant_id: str = TENANT_ID,
    deal_id: str = DEAL_ID,
    run_id: str = RUN_ID,
    document_id: str = "doc-financial-model",
    source_span_ids: list[str] | None = None,
    answer_type: str = "numeric",
    answer: dict[str, Any] | None = None,
) -> MethodologyExtractionOutput:
    return MethodologyExtractionOutput(
        methodology_extraction_output_id=output_id,
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        extraction_task_id="et_revenue_quality",
        methodology_id="financial_dd",
        methodology_version_id="financial_dd_v1",
        methodology_question_id="mq_financial_dd_revenue_quality_0001",
        coverage_record_id="mcr_revenue_quality",
        document_id=document_id,
        source_span_ids=source_span_ids or ["span-001"],
        answer_type=answer_type,
        answer=answer
        or {
            "claim_type": "FINANCIAL_METRIC",
            "label": "revenue",
            "value": 10_000_000,
            "unit": "USD",
            "currency": "USD",
            "time_window": "FY2024",
        },
        extraction_confidence=Decimal("0.97"),
        dhabt_score=Decimal("0.95"),
    )


def _execution_result(
    *,
    tenant_id: str = TENANT_ID,
    deal_id: str = DEAL_ID,
    run_id: str = RUN_ID,
    accepted_outputs: list[MethodologyExtractionOutput] | None = None,
    accepted_claim_drafts: list[MethodologyClaimDraft] | None = None,
    task_results: list[MethodologyTaskExecutionResult] | None = None,
) -> MethodologyExtractionExecutionResult:
    outputs = accepted_outputs or []
    return MethodologyExtractionExecutionResult(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        status=MethodologyExtractionExecutionStatus.COMPLETED,
        task_results=task_results or [],
        accepted_outputs=outputs,
        accepted_claim_drafts=accepted_claim_drafts or [],
        summary=MethodologyExtractionExecutionSummary(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            total_tasks=len(outputs),
            executed_tasks=len(outputs),
            skipped_tasks=0,
            failed_tasks=0,
            accepted_output_count=len(outputs),
            rejected_output_count=0,
            accepted_draft_count=len(accepted_claim_drafts or []),
            rejected_draft_count=0,
            by_status={"completed": len(outputs)} if outputs else {},
            by_reason={},
        ),
    )


def _draft() -> MethodologyClaimDraft:
    return MethodologyClaimDraft(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        extraction_task_id="et_legacy",
        methodology_id="financial_dd",
        methodology_version_id="financial_dd_v1",
        methodology_question_id="mq_legacy",
        document_id="doc-legacy",
        source_span_ids=["span-legacy"],
        claim_text="Legacy draft must not materialize in Slice 6.",
        claim_class="FINANCIAL",
        predicate="legacy",
        value={"value": 1, "unit": "USD"},
        extraction_confidence=Decimal("0.97"),
        dhabt_score=Decimal("0.95"),
        future_claim_input_preview={"claim_type": "primary"},
    )


def _run(
    execution_result: MethodologyExtractionExecutionResult,
) -> tuple[Any, Any]:
    return InMemoryRunMethodologyClaimMaterializationService().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        execution_result=execution_result,
    )


def test_accepted_numeric_execution_output_becomes_run_scoped_claim() -> None:
    run_result, claims = _run(_execution_result(accepted_outputs=[_output()]))

    assert run_result.status == ClaimMaterializationStatus.COMPLETED
    assert len(claims) == 1
    claim = claims[0]
    assert claim.claim_id.startswith("claim_mth_")
    assert claim.claim_type == MaterializedClaimType.FINANCIAL_METRIC
    assert claim.claim_text == "revenue: 10000000 USD"
    assert claim.extraction_output_id.startswith("meo_")
    assert claim.coverage_record_id == "mcr_revenue_quality"
    assert claim.value_struct.currency == "USD"


def test_narrative_claim_text_uses_exact_accepted_answer_text() -> None:
    text = "The founder previously led product at Acme."
    output = _output(
        answer_type="narrative",
        answer={"claim_type": "TEAM", "text": text},
    )

    _run_result, claims = _run(_execution_result(accepted_outputs=[output]))

    assert claims[0].claim_text == text
    assert claims[0].claim_type == MaterializedClaimType.TEAM
    assert claims[0].value_struct.value == text


def test_missing_semantic_claim_type_rejects_output_without_claim() -> None:
    output = _output(answer={"label": "revenue", "value": 10_000_000, "currency": "USD"})

    run_result, claims = _run(_execution_result(accepted_outputs=[output]))

    assert run_result.status == ClaimMaterializationStatus.FAILED
    assert claims == []
    assert run_result.rejected_outputs[0].reason == ClaimMaterializationReason.MISSING_CLAIM_TYPE


def test_numeric_financial_output_missing_required_value_fields_fails_closed() -> None:
    output = _output(
        answer={
            "claim_type": "FINANCIAL_METRIC",
            "label": "revenue",
            "value": 10_000_000,
        }
    )

    run_result, claims = _run(_execution_result(accepted_outputs=[output]))

    assert run_result.status == ClaimMaterializationStatus.FAILED
    assert claims == []
    assert run_result.rejected_outputs[0].reason == ClaimMaterializationReason.MISSING_VALUE_STRUCT


def test_table_outputs_are_rejected_until_deterministic_conversion_exists() -> None:
    output = _output(
        answer_type="table",
        answer={"claim_type": "FINANCIAL_METRIC", "rows": [{"metric": "revenue"}]},
    )

    run_result, claims = _run(_execution_result(accepted_outputs=[output]))

    assert run_result.status == ClaimMaterializationStatus.FAILED
    assert claims == []
    assert run_result.rejected_outputs[0].reason == (
        ClaimMaterializationReason.UNSUPPORTED_ANSWER_TYPE
    )


def test_execution_result_context_mismatch_fails_closed() -> None:
    run_result, claims = _run(
        _execution_result(
            tenant_id="99999999-9999-9999-9999-999999999999",
            accepted_outputs=[_output()],
        )
    )

    assert run_result.status == ClaimMaterializationStatus.FAILED
    assert claims == []
    assert run_result.rejected_outputs[0].reason == (
        ClaimMaterializationReason.TENANT_OR_RUN_MISMATCH
    )


def test_accepted_output_context_mismatch_fails_closed_per_output() -> None:
    output = _output(run_id="44444444-4444-4444-4444-444444444444")

    run_result, claims = _run(_execution_result(accepted_outputs=[output]))

    assert run_result.status == ClaimMaterializationStatus.FAILED
    assert claims == []
    assert run_result.rejected_outputs[0].reason == (
        ClaimMaterializationReason.TENANT_OR_RUN_MISMATCH
    )


def test_duplicate_extraction_output_id_does_not_create_duplicate_claims() -> None:
    first = _output(output_id="meo_duplicate")
    duplicate = _output(output_id="meo_duplicate", source_span_ids=["span-002"])

    run_result, claims = _run(_execution_result(accepted_outputs=[first, duplicate]))

    assert run_result.status == ClaimMaterializationStatus.PARTIAL
    assert len(claims) == 1
    assert run_result.summary.created_claim_count == 1
    assert run_result.summary.rejected_output_count == 1
    assert run_result.rejected_outputs[0].reason == (
        ClaimMaterializationReason.DUPLICATE_EXTRACTION_OUTPUT_ID
    )


def test_rejected_skipped_and_failed_task_results_do_not_become_claims() -> None:
    task_results = [
        MethodologyTaskExecutionResult(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            extraction_task_id="et_failed",
            methodology_question_id="mq_failed",
            coverage_record_id="mcr_failed",
            status=MethodologyTaskExecutionStatus.FAILED,
            accepted_outputs=[],
            rejected_outputs=[{"reason": "extractor_unavailable"}],
            reason=MethodologyExtractionExecutionReason.EXTRACTOR_UNAVAILABLE,
            reason_codes=["extractor_unavailable"],
            source_span_ids=["span-failed"],
        ),
        MethodologyTaskExecutionResult(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            extraction_task_id="et_skipped",
            methodology_question_id="mq_skipped",
            coverage_record_id="mcr_skipped",
            status=MethodologyTaskExecutionStatus.SKIPPED,
            accepted_outputs=[],
            rejected_outputs=[],
            reason=MethodologyExtractionExecutionReason.BLOCKED_TASK,
            reason_codes=["blocked_task"],
            source_span_ids=["span-skipped"],
        ),
    ]

    run_result, claims = _run(_execution_result(task_results=task_results))

    assert run_result.status == ClaimMaterializationStatus.COMPLETED
    assert claims == []
    assert run_result.summary.total_outputs == 0
    assert run_result.summary.created_claim_count == 0


def test_slice6_materialization_ignores_legacy_accepted_claim_drafts() -> None:
    run_result, claims = _run(_execution_result(accepted_claim_drafts=[_draft()]))

    assert run_result.status == ClaimMaterializationStatus.COMPLETED
    assert claims == []
    assert run_result.summary.created_claim_count == 0
    assert run_result.summary.total_outputs == 0


def test_safe_run_step_summary_excludes_answers_drafts_and_raw_content() -> None:
    output = _output(
        answer={
            "claim_type": "FINANCIAL_METRIC",
            "label": "revenue",
            "value": 10_000_000,
            "unit": "USD",
            "currency": "USD",
            "time_window": "FY2024",
            "raw_text": "Revenue was $10M in FY2024.",
        }
    )

    run_result, _claims = _run(_execution_result(accepted_outputs=[output]))
    summary = run_result.to_run_step_summary()
    serialized = json.dumps(summary, sort_keys=True)

    assert "claim_ids" in summary
    assert "created_claims" not in serialized
    assert "answer" not in serialized
    assert "raw_text" not in serialized
    assert "Revenue was $10M" not in serialized
    assert "accepted_claim_drafts" not in serialized
    assert "future_claim_input_preview" not in serialized


def test_unsafe_source_metadata_is_rejected_and_excluded_from_summary() -> None:
    malicious_values = [
        "C:\\secret\\file.pdf",
        "C:/secret/file.pdf",
        "/mnt/secret/file.pdf",
        "\\\\server\\share\\file.pdf",
        "file://secret/file.pdf",
        "s3://bucket/file.pdf",
        "http://example.com/file.pdf",
        "https://example.com/file.pdf",
    ]
    outputs = [
        _output(output_id=f"meo_bad_doc_{index}", document_id=malicious_value)
        for index, malicious_value in enumerate(malicious_values)
    ]
    outputs.extend(
        _output(output_id=f"meo_bad_span_{index}", source_span_ids=[malicious_value])
        for index, malicious_value in enumerate(malicious_values)
    )

    run_result, claims = _run(_execution_result(accepted_outputs=outputs))
    summary = run_result.to_run_step_summary()
    serialized = json.dumps(summary, sort_keys=True)

    assert run_result.status == ClaimMaterializationStatus.FAILED
    assert claims == []
    assert run_result.summary.created_claim_count == 0
    assert run_result.summary.rejected_output_count == len(outputs)
    for malicious_value in malicious_values:
        assert malicious_value not in serialized
    assert "document_id" not in serialized
    assert "source_span_ids" not in serialized
