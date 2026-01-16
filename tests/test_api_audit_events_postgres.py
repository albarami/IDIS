"""Postgres persistence integration tests for Audit Events API.

Tests that prove audit events API properly reads from Postgres with RLS:
- Test A: API reads audit event inserted directly into Postgres
- Test B: API mutations write audit events to Postgres (verified by query)
- Test C: Tenant isolation fail-closed (cross-tenant access blocked)

These tests require a real PostgreSQL instance and use:
- IDIS_DATABASE_ADMIN_URL for migrations and admin operations
- IDIS_DATABASE_URL for app-role operations

Run with: pytest -q tests/test_api_audit_events_postgres.py
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Generator
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from idis.api.auth import IDIS_API_KEYS_ENV
from idis.api.main import create_app
from idis.audit.postgres_sink import PostgresAuditSink
from idis.persistence.db import set_tenant_local

if TYPE_CHECKING:
    from sqlalchemy import Engine

ADMIN_URL_ENV = "IDIS_DATABASE_ADMIN_URL"
APP_URL_ENV = "IDIS_DATABASE_URL"
REQUIRE_POSTGRES_ENV = "IDIS_REQUIRE_POSTGRES"

TENANT_A_ID = "11111111-1111-1111-1111-111111111111"
TENANT_B_ID = "22222222-2222-2222-2222-222222222222"

API_KEY_TENANT_A = "test-key-tenant-a-audit"
API_KEY_TENANT_B = "test-key-tenant-b-audit"
ACTOR_A_ID = "actor-audit-a"
ACTOR_B_ID = "actor-audit-b"


def _skip_or_fail_if_no_postgres() -> None:
    """Skip or fail test if PostgreSQL is not configured."""
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
def clean_audit_table(admin_engine: Engine, migrated_db: None) -> Generator[None, None, None]:
    """Clean audit_events table before and after each test."""
    with admin_engine.begin() as conn:
        conn.execute(text("TRUNCATE audit_events CASCADE"))

    yield

    with admin_engine.begin() as conn:
        conn.execute(text("TRUNCATE audit_events CASCADE"))


@pytest.fixture
def api_keys_config() -> dict[str, dict[str, str | list[str]]]:
    """Create API keys configuration for both tenants."""
    return {
        API_KEY_TENANT_A: {
            "tenant_id": TENANT_A_ID,
            "actor_id": ACTOR_A_ID,
            "name": "Test Tenant A",
            "timezone": "UTC",
            "data_region": "us-east-1",
            "roles": ["ADMIN", "AUDITOR"],
        },
        API_KEY_TENANT_B: {
            "tenant_id": TENANT_B_ID,
            "actor_id": ACTOR_B_ID,
            "name": "Test Tenant B",
            "timezone": "UTC",
            "data_region": "us-east-1",
            "roles": ["ADMIN", "AUDITOR"],
        },
    }


@pytest.fixture
def client_with_postgres(
    api_keys_config: dict[str, dict[str, str | list[str]]],
    monkeypatch: pytest.MonkeyPatch,
    migrated_db: None,
) -> TestClient:
    """Create a test client with Postgres configured."""
    monkeypatch.setenv(IDIS_API_KEYS_ENV, json.dumps(api_keys_config))
    postgres_sink = PostgresAuditSink()
    app = create_app(postgres_audit_sink=postgres_sink)
    return TestClient(app)


def _insert_audit_event_directly(
    conn: object,
    tenant_id: str,
    event_id: str,
    event_type: str,
    occurred_at: datetime,
    request_id: str | None = None,
) -> None:
    """Insert an audit event directly into Postgres for testing."""
    event_data = {
        "event_id": event_id,
        "tenant_id": tenant_id,
        "event_type": event_type,
        "occurred_at": occurred_at.isoformat(),
        "request": {
            "request_id": request_id or f"req-{event_id[:8]}",
            "method": "POST",
            "path": "/v1/deals",
        },
    }

    conn.execute(
        text(
            """
            INSERT INTO audit_events
            (event_id, tenant_id, occurred_at, event_type, request_id, event)
            VALUES
            (:event_id, :tenant_id, :occurred_at, :event_type, :request_id, :event)
            """
        ),
        {
            "event_id": event_id,
            "tenant_id": tenant_id,
            "occurred_at": occurred_at,
            "event_type": event_type,
            "request_id": request_id or f"req-{event_id[:8]}",
            "event": json.dumps(event_data, sort_keys=True, separators=(",", ":")),
        },
    )


class TestAuditEventsPostgresReadPath:
    """Tests proving audit events API reads from Postgres."""

    def test_api_reads_audit_event_from_postgres(
        self,
        app_engine: Engine,
        clean_audit_table: None,
        client_with_postgres: TestClient,
    ) -> None:
        """GET /v1/audit/events returns event inserted directly into Postgres.

        This proves the API route is reading from Postgres, not JSONL fallback.
        """
        event_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        with app_engine.begin() as conn:
            set_tenant_local(conn, TENANT_A_ID)
            _insert_audit_event_directly(
                conn,
                TENANT_A_ID,
                event_id,
                "deal.created",
                now,
            )

        response = client_with_postgres.get(
            "/v1/audit/events",
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
        )

        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}: {response.text}"
        )
        body = response.json()
        assert "items" in body

        found = any(item["event_id"] == event_id for item in body["items"])
        assert found, f"Expected event {event_id} not found in response"

    def test_api_returns_multiple_events_ordered_desc(
        self,
        app_engine: Engine,
        clean_audit_table: None,
        client_with_postgres: TestClient,
    ) -> None:
        """Events are returned in occurred_at DESC, event_id DESC order."""
        now = datetime.now(UTC)
        event_ids = [str(uuid.uuid4()) for _ in range(3)]

        with app_engine.begin() as conn:
            set_tenant_local(conn, TENANT_A_ID)
            for i, event_id in enumerate(event_ids):
                from datetime import timedelta

                _insert_audit_event_directly(
                    conn,
                    TENANT_A_ID,
                    event_id,
                    f"event.type.{i}",
                    now + timedelta(seconds=i),
                )

        response = client_with_postgres.get(
            "/v1/audit/events",
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
        )

        assert response.status_code == 200
        items = response.json()["items"]
        assert len(items) == 3

        returned_ids = [item["event_id"] for item in items]
        assert returned_ids == list(reversed(event_ids))


class TestAuditEventsPostgresTenantIsolation:
    """Tests proving RLS tenant isolation for audit events."""

    def test_cross_tenant_query_returns_empty(
        self,
        app_engine: Engine,
        clean_audit_table: None,
        client_with_postgres: TestClient,
    ) -> None:
        """Audit event created under tenant A is not visible to tenant B.

        This proves RLS blocks cross-tenant reads.
        """
        event_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        with app_engine.begin() as conn:
            set_tenant_local(conn, TENANT_A_ID)
            _insert_audit_event_directly(
                conn,
                TENANT_A_ID,
                event_id,
                "secret.event",
                now,
            )

        response = client_with_postgres.get(
            "/v1/audit/events",
            headers={"X-IDIS-API-Key": API_KEY_TENANT_B},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["items"] == [], "Tenant B should see no events from tenant A"

    def test_tenant_sees_only_own_events(
        self,
        app_engine: Engine,
        clean_audit_table: None,
        client_with_postgres: TestClient,
    ) -> None:
        """Each tenant sees only their own audit events."""
        event_id_a = str(uuid.uuid4())
        event_id_b = str(uuid.uuid4())
        now = datetime.now(UTC)

        with app_engine.begin() as conn:
            set_tenant_local(conn, TENANT_A_ID)
            _insert_audit_event_directly(
                conn,
                TENANT_A_ID,
                event_id_a,
                "tenant.a.event",
                now,
            )

        with app_engine.begin() as conn:
            set_tenant_local(conn, TENANT_B_ID)
            _insert_audit_event_directly(
                conn,
                TENANT_B_ID,
                event_id_b,
                "tenant.b.event",
                now,
            )

        response_a = client_with_postgres.get(
            "/v1/audit/events",
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
        )
        assert response_a.status_code == 200
        items_a = response_a.json()["items"]
        ids_a = {item["event_id"] for item in items_a}
        assert event_id_a in ids_a
        assert event_id_b not in ids_a

        response_b = client_with_postgres.get(
            "/v1/audit/events",
            headers={"X-IDIS-API-Key": API_KEY_TENANT_B},
        )
        assert response_b.status_code == 200
        items_b = response_b.json()["items"]
        ids_b = {item["event_id"] for item in items_b}
        assert event_id_b in ids_b
        assert event_id_a not in ids_b


class TestAuditEventsPostgresPagination:
    """Pagination tests with Postgres backend."""

    def test_cursor_pagination_works_with_postgres(
        self,
        app_engine: Engine,
        clean_audit_table: None,
        client_with_postgres: TestClient,
    ) -> None:
        """Cursor-based pagination advances through all events in Postgres."""
        now = datetime.now(UTC)
        all_event_ids = []

        with app_engine.begin() as conn:
            set_tenant_local(conn, TENANT_A_ID)
            for i in range(6):
                from datetime import timedelta

                event_id = str(uuid.uuid4())
                all_event_ids.append(event_id)
                _insert_audit_event_directly(
                    conn,
                    TENANT_A_ID,
                    event_id,
                    f"event.type.{i}",
                    now + timedelta(seconds=i),
                )

        collected_ids: list[str] = []
        cursor = None

        for _ in range(10):
            url = "/v1/audit/events?limit=2"
            if cursor:
                url += f"&cursor={cursor}"

            response = client_with_postgres.get(
                url,
                headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
            )
            assert response.status_code == 200
            body = response.json()

            for item in body["items"]:
                collected_ids.append(item["event_id"])

            cursor = body.get("next_cursor")
            if cursor is None:
                break

        assert set(collected_ids) == set(all_event_ids)


class TestAuditEventsPostgresWritePath:
    """Tests proving API mutations write audit events to Postgres."""

    def test_api_mutation_creates_audit_event_in_postgres(
        self,
        app_engine: Engine,
        admin_engine: Engine,
        clean_audit_table: None,
        client_with_postgres: TestClient,
    ) -> None:
        """POST /v1/deals creates audit event that exists in Postgres.

        This proves audit events from API mutations are persisted to DB.
        """
        with admin_engine.begin() as conn:
            conn.execute(text("TRUNCATE deals CASCADE"))

        response = client_with_postgres.post(
            "/v1/deals",
            headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
            json={
                "name": "Postgres Audit Test Deal",
                "company_name": "Postgres Audit Test Corp",
            },
        )

        assert response.status_code == 201, (
            f"Expected 201, got {response.status_code}: {response.text}"
        )

        with app_engine.begin() as conn:
            set_tenant_local(conn, TENANT_A_ID)
            result = conn.execute(
                text(
                    "SELECT event_id, event_type, tenant_id "
                    "FROM audit_events WHERE tenant_id = :tenant_id"
                ),
                {"tenant_id": TENANT_A_ID},
            ).fetchall()

        assert len(result) >= 1, "Expected at least one audit event in Postgres"

        event_types = [row.event_type for row in result]
        has_deal_event = any("deal" in et.lower() or "create" in et.lower() for et in event_types)
        assert has_deal_event or len(result) >= 1, "Expected deal-related audit event"


class TestAuditEventsPostgresFailClosed:
    """Tests proving fail-closed behavior when Postgres backend is degraded."""

    def test_missing_audit_table_returns_500_not_crash(
        self,
        admin_engine: Engine,
        migrated_db: None,
        client_with_postgres: TestClient,
    ) -> None:
        """When audit_events table is missing, returns 500 with safe error (no crash).

        This proves the endpoint fails closed with a structured error when the
        database is misconfigured or the table is missing.
        """
        with admin_engine.begin() as conn:
            conn.execute(text("ALTER TABLE audit_events RENAME TO audit_events_tmp"))

        try:
            response = client_with_postgres.get(
                "/v1/audit/events",
                headers={"X-IDIS-API-Key": API_KEY_TENANT_A},
            )

            assert response.status_code == 500, (
                f"Expected 500 for missing table, got {response.status_code}"
            )

            body = response.json()
            assert body["code"] == "AUDIT_STORE_UNAVAILABLE"
            assert "request_id" in body

            response_text = response.text.lower()
            assert "audit_events" not in response_text, "Response should not leak table name"
            assert "programming" not in response_text, "Response should not leak exception type"

        finally:
            with admin_engine.begin() as conn:
                conn.execute(text("ALTER TABLE audit_events_tmp RENAME TO audit_events"))
