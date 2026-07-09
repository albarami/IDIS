"""Slice96 Task 4 (durable) — Postgres-backed provider budget: real cross-instance + race proof.

Env-gated: skips locally without IDIS_DATABASE_URL / IDIS_DATABASE_ADMIN_URL; with
IDIS_REQUIRE_POSTGRES=1 (CI) it fails instead of skipping. Proves the durable hard cap an
in-memory per-process counter cannot give: two ``PostgresProviderBudgetStore`` instances (standing
in for replicas) share ONE cap through the ``provider_budget_usage`` table, and many concurrent
consumers cannot exceed the cap (race-safe atomic consume-under-cap). PYTHONPATH pinned to src.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from sqlalchemy import text

from idis.providers.budget import PostgresProviderBudgetStore

ADMIN_URL_ENV = "IDIS_DATABASE_ADMIN_URL"
APP_URL_ENV = "IDIS_DATABASE_URL"
REQUIRE_POSTGRES_ENV = "IDIS_REQUIRE_POSTGRES"


def _skip_or_fail_if_no_postgres() -> None:
    admin_url = os.environ.get(ADMIN_URL_ENV)
    app_url = os.environ.get(APP_URL_ENV)
    require = os.environ.get(REQUIRE_POSTGRES_ENV, "0") == "1"
    if not admin_url or not app_url:
        msg = f"Postgres budget integration requires {ADMIN_URL_ENV} and {APP_URL_ENV}"
        if require:
            pytest.fail(f"REQUIRED: {msg} (IDIS_REQUIRE_POSTGRES=1)")
        pytest.skip(msg)


@pytest.fixture(scope="module")
def _pg_schema() -> Generator[None, None, None]:
    """Ensure the schema is migrated to head (idempotent); leaves it in place for reuse."""
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
def budget_tenant(_pg_schema: None) -> Generator[str, None, None]:
    """A unique tenant per test; truncate the usage table (admin bypasses RLS) for isolation."""
    from idis.persistence.db import get_admin_engine

    def _truncate() -> None:
        with get_admin_engine().begin() as conn:
            conn.execute(text("TRUNCATE provider_budget_usage"))

    _truncate()
    yield str(uuid.uuid4())
    _truncate()


def _key(tenant: str) -> str:
    return f"{tenant}:anthropic"


def _durable_used(tenant: str) -> int:
    from idis.persistence.db import get_admin_engine

    with get_admin_engine().begin() as conn:
        value = conn.execute(
            text(
                "SELECT used FROM provider_budget_usage "
                "WHERE tenant_id = :t AND provider = 'anthropic'"
            ),
            {"t": tenant},
        ).scalar()
    return int(value or 0)


def test_two_store_instances_share_one_cap_across_replicas(budget_tenant: str) -> None:
    # Two instances = two replicas hitting the same durable table -> a single shared cap.
    replica_a = PostgresProviderBudgetStore()
    replica_b = PostgresProviderBudgetStore()
    assert replica_a.consume(key=_key(budget_tenant), amount=1, cap=3)[0] is True  # used 1
    assert replica_b.consume(key=_key(budget_tenant), amount=1, cap=3)[0] is True  # used 2 (shared)
    assert replica_a.consume(key=_key(budget_tenant), amount=1, cap=3)[0] is True  # used 3
    denied, used = replica_b.consume(key=_key(budget_tenant), amount=1, cap=3)  # 4th -> denied
    assert denied is False
    assert used == 3  # shared bucket exhausted across both instances (not 3 + 3)
    assert _durable_used(budget_tenant) == 3


def test_concurrent_consumers_cannot_exceed_cap(budget_tenant: str) -> None:
    # Race safety: more concurrent consumers than the cap -> exactly `cap` succeed, never more.
    from idis.persistence.db import get_app_engine

    cap = 20
    workers = 40
    stores = [PostgresProviderBudgetStore() for _ in range(workers)]
    get_app_engine()  # warm the cached engine before concurrent checkout

    def _consume(i: int) -> bool:
        return stores[i].consume(key=_key(budget_tenant), amount=1, cap=cap)[0]

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(_consume, range(workers)))

    assert sum(results) == cap  # exactly cap allowed
    assert results.count(False) == workers - cap  # the rest denied
    assert _durable_used(budget_tenant) == cap  # durable total never exceeded the cap


_INSERT_ROW_SQL = (
    "INSERT INTO provider_budget_usage (tenant_id, provider, used) "
    "VALUES (CAST(:t AS uuid), 'anthropic', 1)"
)


def test_no_tenant_context_write_is_blocked(budget_tenant: str) -> None:
    # RLS + WITH CHECK: with no tenant context set, the app role cannot write a usage row.
    from sqlalchemy.exc import DBAPIError

    from idis.persistence.db import begin_app_conn

    with pytest.raises(DBAPIError), begin_app_conn() as conn:  # no set_tenant_local
        conn.execute(text(_INSERT_ROW_SQL), {"t": budget_tenant})
    assert _durable_used(budget_tenant) == 0  # nothing was written


def test_mismatched_tenant_write_is_blocked(budget_tenant: str) -> None:
    # RLS WITH CHECK: with tenant A set, the app role cannot insert a row owned by tenant B.
    from sqlalchemy.exc import DBAPIError

    from idis.persistence.db import begin_app_conn, set_tenant_local

    other = str(uuid.uuid4())
    with pytest.raises(DBAPIError), begin_app_conn() as conn:
        set_tenant_local(conn, budget_tenant)  # tenant A
        conn.execute(text(_INSERT_ROW_SQL), {"t": other})  # row for tenant B
    assert _durable_used(other) == 0


def test_ci_runs_the_provider_budget_postgres_integration() -> None:
    ci = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    # The durable proof must run in CI's postgres-integration job, not silently skip.
    assert "test_slice96_provider_budget_postgres.py" in ci
