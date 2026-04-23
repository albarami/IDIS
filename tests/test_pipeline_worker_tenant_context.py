"""Regression tests for worker tenant/RLS correctness (Sprint 1 Wave 1, Task 4).

The background worker at src/idis/pipeline/worker.py previously set
`app.tenant_id` via `SELECT set_config('app.tenant_id', ...)`. No RLS
policy reads that GUC — every policy (and every repository call via
`set_tenant_local`) uses `idis.tenant_id`. This silently bypassed RLS
isolation on every worker write.

These tests prove the fix and guard against a revert:

* Static guard: the worker source must reference `set_tenant_local` and
  must not contain `app.tenant_id` or `set_config(`. Any revert to the
  old SQL will fail this test immediately.

* Behavioral guard: a spied invocation of `_process_queued_runs` shows
  the worker calls `set_tenant_local(conn, tenant_id)` with the row's
  tenant_id and never executes anything that writes `app.tenant_id` or
  any `set_config(...)` statement against the connection.
"""

from __future__ import annotations

import asyncio
import inspect
import uuid
from typing import Any

import pytest

from idis.pipeline import worker as worker_module
from idis.pipeline.worker import PipelineWorker


class TestWorkerTenantContextSourceGuard:
    """Static guard: revert-to-app.tenant_id must not pass review silently."""

    def test_worker_source_uses_set_tenant_local(self) -> None:
        source = inspect.getsource(PipelineWorker._execute_one)
        assert "set_tenant_local(" in source, (
            "Worker must use set_tenant_local() so its tenant GUC matches "
            "the one the repositories and RLS policies read."
        )

    def test_worker_source_does_not_use_wrong_guc(self) -> None:
        source = inspect.getsource(PipelineWorker._execute_one)
        assert "app.tenant_id" not in source, (
            "Worker must not use 'app.tenant_id' — RLS policies read "
            "'idis.tenant_id'. Use set_tenant_local() from persistence.db."
        )
        assert "set_config(" not in source, (
            "Worker must not use set_config(...) for tenant scoping; "
            "that targets app.tenant_id which RLS does not read. "
            "Use set_tenant_local() from persistence.db."
        )

    def test_worker_does_not_use_stale_pipeline_executor(self) -> None:
        worker_src = inspect.getsource(worker_module)
        assert "PipelineExecutor(" not in worker_src, (
            "Worker must not instantiate the deprecated GDBS-demo "
            "PipelineExecutor. Drive RunOrchestrator directly."
        )
        assert "RunOrchestrator" in inspect.getsource(worker_module._run_snapshot), (
            "Worker's SNAPSHOT helper must route through RunOrchestrator "
            "so background execution uses the same pipeline as the API path."
        )


class _FakeResult:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)


class _FakeConnection:
    """Minimal SQLAlchemy Connection stand-in used as both the polling
    connection and the per-run transaction connection.
    """

    def __init__(self, queued_rows: list[tuple[Any, ...]]) -> None:
        self._queued_rows = queued_rows
        self.executions: list[tuple[str, dict[str, Any] | None]] = []
        self._queued_select_consumed = False

    def execute(self, stmt: Any, params: Any | None = None) -> _FakeResult:
        sql_text = str(getattr(stmt, "text", stmt))
        self.executions.append((sql_text, params))
        if "FROM runs" in sql_text and not self._queued_select_consumed:
            self._queued_select_consumed = True
            return _FakeResult(self._queued_rows)
        return _FakeResult([])


class _CtxConn:
    """Context manager that hands out the shared fake connection."""

    def __init__(self, conn: _FakeConnection) -> None:
        self._conn = conn

    def __enter__(self) -> _FakeConnection:
        return self._conn

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


class _FakeEngine:
    def __init__(self, conn: _FakeConnection) -> None:
        self._conn = conn

    def connect(self) -> _CtxConn:
        return _CtxConn(self._conn)

    def begin(self) -> _CtxConn:
        return _CtxConn(self._conn)


class TestWorkerTenantContextBehavior:
    """Spy/behavioral guard on the runtime call sequence."""

    def _run(
        self,
        monkeypatch: pytest.MonkeyPatch,
        queued_rows: list[tuple[Any, ...]],
    ) -> tuple[_FakeConnection, list[tuple[Any, str]]]:
        fake_conn = _FakeConnection(queued_rows)
        fake_engine = _FakeEngine(fake_conn)
        monkeypatch.setattr(worker_module, "get_app_engine", lambda: fake_engine)
        # Polling now uses the admin engine so cross-tenant discovery
        # bypasses RLS; wire the same fake engine to both.
        monkeypatch.setattr(worker_module, "get_admin_engine", lambda: fake_engine)

        # Capture (conn, tenant_id) passed to set_tenant_local.
        set_tenant_calls: list[tuple[Any, str]] = []

        def _spy_set_tenant_local(conn: Any, tenant_id: str) -> None:
            set_tenant_calls.append((conn, tenant_id))

        monkeypatch.setattr(
            worker_module, "set_tenant_local", _spy_set_tenant_local
        )

        # Stub the SNAPSHOT orchestrator hook so this test stays focused
        # on the tenant-context wiring, not the full pipeline.
        class _Result:
            status = "SUCCEEDED"
            block_reason = None

        monkeypatch.setattr(
            worker_module, "_run_snapshot", lambda **kwargs: _Result()
        )
        monkeypatch.setattr(
            worker_module, "_gather_snapshot_documents", lambda conn, t, d: []
        )

        worker = PipelineWorker(poll_interval=0)
        asyncio.run(worker._process_queued_runs())
        return fake_conn, set_tenant_calls

    def test_worker_calls_set_tenant_local_with_row_tenant_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run_id = uuid.uuid4()
        deal_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        queued = [(run_id, deal_id, "SNAPSHOT", tenant_id)]

        fake_conn, set_tenant_calls = self._run(monkeypatch, queued)

        assert len(set_tenant_calls) == 1, (
            f"expected exactly one set_tenant_local call; got {set_tenant_calls!r}"
        )
        conn_passed, tid_passed = set_tenant_calls[0]
        assert conn_passed is fake_conn
        assert tid_passed == str(tenant_id)

    def test_worker_never_executes_app_tenant_id_or_set_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        tenant_id = uuid.uuid4()
        queued = [(uuid.uuid4(), uuid.uuid4(), "SNAPSHOT", tenant_id)]

        fake_conn, _ = self._run(monkeypatch, queued)

        for sql_text, _params in fake_conn.executions:
            assert "app.tenant_id" not in sql_text, (
                f"worker must not target app.tenant_id GUC; saw: {sql_text!r}"
            )
            assert "set_config(" not in sql_text, (
                f"worker must not use set_config for tenant scoping; "
                f"saw: {sql_text!r}"
            )

    def test_worker_skips_processing_when_no_queued_runs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, set_tenant_calls = self._run(monkeypatch, queued_rows=[])
        # No queued rows -> no tenant context switch.
        assert set_tenant_calls == []

    def test_worker_rejects_full_mode_runs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FULL-mode support via the background worker is deferred.
        Queued FULL runs must be marked FAILED, never silently succeeded
        by a stale demo path.
        """
        queued = [(uuid.uuid4(), uuid.uuid4(), "FULL", uuid.uuid4())]
        fake_conn, _ = self._run(monkeypatch, queued)

        updates = [
            (sql, params)
            for sql, params in fake_conn.executions
            if "UPDATE runs" in sql
        ]
        final = next(
            (p for _, p in updates if p and p.get("s") == "FAILED"),
            None,
        )
        assert final is not None, "FULL mode must be marked FAILED"
