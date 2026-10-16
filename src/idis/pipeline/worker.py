"""Background worker for processing pipeline runs through the canonical service."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import threading
import uuid
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError
from sqlalchemy import text

from idis.audit.sink import AuditSinkError
from idis.models.run_source import RunSource
from idis.models.run_step import RunStep, StepName, StepStatus
from idis.observability.runtime_signals import RUN_QUEUE_OBSERVED, emit_run_signal
from idis.persistence.db import get_app_engine, set_tenant_local
from idis.persistence.provider_reads import ProviderReadConnection
from idis.persistence.repositories.run_steps import get_run_steps_repository
from idis.persistence.repositories.runs import (
    PostgresRunsRepository,
    WorkerLeaseFencedError,
    get_runs_repository,
)
from idis.persistence.repositories.strict_preflight_proofs import (
    StrictPreflightProofError,
    get_strict_preflight_proofs_repository,
)
from idis.runtime.profile import (
    StrictProfileError,
    StrictRuntimeProfile,
    require_runtime_profile,
)
from idis.services.runs.execution import RunExecutionService
from idis.services.runs.orchestrator import RunContext, canonical_result_summary_sha256
from idis.services.runs.strict_full_live import (
    IDIS_STRICT_DOTENV_PATH_ENV,
    STRICT_FULL_LIVE_BLOCKED,
    build_strict_full_live_admission_report,
    is_strict_full_live_required,
    require_strict_process_role,
)
from idis.validators.audit_event_validator import validate_audit_event

logger = logging.getLogger(__name__)

ExecutionServiceFactory = Callable[..., RunExecutionService]
RunContextFactory = Callable[..., RunContext]

_WORKER_INSTANCE_ID = str(uuid.uuid4())

WORKER_LEASE_CONFIG_INVALID = "WORKER_LEASE_CONFIG_INVALID"
WORKER_LEASE_CLAIM_FAILED = "WORKER_LEASE_CLAIM_FAILED"
WORKER_LEASE_RECLAIM_FAILED = "WORKER_LEASE_RECLAIM_FAILED"
WORKER_LEASE_HEARTBEAT_FAILED = "WORKER_LEASE_HEARTBEAT_FAILED"

_UNSTAGED_DURABLE_PROVIDER_STEPS = frozenset(
    {
        StepName.METHODOLOGY_DETERMINISTIC_CALCULATION,
        StepName.METHODOLOGY_EVIDENCE_TRUST_COURT,
        StepName.EXTRACT,
        StepName.GRADE,
        StepName.CALC,
        StepName.GRAPH_EVIDENCE,
        StepName.RAG_EVIDENCE,
        StepName.DELIVERABLES,
    }
)


def _build_provider_output_guard(
    *,
    provider_reads: ProviderReadConnection,
    contracts: dict[StepName, Any],
) -> Callable[..., None]:
    """Block known unstaged producers and detect swallowed direct-write attempts."""
    from idis.services.runs.orchestrator import RunStepBlockedError

    def guard(*, step_name: StepName, phase: str) -> None:
        if (
            phase == "before"
            and step_name in _UNSTAGED_DURABLE_PROVIDER_STEPS
            and step_name not in contracts
        ):
            raise RunStepBlockedError(
                "PIPELINE_CONTRACT_UNAVAILABLE",
                "Durable producer output has no reviewed completion-boundary stager",
            )
        if phase == "after":
            provider_reads.assert_no_write_attempt()

    return guard


_LEASE_EVENT_TYPES = {
    "WORKER_LEASE_CLAIMED": "worker.lease.claimed",
    "WORKER_LEASE_HEARTBEAT_RENEWED": "worker.lease.heartbeat_renewed",
    "WORKER_LEASE_EXPIRED": "worker.lease.expired",
    "WORKER_LEASE_RECLAIMED": "worker.lease.reclaimed",
    "WORKER_LEASE_BOUNDARY_COMMIT_FAILED": "worker.lease.boundary_commit_failed",
}


class WorkerLeaseRuntimeError(RuntimeError):
    """Stable machine-readable worker-lease runtime failure."""

    code = "WORKER_LEASE_RUNTIME_FAILED"


class WorkerLeaseConfigError(WorkerLeaseRuntimeError):
    """Strict worker lease intervals are absent or invalid."""

    code = WORKER_LEASE_CONFIG_INVALID


class WorkerLeaseClaimError(WorkerLeaseRuntimeError):
    """The canonical QUEUED -> RUNNING claim transaction failed."""

    code = WORKER_LEASE_CLAIM_FAILED


class WorkerLeaseReclaimError(WorkerLeaseRuntimeError):
    """The expired-lease reclaim transaction failed."""

    code = WORKER_LEASE_RECLAIM_FAILED


class WorkerLeaseHeartbeatError(WorkerLeaseRuntimeError):
    """A heartbeat database or durable-audit operation failed."""

    code = WORKER_LEASE_HEARTBEAT_FAILED


@dataclass(frozen=True)
class WorkerLeaseConfig:
    """Validated worker lease and heartbeat intervals."""

    lease_seconds: int
    heartbeat_seconds: int


def load_worker_lease_config(
    *,
    env: Mapping[str, str] | None = None,
    strict: bool = False,
) -> WorkerLeaseConfig:
    """Load typed worker lease intervals, failing closed for strict processes."""
    values = os.environ if env is None else env
    raw_lease = values.get("IDIS_WORKER_LEASE_SECONDS")
    raw_heartbeat = values.get("IDIS_WORKER_HEARTBEAT_SECONDS")

    if not strict and (raw_lease is None or not str(raw_lease).strip()):
        raw_lease = "60"
    if not strict and (raw_heartbeat is None or not str(raw_heartbeat).strip()):
        raw_heartbeat = "20"

    try:
        if raw_lease is None or raw_heartbeat is None:
            raise ValueError("missing worker lease interval")
        lease_seconds = int(str(raw_lease).strip())
        heartbeat_seconds = int(str(raw_heartbeat).strip())
    except (TypeError, ValueError) as exc:
        raise WorkerLeaseConfigError(WORKER_LEASE_CONFIG_INVALID) from exc

    if lease_seconds <= 0 or heartbeat_seconds <= 0 or heartbeat_seconds >= lease_seconds:
        raise WorkerLeaseConfigError(WORKER_LEASE_CONFIG_INVALID)
    return WorkerLeaseConfig(
        lease_seconds=lease_seconds,
        heartbeat_seconds=heartbeat_seconds,
    )


def build_worker_lease_audit_event(
    *,
    code: str,
    tenant_id: str,
    deal_id: str | None,
    run_id: str,
    lease_owner_id: str | None = None,
    prior_fencing_token: int | None = None,
    new_fencing_token: int | None = None,
    lease_expires_at: str | None = None,
    step_name: StepName | str | None = None,
    boundary: str | None = None,
) -> dict[str, Any]:
    """Build the schema-safe durable evidence event for a lease transition."""
    try:
        event_type = _LEASE_EVENT_TYPES[code]
    except KeyError as exc:
        raise ValueError(f"unsupported worker lease audit code: {code}") from exc

    safe: dict[str, Any] = {"code": code, "run_id": run_id}
    if code == "WORKER_LEASE_CLAIMED":
        safe.update(
            lease_owner_id=lease_owner_id,
            new_fencing_token=new_fencing_token,
            lease_expires_at=lease_expires_at,
        )
    elif code == "WORKER_LEASE_HEARTBEAT_RENEWED":
        safe.update(
            lease_owner_id=lease_owner_id,
            current_fencing_token=new_fencing_token,
            lease_expires_at=lease_expires_at,
        )
    elif code in {"WORKER_LEASE_EXPIRED", "WORKER_LEASE_RECLAIMED"}:
        safe.update(
            prior_lease_owner_id=lease_owner_id,
            prior_fencing_token=prior_fencing_token,
            new_fencing_token=new_fencing_token,
        )
    else:
        safe.update(
            step_name=step_name.value if isinstance(step_name, StepName) else step_name,
            boundary=boundary,
        )

    resource_id = deal_id or run_id
    resource: dict[str, Any] = {
        "resource_type": "deal" if deal_id else "run",
        "resource_id": resource_id,
    }
    if deal_id is not None:
        resource["deal_id"] = deal_id
    return {
        "event_id": str(uuid.uuid4()),
        "occurred_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "tenant_id": tenant_id,
        "actor": {"actor_type": "SERVICE", "actor_id": "idis-pipeline-worker"},
        "request": {
            "request_id": run_id,
            "method": "POST",
            "path": f"/v1/runs/{run_id}/worker-lease",
            "status_code": 200,
        },
        "resource": resource,
        "event_type": event_type,
        "severity": "MEDIUM",
        "summary": f"{event_type} for run {run_id}",
        "payload": {"safe": safe, "hashes": [], "refs": []},
    }


def emit_worker_lease_audit_in_tx(
    *,
    db_conn: Any,
    audit_sink: Any,
    event: dict[str, Any],
) -> None:
    """Validate and emit a lease event on the transition's transaction."""
    validation = validate_audit_event(event)
    if not validation.passed:
        codes = [error.code for error in validation.errors]
        raise AuditSinkError(f"WORKER_LEASE_AUDIT_INVALID: {codes}")
    emit_in_tx = getattr(audit_sink, "emit_in_tx", None)
    if not callable(emit_in_tx):
        raise AuditSinkError("WORKER_LEASE_AUDIT_SINK_UNAVAILABLE")
    emit_in_tx(db_conn, event)


def _lease_transition_audit_emitter(
    *,
    audit_sink: Any,
    tenant_id: str,
) -> Callable[..., None]:
    """Bind repository transition callbacks to the durable lease event schema."""

    def emit(
        *,
        db_conn: Any,
        code: str,
        run_id: str,
        deal_id: str | None = None,
        lease_owner_id: str | None = None,
        prior_lease_owner_id: str | None = None,
        prior_fencing_token: int | None = None,
        new_fencing_token: int | None = None,
        current_fencing_token: int | None = None,
        lease_expires_at: str | None = None,
        **_safe: Any,
    ) -> None:
        event = build_worker_lease_audit_event(
            code=code,
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=str(run_id),
            lease_owner_id=lease_owner_id or prior_lease_owner_id,
            prior_fencing_token=prior_fencing_token,
            new_fencing_token=(
                current_fencing_token
                if code == "WORKER_LEASE_HEARTBEAT_RENEWED"
                else new_fencing_token
            ),
            lease_expires_at=lease_expires_at,
            step_name=None,
            boundary=None,
        )
        emit_worker_lease_audit_in_tx(
            db_conn=db_conn,
            audit_sink=audit_sink,
            event=event,
        )

    return emit


