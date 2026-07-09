"""Canonical run execution service.

This service is the production boundary for executing a run. It claims a
QUEUED run by marking it RUNNING before invoking RunOrchestrator, preventing
the API path and worker path from executing the same run concurrently.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from idis.audit.sink import AuditSink
from idis.models.run_step import RunStep
from idis.observability.runtime_signals import RUN_CLAIMED, emit_run_signal
from idis.persistence.repositories.run_steps import RunStepsRepo
from idis.services.runs.orchestrator import OrchestratorResult, RunContext, RunOrchestrator


class RunsExecutionRepository(Protocol):
    """Runs repository behavior needed by RunExecutionService."""

    def get(self, run_id: str) -> dict[str, object] | None:
        """Get run row for cancellation checks."""

    def try_mark_running(self, run_id: str) -> bool:
        """Atomically transition QUEUED -> RUNNING."""

    def try_complete_running(self, run_id: str, *, status: str, finished_at: str | None) -> bool:
        """Atomically set a terminal status only while the run is still RUNNING."""

    def complete(self, run_id: str, *, status: str, finished_at: str | None) -> None:
        """Set terminal run status."""


@dataclass(frozen=True)
class RunExecutionResult:
    """Result from attempting canonical run execution."""

    claimed: bool
    status: str
    steps: list[RunStep] = field(default_factory=list)
    block_reason: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    finished_at: str | None = None


class RunExecutionService:
    """Claim and execute runs through RunOrchestrator only."""

    def __init__(
        self,
        *,
        audit_sink: AuditSink,
        runs_repo: RunsExecutionRepository,
        run_steps_repo: RunStepsRepo,
        after_claim_commit: Callable[[], None] | None = None,
    ) -> None:
        """Initialize the canonical execution service."""
        self._audit_sink = audit_sink
        self._runs_repo = runs_repo
        self._run_steps_repo = run_steps_repo
        self._after_claim_commit = after_claim_commit

    def execute(self, ctx: RunContext) -> RunExecutionResult:
        """Claim a queued run and execute it through RunOrchestrator.

        Args:
            ctx: Fully built run context.

        Returns:
            RunExecutionResult indicating whether this caller claimed and ran the run.
        """
        if not self._try_mark_running(ctx.run_id):
            return RunExecutionResult(claimed=False, status="NOT_CLAIMED")
        self._commit_after_claim()

        emit_run_signal(
            self._audit_sink,
            event_type=RUN_CLAIMED,
            tenant_id=ctx.tenant_id,
            details={"run_id": ctx.run_id, "mode": ctx.mode},
        )

        orchestrator = RunOrchestrator(
            audit_sink=self._audit_sink,
            run_steps_repo=self._run_steps_repo,
            runs_repo=self._runs_repo,
        )

        try:
            orch_result = orchestrator.execute(ctx)
        except Exception:
            finished_at = _utc_now()
            if self._is_currently_cancelled(ctx.run_id):
                return _cancelled_execution_result(finished_at=finished_at)
            if not self._try_complete_running(ctx.run_id, status="FAILED", finished_at=finished_at):
                if self._is_currently_cancelled(ctx.run_id):
                    return _cancelled_execution_result(finished_at=finished_at)
                self._complete(ctx.run_id, status="FAILED", finished_at=finished_at)
            raise

        finished_at = _utc_now()
        if self._is_currently_cancelled(ctx.run_id):
            return _cancelled_execution_result(
                steps=orch_result.steps,
                finished_at=finished_at,
            )
        if not self._try_complete_running(
            ctx.run_id, status=orch_result.status, finished_at=finished_at
        ):
            if self._is_currently_cancelled(ctx.run_id):
                return _cancelled_execution_result(
                    steps=orch_result.steps,
                    finished_at=finished_at,
                )
            self._complete(ctx.run_id, status=orch_result.status, finished_at=finished_at)
        return _to_execution_result(orch_result, finished_at=finished_at)

    @property
    def audit_sink(self) -> AuditSink:
        """Audit sink used by the canonical execution path."""
        return self._audit_sink

    def _try_mark_running(self, run_id: str) -> bool:
        return self._runs_repo.try_mark_running(run_id)

    def _commit_after_claim(self) -> None:
        if self._after_claim_commit is not None:
            self._after_claim_commit()

    def _complete(self, run_id: str, *, status: str, finished_at: str) -> None:
        self._runs_repo.complete(run_id, status=status, finished_at=finished_at)

    def _try_complete_running(self, run_id: str, *, status: str, finished_at: str) -> bool:
        """Complete a run only while it is still RUNNING, closing the cancel race.

        Returns True when the guarded completion succeeded. Repositories that do not
        implement the guarded method fall back to the prior unconditional completion
        (preserving existing behavior for minimal test doubles).
        """
        guarded = getattr(self._runs_repo, "try_complete_running", None)
        if not callable(guarded):
            self._runs_repo.complete(run_id, status=status, finished_at=finished_at)
            return True
        return bool(guarded(run_id, status=status, finished_at=finished_at))

    def _is_currently_cancelled(self, run_id: str) -> bool:
        get_run = getattr(self._runs_repo, "get", None)
        if not callable(get_run):
            return False
        run = get_run(run_id)
        return isinstance(run, dict) and run.get("status") == "CANCELLED"


def _to_execution_result(
    orch_result: OrchestratorResult,
    *,
    finished_at: str,
) -> RunExecutionResult:
    return RunExecutionResult(
        claimed=True,
        status=orch_result.status,
        steps=orch_result.steps,
        block_reason=orch_result.block_reason,
        error_code=orch_result.error_code,
        error_message=orch_result.error_message,
        finished_at=finished_at,
    )


def _cancelled_execution_result(
    *,
    finished_at: str,
    steps: list[RunStep] | None = None,
) -> RunExecutionResult:
    return RunExecutionResult(
        claimed=True,
        status="CANCELLED",
        steps=steps or [],
        error_code="RUN_CANCELLED",
        error_message="Run cancellation requested",
        finished_at=finished_at,
    )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
