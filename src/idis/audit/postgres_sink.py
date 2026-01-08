"""PostgreSQL audit event sink for IDIS.

Provides append-only audit event storage in PostgreSQL with RLS tenant isolation.
Supports both standalone emission and in-transaction emission for atomicity.

Design Requirements (v6.3):
    - Append-only: INSERT only, no UPDATE/DELETE (enforced by DB trigger)
    - RLS enforced: tenant_id must match current_setting('idis.tenant_id')
    - Fail closed: any DB error raises AuditSinkError
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from idis.audit.sink import AuditSinkError

if TYPE_CHECKING:
    from sqlalchemy import Connection

logger = logging.getLogger(__name__)


class PostgresAuditSink:
    """PostgreSQL audit sink with RLS tenant isolation.

    Stores audit events in the audit_events table. Supports two modes:
    1. Standalone: Creates its own connection per emit() call
    2. In-transaction: Uses provided connection for atomic operations

    The sink relies on RLS policies to enforce tenant isolation.
    Tenant context must be set on the connection before calling emit_in_tx().
    """

    _INSERT_SQL = text(
        """
        INSERT INTO audit_events
        (event_id, tenant_id, occurred_at, event_type, request_id, idempotency_key, event)
        VALUES
        (:event_id, :tenant_id, :occurred_at, :event_type, :request_id, :idempotency_key, :event)
        """
    )

    def __init__(self) -> None:
        """Initialize the PostgreSQL audit sink."""
        pass

    def emit(self, event: dict[str, Any]) -> None:
        """Emit an audit event using a new connection.

        Creates a new connection, sets tenant context, and inserts the event.
        This method is for standalone emission outside of request transactions.

        Args:
            event: Validated audit event dict with required fields.

        Raises:
            AuditSinkError: If emission fails for any reason.
        """
        from idis.persistence.db import begin_app_conn, set_tenant_local

        tenant_id = event.get("tenant_id")
        if not tenant_id or tenant_id == "unknown":
            raise AuditSinkError("Cannot emit audit event without valid tenant_id")

        try:
            with begin_app_conn() as conn:
                set_tenant_local(conn, tenant_id)
                self._insert_event(conn, event)
        except SQLAlchemyError as e:
            raise AuditSinkError(f"Failed to emit audit event: {e}") from e

    def emit_in_tx(self, conn: Connection, event: dict[str, Any]) -> None:
        """Emit an audit event within an existing transaction.

        Uses the provided connection which should already have tenant context set.
        This allows audit events to be atomic with other database operations.

        Args:
            conn: SQLAlchemy connection with tenant context already set.
            event: Validated audit event dict with required fields.

        Raises:
            AuditSinkError: If emission fails for any reason.
        """
        try:
            self._insert_event(conn, event)
        except SQLAlchemyError as e:
            raise AuditSinkError(f"Failed to emit audit event in transaction: {e}") from e

    def _insert_event(self, conn: Connection, event: dict[str, Any]) -> None:
        """Insert audit event into database.

        Args:
            conn: SQLAlchemy connection to use.
            event: Audit event dict.

        Raises:
            SQLAlchemyError: If INSERT fails.
            AuditSinkError: If required fields are missing.
        """
        event_id = event.get("event_id")
        tenant_id = event.get("tenant_id")
        occurred_at = event.get("occurred_at")
        event_type = event.get("event_type")

        if not all([event_id, tenant_id, occurred_at, event_type]):
            raise AuditSinkError(
                "Audit event missing required fields: event_id, tenant_id, occurred_at, event_type"
            )

        request_info = event.get("request", {})
        request_id = request_info.get("request_id")
        idempotency_key = request_info.get("idempotency_key")

        occurred_at_dt: datetime
        if isinstance(occurred_at, str):
            occurred_at_dt = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
        elif isinstance(occurred_at, datetime):
            occurred_at_dt = occurred_at
        else:
            occurred_at_dt = datetime.now()

        event_json = json.dumps(event, sort_keys=True, separators=(",", ":"))

        conn.execute(
            self._INSERT_SQL,
            {
                "event_id": event_id,
                "tenant_id": tenant_id,
                "occurred_at": occurred_at_dt,
                "event_type": event_type,
                "request_id": request_id,
                "idempotency_key": idempotency_key,
                "event": event_json,
            },
        )

        logger.debug("Emitted audit event %s for tenant %s", event_id, tenant_id)


def get_postgres_audit_sink() -> PostgresAuditSink:
    """Factory function to get a PostgreSQL audit sink.

    Returns:
        PostgresAuditSink instance.
    """
    return PostgresAuditSink()
