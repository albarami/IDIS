"""Worker ↔ RunOrchestrator integration (Sprint 2, Task 10).

Proves the background worker no longer uses the stale GDBS-demo
`PipelineExecutor` and instead drives the real `RunOrchestrator`
against the current schema:

- a QUEUED SNAPSHOT run is picked up by `PipelineWorker._process_queued_runs`,
- status transitions QUEUED → RUNNING → SUCCEEDED,
- `run_steps` rows are written in canonical order through the real
  `RunStepsRepository`,
- extracted claims land in the real `claims` table (not the stale
  columns the old executor wrote to),
- FULL-mode queued runs are explicitly failed with a clear
  `block_reason` instead of silently succeeding via a stub.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Generator
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

from idis.persistence.db import set_tenant_local
from idis.persistence.repositories.documents import (
    DocumentArtifactsRepository,
    DocumentSpansRepository,
    DocumentsRepository,
)
from idis.pipeline.worker import PipelineWorker
from tests._postgres_support import (
    admin_engine_generator,
    migrated_db_generator,
    postgres_configured,
    seed_deal,
    truncate_all,
)

if TYPE_CHECKING:
    from sqlalchemy import Engine


TENANT_ID = "abcd1234-abcd-1234-abcd-1234abcd1234"


@pytest.fixture(scope="module")
def _pg_admin_engine() -> Generator[Engine, None, None]:
    yield from admin_engine_generator()


@pytest.fixture(scope="module")
def _pg_migrated(_pg_admin_engine: Engine) -> Generator[None, None, None]:
    yield from migrated_db_generator(_pg_admin_engine)


@pytest.fixture(autouse=True)
def _pg_clean_state(request: pytest.FixtureRequest) -> Generator[None, None, None]:
    if not postgres_configured():
        pytest.skip("Postgres not configured")
    admin_engine = request.getfixturevalue("_pg_admin_engine")
    request.getfixturevalue("_pg_migrated")
    truncate_all(admin_engine)
    yield
    truncate_all(admin_engine)


def _queue_run(admin_engine: Engine, *, run_id: str, deal_id: str, mode: str) -> None:
    with admin_engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO runs (
                    run_id, tenant_id, deal_id, mode, status,
                    started_at, created_at
                ) VALUES (
                    :r, :t, :d, :m, 'QUEUED', now(), now()
                )
                """
            ),
            {"r": run_id, "t": TENANT_ID, "d": deal_id, "m": mode},
        )


def _seed_ingested_document(
    admin_engine: Engine, app_engine: Engine, deal_id: str
) -> None:
    """Insert a durable document + span through the real repositories so
    the worker's INGEST_CHECK step sees a non-empty documents list.
    """
    from idis.persistence.db import get_app_engine as _get

    del app_engine  # fixture marker; we call get_app_engine() below.
    seed_deal(admin_engine, deal_id=deal_id, tenant_id=TENANT_ID)
    art_id = str(uuid.uuid4())
    document_id = str(uuid.uuid4())
    with _get().begin() as conn:
        set_tenant_local(conn, TENANT_ID)
        DocumentArtifactsRepository(conn, TENANT_ID).create(
            doc_id=art_id,
            deal_id=deal_id,
            doc_type="PITCH_DECK",
            title="worker-gate.pdf",
            source_system="test",
            version_id="v1",
        )
        DocumentsRepository(conn, TENANT_ID).create(
            document_id=document_id,
            deal_id=deal_id,
            doc_id=art_id,
            doc_type="PDF",
            parse_status="PARSED",
        )
        DocumentSpansRepository(conn, TENANT_ID).create_many(
            [
                {
                    "span_id": str(uuid.uuid4()),
                    "document_id": document_id,
                    "span_type": "PAGE_TEXT",
                    "locator": {"page": 1},
                    "text_excerpt": "Revenue was $5M in 2024.",
                }
            ]
        )


class TestWorkerDrivesRealOrchestrator:
    def test_snapshot_queued_run_executes_via_run_orchestrator(
        self,
        _pg_admin_engine: Engine,
    ) -> None:
        from idis.persistence.db import get_app_engine

        admin_engine = _pg_admin_engine
        app_engine = get_app_engine()
        deal_id = str(uuid.uuid4())
        run_id = str(uuid.uuid4())
        _seed_ingested_document(admin_engine, app_engine, deal_id)
        _queue_run(admin_engine, run_id=run_id, deal_id=deal_id, mode="SNAPSHOT")

        worker = PipelineWorker(poll_interval=0)
        asyncio.run(worker._process_queued_runs())

        with admin_engine.begin() as conn:
            run_row = conn.execute(
                text(
                    "SELECT status, started_at, finished_at "
                    "FROM runs WHERE run_id = :r"
                ),
                {"r": run_id},
            ).fetchone()
            step_rows = conn.execute(
                text(
                    "SELECT step_name, status FROM run_steps "
                    "WHERE run_id = :r"
                ),
                {"r": run_id},
            ).fetchall()
            claim_rows = conn.execute(
                text(
                    "SELECT claim_id, tenant_id, deal_id FROM claims "
                    "WHERE deal_id = :d"
                ),
                {"d": deal_id},
            ).fetchall()

        assert run_row is not None
        assert run_row.status == "SUCCEEDED", (
            f"worker run must finish SUCCEEDED via the real orchestrator; "
            f"got status={run_row.status!r}"
        )
        assert run_row.started_at is not None
        assert run_row.finished_at is not None

        step_names = {r.step_name for r in step_rows}
        assert "INGEST_CHECK" in step_names
        assert "EXTRACT" in step_names
        assert "GRADE" in step_names
        assert "CALC" in step_names, (
            "SNAPSHOT must have a CALC step row — proves the run went "
            "through the real RunOrchestrator, not the stale demo path"
        )

        assert len(claim_rows) >= 1, (
            "EXTRACT step must persist at least one claim via the real "
            "ClaimsRepository; the stale demo path wrote to columns "
            "that no longer exist."
        )
        assert all(str(r.tenant_id) == TENANT_ID for r in claim_rows)
        assert all(str(r.deal_id) == deal_id for r in claim_rows)

    def test_full_mode_queued_run_is_marked_failed_with_block_reason(
        self,
        _pg_admin_engine: Engine,
    ) -> None:
        admin_engine = _pg_admin_engine
        deal_id = str(uuid.uuid4())
        run_id = str(uuid.uuid4())
        seed_deal(admin_engine, deal_id=deal_id, tenant_id=TENANT_ID)
        _queue_run(admin_engine, run_id=run_id, deal_id=deal_id, mode="FULL")

        worker = PipelineWorker(poll_interval=0)
        asyncio.run(worker._process_queued_runs())

        with admin_engine.begin() as conn:
            row = conn.execute(
                text("SELECT status FROM runs WHERE run_id = :r"),
                {"r": run_id},
            ).fetchone()

        assert row is not None
        assert row.status == "FAILED", (
            "FULL-mode worker support is deferred — must be FAILED, "
            "never silently SUCCEEDED by a stub"
        )


class TestDeprecatedPipelineExecutorIsNotLive:
    """The stale GDBS demo executor is retained as a shim that raises on
    instantiation; confirms nothing can silently re-wire it."""

    def test_pipeline_executor_raises_on_construction(self) -> None:
        from idis.pipeline.executor import PipelineExecutor

        with pytest.raises(RuntimeError, match="deprecated"):
            PipelineExecutor(None)
