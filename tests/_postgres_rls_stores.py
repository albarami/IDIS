from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from ._postgres_rls_fixtures import TENANT_A_ID, TENANT_B_ID

if TYPE_CHECKING:
    from sqlalchemy import Engine


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
            # The durable sink now rejects non-taxonomy fixtures before SQL execution.
            "event_type": "integration.test_sink.event",
            "actor": {"actor_type": "SERVICE", "actor_id": "test"},
            "request": {
                "request_id": "req-123",
                "method": "POST",
                "path": "/test",
                "status_code": 200,
            },
            "resource": {"resource_type": "integration", "resource_id": str(uuid.uuid4())},
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
        assert rows[0].event_type == "integration.test_sink.event"


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
