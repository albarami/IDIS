"""Run orchestrator tests for Slice 9 deterministic calculation wiring."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from idis.audit.sink import InMemoryAuditSink
from idis.models.calc_materialization import RunScopedCalculationShell
from idis.models.run_step import FULL_ONLY_STEPS, FULL_STEPS, SNAPSHOT_STEPS, StepName
from idis.persistence.repositories.run_steps import (
    InMemoryRunStepsRepository,
    clear_run_steps_store,
)
from idis.services.runs.orchestrator import RunOrchestrator, RunStepBlockedError
from tests.test_run_methodology_claim_materialization_service import TENANT_ID
from tests.test_run_methodology_deterministic_calculation_service import (
    _claim,
    _grade,
    _sanad_record,
    _task,
)
from tests.test_run_orchestrator_methodology_claim_materialization import _ctx


def setup_function() -> None:
    clear_run_steps_store()


def _claim_for_run(claim_id: str, label: str, value: str, run_id: str) -> Any:
    return _claim(claim_id, label, value).model_copy(update={"run_id": run_id})


def _grade_for_run(claim_id: str, run_id: str) -> Any:
    return _grade(claim_id).model_copy(update={"run_id": run_id})


def _sanad_for_run(claim_id: str, run_id: str) -> Any:
    return _sanad_record(claim_id).model_copy(update={"run_id": run_id})


def _task_for_run(run_id: str, *, required: bool = True) -> Any:
    return _task(required=required).model_copy(update={"run_id": run_id})


def test_full_step_order_places_deterministic_calc_after_sanad_before_extract() -> None:
    assert StepName.METHODOLOGY_DETERMINISTIC_CALCULATION in FULL_STEPS
    assert StepName.METHODOLOGY_DETERMINISTIC_CALCULATION in FULL_ONLY_STEPS
    assert StepName.METHODOLOGY_DETERMINISTIC_CALCULATION not in SNAPSHOT_STEPS
    assert FULL_STEPS.index(StepName.METHODOLOGY_SANAD_CREATION_LINKING_GRADING) < FULL_STEPS.index(
        StepName.METHODOLOGY_DETERMINISTIC_CALCULATION
    )
    assert FULL_STEPS.index(StepName.METHODOLOGY_DETERMINISTIC_CALCULATION) < FULL_STEPS.index(
        StepName.EXTRACT
    )


def test_deterministic_calc_step_consumes_expected_answer_schema_requirements() -> None:
    run_id = str(uuid.uuid4())
    ctx = _ctx(run_id)
    ctx.methodology_materialized_claims = [
        _claim_for_run("claim_mth_revenue", "revenue", "1000", run_id),
        _claim_for_run("claim_mth_cogs", "cogs", "400", run_id),
    ]
    ctx.methodology_sanad_grades = [
        _grade_for_run("claim_mth_revenue", run_id),
        _grade_for_run("claim_mth_cogs", run_id),
    ]
    ctx.methodology_sanads = [
        _sanad_for_run("claim_mth_revenue", run_id),
        _sanad_for_run("claim_mth_cogs", run_id),
    ]
    ctx.methodology_extraction_tasks = [_task_for_run(run_id)]
    orchestrator = RunOrchestrator(
        audit_sink=InMemoryAuditSink(),
        run_steps_repo=InMemoryRunStepsRepository(TENANT_ID),
    )

    summary = orchestrator._execute_methodology_deterministic_calculation(ctx)  # noqa: SLF001

    assert len(ctx.methodology_calculations) == 1
    assert len(ctx.methodology_calc_sanads) == 1
    assert summary["summary"]["created_calculation_count"] == 1
    assert summary["calculation_mappings"][0]["calc_type"] == "GROSS_MARGIN"


def test_missing_claim_or_sanad_context_blocks_deterministic_calc_step_cleanly() -> None:
    orchestrator = RunOrchestrator(
        audit_sink=InMemoryAuditSink(),
        run_steps_repo=InMemoryRunStepsRepository(TENANT_ID),
    )
    missing_claims_ctx = _ctx(str(uuid.uuid4()))
    missing_claims_ctx.methodology_materialized_claims = None
    missing_claims_ctx.methodology_extraction_tasks = [_task_for_run(missing_claims_ctx.run_id)]

    with pytest.raises(RunStepBlockedError) as missing_claims:
        orchestrator._execute_methodology_deterministic_calculation(missing_claims_ctx)  # noqa: SLF001

    missing_grades_ctx = _ctx(str(uuid.uuid4()))
    missing_grades_ctx.methodology_materialized_claims = [
        _claim_for_run("claim_mth_revenue", "revenue", "1000", missing_grades_ctx.run_id)
    ]
    missing_grades_ctx.methodology_sanad_grades = None
    missing_grades_ctx.methodology_extraction_tasks = [_task_for_run(missing_grades_ctx.run_id)]

    with pytest.raises(RunStepBlockedError) as missing_grades:
        orchestrator._execute_methodology_deterministic_calculation(missing_grades_ctx)  # noqa: SLF001

    assert missing_claims.value.code == "METHODOLOGY_MATERIALIZED_CLAIMS_MISSING"
    assert missing_grades.value.code == "METHODOLOGY_SANAD_GRADES_MISSING"


def test_required_calculation_rejection_fails_step_with_safe_summary() -> None:
    run_id = str(uuid.uuid4())
    ctx = _ctx(run_id)
    ctx.methodology_materialized_claims = [
        _claim_for_run("claim_mth_revenue", "revenue", "1000", run_id)
    ]
    ctx.methodology_sanad_grades = [_grade_for_run("claim_mth_revenue", run_id)]
    ctx.methodology_extraction_tasks = [_task_for_run(run_id)]
    orchestrator = RunOrchestrator(
        audit_sink=InMemoryAuditSink(),
        run_steps_repo=InMemoryRunStepsRepository(TENANT_ID),
    )

    with pytest.raises(RunStepBlockedError) as blocked:
        orchestrator._execute_methodology_deterministic_calculation(ctx)  # noqa: SLF001

    serialized = json.dumps(blocked.value.result_summary, sort_keys=True)
    assert blocked.value.code == "METHODOLOGY_DETERMINISTIC_CALCULATION_FAILED"
    assert "missing_required_claim" in serialized
    assert "claim_text" not in serialized
    assert "value_struct" not in serialized


def test_resume_skips_completed_deterministic_calc_step_and_rehydrates_safe_shells() -> None:
    run_id = str(uuid.uuid4())
    repo = InMemoryRunStepsRepository(TENANT_ID)
    orchestrator = RunOrchestrator(audit_sink=InMemoryAuditSink(), run_steps_repo=repo)
    ctx = _ctx(run_id)
    ctx.methodology_materialized_claims = [
        _claim_for_run("claim_mth_revenue", "revenue", "1000", run_id),
        _claim_for_run("claim_mth_cogs", "cogs", "400", run_id),
    ]
    ctx.methodology_sanad_grades = [
        _grade_for_run("claim_mth_revenue", run_id),
        _grade_for_run("claim_mth_cogs", run_id),
    ]
    ctx.methodology_sanads = [
        _sanad_for_run("claim_mth_revenue", run_id),
        _sanad_for_run("claim_mth_cogs", run_id),
    ]
    ctx.methodology_extraction_tasks = [_task_for_run(run_id)]

    first_summary = orchestrator._execute_methodology_deterministic_calculation(ctx)  # noqa: SLF001
    step = orchestrator._start_step(ctx, StepName.METHODOLOGY_DETERMINISTIC_CALCULATION, None)  # noqa: SLF001
    orchestrator._complete_step(step, first_summary)  # noqa: SLF001

    call_count = {"calc": 0}

    def failing_calc_materialization(**kwargs: Any) -> Any:
        call_count["calc"] += 1
        raise AssertionError("completed deterministic calc step must not rerun")

    ctx2 = _ctx(run_id)
    ctx2.methodology_deterministic_calculation_fn = failing_calc_materialization
    result = orchestrator.execute(ctx2)

    assert result.status == "SUCCEEDED"
    assert call_count["calc"] == 0
    assert len(ctx2.methodology_calculations) == 1
    assert isinstance(ctx2.methodology_calculations[0], RunScopedCalculationShell)
