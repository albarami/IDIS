"""Slice98 Task 5 (durable) - Postgres break-glass grants: migration 0028, RLS, single-use.

Env-gated: skips locally without IDIS_DATABASE_URL / IDIS_DATABASE_ADMIN_URL; with
IDIS_REQUIRE_POSTGRES=1 (CI) it fails instead of skipping. Proves what the in-memory twin cannot:
the ``break_glass_grants`` schema (unique (tenant_id, token_sha256) enforcement lookup, guarded
RLS), grant durability and STRICT single-use across store/app instances (restart/replica), RLS
tenant isolation with no cross-tenant oracle, atomic concurrent consumption (exactly one winner),
and the REAL request path: issue via the route -> use once -> a second use through a brand-new app
instance is denied because consumption is durable. PYTHONPATH pinned to this worktree's src.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from collections.abc import Generator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from idis.api.auth import IDIS_API_KEYS_ENV
from idis.api.break_glass_grants import (
    BreakGlassGrant,
    PostgresBreakGlassGrantStore,
    reset_break_glass_grant_store,
)
from idis.api.main import create_app

ADMIN_URL_ENV = "IDIS_DATABASE_ADMIN_URL"
APP_URL_ENV = "IDIS_DATABASE_URL"
REQUIRE_POSTGRES_ENV = "IDIS_REQUIRE_POSTGRES"

_TENANT_A = "11111111-1111-1111-1111-111111111111"
_TENANT_B = "22222222-2222-2222-2222-222222222222"
_ADMIN_KEY = "bg-pg-admin-key"
_ADMIN_ACTOR = "bg-pg-admin"
_JUSTIFICATION = "Regulator deadline today; need immediate deal access."


def _skip_or_fail_if_no_postgres() -> None:
    admin_url = os.environ.get(ADMIN_URL_ENV)
    app_url = os.environ.get(APP_URL_ENV)
    require = os.environ.get(REQUIRE_POSTGRES_ENV, "0") == "1"
    if not admin_url or not app_url:
        msg = f"Postgres break-glass integration requires {ADMIN_URL_ENV} and {APP_URL_ENV}"
        if require:
            pytest.fail(f"REQUIRED: {msg} (IDIS_REQUIRE_POSTGRES=1)")
        pytest.skip(msg)


@pytest.fixture(scope="module")
def _pg_schema() -> Generator[None, None, None]:
    """Migrate to head (idempotent) so migration 0028's break_glass_grants table exists."""
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
def bg_pg(_pg_schema: None) -> Generator[None, None, None]:
    """Clean break_glass_grants (admin bypasses RLS) and reset the grant-store seam."""
    from idis.persistence.db import get_admin_engine

    def _truncate() -> None:
        with get_admin_engine().begin() as conn:
            conn.execute(text("TRUNCATE break_glass_grants"))

    reset_break_glass_grant_store()
    _truncate()
    yield
    _truncate()
    reset_break_glass_grant_store()


def _grant(tenant_id: str = _TENANT_A, **overrides: Any) -> BreakGlassGrant:
    now = time.time()
    defaults: dict[str, Any] = {
        "grant_id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "deal_id": str(uuid.uuid4()),
        "actor_id": _ADMIN_ACTOR,
        "justification": _JUSTIFICATION,
        "token_sha256": hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        "issued_at": now,
        "expires_at": now + 900,
    }
    defaults.update(overrides)
    return BreakGlassGrant(**defaults)


def test_break_glass_grants_schema_and_rls(bg_pg: None) -> None:
    """Migration 0028: enforcement-grade schema (unique token lookup) + guarded, forced RLS."""
    from idis.persistence.db import begin_app_conn

    with begin_app_conn() as conn:
        columns = {
            row.column_name
            for row in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'break_glass_grants'"
                )
            )
        }
        assert {
            "tenant_id",
            "grant_id",
            "deal_id",
            "actor_id",
            "justification",
            "token_sha256",
            "issued_at",
            "expires_at",
            "consumed_at",
            "consumed_request_id",
        } <= columns

        indexes = [
            row.indexdef
            for row in conn.execute(
                text("SELECT indexdef FROM pg_indexes WHERE tablename = 'break_glass_grants'")
            )
        ]
        unique_token_index = [
            d for d in indexes if "UNIQUE" in d and "token_sha256" in d and "tenant_id" in d
        ]
        assert unique_token_index, f"missing UNIQUE (tenant_id, token_sha256) index: {indexes}"

        rls = conn.execute(
            text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE relname = 'break_glass_grants'"
            )
        ).fetchone()
        assert rls is not None
        assert rls.relrowsecurity, "RLS not enabled on break_glass_grants"
        assert rls.relforcerowsecurity, "RLS not forced on break_glass_grants"


