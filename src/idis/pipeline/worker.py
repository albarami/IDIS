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
from idis.persistence.db import get_admin_engine, get_app_engine, set_tenant_local

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

        Polling uses the admin engine so cross-tenant discovery is not
        filtered by the per-tenant RLS on `runs`. Per-run execution
        then opens a fresh app-engine transaction, calls
        set_tenant_local (matching the repository path and the RLS
        policies), and drives the real pipeline. SNAPSHOT mode executes
        INGEST_CHECK → EXTRACT → GRADE → CALC through the same step
        callables the API path uses. FULL mode is deferred: those runs
        are marked FAILED rather than silently succeeding via a stale
        stub.
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
                await asyncio.to_thread(_mark_run_failed_in_new_tx, run_id)


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


def _mark_run_failed_in_new_tx(run_id: str) -> None:
    """Best-effort FAILED marker in a fresh transaction after an exception."""
    from datetime import UTC, datetime

    engine = get_app_engine()
    with engine.begin() as conn:
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


async def start_worker() -> None:
    """Start the global pipeline worker."""
    global _worker

    if _worker is not None:
        logger.warning("Worker already started")
        return

    _worker = PipelineWorker(poll_interval=5)
    await _worker.start()


async def stop_worker() -> None:
    """Stop the global pipeline worker."""
    global _worker

    if _worker is None:
        return

    await _worker.stop()
    _worker = None
