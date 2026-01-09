"""PostgreSQL RLS and Audit Immutability Integration Tests.

Tests for:
- Row-Level Security (RLS) tenant isolation
- Audit table immutability (UPDATE/DELETE blocked by trigger)
- Cross-tenant read/write blocking

These tests require a real PostgreSQL instance and use:
- IDIS_DATABASE_ADMIN_URL for migrations and admin operations
- IDIS_DATABASE_URL for app-role operations

Run with: pytest -q tests/test_postgres_rls_and_audit_immutability.py
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Generator
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError

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


class TestAppRoleSecurity:
    """Tests for app role security constraints."""

    def test_app_role_is_not_superuser(self, app_engine: Engine, migrated_db: None) -> None:
        """Verify app role is NOT a superuser (required for RLS to be enforced)."""
        with app_engine.connect() as conn:
            result = conn.execute(
                text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            )
            row = result.fetchone()

        assert row is not None, "App role should exist in pg_roles"
        assert row.rolsuper is False, (
            "App role MUST NOT be superuser - RLS is bypassed for superusers"
        )
        assert row.rolbypassrls is False, "App role MUST NOT have BYPASSRLS - RLS would be bypassed"


class TestRLSTenantIsolation:
    """Tests for Row-Level Security tenant isolation."""

    def test_rls_blocks_cross_tenant_reads(self, app_engine: Engine, clean_tables: None) -> None:
        """Insert under tenant A, read under tenant B => 0 rows."""
        deal_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        with app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_A_ID}'"))
            conn.execute(
                text(
                    """
                    INSERT INTO deals (deal_id, tenant_id, name, created_at)
                    VALUES (:deal_id, :tenant_id, :name, :created_at)
                    """
                ),
                {
                    "deal_id": deal_id,
                    "tenant_id": TENANT_A_ID,
                    "name": "Tenant A Deal",
                    "created_at": now,
                },
            )

        with app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_B_ID}'"))
            result = conn.execute(
                text("SELECT deal_id, name FROM deals WHERE deal_id = :deal_id"),
                {"deal_id": deal_id},
            )
            rows = result.fetchall()

        assert len(rows) == 0, "RLS should block cross-tenant reads"

    def test_rls_allows_same_tenant_reads(self, app_engine: Engine, clean_tables: None) -> None:
        """Insert under tenant A, read under tenant A => 1 row."""
        deal_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        with app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_A_ID}'"))
            conn.execute(
                text(
                    """
                    INSERT INTO deals (deal_id, tenant_id, name, created_at)
                    VALUES (:deal_id, :tenant_id, :name, :created_at)
                    """
                ),
                {
                    "deal_id": deal_id,
                    "tenant_id": TENANT_A_ID,
                    "name": "Tenant A Deal",
                    "created_at": now,
                },
            )

        with app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_A_ID}'"))
            result = conn.execute(
                text("SELECT deal_id, name FROM deals WHERE deal_id = :deal_id"),
                {"deal_id": deal_id},
            )
            rows = result.fetchall()

        assert len(rows) == 1, "RLS should allow same-tenant reads"
        assert rows[0].name == "Tenant A Deal"

    def test_rls_fails_closed_without_tenant_context(
        self, app_engine: Engine, clean_tables: None
    ) -> None:
        """No SET LOCAL => select returns 0 rows (fail closed)."""
        deal_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        with app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_A_ID}'"))
            conn.execute(
                text(
                    """
                    INSERT INTO deals (deal_id, tenant_id, name, created_at)
                    VALUES (:deal_id, :tenant_id, :name, :created_at)
                    """
                ),
                {
                    "deal_id": deal_id,
                    "tenant_id": TENANT_A_ID,
                    "name": "Tenant A Deal",
                    "created_at": now,
                },
            )

        with app_engine.begin() as conn:
            result = conn.execute(text("SELECT deal_id, name FROM deals"))
            rows = result.fetchall()

        assert len(rows) == 0, "RLS should return 0 rows without tenant context"

    def test_rls_blocks_insert_without_tenant_context(
        self, app_engine: Engine, clean_tables: None
    ) -> None:
        """Insert without SET LOCAL should be blocked by RLS WITH CHECK."""
        deal_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        with pytest.raises(DBAPIError) as exc_info, app_engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO deals (deal_id, tenant_id, name, created_at)
                    VALUES (:deal_id, :tenant_id, :name, :created_at)
                    """
                ),
                {
                    "deal_id": deal_id,
                    "tenant_id": TENANT_A_ID,
                    "name": "Should Fail",
                    "created_at": now,
                },
            )

        error_msg = str(exc_info.value).lower()
        assert (
            "row-level security" in error_msg
            or "policy" in error_msg
            or "permission denied" in error_msg
        ), f"INSERT without tenant context should be blocked by RLS, got: {exc_info.value}"

    def test_rls_blocks_cross_tenant_insert_mismatch(
        self, app_engine: Engine, clean_tables: None
    ) -> None:
        """Setting tenant A but inserting tenant B data should fail."""
        deal_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        with pytest.raises(ProgrammingError), app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_A_ID}'"))
            conn.execute(
                text(
                    """
                        INSERT INTO deals (deal_id, tenant_id, name, created_at)
                        VALUES (:deal_id, :tenant_id, :name, :created_at)
                        """
                ),
                {
                    "deal_id": deal_id,
                    "tenant_id": TENANT_B_ID,
                    "name": "Mismatched Tenant",
                    "created_at": now,
                },
            )


