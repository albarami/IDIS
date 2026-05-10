"""Run orchestrator tests for Slice 6 claim materialization wiring."""

from __future__ import annotations

import uuid
from typing import Any

from idis.audit.sink import InMemoryAuditSink
from idis.methodology.models import MethodologyType, RequiredEvidence
from idis.models.extraction_execution import (
    MethodologyExtractionExecutionResult,
    MethodologyExtractionExecutionRunResult,
    MethodologyExtractionExecutionStatus,
    MethodologyTaskExecutionResult,
    MethodologyTaskExecutionStatus,
)
from idis.models.extraction_task import (
    ExpectedAnswerSchema,
    ExtractionTask,
    ExtractionTaskPlanningRunResult,
    ExtractionTaskStatus,
    SourceSpanReference,
)
from idis.models.run_step import FULL_ONLY_STEPS, FULL_STEPS, SNAPSHOT_STEPS, StepName
from idis.persistence.repositories.run_steps import (
    InMemoryRunStepsRepository,
    clear_run_steps_store,
)
from idis.services.runs.orchestrator import RunContext, RunOrchestrator
from tests.test_run_methodology_claim_materialization_service import _output

TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEAL_ID = "22222222-2222-2222-2222-222222222222"


def _documents() -> list[dict[str, Any]]:
    return [
        {
            "document_id": "doc-financial-model",
            "doc_type": "PDF",
            "document_name": "financial_model.pdf",
            "spans": [
                {
                    "span_id": "span-001",
                    "text_excerpt": "Revenue was $10M in FY2024.",
                    "locator": {"sheet": "P&L", "cell": "B12"},
                    "span_type": "PAGE_TEXT",
                }
            ],
        }
    ]


def _stub_extract(**kwargs: Any) -> dict[str, Any]:
    return {"status": "COMPLETED", "created_claim_ids": []}


def _stub_grade(**kwargs: Any) -> dict[str, Any]:
    return {"graded_count": 0, "failed_count": 0, "total_defects": 0, "all_failed": False}


def _stub_calc(**kwargs: Any) -> dict[str, Any]:
    return {"calc_ids": [], "reproducibility_hashes": []}


def _empty_stub(**kwargs: Any) -> dict[str, Any]:
    return {}


def _planning_fn(**kwargs: Any) -> tuple[ExtractionTaskPlanningRunResult, list[ExtractionTask]]:
    run_id = str(kwargs["run_id"])
    task = ExtractionTask(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=run_id,
        status=ExtractionTaskStatus.READY,
        reason_codes=["ready"],
        methodology_id="financial_dd",
        methodology_version_id="financial_dd_v1",
        methodology_question_id="mq_financial_dd_revenue_quality_0001",
        methodology_type=MethodologyType.FINANCIAL_DD,
        methodology_section="Revenue Quality",
        coverage_record_id="mcr_revenue_quality",
        document_id="doc-financial-model",
        classification_id="classification-financial-model",
        source_spans=[
            SourceSpanReference(
                document_id="doc-financial-model",
                span_id="span-001",
                locator={"sheet": "P&L", "cell": "B12"},
            )
        ],
        required_evidence=[
            RequiredEvidence(
                evidence_type="financial_model",
                description="Revenue schedule",
            )
        ],
        expected_answer_schema=ExpectedAnswerSchema(
            answer_type="numeric",
            question_text="What is revenue?",
            required_evidence=[
                RequiredEvidence(
                    evidence_type="financial_model",
                    description="Revenue schedule",
                )
            ],
            validation_requirements=["must cite source span"],
            report_section="Financial DD",
            methodology_type=MethodologyType.FINANCIAL_DD,
            methodology_section="Revenue Quality",
        ),
        validation_requirements=["must cite source span"],
    )
    return (
        ExtractionTaskPlanningRunResult.from_tasks(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=run_id,
            tasks=[task],
        ),
        [task],
    )


def _execution_fn(**kwargs: Any) -> Any:
    run_id = str(kwargs["run_id"])
    output = _output()
    output = output.model_copy(update={"run_id": run_id})
    task_result = MethodologyTaskExecutionResult(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=run_id,
        extraction_task_id="et_revenue_quality",
        methodology_question_id="mq_financial_dd_revenue_quality_0001",
        coverage_record_id="mcr_revenue_quality",
        status=MethodologyTaskExecutionStatus.COMPLETED,
        accepted_outputs=[output],
        rejected_outputs=[],
        reason=None,
        reason_codes=["completed"],
        source_span_ids=["span-001"],
    )
    run_result = MethodologyExtractionExecutionRunResult.from_task_results(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=run_id,
        task_results=[task_result],
    )
    execution_result = MethodologyExtractionExecutionResult(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=run_id,
        status=MethodologyExtractionExecutionStatus.COMPLETED,
        task_results=[task_result],
        accepted_outputs=[output],
        summary=run_result.summary,
    )
    return run_result, execution_result


