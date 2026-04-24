"""Worker-side `deal.run.completed` audit regression (Sprint 2, Task 11 completion).

Proves three properties the cutover left open:

1. When the worker advances a queued SNAPSHOT run to a terminal state,
   it emits a `deal.run.completed` audit event with the correct
   payload (run_id / tenant_id / deal_id / terminal status).

2. The completion emit is fail-closed: if the audit sink raises
   `AuditSinkError`, the worker does NOT leave the run in SUCCEEDED.
   The final status update is rolled back by the surrounding
   transaction and the fresh-tx recovery marks the run FAILED.

3. A future removal of worker-side completion emission would fail the
   tests — a short static-source guard is included as an extra
   regression check, alongside the behavioral proofs.
"""

from __future__ import annotations

import asyncio
import inspect
import uuid
from collections.abc import Generator
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

from idis.audit.sink import AuditSinkError
from idis.persistence.db import set_tenant_local
from idis.persistence.repositories.documents import (
    DocumentArtifactsRepository,
    DocumentSpansRepository,
    DocumentsRepository,
)
from idis.pipeline import worker as worker_module
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


TENANT_ID = "11112222-3333-4444-5555-666677778888"


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


def _queue_run(admin_engine: Engine, *, run_id: str, deal_id: str) -> None:
    with admin_engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO runs (
                    run_id, tenant_id, deal_id, mode, status,
                    started_at, created_at
                ) VALUES (
                    :r, :t, :d, 'SNAPSHOT', 'QUEUED', now(), now()
                )
                """
            ),
            {"r": run_id, "t": TENANT_ID, "d": deal_id},
        )


def _seed_one_document(admin_engine: Engine, deal_id: str) -> None:
    from idis.persistence.db import get_app_engine

    seed_deal(admin_engine, deal_id=deal_id, tenant_id=TENANT_ID)
    with get_app_engine().begin() as conn:
        set_tenant_local(conn, TENANT_ID)
        art_id = str(uuid.uuid4())
        document_id = str(uuid.uuid4())
        DocumentArtifactsRepository(conn, TENANT_ID).create(
            doc_id=art_id,
            deal_id=deal_id,
            doc_type="PITCH_DECK",
            title="completion.pdf",
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


class TestWorkerCompletionAuditEmitted:
    def test_deal_run_completed_row_present_with_terminal_status(
        self, _pg_admin_engine: Engine
    ) -> None:
        admin_engine = _pg_admin_engine
        deal_id = str(uuid.uuid4())
        run_id = str(uuid.uuid4())
        _seed_one_document(admin_engine, deal_id)
        _queue_run(admin_engine, run_id=run_id, deal_id=deal_id)

        asyncio.run(PipelineWorker(poll_interval=0)._process_queued_runs())

        with admin_engine.begin() as conn:
            audit_row = conn.execute(
                text(
                    """
                    SELECT event_type, request_id, event
                    FROM audit_events
                    WHERE event_type = 'deal.run.completed'
                      AND event->'resource'->>'resource_id' = :rid
                    ORDER BY occurred_at DESC
                    LIMIT 1
                    """
                ),
                {"rid": run_id},
            ).fetchone()
            run_row = conn.execute(
                text("SELECT status FROM runs WHERE run_id = :r"),
                {"r": run_id},
            ).fetchone()

        assert run_row is not None
        assert run_row.status == "SUCCEEDED", (
            "prereq: run must have executed to a terminal state before "
            "the audit check is meaningful"
        )
        assert audit_row is not None, (
            "worker must emit deal.run.completed; got no row in audit_events"
        )
        event = audit_row.event
        assert event["event_type"] == "deal.run.completed"
        assert event["tenant_id"] == TENANT_ID
        assert event["resource"]["resource_type"] == "deal"
        assert event["resource"]["resource_id"] == run_id
        # Payload refs the originating deal so downstream consumers can
        # correlate the run to the deal without re-querying.
        deal_refs = [
            r for r in event["payload"]["refs"]
            if r.get("resource_type") == "deal"
            and r.get("resource_id") == deal_id
        ]
        assert deal_refs, f"expected deal ref in payload; got {event['payload']!r}"
        assert "SUCCEEDED" in event["summary"], (
            f"summary must reflect terminal status; got {event['summary']!r}"
        )


class TestWorkerCompletionAuditFailClosed:
    """If audit emission fails, the worker must NOT leave the run
    SUCCEEDED. The status update is rolled back with the transaction;
    the outer exception handler force-marks the run FAILED via a fresh
    transaction with `set_tenant_local` (Task 10 completion path).
    """

    def test_audit_failure_forces_run_to_failed(
        self,
        _pg_admin_engine: Engine,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        admin_engine = _pg_admin_engine
        deal_id = str(uuid.uuid4())
        run_id = str(uuid.uuid4())
        _seed_one_document(admin_engine, deal_id)
        _queue_run(admin_engine, run_id=run_id, deal_id=deal_id)

        # Inject an AuditSinkError at the worker's completion emit point.
        # This simulates e.g. a dead audit DB / disk-full sink.
        original = worker_module._emit_run_completed

        def _boom(**kwargs: object) -> None:
            raise AuditSinkError("sink down during completion emit")

        monkeypatch.setattr(worker_module, "_emit_run_completed", _boom)

        asyncio.run(PipelineWorker(poll_interval=0)._process_queued_runs())
        # Restore to keep the rest of the suite healthy.
        monkeypatch.setattr(worker_module, "_emit_run_completed", original)

        with admin_engine.begin() as conn:
            run_row = conn.execute(
                text(
                    "SELECT status, finished_at FROM runs WHERE run_id = :r"
                ),
                {"r": run_id},
            ).fetchone()
            completion_audit = conn.execute(
                text(
                    """
                    SELECT event_id FROM audit_events
                    WHERE event_type = 'deal.run.completed'
                      AND event->'resource'->>'resource_id' = :rid
                    """
                ),
                {"rid": run_id},
            ).fetchall()

        assert run_row is not None
        assert run_row.status == "FAILED", (
            f"audit failure must not leave run SUCCEEDED; got {run_row.status!r}"
        )
        assert run_row.finished_at is not None, (
            "FAILED recovery must also set finished_at"
        )
        assert completion_audit == [], (
            "the completion audit row must not be durably present when "
            "emission failed"
        )


class TestWorkerCompletionAuditSourceGuard:
    """Static regression guard: removing the completion emit from the
    worker's synchronous execution path must fail this test immediately.
    """

    def test_worker_emits_deal_run_completed_on_the_main_path(self) -> None:
        execute_src = inspect.getsource(PipelineWorker._execute_one)
        assert "_emit_run_completed(" in execute_src, (
            "Worker._execute_one must emit deal.run.completed via "
            "_emit_run_completed(...) on its main execution path. Any "
            "refactor that drops that call silently would regress the "
            "Task 11 completion-audit contract."
        )

    def test_route_is_not_reintroducing_completion_emit(self) -> None:
        from idis.api.routes import runs as runs_route

        route_src = inspect.getsource(runs_route)
        assert "_emit_run_completed_audit" not in route_src, (
            "Route-side deal.run.completed helper was removed in Task 11. "
            "A reintroduction would desynchronize the worker's own emit."
        )