def test_grant_durable_and_single_use_across_store_instances(bg_pg: None) -> None:
    grant = _grant()
    PostgresBreakGlassGrantStore().record_grant(grant)

    # a second instance stands in for a fresh replica/restart: consumption is durable in the DB
    assert (
        PostgresBreakGlassGrantStore().consume_grant(
            _TENANT_A, grant.token_sha256, request_id="req-1"
        )
        is True
    )
    loaded = PostgresBreakGlassGrantStore().get_grant(_TENANT_A, grant.grant_id)
    assert loaded is not None
    assert loaded.consumed_at is not None
    assert loaded.consumed_request_id == "req-1"
    assert (
        PostgresBreakGlassGrantStore().consume_grant(
            _TENANT_A, grant.token_sha256, request_id="req-2"
        )
        is False
    )


def test_rls_blocks_cross_tenant_grant_access(bg_pg: None) -> None:
    """Tenant B can neither read nor consume tenant A's grant (uniform miss, no oracle)."""
    grant = _grant(tenant_id=_TENANT_A)
    store = PostgresBreakGlassGrantStore()
    store.record_grant(grant)

    assert store.get_grant(_TENANT_B, grant.grant_id) is None
    assert store.consume_grant(_TENANT_B, grant.token_sha256, request_id=None) is False
    # tenant A still holds the unconsumed grant
    assert store.consume_grant(_TENANT_A, grant.token_sha256, request_id=None) is True


def test_expired_grant_not_consumable(bg_pg: None) -> None:
    grant = _grant(issued_at=time.time() - 120, expires_at=time.time() - 60)
    store = PostgresBreakGlassGrantStore()
    store.record_grant(grant)
    assert store.consume_grant(_TENANT_A, grant.token_sha256, request_id=None) is False


def test_concurrent_consume_exactly_one_success(bg_pg: None) -> None:
    """The conditional UPDATE is the arbiter: N racing consumers, exactly one winner."""
    grant = _grant()
    PostgresBreakGlassGrantStore().record_grant(grant)

    thread_count = 4
    barrier = threading.Barrier(thread_count)
    results: list[bool] = []
    lock = threading.Lock()

    def _race(worker: int) -> None:
        store = PostgresBreakGlassGrantStore()  # own connection per call
        barrier.wait()
        outcome = store.consume_grant(_TENANT_A, grant.token_sha256, request_id=f"req-{worker}")
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=_race, args=(i,)) for i in range(thread_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count(True) == 1, f"expected exactly one success, got {results}"


def test_wire_and_prove_issue_use_reuse_denied_across_app_instances(
    bg_pg: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REAL path: route issues a durable grant; one use; a fresh app instance denies reuse."""
    from idis.api.abac import reset_deal_assignment_store
    from idis.api.routes.deals import clear_deals_store

    monkeypatch.setenv(
        IDIS_API_KEYS_ENV,
        json.dumps(
            {
                _ADMIN_KEY: {
                    "tenant_id": _TENANT_A,
                    "actor_id": _ADMIN_ACTOR,
                    "name": "BG PG Admin",
                    "timezone": "UTC",
                    "data_region": "us-east-1",
                    "roles": ["ADMIN"],
                }
            }
        ),
    )
    monkeypatch.setenv("IDIS_BREAK_GLASS_SECRET", "pg-break-glass-secret")
    monkeypatch.setenv("IDIS_ENABLE_DURABLE_BREAK_GLASS", "1")
    clear_deals_store()
    reset_deal_assignment_store()
    try:
        headers = {"X-IDIS-API-Key": _ADMIN_KEY, "Content-Type": "application/json"}
        client1 = TestClient(create_app(service_region="us-east-1"))
        deal = client1.post(
            "/v1/deals", json={"name": "BG", "company_name": "Acme"}, headers=headers
        )
        assert deal.status_code == 201, deal.text
        deal_id = deal.json()["deal_id"]

        issued = client1.post(
            "/v1/break-glass/grants",
            json={"deal_id": deal_id, "justification": _JUSTIFICATION},
            headers=headers,
        )
        assert issued.status_code == 201, issued.text
        token = issued.json()["token"]

        bg_headers = dict(headers)
        bg_headers["X-IDIS-Break-Glass"] = token
        first = client1.get(f"/v1/deals/{deal_id}", headers=bg_headers)
        assert first.status_code == 200, first.text

        # a brand-new app instance (fresh seam, fresh middleware) = replica; the consumption is
        # durable in Postgres, so the same token is denied everywhere
        reset_break_glass_grant_store()
        client2 = TestClient(create_app(service_region="us-east-1"))
        second = client2.get(f"/v1/deals/{deal_id}", headers=bg_headers)
        assert second.status_code == 403, second.text
        assert second.json()["code"] == "BREAK_GLASS_GRANT_INVALID"
    finally:
        clear_deals_store()
        reset_deal_assignment_store()
