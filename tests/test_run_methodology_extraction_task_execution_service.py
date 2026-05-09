"""Tests for run-scoped methodology extraction task execution."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from idis.methodology.models import MethodologyType, RequiredEvidence
from idis.models.document_classification import CddDocumentCategory, FddDocumentCategory
from idis.models.extraction_execution import (
    MethodologyExtractionExecutionReason,
    MethodologyTaskExecutionStatus,
)
from idis.models.extraction_task import (
    ExpectedAnswerSchema,
    ExtractionTask,
    ExtractionTaskBlockerReason,
    ExtractionTaskStatus,
    SourceSpanReference,
)
from idis.services.extraction.service import ExtractedClaimDraft
from idis.services.runs.methodology_extraction_task_execution import (
    InMemoryRunMethodologyExtractionTaskExecutionService,
)

TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEAL_ID = "22222222-2222-2222-2222-222222222222"
RUN_ID = "33333333-3333-3333-3333-333333333333"
COVERAGE_RECORD_ID = "mc_123456789012345678901234"


class RecordingExtractor:
    """Test-only extractor that records hydrated span payloads."""

    def __init__(self, drafts: list[Any] | None = None) -> None:
        self.drafts = drafts if drafts is not None else [_draft()]
        self.calls: list[list[dict[str, Any]]] = []

    def extract(
        self,
        tenant_id: str,
        deal_id: str,
        spans: list[dict[str, Any]],
    ) -> list[Any]:
        self.calls.append(spans)
        return self.drafts


def _draft() -> ExtractedClaimDraft:
    return ExtractedClaimDraft(
        claim_text="Revenue was $10M in FY2024.",
        claim_class="FINANCIAL",
        extraction_confidence=Decimal("0.97"),
        dhabt_score=Decimal("0.95"),
        span_id="span-001",
        predicate="revenue",
        value={"text": "Revenue was $10M in FY2024."},
    )


def _answer_schema() -> ExpectedAnswerSchema:
    return ExpectedAnswerSchema(
        answer_type="narrative",
        question_text="Explain revenue quality.",
        required_evidence=[RequiredEvidence(evidence_type="schedule", description="Schedule")],
        required_calculations=[],
        validation_requirements=["cite source spans"],
        report_section="Financial Due Diligence",
        report_subsection="Revenue",
        methodology_type=MethodologyType.FINANCIAL_DD,
        methodology_section="P&L",
    )


def _ready_task() -> ExtractionTask:
    return ExtractionTask(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        status=ExtractionTaskStatus.READY,
        reason_codes=["ready"],
        methodology_id="financial_dd",
        methodology_version_id="financial_dd_v1",
        methodology_question_id="mq_financial_dd_revenue_quality_0001",
        methodology_type=MethodologyType.FINANCIAL_DD,
        methodology_section="P&L",
        coverage_record_id=COVERAGE_RECORD_ID,
        document_id="doc-financial-model",
        classification_id="dc_financial_model",
        source_spans=[
            SourceSpanReference(
                document_id="doc-financial-model",
                span_id="span-001",
                evidence_tags=["schedule"],
                locator={"sheet": "P&L", "cell": "A1"},
                content_hash="a" * 64,
                text_excerpt=None,
            )
        ],
        target_fdd_category=FddDocumentCategory.FINANCIAL_SCHEDULE_MODEL,
        target_cdd_category=CddDocumentCategory.BUSINESS_PLAN_ASSUMPTIONS,
        required_evidence=[RequiredEvidence(evidence_type="schedule", description="Schedule")],
        expected_answer_schema=_answer_schema(),
        validation_requirements=["cite source spans"],
    )


def _blocked_task() -> ExtractionTask:
    return ExtractionTask(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        status=ExtractionTaskStatus.BLOCKED,
        blocker_reason=ExtractionTaskBlockerReason.NO_SOURCE_SPANS,
        reason_codes=[ExtractionTaskBlockerReason.NO_SOURCE_SPANS.value],
        methodology_id="financial_dd",
        methodology_version_id="financial_dd_v1",
        methodology_question_id="mq_financial_dd_revenue_quality_0002",
        methodology_type=MethodologyType.FINANCIAL_DD,
        methodology_section="P&L",
        coverage_record_id=COVERAGE_RECORD_ID,
        document_id="doc-financial-model",
        classification_id="dc_financial_model",
        source_spans=[],
        target_fdd_category=FddDocumentCategory.FINANCIAL_SCHEDULE_MODEL,
        target_cdd_category=CddDocumentCategory.BUSINESS_PLAN_ASSUMPTIONS,
        required_evidence=[RequiredEvidence(evidence_type="schedule", description="Schedule")],
        expected_answer_schema=_answer_schema(),
        validation_requirements=["cite source spans"],
    )


def _documents() -> list[dict[str, Any]]:
    return [
        {
            "document_id": "doc-financial-model",
            "doc_type": "XLSX",
            "document_name": "Sensitive model.xlsx",
            "spans": [
                {
                    "span_id": "span-001",
                    "text_excerpt": "Revenue was $10M in FY2024.",
                    "locator": {"sheet": "P&L", "cell": "A1"},
                    "span_type": "CELL",
                    "content_hash": "a" * 64,
                }
            ],
        }
    ]


def test_hydrates_raw_span_text_in_memory_but_keeps_run_summary_safe() -> None:
    extractor = RecordingExtractor()

    run_result, execution_result = InMemoryRunMethodologyExtractionTaskExecutionService().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        tasks=[_ready_task()],
        documents=_documents(),
        extractor=extractor,
    )

    assert extractor.calls[0][0]["text_excerpt"] == "Revenue was $10M in FY2024."
    assert execution_result.task_results[0].accepted_outputs[0].coverage_record_id == (
        COVERAGE_RECORD_ID
    )

    summary = run_result.to_run_step_summary()
    serialized = json.dumps(summary, sort_keys=True)
    assert summary["summary"]["accepted_output_count"] == 1
    assert "span-001" in serialized
    assert "Revenue was $10M" not in serialized
    assert "Sensitive model.xlsx" not in serialized
    assert "text_excerpt" not in serialized


def test_slice5_run_execution_result_is_neutral_without_claim_drafts() -> None:
    extractor = RecordingExtractor()

    _run_result, execution_result = InMemoryRunMethodologyExtractionTaskExecutionService().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        tasks=[_ready_task()],
        documents=_documents(),
        extractor=extractor,
    )

    serialized = json.dumps(execution_result.model_dump(mode="json"), sort_keys=True)
    assert len(execution_result.accepted_outputs) == 1
    assert "accepted_claim_drafts" not in serialized
    assert "MethodologyClaimDraft" not in serialized
    assert "future_claim_input_preview" not in serialized


def test_ready_task_without_extractor_fails_closed_without_fabricated_output() -> None:
    run_result, execution_result = InMemoryRunMethodologyExtractionTaskExecutionService().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        tasks=[_ready_task()],
        documents=_documents(),
        extractor=None,
    )

    task_result = execution_result.task_results[0]
    assert task_result.status == MethodologyTaskExecutionStatus.FAILED
    assert task_result.reason == MethodologyExtractionExecutionReason.EXTRACTOR_UNAVAILABLE
    assert task_result.accepted_outputs == []
    assert run_result.to_run_step_summary()["status"] == "FAILED"
    assert run_result.summary.accepted_output_count == 0


def test_missing_source_span_fails_closed_before_extractor_call() -> None:
    extractor = RecordingExtractor()
    documents = _documents()
    documents[0]["spans"] = []

    _run_result, execution_result = InMemoryRunMethodologyExtractionTaskExecutionService().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        tasks=[_ready_task()],
        documents=documents,
        extractor=extractor,
    )

    task_result = execution_result.task_results[0]
    assert extractor.calls == []
    assert task_result.status == MethodologyTaskExecutionStatus.FAILED
    assert task_result.reason == MethodologyExtractionExecutionReason.SOURCE_SPAN_UNAVAILABLE


def test_blocked_task_is_skipped_diagnostically_without_extractor_call() -> None:
    extractor = RecordingExtractor()

    run_result, execution_result = InMemoryRunMethodologyExtractionTaskExecutionService().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        tasks=[_blocked_task()],
        documents=_documents(),
        extractor=extractor,
    )

    assert extractor.calls == []
    assert execution_result.task_results[0].status == MethodologyTaskExecutionStatus.SKIPPED
    assert run_result.summary.skipped_tasks == 1
