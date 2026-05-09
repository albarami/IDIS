"""Tests for synthetic-only methodology extraction task execution."""

from __future__ import annotations

import inspect
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
from idis.services.extraction.task_executor import InMemoryMethodologyExtractionTaskExecutor

TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEAL_ID = "22222222-2222-2222-2222-222222222222"
RUN_ID = "33333333-3333-3333-3333-333333333333"


class RecordingExtractor:
    """Deterministic test extractor that records task-scoped calls."""

    def __init__(
        self,
        drafts: list[Any] | None = None,
        *,
        raises: Exception | None = None,
    ) -> None:
        self.drafts = drafts if drafts is not None else [_draft()]
        self.raises = raises
        self.calls: list[list[dict[str, Any]]] = []

    def extract(
        self,
        tenant_id: str,
        deal_id: str,
        spans: list[dict[str, Any]],
    ) -> list[Any]:
        if self.raises is not None:
            raise self.raises
        self.calls.append(spans)
        return self.drafts


def _span(span_id: str = "span-001") -> SourceSpanReference:
    return SourceSpanReference(
        document_id="doc-financial-model",
        span_id=span_id,
        evidence_tags=["schedule"],
        locator={"sheet": "P&L", "cell": "A1"},
        text_excerpt="Revenue was $10M in FY2024.",
    )


def _answer_schema(answer_type: str = "narrative") -> ExpectedAnswerSchema:
    return ExpectedAnswerSchema(
        answer_type=answer_type,
        question_text="Explain revenue quality.",
        required_evidence=[RequiredEvidence(evidence_type="schedule", description="Schedule")],
        required_calculations=[],
        validation_requirements=["cite source spans"],
        report_section="Financial Due Diligence",
        report_subsection="Revenue",
        methodology_type=MethodologyType.FINANCIAL_DD,
        methodology_section="P&L",
    )


def _ready_task(answer_type: str = "narrative") -> ExtractionTask:
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
        document_id="doc-financial-model",
        classification_id="dc_financial_model",
        source_spans=[_span("span-002"), _span("span-001")],
        target_fdd_category=FddDocumentCategory.FINANCIAL_SCHEDULE_MODEL,
        target_cdd_category=CddDocumentCategory.BUSINESS_PLAN_ASSUMPTIONS,
        required_evidence=[RequiredEvidence(evidence_type="schedule", description="Schedule")],
        expected_answer_schema=_answer_schema(answer_type),
        validation_requirements=["cite source spans"],
    )


def _blocked_task(
    status: ExtractionTaskStatus,
    blocker_reason: ExtractionTaskBlockerReason = ExtractionTaskBlockerReason.NO_SOURCE_SPANS,
) -> ExtractionTask:
    return ExtractionTask(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        status=status,
        blocker_reason=blocker_reason,
        reason_codes=[blocker_reason.value],
        methodology_id="financial_dd",
        methodology_version_id="financial_dd_v1",
        methodology_question_id=f"mq_financial_dd_{status.value}_0001",
        methodology_type=MethodologyType.FINANCIAL_DD,
        methodology_section="P&L",
        document_id="doc-financial-model",
        classification_id="dc_financial_model",
        source_spans=[],
        target_fdd_category=FddDocumentCategory.FINANCIAL_SCHEDULE_MODEL,
        target_cdd_category=CddDocumentCategory.BUSINESS_PLAN_ASSUMPTIONS,
        required_evidence=[RequiredEvidence(evidence_type="schedule", description="Schedule")],
        expected_answer_schema=_answer_schema(),
        validation_requirements=["cite source spans"],
    )


