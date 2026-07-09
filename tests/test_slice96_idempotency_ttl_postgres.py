"""Slice96 Task 5 — idempotency TTL cleanup (DEC-E): real Postgres path (env-gated, RLS).

Env-gated: skips without IDIS_DATABASE_URL / IDIS_DATABASE_ADMIN_URL; with IDIS_REQUIRE_POSTGRES=1
(CI) it fails instead of skipping. Proves the Postgres cleanup on the real RLS-enforced
``idempotency_records`` table: expired records are removed, unexpired remain, and cleanup for one
tenant never touches another tenant's records. PYTHONPATH pinned to src.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text

from idis.idempotency.postgres_store import PostgresIdempotencyStore
from idis.idempotency.store import IdempotencyRecord, ScopeKey

ADMIN_URL_ENV = "IDIS_DATABASE_ADMIN_URL"
APP_URL_ENV = "IDIS_DATABASE_URL"
REQUIRE_POSTGRES_ENV = "IDIS_REQUIRE_POSTGRES"

_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)


def _skip_or_fail_if_no_postgres() -> None:
    admin_url = os.environ.get(ADMIN_URL_ENV)
    app_url = os.environ.get(APP_URL_ENV)
    require = os.environ.get(REQUIRE_POSTGRES_ENV, "0") == "1"
    if not admin_url or not app_url:
        msg = f"Postgres idempotency-TTL integration requires {ADMIN_URL_ENV} and {APP_URL_ENV}"
        if require:
            pytest.fail(f"REQUIRED: {msg} (IDIS_REQUIRE_POSTGRES=1)")
        pytest.skip(msg)


@pytest.fixture(scope="module")
def _pg_schema() -> Generator[None, None, None]:
    _skip_or_fail_if_no_postgres()

    from alembic import command
    from alembic.config import Config

    import idis.persistence.migrations as migrations_pkg
    from idis.persistence.db import get_admin_engine, reset_engines

    config = Config()
    config.set_main_option("script_location", os.path.dirname(migrations_pkg.__file__))
    with get_admin_engine().begin() as conn:
        config.attributes["connection"] = conn
        command.upgrade(config, "head")
    yield
    reset_engines()


@pytest.fixture
def clean_idempotency(_pg_schema: None) -> Generator[None, None, None]:
    from idis.persistence.db import get_admin_engine

    def _truncate() -> None:
        with get_admin_engine().begin() as conn:
            conn.execute(text("TRUNCATE idempotency_records"))

    _truncate()
    yield
    _truncate()


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _scope(tenant: str, key: str) -> ScopeKey:
    return ScopeKey(tenant, "actor-1", "POST", "startRun", key)


def _record(created_at: datetime) -> IdempotencyRecord:
    return IdempotencyRecord(
        payload_sha256="sha256:abc",
        status_code=202,
        media_type="application/json",
        body_bytes=b"{}",
        created_at=_iso(created_at),
    )


def test_postgres_cleanup_removes_expired_keeps_unexpired(clean_idempotency: None) -> None:
    store = PostgresIdempotencyStore()
    tenant = str(uuid.uuid4())
    cutoff = _NOW - timedelta(days=30)
    store.put(_scope(tenant, "old"), _record(_NOW - timedelta(days=60)))  # expired
    store.put(_scope(tenant, "new"), _record(_NOW))  # fresh
    removed = store.delete_expired(tenant_id=tenant, older_than=cutoff)
    assert removed == 1
    assert store.get(_scope(tenant, "old")) is None
    assert store.get(_scope(tenant, "new")) is not None


def test_postgres_cleanup_is_tenant_safe(clean_idempotency: None) -> None:
    store = PostgresIdempotencyStore()
    tenant_a = str(uuid.uuid4())
    tenant_b = str(uuid.uuid4())
    cutoff = _NOW - timedelta(days=30)
    store.put(_scope(tenant_a, "old"), _record(_NOW - timedelta(days=60)))  # A expired
    store.put(_scope(tenant_b, "old"), _record(_NOW - timedelta(days=60)))  # B expired
    store.put(_scope(tenant_b, "new"), _record(_NOW))  # B fresh
    removed = store.delete_expired(tenant_id=tenant_a, older_than=cutoff)
    assert removed == 1  # only tenant A's expired row (RLS-scoped)
    assert store.get(_scope(tenant_a, "old")) is None
    assert store.get(_scope(tenant_b, "old")) is not None  # B's expired UNTOUCHED (no cross-tenant)
    assert store.get(_scope(tenant_b, "new")) is not None


async def _noop_app(scope: object, receive: object, send: object) -> None:  # pragma: no cover
    return None


def test_middleware_cleans_expired_postgres_during_dispatch(clean_idempotency: None) -> None:
    # Real idempotency flow: drive the middleware's dispatch() with a real tenant-scoped Postgres
    # connection so opportunistic cleanup runs via the middleware (not a direct delete_expired call)
    # and actually removes expired rows from the real RLS-enforced table.
    import asyncio
    from types import SimpleNamespace

    from starlette.requests import Request
    from starlette.responses import Response

    from idis.api.middleware.idempotency import IdempotencyMiddleware
    from idis.persistence.db import begin_app_conn, set_tenant_local

    pg_store = PostgresIdempotencyStore()
    tenant_a = str(uuid.uuid4())
    tenant_b = str(uuid.uuid4())
    now = datetime.now(UTC)  # middleware cutoff is real-now - TTL, so seed relative to now
    pg_store.put(_scope(tenant_a, "a-old"), _record(now - timedelta(days=60)))  # expired
    pg_store.put(_scope(tenant_a, "a-new"), _record(now))  # fresh
    pg_store.put(_scope(tenant_b, "b-old"), _record(now - timedelta(days=60)))  # other tenant

    mw = IdempotencyMiddleware(_noop_app, postgres_store=pg_store, cleanup_interval_seconds=0.0)

    async def _call_next(_req: Request) -> Response:
        return Response(content=b'{"ok":true}', status_code=200, media_type="application/json")

    async def _receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"{}", "more_body": False}

    with begin_app_conn() as conn:
        set_tenant_local(conn, tenant_a)
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/v1/deals",
            "query_string": b"",
            "headers": [(b"idempotency-key", b"flow-key-1")],
            "state": {
                "tenant_context": SimpleNamespace(tenant_id=tenant_a, actor_id="actor-1"),
                "openapi_operation_id": "createDeal",
                "request_body_sha256": "sha256:abc",
                "db_conn": conn,
                "request_id": "req-1",
            },
        }
        response = asyncio.run(mw.dispatch(Request(scope, _receive), _call_next))
    assert response.status_code == 200  # real dispatch through the middleware succeeded

    assert pg_store.get(_scope(tenant_a, "a-old")) is None  # expired removed during dispatch
    assert pg_store.get(_scope(tenant_a, "a-new")) is not None  # unexpired remains
    assert pg_store.get(_scope(tenant_b, "b-old")) is not None  # other tenant UNTOUCHED


def test_ci_runs_the_idempotency_ttl_postgres_integration() -> None:
    ci = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    # The durable proof must run in CI's postgres-integration job, not silently skip.
    assert "test_slice96_idempotency_ttl_postgres.py" in ci
