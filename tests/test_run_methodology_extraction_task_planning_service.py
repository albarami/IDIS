"""Tests for run-scoped methodology extraction task planning."""

from __future__ import annotations

import json

import pytest

from idis.methodology.models import (
    AssignedAgent,
    MethodologyQuestion,
    MethodologyRegistry,
    MethodologySourceTrace,
    MethodologyType,
    MethodologyVersion,
    ReportMapping,
    RequiredEvidence,
)
from idis.models.document_classification import (
    DocumentSupportStatus,
    DocumentTriageStatus,
    FddDocumentCategory,
)
from idis.models.extraction_task import ExtractionTaskBlockerReason, ExtractionTaskStatus
from idis.models.methodology_coverage import MethodologyCoverageRecord
from idis.services.extraction.task_planner import InMemoryExtractionTaskPlanner
from idis.services.runs.methodology_extraction_task_planning import (
    InMemoryRunMethodologyExtractionTaskPlanningService,
    MethodologyExtractionTaskPlanningInputError,
)

TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEAL_ID = "22222222-2222-2222-2222-222222222222"
RUN_ID = "33333333-3333-3333-3333-333333333333"


def _question(
    *,
    question_id: str = "mq_financial_dd_revenue_quality_0001",
    target_categories: list[str] | None = None,
) -> MethodologyQuestion:
    return MethodologyQuestion(
        methodology_id="financial_dd",
        methodology_version_id="financial_dd_v1",
        methodology_question_id=question_id,
        methodology_type=MethodologyType.FINANCIAL_DD,
        section="Highly Sensitive Arbitrary Section",
        sheet_or_source_section="Highly Sensitive Arbitrary Section",
        source_row_number=2,
        question_text="Explain raw sensitive revenue quality.",
        required_evidence=[RequiredEvidence(evidence_type="schedule", description="schedule")],
        target_document_categories=target_categories or ["financial_schedule_model"],
        assigned_agents=[AssignedAgent(role="financial_analyst", responsibility="Assess revenue")],
        report_mapping=ReportMapping(report_section="Financial Due Diligence"),
        validation_requirements=["cite spans"],
        source_trace=MethodologySourceTrace(
            source_type="synthetic",
            source_name="synthetic_methodology",
            source_hash="0" * 64,
            sheet_or_section="Highly Sensitive Arbitrary Section",
            row_number=2,
        ),
    )


def _registry(questions: list[MethodologyQuestion] | None = None) -> MethodologyRegistry:
    questions = questions or [_question()]
    return MethodologyRegistry(
        methodology_id="financial_dd",
        methodology_type=MethodologyType.FINANCIAL_DD,
        versions=[
            MethodologyVersion(
                methodology_id="financial_dd",
                methodology_version_id="financial_dd_v1",
                methodology_type=MethodologyType.FINANCIAL_DD,
                version_label="v1",
                source_hash="1" * 64,
                source_name="synthetic_methodology",
                questions=questions,
            )
        ],
    )


def _coverage_record(
    *,
    question_id: str = "mq_financial_dd_revenue_quality_0001",
) -> MethodologyCoverageRecord:
    return MethodologyCoverageRecord(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        methodology_id="financial_dd",
        methodology_version_id="financial_dd_v1",
        methodology_question_id=question_id,
        methodology_type=MethodologyType.FINANCIAL_DD,
        section="Highly Sensitive Arbitrary Section",
    )


def _preflight_summary(
    *,
    usable: bool = True,
    fdd_category: str = FddDocumentCategory.FINANCIAL_SCHEDULE_MODEL.value,
    evidence_tags: list[str] | None = None,
) -> dict[str, object]:
    return {
        "status": "COMPLETED",
        "eligible_document_ids": ["doc-financial-model"] if usable else [],
        "blocked_document_ids": [] if usable else ["doc-financial-model"],
        "classifications": [
            {
                "classification_id": "dc_doc_financial_model",
                "document_id": "doc-financial-model",
                "support_status": DocumentSupportStatus.PARTIALLY_SUPPORTED.value,
                "triage_status": DocumentTriageStatus.PARTIAL.value,
                "reason": "PARTIAL_SUPPORT",
                "reason_codes": ["partial"],
                "warning_codes": ["warning-safe"],
                "fdd_category": fdd_category,
                "cdd_category": None,
                "methodology_target_areas": ["Highly Sensitive Arbitrary Section"],
                "usable_for_methodology_extraction": usable,
            }
        ],
        "source_spans_by_document_id": {
            "doc-financial-model": [
                {
                    "span_id": "span-001",
                    "document_id": "doc-financial-model",
                    "locator": {"sheet": "P&L", "cell": "A1"},
                    "span_type": "CELL",
                    "content_hash": "b" * 64,
                    "text_excerpt": "Raw revenue text must not survive reconstruction.",
                    "evidence_tags": evidence_tags or ["schedule"],
                }
            ]
        },
    }