class TestAuditImmutability:
    """Tests for audit table immutability (UPDATE/DELETE blocked)."""

    def test_audit_update_blocked_by_trigger(self, app_engine: Engine, clean_tables: None) -> None:
        """UPDATE on audit_events should be blocked by trigger."""
        event_id = str(uuid.uuid4())
        now = datetime.now(UTC)
        event_data = {
            "event_id": event_id,
            "tenant_id": TENANT_A_ID,
            "event_type": "test.event",
            "occurred_at": now.isoformat(),
        }

        with app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_A_ID}'"))
            conn.execute(
                text(
                    """
                    INSERT INTO audit_events
                    (event_id, tenant_id, occurred_at, event_type, event)
                    VALUES (:event_id, :tenant_id, :occurred_at, :event_type, :event)
                    """
                ),
                {
                    "event_id": event_id,
                    "tenant_id": TENANT_A_ID,
                    "occurred_at": now,
                    "event_type": "test.event",
                    "event": json.dumps(event_data),
                },
            )

        with pytest.raises(DBAPIError) as exc_info, app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_A_ID}'"))
            conn.execute(
                text(
                    """
                    UPDATE audit_events
                    SET event_type = 'modified.event'
                    WHERE event_id = :event_id
                    """
                ),
                {"event_id": event_id},
            )

        assert "Audit events are immutable" in str(exc_info.value), (
            f"UPDATE should be blocked by immutability trigger, got: {exc_info.value}"
        )

    def test_audit_delete_blocked_by_trigger(self, app_engine: Engine, clean_tables: None) -> None:
        """DELETE on audit_events should be blocked by trigger."""
        event_id = str(uuid.uuid4())
        now = datetime.now(UTC)
        event_data = {
            "event_id": event_id,
            "tenant_id": TENANT_A_ID,
            "event_type": "test.event",
            "occurred_at": now.isoformat(),
        }

        with app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_A_ID}'"))
            conn.execute(
                text(
                    """
                    INSERT INTO audit_events
                    (event_id, tenant_id, occurred_at, event_type, event)
                    VALUES (:event_id, :tenant_id, :occurred_at, :event_type, :event)
                    """
                ),
                {
                    "event_id": event_id,
                    "tenant_id": TENANT_A_ID,
                    "occurred_at": now,
                    "event_type": "test.event",
                    "event": json.dumps(event_data),
                },
            )

        with pytest.raises(DBAPIError) as exc_info, app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_A_ID}'"))
            conn.execute(
                text("DELETE FROM audit_events WHERE event_id = :event_id"),
                {"event_id": event_id},
            )

        assert "Audit events are immutable" in str(exc_info.value), (
            f"DELETE should be blocked by immutability trigger, got: {exc_info.value}"
        )

    def test_audit_insert_allowed(self, app_engine: Engine, clean_tables: None) -> None:
        """INSERT on audit_events should be allowed (append-only)."""
        event_id = str(uuid.uuid4())
        now = datetime.now(UTC)
        event_data = {
            "event_id": event_id,
            "tenant_id": TENANT_A_ID,
            "event_type": "test.event",
            "occurred_at": now.isoformat(),
        }

        with app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_A_ID}'"))
            conn.execute(
                text(
                    """
                    INSERT INTO audit_events
                    (event_id, tenant_id, occurred_at, event_type, event)
                    VALUES (:event_id, :tenant_id, :occurred_at, :event_type, :event)
                    """
                ),
                {
                    "event_id": event_id,
                    "tenant_id": TENANT_A_ID,
                    "occurred_at": now,
                    "event_type": "test.event",
                    "event": json.dumps(event_data),
                },
            )

        with app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_A_ID}'"))
            result = conn.execute(
                text("SELECT event_id, event_type FROM audit_events WHERE event_id = :event_id"),
                {"event_id": event_id},
            )
            rows = result.fetchall()

        assert len(rows) == 1, "INSERT should be allowed"
        assert rows[0].event_type == "test.event"


class TestIdempotencyRecordsRLS:
    """Tests for idempotency_records table RLS."""

    def test_idempotency_rls_blocks_cross_tenant(
        self, app_engine: Engine, clean_tables: None
    ) -> None:
        """Insert idempotency record under tenant A, read under tenant B => 0 rows."""
        now = datetime.now(UTC)

        with app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_A_ID}'"))
            conn.execute(
                text(
                    """
                    INSERT INTO idempotency_records
                    (tenant_id, actor_id, method, operation_id, idempotency_key,
                     payload_sha256, status_code, media_type, body_bytes, created_at)
                    VALUES
                    (:tenant_id, :actor_id, :method, :operation_id, :idempotency_key,
                     :payload_sha256, :status_code, :media_type, :body_bytes, :created_at)
                    """
                ),
                {
                    "tenant_id": TENANT_A_ID,
                    "actor_id": "test-actor",
                    "method": "POST",
                    "operation_id": "createDeal",
                    "idempotency_key": "test-key-123",
                    "payload_sha256": "sha256:abc123",
                    "status_code": 201,
                    "media_type": "application/json",
                    "body_bytes": b'{"id": "test"}',
                    "created_at": now,
                },
            )

        with app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_B_ID}'"))
            result = conn.execute(
                text(
                    """
                    SELECT idempotency_key FROM idempotency_records
                    WHERE idempotency_key = 'test-key-123'
                    """
                )
            )
            rows = result.fetchall()

        assert len(rows) == 0, "RLS should block cross-tenant idempotency reads"


class TestPostgresAuditSink:
    """Tests for PostgresAuditSink functionality."""

    def test_postgres_audit_sink_emit_in_tx(self, app_engine: Engine, clean_tables: None) -> None:
        """Test PostgresAuditSink.emit_in_tx stores event correctly."""
        from idis.audit.postgres_sink import PostgresAuditSink
        from idis.persistence.db import set_tenant_local

        sink = PostgresAuditSink()
        event_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")

        event = {
            "event_id": event_id,
            "tenant_id": TENANT_A_ID,
            "occurred_at": now,
            "event_type": "test.sink.event",
            "actor": {"actor_type": "SERVICE", "actor_id": "test"},
            "request": {"request_id": "req-123", "method": "POST", "path": "/test"},
            "resource": {"resource_type": "test", "resource_id": str(uuid.uuid4())},
            "severity": "LOW",
            "summary": "Test event",
            "payload": {"refs": []},
        }

        with app_engine.begin() as conn:
            set_tenant_local(conn, TENANT_A_ID)
            sink.emit_in_tx(conn, event)

        with app_engine.begin() as conn:
            set_tenant_local(conn, TENANT_A_ID)
            result = conn.execute(
                text("SELECT event_id, event_type FROM audit_events WHERE event_id = :event_id"),
                {"event_id": event_id},
            )
            rows = result.fetchall()

        assert len(rows) == 1
        assert rows[0].event_type == "test.sink.event"


class TestPostgresIdempotencyStore:
    """Tests for PostgresIdempotencyStore functionality."""

    def test_postgres_idempotency_store_get_put(
        self, app_engine: Engine, clean_tables: None
    ) -> None:
        """Test PostgresIdempotencyStore get/put operations."""
        from idis.idempotency.postgres_store import PostgresIdempotencyStore
        from idis.idempotency.store import IdempotencyRecord, ScopeKey
        from idis.persistence.db import set_tenant_local

        store = PostgresIdempotencyStore()
        scope_key = ScopeKey(
            tenant_id=TENANT_A_ID,
            actor_id="test-actor",
            method="POST",
            operation_id="createDeal",
            idempotency_key="unique-key-456",
        )
        record = IdempotencyRecord(
            payload_sha256="sha256:def456",
            status_code=201,
            media_type="application/json",
            body_bytes=b'{"result": "success"}',
            created_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        )

        with app_engine.begin() as conn:
            set_tenant_local(conn, TENANT_A_ID)
            store.put(scope_key, record, conn=conn)

        with app_engine.begin() as conn:
            set_tenant_local(conn, TENANT_A_ID)
            retrieved = store.get(scope_key, conn=conn)

        assert retrieved is not None
        assert retrieved.payload_sha256 == "sha256:def456"
        assert retrieved.status_code == 201
        assert retrieved.body_bytes == b'{"result": "success"}'

    def test_postgres_idempotency_store_cross_tenant_blocked(
        self, app_engine: Engine, clean_tables: None
    ) -> None:
        """Test that cross-tenant idempotency lookups return None."""
        from idis.idempotency.postgres_store import PostgresIdempotencyStore
        from idis.idempotency.store import IdempotencyRecord, ScopeKey
        from idis.persistence.db import set_tenant_local

        store = PostgresIdempotencyStore()
        scope_key_a = ScopeKey(
            tenant_id=TENANT_A_ID,
            actor_id="test-actor",
            method="POST",
            operation_id="createDeal",
            idempotency_key="cross-tenant-key",
        )
        record = IdempotencyRecord(
            payload_sha256="sha256:cross123",
            status_code=201,
            media_type="application/json",
            body_bytes=b'{"tenant": "A"}',
            created_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        )

        with app_engine.begin() as conn:
            set_tenant_local(conn, TENANT_A_ID)
            store.put(scope_key_a, record, conn=conn)

        scope_key_b = ScopeKey(
            tenant_id=TENANT_B_ID,
            actor_id="test-actor",
            method="POST",
            operation_id="createDeal",
            idempotency_key="cross-tenant-key",
        )

        with app_engine.begin() as conn:
            set_tenant_local(conn, TENANT_B_ID)
            retrieved = store.get(scope_key_b, conn=conn)

        assert retrieved is None, "Cross-tenant lookup should return None due to RLS"


