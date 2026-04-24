"""Background worker for processing pipeline runs.

Polls the `runs` table for QUEUED rows and executes each one through the
real `RunOrchestrator` — the same orchestrator the API path uses. The
worker is schema-compatible with the current migrations: it does not
write to stale columns (metric_type / time_period / grade / etc.) and
it does not pull GDBS synthetic data into production tables.

The worker is strictly SNAPSHOT in this task. FULL-mode execution via
the background path is a separate follow-up (the SNAPSHOT step
callables are shared with the API route; the FULL step callables live
alongside them and can be wired up in a later task without touching
this worker surface).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from sqlalchemy import text

from idis.audit.sink import AuditSink, InMemoryAuditSink
from idis.persistence.db import (
    IDIS_DATABASE_ADMIN_URL_ENV,
    IDIS_DATABASE_URL_ENV,
    get_admin_engine,
    get_app_engine,
    set_tenant_local,
)

try:
    from idis.audit.postgres_sink import PostgresAuditSink
except ImportError:  # pragma: no cover - fallback only when optional dep missing
    PostgresAuditSink = None  # type: ignore[misc,assignment]

logger = logging.getLogger(__name__)


class PipelineWorker:
    """Background worker that processes queued runs via RunOrchestrator."""

    def __init__(
        self,
        poll_interval: int = 5,
        audit_sink: AuditSink | None = None,
    ) -> None:
        """Initialize worker.

        Args:
            poll_interval: Seconds between polls for new runs.
            audit_sink: Optional audit sink for orchestrator events.
                Defaults to InMemoryAuditSink when not supplied — the
                Postgres audit path is engaged per-transaction by the
                orchestrator's own helpers when a connection is provided.
        """
        self._poll_interval = poll_interval
        self._audit_sink = audit_sink or InMemoryAuditSink()
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

    async def _process_queued_runs(self) -> None:
        """Process all queued runs via the real RunOrchestrator.

        Polling deliberately uses the admin engine (IDIS_DATABASE_ADMIN_URL).
        Cross-tenant discovery of QUEUED runs bypasses the per-tenant RLS
        policy on `runs` by design — the worker operates above any one
        tenant's scope so it can pick up work from all tenants. Once a
        row is selected the worker switches to the app engine
        (IDIS_DATABASE_URL), opens a fresh transaction, and calls
        set_tenant_local(conn, tenant_id) before doing anything tenant-
        scoped; the real RLS policies apply from that point on.

        Deployment implication: any environment that starts this worker
        MUST provide both IDIS_DATABASE_URL (app role) and
        IDIS_DATABASE_ADMIN_URL (trusted admin role). Startup refuses
        to start the worker if the admin URL is missing (see
        `_require_worker_runtime_contract` / `start_worker`), so this
        method can assume both engines are reachable.

        SNAPSHOT mode executes INGEST_CHECK → EXTRACT → GRADE → CALC
        through the same step callables the API path uses. FULL mode is
        deferred: those runs are marked FAILED rather than silently
        succeeding via a stale stub.
        """
        admin = get_admin_engine()

        with admin.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT run_id, deal_id, mode, tenant_id
                    FROM runs
                    WHERE status = 'QUEUED'
                    ORDER BY created_at ASC
                    LIMIT 10
                    """
                )
            ).fetchall()

        if not rows:
            return

        logger.info(f"Found {len(rows)} queued runs to process")

        for row in rows:
            run_id, deal_id, mode, tenant_id = row
            await self._execute_one(
                run_id=str(run_id),
                deal_id=str(deal_id),
                mode=str(mode),
                tenant_id=str(tenant_id),
            )

    async def _execute_one(
        self,
        *,
        run_id: str,
        deal_id: str,
        mode: str,
        tenant_id: str,
    ) -> None:
        """Execute a single queued run under a fresh transaction."""
        engine = get_app_engine()

        def _run_sync() -> None:
            # Set tenant context using the same helper the Postgres
            # repositories use. RLS policies read this GUC; a prior
            # implementation wrote a different GUC and silently bypassed
            # tenant isolation.
            with engine.begin() as conn:
                set_tenant_local(conn, tenant_id)

                if mode != "SNAPSHOT":
                    _mark_run_failed(
                        conn,
                        run_id=run_id,
                        block_reason="MODE_NOT_SUPPORTED_BY_WORKER",
                    )
                    _emit_run_completed(
                        conn=conn,
                        run_id=run_id,
                        tenant_id=tenant_id,
                        deal_id=deal_id,
                        status="FAILED",
                        audit_sink=self._audit_sink,
                    )
                    return

                _mark_run_running(conn, run_id=run_id)

                documents = _gather_snapshot_documents(conn, tenant_id, deal_id)

                result = _run_snapshot(
                    conn=conn,
                    tenant_id=tenant_id,
                    deal_id=deal_id,
                    run_id=run_id,
                    documents=documents,
                    audit_sink=self._audit_sink,
                )

                _mark_run_final(
                    conn,
                    run_id=run_id,
                    status=result.status,
                    block_reason=result.block_reason,
                )

                # Fail-closed worker-side completion audit: emitted in the
                # SAME transaction as the final status update. Any
                # AuditSinkError propagates out of this block so the
                # surrounding engine.begin() rolls the status update back
                # and the outer except re-marks the run FAILED in a fresh
                # transaction. A missing/failed audit event must never
                # leave a run silently SUCCEEDED.
                _emit_run_completed(
                    conn=conn,
                    run_id=run_id,
                    tenant_id=tenant_id,
                    deal_id=deal_id,
                    status=result.status,
                    audit_sink=self._audit_sink,
                )

        try:
            await asyncio.to_thread(_run_sync)
            logger.info(f"Completed run {run_id}")
        except Exception as e:
            logger.error(
                f"Failed to execute run {run_id}: {e}",
                extra={"run_id": run_id, "error": str(e)},
                exc_info=True,
            )
            with contextlib.suppress(Exception):
                await asyncio.to_thread(
                    _mark_run_failed_in_new_tx, run_id, tenant_id
                )


