"""Guard tests: run-level status values align with DB CHECK constraint.

The Postgres runs table (migration 0009) enforces:
    CHECK (status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED'))

These tests ensure the orchestrator never produces a run-level status
that violates this constraint. Step-level statuses (StepStatus) are
unchanged and are cross-checked for non-contamination.
"""

from __future__ import annotations

import uuid
from typing import Any

from idis.audit.sink import AuditSink
from idis.models.run_step import (
    RunStep,
    StepName,
    StepStatus,
)
from idis.services.runs.orchestrator import (
    RunContext,
    RunOrchestrator,
)

TENANT_A = str(uuid.uuid4())
DB_ALLOWED_RUN_STATUSES = {"QUEUED", "RUNNING", "SUCCEEDED", "FAILED"}


class InMemoryAuditSink(AuditSink):
    """Minimal audit sink for testing."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, event: dict[str, Any]) -> None:
        self.events.append(event)


class InMemoryRunStepsRepository:
    """Minimal in-memory RunStep repository for testing."""

    def __init__(self, tenant_id: str) -> None:
        self._tenant_id = tenant_id
        self._steps: dict[str, Any] = {}

    def create(self, step: Any) -> Any:
        self._steps[f"{step.run_id}:{step.step_name}"] = step
        return step

    def update(self, step: Any) -> Any:
        self._steps[f"{step.run_id}:{step.step_name}"] = step
        return step

    def get_step(self, run_id: str, step_name: StepName) -> Any:
        return self._steps.get(f"{run_id}:{step_name}")

    def get_by_run_id(self, run_id: str) -> list[Any]:
        return sorted(
            [s for s in self._steps.values() if s.run_id == run_id],
            key=lambda s: s.step_order,
        )


def _stub_extract(**kwargs: Any) -> dict[str, Any]:
    return {
        "status": "COMPLETED",
        "created_claim_ids": ["claim-001"],
        "chunk_count": 1,
        "unique_claim_count": 1,
    }


def _stub_grade(**kwargs: Any) -> dict[str, Any]:
    return {"graded_count": 1}


def _stub_calc(**kwargs: Any) -> dict[str, Any]:
    return {"calc_ids": ["calc-001"], "reproducibility_hashes": {"calc-001": "abc"}}


def _stub_enrichment(**kwargs: Any) -> dict[str, Any]:
    return {"provider_count": 1, "result_count": 1, "blocked_count": 0, "enrichment_refs": {}}


def _stub_debate(**kwargs: Any) -> dict[str, Any]:
    return {"stop_reason": "CONSENSUS", "muhasabah_passed": True}


def _stub_analysis(**kwargs: Any) -> dict[str, Any]:
    return {"agent_count": 1, "report_ids": ["r1"], "bundle_id": "b1"}


def _stub_scoring(**kwargs: Any) -> dict[str, Any]:
    return {"composite_score": 75.0, "band": "HIGH", "routing": "INVEST"}


def _stub_deliverables(**kwargs: Any) -> dict[str, Any]:
    return {"deliverable_count": 3, "types": ["SCREENING_SNAPSHOT", "IC_MEMO", "TRUTH_DASHBOARD"]}


def _failing_extract(**kwargs: Any) -> dict[str, Any]:
    raise ValueError("Simulated extraction failure")


def _make_orchestrator() -> tuple[RunOrchestrator, InMemoryAuditSink, InMemoryRunStepsRepository]:
    audit_sink = InMemoryAuditSink()
    repo = InMemoryRunStepsRepository(TENANT_A)
    orchestrator = RunOrchestrator(audit_sink=audit_sink, run_steps_repo=repo)
    return orchestrator, audit_sink, repo


def _make_snapshot_ctx(**overrides: Any) -> RunContext:
    defaults: dict[str, Any] = {
        "run_id": str(uuid.uuid4()),
        "tenant_id": TENANT_A,
        "deal_id": str(uuid.uuid4()),
        "mode": "SNAPSHOT",
        "documents": [{"doc_id": "d1", "content": "test"}],
        "extract_fn": _stub_extract,
        "grade_fn": _stub_grade,
        "calc_fn": _stub_calc,
    }
    defaults.update(overrides)
    return RunContext(**defaults)


def _make_full_ctx(**overrides: Any) -> RunContext:
    defaults: dict[str, Any] = {
        "run_id": str(uuid.uuid4()),
        "tenant_id": TENANT_A,
        "deal_id": str(uuid.uuid4()),
        "mode": "FULL",
        "documents": [{"doc_id": "d1", "content": "test"}],
        "extract_fn": _stub_extract,
        "grade_fn": _stub_grade,
        "calc_fn": _stub_calc,
        "enrich_fn": _stub_enrichment,
        "debate_fn": _stub_debate,
        "analysis_fn": _stub_analysis,
        "scoring_fn": _stub_scoring,
        "deliverables_fn": _stub_deliverables,
    }
    defaults.update(overrides)
    return RunContext(**defaults)


class TestRunStatusDBAlignment:
    """Guard tests ensuring run-level status conforms to DB CHECK constraint."""

    def test_run_success_returns_succeeded(self) -> None:
        """Run with all steps passing produces SUCCEEDED."""
        orchestrator, _, _ = _make_orchestrator()
        result = orchestrator.execute(_make_snapshot_ctx())

        assert result.status == "SUCCEEDED"

    def test_run_failure_returns_failed(self) -> None:
        """Run with a failing step produces FAILED."""
        orchestrator, _, _ = _make_orchestrator()
        result = orchestrator.execute(_make_snapshot_ctx(extract_fn=_failing_extract))

        assert result.status == "FAILED"

    def test_run_blocked_returns_failed_with_block_reason(self) -> None:
        """Run hitting an unimplemented step produces FAILED with block_reason."""
        import idis.services.runs.orchestrator as orch_mod

        original_implemented = orch_mod.IMPLEMENTED_STEPS
        orch_mod.IMPLEMENTED_STEPS = frozenset(
            s for s in original_implemented if s != StepName.CALC
        )
        try:
            orchestrator, _, _ = _make_orchestrator()
            result = orchestrator.execute(_make_snapshot_ctx())

            assert result.status == "FAILED"
            assert result.block_reason is not None
        finally:
            orch_mod.IMPLEMENTED_STEPS = original_implemented

    def test_run_status_never_completed(self) -> None:
        """Successful run never returns legacy 'COMPLETED' status."""
        orchestrator, _, _ = _make_orchestrator()
        result = orchestrator.execute(_make_full_ctx())

        assert result.status != "COMPLETED"

    def test_run_status_never_partial(self) -> None:
        """Mixed-result run never returns legacy 'PARTIAL' status."""
        orchestrator, _, _ = _make_orchestrator()
        result = orchestrator.execute(_make_snapshot_ctx(extract_fn=_failing_extract))

        assert result.status != "PARTIAL"

    def test_run_status_never_blocked(self) -> None:
        """Blocked run never returns legacy 'BLOCKED' status."""
        import idis.services.runs.orchestrator as orch_mod

        original_implemented = orch_mod.IMPLEMENTED_STEPS
        orch_mod.IMPLEMENTED_STEPS = frozenset(
            s for s in original_implemented if s != StepName.CALC
        )
        try:
            orchestrator, _, _ = _make_orchestrator()
            result = orchestrator.execute(_make_snapshot_ctx())

            assert result.status != "BLOCKED"
        finally:
            orch_mod.IMPLEMENTED_STEPS = original_implemented

    def test_step_status_still_uses_completed(self) -> None:
        """Successful steps still use StepStatus.COMPLETED (cross-contamination guard)."""
        orchestrator, _, _ = _make_orchestrator()
        result = orchestrator.execute(_make_snapshot_ctx())

        completed_steps = [s for s in result.steps if s.status == StepStatus.COMPLETED]
        assert len(completed_steps) == 4

    def test_compute_final_status_with_failed_step_returns_failed_string(self) -> None:
        """_compute_final_status returns exactly 'FAILED' (not 'PARTIAL') when a step failed."""
        failed_step = RunStep(
            step_id=str(uuid.uuid4()),
            run_id="run-1",
            tenant_id=TENANT_A,
            step_name=StepName.EXTRACT,
            step_order=1,
            status=StepStatus.FAILED,
        )
        passed_step = RunStep(
            step_id=str(uuid.uuid4()),
            run_id="run-1",
            tenant_id=TENANT_A,
            step_name=StepName.INGEST_CHECK,
            step_order=0,
            status=StepStatus.COMPLETED,
        )

        result = RunOrchestrator._compute_final_status([passed_step, failed_step])

        assert result == "FAILED"
        assert result != "PARTIAL"
        assert result in DB_ALLOWED_RUN_STATUSES

    def test_step_status_completed_value_is_completed_string(self) -> None:
        """StepStatus.COMPLETED.value is exactly 'COMPLETED', not 'SUCCEEDED'."""
        assert StepStatus.COMPLETED.value == "COMPLETED"
        assert StepStatus.COMPLETED.value != "SUCCEEDED"

        orchestrator, _, _ = _make_orchestrator()
        result = orchestrator.execute(_make_snapshot_ctx())

        completed_steps = [s for s in result.steps if s.status == StepStatus.COMPLETED]
        assert len(completed_steps) > 0
        for step in completed_steps:
            assert step.status.value == "COMPLETED"
            assert step.status.value != "SUCCEEDED"

    def test_db_constraint_whitelist_all_cases(self) -> None:
        """All orchestrator result statuses are in the DB-allowed whitelist."""
        orchestrator_success, _, _ = _make_orchestrator()
        orchestrator_fail, _, _ = _make_orchestrator()

        success_result = orchestrator_success.execute(_make_full_ctx())
        fail_result = orchestrator_fail.execute(_make_snapshot_ctx(extract_fn=_failing_extract))

        for result in [success_result, fail_result]:
            assert result.status in DB_ALLOWED_RUN_STATUSES, (
                f"Run status '{result.status}' not in DB constraint whitelist: "
                f"{DB_ALLOWED_RUN_STATUSES}"
            )
