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

TENANT_A_ID = "11111111-1111-1111-1111-111111111111"
TENANT_B_ID = "22222222-2222-2222-2222-222222222222"


def _skip_or_fail_if_no_postgres() -> None:
    """Skip or fail test if PostgreSQL is not configured.

    If IDIS_REQUIRE_POSTGRES=1, fails the test (for CI).
    Otherwise, skips the test (for local dev without Postgres).
    """
    admin_url = os.environ.get(ADMIN_URL_ENV)
    app_url = os.environ.get(APP_URL_ENV)
    require_postgres = os.environ.get(REQUIRE_POSTGRES_ENV, "0") == "1"

    if not admin_url or not app_url:
        msg = f"PostgreSQL integration tests require {ADMIN_URL_ENV} and {APP_URL_ENV} env vars"
        if require_postgres:
            pytest.fail(f"REQUIRED: {msg} (IDIS_REQUIRE_POSTGRES=1)")
        else:
            pytest.skip(msg)


@pytest.fixture(scope="module")
def admin_engine() -> Generator[Engine, None, None]:
    """Create admin engine for migrations and test setup."""
    _skip_or_fail_if_no_postgres()

    from idis.persistence.db import get_admin_engine, reset_engines

    engine = get_admin_engine()
    yield engine
    reset_engines()


@pytest.fixture(scope="module")
def app_engine() -> Generator[Engine, None, None]:
    """Create app engine for non-superuser operations."""
    _skip_or_fail_if_no_postgres()

    from idis.persistence.db import get_app_engine, reset_engines

    engine = get_app_engine()
    yield engine
    reset_engines()


@pytest.fixture(scope="module")
def migrated_db(admin_engine: Engine) -> Generator[None, None, None]:
    """Run migrations to set up schema before tests."""
    import os

    from alembic import command
    from alembic.config import Config

    import idis.persistence.migrations as migrations_pkg

    migrations_dir = os.path.dirname(migrations_pkg.__file__)

    config = Config()
    config.set_main_option("script_location", migrations_dir)

    with admin_engine.begin() as conn:
        config.attributes["connection"] = conn
        command.upgrade(config, "head")

    yield

    with admin_engine.begin() as conn:
        config.attributes["connection"] = conn
        command.downgrade(config, "base")


@pytest.fixture
def clean_tables(admin_engine: Engine, migrated_db: None) -> Generator[None, None, None]:
    """Clean tables before each test using TRUNCATE (bypasses immutability trigger)."""
    with admin_engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE document_spans, documents, document_artifacts, deals, "
                "idempotency_records, audit_events, "
                "webhook_delivery_attempts, webhooks CASCADE"
            )
        )

    yield

    with admin_engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE document_spans, documents, document_artifacts, deals, "
                "idempotency_records, audit_events, "
                "webhook_delivery_attempts, webhooks CASCADE"
            )
        )
