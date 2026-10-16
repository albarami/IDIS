"""RED-first production-root contracts for Task 7 lifecycle composition."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from idis.audit.sink import InMemoryAuditSink
from idis.models.run_step import RunStep, StepName, StepStatus
from idis.pipeline import contract_registry
from idis.pipeline import worker as worker_module
from idis.services.runs import execution as execution_module
from idis.services.runs.execution import RunExecutionService
from idis.services.runs.orchestrator import RunContext, RunOrchestrator

TENANT_ID = "11111111-1111-4111-8111-111111111111"
DEAL_ID = "22222222-2222-4222-8222-222222222222"
RUN_ID = "33333333-3333-4333-8333-333333333333"
OWNER_ID = "44444444-4444-4444-8444-444444444444"
CONTRACT = contract_registry.PIPELINE_CONTRACTS["full-strict-v21"]


def _steps() -> list[RunStep]:
    return [
        RunStep(
            step_id=f"00000000-0000-4000-8000-{spec.step_order:012d}",
            tenant_id=TENANT_ID,
            run_id=RUN_ID,
            step_name=StepName(spec.step_name),
            step_order=spec.step_order,
            status=StepStatus.COMPLETED,
            pipeline_contract_version="full-strict-v21",
            pipeline_contract_hash=CONTRACT.contract_hash,
            semantic_rule_version=spec.semantic_rule_version,
            semantic_rule_hash=spec.semantic_rule_hash,
        )
        for spec in CONTRACT.steps
    ]


def _context() -> RunContext:
    return RunContext(
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        mode="FULL",
        documents=[],
        extract_fn=lambda **_kwargs: {},
        grade_fn=lambda **_kwargs: {},
        pipeline_contract_version="full-strict-v21",
        pipeline_contract_hash=CONTRACT.contract_hash,
        pipeline_resume_state="RESUMABLE",
        lease_owner_id=OWNER_ID,
        lease_fencing_token=7,
    )


@pytest.mark.parametrize("bad_status", (StepStatus.PENDING, StepStatus.RUNNING))
def test_strict_final_status_rejects_any_nonterminal_step(
    bad_status: StepStatus,
) -> None:
    steps = _steps()
    steps[13] = steps[13].model_copy(update={"status": bad_status})

    assert (
        RunOrchestrator._compute_final_status(
            steps,
            expected_step_sequence=tuple(StepName(spec.step_name) for spec in CONTRACT.steps),
        )
        == "FAILED"
    )


@pytest.mark.parametrize("shape", ("missing", "extra"))
def test_strict_final_status_rejects_missing_or_extra_steps(shape: str) -> None:
    steps = _steps()
    if shape == "missing":
        steps.pop(9)
    else:
        steps.append(
            steps[-1].model_copy(
                update={
                    "step_id": "99999999-9999-4999-8999-999999999999",
                    "step_order": 99,
                }
            )
        )

    assert (
        RunOrchestrator._compute_final_status(
            steps,
            expected_step_sequence=tuple(StepName(spec.step_name) for spec in CONTRACT.steps),
        )
        == "FAILED"
    )


def test_strict_final_status_accepts_only_the_exact_completed_28() -> None:
    assert (
        RunOrchestrator._compute_final_status(
            _steps(),
            expected_step_sequence=tuple(StepName(spec.step_name) for spec in CONTRACT.steps),
        )
        == "SUCCEEDED"
    )


class _Runs:
    def __init__(self) -> None:
        self.terminal_calls: list[dict[str, object]] = []

    def get(self, _run_id: str) -> dict[str, object]:
        return {"status": "RUNNING"}

    def try_complete_running(self, _run_id: str, **kwargs: object) -> bool:
        self.terminal_calls.append(dict(kwargs))
        return True


class _Finalizer:
    def __init__(self) -> None:
        self.resume_commands: list[object] = []
        self.finalize_commands: list[object] = []

    def rehydrate_run(self, command: object) -> SimpleNamespace:
        self.resume_commands.append(command)
        return SimpleNamespace(completed_count=0)

    def finalize_run(self, command: object) -> SimpleNamespace:
        self.finalize_commands.append(command)
        return SimpleNamespace(
            run_id=RUN_ID,
            status="SUCCEEDED",
            edge_count=1,
            receipt_count=1,
        )


def test_strict_execution_uses_task7_rehydrate_and_finalizer_as_sole_success_writer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runs = _Runs()
    finalizer = _Finalizer()

    class _Orchestrator:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def execute(self, _ctx: RunContext) -> SimpleNamespace:
            return SimpleNamespace(
                status="SUCCEEDED",
                steps=_steps(),
                block_reason=None,
                error_code=None,
                error_message=None,
            )

    monkeypatch.setattr(execution_module, "RunOrchestrator", _Orchestrator)
    service = RunExecutionService(
        audit_sink=InMemoryAuditSink(),
        runs_repo=runs,  # type: ignore[arg-type]
        run_steps_repo=SimpleNamespace(),  # type: ignore[arg-type]
        task7_completion_service=finalizer,
    )

    result = service.execute_preclaimed(_context())

    assert result.status == "SUCCEEDED"
    assert len(finalizer.resume_commands) == 1
    assert len(finalizer.finalize_commands) == 1
    assert runs.terminal_calls == []


def test_strict_step_completion_callback_replaces_the_legacy_boundary_uow() -> None:
    calls: list[tuple[str, object]] = []
    ctx = _context()
    ctx.task7_step_completion_fn = lambda **kwargs: calls.append(("task7", kwargs))
    ctx.step_boundary_uow_fn = lambda **kwargs: calls.append(("legacy", kwargs))
    ctx.stage_native_output_writes_fn = lambda **_kwargs: ()
    orchestrator = RunOrchestrator(
        audit_sink=InMemoryAuditSink(),
        run_steps_repo=SimpleNamespace(),  # type: ignore[arg-type]
    )

    orchestrator._complete_step_boundary(ctx, _steps()[0], {"status": "COMPLETED"})

    assert [kind for kind, _payload in calls] == ["task7"]


def test_worker_composition_root_constructs_the_concrete_task7_postgres_path() -> None:
    source = __import__("inspect").getsource(worker_module.build_postgres_claimed_run_runtime)

    assert "Task7CompletionFinalizationService" in source
    assert "PostgresTask7CompletionPort" in source
    assert "task7_completion_service=" in source
    assert "task7_step_completion_fn" in source
    assert "require_task7_admission_graph_for_run" in source