def _mark_run_running(conn: Any, *, run_id: str) -> None:
    from datetime import UTC, datetime

    conn.execute(
        text(
            """
            UPDATE runs
            SET status = 'RUNNING', started_at = :ts
            WHERE run_id = :r
            """
        ),
        {"r": run_id, "ts": datetime.now(UTC)},
    )


def _mark_run_final(
    conn: Any,
    *,
    run_id: str,
    status: str,
    block_reason: str | None = None,
) -> None:
    """Mark a run's final state. `block_reason` is logged but not
    persisted — the current `runs` schema does not have that column.
    """
    from datetime import UTC, datetime

    if block_reason is not None:
        logger.info(
            "Worker marking run %s FAILED: %s",
            run_id,
            block_reason,
            extra={"run_id": run_id, "block_reason": block_reason},
        )
    conn.execute(
        text(
            """
            UPDATE runs
            SET status = :s, finished_at = :ts
            WHERE run_id = :r
            """
        ),
        {"r": run_id, "s": status, "ts": datetime.now(UTC)},
    )


def _mark_run_failed(conn: Any, *, run_id: str, block_reason: str) -> None:
    _mark_run_final(conn, run_id=run_id, status="FAILED", block_reason=block_reason)


def _mark_run_failed_in_new_tx(run_id: str, tenant_id: str) -> None:
    """Durably mark a run FAILED in a fresh transaction after an exception.

    Must call set_tenant_local(conn, tenant_id) before the UPDATE —
    `runs` has RLS enabled with a policy keyed on `idis.tenant_id`, so
    an app-role transaction without the tenant GUC set updates 0 rows
    and the run would remain QUEUED forever.
    """
    from datetime import UTC, datetime

    engine = get_app_engine()
    with engine.begin() as conn:
        set_tenant_local(conn, tenant_id)
        conn.execute(
            text(
                """
                UPDATE runs
                SET status = 'FAILED', finished_at = :ts
                WHERE run_id = :r
                """
            ),
            {"r": run_id, "ts": datetime.now(UTC)},
        )


def _emit_run_completed(
    *,
    conn: Any,
    run_id: str,
    tenant_id: str,
    deal_id: str,
    status: str,
    audit_sink: AuditSink,
) -> None:
    """Emit the worker-side `deal.run.completed` audit event.

    Under Postgres the event is written via `PostgresAuditSink.emit_in_tx`
    using the same connection the run's final status was written on —
    so the audit row and the status row commit atomically. Without
    Postgres (tests/dev) the in-memory `audit_sink` is used instead.

    Fail-closed: any `AuditSinkError` propagates. Callers rely on that
    propagation to roll back the status update and force-mark the run
    FAILED in a fresh transaction. Swallowing the exception here would
    leave a SUCCEEDED run with no durable audit trail — exactly the
    behavior the route-side test that shipped with this file's original
    design was meant to prevent.
    """
    import uuid as _uuid
    from datetime import UTC, datetime

    event = {
        "event_id": str(_uuid.uuid4()),
        "occurred_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "tenant_id": tenant_id,
        "event_type": "deal.run.completed",
        "severity": "LOW",
        "actor": {
            "actor_type": "SERVICE",
            "actor_id": "pipeline-worker",
            "roles": ["INTEGRATION_SERVICE"],
        },
        "request": {
            "request_id": f"worker-{run_id}",
            "method": "WORKER",
            "path": "/pipeline/worker/runs",
            "status_code": 200 if status == "SUCCEEDED" else 500,
        },
        "resource": {
            "resource_type": "deal",
            "resource_id": run_id,
        },
        "summary": f"deal.run.completed status={status} deal={deal_id}",
        "payload": {
            "hashes": [],
            "refs": [
                {"resource_type": "deal", "resource_id": deal_id},
            ],
        },
    }

    if PostgresAuditSink is not None and conn is not None:
        PostgresAuditSink().emit_in_tx(conn, event)
        return
    audit_sink.emit(event)