class TestWebhooksRLS:
    """Tests for webhooks table RLS tenant isolation."""

    def test_webhooks_same_tenant_read_allowed(
        self, app_engine: Engine, clean_tables: None
    ) -> None:
        """Insert webhook under tenant A, read under tenant A => 1 row."""
        webhook_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        with app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_A_ID}'"))
            conn.execute(
                text(
                    """
                    INSERT INTO webhooks
                    (webhook_id, tenant_id, url, events, active, created_at, updated_at)
                    VALUES (:webhook_id, :tenant_id, :url, :events,
                            :active, :created_at, :updated_at)
                    """
                ),
                {
                    "webhook_id": webhook_id,
                    "tenant_id": TENANT_A_ID,
                    "url": "https://example.com/webhook",
                    "events": ["deal.created", "deal.updated"],
                    "active": True,
                    "created_at": now,
                    "updated_at": now,
                },
            )

        with app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_A_ID}'"))
            result = conn.execute(
                text("SELECT webhook_id, url FROM webhooks WHERE webhook_id = :webhook_id"),
                {"webhook_id": webhook_id},
            )
            rows = result.fetchall()

        assert len(rows) == 1, "RLS should allow same-tenant webhook reads"
        assert rows[0].url == "https://example.com/webhook"

    def test_webhooks_cross_tenant_read_blocked(
        self, app_engine: Engine, clean_tables: None
    ) -> None:
        """Insert webhook under tenant A, read under tenant B => 0 rows."""
        webhook_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        with app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_A_ID}'"))
            conn.execute(
                text(
                    """
                    INSERT INTO webhooks
                    (webhook_id, tenant_id, url, events, active, created_at, updated_at)
                    VALUES (:webhook_id, :tenant_id, :url, :events,
                            :active, :created_at, :updated_at)
                    """
                ),
                {
                    "webhook_id": webhook_id,
                    "tenant_id": TENANT_A_ID,
                    "url": "https://example.com/webhook",
                    "events": ["deal.created"],
                    "active": True,
                    "created_at": now,
                    "updated_at": now,
                },
            )

        with app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_B_ID}'"))
            result = conn.execute(
                text("SELECT webhook_id FROM webhooks WHERE webhook_id = :webhook_id"),
                {"webhook_id": webhook_id},
            )
            rows = result.fetchall()

        assert len(rows) == 0, "RLS should block cross-tenant webhook reads"

    def test_webhooks_fail_closed_without_tenant_context(
        self, app_engine: Engine, clean_tables: None
    ) -> None:
        """No SET LOCAL => SELECT returns 0 rows (fail closed)."""
        webhook_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        with app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_A_ID}'"))
            conn.execute(
                text(
                    """
                    INSERT INTO webhooks
                    (webhook_id, tenant_id, url, events, active, created_at, updated_at)
                    VALUES (:webhook_id, :tenant_id, :url, :events,
                            :active, :created_at, :updated_at)
                    """
                ),
                {
                    "webhook_id": webhook_id,
                    "tenant_id": TENANT_A_ID,
                    "url": "https://example.com/webhook",
                    "events": ["deal.created"],
                    "active": True,
                    "created_at": now,
                    "updated_at": now,
                },
            )

        with app_engine.begin() as conn:
            result = conn.execute(text("SELECT webhook_id FROM webhooks"))
            rows = result.fetchall()

        assert len(rows) == 0, "RLS should return 0 rows without tenant context"

    def test_webhooks_insert_blocked_without_tenant_context(
        self, app_engine: Engine, clean_tables: None
    ) -> None:
        """INSERT without SET LOCAL should be blocked by RLS WITH CHECK."""
        webhook_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        with pytest.raises(DBAPIError) as exc_info, app_engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO webhooks
                    (webhook_id, tenant_id, url, events, active, created_at, updated_at)
                    VALUES (:webhook_id, :tenant_id, :url, :events,
                            :active, :created_at, :updated_at)
                    """
                ),
                {
                    "webhook_id": webhook_id,
                    "tenant_id": TENANT_A_ID,
                    "url": "https://example.com/webhook",
                    "events": ["deal.created"],
                    "active": True,
                    "created_at": now,
                    "updated_at": now,
                },
            )

        error_msg = str(exc_info.value).lower()
        assert (
            "row-level security" in error_msg
            or "policy" in error_msg
            or "permission denied" in error_msg
        ), f"INSERT without tenant context should be blocked by RLS, got: {exc_info.value}"


class TestWebhookDeliveryAttemptsRLS:
    """Tests for webhook_delivery_attempts table RLS tenant isolation."""

    def _create_webhook(self, conn: object, tenant_id: str, webhook_id: str) -> None:
        """Helper to create a webhook for FK constraint."""

        now = datetime.now(UTC)
        conn.execute(
            text(
                """
                INSERT INTO webhooks
                (webhook_id, tenant_id, url, events, active, created_at, updated_at)
                VALUES (:webhook_id, :tenant_id, :url, :events,
                        :active, :created_at, :updated_at)
                """
            ),
            {
                "webhook_id": webhook_id,
                "tenant_id": tenant_id,
                "url": "https://example.com/webhook",
                "events": ["deal.created"],
                "active": True,
                "created_at": now,
                "updated_at": now,
            },
        )

    def test_delivery_attempts_same_tenant_read_allowed(
        self, app_engine: Engine, clean_tables: None
    ) -> None:
        """Insert attempt under tenant A, read under tenant A => 1 row."""
        webhook_id = str(uuid.uuid4())
        attempt_id = str(uuid.uuid4())
        event_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        with app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_A_ID}'"))
            self._create_webhook(conn, TENANT_A_ID, webhook_id)
            conn.execute(
                text(
                    """
                    INSERT INTO webhook_delivery_attempts
                    (attempt_id, webhook_id, tenant_id, event_id, event_type, payload,
                     attempt_count, status, created_at, updated_at)
                    VALUES (:attempt_id, :webhook_id, :tenant_id, :event_id, :event_type, :payload,
                            :attempt_count, :status, :created_at, :updated_at)
                    """
                ),
                {
                    "attempt_id": attempt_id,
                    "webhook_id": webhook_id,
                    "tenant_id": TENANT_A_ID,
                    "event_id": event_id,
                    "event_type": "deal.created",
                    "payload": json.dumps({"deal_id": "test"}),
                    "attempt_count": 1,
                    "status": "pending",
                    "created_at": now,
                    "updated_at": now,
                },
            )

        with app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_A_ID}'"))
            result = conn.execute(
                text(
                    "SELECT attempt_id, event_type FROM webhook_delivery_attempts "
                    "WHERE attempt_id = :attempt_id"
                ),
                {"attempt_id": attempt_id},
            )
            rows = result.fetchall()

        assert len(rows) == 1, "RLS should allow same-tenant delivery attempt reads"
        assert rows[0].event_type == "deal.created"

    def test_delivery_attempts_cross_tenant_read_blocked(
        self, app_engine: Engine, clean_tables: None
    ) -> None:
        """Insert attempt under tenant A, read under tenant B => 0 rows."""
        webhook_id = str(uuid.uuid4())
        attempt_id = str(uuid.uuid4())
        event_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        with app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_A_ID}'"))
            self._create_webhook(conn, TENANT_A_ID, webhook_id)
            conn.execute(
                text(
                    """
                    INSERT INTO webhook_delivery_attempts
                    (attempt_id, webhook_id, tenant_id, event_id, event_type, payload,
                     attempt_count, status, created_at, updated_at)
                    VALUES (:attempt_id, :webhook_id, :tenant_id, :event_id, :event_type, :payload,
                            :attempt_count, :status, :created_at, :updated_at)
                    """
                ),
                {
                    "attempt_id": attempt_id,
                    "webhook_id": webhook_id,
                    "tenant_id": TENANT_A_ID,
                    "event_id": event_id,
                    "event_type": "deal.created",
                    "payload": json.dumps({"deal_id": "test"}),
                    "attempt_count": 1,
                    "status": "pending",
                    "created_at": now,
                    "updated_at": now,
                },
            )

        with app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_B_ID}'"))
            result = conn.execute(
                text(
                    "SELECT attempt_id FROM webhook_delivery_attempts "
                    "WHERE attempt_id = :attempt_id"
                ),
                {"attempt_id": attempt_id},
            )
            rows = result.fetchall()

        assert len(rows) == 0, "RLS should block cross-tenant delivery attempt reads"

    def test_delivery_attempts_fail_closed_without_tenant_context(
        self, app_engine: Engine, clean_tables: None
    ) -> None:
        """No SET LOCAL => SELECT returns 0 rows (fail closed)."""
        webhook_id = str(uuid.uuid4())
        attempt_id = str(uuid.uuid4())
        event_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        with app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_A_ID}'"))
            self._create_webhook(conn, TENANT_A_ID, webhook_id)
            conn.execute(
                text(
                    """
                    INSERT INTO webhook_delivery_attempts
                    (attempt_id, webhook_id, tenant_id, event_id, event_type, payload,
                     attempt_count, status, created_at, updated_at)
                    VALUES (:attempt_id, :webhook_id, :tenant_id, :event_id, :event_type, :payload,
                            :attempt_count, :status, :created_at, :updated_at)
                    """
                ),
                {
                    "attempt_id": attempt_id,
                    "webhook_id": webhook_id,
                    "tenant_id": TENANT_A_ID,
                    "event_id": event_id,
                    "event_type": "deal.created",
                    "payload": json.dumps({"deal_id": "test"}),
                    "attempt_count": 1,
                    "status": "pending",
                    "created_at": now,
                    "updated_at": now,
                },
            )

        with app_engine.begin() as conn:
            result = conn.execute(text("SELECT attempt_id FROM webhook_delivery_attempts"))
            rows = result.fetchall()

        assert len(rows) == 0, "RLS should return 0 rows without tenant context"

    def test_delivery_attempts_insert_blocked_without_tenant_context(
        self, app_engine: Engine, admin_engine: Engine, clean_tables: None
    ) -> None:
        """INSERT without SET LOCAL should be blocked by RLS WITH CHECK."""
        webhook_id = str(uuid.uuid4())
        attempt_id = str(uuid.uuid4())
        event_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        with admin_engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO webhooks
                    (webhook_id, tenant_id, url, events, active, created_at, updated_at)
                    VALUES (:webhook_id, :tenant_id, :url, :events,
                            :active, :created_at, :updated_at)
                    """
                ),
                {
                    "webhook_id": webhook_id,
                    "tenant_id": TENANT_A_ID,
                    "url": "https://example.com/webhook",
                    "events": ["deal.created"],
                    "active": True,
                    "created_at": now,
                    "updated_at": now,
                },
            )

        with pytest.raises(DBAPIError) as exc_info, app_engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO webhook_delivery_attempts
                    (attempt_id, webhook_id, tenant_id, event_id, event_type, payload,
                     attempt_count, status, created_at, updated_at)
                    VALUES (:attempt_id, :webhook_id, :tenant_id, :event_id, :event_type, :payload,
                            :attempt_count, :status, :created_at, :updated_at)
                    """
                ),
                {
                    "attempt_id": attempt_id,
                    "webhook_id": webhook_id,
                    "tenant_id": TENANT_A_ID,
                    "event_id": event_id,
                    "event_type": "deal.created",
                    "payload": json.dumps({"deal_id": "test"}),
                    "attempt_count": 1,
                    "status": "pending",
                    "created_at": now,
                    "updated_at": now,
                },
            )

        error_msg = str(exc_info.value).lower()
        assert (
            "row-level security" in error_msg
            or "policy" in error_msg
            or "permission denied" in error_msg
        ), f"INSERT without tenant context should be blocked by RLS, got: {exc_info.value}"

    def test_delivery_attempts_rls_blocks_mismatched_tenant_insert(
        self, app_engine: Engine, clean_tables: None
    ) -> None:
        """INSERT with tenant context A but tenant_id=B must be blocked by WITH CHECK."""
        webhook_id = str(uuid.uuid4())
        attempt_id = str(uuid.uuid4())
        event_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        # Create webhook under Tenant A context
        with app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_A_ID}'"))
            conn.execute(
                text(
                    """
                    INSERT INTO webhooks
                    (webhook_id, tenant_id, url, events, active, created_at, updated_at)
                    VALUES (:webhook_id, :tenant_id, :url, :events,
                            :active, :created_at, :updated_at)
                    """
                ),
                {
                    "webhook_id": webhook_id,
                    "tenant_id": TENANT_A_ID,
                    "url": "https://example.com/webhook",
                    "events": ["deal.created"],
                    "active": True,
                    "created_at": now,
                    "updated_at": now,
                },
            )

        # Attempt to insert delivery_attempt with tenant context A but tenant_id = B
        # This must be blocked by RLS WITH CHECK
        with pytest.raises(DBAPIError) as exc_info, app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_A_ID}'"))
            conn.execute(
                text(
                    """
                    INSERT INTO webhook_delivery_attempts
                    (attempt_id, webhook_id, tenant_id, event_id, event_type, payload,
                     attempt_count, status, created_at, updated_at)
                    VALUES (:attempt_id, :webhook_id, :tenant_id, :event_id, :event_type,
                            :payload, :attempt_count, :status, :created_at, :updated_at)
                    """
                ),
                {
                    "attempt_id": attempt_id,
                    "webhook_id": webhook_id,
                    "tenant_id": TENANT_B_ID,  # Mismatched: context=A, value=B
                    "event_id": event_id,
                    "event_type": "deal.created",
                    "payload": json.dumps({"deal_id": "test"}),
                    "attempt_count": 1,
                    "status": "pending",
                    "created_at": now,
                    "updated_at": now,
                },
            )

        error_msg = str(exc_info.value).lower()
        # Must be RLS violation, not a UUID cast error
        assert "uuid" not in error_msg or "row-level" in error_msg, (
            f"Error should be RLS violation, not UUID cast error: {exc_info.value}"
        )
        assert (
            "row-level security" in error_msg
            or "policy" in error_msg
            or "permission denied" in error_msg
        ), f"Mismatched tenant INSERT should be blocked by RLS WITH CHECK: {exc_info.value}"


class TestDocumentArtifactsRLS:
    """Tests for document_artifacts table RLS tenant isolation (Phase 3.1)."""

    def _create_deal(self, conn: object, tenant_id: str, deal_id: str) -> None:
        """Helper to create a deal for FK constraint."""
        now = datetime.now(UTC)
        conn.execute(
            text(
                """
                INSERT INTO deals (deal_id, tenant_id, name, created_at)
                VALUES (:deal_id, :tenant_id, :name, :created_at)
                """
            ),
            {
                "deal_id": deal_id,
                "tenant_id": tenant_id,
                "name": "Test Deal",
                "created_at": now,
            },
        )

    def test_document_artifacts_same_tenant_read_allowed(
        self, app_engine: Engine, clean_tables: None
    ) -> None:
        """Insert artifact under tenant A, read under tenant A => 1 row."""
        deal_id = str(uuid.uuid4())
        doc_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        with app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_A_ID}'"))
            self._create_deal(conn, TENANT_A_ID, deal_id)
            conn.execute(
                text(
                    """
                    INSERT INTO document_artifacts
                    (doc_id, tenant_id, deal_id, doc_type, title, source_system,
                     version_id, created_at, updated_at)
                    VALUES (:doc_id, :tenant_id, :deal_id, :doc_type, :title, :source_system,
                            :version_id, :created_at, :updated_at)
                    """
                ),
                {
                    "doc_id": doc_id,
                    "tenant_id": TENANT_A_ID,
                    "deal_id": deal_id,
                    "doc_type": "PITCH_DECK",
                    "title": "Test Document",
                    "source_system": "DocSend",
                    "version_id": "v1",
                    "created_at": now,
                    "updated_at": now,
                },
            )

        with app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_A_ID}'"))
            result = conn.execute(
                text("SELECT doc_id, doc_type FROM document_artifacts WHERE doc_id = :doc_id"),
                {"doc_id": doc_id},
            )
            rows = result.fetchall()

        assert len(rows) == 1, "RLS should allow same-tenant artifact reads"
        assert rows[0].doc_type == "PITCH_DECK"

    def test_document_artifacts_cross_tenant_read_blocked(
        self, app_engine: Engine, clean_tables: None
    ) -> None:
        """Insert artifact under tenant A, read under tenant B => 0 rows."""
        deal_id = str(uuid.uuid4())
        doc_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        with app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_A_ID}'"))
            self._create_deal(conn, TENANT_A_ID, deal_id)
            conn.execute(
                text(
                    """
                    INSERT INTO document_artifacts
                    (doc_id, tenant_id, deal_id, doc_type, title, source_system,
                     version_id, created_at, updated_at)
                    VALUES (:doc_id, :tenant_id, :deal_id, :doc_type, :title, :source_system,
                            :version_id, :created_at, :updated_at)
                    """
                ),
                {
                    "doc_id": doc_id,
                    "tenant_id": TENANT_A_ID,
                    "deal_id": deal_id,
                    "doc_type": "PITCH_DECK",
                    "title": "Test Document",
                    "source_system": "DocSend",
                    "version_id": "v1",
                    "created_at": now,
                    "updated_at": now,
                },
            )

        with app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_B_ID}'"))
            result = conn.execute(
                text("SELECT doc_id FROM document_artifacts WHERE doc_id = :doc_id"),
                {"doc_id": doc_id},
            )
            rows = result.fetchall()

        assert len(rows) == 0, "RLS should block cross-tenant artifact reads"

    def test_document_artifacts_fail_closed_without_tenant_context(
        self, app_engine: Engine, clean_tables: None
    ) -> None:
        """No SET LOCAL => SELECT returns 0 rows (fail closed)."""
        deal_id = str(uuid.uuid4())
        doc_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        with app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_A_ID}'"))
            self._create_deal(conn, TENANT_A_ID, deal_id)
            conn.execute(
                text(
                    """
                    INSERT INTO document_artifacts
                    (doc_id, tenant_id, deal_id, doc_type, title, source_system,
                     version_id, created_at, updated_at)
                    VALUES (:doc_id, :tenant_id, :deal_id, :doc_type, :title, :source_system,
                            :version_id, :created_at, :updated_at)
                    """
                ),
                {
                    "doc_id": doc_id,
                    "tenant_id": TENANT_A_ID,
                    "deal_id": deal_id,
                    "doc_type": "PITCH_DECK",
                    "title": "Test Document",
                    "source_system": "DocSend",
                    "version_id": "v1",
                    "created_at": now,
                    "updated_at": now,
                },
            )

        with app_engine.begin() as conn:
            result = conn.execute(text("SELECT doc_id FROM document_artifacts"))
            rows = result.fetchall()

        assert len(rows) == 0, "RLS should return 0 rows without tenant context"

    def test_document_artifacts_insert_blocked_without_tenant_context(
        self, app_engine: Engine, admin_engine: Engine, clean_tables: None
    ) -> None:
        """INSERT without SET LOCAL should be blocked by RLS WITH CHECK."""
        deal_id = str(uuid.uuid4())
        doc_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        with admin_engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO deals (deal_id, tenant_id, name, created_at)
                    VALUES (:deal_id, :tenant_id, :name, :created_at)
                    """
                ),
                {
                    "deal_id": deal_id,
                    "tenant_id": TENANT_A_ID,
                    "name": "Test Deal",
                    "created_at": now,
                },
            )

        with pytest.raises(DBAPIError) as exc_info, app_engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO document_artifacts
                    (doc_id, tenant_id, deal_id, doc_type, title, source_system,
                     version_id, created_at, updated_at)
                    VALUES (:doc_id, :tenant_id, :deal_id, :doc_type, :title, :source_system,
                            :version_id, :created_at, :updated_at)
                    """
                ),
                {
                    "doc_id": doc_id,
                    "tenant_id": TENANT_A_ID,
                    "deal_id": deal_id,
                    "doc_type": "PITCH_DECK",
                    "title": "Test Document",
                    "source_system": "DocSend",
                    "version_id": "v1",
                    "created_at": now,
                    "updated_at": now,
                },
            )

        error_msg = str(exc_info.value).lower()
        assert (
            "row-level security" in error_msg
            or "policy" in error_msg
            or "permission denied" in error_msg
        ), f"INSERT without tenant context should be blocked by RLS, got: {exc_info.value}"

    def test_document_artifacts_rls_blocks_mismatched_tenant_insert(
        self, app_engine: Engine, clean_tables: None
    ) -> None:
        """INSERT with tenant context A but tenant_id=B must be blocked by WITH CHECK."""
        deal_id = str(uuid.uuid4())
        doc_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        with app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_A_ID}'"))
            self._create_deal(conn, TENANT_A_ID, deal_id)

        with pytest.raises(DBAPIError) as exc_info, app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_A_ID}'"))
            conn.execute(
                text(
                    """
                    INSERT INTO document_artifacts
                    (doc_id, tenant_id, deal_id, doc_type, title, source_system,
                     version_id, created_at, updated_at)
                    VALUES (:doc_id, :tenant_id, :deal_id, :doc_type, :title, :source_system,
                            :version_id, :created_at, :updated_at)
                    """
                ),
                {
                    "doc_id": doc_id,
                    "tenant_id": TENANT_B_ID,  # Mismatched: context=A, value=B
                    "deal_id": deal_id,
                    "doc_type": "PITCH_DECK",
                    "title": "Test Document",
                    "source_system": "DocSend",
                    "version_id": "v1",
                    "created_at": now,
                    "updated_at": now,
                },
            )

        error_msg = str(exc_info.value).lower()
        assert (
            "row-level security" in error_msg
            or "policy" in error_msg
            or "permission denied" in error_msg
        ), f"Mismatched tenant INSERT should be blocked by RLS WITH CHECK: {exc_info.value}"


class TestDocumentsRLS:
    """Tests for documents table RLS tenant isolation (Phase 3.1)."""

    def _create_deal_and_artifact(
        self, conn: object, tenant_id: str, deal_id: str, doc_id: str
    ) -> None:
        """Helper to create deal and document_artifact for FK constraints."""
        now = datetime.now(UTC)
        conn.execute(
            text(
                """
                INSERT INTO deals (deal_id, tenant_id, name, created_at)
                VALUES (:deal_id, :tenant_id, :name, :created_at)
                """
            ),
            {
                "deal_id": deal_id,
                "tenant_id": tenant_id,
                "name": "Test Deal",
                "created_at": now,
            },
        )
        conn.execute(
            text(
                """
                INSERT INTO document_artifacts
                (doc_id, tenant_id, deal_id, doc_type, title, source_system,
                 version_id, created_at, updated_at)
                VALUES (:doc_id, :tenant_id, :deal_id, :doc_type, :title, :source_system,
                        :version_id, :created_at, :updated_at)
                """
            ),
            {
                "doc_id": doc_id,
                "tenant_id": tenant_id,
                "deal_id": deal_id,
                "doc_type": "PITCH_DECK",
                "title": "Test Document",
                "source_system": "DocSend",
                "version_id": "v1",
                "created_at": now,
                "updated_at": now,
            },
        )

    def test_documents_same_tenant_read_allowed(
        self, app_engine: Engine, clean_tables: None
    ) -> None:
        """Insert document under tenant A, read under tenant A => 1 row."""
        deal_id = str(uuid.uuid4())
        doc_id = str(uuid.uuid4())
        document_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        with app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_A_ID}'"))
            self._create_deal_and_artifact(conn, TENANT_A_ID, deal_id, doc_id)
            conn.execute(
                text(
                    """
                    INSERT INTO documents
                    (document_id, tenant_id, deal_id, doc_id, doc_type,
                     parse_status, created_at, updated_at)
                    VALUES (:document_id, :tenant_id, :deal_id, :doc_id, :doc_type,
                            :parse_status, :created_at, :updated_at)
                    """
                ),
                {
                    "document_id": document_id,
                    "tenant_id": TENANT_A_ID,
                    "deal_id": deal_id,
                    "doc_id": doc_id,
                    "doc_type": "PDF",
                    "parse_status": "PENDING",
                    "created_at": now,
                    "updated_at": now,
                },
            )

        with app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_A_ID}'"))
            result = conn.execute(
                text(
                    "SELECT document_id, doc_type FROM documents WHERE document_id = :document_id"
                ),
                {"document_id": document_id},
            )
            rows = result.fetchall()

        assert len(rows) == 1, "RLS should allow same-tenant document reads"
        assert rows[0].doc_type == "PDF"

    def test_documents_cross_tenant_read_blocked(
        self, app_engine: Engine, clean_tables: None
    ) -> None:
        """Insert document under tenant A, read under tenant B => 0 rows."""
        deal_id = str(uuid.uuid4())
        doc_id = str(uuid.uuid4())
        document_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        with app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_A_ID}'"))
            self._create_deal_and_artifact(conn, TENANT_A_ID, deal_id, doc_id)
            conn.execute(
                text(
                    """
                    INSERT INTO documents
                    (document_id, tenant_id, deal_id, doc_id, doc_type,
                     parse_status, created_at, updated_at)
                    VALUES (:document_id, :tenant_id, :deal_id, :doc_id, :doc_type,
                            :parse_status, :created_at, :updated_at)
                    """
                ),
                {
                    "document_id": document_id,
                    "tenant_id": TENANT_A_ID,
                    "deal_id": deal_id,
                    "doc_id": doc_id,
                    "doc_type": "PDF",
                    "parse_status": "PENDING",
                    "created_at": now,
                    "updated_at": now,
                },
            )

        with app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_B_ID}'"))
            result = conn.execute(
                text("SELECT document_id FROM documents WHERE document_id = :document_id"),
                {"document_id": document_id},
            )
            rows = result.fetchall()

        assert len(rows) == 0, "RLS should block cross-tenant document reads"

    def test_documents_fail_closed_without_tenant_context(
        self, app_engine: Engine, clean_tables: None
    ) -> None:
        """No SET LOCAL => SELECT returns 0 rows (fail closed)."""
        deal_id = str(uuid.uuid4())
        doc_id = str(uuid.uuid4())
        document_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        with app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_A_ID}'"))
            self._create_deal_and_artifact(conn, TENANT_A_ID, deal_id, doc_id)
            conn.execute(
                text(
                    """
                    INSERT INTO documents
                    (document_id, tenant_id, deal_id, doc_id, doc_type,
                     parse_status, created_at, updated_at)
                    VALUES (:document_id, :tenant_id, :deal_id, :doc_id, :doc_type,
                            :parse_status, :created_at, :updated_at)
                    """
                ),
                {
                    "document_id": document_id,
                    "tenant_id": TENANT_A_ID,
                    "deal_id": deal_id,
                    "doc_id": doc_id,
                    "doc_type": "PDF",
                    "parse_status": "PENDING",
                    "created_at": now,
                    "updated_at": now,
                },
            )

        with app_engine.begin() as conn:
            result = conn.execute(text("SELECT document_id FROM documents"))
            rows = result.fetchall()

        assert len(rows) == 0, "RLS should return 0 rows without tenant context"

    def test_documents_insert_blocked_without_tenant_context(
        self, app_engine: Engine, admin_engine: Engine, clean_tables: None
    ) -> None:
        """INSERT without SET LOCAL should be blocked by RLS WITH CHECK."""
        deal_id = str(uuid.uuid4())
        doc_id = str(uuid.uuid4())
        document_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        with admin_engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO deals (deal_id, tenant_id, name, created_at)
                    VALUES (:deal_id, :tenant_id, :name, :created_at)
                    """
                ),
                {
                    "deal_id": deal_id,
                    "tenant_id": TENANT_A_ID,
                    "name": "Test Deal",
                    "created_at": now,
                },
            )
            conn.execute(
                text(
                    """
                    INSERT INTO document_artifacts
                    (doc_id, tenant_id, deal_id, doc_type, title, source_system,
                     version_id, created_at, updated_at)
                    VALUES (:doc_id, :tenant_id, :deal_id, :doc_type, :title, :source_system,
                            :version_id, :created_at, :updated_at)
                    """
                ),
                {
                    "doc_id": doc_id,
                    "tenant_id": TENANT_A_ID,
                    "deal_id": deal_id,
                    "doc_type": "PITCH_DECK",
                    "title": "Test Document",
                    "source_system": "DocSend",
                    "version_id": "v1",
                    "created_at": now,
                    "updated_at": now,
                },
            )

        with pytest.raises(DBAPIError) as exc_info, app_engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO documents
                    (document_id, tenant_id, deal_id, doc_id, doc_type,
                     parse_status, created_at, updated_at)
                    VALUES (:document_id, :tenant_id, :deal_id, :doc_id, :doc_type,
                            :parse_status, :created_at, :updated_at)
                    """
                ),
                {
                    "document_id": document_id,
                    "tenant_id": TENANT_A_ID,
                    "deal_id": deal_id,
                    "doc_id": doc_id,
                    "doc_type": "PDF",
                    "parse_status": "PENDING",
                    "created_at": now,
                    "updated_at": now,
                },
            )

        error_msg = str(exc_info.value).lower()
        assert (
            "row-level security" in error_msg
            or "policy" in error_msg
            or "permission denied" in error_msg
        ), f"INSERT without tenant context should be blocked by RLS, got: {exc_info.value}"

    def test_documents_rls_blocks_mismatched_tenant_insert(
        self, app_engine: Engine, clean_tables: None
    ) -> None:
        """INSERT with tenant context A but tenant_id=B must be blocked by WITH CHECK."""
        deal_id = str(uuid.uuid4())
        doc_id = str(uuid.uuid4())
        document_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        with app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_A_ID}'"))
            self._create_deal_and_artifact(conn, TENANT_A_ID, deal_id, doc_id)

        with pytest.raises(DBAPIError) as exc_info, app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_A_ID}'"))
            conn.execute(
                text(
                    """
                    INSERT INTO documents
                    (document_id, tenant_id, deal_id, doc_id, doc_type,
                     parse_status, created_at, updated_at)
                    VALUES (:document_id, :tenant_id, :deal_id, :doc_id, :doc_type,
                            :parse_status, :created_at, :updated_at)
                    """
                ),
                {
                    "document_id": document_id,
                    "tenant_id": TENANT_B_ID,  # Mismatched: context=A, value=B
                    "deal_id": deal_id,
                    "doc_id": doc_id,
                    "doc_type": "PDF",
                    "parse_status": "PENDING",
                    "created_at": now,
                    "updated_at": now,
                },
            )

        error_msg = str(exc_info.value).lower()
        assert (
            "row-level security" in error_msg
            or "policy" in error_msg
            or "permission denied" in error_msg
        ), f"Mismatched tenant INSERT should be blocked by RLS WITH CHECK: {exc_info.value}"


class TestDocumentSpansRLS:
    """Tests for document_spans table RLS tenant isolation (Phase 3.1)."""

    def _create_full_hierarchy(
        self,
        conn: object,
        tenant_id: str,
        deal_id: str,
        doc_id: str,
        document_id: str,
    ) -> None:
        """Helper to create deal, document_artifact, document for FK constraints."""
        now = datetime.now(UTC)
        conn.execute(
            text(
                """
                INSERT INTO deals (deal_id, tenant_id, name, created_at)
                VALUES (:deal_id, :tenant_id, :name, :created_at)
                """
            ),
            {
                "deal_id": deal_id,
                "tenant_id": tenant_id,
                "name": "Test Deal",
                "created_at": now,
            },
        )
        conn.execute(
            text(
                """
                INSERT INTO document_artifacts
                (doc_id, tenant_id, deal_id, doc_type, title, source_system,
                 version_id, created_at, updated_at)
                VALUES (:doc_id, :tenant_id, :deal_id, :doc_type, :title, :source_system,
                        :version_id, :created_at, :updated_at)
                """
            ),
            {
                "doc_id": doc_id,
                "tenant_id": tenant_id,
                "deal_id": deal_id,
                "doc_type": "PITCH_DECK",
                "title": "Test Document",
                "source_system": "DocSend",
                "version_id": "v1",
                "created_at": now,
                "updated_at": now,
            },
        )
        conn.execute(
            text(
                """
                INSERT INTO documents
                (document_id, tenant_id, deal_id, doc_id, doc_type,
                 parse_status, created_at, updated_at)
                VALUES (:document_id, :tenant_id, :deal_id, :doc_id, :doc_type,
                        :parse_status, :created_at, :updated_at)
                """
            ),
            {
                "document_id": document_id,
                "tenant_id": tenant_id,
                "deal_id": deal_id,
                "doc_id": doc_id,
                "doc_type": "PDF",
                "parse_status": "PARSED",
                "created_at": now,
                "updated_at": now,
            },
        )

    def test_document_spans_same_tenant_read_allowed(
        self, app_engine: Engine, clean_tables: None
    ) -> None:
        """Insert span under tenant A, read under tenant A => 1 row."""
        deal_id = str(uuid.uuid4())
        doc_id = str(uuid.uuid4())
        document_id = str(uuid.uuid4())
        span_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        with app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_A_ID}'"))
            self._create_full_hierarchy(conn, TENANT_A_ID, deal_id, doc_id, document_id)
            conn.execute(
                text(
                    """
                    INSERT INTO document_spans
                    (span_id, tenant_id, document_id, span_type, locator,
                     text_excerpt, created_at, updated_at)
                    VALUES (:span_id, :tenant_id, :document_id, :span_type, :locator,
                            :text_excerpt, :created_at, :updated_at)
                    """
                ),
                {
                    "span_id": span_id,
                    "tenant_id": TENANT_A_ID,
                    "document_id": document_id,
                    "span_type": "PAGE_TEXT",
                    "locator": json.dumps({"page": 1}),
                    "text_excerpt": "Sample text",
                    "created_at": now,
                    "updated_at": now,
                },
            )

        with app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_A_ID}'"))
            result = conn.execute(
                text("SELECT span_id, span_type FROM document_spans WHERE span_id = :span_id"),
                {"span_id": span_id},
            )
            rows = result.fetchall()

        assert len(rows) == 1, "RLS should allow same-tenant span reads"
        assert rows[0].span_type == "PAGE_TEXT"

    def test_document_spans_cross_tenant_read_blocked(
        self, app_engine: Engine, clean_tables: None
    ) -> None:
        """Insert span under tenant A, read under tenant B => 0 rows."""
        deal_id = str(uuid.uuid4())
        doc_id = str(uuid.uuid4())
        document_id = str(uuid.uuid4())
        span_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        with app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_A_ID}'"))
            self._create_full_hierarchy(conn, TENANT_A_ID, deal_id, doc_id, document_id)
            conn.execute(
                text(
                    """
                    INSERT INTO document_spans
                    (span_id, tenant_id, document_id, span_type, locator,
                     text_excerpt, created_at, updated_at)
                    VALUES (:span_id, :tenant_id, :document_id, :span_type, :locator,
                            :text_excerpt, :created_at, :updated_at)
                    """
                ),
                {
                    "span_id": span_id,
                    "tenant_id": TENANT_A_ID,
                    "document_id": document_id,
                    "span_type": "PAGE_TEXT",
                    "locator": json.dumps({"page": 1}),
                    "text_excerpt": "Sample text",
                    "created_at": now,
                    "updated_at": now,
                },
            )

        with app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_B_ID}'"))
            result = conn.execute(
                text("SELECT span_id FROM document_spans WHERE span_id = :span_id"),
                {"span_id": span_id},
            )
            rows = result.fetchall()

        assert len(rows) == 0, "RLS should block cross-tenant span reads"

    def test_document_spans_fail_closed_without_tenant_context(
        self, app_engine: Engine, clean_tables: None
    ) -> None:
        """No SET LOCAL => SELECT returns 0 rows (fail closed)."""
        deal_id = str(uuid.uuid4())
        doc_id = str(uuid.uuid4())
        document_id = str(uuid.uuid4())
        span_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        with app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_A_ID}'"))
            self._create_full_hierarchy(conn, TENANT_A_ID, deal_id, doc_id, document_id)
            conn.execute(
                text(
                    """
                    INSERT INTO document_spans
                    (span_id, tenant_id, document_id, span_type, locator,
                     text_excerpt, created_at, updated_at)
                    VALUES (:span_id, :tenant_id, :document_id, :span_type, :locator,
                            :text_excerpt, :created_at, :updated_at)
                    """
                ),
                {
                    "span_id": span_id,
                    "tenant_id": TENANT_A_ID,
                    "document_id": document_id,
                    "span_type": "PAGE_TEXT",
                    "locator": json.dumps({"page": 1}),
                    "text_excerpt": "Sample text",
                    "created_at": now,
                    "updated_at": now,
                },
            )

        with app_engine.begin() as conn:
            result = conn.execute(text("SELECT span_id FROM document_spans"))
            rows = result.fetchall()

        assert len(rows) == 0, "RLS should return 0 rows without tenant context"

    def test_document_spans_insert_blocked_without_tenant_context(
        self, app_engine: Engine, admin_engine: Engine, clean_tables: None
    ) -> None:
        """INSERT without SET LOCAL should be blocked by RLS WITH CHECK."""
        deal_id = str(uuid.uuid4())
        doc_id = str(uuid.uuid4())
        document_id = str(uuid.uuid4())
        span_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        with admin_engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO deals (deal_id, tenant_id, name, created_at)
                    VALUES (:deal_id, :tenant_id, :name, :created_at)
                    """
                ),
                {
                    "deal_id": deal_id,
                    "tenant_id": TENANT_A_ID,
                    "name": "Test Deal",
                    "created_at": now,
                },
            )
            conn.execute(
                text(
                    """
                    INSERT INTO document_artifacts
                    (doc_id, tenant_id, deal_id, doc_type, title, source_system,
                     version_id, created_at, updated_at)
                    VALUES (:doc_id, :tenant_id, :deal_id, :doc_type, :title, :source_system,
                            :version_id, :created_at, :updated_at)
                    """
                ),
                {
                    "doc_id": doc_id,
                    "tenant_id": TENANT_A_ID,
                    "deal_id": deal_id,
                    "doc_type": "PITCH_DECK",
                    "title": "Test Document",
                    "source_system": "DocSend",
                    "version_id": "v1",
                    "created_at": now,
                    "updated_at": now,
                },
            )
            conn.execute(
                text(
                    """
                    INSERT INTO documents
                    (document_id, tenant_id, deal_id, doc_id, doc_type,
                     parse_status, created_at, updated_at)
                    VALUES (:document_id, :tenant_id, :deal_id, :doc_id, :doc_type,
                            :parse_status, :created_at, :updated_at)
                    """
                ),
                {
                    "document_id": document_id,
                    "tenant_id": TENANT_A_ID,
                    "deal_id": deal_id,
                    "doc_id": doc_id,
                    "doc_type": "PDF",
                    "parse_status": "PARSED",
                    "created_at": now,
                    "updated_at": now,
                },
            )

        with pytest.raises(DBAPIError) as exc_info, app_engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO document_spans
                    (span_id, tenant_id, document_id, span_type, locator,
                     text_excerpt, created_at, updated_at)
                    VALUES (:span_id, :tenant_id, :document_id, :span_type, :locator,
                            :text_excerpt, :created_at, :updated_at)
                    """
                ),
                {
                    "span_id": span_id,
                    "tenant_id": TENANT_A_ID,
                    "document_id": document_id,
                    "span_type": "PAGE_TEXT",
                    "locator": json.dumps({"page": 1}),
                    "text_excerpt": "Sample text",
                    "created_at": now,
                    "updated_at": now,
                },
            )

        error_msg = str(exc_info.value).lower()
        assert (
            "row-level security" in error_msg
            or "policy" in error_msg
            or "permission denied" in error_msg
        ), f"INSERT without tenant context should be blocked by RLS, got: {exc_info.value}"

    def test_document_spans_rls_blocks_mismatched_tenant_insert(
        self, app_engine: Engine, clean_tables: None
    ) -> None:
        """INSERT with tenant context A but tenant_id=B must be blocked by WITH CHECK."""
        deal_id = str(uuid.uuid4())
        doc_id = str(uuid.uuid4())
        document_id = str(uuid.uuid4())
        span_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        with app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_A_ID}'"))
            self._create_full_hierarchy(conn, TENANT_A_ID, deal_id, doc_id, document_id)

        with pytest.raises(DBAPIError) as exc_info, app_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL idis.tenant_id = '{TENANT_A_ID}'"))
            conn.execute(
                text(
                    """
                    INSERT INTO document_spans
                    (span_id, tenant_id, document_id, span_type, locator,
                     text_excerpt, created_at, updated_at)
                    VALUES (:span_id, :tenant_id, :document_id, :span_type, :locator,
                            :text_excerpt, :created_at, :updated_at)
                    """
                ),
                {
                    "span_id": span_id,
                    "tenant_id": TENANT_B_ID,  # Mismatched: context=A, value=B
                    "document_id": document_id,
                    "span_type": "PAGE_TEXT",
                    "locator": json.dumps({"page": 1}),
                    "text_excerpt": "Sample text",
                    "created_at": now,
                    "updated_at": now,
                },
            )

        error_msg = str(exc_info.value).lower()
        assert (
            "row-level security" in error_msg
            or "policy" in error_msg
            or "permission denied" in error_msg
        ), f"Mismatched tenant INSERT should be blocked by RLS WITH CHECK: {exc_info.value}"
