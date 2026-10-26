from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from ._postgres_rls_fixtures import TENANT_A_ID, TENANT_B_ID

if TYPE_CHECKING:
    from sqlalchemy import Connection, Engine


class TestWebhookDeliveryAttemptsRLS:
    """Tests for webhook_delivery_attempts table RLS tenant isolation."""

    def _create_webhook(self, conn: Connection, tenant_id: str, webhook_id: str) -> None:
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