def _draft(
    *,
    span_id: str = "span-001",
    confidence: Decimal | None = Decimal("0.97"),
    dhabt: Decimal | None = Decimal("0.95"),
    claim_text: str = "Revenue was $10M in FY2024.",
    claim_class: str = "FINANCIAL",
    predicate: str | None = "revenue",
    value: Any = None,
) -> ExtractedClaimDraft:
    return ExtractedClaimDraft(
        claim_text=claim_text,
        claim_class=claim_class,
        extraction_confidence=confidence,  # type: ignore[arg-type]
        dhabt_score=dhabt,  # type: ignore[arg-type]
        span_id=span_id,
        predicate=predicate,
        value=value if value is not None else {"value": 10_000_000, "unit": "USD"},
    )


def _execute(
    tasks: list[ExtractionTask],
    extractor: RecordingExtractor | None,
):
    return InMemoryMethodologyExtractionTaskExecutor().execute_tasks(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        tasks=tasks,
        extractor=extractor,
    )


def test_ready_task_with_synthetic_span_produces_methodology_linked_claim_draft() -> None:
    extractor = RecordingExtractor()
    result = _execute([_ready_task()], extractor)

    assert result.summary.accepted_draft_count == 1
    assert result.summary.accepted_output_count == 1
    assert extractor.calls[0][0]["span_id"] == "span-001"
    assert extractor.calls[0][1]["span_id"] == "span-002"

    output = result.task_results[0].accepted_outputs[0]
    assert output.methodology_extraction_output_id.startswith("meo_")
    assert output.extraction_task_id == result.task_results[0].extraction_task_id
    assert output.methodology_question_id == "mq_financial_dd_revenue_quality_0001"
    assert output.answer_type == "narrative"
    assert output.answer == {"text": "Revenue was $10M in FY2024."}
    assert output.source_span_ids == ["span-001"]
    assert output.extraction_confidence == Decimal("0.97")

    draft = result.accepted_claim_drafts[0]
    assert draft.methodology_claim_draft_id.startswith("mcd_")
    assert draft.extraction_task_id == result.task_results[0].extraction_task_id
    assert draft.methodology_id == "financial_dd"
    assert draft.methodology_version_id == "financial_dd_v1"
    assert draft.methodology_question_id == "mq_financial_dd_revenue_quality_0001"
    assert draft.document_id == "doc-financial-model"
    assert draft.source_span_ids == ["span-001"]
    assert draft.predicate == "revenue"
    assert draft.value == {"value": 10_000_000, "unit": "USD"}
    assert draft.extraction_confidence == Decimal("0.97")
    assert draft.dhabt_score == Decimal("0.95")
    assert draft.future_claim_input_preview["corroboration"]["extraction_task_id"] == (
        draft.extraction_task_id
    )
    assert draft.future_claim_input_preview["corroboration"]["methodology_claim_draft_id"] == (
        draft.methodology_claim_draft_id
    )
    span_metadata = draft.future_claim_input_preview["corroboration"]["source_span_metadata"]
    assert span_metadata == [
        {
            "span_id": "span-001",
            "document_id": "doc-financial-model",
            "locator": {"sheet": "P&L", "cell": "A1"},
            "evidence_tags": ["schedule"],
        }
    ]
    assert "Revenue was $10M" not in str(span_metadata)


def test_ready_task_output_must_match_expected_answer_schema() -> None:
    result = _execute(
        [_ready_task(answer_type="numeric")],
        RecordingExtractor([_draft(value={"text": "not numeric"})]),
    )

    task_result = result.task_results[0]
    assert task_result.status == MethodologyTaskExecutionStatus.FAILED
    assert task_result.reason == MethodologyExtractionExecutionReason.SCHEMA_VALIDATION_FAILED
    assert task_result.accepted_outputs == []
    assert result.summary.accepted_output_count == 0
    assert result.summary.rejected_output_count == 1


def test_blocked_evidence_missing_and_not_applicable_tasks_are_skipped() -> None:
    extractor = RecordingExtractor()
    result = _execute(
        [
            _blocked_task(ExtractionTaskStatus.BLOCKED),
            _blocked_task(ExtractionTaskStatus.EVIDENCE_MISSING),
            _blocked_task(ExtractionTaskStatus.NOT_APPLICABLE),
        ],
        extractor,
    )

    assert extractor.calls == []
    assert result.summary.skipped_tasks == 3
    assert {task.status for task in result.task_results} == {MethodologyTaskExecutionStatus.SKIPPED}


