"""Tests for run-scoped document preflight models."""

from __future__ import annotations

from idis.models.document_classification import DocumentSupportStatus, DocumentTriageStatus
from idis.models.document_preflight import (
    DocumentPreflightReason,
    DocumentPreflightResult,
    DocumentPreflightSpanReference,
    DocumentPreflightStatus,
    RunDocumentPreflightDecision,
)

TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEAL_ID = "22222222-2222-2222-2222-222222222222"
RUN_ID = "33333333-3333-3333-3333-333333333333"


def _decision(document_id: str = "doc-1") -> RunDocumentPreflightDecision:
    return RunDocumentPreflightDecision(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        document_id=document_id,
        classification_id=f"classification-{document_id}",
        support_status=DocumentSupportStatus.PARTIALLY_SUPPORTED,
        triage_status=DocumentTriageStatus.PARTIAL,
        usable_for_methodology_extraction=True,
        reason=DocumentPreflightReason.PARTIAL_SUPPORT,
        reason_codes=["pdf_text_only_no_ocr"],
        warning_codes=["PDF parser extracts text only; OCR/table extraction is not claimed"],
        source_spans=[
            DocumentPreflightSpanReference(
                span_id="span-1",
                document_id=document_id,
                locator={"page": 1},
                span_type="PAGE_TEXT",
                content_hash="a" * 64,
            )
        ],
        methodology_target_areas=["P&L"],
    )


def test_document_preflight_result_is_deterministic() -> None:
    result = DocumentPreflightResult(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        status=DocumentPreflightStatus.PARTIAL,
        decisions=[_decision()],
    )

    assert result.to_deterministic_json() == result.to_deterministic_json()
    assert result.summary.total_documents == 1
    assert result.summary.usable_documents == 1
    assert result.summary.by_reason[DocumentPreflightReason.PARTIAL_SUPPORT.value] == 1


def test_document_preflight_result_summary_has_no_raw_span_text() -> None:
    result = DocumentPreflightResult(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        status=DocumentPreflightStatus.PARTIAL,
        decisions=[_decision()],
    )

    summary = result.to_run_step_summary()

    assert "Revenue was $5M" not in str(summary)
    assert "text_excerpt" not in str(summary)
    assert summary["eligible_document_ids"] == ["doc-1"]
    assert summary["source_spans_by_document_id"]["doc-1"][0]["span_id"] == "span-1"
