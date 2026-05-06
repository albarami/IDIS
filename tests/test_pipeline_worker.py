"""Regression tests for pipeline worker tenant isolation setup."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from idis.pipeline.worker import PipelineWorker

TENANT_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def test_worker_sets_rls_tenant_with_shared_helper() -> None:
    """Queued run processing must use the canonical idis.tenant_id RLS helper."""
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = [
        ("run-1", "deal-1", "SNAPSHOT", TENANT_ID),
    ]

    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn

    executor = MagicMock()
    executor.execute_run = AsyncMock()

    worker = PipelineWorker(poll_interval=0)

    with (
        patch("idis.pipeline.worker.get_app_engine", return_value=engine),
        patch("idis.pipeline.worker.PipelineExecutor", return_value=executor),
        patch("idis.pipeline.worker.set_tenant_local", create=True) as set_tenant_local,
    ):
        asyncio.run(worker._process_queued_runs())

    set_tenant_local.assert_called_once_with(conn, TENANT_ID)
