"""Tests for Phase 2.4 extraction task planning models."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from idis.methodology.models import MethodologyType, RequiredCalculation, RequiredEvidence
from idis.models.document_classification import CddDocumentCategory, FddDocumentCategory
from idis.models.extraction_task import (
    ExpectedAnswerSchema,
    ExtractionTask,
    ExtractionTaskBlockerReason,
    ExtractionTaskStatus,
    SourceSpanReference,
    generate_extraction_task_id,
)

TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEAL_ID = "22222222-2222-2222-2222-222222222222"
RUN_ID = "33333333-3333-3333-3333-333333333333"


def _answer_schema() -> ExpectedAnswerSchema:
    return ExpectedAnswerSchema(
        answer_type="narrative",
        question_text="Explain revenue quality.",
        required_evidence=[RequiredEvidence(evidence_type="schedule", description="P&L schedule")],
        required_calculations=[RequiredCalculation(calc_type="revenue_growth")],
        validation_requirements=["cite source spans"],
        report_section="Financial Due Diligence",
        report_subsection="Revenue",
        methodology_type=MethodologyType.FINANCIAL_DD,
        methodology_section="P&L",
    )


def _span() -> SourceSpanReference:
    return SourceSpanReference(
        document_id="doc-financial-model",
        span_id="span-001",
        evidence_tags=["schedule"],
        locator={"sheet": "P&L", "cell": "A1"},
        text_excerpt="Revenue",
    )


def test_valid_ready_task_model() -> None:
    task = ExtractionTask(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        status=ExtractionTaskStatus.READY,
        methodology_id="financial_dd",
        methodology_version_id="financial_dd_v1",
        methodology_question_id="mq_financial_dd_revenue_quality_0001",
        methodology_type=MethodologyType.FINANCIAL_DD,
        methodology_section="P&L",
        document_id="doc-financial-model",
        classification_id="dc_financial_model",
        source_spans=[_span()],
        target_fdd_category=FddDocumentCategory.FINANCIAL_SCHEDULE_MODEL,
        target_cdd_category=CddDocumentCategory.BUSINESS_PLAN_ASSUMPTIONS,
        required_evidence=[RequiredEvidence(evidence_type="schedule", description="P&L schedule")],
        expected_answer_schema=_answer_schema(),
        validation_requirements=["cite source spans"],
        reason_codes=["ready"],
    )

    assert task.extraction_task_id.startswith("et_")
    assert task.source_span_ids == ["span-001"]
    assert task.blocker_reason is None


def test_deterministic_task_id_generation_sorts_source_spans() -> None:
    first = generate_extraction_task_id(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        methodology_question_id="mq_financial_dd_revenue_quality_0001",
        document_id="doc-financial-model",
        source_span_ids=["span-002", "span-001"],
        target_fdd_category=FddDocumentCategory.FINANCIAL_SCHEDULE_MODEL,
        target_cdd_category=CddDocumentCategory.BUSINESS_PLAN_ASSUMPTIONS,
        status=ExtractionTaskStatus.READY,
        blocker_reason=None,
    )
    second = generate_extraction_task_id(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        methodology_question_id="mq_financial_dd_revenue_quality_0001",
        document_id="doc-financial-model",
        source_span_ids=["span-001", "span-002"],
        target_fdd_category=FddDocumentCategory.FINANCIAL_SCHEDULE_MODEL,
        target_cdd_category=CddDocumentCategory.BUSINESS_PLAN_ASSUMPTIONS,
        status=ExtractionTaskStatus.READY,
        blocker_reason=None,
    )

    assert first == second
    assert first.startswith("et_")


def test_blank_ids_and_reason_codes_are_rejected() -> None:
    with pytest.raises(ValidationError):
        SourceSpanReference(document_id="doc-1", span_id=" ", locator={})

    with pytest.raises(ValidationError):
        SourceSpanReference(
            document_id="doc-1",
            span_id="span-001",
            evidence_tags=["schedule", " "],
            locator={},
        )

    with pytest.raises(ValidationError):
        ExtractionTask(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            extraction_task_id=" ",
            status=ExtractionTaskStatus.READY,
            methodology_id="financial_dd",
            methodology_version_id="financial_dd_v1",
            methodology_question_id="mq_financial_dd_revenue_quality_0001",
            methodology_type=MethodologyType.FINANCIAL_DD,
            methodology_section="P&L",
            document_id="doc-financial-model",
            classification_id="dc_financial_model",
            source_spans=[_span()],
            target_fdd_category=FddDocumentCategory.FINANCIAL_SCHEDULE_MODEL,
            target_cdd_category=CddDocumentCategory.BUSINESS_PLAN_ASSUMPTIONS,
            required_evidence=[
                RequiredEvidence(evidence_type="schedule", description="P&L schedule")
            ],
            expected_answer_schema=_answer_schema(),
            validation_requirements=["cite source spans"],
            reason_codes=["ready"],
        )

    payload = _answer_schema().model_dump(mode="json")
    payload["validation_requirements"] = [" "]
    with pytest.raises(ValidationError):
        ExpectedAnswerSchema.model_validate(payload)

    with pytest.raises(ValidationError):
        ExtractionTask(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            status=ExtractionTaskStatus.BLOCKED,
            blocker_reason=ExtractionTaskBlockerReason.NO_SOURCE_SPANS,
            methodology_id="financial_dd",
            methodology_version_id="financial_dd_v1",
            methodology_question_id="mq_financial_dd_revenue_quality_0001",
            methodology_type=MethodologyType.FINANCIAL_DD,
            methodology_section="P&L",
            document_id="doc-financial-model",
            classification_id="dc_financial_model",
            source_spans=[],
            target_fdd_category=FddDocumentCategory.FINANCIAL_SCHEDULE_MODEL,
            target_cdd_category=CddDocumentCategory.BUSINESS_PLAN_ASSUMPTIONS,
            required_evidence=[
                RequiredEvidence(evidence_type="schedule", description="P&L schedule")
            ],
            expected_answer_schema=_answer_schema(),
            validation_requirements=["cite source spans"],
            reason_codes=[" "],
        )


def test_ready_task_requires_source_spans() -> None:
    with pytest.raises(ValidationError):
        ExtractionTask(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            status=ExtractionTaskStatus.READY,
            methodology_id="financial_dd",
            methodology_version_id="financial_dd_v1",
            methodology_question_id="mq_financial_dd_revenue_quality_0001",
            methodology_type=MethodologyType.FINANCIAL_DD,
            methodology_section="P&L",
            document_id="doc-financial-model",
            classification_id="dc_financial_model",
            source_spans=[],
            target_fdd_category=FddDocumentCategory.FINANCIAL_SCHEDULE_MODEL,
            target_cdd_category=CddDocumentCategory.BUSINESS_PLAN_ASSUMPTIONS,
            required_evidence=[
                RequiredEvidence(evidence_type="schedule", description="P&L schedule")
            ],
            expected_answer_schema=_answer_schema(),
            validation_requirements=["cite source spans"],
            reason_codes=["ready"],
        )


def test_deterministic_serialization() -> None:
    task = ExtractionTask(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        status=ExtractionTaskStatus.READY,
        methodology_id="financial_dd",
        methodology_version_id="financial_dd_v1",
        methodology_question_id="mq_financial_dd_revenue_quality_0001",
        methodology_type=MethodologyType.FINANCIAL_DD,
        methodology_section="P&L",
        document_id="doc-financial-model",
        classification_id="dc_financial_model",
        source_spans=[_span()],
        target_fdd_category=FddDocumentCategory.FINANCIAL_SCHEDULE_MODEL,
        target_cdd_category=CddDocumentCategory.BUSINESS_PLAN_ASSUMPTIONS,
        required_evidence=[RequiredEvidence(evidence_type="schedule", description="P&L schedule")],
        expected_answer_schema=_answer_schema(),
        validation_requirements=["cite source spans"],
        reason_codes=["ready"],
    )

    assert task.to_deterministic_json() == task.to_deterministic_json()
    assert json.loads(task.to_deterministic_json())["extraction_task_id"].startswith("et_")