def test_skipped_blocked_task_preserves_original_blocker_reason() -> None:
    result = _execute([_blocked_task(ExtractionTaskStatus.BLOCKED)], RecordingExtractor())

    task_result = result.task_results[0]
    assert task_result.status == MethodologyTaskExecutionStatus.SKIPPED
    assert "no_source_spans" in task_result.reason_codes
    assert result.summary.by_reason == {"no_source_spans": 1}


def test_skipped_evidence_missing_task_preserves_original_blocker_reason() -> None:
    result = _execute(
        [
            _blocked_task(
                ExtractionTaskStatus.EVIDENCE_MISSING,
                ExtractionTaskBlockerReason.REQUIRED_EVIDENCE_MISSING,
            )
        ],
        RecordingExtractor(),
    )

    task_result = result.task_results[0]
    assert task_result.status == MethodologyTaskExecutionStatus.SKIPPED
    assert "required_evidence_missing" in task_result.reason_codes
    assert result.summary.by_reason == {"required_evidence_missing": 1}


def test_skipped_summary_counts_retain_original_blocker_reasons() -> None:
    result = _execute(
        [
            _blocked_task(ExtractionTaskStatus.BLOCKED),
            _blocked_task(
                ExtractionTaskStatus.EVIDENCE_MISSING,
                ExtractionTaskBlockerReason.REQUIRED_EVIDENCE_MISSING,
            ),
        ],
        RecordingExtractor(),
    )

    assert result.summary.by_reason == {
        "no_source_spans": 1,
        "required_evidence_missing": 1,
    }


def test_same_task_and_extractor_output_produces_same_draft_id() -> None:
    first = _execute([_ready_task()], RecordingExtractor()).accepted_claim_drafts[0]
    second = _execute([_ready_task()], RecordingExtractor()).accepted_claim_drafts[0]

    assert first.methodology_claim_draft_id == second.methodology_claim_draft_id


def test_changed_predicate_text_value_or_span_changes_draft_id() -> None:
    baseline = _execute([_ready_task()], RecordingExtractor()).accepted_claim_drafts[0]
    cases = [
        _draft(predicate="gross_margin"),
        _draft(claim_text="Revenue was $11M in FY2024."),
        _draft(value={"value": 11_000_000, "unit": "USD"}),
        _draft(span_id="span-002"),
    ]

    changed_ids = [
        _execute([_ready_task()], RecordingExtractor([draft]))
        .accepted_claim_drafts[0]
        .methodology_claim_draft_id
        for draft in cases
    ]

    assert all(draft_id != baseline.methodology_claim_draft_id for draft_id in changed_ids)


def test_hallucinated_source_span_is_rejected() -> None:
    result = _execute([_ready_task()], RecordingExtractor([_draft(span_id="span-999")]))

    task_result = result.task_results[0]
    assert task_result.status == MethodologyTaskExecutionStatus.FAILED
    assert task_result.reason == MethodologyExtractionExecutionReason.HALLUCINATED_SOURCE_REFERENCE
    assert result.summary.accepted_draft_count == 0


def test_extractor_unavailable_fails_closed() -> None:
    result = _execute([_ready_task()], None)

    assert result.task_results[0].status == MethodologyTaskExecutionStatus.FAILED
    assert (
        result.task_results[0].reason == MethodologyExtractionExecutionReason.EXTRACTOR_UNAVAILABLE
    )
    assert result.accepted_claim_drafts == []


def test_ready_task_with_no_source_spans_fails_closed() -> None:
    task = _ready_task().model_copy(update={"source_spans": []})
    extractor = RecordingExtractor()

    result = _execute([task], extractor)

    assert extractor.calls == []
    assert result.task_results[0].reason == MethodologyExtractionExecutionReason.NO_SOURCE_SPANS


