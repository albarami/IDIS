"""Run orchestrator tests for Slice 8 Sanad creation/linking/grading wiring."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from idis.audit.sink import InMemoryAuditSink
from idis.models.run_step import FULL_ONLY_STEPS, FULL_STEPS, SNAPSHOT_STEPS, StepName
from idis.models.sanad_materialization import RunScopedSanadShell
from idis.persistence.repositories.run_steps import (
    InMemoryRunStepsRepository,
    clear_run_steps_store,
)
from idis.services.runs.orchestrator import RunOrchestrator, RunStepBlockedError
from tests.test_run_orchestrator_methodology_claim_materialization import TENANT_ID, _ctx


def setup_function() -> None:
    clear_run_steps_store()


def test_full_step_order_places_sanad_after_evidence_before_extract() -> None:
    assert StepName.METHODOLOGY_SANAD_CREATION_LINKING_GRADING in FULL_STEPS
    assert StepName.METHODOLOGY_SANAD_CREATION_LINKING_GRADING in FULL_ONLY_STEPS
    assert StepName.METHODOLOGY_SANAD_CREATION_LINKING_GRADING not in SNAPSHOT_STEPS
    assert FULL_STEPS.index(StepName.METHODOLOGY_EVIDENCE_ITEM_MATERIALIZATION) < FULL_STEPS.index(
        StepName.METHODOLOGY_SANAD_CREATION_LINKING_GRADING
    )
    assert FULL_STEPS.index(StepName.METHODOLOGY_SANAD_CREATION_LINKING_GRADING) < FULL_STEPS.index(
        StepName.EXTRACT
    )


def test_full_run_creates_links_grades_and_defect_outputs() -> None:
    run_id = str(uuid.uuid4())
    orchestrator = RunOrchestrator(
        audit_sink=InMemoryAuditSink(),
        run_steps_repo=InMemoryRunStepsRepository(TENANT_ID),
    )
    ctx = _ctx(run_id)

    result = orchestrator.execute(ctx)

    assert result.status == "SUCCEEDED"
    assert len(ctx.methodology_sanads) == 1
    assert len(ctx.methodology_sanad_links) == 1
    assert len(ctx.methodology_sanad_grades) == 1
    assert ctx.methodology_sanad_defects is not None
    sanad_steps = [
        step
        for step in result.steps
        if step.step_name == StepName.METHODOLOGY_SANAD_CREATION_LINKING_GRADING
    ]
    assert len(sanad_steps) == 1
    summary = sanad_steps[0].result_summary
    serialized = json.dumps(summary, sort_keys=True)
    assert summary["summary"]["created_sanad_count"] == 1
    assert "sanad_ids" in summary
    assert "claim_mth_" in serialized
    assert "evidence_ids" in serialized
    assert "claim_text" not in serialized
    assert "value_struct" not in serialized
    assert "document_name" not in serialized
    assert "locator" not in serialized
    assert "input_refs" not in serialized
    assert "output_refs" not in serialized
    assert "grade_explanation" not in serialized
    assert "description" not in serialized
    assert "truth_dashboard" not in serialized
    assert "validated_evidence_package" not in serialized
    assert "go_no_go" not in serialized


def test_missing_claim_or_evidence_context_blocks_sanad_step_cleanly() -> None:
    orchestrator = RunOrchestrator(
        audit_sink=InMemoryAuditSink(),
        run_steps_repo=InMemoryRunStepsRepository(TENANT_ID),
    )
    missing_claims_ctx = _ctx(str(uuid.uuid4()))
    missing_claims_ctx.methodology_materialized_claims = None

    with pytest.raises(RunStepBlockedError) as missing_claims:
        orchestrator._execute_methodology_sanad_creation_linking_grading(  # noqa: SLF001
            missing_claims_ctx
        )

    missing_evidence_ctx = _ctx(str(uuid.uuid4()))
    missing_evidence_ctx.methodology_materialized_claims = []
    missing_evidence_ctx.methodology_evidence_items = None

    with pytest.raises(RunStepBlockedError) as missing_evidence:
        orchestrator._execute_methodology_sanad_creation_linking_grading(  # noqa: SLF001
            missing_evidence_ctx
        )

    assert missing_claims.value.code == "METHODOLOGY_MATERIALIZED_CLAIMS_MISSING"
    assert missing_evidence.value.code == "METHODOLOGY_EVIDENCE_ITEMS_MISSING"


def test_resume_skips_completed_sanad_step_and_rehydrates_safe_shells() -> None:
    run_id = str(uuid.uuid4())
    repo = InMemoryRunStepsRepository(TENANT_ID)
    orchestrator = RunOrchestrator(audit_sink=InMemoryAuditSink(), run_steps_repo=repo)

    first = orchestrator.execute(_ctx(run_id))
    assert first.status == "SUCCEEDED"

    call_count = {"sanad": 0}

    def failing_sanad_materialization(**kwargs: Any) -> Any:
        call_count["sanad"] += 1
        raise AssertionError("completed Sanad step must not rerun")

    ctx2 = _ctx(run_id)
    ctx2.methodology_sanad_creation_linking_grading_fn = failing_sanad_materialization
    second = orchestrator.execute(ctx2)

    assert second.status == "SUCCEEDED"
    assert call_count["sanad"] == 0
    assert len(ctx2.methodology_sanads) == 1
    shell = ctx2.methodology_sanads[0]
    assert isinstance(shell, RunScopedSanadShell)
    assert shell.sanad_id
    assert shell.defect_ids is not None