def test_reconstructs_safe_planner_inputs_and_links_coverage_records() -> None:
    registry = _registry()
    coverage = _coverage_record()

    result, tasks = InMemoryRunMethodologyExtractionTaskPlanningService().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        registry=registry,
        coverage_records=[coverage],
        document_preflight_summary=_preflight_summary(),
    )

    assert len(tasks) == 1
    task = tasks[0]
    assert task.status == ExtractionTaskStatus.READY
    assert task.coverage_record_id == coverage.coverage_record_id
    assert task.source_spans[0].span_type == "CELL"
    assert task.source_spans[0].content_hash == "b" * 64
    assert task.source_spans[0].text_excerpt is None
    assert result.task_ids == [task.extraction_task_id]


def test_reconstructed_planner_input_contains_no_fabricated_classification_evidence() -> None:
    class RecordingPlanner:
        captured_classifications: list[object]

        def plan_tasks(self, **kwargs: object) -> object:
            classifications = kwargs["classifications"]
            assert isinstance(classifications, list)
            self.captured_classifications = classifications
            return InMemoryExtractionTaskPlanner().plan_tasks(**kwargs)

    planner = RecordingPlanner()

    _result, tasks = InMemoryRunMethodologyExtractionTaskPlanningService(
        planner=planner,
    ).run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        registry=_registry(),
        coverage_records=[_coverage_record()],
        document_preflight_summary=_preflight_summary(),
    )

    assert tasks[0].status == ExtractionTaskStatus.READY
    assert planner.captured_classifications
    assert all(
        not getattr(classification, "evidence", [])
        for classification in planner.captured_classifications
    )
    assert "Safe run preflight summary" not in repr(planner.captured_classifications)


def test_run_step_summary_excludes_registry_and_document_text() -> None:
    result, _tasks = InMemoryRunMethodologyExtractionTaskPlanningService().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        registry=_registry(),
        coverage_records=[_coverage_record()],
        document_preflight_summary=_preflight_summary(),
    )

    serialized = json.dumps(result.to_run_step_summary(), sort_keys=True)
    assert "Raw revenue text" not in serialized
    assert "Explain raw sensitive revenue quality" not in serialized
    assert "Highly Sensitive Arbitrary Section" not in serialized
    assert "text_excerpt" not in serialized
    assert "question_text" not in serialized
    assert "expected_answer_schema" not in serialized


def test_all_blocked_plan_returns_diagnostic_tasks() -> None:
    result, tasks = InMemoryRunMethodologyExtractionTaskPlanningService().run(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        registry=_registry(),
        coverage_records=[_coverage_record()],
        document_preflight_summary=_preflight_summary(
            fdd_category=FddDocumentCategory.MARKET_RESEARCH.value
        ),
    )

    assert len(tasks) == 1
    assert tasks[0].status == ExtractionTaskStatus.BLOCKED
    assert tasks[0].blocker_reason == ExtractionTaskBlockerReason.NO_MATCHING_DOCUMENT_CATEGORY
    assert result.summary.by_status == {"blocked": 1}


def test_missing_coverage_records_fails_with_stable_input_error() -> None:
    with pytest.raises(MethodologyExtractionTaskPlanningInputError) as exc_info:
        InMemoryRunMethodologyExtractionTaskPlanningService().run(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            registry=_registry(),
            coverage_records=[],
            document_preflight_summary=_preflight_summary(),
        )

    assert exc_info.value.code == "METHODOLOGY_EXTRACTION_TASK_PLANNING_INPUT_INVALID"


def test_partial_coverage_records_fail_closed_with_stable_input_error() -> None:
    registry = _registry(
        [
            _question(question_id="mq_financial_dd_revenue_quality_0001"),
            _question(question_id="mq_financial_dd_revenue_quality_0002"),
        ]
    )

    with pytest.raises(MethodologyExtractionTaskPlanningInputError) as exc_info:
        InMemoryRunMethodologyExtractionTaskPlanningService().run(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            registry=registry,
            coverage_records=[_coverage_record(question_id="mq_financial_dd_revenue_quality_0001")],
            document_preflight_summary=_preflight_summary(),
        )

    assert exc_info.value.code == "METHODOLOGY_EXTRACTION_TASK_PLANNING_INPUT_INVALID"


def test_source_span_document_mismatch_fails_with_stable_input_error() -> None:
    preflight_summary = _preflight_summary()
    spans_by_document = preflight_summary["source_spans_by_document_id"]
    assert isinstance(spans_by_document, dict)
    raw_spans = spans_by_document["doc-financial-model"]
    assert isinstance(raw_spans, list)
    raw_spans[0]["document_id"] = "doc-other"

    with pytest.raises(MethodologyExtractionTaskPlanningInputError) as exc_info:
        InMemoryRunMethodologyExtractionTaskPlanningService().run(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=RUN_ID,
            registry=_registry(),
            coverage_records=[_coverage_record()],
            document_preflight_summary=preflight_summary,
        )

    assert exc_info.value.code == "METHODOLOGY_EXTRACTION_TASK_PLANNING_INPUT_INVALID"
