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
from unittest.mock import MagicMock

import pytest

from idis.pipeline import worker as worker_module
from idis.pipeline.worker import PipelineWorker


class TestWorkerTenantContextSourceGuard:
    """Static guard: revert-to-app.tenant_id must not pass review silently."""

    def test_worker_source_uses_set_tenant_local(self) -> None:
        source = inspect.getsource(PipelineWorker._process_queued_runs)
        assert "set_tenant_local(" in source, (
            "Worker must use set_tenant_local() so its tenant GUC matches "
            "the one the repositories and RLS policies read."
        )

    def test_worker_source_does_not_use_wrong_guc(self) -> None:
        source = inspect.getsource(PipelineWorker._process_queued_runs)
        assert "app.tenant_id" not in source, (
            "Worker must not use 'app.tenant_id' — RLS policies read "
            "'idis.tenant_id'. Use set_tenant_local() from persistence.db."
        )
        assert "set_config(" not in source, (
            "Worker must not use set_config(...) for tenant scoping; "
            "that targets app.tenant_id which RLS does not read. "
            "Use set_tenant_local() from persistence.db."
        )


class _FakeResult:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)


class _FakeConnection:
    """Minimal stand-in for a SQLAlchemy Connection.

    Records every `execute(stmt, params)` call so the test can assert the
    worker never issues app.tenant_id / set_config tenant statements.
    Returns a canned result for the QUEUED-runs SELECT; a no-op result
    for everything else.
    """

    def __init__(self, queued_rows: list[tuple[Any, ...]]) -> None:
        self._queued_rows = queued_rows
        self.executions: list[tuple[str, dict[str, Any] | None]] = []
        self.commit_count = 0
        self.rollback_count = 0
        self._queued_select_consumed = False

    def execute(self, stmt: Any, params: Any | None = None) -> _FakeResult:
        sql_text = str(getattr(stmt, "text", stmt))
        self.executions.append((sql_text, params))
        if "FROM runs" in sql_text and not self._queued_select_consumed:
            self._queued_select_consumed = True
            return _FakeResult(self._queued_rows)
        return _FakeResult([])

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1


class _FakeEngine:
    def __init__(self, conn: _FakeConnection) -> None:
        self._conn = conn

    def connect(self) -> _FakeEngine:
        return self

    def __enter__(self) -> _FakeConnection:
        return self._conn

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


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

        # Capture (conn, tenant_id) passed to set_tenant_local.
        set_tenant_calls: list[tuple[Any, str]] = []

        def _spy_set_tenant_local(conn: Any, tenant_id: str) -> None:
            set_tenant_calls.append((conn, tenant_id))

        monkeypatch.setattr(
            worker_module, "set_tenant_local", _spy_set_tenant_local
        )

        # Stub PipelineExecutor so this test stays focused on tenant context.
        fake_executor = MagicMock()

        async def _fake_execute_run(
            run_id: str, deal_id: str, mode: str, tenant_id: str
        ) -> None:
            return None

        fake_executor.execute_run.side_effect = _fake_execute_run
        monkeypatch.setattr(
            worker_module,
            "PipelineExecutor",
            MagicMock(return_value=fake_executor),
        )

        worker = PipelineWorker(poll_interval=0, gdbs_path=None)
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
        fake_conn, set_tenant_calls = self._run(monkeypatch, queued_rows=[])
        # No queued rows -> no tenant context switch, no commits.
        assert set_tenant_calls == []
        assert fake_conn.commit_count == 0
