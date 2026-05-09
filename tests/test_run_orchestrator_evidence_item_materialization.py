"""Run orchestrator tests for Slice 7 EvidenceItem materialization wiring."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from idis.audit.sink import InMemoryAuditSink
from idis.models.claim_materialization import (
    ClaimMaterializationStatus,
    MethodologyOutputClaimMaterializationRunResult,
    MethodologyOutputClaimMaterializationSummary,
)
from idis.models.evidence_item_materialization import RunScopedEvidenceItemShell
from idis.models.run_step import FULL_ONLY_STEPS, FULL_STEPS, SNAPSHOT_STEPS, StepName
from idis.persistence.repositories.run_steps import (
    InMemoryRunStepsRepository,
    clear_run_steps_store,
)
from idis.services.runs.orchestrator import RunOrchestrator, RunStepBlockedError
from tests.test_run_orchestrator_methodology_claim_materialization import (
    DEAL_ID,
    TENANT_ID,
    _ctx,
)


def setup_function() -> None:
    clear_run_steps_store()


def test_full_step_order_places_evidence_after_claim_materialization_before_extract() -> None:
    assert StepName.METHODOLOGY_EVIDENCE_ITEM_MATERIALIZATION in FULL_STEPS
    assert StepName.METHODOLOGY_EVIDENCE_ITEM_MATERIALIZATION in FULL_ONLY_STEPS
    assert StepName.METHODOLOGY_EVIDENCE_ITEM_MATERIALIZATION not in SNAPSHOT_STEPS
    assert FULL_STEPS.index(StepName.METHODOLOGY_CLAIM_MATERIALIZATION) < FULL_STEPS.index(
        StepName.METHODOLOGY_EVIDENCE_ITEM_MATERIALIZATION
    )
    assert FULL_STEPS.index(StepName.METHODOLOGY_EVIDENCE_ITEM_MATERIALIZATION) < FULL_STEPS.index(
        StepName.EXTRACT
    )


def test_full_run_materializes_evidence_items_from_slice6_claims() -> None:
    run_id = str(uuid.uuid4())
    orchestrator = RunOrchestrator(
        audit_sink=InMemoryAuditSink(),
        run_steps_repo=InMemoryRunStepsRepository(TENANT_ID),
    )
    ctx = _ctx(run_id)

    result = orchestrator.execute(ctx)

    assert result.status == "SUCCEEDED"
    assert len(ctx.methodology_evidence_items) == 1
    assert len(ctx.methodology_evidence_source_provenance) == 1
    evidence_steps = [
        step
        for step in result.steps
        if step.step_name == StepName.METHODOLOGY_EVIDENCE_ITEM_MATERIALIZATION
    ]
    assert len(evidence_steps) == 1
    summary = evidence_steps[0].result_summary
    serialized = json.dumps(summary, sort_keys=True)
    assert summary["summary"]["created_evidence_count"] == 1
    assert "evidence_ids" in summary
    assert "claim_mth_" in serialized
    assert "doc-financial-model" in serialized
    assert "span-001" in serialized
    assert "locator" not in serialized
    assert "claim_text" not in serialized
    assert "value_struct" not in serialized
    assert "sanad" not in serialized.lower()
    assert "truth_dashboard" not in serialized
    assert "calc_ids" not in serialized
    assert "deliverables" not in serialized


def test_explicit_empty_claim_materialization_result_is_evidence_noop() -> None:
    run_id = str(uuid.uuid4())
    orchestrator = RunOrchestrator(
        audit_sink=InMemoryAuditSink(),
        run_steps_repo=InMemoryRunStepsRepository(TENANT_ID),
    )
    ctx = _ctx(run_id)

    def empty_materialization(**kwargs: Any) -> Any:
        run_result = MethodologyOutputClaimMaterializationRunResult(
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            run_id=run_id,
            status=ClaimMaterializationStatus.COMPLETED,
            output_claim_mappings=[],
            rejected_outputs=[],
            summary=MethodologyOutputClaimMaterializationSummary(
                tenant_id=TENANT_ID,
                deal_id=DEAL_ID,
                run_id=run_id,
                total_outputs=0,
                created_claim_count=0,
                rejected_output_count=0,
                by_status={},
                by_reason={},
            ),
        )
        return run_result, []

    ctx.methodology_claim_materialization_fn = empty_materialization

    result = orchestrator.execute(ctx)

    assert result.status == "SUCCEEDED"
    assert ctx.methodology_evidence_items == []
    evidence_step = [
        step
        for step in result.steps
        if step.step_name == StepName.METHODOLOGY_EVIDENCE_ITEM_MATERIALIZATION
    ][0]
    assert evidence_step.result_summary["summary"]["created_evidence_count"] == 0


def test_missing_claim_materialization_context_blocks_evidence_step_cleanly() -> None:
    orchestrator = RunOrchestrator(
        audit_sink=InMemoryAuditSink(),
        run_steps_repo=InMemoryRunStepsRepository(TENANT_ID),
    )
    ctx = _ctx(str(uuid.uuid4()))

    with pytest.raises(RunStepBlockedError) as exc_info:
        orchestrator._execute_methodology_evidence_item_materialization(ctx)  # noqa: SLF001

    assert exc_info.value.code == "METHODOLOGY_MATERIALIZED_CLAIMS_MISSING"


def test_resume_skips_completed_evidence_materialization_and_rehydrates_shells() -> None:
    run_id = str(uuid.uuid4())
    repo = InMemoryRunStepsRepository(TENANT_ID)
    orchestrator = RunOrchestrator(audit_sink=InMemoryAuditSink(), run_steps_repo=repo)

    result1 = orchestrator.execute(_ctx(run_id))
    assert result1.status == "SUCCEEDED"

    call_count = {"evidence": 0}

    def failing_evidence_materialization(**kwargs: Any) -> Any:
        call_count["evidence"] += 1
        raise AssertionError("completed evidence step must not rerun")

    ctx2 = _ctx(run_id)
    ctx2.methodology_evidence_item_materialization_fn = failing_evidence_materialization
    result2 = orchestrator.execute(ctx2)

    assert result2.status == "SUCCEEDED"
    assert call_count["evidence"] == 0
    assert len(ctx2.methodology_evidence_items) == 1
    shell = ctx2.methodology_evidence_items[0]
    assert isinstance(shell, RunScopedEvidenceItemShell)
    assert shell.evidence_id
    assert shell.claim_id.startswith("claim_mth_")
