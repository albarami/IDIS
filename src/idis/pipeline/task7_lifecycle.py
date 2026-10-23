"""Read-only restart rehydration for the reviewed full-strict-v21 contract."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final

from idis.models.run_step import RunStep, StepStatus
from idis.pipeline.contract_registry import (
    FULL_STRICT_V21_CONTRACT_HASH,
    PIPELINE_CONTRACTS,
)

STEP_REHYDRATION_FAILED: Final = "STEP_REHYDRATION_FAILED"
STEP_OUTPUT_MISSING: Final = "STEP_OUTPUT_MISSING"
CONSUMER_RECEIPT_INCOMPLETE: Final = "CONSUMER_RECEIPT_INCOMPLETE"


class Task7LifecycleError(RuntimeError):
    """Stable fail-closed lifecycle validation error."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class Task7ResumePlan:
    """Read-only result of validating one exact strict run prefix."""

    completed_count: int
    next_step_order: int | None
    next_step_name: str | None
    replay_uncompleted_boundary: bool
    finalization_required: bool


def _fail(message: str) -> None:
    raise Task7LifecycleError(STEP_REHYDRATION_FAILED, message)


def rehydrate_strict_run(
    *,
    tenant_id: str,
    run_id: str,
    pipeline_contract_version: str,
    pipeline_contract_hash: str,
    pipeline_resume_state: str,
    steps: Sequence[RunStep],
    validate_completed: Callable[[RunStep], bool],
) -> Task7ResumePlan:
    """Validate an exact completed prefix without repairing or mutating any row."""
    if (
        pipeline_contract_version != "full-strict-v21"
        or pipeline_contract_hash != FULL_STRICT_V21_CONTRACT_HASH
        or pipeline_resume_state != "RESUMABLE"
    ):
        _fail("run is not resumable under the reviewed strict contract")
    contract = PIPELINE_CONTRACTS["full-strict-v21"]
    ordered = tuple(sorted(steps, key=lambda step: (step.step_order, step.step_id)))
    if len(ordered) != contract.required_cardinality:
        _fail("strict run does not contain the exact canonical step cardinality")

    completed_count = 0
    first_uncompleted_status: StepStatus | None = None
    for expected, persisted in zip(contract.steps, ordered, strict=True):
        if (
            persisted.tenant_id != tenant_id
            or persisted.run_id != run_id
            or persisted.step_order != expected.step_order
            or persisted.step_name.value != expected.step_name
            or persisted.pipeline_contract_version != pipeline_contract_version
            or persisted.pipeline_contract_hash != pipeline_contract_hash
            or persisted.semantic_rule_version != expected.semantic_rule_version
            or persisted.semantic_rule_hash != expected.semantic_rule_hash
        ):
            _fail("persisted step identity differs from the reviewed contract")

        if first_uncompleted_status is None and persisted.status is StepStatus.COMPLETED:
            before = persisted.model_dump(mode="json")
            try:
                accepted = validate_completed(persisted)
            except Task7LifecycleError:
                raise
            except Exception as exc:
                raise Task7LifecycleError(
                    STEP_REHYDRATION_FAILED,
                    "completed-step validation raised",
                ) from exc
            if accepted is not True or persisted.model_dump(mode="json") != before:
                _fail("completed step lacks immutable semantic/receipt proof")
            completed_count += 1
            continue

        if first_uncompleted_status is None:
            if persisted.status not in {StepStatus.PENDING, StepStatus.RUNNING}:
                _fail("strict resumable run contains a terminal failure")
            first_uncompleted_status = persisted.status
            continue

        if persisted.status is not StepStatus.PENDING:
            _fail("strict run is not one completed prefix plus one replay boundary")

    if completed_count == contract.required_cardinality:
        return Task7ResumePlan(
            completed_count=completed_count,
            next_step_order=None,
            next_step_name=None,
            replay_uncompleted_boundary=False,
            finalization_required=True,
        )

    next_step = contract.steps[completed_count]
    return Task7ResumePlan(
        completed_count=completed_count,
        next_step_order=next_step.step_order,
        next_step_name=next_step.step_name,
        replay_uncompleted_boundary=first_uncompleted_status is StepStatus.RUNNING,
        finalization_required=False,
    )