def build_worker_lease_renewal(
    *,
    connection_factory: Callable[[], Any],
    audit_sink: Any,
    tenant_id: str,
    run_id: str,
    lease_owner_id: str,
    lease_fencing_token: int,
    lease_seconds: int,
) -> Callable[[], bool]:
    """Bind a fresh tenant-scoped transaction to each heartbeat tick."""
    emitter = _lease_transition_audit_emitter(
        audit_sink=audit_sink,
        tenant_id=tenant_id,
    )

    def renew() -> bool:
        try:
            with connection_factory() as conn:
                set_tenant_local(conn, tenant_id)
                repository = PostgresRunsRepository(conn, tenant_id)
                renewed = repository.renew_worker_lease(
                    run_id=run_id,
                    lease_owner_id=lease_owner_id,
                    lease_fencing_token=lease_fencing_token,
                    lease_seconds=lease_seconds,
                    lease_audit_emitter=emitter,
                )
                if not renewed:
                    raise WorkerLeaseFencedError("worker lease renewal was fenced")
            return True
        except WorkerLeaseFencedError:
            raise
        except Exception as exc:
            raise WorkerLeaseHeartbeatError(WORKER_LEASE_HEARTBEAT_FAILED) from exc

    return renew


def execute_with_worker_lease_heartbeat(
    *,
    operation: Callable[[], Any],
    renew: Callable[[], bool],
    heartbeat_seconds: float,
) -> Any:
    """Execute a provider operation while renewing its lease at fixed cadence.

    Provider work remains on the caller thread while a joined background loop
    owns only short heartbeat transactions.  If renewal is fenced or unavailable,
    the provider is allowed to unwind, its result is discarded, and no completion
    boundary can be entered by this worker.
    """
    stop_heartbeat = threading.Event()
    renewal_errors: list[BaseException] = []

    def heartbeat_loop() -> None:
        while not stop_heartbeat.wait(timeout=heartbeat_seconds):
            try:
                renewed = renew()
            except (WorkerLeaseFencedError, WorkerLeaseHeartbeatError) as exc:
                renewal_errors.append(exc)
                return
            except BaseException as exc:
                failure = WorkerLeaseHeartbeatError(WORKER_LEASE_HEARTBEAT_FAILED)
                failure.__cause__ = exc
                renewal_errors.append(failure)
                return
            if renewed is not True:
                renewal_errors.append(WorkerLeaseFencedError("worker lease renewal was fenced"))
                return

    heartbeat_thread = threading.Thread(
        target=heartbeat_loop,
        name="idis-worker-lease-heartbeat",
    )
    heartbeat_thread.start()
    operation_result: Any = None
    operation_error: BaseException | None = None
    try:
        # Provider connections and transaction-affine resources remain on the
        # worker's calling thread.  Lease loss suppresses this result only after
        # the provider has returned and released those resources.
        operation_result = operation()
    except BaseException as exc:  # preserve deliberate crash/fence probes
        operation_error = exc
    finally:
        stop_heartbeat.set()
        heartbeat_thread.join()

    if renewal_errors:
        raise renewal_errors[0]
    if operation_error is not None:
        raise operation_error
    return operation_result


class InvalidRunSourceMetadataError(ValueError):
    """Stored run source metadata failed schema validation."""


class InvalidRunSourceSelectionError(ValueError):
    """Stored run source selected documents absent from persisted corpus."""


class WorkerAuditConfigurationError(RuntimeError):
    """Durable worker audit sink could not be configured."""


