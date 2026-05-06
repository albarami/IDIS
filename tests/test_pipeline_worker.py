"""Regression tests for pipeline worker canonical execution setup."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

from idis.audit.sink import InMemoryAuditSink
from idis.pipeline.worker import PipelineWorker, _default_run_context_factory

TENANT_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


class FakeRunsRepository:
    """Tenant-scoped queued-run repository for worker tests."""

    def __init__(self) -> None:
        self.claim_queued_runs_calls: list[int] = []
        self.completed: list[tuple[str, str, str | None]] = []

    def claim_queued_runs(self, *, limit: int) -> list[dict[str, str]]:
        """Return one safely claimed queued run."""
        self.claim_queued_runs_calls.append(limit)
        return [
            {
                "run_id": "run-1",
                "deal_id": "deal-1",
                "mode": "SNAPSHOT",
                "tenant_id": TENANT_ID,
            }
        ]

    def complete(
        self,
        run_id: str,
        *,
        status: str,
        finished_at: str | None,
    ) -> None:
        """Record failure recovery status updates."""
        self.completed.append((run_id, status, finished_at))


def test_worker_uses_run_execution_service_not_pipeline_executor() -> None:
    """Production worker runs must go through RunExecutionService only."""
    conn = MagicMock()

    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn

    runs_repo = FakeRunsRepository()
    execution_service = MagicMock()
    run_context = MagicMock()
    run_context.run_id = "run-1"
    run_context.tenant_id = TENANT_ID

    worker = PipelineWorker(
        poll_interval=0,
        tenant_ids=[TENANT_ID],
        execution_service_factory=lambda **kwargs: execution_service,
        run_context_factory=lambda **kwargs: run_context,
    )

    with (
        patch("idis.pipeline.worker.get_app_engine", return_value=engine),
        patch("idis.pipeline.worker.get_runs_repository", return_value=runs_repo),
        patch(
            "idis.pipeline.worker.PipelineExecutor",
            side_effect=AssertionError("worker must not instantiate PipelineExecutor"),
            create=True,
        ),
        patch("idis.pipeline.worker.set_tenant_local", create=True) as set_tenant_local,
    ):
        asyncio.run(worker._process_queued_runs())

    set_tenant_local.assert_called_once_with(conn, TENANT_ID)
    assert runs_repo.claim_queued_runs_calls == [10]
    execution_service.execute.assert_called_once_with(run_context)


def test_worker_without_tenant_scope_does_not_scan_global_queued_runs() -> None:
    """Worker must fail safe instead of globally polling queued rows without RLS scope."""
    conn = MagicMock()
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn

    worker = PipelineWorker(poll_interval=0, tenant_ids=[])

    with (
        patch("idis.pipeline.worker.get_app_engine", return_value=engine),
        patch("idis.pipeline.worker.set_tenant_local", create=True) as set_tenant_local,
    ):
        asyncio.run(worker._process_queued_runs())

    set_tenant_local.assert_not_called()
    conn.execute.assert_not_called()


def test_worker_persists_failed_status_after_execution_exception() -> None:
    """Rollback after service errors must not leave claimed runs queued forever."""
    conn = MagicMock()
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn

    runs_repo = FakeRunsRepository()
    execution_service = MagicMock()
    execution_service.audit_sink = InMemoryAuditSink()
    execution_service.execute.side_effect = RuntimeError("orchestration failed")
    run_context = MagicMock()

    worker = PipelineWorker(
        poll_interval=0,
        tenant_ids=[TENANT_ID],
        execution_service_factory=lambda **kwargs: execution_service,
        run_context_factory=lambda **kwargs: run_context,
    )

    with (
        patch("idis.pipeline.worker.get_app_engine", return_value=engine),
        patch("idis.pipeline.worker.get_runs_repository", return_value=runs_repo),
        patch("idis.pipeline.worker.set_tenant_local", create=True),
    ):
        processed = asyncio.run(worker._process_queued_runs())

    assert processed == 1
    conn.rollback.assert_called()
    conn.commit.assert_called_once()
    assert len(runs_repo.completed) == 1
    assert runs_repo.completed[0][0] == "run-1"
    assert runs_repo.completed[0][1] == "FAILED"
    assert runs_repo.completed[0][2] is not None


def test_default_worker_context_factory_hydrates_persisted_documents() -> None:
    """Default worker context must not claim real runs with an empty document list."""
    conn = MagicMock()

    def execute(statement: object, params: dict[str, str]) -> MagicMock:
        sql = str(statement)
        result = MagicMock()
        if "FROM documents" in sql:
            result.fetchall.return_value = [
                MagicMock(
                    document_id="doc-1",
                    doc_type="PDF",
                    metadata={"name": "source.pdf"},
                )
            ]
            return result
        if "FROM document_spans" in sql:
            result.fetchall.return_value = [
                MagicMock(
                    span_id="span-1",
                    text_excerpt="Revenue was $5M.",
                    locator={"page": 1},
                    span_type="PAGE_TEXT",
                )
            ]
            return result
        raise AssertionError(f"Unexpected SQL: {sql}")

    conn.execute.side_effect = execute

    ctx = _default_run_context_factory(
        db_conn=conn,
        tenant_id=TENANT_ID,
        run_data={"run_id": "run-1", "deal_id": "deal-1", "mode": "SNAPSHOT"},
        audit_sink=InMemoryAuditSink(),
    )

    assert ctx.documents == [
        {
            "document_id": "doc-1",
            "doc_type": "PDF",
            "document_name": "source.pdf",
            "spans": [
                {
                    "span_id": "span-1",
                    "text_excerpt": "Revenue was $5M.",
                    "locator": {"page": 1},
                    "span_type": "PAGE_TEXT",
                }
            ],
        }
    ]
