"""RunStep model — tracks individual pipeline step execution within a Run.

Each RunStep records the lifecycle of a single orchestration step:
start, completion/failure, error details, and retry count.
Steps are ordered by a canonical step_order for deterministic iteration.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class StepName(StrEnum):
    """Canonical pipeline step names in execution order."""

    INGEST_CHECK = "INGEST_CHECK"
    EXTRACT = "EXTRACT"
    GRADE = "GRADE"
    DEBATE = "DEBATE"


class StepStatus(StrEnum):
    """Lifecycle status of a single pipeline step."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"


STEP_ORDER: dict[StepName, int] = {
    StepName.INGEST_CHECK: 0,
    StepName.EXTRACT: 1,
    StepName.GRADE: 2,
    StepName.DEBATE: 3,
}
"""Canonical ordering for deterministic step iteration."""

SNAPSHOT_STEPS: list[StepName] = [
    StepName.INGEST_CHECK,
    StepName.EXTRACT,
    StepName.GRADE,
]
"""Steps executed during a SNAPSHOT run."""

FULL_STEPS: list[StepName] = [
    StepName.INGEST_CHECK,
    StepName.EXTRACT,
    StepName.GRADE,
    StepName.DEBATE,
]
"""Steps executed during a FULL run."""

IMPLEMENTED_STEPS: frozenset[StepName] = frozenset(
    {
        StepName.INGEST_CHECK,
        StepName.EXTRACT,
        StepName.GRADE,
    }
)
"""Steps with working implementations. Unimplemented steps trigger BLOCKED."""


class StepError(BaseModel):
    """Structured error envelope persisted on step failure.

    Attributes:
        code: Stable machine-readable error code.
        message: Human-readable description.
    """

    code: str
    message: str


class RunStep(BaseModel):
    """Single pipeline step record within a Run.

    Attributes:
        step_id: Unique UUID for this step execution.
        run_id: Parent run UUID.
        tenant_id: Tenant scope for isolation.
        step_name: Canonical step name enum.
        step_order: Integer for deterministic ordering.
        status: Current lifecycle status.
        started_at: ISO timestamp when step began.
        finished_at: ISO timestamp when step ended.
        error_code: Stable error code on failure.
        error_message: Human-readable error on failure.
        retry_count: Number of retry attempts so far.
        result_summary: Step-specific output data for downstream consumption.
    """

    step_id: str
    run_id: str
    tenant_id: str
    step_name: StepName
    step_order: int
    status: StepStatus = StepStatus.PENDING
    started_at: str | None = None
    finished_at: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    retry_count: int = 0
    result_summary: dict[str, Any] = Field(default_factory=dict)