class PipelineWorker:
    """Background worker that processes queued runs."""

    def __init__(
        self,
        poll_interval: float = 5,
        gdbs_path: str | None = None,
        tenant_ids: list[str] | None = None,
        execution_service_factory: ExecutionServiceFactory | None = None,
        run_context_factory: RunContextFactory | None = None,
        lease_config: WorkerLeaseConfig | None = None,
        strict_profile: StrictRuntimeProfile | None = None,
    ) -> None:
        """Initialize worker.

        Args:
            poll_interval: Seconds between polls for new runs.
            gdbs_path: Path to GDBS dataset for loading synthetic claims.
            tenant_ids: Explicit tenant scopes the worker may poll.
            execution_service_factory: Optional factory for tests.
            run_context_factory: Optional factory for tests.
        """
        self._poll_interval = poll_interval
        self._gdbs_path = gdbs_path
        self._uses_production_runtime = (
            execution_service_factory is None and run_context_factory is None
        )
        ambient_strict = is_strict_full_live_required(
            env=os.environ,
            dotenv_path=os.environ.get(IDIS_STRICT_DOTENV_PATH_ENV),
        )
        if strict_profile is not None:
            try:
                require_runtime_profile(strict_profile, command="worker")
            except StrictProfileError as exc:
                raise RuntimeError(exc.code) from exc
        elif ambient_strict and self._uses_production_runtime:
            raise RuntimeError("STRICT_PROFILE_LOAD_FAILED")
        self._strict_profile = strict_profile
        profile_tenant_ids = (
            get_worker_tenant_ids(env=strict_profile.values) if strict_profile is not None else None
        )
        if (
            strict_profile is not None
            and tenant_ids is not None
            and tenant_ids != profile_tenant_ids
        ):
            raise RuntimeError("STRICT_PROFILE_MISMATCH")
        self._tenant_ids = (
            tenant_ids
            if tenant_ids is not None
            else profile_tenant_ids
            if profile_tenant_ids is not None
            else get_worker_tenant_ids()
        )
        self._execution_service_factory = (
            execution_service_factory or _default_execution_service_factory
        )
        self._run_context_factory = run_context_factory or _default_run_context_factory
        self._lease_config = lease_config
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the worker."""
        runtime_env = (
            self._strict_profile.values if self._strict_profile is not None else os.environ
        )
        require_strict_process_role(
            "worker",
            env=runtime_env,
            dotenv_path=(
                None
                if self._strict_profile is not None
                else os.environ.get(IDIS_STRICT_DOTENV_PATH_ENV)
            ),
        )
        if self._lease_config is None:
            self._lease_config = load_worker_lease_config(env=runtime_env, strict=True)
        if self._running:
            logger.warning("Worker already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("Pipeline worker started")

    async def stop(self) -> None:
        """Stop the worker."""
        if not self._running:
            return

        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        logger.info("Pipeline worker stopped")

    async def _poll_loop(self) -> None:
        """Main polling loop."""
        while self._running:
            try:
                await self._process_queued_runs()
            except Exception as e:
                logger.error(f"Error in worker poll loop: {e}", exc_info=True)

            await asyncio.sleep(self._poll_interval)

    def _observability_sink(self) -> Any:
        """Lazily build a best-effort audit sink for observability signals (None if unavailable)."""
        if not hasattr(self, "_obs_sink"):
            try:
                self._obs_sink = _default_worker_audit_sink()
            except Exception:
                self._obs_sink = None
        return self._obs_sink

    async def _process_queued_runs(self) -> int:
        """Reclaim/claim at most one run per tenant and execute its fenced lease."""
        if not self._tenant_ids:
            logger.info("Pipeline worker has no tenant scope configured; skipping queued polling")
            return 0

        if self._lease_config is None:
            # Public startup/CLI entry points validate strictly before reaching
            # this seam.  The non-strict fallback keeps direct hermetic calls
            # typed without weakening those process boundaries.
            self._lease_config = load_worker_lease_config(strict=False)
        lease_config = self._lease_config
        engine = get_app_engine()
        audit_sink = _default_worker_audit_sink()
        processed_count = 0

        for tenant_id in self._tenant_ids:
            claimed: list[dict[str, Any]] = []
            queued_count = 0
            with engine.connect() as conn:
                set_tenant_local(conn, tenant_id)
                runs_repo = get_runs_repository(conn, tenant_id)
                transition_audit = _lease_transition_audit_emitter(
                    audit_sink=audit_sink,
                    tenant_id=tenant_id,
                )

                try:
                    runs_repo.requeue_expired_worker_leases(
                        limit=1,
                        lease_audit_emitter=transition_audit,
                    )
                    conn.commit()
                except Exception as exc:
                    conn.rollback()
                    raise WorkerLeaseReclaimError(WORKER_LEASE_RECLAIM_FAILED) from exc

                set_tenant_local(conn, tenant_id)
                try:
                    claimed = runs_repo.claim_queued_runs(
                        limit=1,
                        lease_owner_id=_WORKER_INSTANCE_ID,
                        lease_seconds=lease_config.lease_seconds,
                        lease_audit_emitter=transition_audit,
                    )
                    count_queued = getattr(runs_repo, "count_queued_runs", None)
                    queued_count = int(count_queued()) if callable(count_queued) else 0
                    conn.commit()
                except Exception as exc:
                    conn.rollback()
                    raise WorkerLeaseClaimError(WORKER_LEASE_CLAIM_FAILED) from exc

                if not claimed:
                    continue

                try:
                    emit_run_signal(
                        audit_sink,
                        event_type=RUN_QUEUE_OBSERVED,
                        tenant_id=tenant_id,
                        details={
                            "queued_count": queued_count,
                            "claimed_count": len(claimed),
                        },
                    )
                except Exception:  # observability is best-effort; never break the poll
                    logger.debug("queue-depth signal skipped", exc_info=True)
                logger.info("Claimed %s queued run for tenant %s", len(claimed), tenant_id)

                if not self._uses_production_runtime:
                    for run_data in claimed:
                        run_id = str(run_data["run_id"])
                        processed_count += 1
                        self._verify_claimed_run_preflight(
                            conn=conn,
                            tenant_id=tenant_id,
                            run_data=run_data,
                        )
                        service = self._execution_service_factory(
                            db_conn=conn,
                            tenant_id=tenant_id,
                        )
                        ctx = self._run_context_factory(
                            db_conn=conn,
                            tenant_id=tenant_id,
                            run_data=run_data,
                            audit_sink=service.audit_sink,
                        )
                        await asyncio.to_thread(service.execute_preclaimed, ctx)
                        logger.info("Completed preclaimed run %s", run_id)

            if not claimed or not self._uses_production_runtime:
                continue

            # The claim transaction and its polling connection are both gone
            # before provider composition.  Every fenced boundary/heartbeat
            # opens its own short tenant-scoped transaction.
            for run_data in claimed:
                processed_count += 1
                transaction_factory = getattr(engine, "begin", engine.connect)
                runtime = build_postgres_claimed_run_runtime(
                    tenant_id=tenant_id,
                    run_data=run_data,
                    lease_config=lease_config,
                    audit_sink=audit_sink,
                    connection_factory=transaction_factory,
                    strict_profile=self._strict_profile,
                )
                await asyncio.to_thread(runtime.execute_preclaimed)
                logger.info("Completed preclaimed run %s", run_data["run_id"])

        return processed_count

    def _verify_claimed_run_preflight(
        self,
        *,
        conn: Any,
        tenant_id: str,
        run_data: dict[str, Any],
    ) -> None:
        """Preserve the strict preflight gate for the injected worker seam.

        The production composition root performs the equivalent provider-context
        validation.  This compatibility seam remains useful for hermetic tests,
        but still operates only after the durable lease claim has committed.
        """
        if str(run_data.get("mode", "")).upper() != "FULL":
            return
        runtime_env = (
            self._strict_profile.values if self._strict_profile is not None else os.environ
        )
        strict_dotenv_path = (
            None
            if self._strict_profile is not None
            else os.environ.get(IDIS_STRICT_DOTENV_PATH_ENV)
        )
        if not is_strict_full_live_required(env=runtime_env, dotenv_path=strict_dotenv_path):
            return
        if self._strict_profile is None:
            raise RuntimeError("STRICT_PROFILE_LOAD_FAILED")
        try:
            _verify_worker_strict_preflight_proof(
                db_conn=conn,
                tenant_id=tenant_id,
                run_data=run_data,
                strict_profile=self._strict_profile,
            )
            preflight_corpus = _load_worker_preflight_corpus(
                db_conn=conn,
                tenant_id=tenant_id,
                run_data=run_data,
            )
        except StrictPreflightProofError:
            raise
        except (InvalidRunSourceMetadataError, InvalidRunSourceSelectionError):
            raise
        strict_report = build_strict_full_live_admission_report(
            db_conn=conn,
            tenant_id=tenant_id,
            preflight_corpus=preflight_corpus,
            strict_dotenv_path=strict_dotenv_path,
        )
        if not strict_report.may_proceed:
            error = RuntimeError(STRICT_FULL_LIVE_BLOCKED)
            error.code = STRICT_FULL_LIVE_BLOCKED  # type: ignore[attr-defined]
            raise error

    def _mark_run_failed_after_exception(self, conn: Any, tenant_id: str, run_id: str) -> None:
        """Persist a terminal FAILED status after rollback clears the failed transaction.

        Uses the guarded completion so a cancellation that won the row-lock race after
        the claim commit is not overwritten: it only marks FAILED while the run is still
        RUNNING, leaving a CANCELLED/terminal status untouched.
        """
        try:
            set_tenant_local(conn, tenant_id)
            runs_repo = get_runs_repository(conn, tenant_id)
            finished_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            guarded = getattr(runs_repo, "try_complete_running", None)
            if callable(guarded):
                # Do not fall back to unconditional complete(): if the run is no longer
                # RUNNING (e.g. CANCELLED), the existing terminal status must be preserved.
                guarded(run_id, status="FAILED", finished_at=finished_at)
            else:
                runs_repo.complete(run_id, status="FAILED", finished_at=finished_at)
            conn.commit()
        except Exception:
            conn.rollback()
            logger.error(
                "Failed to persist FAILED status for run %s after execution error",
                run_id,
                extra={"run_id": run_id},
                exc_info=True,
            )

    def _persist_worker_preflight_block(
        self,
        *,
        conn: Any,
        tenant_id: str,
        run_id: str,
        reason_code: str,
        message: str,
        strict_report: Any = None,
    ) -> None:
        """Persist a safe preflight blocker using existing status and ledger surfaces.

        Fails the run only while it is still QUEUED/RUNNING. If the run was cancelled
        after the claim batch released its row lock, the guarded completion returns
        False: the existing terminal status is preserved and no preflight ledger step
        is written.

        When a strict readiness report is provided, safe StepProvenance for the
        blocking components is attached to the step result_summary (classes/statuses
        only — never raw env values, paths, secrets, or blocker text).
        """
        set_tenant_local(conn, tenant_id)
        runs_repo = get_runs_repository(conn, tenant_id)
        run_steps_repo = get_run_steps_repository(conn, tenant_id)
        finished_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        guarded = getattr(runs_repo, "try_complete_active", None)
        if callable(guarded):
            if not guarded(run_id, status="FAILED", finished_at=finished_at):
                # Run is no longer QUEUED/RUNNING (e.g. cancelled after the claim batch
                # released its row lock): preserve the terminal status and skip the
                # preflight ledger step.
                return
        else:
            # Minimal legacy repositories without the guard keep prior behavior.
            runs_repo.complete(run_id, status="FAILED", finished_at=finished_at)

        result_summary: dict[str, Any] = {"reason_code": reason_code}
        provenance_items = _safe_provenance_items(strict_report)
        if provenance_items:
            result_summary["provenance_items"] = provenance_items

        strict_step = RunStep(
            step_id=str(uuid.uuid4()),
            run_id=run_id,
            tenant_id=tenant_id,
            step_name=StepName.DOCUMENT_PREFLIGHT,
            step_order=3,
            status=StepStatus.FAILED,
            started_at=finished_at,
            finished_at=finished_at,
            result_summary=result_summary,
            error_code=reason_code,
            error_message=message,
        )
        run_steps_repo.create(strict_step)


def get_worker_tenant_ids(*, env: Mapping[str, str] | None = None) -> list[str]:
    """Return tenant IDs the worker is allowed to poll.

    Empty configuration is fail-safe: the worker does not globally scan queued
    runs without first setting an RLS tenant context.
    """
    values = os.environ if env is None else env
    raw = values.get("IDIS_WORKER_TENANT_IDS", "")
    return [item.strip() for item in raw.split(",") if item.strip()]


def get_gdbs_path() -> str | None:
    """Get GDBS dataset path from environment or default location."""
    env_path = os.getenv("IDIS_GDBS_PATH")
    if env_path:
        return env_path

    # Try default location relative to repo root
    repo_root = Path(__file__).parent.parent.parent.parent
    default_path = repo_root / "datasets" / "gdbs_full"

    if default_path.exists():
        return str(default_path)

    logger.warning("GDBS dataset path not found")
    return None


def _open_app_transaction() -> Any:
    """Lazily open a short application transaction without capturing a poll connection."""
    engine = get_app_engine()
    factory = getattr(engine, "begin", engine.connect)
    return factory()


def _commit_claim_and_restore_tenant_context(db_conn: Any, tenant_id: str) -> None:
    """Commit the QUEUED->RUNNING claim, then re-establish RLS tenant context.

    The claim commit releases the run row lock so a concurrent cancel can proceed.
    Tenant context is set via ``SET LOCAL idis.tenant_id`` which is transaction-scoped,
    so committing clears it; orchestration must run with tenant RLS context, so it is
    immediately re-applied before RunOrchestrator executes.
    """
    db_conn.commit()
    set_tenant_local(db_conn, tenant_id)


def _emit_worker_claim_audit_in_tx(
    *,
    db_conn: Any,
    audit_sink: Any,
    ctx: RunContext,
    claim_identity: dict[str, str],
) -> None:
    """Persist formal worker-claim proof before the claim transaction commits."""
    try:
        worker_instance_id = str(uuid.UUID(claim_identity["worker_instance_id"]))
        worker_claim_id = str(uuid.UUID(claim_identity["worker_claim_id"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise AuditSinkError("WORKER_CLAIM_IDENTITY_INVALID") from exc

    safe_payload: dict[str, Any] = {
        "run_id": ctx.run_id,
        "mode": ctx.mode,
        "worker_instance_id": worker_instance_id,
        "worker_claim_id": worker_claim_id,
    }
    if ctx.strict_preflight_proof_id is not None:
        safe_payload["strict_preflight_proof_id"] = ctx.strict_preflight_proof_id
    event = {
        "event_id": str(uuid.uuid4()),
        "occurred_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "tenant_id": ctx.tenant_id,
        "actor": {"actor_type": "SERVICE", "actor_id": "idis-pipeline-worker"},
        "request": {
            "request_id": ctx.run_id,
            "method": "POST",
            "path": f"/v1/runs/{ctx.run_id}",
            "status_code": 200,
        },
        "resource": {
            "resource_type": "deal",
            "resource_id": ctx.deal_id,
            "deal_id": ctx.deal_id,
        },
        "event_type": "deal.run.claimed",
        "severity": "LOW",
        "summary": f"deal.run.claimed for run {ctx.run_id}",
        "payload": {
            "safe": safe_payload,
            "hashes": [],
            "refs": [],
        },
    }

    emit_in_tx = getattr(audit_sink, "emit_in_tx", None)
    if callable(emit_in_tx):
        validation = validate_audit_event(event)
        if not validation.passed:
            codes = [error.code for error in validation.errors]
            raise AuditSinkError(f"WORKER_CLAIM_AUDIT_INVALID: {codes}")
        emit_in_tx(db_conn, event)
        return

    # The production factory returns PostgresAuditSink, whose emit_in_tx path
    # above is atomic.  This fallback preserves the existing hermetic worker
    # seam used by non-Postgres unit tests without weakening production.
    emit = getattr(audit_sink, "emit", None)
    if not callable(emit):
        raise AuditSinkError("WORKER_CLAIM_AUDIT_SINK_UNAVAILABLE")
    emit(event)


class _FreshRunsRepository:
    """Run reads/finalization on fresh tenant-scoped transactions."""

    def __init__(
        self,
        *,
        connection_factory: Callable[[], Any],
        tenant_id: str,
    ) -> None:
        self._connection_factory = connection_factory
        self._tenant_id = tenant_id

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self._connection_factory() as conn:
            set_tenant_local(conn, self._tenant_id)
            return get_runs_repository(conn, self._tenant_id).get(run_id)

    def try_mark_running(self, _run_id: str) -> bool:
        raise WorkerLeaseFencedError("preclaimed runtime cannot mark RUNNING twice")

    def try_complete_running(
        self,
        run_id: str,
        *,
        status: str,
        finished_at: str | None,
        lease_owner_id: str | None = None,
        lease_fencing_token: int | None = None,
    ) -> bool:
        with self._connection_factory() as conn:
            set_tenant_local(conn, self._tenant_id)
            return bool(
                get_runs_repository(conn, self._tenant_id).try_complete_running(
                    run_id,
                    status=status,
                    finished_at=finished_at,
                    lease_owner_id=lease_owner_id,
                    lease_fencing_token=lease_fencing_token,
                )
            )

    def complete(
        self,
        _run_id: str,
        *,
        status: str,
        finished_at: str | None,
    ) -> None:
        _ = (status, finished_at)
        raise WorkerLeaseFencedError("unfenced terminal fallback is disabled")


class _FreshRunStepsRepository:
    """Read-only orchestration view; all writes must enter the boundary UoW."""

    def __init__(
        self,
        *,
        connection_factory: Callable[[], Any],
        tenant_id: str,
    ) -> None:
        self._connection_factory = connection_factory
        self._tenant_id = tenant_id

    def get_step(self, run_id: str, step_name: StepName) -> RunStep | None:
        with self._connection_factory() as conn:
            set_tenant_local(conn, self._tenant_id)
            return get_run_steps_repository(conn, self._tenant_id).get_step(run_id, step_name)

    def get_by_run_id(self, run_id: str) -> list[RunStep]:
        with self._connection_factory() as conn:
            set_tenant_local(conn, self._tenant_id)
            return get_run_steps_repository(conn, self._tenant_id).get_by_run_id(run_id)

    def create(
        self,
        _step: RunStep,
        *,
        lease_owner_id: str | None = None,
        lease_fencing_token: int | None = None,
    ) -> RunStep:
        _ = (lease_owner_id, lease_fencing_token)
        raise WorkerLeaseFencedError("step create bypassed the boundary UoW")

    def update(
        self,
        _step: RunStep,
        *,
        lease_owner_id: str | None = None,
        lease_fencing_token: int | None = None,
    ) -> RunStep:
        _ = (lease_owner_id, lease_fencing_token)
        raise WorkerLeaseFencedError("step update bypassed the boundary UoW")


@dataclass
class ClaimedRunRuntime:
    """Production runtime composed around one already-claimed owner/token."""

    context: RunContext
    audit_sink: Any
    runs_repository_factory: Callable[[Any], Any]
    run_steps_repository_factory: Callable[[Any], Any]
    native_output_contracts: dict[StepName, Any]
    native_output_repository_factories: dict[StepName, dict[type[Any], Callable[[Any], Any]]]
    step_boundary_uow_fn: Callable[..., None]
    stage_native_output_writes_fn: Callable[..., tuple[Any, ...]]
    completed_step_resume_validator: Callable[[RunContext, RunStep], bool]
    execute_with_lease_heartbeat_fn: Callable[..., Any]
    _service: RunExecutionService

    def execute_preclaimed(self) -> Any:
        """Execute once without retaining a provider database transaction."""
        return self._service.execute_preclaimed(self.context)


class _BoundMethodologyTransaction:
    """Expose the already-open short transaction to the durable initializer."""

    @contextlib.contextmanager
    def transaction(self) -> Iterator[None]:
        """Let the connection-factory context own commit or rollback."""
        yield


def build_postgres_methodology_coverage_callbacks(
    *,
    connection_factory: Callable[[], Any],
    tenant_id: str,
    lease_owner_id: str,
    lease_fencing_token: int,
) -> tuple[Callable[..., Any], Callable[..., Any]]:
    """Build strict callbacks that open a fresh tenant transaction per operation."""
    configured_tenant_id = str(tenant_id)
    configured_lease_owner_id = str(lease_owner_id).strip()
    if (
        not configured_lease_owner_id
        or not isinstance(lease_fencing_token, int)
        or isinstance(lease_fencing_token, bool)
        or lease_fencing_token <= 0
    ):
        raise WorkerLeaseFencedError("methodology callbacks require an active lease identity")

    def initialize(
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        registry: Any = None,
    ) -> Any:
        from idis.persistence.repositories.methodologies import get_methodologies_repository
        from idis.services.runs.methodology_coverage_init import (
            DurableRunMethodologyCoverageInitService,
            MethodologyCoverageInitializationError,
        )

        if tenant_id != configured_tenant_id:
            raise MethodologyCoverageInitializationError
        # The orchestrator keeps the legacy registry keyword in its callable
        # protocol. Strict execution deliberately selects only the persisted,
        # hash-bound pair and therefore ignores that legacy value.
        _ = registry
        with connection_factory() as conn:
            set_tenant_local(conn, configured_tenant_id)
            service = DurableRunMethodologyCoverageInitService(
                methodology_repository=get_methodologies_repository(conn, configured_tenant_id),
                runs_repository=PostgresRunsRepository(conn, configured_tenant_id),
                unit_of_work=_BoundMethodologyTransaction(),
                lease_owner_id=configured_lease_owner_id,
                lease_fencing_token=lease_fencing_token,
            )
            return service.initialize_for_run(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
            )

    def rehydrate(*, tenant_id: str, deal_id: str, run_id: str) -> Any:
        from idis.persistence.repositories.methodologies import get_methodologies_repository
        from idis.services.runs.methodology_coverage_init import (
            MethodologyCoverageInitializationError,
            validate_durable_methodology_coverage_records,
        )

        if tenant_id != configured_tenant_id:
            raise MethodologyCoverageInitializationError
        with connection_factory() as conn:
            set_tenant_local(conn, configured_tenant_id)
            repository = get_methodologies_repository(conn, configured_tenant_id)
            return validate_durable_methodology_coverage_records(
                records=repository.list_coverage_records(deal_id=deal_id, run_id=run_id),
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
            )

    return initialize, rehydrate


def build_postgres_methodology_extraction_callbacks(
    *,
    connection_factory: Callable[[], Any],
    tenant_id: str,
    lease_owner_id: str,
    lease_fencing_token: int,
    audit_sink: Any,
    strict_profile: StrictRuntimeProfile | None,
) -> dict[str, Callable[..., Any]]:
    """Compose all five strict Task-6 callbacks over fresh PostgreSQL transactions.

    Construction performs only validation of the checked-in governed CDD map;
    it performs no credential read, prompt resolution, provider call, or database
    operation.  This lets the canonical worker be composed before Step 6 while
    preserving the Gate-A boundary; each callback opens only the short transaction
    needed by its own durable boundary.
    """
    from idis.methodology.category_mapping import (
        load_commercial_dd_question_category_map,
    )
    from idis.services.extraction.task_planner import (
        StrictDurableCategorySpanAdmissionPolicy,
    )

    configured_tenant_id = str(tenant_id)
    configured_lease_owner_id = str(lease_owner_id).strip()
    if (
        not configured_tenant_id
        or not configured_lease_owner_id
        or not isinstance(lease_fencing_token, int)
        or isinstance(lease_fencing_token, bool)
        or lease_fencing_token <= 0
    ):
        raise WorkerLeaseFencedError("methodology callbacks require an active lease identity")
    admission_policy = StrictDurableCategorySpanAdmissionPolicy(
        cdd_category_map_loader=load_commercial_dd_question_category_map,
    )

    def require_scope(callback_tenant_id: str) -> None:
        if str(callback_tenant_id) != configured_tenant_id:
            raise WorkerLeaseFencedError("methodology callback tenant scope mismatch")

    def plan(
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        run_step_id: str,
        document_preflight_summary: dict[str, Any],
    ) -> Any:
        from idis.persistence.repositories.methodologies import get_methodologies_repository
        from idis.persistence.repositories.methodology_extraction import (
            PostgresMethodologyExtractionRepository,
        )
        from idis.services.runs.methodology_extraction_task_planning import (
            DurableRunMethodologyExtractionTaskPlanningService,
        )

        require_scope(tenant_id)
        if not str(run_step_id).strip():
            raise WorkerLeaseFencedError("methodology planning requires a running step")
        with connection_factory() as conn:
            set_tenant_local(conn, configured_tenant_id)
            service = DurableRunMethodologyExtractionTaskPlanningService(
                methodology_repository=get_methodologies_repository(conn, configured_tenant_id),
                extraction_repository=PostgresMethodologyExtractionRepository(
                    conn, configured_tenant_id
                ),
                extraction_repository_factory=lambda boundary_conn: (
                    PostgresMethodologyExtractionRepository(boundary_conn, configured_tenant_id)
                ),
                runs_repository=PostgresRunsRepository(conn, configured_tenant_id),
                unit_of_work=_BoundMethodologyTransaction(),
                lease_owner_id=configured_lease_owner_id,
                lease_fencing_token=lease_fencing_token,
                admission_policy=admission_policy,
            )
            return service.plan_for_run(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                document_preflight_summary=document_preflight_summary,
            )

    def rehydrate_plan(*, tenant_id: str, deal_id: str, run_id: str) -> Any:
        from idis.models.run_step import STEP_ORDER, StepName, StepStatus
        from idis.persistence.repositories.methodologies import get_methodologies_repository
        from idis.persistence.repositories.methodology_extraction import (
            PostgresMethodologyExtractionRepository,
        )
        from idis.persistence.repositories.run_steps import PostgresRunStepsRepository
        from idis.services.runs.methodology_extraction_task_planning import (
            DurableRunMethodologyExtractionTaskPlanningService,
        )

        require_scope(tenant_id)
        try:
            with connection_factory() as conn:
                conn.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                set_tenant_local(conn, configured_tenant_id)
                step_repository = PostgresRunStepsRepository(conn, configured_tenant_id)
                preflight_step = step_repository.get_step(
                    run_id,
                    StepName.DOCUMENT_PREFLIGHT,
                )
                planning_step = step_repository.get_step(
                    run_id,
                    StepName.METHODOLOGY_EXTRACTION_TASK_PLANNING,
                )
                if (
                    preflight_step is None
                    or preflight_step.tenant_id != configured_tenant_id
                    or preflight_step.run_id != run_id
                    or preflight_step.step_name is not StepName.DOCUMENT_PREFLIGHT
                    or preflight_step.step_order != STEP_ORDER[StepName.DOCUMENT_PREFLIGHT]
                    or preflight_step.status is not StepStatus.COMPLETED
                    or planning_step is None
                    or planning_step.tenant_id != configured_tenant_id
                    or planning_step.run_id != run_id
                    or planning_step.step_name is not StepName.METHODOLOGY_EXTRACTION_TASK_PLANNING
                    or planning_step.step_order
                    != STEP_ORDER[StepName.METHODOLOGY_EXTRACTION_TASK_PLANNING]
                    or planning_step.status is not StepStatus.COMPLETED
                ):
                    raise RuntimeError("METHODOLOGY_TASK_COVERAGE_INCOMPLETE")

                extraction_repository = PostgresMethodologyExtractionRepository(
                    conn,
                    configured_tenant_id,
                )
                planning_result, tasks, staged_write = (
                    DurableRunMethodologyExtractionTaskPlanningService(
                        methodology_repository=get_methodologies_repository(
                            conn,
                            configured_tenant_id,
                        ),
                        extraction_repository=extraction_repository,
                        extraction_repository_factory=lambda boundary_conn: (
                            PostgresMethodologyExtractionRepository(
                                boundary_conn,
                                configured_tenant_id,
                            )
                        ),
                        runs_repository=PostgresRunsRepository(
                            conn,
                            configured_tenant_id,
                        ),
                        unit_of_work=_BoundMethodologyTransaction(),
                        lease_owner_id=configured_lease_owner_id,
                        lease_fencing_token=lease_fencing_token,
                        admission_policy=admission_policy,
                    ).plan_for_run(
                        tenant_id=tenant_id,
                        deal_id=deal_id,
                        run_id=run_id,
                        document_preflight_summary=preflight_step.result_summary,
                    )
                )
                records = extraction_repository.validate_recomputed_planned_tasks(
                    deal_id=deal_id,
                    run_id=run_id,
                    selection_id=staged_write.selection_id,
                    tasks=tasks,
                )
                if not records or planning_step.result_summary != (
                    planning_result.to_run_step_summary()
                ):
                    raise RuntimeError("METHODOLOGY_TASK_COVERAGE_INCOMPLETE")
                admission_policy.validate_rehydrated_tasks(tasks)
                return tasks
        except WorkerLeaseFencedError:
            raise
        except RuntimeError as exc:
            if str(exc) == "METHODOLOGY_TASK_COVERAGE_INCOMPLETE":
                raise
            raise RuntimeError("METHODOLOGY_TASK_COVERAGE_INCOMPLETE") from exc
        except Exception as exc:
            raise RuntimeError("METHODOLOGY_TASK_COVERAGE_INCOMPLETE") from exc

    def execute(**kwargs: Any) -> Any:
        require_scope(str(kwargs.get("tenant_id", "")))
        return _execute_postgres_methodology_tasks(
            connection_factory=connection_factory,
            configured_tenant_id=configured_tenant_id,
            lease_owner_id=configured_lease_owner_id,
            lease_fencing_token=lease_fencing_token,
            audit_sink=audit_sink,
            strict_profile=strict_profile,
            **kwargs,
        )

    def rehydrate_execution(*, tenant_id: str, deal_id: str, run_id: str) -> Any:
        require_scope(tenant_id)
        return _rehydrate_postgres_methodology_execution(
            connection_factory=connection_factory,
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
        )

    def materialize_claims(**kwargs: Any) -> Any:
        require_scope(str(kwargs.get("tenant_id", "")))
        return _materialize_postgres_methodology_claims(
            connection_factory=connection_factory,
            lease_owner_id=configured_lease_owner_id,
            lease_fencing_token=lease_fencing_token,
            audit_sink=audit_sink,
            **kwargs,
        )

    def rehydrate_claims(*, tenant_id: str, deal_id: str, run_id: str) -> Any:
        require_scope(tenant_id)
        return _rehydrate_postgres_methodology_claims(
            connection_factory=connection_factory,
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
        )

    def materialize_evidence(**kwargs: Any) -> Any:
        require_scope(str(kwargs.get("tenant_id", "")))
        return _materialize_postgres_methodology_evidence(
            connection_factory=connection_factory,
            lease_owner_id=configured_lease_owner_id,
            lease_fencing_token=lease_fencing_token,
            audit_sink=audit_sink,
            **kwargs,
        )

    def rehydrate_evidence(*, tenant_id: str, deal_id: str, run_id: str) -> Any:
        require_scope(tenant_id)
        return _rehydrate_postgres_methodology_evidence(
            connection_factory=connection_factory,
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
        )

    def materialize_sanads(**kwargs: Any) -> Any:
        require_scope(str(kwargs.get("tenant_id", "")))
        return _materialize_postgres_methodology_sanads(
            connection_factory=connection_factory,
            lease_owner_id=configured_lease_owner_id,
            lease_fencing_token=lease_fencing_token,
            audit_sink=audit_sink,
            **kwargs,
        )

    def rehydrate_sanads(*, tenant_id: str, deal_id: str, run_id: str) -> Any:
        require_scope(tenant_id)
        return _rehydrate_postgres_methodology_sanads(
            connection_factory=connection_factory,
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
        )

    callbacks = (
        (plan, rehydrate_plan, False),
        (execute, rehydrate_execution, True),
        (materialize_claims, rehydrate_claims, False),
        (materialize_evidence, rehydrate_evidence, False),
        (materialize_sanads, rehydrate_sanads, False),
    )
    for callback, rehydrate_callback, performs_external_calls in callbacks:
        for attribute, value in (
            ("persistence_backend", "postgresql"),
            ("external_calls_enabled", performs_external_calls),
            ("rehydrate", rehydrate_callback),
        ):
            setattr(callback, attribute, value)

    return {
        "methodology_extraction_task_planning_fn": plan,
        "methodology_extraction_task_execution_fn": execute,
        "methodology_claim_materialization_fn": materialize_claims,
        "methodology_evidence_item_materialization_fn": materialize_evidence,
        "methodology_sanad_creation_linking_grading_fn": materialize_sanads,
    }


def _execute_postgres_methodology_tasks(**kwargs: Any) -> Any:
    """Load the strict Step-7 runtime lazily, preserving the Gate-A boundary."""
    from idis.services.runs.methodology_postgres_runtime import (
        execute_postgres_methodology_tasks,
    )

    return execute_postgres_methodology_tasks(**kwargs)


def _rehydrate_postgres_methodology_execution(**kwargs: Any) -> Any:
    """Rehydrate Step 7 only from committed durable terminal rows."""
    from idis.services.runs.methodology_postgres_runtime import (
        rehydrate_postgres_methodology_execution,
    )

    return rehydrate_postgres_methodology_execution(**kwargs)


def _materialize_postgres_methodology_claims(**kwargs: Any) -> Any:
    from idis.services.runs.methodology_postgres_materialization_runtime import (
        materialize_postgres_methodology_claims,
    )

    return materialize_postgres_methodology_claims(**kwargs)


def _rehydrate_postgres_methodology_claims(**kwargs: Any) -> Any:
    from idis.services.runs.methodology_postgres_materialization_runtime import (
        rehydrate_postgres_methodology_claims,
    )

    return rehydrate_postgres_methodology_claims(**kwargs)


def _materialize_postgres_methodology_evidence(**kwargs: Any) -> Any:
    from idis.services.runs.methodology_postgres_materialization_runtime import (
        materialize_postgres_methodology_evidence,
    )

    return materialize_postgres_methodology_evidence(**kwargs)


def _rehydrate_postgres_methodology_evidence(**kwargs: Any) -> Any:
    from idis.services.runs.methodology_postgres_materialization_runtime import (
        rehydrate_postgres_methodology_evidence,
    )

    return rehydrate_postgres_methodology_evidence(**kwargs)


def _materialize_postgres_methodology_sanads(**kwargs: Any) -> Any:
    from idis.services.runs.methodology_postgres_materialization_runtime import (
        materialize_postgres_methodology_sanads,
    )

    return materialize_postgres_methodology_sanads(**kwargs)


def _rehydrate_postgres_methodology_sanads(**kwargs: Any) -> Any:
    from idis.services.runs.methodology_postgres_materialization_runtime import (
        rehydrate_postgres_methodology_sanads,
    )

    return rehydrate_postgres_methodology_sanads(**kwargs)


def _build_methodology_extractor_request(
    *,
    task_record: Any,
    question: Any,
    version_record: Any,
    methodology_question_sha256: str,
    live_spans: list[dict[str, Any]],
) -> Any:
    """Bind one durable task to its exact frozen question and live source spans."""
    from idis.models.methodology_extraction import (
        CDDMethodologySourceTrace,
        FDDMethodologySourceTrace,
        MethodologyCandidateSpan,
        MethodologyTaskExtractorRequest,
        canonical_json_bytes,
        validate_methodology_source_text_sha256,
    )

    task = task_record.task
    methodology_type = str(getattr(task.methodology_type, "value", task.methodology_type))
    if (
        str(task_record.methodology_version_record_id)
        != str(version_record.methodology_version_record_id)
        or str(task.methodology_version_id) != str(version_record.methodology_version_id)
        or str(task.methodology_question_id) != str(question.methodology_question_id)
        or str(task.methodology_version_id) != str(question.methodology_version_id)
        or methodology_type
        != str(getattr(question.methodology_type, "value", question.methodology_type))
    ):
        raise RuntimeError("METHODOLOGY_TASK_COVERAGE_INCOMPLETE")

    admitted_span_ids = tuple(str(item.span_id) for item in task.source_spans)
    admitted_spans = {str(item.span_id): item for item in task.source_spans}
    live_by_id = {str(item.get("span_id", "")): item for item in live_spans}
    if (
        not admitted_span_ids
        or len(admitted_span_ids) != len(set(admitted_span_ids))
        or set(live_by_id) != set(admitted_span_ids)
    ):
        raise RuntimeError("METHODOLOGY_SOURCE_LOCATOR_REJECTED")
    candidates: list[Any] = []
    for ordinal, span_id in enumerate(admitted_span_ids, start=1):
        raw = live_by_id[span_id]
        if (
            str(raw.get("document_id", "")) != str(task.document_id)
            or str(raw.get("tenant_id", task.tenant_id)) != str(task.tenant_id)
            or str(raw.get("deal_id", task.deal_id)) != str(task.deal_id)
            or not isinstance(raw.get("locator"), dict)
            or not isinstance(raw.get("text_excerpt"), str)
            or not str(raw["text_excerpt"]).strip()
        ):
            raise RuntimeError("METHODOLOGY_SOURCE_LOCATOR_REJECTED")
        locator_sha256 = hashlib.sha256(canonical_json_bytes(raw["locator"])).hexdigest()
        raw_content_sha256 = raw.get("content_hash")
        try:
            validate_methodology_source_text_sha256(
                text=raw["text_excerpt"],
                content_sha256=raw_content_sha256,
            )
        except ValueError as exc:
            raise RuntimeError("METHODOLOGY_SOURCE_LOCATOR_REJECTED") from exc
        if raw_content_sha256 != admitted_spans[span_id].content_hash:
            raise RuntimeError("METHODOLOGY_SOURCE_LOCATOR_REJECTED")
        candidates.append(
            MethodologyCandidateSpan(
                document_id=str(task.document_id),
                span_id=span_id,
                ordinal=ordinal,
                content_sha256=str(raw_content_sha256),
                locator_sha256=locator_sha256,
                text=str(raw["text_excerpt"]),
            )
        )

    trace = question.source_trace
    if str(trace.source_hash) != str(version_record.source_sha256):
        raise RuntimeError("METHODOLOGY_TASK_COVERAGE_INCOMPLETE")
    if methodology_type == "commercial_dd":
        typed_methodology_type: Literal["commercial_dd", "financial_dd"] = "commercial_dd"
        if trace.row_number is not None or trace.source_anchor_cell is not None:
            raise RuntimeError("METHODOLOGY_TASK_COVERAGE_INCOMPLETE")
        source_trace: Any = CDDMethodologySourceTrace(
            kind="cdd_section",
            source_section=str(trace.sheet_or_section),
            source_sha256=str(trace.source_hash),
        )
    elif methodology_type == "financial_dd":
        typed_methodology_type = "financial_dd"
        source_trace = FDDMethodologySourceTrace(
            kind="fdd_workbook_row",
            sheet_name=str(trace.sheet_or_section),
            row_number=trace.row_number,
            anchor_cell=trace.source_anchor_cell,
            source_sha256=str(trace.source_hash),
        )
    else:
        raise RuntimeError("METHODOLOGY_TASK_COVERAGE_INCOMPLETE")

    predicates = tuple(
        dict.fromkeys(
            str(value).strip()
            for value in (getattr(question, "line_item", None), getattr(question, "term", None))
            if isinstance(value, str) and value.strip()
        )
    )
    if not predicates:
        raise RuntimeError("METHODOLOGY_TASK_COVERAGE_INCOMPLETE")
    exact_question_text = question.question_text
    if type(exact_question_text) is not str or not exact_question_text.strip():
        raise RuntimeError("METHODOLOGY_TASK_COVERAGE_INCOMPLETE")
    required_evidence = tuple(item.model_dump(mode="json") for item in task.required_evidence)
    nested_calculations = getattr(task.expected_answer_schema, "required_calculations", None)
    calculation_values = (
        nested_calculations
        if nested_calculations is not None
        else getattr(task, "required_calculations", ())
    )
    required_calculations = tuple(item.model_dump(mode="json") for item in calculation_values)
    return MethodologyTaskExtractorRequest(
        tenant_id=str(task.tenant_id),
        deal_id=str(task.deal_id),
        run_id=str(task.run_id),
        extraction_task_id=str(task.extraction_task_id),
        task_input_sha256=str(task_record.task_input_sha256),
        methodology_type=typed_methodology_type,
        methodology_version_id=str(task.methodology_version_id),
        methodology_version_sha256=str(version_record.version_hash),
        methodology_question_id=str(task.methodology_question_id),
        methodology_question_record_id=str(task_record.methodology_question_record_id),
        methodology_question_sha256=str(methodology_question_sha256),
        source_trace=source_trace,
        question_text=exact_question_text,
        expected_answer_schema=task.expected_answer_schema.model_dump(mode="json"),
        required_evidence=required_evidence,
        candidate_spans=tuple(candidates),
        predicates=predicates,
        validation_requirements=tuple(task.validation_requirements),
        required_calculations=required_calculations,
    )


def build_postgres_claimed_run_runtime(
    *,
    tenant_id: str,
    run_data: dict[str, Any],
    lease_config: WorkerLeaseConfig,
    audit_sink: Any,
    connection_factory: Callable[[], Any],
    provider_context_factory: Callable[..., RunContext] | None = None,
    strict_profile: StrictRuntimeProfile | None = None,
) -> ClaimedRunRuntime:
    """Compose the canonical claimed-run path without retaining the poll connection."""
    from idis.persistence.repositories.task7_completion import (
        PostgresTask7CompletionPort,
    )
    from idis.pipeline.contract_registry import FULL_STRICT_V21_CONTRACT_HASH
    from idis.pipeline.task7_completion import (
        Task7CompletionFinalizationService,
        Task7LeaseScope,
        Task7RunResumeCommand,
        Task7StepCompletionCommand,
    )
    from idis.pipeline.task7_semantics import (
        PIPELINE_CONTRACT_UNAVAILABLE,
        SemanticValidationInput,
    )
    from idis.services.runs.execution import build_postgres_step_boundary_uow
    from idis.services.runs.orchestrator import RunStepBlockedError
    from idis.services.runs.steps import (
        build_postgres_native_output_stager,
        build_postgres_native_output_stager_registry,
    )

    owner_id = run_data.get("lease_owner_id")
    token = run_data.get("lease_fencing_token")
    if owner_id is None or token is None or str(run_data.get("status")) != "RUNNING":
        raise WorkerLeaseFencedError("claimed runtime received no active lease identity")
    owner_id = str(owner_id)
    token = int(token)
    pipeline_contract_version = run_data.get("pipeline_contract_version")
    if pipeline_contract_version in {
        "full-strict-v16",
        "full-strict-v17",
        "full-strict-v18",
        "full-strict-v20",
    }:
        raise RuntimeError("PIPELINE_CONTRACT_UNAVAILABLE")
    is_full_strict_v21 = pipeline_contract_version == "full-strict-v21"
    if is_full_strict_v21 and (
        run_data.get("pipeline_contract_hash") != FULL_STRICT_V21_CONTRACT_HASH
        or run_data.get("pipeline_resume_state") != "RESUMABLE"
    ):
        raise RuntimeError("PIPELINE_CONTRACT_HASH_MISMATCH")

    runtime_env = strict_profile.values if strict_profile is not None else os.environ
    strict_dotenv_path = (
        None if strict_profile is not None else os.environ.get(IDIS_STRICT_DOTENV_PATH_ENV)
    )
    if str(run_data.get("mode", "")).upper() == "FULL" and is_strict_full_live_required(
        env=runtime_env,
        dotenv_path=strict_dotenv_path,
    ):
        if strict_profile is None:
            raise RuntimeError("STRICT_PROFILE_LOAD_FAILED")
        try:
            require_runtime_profile(strict_profile, command="worker")
        except StrictProfileError as exc:
            raise RuntimeError(exc.code) from exc
        # The API's consume-and-create transaction is not trusted as worker admission.
        # Re-read the reciprocal durable link after claim commit, under fresh tenant RLS,
        # and release that transaction before any provider context is composed.
        with connection_factory() as proof_conn:
            set_tenant_local(proof_conn, tenant_id)
            _verify_worker_strict_preflight_proof(
                db_conn=proof_conn,
                tenant_id=tenant_id,
                run_data=run_data,
                strict_profile=strict_profile,
            )
            if is_full_strict_v21:
                get_strict_preflight_proofs_repository(
                    proof_conn,
                    tenant_id,
                ).require_task7_admission_graph_for_run(
                    deal_id=str(run_data["deal_id"]),
                    run_id=str(run_data["run_id"]),
                )

    def runs_repository_factory(conn: Any) -> Any:
        return get_runs_repository(conn, tenant_id)

    def run_steps_repository_factory(conn: Any) -> Any:
        return get_run_steps_repository(conn, tenant_id)

    contracts = build_postgres_native_output_stager_registry()
    stager = build_postgres_native_output_stager()
    boundary_uow = build_postgres_step_boundary_uow(
        audit_sink=audit_sink,
        connection_factory=connection_factory,
        lease_seconds=lease_config.lease_seconds,
    )
    renew = build_worker_lease_renewal(
        connection_factory=connection_factory,
        audit_sink=audit_sink,
        tenant_id=tenant_id,
        run_id=str(run_data["run_id"]),
        lease_owner_id=owner_id,
        lease_fencing_token=token,
        lease_seconds=lease_config.lease_seconds,
    )

    def with_heartbeat(
        *,
        operation: Callable[[], Any],
        step_name: StepName | None = None,
    ) -> Any:
        _ = step_name
        return execute_with_worker_lease_heartbeat(
            operation=operation,
            renew=renew,
            heartbeat_seconds=lease_config.heartbeat_seconds,
        )

    task7_scope: Task7LeaseScope | None = None
    task7_completion_service: Task7CompletionFinalizationService | None = None
    if is_full_strict_v21:
        task7_scope = Task7LeaseScope(
            tenant_id=tenant_id,
            deal_id=str(run_data["deal_id"]),
            run_id=str(run_data["run_id"]),
            pipeline_contract_version="full-strict-v21",
            pipeline_contract_hash=FULL_STRICT_V21_CONTRACT_HASH,
            pipeline_resume_state="RESUMABLE",
            lease_owner_id=owner_id,
            lease_fencing_token=token,
        )

        def task7_port_factory(
            connection: Any,
            scoped_tenant_id: str,
        ) -> PostgresTask7CompletionPort:
            return PostgresTask7CompletionPort(
                connection=connection,
                tenant_id=scoped_tenant_id,
                audit_sink=audit_sink,
                lease_seconds=lease_config.lease_seconds,
            )

        task7_completion_service = Task7CompletionFinalizationService(
            connection_factory=connection_factory,
            port_factory=task7_port_factory,
        )

    def completed_step_resume_validator(ctx: RunContext, step: RunStep) -> bool:
        expected_run_id = str(run_data["run_id"])
        if (
            ctx.tenant_id != tenant_id
            or ctx.run_id != expected_run_id
            or step.tenant_id != tenant_id
            or step.run_id != expected_run_id
        ):
            raise RunStepBlockedError(
                "STEP_SEMANTIC_VALIDATION_FAILED",
                "Completed-step checkpoint identity does not match the claimed run",
            )
        if task7_completion_service is not None and task7_scope is not None:
            return task7_completion_service.validate_completed_step(
                Task7RunResumeCommand(scope=task7_scope),
                step=step,
            )
        event_type = f"deal.run.step.{step.step_name.value.lower()}.completed"
        with connection_factory() as conn:
            set_tenant_local(conn, tenant_id)
            row = conn.execute(
                text(
                    """
                    SELECT event
                    FROM audit_events
                    WHERE tenant_id = :tenant_id
                      AND request_id = :run_id
                      AND event_type = :event_type
                      AND event -> 'payload' -> 'safe' ->> 'run_id' = :run_id
                      AND event -> 'payload' -> 'safe' ->> 'step_id' = :step_id
                      AND event -> 'payload' -> 'safe' ->> 'step_name' = :step_name
                    ORDER BY occurred_at DESC, event_id DESC
                    LIMIT 1
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "event_type": event_type,
                    "run_id": expected_run_id,
                    "step_id": step.step_id,
                    "step_name": step.step_name.value,
                },
            ).fetchone()
        if row is None:
            raise RunStepBlockedError(
                "STEP_SEMANTIC_VALIDATION_FAILED",
                "Completed step has no immutable completion checkpoint",
            )
        mapping = getattr(row, "_mapping", None)
        raw_event = mapping.get("event") if mapping is not None else getattr(row, "event", row)
        if isinstance(raw_event, str):
            try:
                raw_event = json.loads(raw_event)
            except (TypeError, ValueError) as exc:
                raise RunStepBlockedError(
                    "STEP_SEMANTIC_VALIDATION_FAILED",
                    "Completed-step checkpoint audit is malformed",
                ) from exc
        if not isinstance(raw_event, dict):
            raise RunStepBlockedError(
                "STEP_SEMANTIC_VALIDATION_FAILED",
                "Completed-step checkpoint audit is malformed",
            )
        safe = raw_event.get("payload", {}).get("safe", {})
        if (
            raw_event.get("tenant_id") != tenant_id
            or raw_event.get("event_type") != event_type
            or not isinstance(safe, dict)
            or safe.get("run_id") != expected_run_id
            or safe.get("step_id") != step.step_id
            or safe.get("step_name") != step.step_name.value
            or safe.get("result_summary_sha256")
            != canonical_result_summary_sha256(step.result_summary)
        ):
            raise RunStepBlockedError(
                "STEP_SEMANTIC_VALIDATION_FAILED",
                "Immutable completion checkpoint does not match the completed step ledger",
            )
        return True

    def task7_step_completion_fn(
        *,
        ctx: RunContext,
        step: RunStep,
        result_summary: dict[str, Any],
        staged_native_output_writes: tuple[Any, ...],
    ) -> None:
        if task7_completion_service is None or task7_scope is None:
            raise RunStepBlockedError(
                PIPELINE_CONTRACT_UNAVAILABLE,
                "Task 7 completion service is unavailable",
            )
        semantic_input = result_summary.get("_task7_semantic_validation_input")
        if not isinstance(semantic_input, SemanticValidationInput):
            raise RunStepBlockedError(
                PIPELINE_CONTRACT_UNAVAILABLE,
                "Step producer supplied no typed Task 7 semantic evidence",
            )
        if (
            ctx.run_id != task7_scope.run_id
            or ctx.tenant_id != task7_scope.tenant_id
            or ctx.deal_id != task7_scope.deal_id
            or semantic_input.step_name != step.step_name.value
            or semantic_input.semantic_rule_version != step.semantic_rule_version
            or semantic_input.semantic_rule_hash != step.semantic_rule_hash
        ):
            raise RunStepBlockedError(
                "PIPELINE_CONTRACT_HASH_MISMATCH",
                "Typed Task 7 semantic evidence escaped its step scope",
            )
        task7_completion_service.complete_step(
            Task7StepCompletionCommand(
                scope=task7_scope,
                step_id=step.step_id,
                step_name=step.step_name.value,
                semantic_input=semantic_input,
                staged_native_writes=tuple(staged_native_output_writes),
            )
        )

    components = {
        "tenant_id": tenant_id,
        "run_data": run_data,
        "lease_owner_id": owner_id,
        "lease_fencing_token": token,
        "step_boundary_uow_fn": boundary_uow,
        "stage_native_output_writes_fn": stager,
        "completed_step_resume_validator": completed_step_resume_validator,
        "execute_with_lease_heartbeat_fn": with_heartbeat,
    }
    if strict_profile is not None:
        components["strict_profile"] = strict_profile
    if provider_context_factory is not None:
        context = provider_context_factory(**components)
    else:
        provider_reads = ProviderReadConnection(
            connection_factory=connection_factory,
            tenant_id=tenant_id,
        )

        provider_output_guard = _build_provider_output_guard(
            provider_reads=provider_reads,
            contracts=contracts,
        )

        with connection_factory() as bootstrap_conn:
            set_tenant_local(bootstrap_conn, tenant_id)
            context = _default_run_context_factory(
                db_conn=bootstrap_conn,
                provider_db_conn=provider_reads,
                provider_output_guard_fn=provider_output_guard,
                tenant_id=tenant_id,
                run_data=run_data,
                audit_sink=audit_sink,
                lease_config=lease_config,
                connection_factory=connection_factory,
                strict_profile=strict_profile,
            )
    context.strict_resume_validation_required = True
    context.completed_step_resume_validator = completed_step_resume_validator
    if task7_completion_service is not None:
        context.task7_step_completion_fn = task7_step_completion_fn

    native_repository_factories: dict[
        StepName,
        dict[type[Any], Callable[[Any], Any]],
    ] = {}
    for step_name, contract in contracts.items():
        factories: dict[type[Any], Callable[[Any], Any]] = {}
        for binding in contract.write_bindings:
            repository_type = binding.repository_type

            def build_repository(
                conn: Any,
                repository_type: type[Any] = repository_type,
            ) -> Any:
                return repository_type(conn, tenant_id)

            factories[repository_type] = build_repository
        native_repository_factories[step_name] = factories

    service = RunExecutionService(
        audit_sink=audit_sink,
        runs_repo=_FreshRunsRepository(
            connection_factory=connection_factory,
            tenant_id=tenant_id,
        ),
        run_steps_repo=_FreshRunStepsRepository(
            connection_factory=connection_factory,
            tenant_id=tenant_id,
        ),
        task7_completion_service=task7_completion_service,
    )
    return ClaimedRunRuntime(
        context=context,
        audit_sink=audit_sink,
        runs_repository_factory=runs_repository_factory,
        run_steps_repository_factory=run_steps_repository_factory,
        native_output_contracts=contracts,
        native_output_repository_factories=native_repository_factories,
        step_boundary_uow_fn=boundary_uow,
        stage_native_output_writes_fn=stager,
        completed_step_resume_validator=completed_step_resume_validator,
        execute_with_lease_heartbeat_fn=with_heartbeat,
        _service=service,
    )