def _missing_execution_result_fn(**kwargs: Any) -> Any:
    run_id = str(kwargs["run_id"])
    task_result = MethodologyTaskExecutionResult(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=run_id,
        extraction_task_id="et_revenue_quality",
        methodology_question_id="mq_financial_dd_revenue_quality_0001",
        coverage_record_id="mcr_revenue_quality",
        status=MethodologyTaskExecutionStatus.COMPLETED,
        accepted_outputs=[],
        rejected_outputs=[],
        reason=None,
        reason_codes=["completed"],
        source_span_ids=["span-001"],
    )
    return (
        MethodologyExtractionExecutionRunResult.from_task_results(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=run_id,
            task_results=[task_result],
        ),
        None,
    )


def _ctx(run_id: str) -> RunContext:
    return RunContext(
        run_id=run_id,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        mode="FULL",
        documents=_documents(),
        deal_metadata={
            "tenant_id": TENANT_ID,
            "deal_id": DEAL_ID,
            "company_name": "Acme Corp",
        },
        extract_fn=_stub_extract,
        grade_fn=_stub_grade,
        calc_fn=_stub_calc,
        enrich_fn=_empty_stub,
        debate_fn=_empty_stub,
        analysis_fn=_empty_stub,
        scoring_fn=_empty_stub,
        deliverables_fn=_empty_stub,
        methodology_extraction_task_planning_fn=_planning_fn,
        methodology_extraction_task_execution_fn=_execution_fn,
    )


def setup_function() -> None:
    clear_run_steps_store()


def test_full_step_order_places_claim_materialization_between_execution_and_extract() -> None:
    assert StepName.METHODOLOGY_CLAIM_MATERIALIZATION in FULL_STEPS
    assert StepName.METHODOLOGY_CLAIM_MATERIALIZATION in FULL_ONLY_STEPS
    assert StepName.METHODOLOGY_CLAIM_MATERIALIZATION not in SNAPSHOT_STEPS
    assert FULL_STEPS.index(StepName.METHODOLOGY_EXTRACTION_TASK_EXECUTION) < FULL_STEPS.index(
        StepName.METHODOLOGY_CLAIM_MATERIALIZATION
    )
    assert FULL_STEPS.index(StepName.METHODOLOGY_CLAIM_MATERIALIZATION) < FULL_STEPS.index(
        StepName.EXTRACT
    )


def test_full_run_materializes_claims_from_neutral_execution_outputs() -> None:
    run_id = str(uuid.uuid4())
    orchestrator = RunOrchestrator(
        audit_sink=InMemoryAuditSink(),
        run_steps_repo=InMemoryRunStepsRepository(TENANT_ID),
    )
    ctx = _ctx(run_id)

    result = orchestrator.execute(ctx)

    assert result.status == "SUCCEEDED"
    assert len(ctx.methodology_materialized_claims) == 1
    materialization_steps = [
        step
        for step in result.steps
        if step.step_name == StepName.METHODOLOGY_CLAIM_MATERIALIZATION
    ]
    assert len(materialization_steps) == 1
    summary = materialization_steps[0].result_summary
    assert summary["summary"]["created_claim_count"] == 1
    assert "claim_ids" in summary


def test_resume_skips_completed_materialization_and_rehydrates_claim_shells() -> None:
    run_id = str(uuid.uuid4())
    repo = InMemoryRunStepsRepository(TENANT_ID)
    orchestrator = RunOrchestrator(audit_sink=InMemoryAuditSink(), run_steps_repo=repo)

    result1 = orchestrator.execute(_ctx(run_id))
    assert result1.status == "SUCCEEDED"

    call_count = {"materialization": 0}

    def failing_materialization(**kwargs: Any) -> Any:
        call_count["materialization"] += 1
        raise AssertionError("completed materialization step must not rerun")

    ctx2 = _ctx(run_id)
    ctx2.methodology_claim_materialization_fn = failing_materialization
    result2 = orchestrator.execute(ctx2)

    assert result2.status == "SUCCEEDED"
    assert call_count["materialization"] == 0
    assert len(ctx2.methodology_materialized_claims) == 1
    shell = ctx2.methodology_materialized_claims[0]
    assert shell.claim_id.startswith("claim_mth_")
    assert shell.extraction_output_id.startswith("meo_")


def test_missing_execution_result_fails_materialization_step_cleanly() -> None:
    run_id = str(uuid.uuid4())
    orchestrator = RunOrchestrator(
        audit_sink=InMemoryAuditSink(),
        run_steps_repo=InMemoryRunStepsRepository(TENANT_ID),
    )
    ctx = _ctx(run_id)
    ctx.methodology_extraction_task_execution_fn = _missing_execution_result_fn

    result = orchestrator.execute(ctx)

    assert result.status == "FAILED"
    assert result.error_code == "METHODOLOGY_EXECUTION_RESULT_MISSING"
    failed_steps = [step for step in result.steps if step.error_code]
    assert len(failed_steps) == 1
    assert failed_steps[0].step_name == StepName.METHODOLOGY_CLAIM_MATERIALIZATION
