"""Canonical run execution service.

This service is the production boundary for executing a run. It claims a
QUEUED run by marking it RUNNING before invoking RunOrchestrator, preventing
the API path and worker path from executing the same run concurrently.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import text

from idis.audit.sink import AuditSink
from idis.models.run_step import RunStep
from idis.observability.runtime_signals import RUN_CLAIMED, emit_run_signal
from idis.persistence.db import set_tenant_local
from idis.persistence.repositories.run_steps import RunStepsRepo
from idis.persistence.repositories.runs import WorkerLeaseFencedError
from idis.pipeline.task7_completion import (
    Task7CompletionError,
    Task7CompletionFinalizationService,
    Task7LeaseScope,
    Task7RunFinalizationCommand,
    Task7RunResumeCommand,
)
from idis.pipeline.task7_semantics import PIPELINE_CONTRACT_UNAVAILABLE
from idis.services.runs.orchestrator import OrchestratorResult, RunContext, RunOrchestrator
from idis.services.runs.transaction_characteristics import (
    REPEATABLE_READ_WRITE,
    TransactionAccessMode,
    TransactionCharacteristics,
    TransactionCharacteristicsError,
)
from idis.services.webhooks import lifecycle as webhook_lifecycle

_TERMINAL_RUN_WEBHOOK_EVENT = {
    "SUCCEEDED": webhook_lifecycle.RUN_COMPLETED,
    "COMPLETED": webhook_lifecycle.RUN_COMPLETED,
    "FAILED": webhook_lifecycle.RUN_FAILED,
    "CANCELLED": webhook_lifecycle.RUN_CANCELLED,
}


class WorkerLeaseHeartbeatFailedError(RuntimeError):
    """A boundary heartbeat database/audit operation failed."""

    code = "WORKER_LEASE_HEARTBEAT_FAILED"


class WorkerLeaseBoundaryCommitFailedError(RuntimeError):
    """A complete fenced step boundary failed and was rolled back."""

    code = "WORKER_LEASE_BOUNDARY_COMMIT_FAILED"


_TRANSACTION_CHARACTERISTICS_MISSING = object()
_REPEATABLE_READ_WRITE_SQL = "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"


def _resolve_write_transaction_characteristics(
    native_output_writes: tuple[Any, ...],
) -> TransactionCharacteristics | None:
    """Resolve every staged marker before the persistence connection is opened."""
    requested: list[TransactionCharacteristics] = []
    for staged in native_output_writes:
        marker = getattr(
            staged,
            "transaction_characteristics",
            _TRANSACTION_CHARACTERISTICS_MISSING,
        )
        if marker is _TRANSACTION_CHARACTERISTICS_MISSING:
            continue
        if not isinstance(marker, TransactionCharacteristics):
            raise TransactionCharacteristicsError(
                "staged write has a malformed transaction characteristics marker"
            )
        requested.append(marker)
    if not requested:
        return None

    distinct = frozenset(requested)
    if len(distinct) != 1:
        raise TransactionCharacteristicsError(
            "staged writes request conflicting transaction characteristics"
        )
    resolved = next(iter(distinct))
    if resolved.access_mode is TransactionAccessMode.READ_ONLY:
        raise TransactionCharacteristicsError(
            "a staged completion write cannot request a read-only transaction"
        )
    if resolved != REPEATABLE_READ_WRITE:
        raise TransactionCharacteristicsError(
            "staged write requests unsupported transaction characteristics"
        )
    return REPEATABLE_READ_WRITE


def build_postgres_step_boundary_uow(
    *,
    audit_sink: AuditSink,
    connection_factory: Callable[[], Any],
    lease_seconds: int = 60,
) -> Callable[..., None]:
    """Build the fresh-transaction fenced UoW for START/COMPLETE boundaries."""

    def boundary_uow(
        *,
        run_id: str,
        tenant_id: str,
        step_name: Any,
        boundary: str,
        lease_owner_id: str,
        lease_fencing_token: int,
        mutation: Callable[..., Any],
        native_output_writes: tuple[Any, ...] | list[Any],
    ) -> None:
        from idis.pipeline.worker import (
            build_worker_lease_audit_event,
            emit_worker_lease_audit_in_tx,
        )

        staged_writes = tuple(native_output_writes)
        lease_expires_at = (
            (datetime.now(UTC) + timedelta(seconds=lease_seconds))
            .isoformat()
            .replace("+00:00", "Z")
        )
        heartbeat_event = build_worker_lease_audit_event(
            code="WORKER_LEASE_HEARTBEAT_RENEWED",
            tenant_id=tenant_id,
            deal_id=None,
            run_id=run_id,
            lease_owner_id=lease_owner_id,
            prior_fencing_token=None,
            new_fencing_token=lease_fencing_token,
            lease_expires_at=lease_expires_at,
            step_name=step_name,
            boundary=boundary,
        )
        try:
            transaction_characteristics = _resolve_write_transaction_characteristics(staged_writes)
            with connection_factory() as db_conn:
                if transaction_characteristics is REPEATABLE_READ_WRITE:
                    db_conn.exec_driver_sql(_REPEATABLE_READ_WRITE_SQL)
                set_tenant_local(db_conn, tenant_id)
                try:
                    renewal = db_conn.execute(
                        text(
                            """
                            UPDATE runs
                            SET last_heartbeat_at = statement_timestamp(),
                                lease_expires_at = statement_timestamp()
                                    + make_interval(secs => :lease_seconds)
                            WHERE tenant_id = :tenant_id
                              AND run_id = :run_id
                              AND status = :running_status
                              AND lease_owner_id = :lease_owner_id
                              AND lease_fencing_token = :lease_fencing_token
                              AND lease_expires_at > statement_timestamp()
                            RETURNING run_id
                            """
                        ),
                        {
                            "tenant_id": tenant_id,
                            "run_id": run_id,
                            "running_status": "RUNNING",
                            "lease_owner_id": lease_owner_id,
                            "lease_fencing_token": lease_fencing_token,
                            "lease_seconds": lease_seconds,
                        },
                    )
                except Exception as exc:
                    raise WorkerLeaseHeartbeatFailedError("WORKER_LEASE_HEARTBEAT_FAILED") from exc
                if int(getattr(renewal, "rowcount", 0) or 0) == 0:
                    raise WorkerLeaseFencedError("worker lease boundary was fenced")

                call_context = {
                    "db_conn": db_conn,
                    "lease_owner_id": lease_owner_id,
                    "lease_fencing_token": lease_fencing_token,
                }
                for staged in staged_writes:
                    writer = getattr(staged, "persist", staged)
                    writer(**call_context)
                mutation(**call_context)
                try:
                    emit_worker_lease_audit_in_tx(
                        db_conn=db_conn,
                        audit_sink=audit_sink,
                        event=heartbeat_event,
                    )
                except Exception as exc:
                    raise WorkerLeaseHeartbeatFailedError("WORKER_LEASE_HEARTBEAT_FAILED") from exc
        except (WorkerLeaseFencedError, WorkerLeaseHeartbeatFailedError):
            raise
        except Exception as exc:
            failure = WorkerLeaseBoundaryCommitFailedError("WORKER_LEASE_BOUNDARY_COMMIT_FAILED")
            failure_event = build_worker_lease_audit_event(
                code=failure.code,
                tenant_id=tenant_id,
                deal_id=None,
                run_id=run_id,
                lease_owner_id=None,
                prior_fencing_token=None,
                new_fencing_token=None,
                lease_expires_at=None,
                step_name=step_name,
                boundary=boundary,
            )
            with contextlib.suppress(Exception):
                audit_sink.emit(failure_event)
            raise failure from exc

    return boundary_uow


def _notify_run_webhook(
    ctx: RunContext, event_type: str, data: dict[str, object] | None = None
) -> None:
    """Best-effort run lifecycle webhook (never raises; opens its own tenant-scoped conn)."""
    webhook_lifecycle.notify_webhook_lifecycle(
        tenant_id=ctx.tenant_id,
        event_type=event_type,
        resource_type="run",
        resource_id=ctx.run_id,
        data=data,
    )


class RunsExecutionRepository(Protocol):
    """Runs repository behavior needed by RunExecutionService."""

    def get(self, run_id: str) -> dict[str, object] | None:
        """Get run row for cancellation checks."""

    def try_mark_running(self, run_id: str) -> bool:
        """Atomically transition QUEUED -> RUNNING."""

    def try_complete_running(
        self,
        run_id: str,
        *,
        status: str,
        finished_at: str | None,
        lease_owner_id: str | None = None,
        lease_fencing_token: int | None = None,
    ) -> bool:
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
        claim_identity: dict[str, str] | None = None,
        claim_audit_emitter: Callable[[RunContext, dict[str, str]], None] | None = None,
        after_claim_commit: Callable[[], None] | None = None,
        task7_completion_service: Task7CompletionFinalizationService | None = None,
    ) -> None:
        """Initialize the canonical execution service."""
        self._audit_sink = audit_sink
        self._runs_repo = runs_repo
        self._run_steps_repo = run_steps_repo
        self._claim_identity = dict(claim_identity or {})
        self._claim_audit_emitter = claim_audit_emitter
        self._after_claim_commit = after_claim_commit
        self._task7_completion_service = task7_completion_service

    def execute(self, ctx: RunContext) -> RunExecutionResult:
        """Claim a queued run and execute it through RunOrchestrator.

        Args:
            ctx: Fully built run context.

        Returns:
            RunExecutionResult indicating whether this caller claimed and ran the run.
        """
        if ctx.lease_owner_id is not None and ctx.lease_fencing_token is not None:
            return self.execute_preclaimed(ctx)

        inline_claim = getattr(self._runs_repo, "claim_queued_run_for_execution", None)
        if callable(inline_claim):
            lease_owner_id = str(uuid.uuid4())
            claimed_row = inline_claim(
                run_id=ctx.run_id,
                lease_owner_id=lease_owner_id,
                lease_seconds=60,
            )
            if claimed_row is None:
                return RunExecutionResult(claimed=False, status="NOT_CLAIMED")
            ctx.lease_owner_id = lease_owner_id
            ctx.lease_fencing_token = int(claimed_row["lease_fencing_token"])
            self._emit_claim_audit(ctx)
            self._commit_after_claim()
            return self.execute_preclaimed(ctx)

        if not self._try_mark_running(ctx.run_id):
            return RunExecutionResult(claimed=False, status="NOT_CLAIMED")

        # The canonical worker injects an emitter which writes the formal
        # claim event through the same database transaction as QUEUED ->
        # RUNNING.  Emission deliberately precedes commit: an audit failure
        # leaves both writes rollback-able and prevents orchestration.
        self._emit_claim_audit(ctx)
        self._commit_after_claim()

        emit_run_signal(
            self._audit_sink,
            event_type=RUN_CLAIMED,
            tenant_id=ctx.tenant_id,
            details={"run_id": ctx.run_id, "mode": ctx.mode},
        )
        _notify_run_webhook(ctx, webhook_lifecycle.RUN_CLAIMED, {"mode": ctx.mode})

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
                _notify_run_webhook(ctx, webhook_lifecycle.RUN_CANCELLED)
                return _cancelled_execution_result(finished_at=finished_at)
            if not self._try_complete_running(ctx.run_id, status="FAILED", finished_at=finished_at):
                if self._is_currently_cancelled(ctx.run_id):
                    _notify_run_webhook(ctx, webhook_lifecycle.RUN_CANCELLED)
                    return _cancelled_execution_result(finished_at=finished_at)
                self._complete(ctx.run_id, status="FAILED", finished_at=finished_at)
            _notify_run_webhook(ctx, webhook_lifecycle.RUN_FAILED)
            raise

        finished_at = _utc_now()
        if self._is_currently_cancelled(ctx.run_id):
            _notify_run_webhook(ctx, webhook_lifecycle.RUN_CANCELLED)
            return _cancelled_execution_result(
                steps=orch_result.steps,
                finished_at=finished_at,
            )
        if not self._try_complete_running(
            ctx.run_id, status=orch_result.status, finished_at=finished_at
        ):
            if self._is_currently_cancelled(ctx.run_id):
                _notify_run_webhook(ctx, webhook_lifecycle.RUN_CANCELLED)
                return _cancelled_execution_result(
                    steps=orch_result.steps,
                    finished_at=finished_at,
                )
            self._complete(ctx.run_id, status=orch_result.status, finished_at=finished_at)
        _notify_run_webhook(
            ctx,
            _TERMINAL_RUN_WEBHOOK_EVENT.get(orch_result.status, webhook_lifecycle.RUN_COMPLETED),
        )
        return _to_execution_result(orch_result, finished_at=finished_at)

    def execute_preclaimed(self, ctx: RunContext) -> RunExecutionResult:
        """Execute an already atomically claimed run under its owner/token fence."""
        owner_id = ctx.lease_owner_id
        fencing_token = ctx.lease_fencing_token
        if owner_id is None or fencing_token is None:
            raise WorkerLeaseFencedError("preclaimed execution is missing its lease identity")
        task7_scope = self._task7_scope(ctx)
        if task7_scope is not None:
            service = self._require_task7_completion_service()
            service.rehydrate_run(Task7RunResumeCommand(scope=task7_scope))

        emit_run_signal(
            self._audit_sink,
            event_type=RUN_CLAIMED,
            tenant_id=ctx.tenant_id,
            details={"run_id": ctx.run_id, "mode": ctx.mode},
        )
        _notify_run_webhook(ctx, webhook_lifecycle.RUN_CLAIMED, {"mode": ctx.mode})
        orchestrator = RunOrchestrator(
            audit_sink=self._audit_sink,
            run_steps_repo=self._run_steps_repo,
            runs_repo=self._runs_repo,
        )

        try:
            orch_result = orchestrator.execute(ctx)
        except Exception as exc:
            if _is_lease_control_failure(exc):
                raise
            finished_at = _utc_now()
            if self._is_currently_cancelled(ctx.run_id):
                _notify_run_webhook(ctx, webhook_lifecycle.RUN_CANCELLED)
                return _cancelled_execution_result(finished_at=finished_at)
            if not self._try_complete_running_with_lease(
                ctx.run_id,
                status="FAILED",
                finished_at=finished_at,
                lease_owner_id=owner_id,
                lease_fencing_token=fencing_token,
            ):
                if self._is_currently_cancelled(ctx.run_id):
                    _notify_run_webhook(ctx, webhook_lifecycle.RUN_CANCELLED)
                    return _cancelled_execution_result(finished_at=finished_at)
                raise WorkerLeaseFencedError("terminal failure write was fenced") from exc
            _notify_run_webhook(ctx, webhook_lifecycle.RUN_FAILED)
            raise

        finished_at = _utc_now()
        if self._is_currently_cancelled(ctx.run_id):
            _notify_run_webhook(ctx, webhook_lifecycle.RUN_CANCELLED)
            return _cancelled_execution_result(
                steps=orch_result.steps,
                finished_at=finished_at,
            )
        if task7_scope is not None and orch_result.status == "SUCCEEDED":
            finalization = self._require_task7_completion_service().finalize_run(
                Task7RunFinalizationCommand(scope=task7_scope)
            )
            if finalization.status != "SUCCEEDED":
                raise Task7CompletionError(
                    "CONSUMER_RECEIPT_INCOMPLETE",
                    "CONSUMER_RECEIPT_INCOMPLETE",
                )
            _notify_run_webhook(ctx, webhook_lifecycle.RUN_COMPLETED)
            return _to_execution_result(orch_result, finished_at=finished_at)

        terminal_status = "FAILED" if task7_scope is not None else orch_result.status
        if not self._try_complete_running_with_lease(
            ctx.run_id,
            status=terminal_status,
            finished_at=finished_at,
            lease_owner_id=owner_id,
            lease_fencing_token=fencing_token,
        ):
            if self._is_currently_cancelled(ctx.run_id):
                _notify_run_webhook(ctx, webhook_lifecycle.RUN_CANCELLED)
                return _cancelled_execution_result(
                    steps=orch_result.steps,
                    finished_at=finished_at,
                )
            raise WorkerLeaseFencedError("terminal completion write was fenced")
        _notify_run_webhook(
            ctx,
            _TERMINAL_RUN_WEBHOOK_EVENT.get(
                terminal_status,
                webhook_lifecycle.RUN_COMPLETED,
            ),
        )
        return _to_execution_result(orch_result, finished_at=finished_at)

    def _task7_scope(self, ctx: RunContext) -> Task7LeaseScope | None:
        if ctx.pipeline_contract_version != "full-strict-v21":
            return None
        if (
            not isinstance(ctx.deal_id, str)
            or not ctx.deal_id
            or ctx.pipeline_contract_hash is None
            or ctx.pipeline_resume_state is None
            or ctx.lease_owner_id is None
            or ctx.lease_fencing_token is None
        ):
            raise Task7CompletionError(
                PIPELINE_CONTRACT_UNAVAILABLE,
                PIPELINE_CONTRACT_UNAVAILABLE,
            )
        return Task7LeaseScope(
            tenant_id=ctx.tenant_id,
            deal_id=ctx.deal_id,
            run_id=ctx.run_id,
            pipeline_contract_version=ctx.pipeline_contract_version,
            pipeline_contract_hash=ctx.pipeline_contract_hash,
            pipeline_resume_state=ctx.pipeline_resume_state,
            lease_owner_id=ctx.lease_owner_id,
            lease_fencing_token=ctx.lease_fencing_token,
        )

    def _require_task7_completion_service(self) -> Task7CompletionFinalizationService:
        if self._task7_completion_service is None:
            raise Task7CompletionError(
                PIPELINE_CONTRACT_UNAVAILABLE,
                PIPELINE_CONTRACT_UNAVAILABLE,
            )
        return self._task7_completion_service

    @property
    def audit_sink(self) -> AuditSink:
        """Audit sink used by the canonical execution path."""
        return self._audit_sink

    def _try_mark_running(self, run_id: str) -> bool:
        return self._runs_repo.try_mark_running(run_id)

    def _commit_after_claim(self) -> None:
        if self._after_claim_commit is not None:
            self._after_claim_commit()

    def _emit_claim_audit(self, ctx: RunContext) -> None:
        if self._claim_audit_emitter is None:
            return
        self._claim_audit_emitter(ctx, dict(self._claim_identity))

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

    def _try_complete_running_with_lease(
        self,
        run_id: str,
        *,
        status: str,
        finished_at: str,
        lease_owner_id: str,
        lease_fencing_token: int,
    ) -> bool:
        """Persist terminal state only for the exact live owner/token."""
        guarded = getattr(self._runs_repo, "try_complete_running", None)
        if not callable(guarded):
            raise WorkerLeaseFencedError("fenced terminal repository operation unavailable")
        return bool(
            guarded(
                run_id,
                status=status,
                finished_at=finished_at,
                lease_owner_id=lease_owner_id,
                lease_fencing_token=lease_fencing_token,
            )
        )

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


def _is_lease_control_failure(exc: BaseException) -> bool:
    return getattr(exc, "code", None) in {
        "WORKER_LEASE_FENCED",
        "WORKER_LEASE_HEARTBEAT_FAILED",
        "WORKER_LEASE_BOUNDARY_COMMIT_FAILED",
    }
