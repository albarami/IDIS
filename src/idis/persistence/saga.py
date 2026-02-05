"""Saga/Compensation module for Graph-DB + Postgres dual-write consistency.

Phase POST-5.2: Ensures Postgres + Graph DB writes either both succeed or
both roll back. Implements saga pattern with compensation actions.

Design:
- SagaStep: Individual write operation with compensation action
- SagaExecutor: Orchestrates multi-store writes with rollback on failure
- Fail-closed: Any failure triggers compensation for all completed steps
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Generic, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class SagaStepStatus(StrEnum):
    """Status of a saga step."""

    PENDING = "pending"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    COMPENSATING = "compensating"
    COMPENSATED = "compensated"
    COMPENSATION_FAILED = "compensation_failed"


class SagaStatus(StrEnum):
    """Overall status of a saga execution."""

    PENDING = "pending"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    COMPENSATING = "compensating"
    COMPENSATED = "compensated"
    COMPENSATION_FAILED = "compensation_failed"


@dataclass
class SagaStepResult:
    """Result of executing a saga step."""

    step_name: str
    status: SagaStepStatus
    result: Any = None
    error: Exception | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


@dataclass
class SagaResult:
    """Result of executing a complete saga."""

    saga_id: str
    status: SagaStatus
    step_results: list[SagaStepResult] = field(default_factory=list)
    error: Exception | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @property
    def is_success(self) -> bool:
        """Check if saga completed successfully."""
        return self.status == SagaStatus.COMPLETED

    @property
    def is_compensated(self) -> bool:
        """Check if saga was compensated (rolled back)."""
        return self.status == SagaStatus.COMPENSATED


class SagaStep(ABC, Generic[T]):
    """Abstract base class for a saga step.

    Each step must implement:
    - execute(): Perform the forward action
    - compensate(): Undo the action if saga fails later
    """

    def __init__(self, name: str) -> None:
        """Initialize the step.

        Args:
            name: Human-readable name for logging/debugging.
        """
        self.name = name

    @abstractmethod
    def execute(self, context: dict[str, Any]) -> T:
        """Execute the forward action.

        Args:
            context: Shared context dictionary for passing data between steps.

        Returns:
            Result of the step execution.

        Raises:
            Exception: If execution fails.
        """
        pass

    @abstractmethod
    def compensate(self, context: dict[str, Any], result: T) -> None:
        """Compensate (undo) the action.

        Called if a later step fails and the saga needs to roll back.

        Args:
            context: Shared context dictionary.
            result: Result from the execute() call to help with compensation.
        """
        pass


class PostgresWriteStep(SagaStep[str]):
    """Saga step for Postgres write operations.

    Executes a Postgres insert/update and provides compensation
    via delete or reverse update.
    """

    def __init__(
        self,
        name: str,
        execute_fn: Callable[[dict[str, Any]], str],
        compensate_fn: Callable[[dict[str, Any], str], None],
    ) -> None:
        """Initialize Postgres write step.

        Args:
            name: Step name.
            execute_fn: Function to execute the write, returns record ID.
            compensate_fn: Function to compensate (delete/reverse) the write.
        """
        super().__init__(name)
        self._execute_fn = execute_fn
        self._compensate_fn = compensate_fn

    def execute(self, context: dict[str, Any]) -> str:
        """Execute Postgres write."""
        return self._execute_fn(context)

    def compensate(self, context: dict[str, Any], result: str) -> None:
        """Compensate by deleting/reversing the Postgres write."""
        self._compensate_fn(context, result)


class GraphWriteStep(SagaStep[str]):
    """Saga step for Graph DB write operations.

    Executes a Graph DB node/edge creation and provides compensation
    via deletion.
    """

    def __init__(
        self,
        name: str,
        execute_fn: Callable[[dict[str, Any]], str],
        compensate_fn: Callable[[dict[str, Any], str], None],
    ) -> None:
        """Initialize Graph write step.

        Args:
            name: Step name.
            execute_fn: Function to execute the graph write, returns node/edge ID.
            compensate_fn: Function to delete the node/edge.
        """
        super().__init__(name)
        self._execute_fn = execute_fn
        self._compensate_fn = compensate_fn

    def execute(self, context: dict[str, Any]) -> str:
        """Execute Graph DB write."""
        return self._execute_fn(context)

    def compensate(self, context: dict[str, Any], result: str) -> None:
        """Compensate by deleting the graph node/edge."""
        self._compensate_fn(context, result)


class DualWriteSagaExecutor:
    """Executor for dual-write sagas (Postgres + Graph DB).

    Ensures both stores are consistent:
    - If Postgres write succeeds but Graph write fails, Postgres is rolled back
    - If Graph write succeeds but later step fails, Graph is rolled back
    - All compensation actions are attempted even if some fail

    Fail-closed semantics:
    - Any step failure triggers compensation
    - Compensation failures are logged but saga reports overall failure
    """

    def __init__(self, saga_id: str) -> None:
        """Initialize the saga executor.

        Args:
            saga_id: Unique identifier for this saga execution.
        """
        self.saga_id = saga_id
        self._steps: list[SagaStep[Any]] = []
        self._step_results: list[SagaStepResult] = []
        self._context: dict[str, Any] = {}

    def add_step(self, step: SagaStep[Any]) -> DualWriteSagaExecutor:
        """Add a step to the saga.

        Args:
            step: Step to add.

        Returns:
            Self for chaining.
        """
        self._steps.append(step)
        return self

    def add_postgres_step(
        self,
        name: str,
        execute_fn: Callable[[dict[str, Any]], str],
        compensate_fn: Callable[[dict[str, Any], str], None],
    ) -> DualWriteSagaExecutor:
        """Add a Postgres write step.

        Args:
            name: Step name.
            execute_fn: Execute function.
            compensate_fn: Compensation function.

        Returns:
            Self for chaining.
        """
        step = PostgresWriteStep(name, execute_fn, compensate_fn)
        return self.add_step(step)

    def add_graph_step(
        self,
        name: str,
        execute_fn: Callable[[dict[str, Any]], str],
        compensate_fn: Callable[[dict[str, Any], str], None],
    ) -> DualWriteSagaExecutor:
        """Add a Graph DB write step.

        Args:
            name: Step name.
            execute_fn: Execute function.
            compensate_fn: Compensation function.

        Returns:
            Self for chaining.
        """
        step = GraphWriteStep(name, execute_fn, compensate_fn)
        return self.add_step(step)

    def execute(self, initial_context: dict[str, Any] | None = None) -> SagaResult:
        """Execute the saga with all steps.

        Args:
            initial_context: Initial context data to pass to steps.

        Returns:
            SagaResult with overall status and per-step results.
        """
        self._context = initial_context or {}
        self._step_results = []

        started_at = datetime.now()
        completed_steps: list[tuple[SagaStep[Any], Any]] = []

        logger.info("Starting saga %s with %d steps", self.saga_id, len(self._steps))

        for step in self._steps:
            step_started = datetime.now()
            step_result = SagaStepResult(
                step_name=step.name,
                status=SagaStepStatus.EXECUTING,
                started_at=step_started,
            )

            try:
                result = step.execute(self._context)
                step_result.status = SagaStepStatus.COMPLETED
                step_result.result = result
                step_result.completed_at = datetime.now()
                completed_steps.append((step, result))
                self._step_results.append(step_result)
                logger.debug("Step %s completed successfully", step.name)

            except Exception as e:
                step_result.status = SagaStepStatus.FAILED
                step_result.error = e
                step_result.completed_at = datetime.now()
                self._step_results.append(step_result)
                logger.error("Step %s failed: %s", step.name, e)

                # Compensate all completed steps in reverse order
                compensation_result = self._compensate(completed_steps)

                return SagaResult(
                    saga_id=self.saga_id,
                    status=compensation_result,
                    step_results=self._step_results,
                    error=e,
                    started_at=started_at,
                    completed_at=datetime.now(),
                )

        logger.info("Saga %s completed successfully", self.saga_id)
        return SagaResult(
            saga_id=self.saga_id,
            status=SagaStatus.COMPLETED,
            step_results=self._step_results,
            started_at=started_at,
            completed_at=datetime.now(),
        )

    def _compensate(self, completed_steps: list[tuple[SagaStep[Any], Any]]) -> SagaStatus:
        """Compensate all completed steps in reverse order.

        Args:
            completed_steps: List of (step, result) tuples to compensate.

        Returns:
            COMPENSATED if all compensations succeeded, COMPENSATION_FAILED otherwise.
        """
        logger.info("Compensating %d completed steps", len(completed_steps))
        all_compensated = True

        for step, result in reversed(completed_steps):
            comp_result = SagaStepResult(
                step_name=f"{step.name}_compensation",
                status=SagaStepStatus.COMPENSATING,
                started_at=datetime.now(),
            )

            try:
                step.compensate(self._context, result)
                comp_result.status = SagaStepStatus.COMPENSATED
                comp_result.completed_at = datetime.now()
                logger.debug("Compensated step %s", step.name)

            except Exception as e:
                comp_result.status = SagaStepStatus.COMPENSATION_FAILED
                comp_result.error = e
                comp_result.completed_at = datetime.now()
                all_compensated = False
                logger.error("Compensation failed for step %s: %s", step.name, e)

            self._step_results.append(comp_result)

        return SagaStatus.COMPENSATED if all_compensated else SagaStatus.COMPENSATION_FAILED


class DualWriteConsistencyError(Exception):
    """Raised when dual-write consistency cannot be maintained.

    This error indicates that:
    - A write operation failed
    - Compensation was attempted
    - The system may be in an inconsistent state if compensation failed
    """

    def __init__(self, saga_result: SagaResult) -> None:
        self.saga_result = saga_result
        status_msg = "compensated" if saga_result.is_compensated else "compensation failed"
        super().__init__(
            f"Dual-write saga {saga_result.saga_id} failed ({status_msg}): {saga_result.error}"
        )


def create_claim_dual_write_saga(
    saga_id: str,
    postgres_insert: Callable[[dict[str, Any]], str],
    postgres_delete: Callable[[dict[str, Any], str], None],
    graph_insert: Callable[[dict[str, Any]], str],
    graph_delete: Callable[[dict[str, Any], str], None],
) -> DualWriteSagaExecutor:
    """Create a saga for dual-writing a claim to Postgres and Graph DB.

    Args:
        saga_id: Unique saga identifier.
        postgres_insert: Function to insert claim into Postgres.
        postgres_delete: Function to delete claim from Postgres.
        graph_insert: Function to insert claim node into Graph DB.
        graph_delete: Function to delete claim node from Graph DB.

    Returns:
        Configured saga executor ready to execute.
    """
    return (
        DualWriteSagaExecutor(saga_id)
        .add_postgres_step("postgres_claim_insert", postgres_insert, postgres_delete)
        .add_graph_step("graph_claim_insert", graph_insert, graph_delete)
    )


def create_sanad_dual_write_saga(
    saga_id: str,
    postgres_insert: Callable[[dict[str, Any]], str],
    postgres_delete: Callable[[dict[str, Any], str], None],
    graph_insert: Callable[[dict[str, Any]], str],
    graph_delete: Callable[[dict[str, Any], str], None],
) -> DualWriteSagaExecutor:
    """Create a saga for dual-writing a Sanad to Postgres and Graph DB.

    Args:
        saga_id: Unique saga identifier.
        postgres_insert: Function to insert Sanad into Postgres.
        postgres_delete: Function to delete Sanad from Postgres.
        graph_insert: Function to insert Sanad chain into Graph DB.
        graph_delete: Function to delete Sanad chain from Graph DB.

    Returns:
        Configured saga executor ready to execute.
    """
    return (
        DualWriteSagaExecutor(saga_id)
        .add_postgres_step("postgres_sanad_insert", postgres_insert, postgres_delete)
        .add_graph_step("graph_sanad_insert", graph_insert, graph_delete)
    )
