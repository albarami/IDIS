"""RED-first contracts for v7 orchestration and immutable step rehydration."""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType
from typing import Any

import pytest

from idis.audit.sink import InMemoryAuditSink
from idis.models.run_step import RunStep, StepName, StepStatus
from idis.pipeline import contract_registry
from idis.services.runs.orchestrator import (
    RunContext,
    RunOrchestrator,
    RunStepBlockedError,
)
from idis.services.runs.steps import build_run_context

TENANT_ID = "11111111-1111-4111-8111-111111111111"
DEAL_ID = "22222222-2222-4222-8222-222222222222"
RUN_ID = "33333333-3333-4333-8333-333333333333"
CONTRACT_HASH = contract_registry.FULL_STRICT_V21_CONTRACT_HASH


class _StepsRepository:
    def __init__(self, steps: list[RunStep] | None = None) -> None:
        self.steps = {step.step_name: step for step in steps or []}
        self.create_calls = 0
        self.update_calls = 0

    def create(self, step: RunStep, **_kwargs: Any) -> RunStep:
        self.create_calls += 1
        self.steps[step.step_name] = step
        return step

    def update(self, step: RunStep, **_kwargs: Any) -> RunStep:
        self.update_calls += 1
        self.steps[step.step_name] = step
        return step

    def get_step(self, _run_id: str, step_name: StepName) -> RunStep | None:
        return self.steps.get(step_name)

    def get_by_run_id(self, _run_id: str) -> list[RunStep]:
        return sorted(self.steps.values(), key=lambda step: (step.step_order, step.step_id))


def _activated_registry() -> Any:
    registry = dict(contract_registry.PIPELINE_CONTRACTS)
    registry["full-strict-v21"] = replace(
        registry["full-strict-v21"],
        state=contract_registry.PipelineContractState.ACTIVE,
    )
    return MappingProxyType(registry)


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
        pipeline_contract_hash=CONTRACT_HASH,
        pipeline_resume_state="RESUMABLE",
        lease_owner_id="44444444-4444-4444-8444-444444444444",
        lease_fencing_token=1,
    )


def _orchestrator(
    repository: _StepsRepository,
    *,
    registry: Any,
) -> RunOrchestrator:
    return RunOrchestrator(
        audit_sink=InMemoryAuditSink(),
        run_steps_repo=repository,  # type: ignore[arg-type]
        pipeline_contract_registry=registry,
    )


def _step(*, contract_hash: str = CONTRACT_HASH) -> RunStep:
    strict = contract_registry.PIPELINE_CONTRACTS["full-strict-v21"].steps[0]
    return RunStep(
        step_id="55555555-5555-4555-8555-555555555555",
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        step_name=strict.step_name,
        step_order=strict.step_order,
        status=StepStatus.PENDING,
        pipeline_contract_version="full-strict-v21",
        pipeline_contract_hash=contract_hash,
        semantic_rule_version=strict.semantic_rule_version,
        semantic_rule_hash=strict.semantic_rule_hash,
    )


def test_v5_uses_contract_local_28_step_order_not_legacy_global_order() -> None:
    orchestrator = _orchestrator(_StepsRepository(), registry=_activated_registry())

    sequence = orchestrator._resolve_pipeline_step_sequence(_context())

    assert len(sequence) == 28
    assert sequence[0] is StepName.DATA_ROOM_INVENTORY_PACKAGE
    assert sequence[12] is StepName.EXTRACT
    assert sequence[27] is StepName.DELIVERABLES


def test_inactive_v5_worker_resolution_fails_closed() -> None:
    orchestrator = _orchestrator(
        _StepsRepository(),
        registry=contract_registry.PIPELINE_CONTRACTS,
    )

    with pytest.raises(RunStepBlockedError) as exc_info:
        orchestrator._resolve_pipeline_step_sequence(_context())

    assert exc_info.value.code == "PIPELINE_CONTRACT_UNAVAILABLE"


def test_v5_start_requires_the_precreated_admission_step() -> None:
    repository = _StepsRepository()
    orchestrator = _orchestrator(repository, registry=_activated_registry())

    with pytest.raises(RunStepBlockedError) as exc_info:
        orchestrator._start_step(_context(), StepName.DATA_ROOM_INVENTORY_PACKAGE, None)

    assert exc_info.value.code == "STEP_REHYDRATION_FAILED"
    assert repository.create_calls == 0


def test_v5_start_rejects_a_persisted_step_with_contract_drift() -> None:
    persisted = _step(contract_hash="a" * 64)
    repository = _StepsRepository([persisted])
    orchestrator = _orchestrator(repository, registry=_activated_registry())

    with pytest.raises(RunStepBlockedError) as exc_info:
        orchestrator._start_step(
            _context(),
            StepName.DATA_ROOM_INVENTORY_PACKAGE,
            persisted,
        )

    assert exc_info.value.code == "PIPELINE_CONTRACT_HASH_MISMATCH"
    assert repository.update_calls == 0


def test_v5_completed_resume_failure_never_rewrites_the_completed_step() -> None:
    persisted = _step()
    persisted.status = StepStatus.COMPLETED
    repository = _StepsRepository([persisted])
    orchestrator = _orchestrator(repository, registry=_activated_registry())

    with pytest.raises(RunStepBlockedError) as exc_info:
        orchestrator._fail_completed_resume_boundary(
            _context(),
            persisted,
            RunStepBlockedError("STEP_REHYDRATION_FAILED", "mismatch"),
        )

    assert exc_info.value.code == "STEP_REHYDRATION_FAILED"
    assert persisted.status is StepStatus.COMPLETED
    assert repository.update_calls == 0


def test_run_context_composition_threads_the_persisted_contract_identity() -> None:
    context = build_run_context(
        db_conn=None,
        tenant_id=TENANT_ID,
        run_id=RUN_ID,
        deal_id=DEAL_ID,
        mode="SNAPSHOT",
        documents=[],
        audit_sink=InMemoryAuditSink(),
        pipeline_contract_version="full-strict-v21",
        pipeline_contract_hash=CONTRACT_HASH,
        pipeline_resume_state="RESUMABLE",
    )

    assert context.pipeline_contract_version == "full-strict-v21"
    assert context.pipeline_contract_hash == CONTRACT_HASH
    assert context.pipeline_resume_state == "RESUMABLE"
