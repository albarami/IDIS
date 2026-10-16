from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from ._postgres_rls_fixtures import TENANT_A_ID, TENANT_B_ID

if TYPE_CHECKING:
    from sqlalchemy import Engine


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
