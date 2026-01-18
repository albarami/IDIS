"""Background worker for processing pipeline runs.

Polls for QUEUED runs and executes them using PipelineExecutor.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from pathlib import Path

from idis.persistence.db import get_app_engine
from idis.pipeline.executor import PipelineExecutor

logger = logging.getLogger(__name__)


class PipelineWorker:
    """Background worker that processes queued runs."""

    def __init__(self, poll_interval: int = 5, gdbs_path: str | None = None) -> None:
        """Initialize worker.

        Args:
            poll_interval: Seconds between polls for new runs.
            gdbs_path: Path to GDBS dataset for loading synthetic claims.
        """
        self._poll_interval = poll_interval
        self._gdbs_path = gdbs_path
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
        """Process all queued runs."""
        from sqlalchemy import text

        engine = get_app_engine()

        with engine.connect() as conn:
            # Find queued runs
            result = conn.execute(
                text(
                    """
                    SELECT run_id, deal_id, mode, tenant_id
                    FROM runs
                    WHERE status = 'QUEUED'
                    ORDER BY created_at ASC
                    LIMIT 10
                    """
                )
            )

            runs = result.fetchall()

            if not runs:
                return

            logger.info(f"Found {len(runs)} queued runs to process")

            for row in runs:
                run_id, deal_id, mode, tenant_id = row

                try:
                    # Set tenant context
                    conn.execute(
                        text("SELECT set_config('app.tenant_id', :tenant_id, false)"),
                        {"tenant_id": tenant_id},
                    )

                    # Execute run
                    executor = PipelineExecutor(conn, gdbs_path=self._gdbs_path)
                    await executor.execute_run(run_id, deal_id, mode, tenant_id)

                    conn.commit()
                    logger.info(f"Completed run {run_id}")

                except Exception as e:
                    conn.rollback()
                    logger.error(
                        f"Failed to execute run {run_id}: {e}",
                        extra={"run_id": run_id, "error": str(e)},
                        exc_info=True,
                    )


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