def _default_execution_service_factory(
    *,
    db_conn: Any,
    tenant_id: str,
) -> RunExecutionService:
    audit_sink = _default_worker_audit_sink()
    claim_identity = {
        "worker_instance_id": _WORKER_INSTANCE_ID,
        "worker_claim_id": str(uuid.uuid4()),
    }
    return RunExecutionService(
        audit_sink=audit_sink,
        runs_repo=get_runs_repository(db_conn, tenant_id),
        run_steps_repo=get_run_steps_repository(db_conn, tenant_id),
        claim_identity=claim_identity,
        claim_audit_emitter=lambda ctx, identity: _emit_worker_claim_audit_in_tx(
            db_conn=db_conn,
            audit_sink=audit_sink,
            ctx=ctx,
            claim_identity=identity,
        ),
        after_claim_commit=lambda: _commit_claim_and_restore_tenant_context(db_conn, tenant_id),
    )


def _default_worker_audit_sink() -> Any:
    """Build the durable worker audit sink, failing closed if unavailable."""
    try:
        from idis.audit.postgres_sink import PostgresAuditSink

        return PostgresAuditSink()
    except Exception as exc:
        raise WorkerAuditConfigurationError("WORKER_AUDIT_SINK_UNAVAILABLE") from exc


def _load_worker_preflight_corpus(
    *,
    db_conn: Any,
    tenant_id: str,
    run_data: dict[str, Any],
) -> list[dict[str, Any]]:
    from idis.services.runs.steps import (
        filter_preflight_corpus_by_run_source,
        load_document_preflight_corpus_for_deal,
        missing_document_ids_for_run_source,
    )

    deal_id = str(run_data["deal_id"])
    preflight_corpus = load_document_preflight_corpus_for_deal(
        db_conn=db_conn,
        deal_id=deal_id,
        tenant_id=tenant_id,
    )
    source: dict[str, Any] | RunSource | None = run_data.get("source")
    if source is not None and not isinstance(source, RunSource):
        try:
            source = RunSource.model_validate(source)
        except ValidationError as exc:
            raise InvalidRunSourceMetadataError("invalid run source metadata") from exc
    if isinstance(source, RunSource):
        missing = missing_document_ids_for_run_source(preflight_corpus, source)
        if missing:
            raise InvalidRunSourceSelectionError("run source selected missing documents")
    return filter_preflight_corpus_by_run_source(preflight_corpus, source)


