"""Background worker for processing pipeline runs through the canonical service."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from idis.models.run_source import RunSource
from idis.models.run_step import RunStep, StepName, StepStatus
from idis.persistence.db import get_app_engine, set_tenant_local
from idis.persistence.repositories.run_steps import get_run_steps_repository
from idis.persistence.repositories.runs import get_runs_repository
from idis.services.runs.execution import RunExecutionService
from idis.services.runs.orchestrator import RunContext
from idis.services.runs.strict_full_live import (
    IDIS_STRICT_DOTENV_PATH_ENV,
    STRICT_FULL_LIVE_BLOCKED,
    build_strict_full_live_admission_report,
    is_strict_full_live_required,
)

logger = logging.getLogger(__name__)

ExecutionServiceFactory = Callable[..., RunExecutionService]
RunContextFactory = Callable[..., RunContext]


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
        poll_interval: int = 5,
        gdbs_path: str | None = None,
        tenant_ids: list[str] | None = None,
        execution_service_factory: ExecutionServiceFactory | None = None,
        run_context_factory: RunContextFactory | None = None,
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
        self._tenant_ids = tenant_ids if tenant_ids is not None else get_worker_tenant_ids()
        self._execution_service_factory = (
            execution_service_factory or _default_execution_service_factory
        )
        self._run_context_factory = run_context_factory or _default_run_context_factory
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the worker."""
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

    async def _process_queued_runs(self) -> int:
        """Process all queued runs."""
        if not self._tenant_ids:
            logger.info("Pipeline worker has no tenant scope configured; skipping queued polling")
            return 0

        engine = get_app_engine()
        processed_count = 0

        with engine.connect() as conn:
            for tenant_id in self._tenant_ids:
                set_tenant_local(conn, tenant_id)
                runs_repo = get_runs_repository(conn, tenant_id)
                runs = runs_repo.claim_queued_runs(limit=10)

                if not runs:
                    continue

                logger.info("Found %s queued runs for tenant %s", len(runs), tenant_id)

                for run_data in runs:
                    run_id = str(run_data["run_id"])
                    processed_count += 1

                    try:
                        if str(run_data.get("mode", "")).upper() == "FULL":
                            strict_dotenv_path = os.environ.get(IDIS_STRICT_DOTENV_PATH_ENV)
                            if is_strict_full_live_required(dotenv_path=strict_dotenv_path):
                                try:
                                    preflight_corpus = _load_worker_preflight_corpus(
                                        db_conn=conn,
                                        tenant_id=tenant_id,
                                        run_data=run_data,
                                    )
                                except (
                                    InvalidRunSourceMetadataError,
                                    InvalidRunSourceSelectionError,
                                ):
                                    self._persist_worker_preflight_block(
                                        conn=conn,
                                        tenant_id=tenant_id,
                                        run_id=run_id,
                                        reason_code="INVALID_RUN_SOURCE",
                                        message=(
                                            "Queued FULL run has invalid or missing run-source "
                                            "document selection"
                                        ),
                                    )
                                    conn.commit()
                                    logger.warning(
                                        (
                                            "Queued FULL run %s failed closed before strict "
                                            "preflight due to invalid source selection metadata"
                                        ),
                                        run_id,
                                    )
                                    continue
                                strict_report = build_strict_full_live_admission_report(
                                    db_conn=conn,
                                    tenant_id=tenant_id,
                                    preflight_corpus=preflight_corpus,
                                    strict_dotenv_path=strict_dotenv_path,
                                )
                                if not strict_report.may_proceed:
                                    self._persist_worker_preflight_block(
                                        conn=conn,
                                        tenant_id=tenant_id,
                                        run_id=run_id,
                                        reason_code=STRICT_FULL_LIVE_BLOCKED,
                                        message=(
                                            "Strict full-live preflight blocked queued FULL run "
                                            "before execution"
                                        ),
                                    )
                                    conn.commit()
                                    logger.info(
                                        "Strict full-live blocked queued FULL run %s", run_id
                                    )
                                    continue

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
                        await asyncio.to_thread(service.execute, ctx)

                        conn.commit()
                        logger.info("Completed run %s", run_id)

                    except Exception as e:
                        conn.rollback()
                        self._mark_run_failed_after_exception(conn, tenant_id, run_id)
                        logger.error(
                            "Failed to execute run %s: %s",
                            run_id,
                            e,
                            extra={"run_id": run_id, "error": str(e)},
                            exc_info=True,
                        )

        return processed_count

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
    ) -> None:
        """Persist a safe preflight blocker using existing status and ledger surfaces.

        Fails the run only while it is still QUEUED/RUNNING. If the run was cancelled
        after the claim batch released its row lock, the guarded completion returns
        False: the existing terminal status is preserved and no preflight ledger step
        is written.
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

        strict_step = RunStep(
            step_id=str(uuid.uuid4()),
            run_id=run_id,
            tenant_id=tenant_id,
            step_name=StepName.DOCUMENT_PREFLIGHT,
            step_order=3,
            status=StepStatus.FAILED,
            started_at=finished_at,
            finished_at=finished_at,
            result_summary={"reason_code": reason_code},
            error_code=reason_code,
            error_message=message,
        )
        run_steps_repo.create(strict_step)


def get_worker_tenant_ids() -> list[str]:
    """Return tenant IDs the worker is allowed to poll.

    Empty configuration is fail-safe: the worker does not globally scan queued
    runs without first setting an RLS tenant context.
    """
    raw = os.getenv("IDIS_WORKER_TENANT_IDS", "")
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


def _commit_claim_and_restore_tenant_context(db_conn: Any, tenant_id: str) -> None:
    """Commit the QUEUED->RUNNING claim, then re-establish RLS tenant context.

    The claim commit releases the run row lock so a concurrent cancel can proceed.
    Tenant context is set via ``SET LOCAL idis.tenant_id`` which is transaction-scoped,
    so committing clears it; orchestration must run with tenant RLS context, so it is
    immediately re-applied before RunOrchestrator executes.
    """
    db_conn.commit()
    set_tenant_local(db_conn, tenant_id)


def _default_execution_service_factory(
    *,
    db_conn: Any,
    tenant_id: str,
) -> RunExecutionService:
    audit_sink = _default_worker_audit_sink()
    return RunExecutionService(
        audit_sink=audit_sink,
        runs_repo=get_runs_repository(db_conn, tenant_id),
        run_steps_repo=get_run_steps_repository(db_conn, tenant_id),
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


def _default_run_context_factory(
    *,
    db_conn: Any,
    tenant_id: str,
    run_data: dict[str, Any],
    audit_sink: Any,
) -> RunContext:
    """Build a worker run context using shared run step wiring."""
    from idis.services.runs.steps import (
        build_run_context,
        extraction_ready_documents_from_preflight_corpus,
    )

    deal_id = str(run_data["deal_id"])
    preflight_corpus = _load_worker_preflight_corpus(
        db_conn=db_conn,
        tenant_id=tenant_id,
        run_data=run_data,
    )

    return build_run_context(
        db_conn=db_conn,
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
        audit_sink=audit_sink,
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

    gdbs_path = get_gdbs_path()
    _worker = PipelineWorker(poll_interval=5, gdbs_path=gdbs_path)
    await _worker.start()


async def stop_worker() -> None:
    """Stop the global pipeline worker."""
    global _worker

    if _worker is None:
        return

    await _worker.stop()
    _worker = None
