"""Task 2 RED tests for durable Postgres idempotency conflict semantics."""

from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse

import idis.idempotency.store as store_contract
from idis.api.middleware.idempotency import IdempotencyMiddleware
from idis.idempotency.postgres_store import PostgresIdempotencyStore
from idis.idempotency.store import IdempotencyRecord, IdempotencyStoreError, ScopeKey


@dataclass
class _Result:
    row: object | None = None

    def fetchone(self) -> object | None:
        return self.row


class _StatefulConnection:
    """Small SQLAlchemy-connection double that models insert conflict behavior."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.rows: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}

    @staticmethod
    def _key(params: dict[str, Any]) -> tuple[str, str, str, str, str]:
        return (
            str(params["tenant_id"]),
            str(params["actor_id"]),
            str(params["method"]),
            str(params["operation_id"]),
            str(params["idempotency_key"]),
        )

    def execute(self, statement: object, params: dict[str, Any] | None = None) -> _Result:
        sql = " ".join(str(statement).lower().split())
        bound = dict(params or {})
        self.calls.append((sql, bound))

        if "pg_advisory_xact_lock" in sql:
            return _Result((None,))

        if sql.startswith("insert into idempotency_records"):
            key = self._key(bound)
            existing = self.rows.get(key)
            if existing is None:
                self.rows[key] = bound
                return _Result((1,))
            if "do update" in sql:
                # Models the pre-fix last-writer-wins defect.
                self.rows[key] = bound
            return _Result(None)

        if sql.startswith("select payload_sha256"):
            row = self.rows.get(self._key(bound))
            return _Result(SimpleNamespace(**row) if row is not None else None)

        raise AssertionError(f"Unexpected SQL: {sql}")


def _scope(*, key: str = "idem-secret-value", actor: str = "actor-1") -> ScopeKey:
    return ScopeKey("tenant-1", actor, "POST", "startRun", key)


def _record(payload: str, body: bytes, *, status: int = 202) -> IdempotencyRecord:
    return IdempotencyRecord(
        payload_sha256=payload,
        status_code=status,
        media_type="application/json",
        body_bytes=body,
        created_at="2026-07-15T09:00:00Z",
    )


def test_conflict_has_stable_typed_store_contract() -> None:
    conflict_type = getattr(store_contract, "IdempotencyKeyConflictError", None)
    assert conflict_type is not None
    error = conflict_type()
    assert isinstance(error, IdempotencyStoreError)
    assert error.code == "IDEMPOTENCY_KEY_CONFLICT"


def test_insert_is_first_writer_wins_not_last_writer_wins() -> None:
    sql = " ".join(str(PostgresIdempotencyStore._INSERT_SQL).upper().split())
    assert "ON CONFLICT" in sql
    assert "DO NOTHING" in sql
    assert "DO UPDATE" not in sql
    assert "RETURNING" in sql


def test_get_takes_transaction_lock_before_read_without_exposing_scope() -> None:
    conn = _StatefulConnection()
    raw_key = "idem-do-not-leak-this-value"

    assert PostgresIdempotencyStore().get(_scope(key=raw_key), conn=conn) is None

    assert len(conn.calls) == 2
    lock_sql, lock_params = conn.calls[0]
    select_sql, _ = conn.calls[1]
    assert "pg_advisory_xact_lock" in lock_sql
    assert select_sql.startswith("select payload_sha256")
    assert set(lock_params) == {"lock_key"}
    assert raw_key not in str(lock_params)
    assert len(str(lock_params["lock_key"])) == 64


def test_lock_token_covers_the_full_idempotency_scope() -> None:
    first = _StatefulConnection()
    second = _StatefulConnection()
    store = PostgresIdempotencyStore()

    store.get(_scope(actor="actor-1"), conn=first)
    store.get(_scope(actor="actor-2"), conn=second)

    assert first.calls[0][1]["lock_key"] != second.calls[0][1]["lock_key"]


def test_different_payload_conflict_preserves_original_record() -> None:
    conflict_type = store_contract.IdempotencyKeyConflictError
    conn = _StatefulConnection()
    store = PostgresIdempotencyStore()
    scope = _scope()
    original = _record("sha256:original", b'{"winner":1}')
    conflict = _record("sha256:different", b'{"winner":2}', status=201)

    store.put(scope, original, conn=conn)
    with pytest.raises(conflict_type) as exc_info:
        store.put(scope, conflict, conn=conn)

    assert exc_info.value.code == "IDEMPOTENCY_KEY_CONFLICT"
    assert store.get(scope, conn=conn) == original


def test_same_payload_retry_never_overwrites_original_response() -> None:
    conn = _StatefulConnection()
    store = PostgresIdempotencyStore()
    scope = _scope()
    original = _record("sha256:same", b'{"winner":1}')
    later_response = _record("sha256:same", b'{"winner":2}', status=201)

    store.put(scope, original, conn=conn)
    store.put(scope, later_response, conn=conn)

    assert store.get(scope, conn=conn) == original


class _PutConflictStore:
    def get(self, scope_key: ScopeKey, *, conn: object) -> None:
        return None

    def put(self, scope_key: ScopeKey, record: IdempotencyRecord, *, conn: object) -> None:
        conflict_type = store_contract.IdempotencyKeyConflictError
        raise conflict_type()

    def delete_expired(self, *, tenant_id: str, older_than: object) -> int:
        return 0


async def _noop_app(scope: object, receive: object, send: object) -> None:  # pragma: no cover
    return None


def test_middleware_maps_atomic_put_conflict_to_stable_409() -> None:
    middleware = IdempotencyMiddleware(
        _noop_app,
        postgres_store=_PutConflictStore(),  # type: ignore[arg-type]
    )

    async def call_next(_request: Request) -> Response:
        return StreamingResponse(
            iter([b'{"created":true}']),
            status_code=201,
            media_type="application/json",
        )

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"{}", "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/runs",
            "query_string": b"",
            "headers": [(b"idempotency-key", b"idem-race")],
            "state": {
                "tenant_context": SimpleNamespace(tenant_id="tenant-1", actor_id="actor-1"),
                "openapi_operation_id": "startRun",
                "request_body_sha256": "sha256:payload",
                "db_conn": object(),
                "request_id": "request-1",
            },
        },
        receive,
    )

    response = asyncio.run(middleware.dispatch(request, call_next))

    assert response.status_code == 409
    assert b'"code":"IDEMPOTENCY_KEY_CONFLICT"' in response.body


def _require_disposable_postgres() -> None:
    if os.environ.get("IDIS_DATABASE_ADMIN_URL"):
        return
    message = "Postgres idempotency proof requires IDIS_DATABASE_ADMIN_URL"
    if os.environ.get("IDIS_REQUIRE_POSTGRES") == "1":
        pytest.fail(f"REQUIRED: {message} (IDIS_REQUIRE_POSTGRES=1)")
    pytest.skip(message)


def test_disposable_postgres_is_first_writer_wins_across_store_restart() -> None:
    """Prove real SQL conflict semantics and restart visibility on an isolated database."""
    _require_disposable_postgres()
    from alembic import command
    from alembic.config import Config

    import idis.persistence.migrations as migrations_pkg
    from idis.persistence.db import set_tenant_local
    from tests.slice100_task2.test_durable_governance_migration import (
        _create_disposable_engine,
        _run_alembic,
    )

    engine, cleanup = _create_disposable_engine()
    config = Config()
    config.set_main_option("script_location", str(Path(migrations_pkg.__file__).parent))
    tenant_id = str(uuid.uuid4())
    scope = ScopeKey(tenant_id, "actor-1", "POST", "startRun", "durable-idem-key")
    original = _record("sha256:original", b'{"winner":1}')
    conflicting = _record("sha256:different", b'{"winner":2}', status=201)
    same_payload_later = _record("sha256:original", b'{"winner":3}', status=200)
    try:
        _run_alembic(config, engine, command.upgrade, "0033")
        store = PostgresIdempotencyStore()
        with engine.begin() as connection:
            set_tenant_local(connection, tenant_id)
            store.put(scope, original, conn=connection)

        with engine.begin() as connection:
            set_tenant_local(connection, tenant_id)
            with pytest.raises(store_contract.IdempotencyKeyConflictError):
                store.put(scope, conflicting, conn=connection)

        with engine.begin() as connection:
            set_tenant_local(connection, tenant_id)
            store.put(scope, same_payload_later, conn=connection)

        restarted = PostgresIdempotencyStore()
        with engine.begin() as connection:
            set_tenant_local(connection, tenant_id)
            assert restarted.get(scope, conn=connection) == original
    finally:
        cleanup()