def _verify_worker_strict_preflight_proof(
    *,
    db_conn: Any,
    tenant_id: str,
    run_data: dict[str, Any],
    strict_profile: StrictRuntimeProfile,
) -> None:
    """Fail closed unless a strict queued run has its consumed reciprocal proof link."""
    try:
        require_runtime_profile(strict_profile, command="worker")
    except StrictProfileError as exc:
        raise StrictPreflightProofError(exc.code) from exc
    proof_id = run_data.get("strict_preflight_proof_id")
    if not isinstance(proof_id, str) or not proof_id.strip():
        raise StrictPreflightProofError("STRICT_PREFLIGHT_PROOF_REQUIRED")

    repository = get_strict_preflight_proofs_repository(db_conn, tenant_id)
    repository.require_consumed_for_run(
        proof_id=proof_id,
        run_id=str(run_data["run_id"]),
        deal_id=str(run_data["deal_id"]),
        strict_profile_generation=strict_profile.generation,
        strict_profile_hmac=strict_profile.base_profile_hmac,
    )


def _safe_provenance_items(strict_report: Any) -> list[dict[str, Any]]:
    """Serialize safe StepProvenance for a strict report's blocking components."""
    if strict_report is None:
        return []
    from idis.services.runs.strict_full_live import build_blocking_step_provenance

    return [item.model_dump(mode="json") for item in build_blocking_step_provenance(strict_report)]