def _gather_snapshot_documents(
    conn: Any, tenant_id: str, deal_id: str
) -> list[dict[str, Any]]:
    """Collect ingested documents + spans via the durable repositories."""
    from idis.persistence.repositories.documents import (
        DocumentSpansRepository,
        DocumentsRepository,
    )

    docs, _ = DocumentsRepository(conn, tenant_id).list_by_deal(deal_id, limit=200)
    spans_repo = DocumentSpansRepository(conn, tenant_id)
    gathered: list[dict[str, Any]] = []
    for d in docs:
        span_rows = spans_repo.list_by_document(d["document_id"])
        if not span_rows:
            continue
        gathered.append(
            {
                "document_id": d["document_id"],
                "doc_type": d["doc_type"],
                "document_name": d["document_id"],
                "spans": [
                    {
                        "span_id": s["span_id"],
                        "text_excerpt": s.get("text_excerpt"),
                        "locator": s.get("locator") or {},
                        "span_type": s["span_type"],
                    }
                    for s in span_rows
                ],
            }
        )
    return gathered


def _run_snapshot(
    *,
    conn: Any,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    documents: list[dict[str, Any]],
    audit_sink: AuditSink,
) -> Any:
    """Drive the real RunOrchestrator for a SNAPSHOT mode run.

    Reuses the exact same step callables the API route uses so the
    background path and the foreground path converge on one
    implementation.
    """
    from functools import partial

    from idis.api.routes.runs import (
        _run_snapshot_auto_grade,
        _run_snapshot_calc,
        _run_snapshot_extraction,
    )
    from idis.persistence.repositories.run_steps import get_run_steps_repository
    from idis.services.runs.orchestrator import RunContext, RunOrchestrator

    run_steps_repo = get_run_steps_repository(conn, tenant_id)
    orchestrator = RunOrchestrator(
        audit_sink=audit_sink,
        run_steps_repo=run_steps_repo,
    )
    ctx = RunContext(
        run_id=run_id,
        tenant_id=tenant_id,
        deal_id=deal_id,
        mode="SNAPSHOT",
        documents=documents,
        extract_fn=partial(_run_snapshot_extraction, db_conn=conn),
        grade_fn=partial(_run_snapshot_auto_grade, db_conn=conn),
        calc_fn=_run_snapshot_calc,
    )
    return orchestrator.execute(ctx)


# Global worker instance
_worker: PipelineWorker | None = None


class WorkerRuntimeContractError(RuntimeError):
    """Raised when the worker is asked to start without the required
    runtime contract (both IDIS_DATABASE_URL and IDIS_DATABASE_ADMIN_URL).
    """


def _require_worker_runtime_contract() -> None:
    """Fail loudly if the worker's runtime contract is not satisfied.

    The worker needs the admin URL for cross-tenant polling (see
    `_process_queued_runs`) and the app URL for tenant-scoped execution.
    Silently starting the loop with only one of them wired produces a
    broken background path; we raise instead.
    """
    import os

    missing: list[str] = []
    if not os.environ.get(IDIS_DATABASE_URL_ENV):
        missing.append(IDIS_DATABASE_URL_ENV)
    if not os.environ.get(IDIS_DATABASE_ADMIN_URL_ENV):
        missing.append(IDIS_DATABASE_ADMIN_URL_ENV)
    if missing:
        raise WorkerRuntimeContractError(
            "Pipeline worker cannot start: missing required environment "
            f"variables {missing}. Both IDIS_DATABASE_URL (app role, per-run "
            "tenant-scoped transactions) and IDIS_DATABASE_ADMIN_URL "
            "(trusted role, cross-tenant polling) are required; starting "
            "with only one wired produces a broken background loop."
        )


async def start_worker() -> None:
    """Start the global pipeline worker.

    Validates the runtime contract (both DB URLs present) before
    starting the poll loop, so a partially-configured environment
    surfaces the problem at startup rather than silently running a
    broken worker.
    """
    global _worker

    if _worker is not None:
        logger.warning("Worker already started")
        return

    _require_worker_runtime_contract()
    _worker = PipelineWorker(poll_interval=5)
    await _worker.start()


async def stop_worker() -> None:
    """Stop the global pipeline worker."""
    global _worker

    if _worker is None:
        return

    await _worker.stop()
    _worker = None