def test_malformed_extractor_output_fails_closed() -> None:
    result = _execute([_ready_task()], RecordingExtractor([{"not": "a draft"}]))

    assert result.task_results[0].status == MethodologyTaskExecutionStatus.FAILED
    assert (
        result.task_results[0].reason
        == MethodologyExtractionExecutionReason.MALFORMED_EXTRACTOR_OUTPUT
    )


def test_missing_methodology_linkage_fails_closed() -> None:
    task = _ready_task().model_copy(update={"methodology_question_id": ""})
    extractor = RecordingExtractor()

    result = _execute([task], extractor)

    assert extractor.calls == []
    assert (
        result.task_results[0].reason
        == MethodologyExtractionExecutionReason.MISSING_METHODOLOGY_LINKAGE
    )


def test_low_confidence_low_dhabt_and_missing_gate_metadata_are_rejected() -> None:
    cases = [
        (
            _draft(confidence=Decimal("0.80")),
            MethodologyExtractionExecutionReason.BELOW_CONFIDENCE_THRESHOLD,
        ),
        (_draft(dhabt=Decimal("0.70")), MethodologyExtractionExecutionReason.BELOW_DHABT_THRESHOLD),
        (_draft(confidence=None), MethodologyExtractionExecutionReason.MISSING_GATE_METADATA),
        (_draft(dhabt=None), MethodologyExtractionExecutionReason.MISSING_GATE_METADATA),
    ]

    for draft, reason in cases:
        result = _execute([_ready_task()], RecordingExtractor([draft]))
        assert result.task_results[0].status == MethodologyTaskExecutionStatus.FAILED
        assert result.task_results[0].reason == reason


def test_blank_claim_text_class_missing_predicate_and_invalid_value_are_rejected() -> None:
    cases = [
        _draft(claim_text=" "),
        _draft(claim_class=" "),
        _draft(predicate=None),
        _draft(value="not-a-dict"),
    ]

    for draft in cases:
        result = _execute([_ready_task()], RecordingExtractor([draft]))
        assert result.task_results[0].status == MethodologyTaskExecutionStatus.FAILED
        assert (
            result.task_results[0].reason
            == MethodologyExtractionExecutionReason.MALFORMED_EXTRACTOR_OUTPUT
        )


def test_wrong_type_draft_fields_are_rejected_without_crashing() -> None:
    cases = [
        _draft(claim_text=None),  # type: ignore[arg-type]
        _draft(claim_class=None),  # type: ignore[arg-type]
        _draft(predicate=123),  # type: ignore[arg-type]
        _draft(span_id=123),  # type: ignore[arg-type]
    ]

    for draft in cases:
        result = _execute([_ready_task()], RecordingExtractor([draft]))
        assert result.task_results[0].status == MethodologyTaskExecutionStatus.FAILED
        assert (
            result.task_results[0].reason
            == MethodologyExtractionExecutionReason.MALFORMED_EXTRACTOR_OUTPUT
        )


def test_extractor_exception_fails_closed() -> None:
    result = _execute([_ready_task()], RecordingExtractor(raises=RuntimeError("boom")))

    assert result.task_results[0].status == MethodologyTaskExecutionStatus.FAILED
    assert result.task_results[0].reason == MethodologyExtractionExecutionReason.EXTRACTOR_EXCEPTION


def test_executor_does_not_import_persistence_or_external_integrations() -> None:
    import idis.services.extraction.task_executor as task_executor

    source = inspect.getsource(task_executor)
    forbidden = [
        "ClaimService",
        "Sanad",
        "InMemoryMethodologyCoverageService",
        "sqlalchemy",
        "FastAPI",
        "APIRouter",
        "neo4j",
        "redis",
        "pgvector",
        "LLMClaimExtractor",
        "Anthropic",
        "OpenAI",
        "requests",
        "httpx",
    ]

    for token in forbidden:
        assert token not in source