def _default_run_context_factory(
    *,
    db_conn: Any,
    provider_db_conn: Any = None,
    provider_output_guard_fn: Callable[..., None] | None = None,
    tenant_id: str,
    run_data: dict[str, Any],
    audit_sink: Any,
    lease_config: WorkerLeaseConfig | None = None,
    connection_factory: Callable[[], Any] | None = None,
    strict_profile: StrictRuntimeProfile | None = None,
) -> RunContext:
    """Build a worker run context using shared run step wiring."""
    from idis.services.runs.steps import (
        build_postgres_native_output_stager,
        build_run_context,
        extraction_ready_documents_from_preflight_corpus,
    )

    deal_id = str(run_data["deal_id"])
    preflight_corpus = _load_worker_preflight_corpus(
        db_conn=db_conn,
        tenant_id=tenant_id,
        run_data=run_data,
    )

    runtime_env = strict_profile.values if strict_profile is not None else os.environ
    strict_dotenv_path = (
        None if strict_profile is not None else os.environ.get(IDIS_STRICT_DOTENV_PATH_ENV)
    )
    strict_live_extraction_required = str(
        run_data["mode"]
    ).upper() == "FULL" and is_strict_full_live_required(
        env=runtime_env,
        dotenv_path=strict_dotenv_path,
    )
    methodology_coverage_init_fn: Callable[..., Any] | None = None
    methodology_coverage_rehydrate_fn: Callable[..., Any] | None = None
    methodology_extraction_callbacks: dict[str, Callable[..., Any]] = {}
    if strict_live_extraction_required:
        if connection_factory is None:
            raise RuntimeError("METHODOLOGY_COVERAGE_INCOMPLETE")
        (
            methodology_coverage_init_fn,
            methodology_coverage_rehydrate_fn,
        ) = build_postgres_methodology_coverage_callbacks(
            connection_factory=connection_factory,
            tenant_id=tenant_id,
            lease_owner_id=str(run_data.get("lease_owner_id") or ""),
            lease_fencing_token=int(run_data.get("lease_fencing_token") or 0),
        )
        methodology_extraction_callbacks = build_postgres_methodology_extraction_callbacks(
            connection_factory=connection_factory,
            tenant_id=tenant_id,
            lease_owner_id=str(run_data.get("lease_owner_id") or ""),
            lease_fencing_token=int(run_data.get("lease_fencing_token") or 0),
            audit_sink=audit_sink,
            strict_profile=strict_profile,
        )
    from idis.services.ingestion.no_substantive_content_attestation import (
        VerifiedDocumentNoSubstantiveContentAttestation,
        VerifiedDocumentNoSubstantiveContentAttestationManifest,
        build_document_no_substantive_content_receipt_v2_verifier,
        build_document_no_substantive_content_receipt_verifier,
        load_document_no_substantive_content_attestation,
    )

    no_substantive_content_attestation = load_document_no_substantive_content_attestation(
        env=runtime_env
    )
    if isinstance(
        no_substantive_content_attestation,
        VerifiedDocumentNoSubstantiveContentAttestation,
    ):
        no_substantive_content_receipt_verifier = (
            build_document_no_substantive_content_receipt_verifier(
                no_substantive_content_attestation
            )
        )
    elif isinstance(
        no_substantive_content_attestation,
        VerifiedDocumentNoSubstantiveContentAttestationManifest,
    ):
        no_substantive_content_receipt_verifier = (
            build_document_no_substantive_content_receipt_v2_verifier(
                no_substantive_content_attestation
            )
        )
    else:
        no_substantive_content_receipt_verifier = None

    lease_owner_id = run_data.get("lease_owner_id")
    lease_fencing_token = run_data.get("lease_fencing_token")
    lease_callbacks: dict[str, Any] = {}
    if (lease_owner_id is None) != (lease_fencing_token is None):
        raise WorkerLeaseFencedError("claimed run has an incomplete lease identity")
    if lease_owner_id is not None and lease_fencing_token is not None:
        from idis.services.runs.execution import build_postgres_step_boundary_uow

        config = lease_config or load_worker_lease_config(env=runtime_env, strict=True)
        fresh_connection = connection_factory or _open_app_transaction
        boundary_uow = build_postgres_step_boundary_uow(
            audit_sink=audit_sink,
            connection_factory=fresh_connection,
            lease_seconds=config.lease_seconds,
        )
        renew = build_worker_lease_renewal(
            connection_factory=fresh_connection,
            audit_sink=audit_sink,
            tenant_id=tenant_id,
            run_id=str(run_data["run_id"]),
            lease_owner_id=str(lease_owner_id),
            lease_fencing_token=int(lease_fencing_token),
            lease_seconds=config.lease_seconds,
        )

        def execute_with_lease_heartbeat(
            *,
            operation: Callable[[], Any],
            step_name: StepName | None = None,
        ) -> Any:
            _ = step_name
            return execute_with_worker_lease_heartbeat(
                operation=operation,
                renew=renew,
                heartbeat_seconds=config.heartbeat_seconds,
            )

        lease_callbacks = {
            "lease_owner_id": str(lease_owner_id),
            "lease_fencing_token": int(lease_fencing_token),
            "step_boundary_uow_fn": boundary_uow,
            "after_step_boundary_commit_fn": lambda **_kwargs: None,
            "execute_with_lease_heartbeat_fn": execute_with_lease_heartbeat,
            "stage_native_output_writes_fn": build_postgres_native_output_stager(),
        }

    provider_connection = provider_db_conn if provider_db_conn is not None else db_conn
    return build_run_context(
        db_conn=provider_connection,
        tenant_id=tenant_id,
        run_id=str(run_data["run_id"]),
        deal_id=deal_id,
        mode=str(run_data["mode"]),
        documents=extraction_ready_documents_from_preflight_corpus(preflight_corpus),
        deal_metadata=_load_worker_deal_metadata(
            db_conn=db_conn,
            tenant_id=tenant_id,
            deal_id=deal_id,
        ),
        preflight_corpus=preflight_corpus,
        created_by_actor_id=run_data.get("created_by_actor_id"),
        created_by_actor_type=run_data.get("created_by_actor_type"),
        strict_preflight_proof_id=run_data.get("strict_preflight_proof_id"),
        pipeline_contract_version=(
            run_data.get("pipeline_contract_version")
            if run_data.get("pipeline_contract_version") == "full-strict-v21"
            else None
        ),
        pipeline_contract_hash=(
            run_data.get("pipeline_contract_hash")
            if run_data.get("pipeline_contract_version") == "full-strict-v21"
            else None
        ),
        pipeline_resume_state=(
            run_data.get("pipeline_resume_state")
            if run_data.get("pipeline_contract_version") == "full-strict-v21"
            else None
        ),
        no_substantive_content_receipt_verifier=(no_substantive_content_receipt_verifier),
        audit_sink=audit_sink,
        strict_live_extraction_required=strict_live_extraction_required,
        strict_live_debate_backend_required=strict_live_extraction_required,
        provider_output_guard_fn=provider_output_guard_fn,
        methodology_coverage_init_fn=methodology_coverage_init_fn,
        methodology_coverage_rehydrate_fn=methodology_coverage_rehydrate_fn,
        methodology_extraction_task_planning_fn=methodology_extraction_callbacks.get(
            "methodology_extraction_task_planning_fn"
        ),
        methodology_extraction_task_execution_fn=methodology_extraction_callbacks.get(
            "methodology_extraction_task_execution_fn"
        ),
        methodology_claim_materialization_fn=methodology_extraction_callbacks.get(
            "methodology_claim_materialization_fn"
        ),
        methodology_evidence_item_materialization_fn=methodology_extraction_callbacks.get(
            "methodology_evidence_item_materialization_fn"
        ),
        methodology_sanad_creation_linking_grading_fn=methodology_extraction_callbacks.get(
            "methodology_sanad_creation_linking_grading_fn"
        ),
        **lease_callbacks,
    )


def _load_worker_deal_metadata(
    *,
    db_conn: Any,
    tenant_id: str,
    deal_id: str,
) -> dict[str, Any] | None:
    """Load deal metadata for worker parity with API-started runs."""
    from idis.persistence.repositories.deals import DealsRepository

    return DealsRepository(db_conn, tenant_id).get(deal_id)


# Global worker instance
_worker: PipelineWorker | None = None


async def start_worker() -> None:
    """Start the global pipeline worker."""
    global _worker

    if _worker is not None:
        logger.warning("Worker already started")
        return

    lease_config = load_worker_lease_config(strict=True)
    gdbs_path = get_gdbs_path()
    candidate = PipelineWorker(
        poll_interval=5,
        gdbs_path=gdbs_path,
        lease_config=lease_config,
    )
    await candidate.start()
    _worker = candidate


async def stop_worker() -> None:
    """Stop the global pipeline worker."""
    global _worker

    if _worker is None:
        return

    await _worker.stop()
    _worker = None
