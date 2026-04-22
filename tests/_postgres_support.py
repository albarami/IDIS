"""Shared Postgres test support (Sprint 1 Wave 2, Task 7).

Thin helpers reused by the four pre-existing ingestion/document/run test
files so they can run cleanly under `IDIS_REQUIRE_POSTGRES=1`. Mirrors the
style already used by tests/test_api_deals_postgres.py and the Task 5/6
Postgres-gated suites:

* admin connection runs alembic upgrade head and does TRUNCATEs,
* tests operate through the real app path via the `idis_app` role,
* FK-dependent seed rows (deals) are inserted via the admin connection
  so RLS does not get in the way of test setup.

No new fallbacks. No production changes. When Postgres env is not set
these helpers skip the callers exactly the way the existing
`test_api_deals_postgres.py` pattern does.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy import Engine


ADMIN_URL_ENV = "IDIS_DATABASE_ADMIN_URL"
APP_URL_ENV = "IDIS_DATABASE_URL"
REQUIRE_POSTGRES_ENV = "IDIS_REQUIRE_POSTGRES"


# Tables created by the current migrations that these test files touch
# directly or transitively. Ordered so TRUNCATE CASCADE doesn't need to
# traverse more than one FK depth.
_TRUNCATE_TABLES = (
    "document_spans",
    "documents",
    "document_artifacts",
    "evidence_items",
    "defects",
    "sanads",
    "claims",
    "run_steps",
    "runs",
    "debate_sessions",
    "deliverables",
    "human_gate_actions",
    "human_gates",
    "overrides",
    "calc_sanads",
    "deterministic_calculations",
    "webhook_delivery_attempts",
    "webhooks",
    "enrichment_credentials",
    "idempotency_records",
    "audit_events",
    "deals",
)


def postgres_configured() -> bool:
    """True when both Postgres URL env vars are set."""
    return bool(os.environ.get(ADMIN_URL_ENV)) and bool(os.environ.get(APP_URL_ENV))


def skip_or_fail_if_no_postgres() -> None:
    """Shared skip gate used inside module-scope fixtures."""
    require_postgres = os.environ.get(REQUIRE_POSTGRES_ENV, "0") == "1"
    if not postgres_configured():
        msg = f"Requires {ADMIN_URL_ENV} and {APP_URL_ENV}"
        if require_postgres:
            pytest.fail(f"REQUIRED: {msg} (IDIS_REQUIRE_POSTGRES=1)")
        else:
            pytest.skip(msg)


def admin_engine_generator() -> Generator[Engine, None, None]:
    """Module-scope admin engine fixture body."""
    skip_or_fail_if_no_postgres()
    from idis.persistence.db import get_admin_engine, reset_engines

    engine = get_admin_engine()
    yield engine
    reset_engines()


def migrated_db_generator(admin_engine: Engine) -> Generator[None, None, None]:
    """Module-scope alembic upgrade/downgrade fixture body."""
    from alembic import command
    from alembic.config import Config

    import idis.persistence.migrations as migrations_pkg

    config = Config()
    config.set_main_option("script_location", os.path.dirname(migrations_pkg.__file__))
    with admin_engine.begin() as conn:
        config.attributes["connection"] = conn
        command.upgrade(config, "head")
    yield
    with admin_engine.begin() as conn:
        config.attributes["connection"] = conn
        command.downgrade(config, "base")


def truncate_all(admin_engine: Engine) -> None:
    """TRUNCATE every table the test surface touches."""
    table_list = ", ".join(_TRUNCATE_TABLES)
    with admin_engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {table_list} RESTART IDENTITY CASCADE"))


def seed_deal(admin_engine: Engine, *, deal_id: str, tenant_id: str) -> None:
    """Insert a minimal deals row for FK satisfaction.

    Done via the admin connection so the test does not have to set up an
    RLS tenant context to bootstrap its own seed data.
    """
    with admin_engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO deals (
                    deal_id, tenant_id, name, company_name, status,
                    stage, tags, created_at, updated_at
                ) VALUES (
                    :deal_id, :tenant_id, 'seed-deal', 'seed-company', 'NEW',
                    NULL, CAST('[]' AS JSONB), now(), NULL
                )
                ON CONFLICT (deal_id) DO NOTHING
                """
            ),
            {"deal_id": deal_id, "tenant_id": tenant_id},
        )
