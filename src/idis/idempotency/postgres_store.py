"""PostgreSQL-backed idempotency store for IDIS API.

Provides tenant-scoped, actor-scoped idempotency key storage using PostgreSQL.
Supports in-transaction operations for atomicity with other database operations.

Design Requirements (v6.3 API Contracts §4.1):
    - Scope by tenant_id + actor_id + method + operation_id + idempotency_key
    - Store payload SHA-256 digest (not raw body)
    - Store exact response bytes for deterministic replay
    - Fail closed when store is unavailable and Idempotency-Key is present
    - RLS tenant isolation enforced at database level
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from idis.idempotency.store import IdempotencyRecord, IdempotencyStoreError, ScopeKey

if TYPE_CHECKING:
    from sqlalchemy import Connection

logger = logging.getLogger(__name__)


class PostgresIdempotencyStore:
    """PostgreSQL-backed idempotency store with RLS tenant isolation.

    Provides get/put operations for idempotency records. Supports two modes:
    1. Standalone: Creates its own connection per operation
    2. In-transaction: Uses provided connection for atomic operations

    The store relies on RLS policies to enforce tenant isolation.
    Tenant context must be set on the connection before calling in-tx methods.
    """

    _SELECT_SQL = text(
        """
        SELECT payload_sha256, status_code, media_type, body_bytes, created_at
        FROM idempotency_records
        WHERE tenant_id = :tenant_id
            AND actor_id = :actor_id
            AND method = :method
            AND operation_id = :operation_id
            AND idempotency_key = :idempotency_key
        """
    )

    _INSERT_SQL = text(
        """
        INSERT INTO idempotency_records
        (tenant_id, actor_id, method, operation_id, idempotency_key,
         payload_sha256, status_code, media_type, body_bytes, created_at)
        VALUES
        (:tenant_id, :actor_id, :method, :operation_id, :idempotency_key,
         :payload_sha256, :status_code, :media_type, :body_bytes, :created_at)
        ON CONFLICT (tenant_id, actor_id, method, operation_id, idempotency_key)
        DO UPDATE SET
            payload_sha256 = EXCLUDED.payload_sha256,
            status_code = EXCLUDED.status_code,
            media_type = EXCLUDED.media_type,
            body_bytes = EXCLUDED.body_bytes,
            created_at = EXCLUDED.created_at
        """
    )

    def __init__(self) -> None:
        """Initialize the PostgreSQL idempotency store."""
        pass

    def get(self, scope_key: ScopeKey, conn: Connection | None = None) -> IdempotencyRecord | None:
        """Look up an idempotency record by scope key.

        Args:
            scope_key: Composite key (tenant_id, actor_id, method, operation_id, idempotency_key)
            conn: Optional SQLAlchemy connection. If None, creates new connection.

        Returns:
            IdempotencyRecord if found, None otherwise.

        Raises:
            IdempotencyStoreError: If lookup fails due to store error.
        """
        if conn is not None:
            return self._get_with_conn(scope_key, conn)

        from idis.persistence.db import begin_app_conn, set_tenant_local

        try:
            with begin_app_conn() as new_conn:
                set_tenant_local(new_conn, scope_key.tenant_id)
                return self._get_with_conn(scope_key, new_conn)
        except SQLAlchemyError as e:
            raise IdempotencyStoreError(f"Failed to lookup idempotency record: {e}") from e

    def _get_with_conn(self, scope_key: ScopeKey, conn: Connection) -> IdempotencyRecord | None:
        """Look up idempotency record using provided connection.

        Args:
            scope_key: Composite key.
            conn: SQLAlchemy connection with tenant context set.

        Returns:
            IdempotencyRecord if found, None otherwise.

        Raises:
            SQLAlchemyError: If query fails.
        """
        result = conn.execute(
            self._SELECT_SQL,
            {
                "tenant_id": scope_key.tenant_id,
                "actor_id": scope_key.actor_id,
                "method": scope_key.method,
                "operation_id": scope_key.operation_id,
                "idempotency_key": scope_key.idempotency_key,
            },
        )
        row = result.fetchone()

        if row is None:
            return None

        created_at = row.created_at
        if isinstance(created_at, datetime):
            created_at_str = created_at.isoformat().replace("+00:00", "Z")
        else:
            created_at_str = str(created_at)

        return IdempotencyRecord(
            payload_sha256=row.payload_sha256,
            status_code=row.status_code,
            media_type=row.media_type,
            body_bytes=bytes(row.body_bytes) if row.body_bytes else b"",
            created_at=created_at_str,
        )

    def put(
        self, scope_key: ScopeKey, record: IdempotencyRecord, conn: Connection | None = None
    ) -> None:
        """Store an idempotency record.

        Args:
            scope_key: Composite key (tenant_id, actor_id, method, operation_id, idempotency_key)
            record: Idempotency record to store.
            conn: Optional SQLAlchemy connection. If None, creates new connection.

        Raises:
            IdempotencyStoreError: If storage fails.
        """
        if conn is not None:
            self._put_with_conn(scope_key, record, conn)
            return

        from idis.persistence.db import begin_app_conn, set_tenant_local

        try:
            with begin_app_conn() as new_conn:
                set_tenant_local(new_conn, scope_key.tenant_id)
                self._put_with_conn(scope_key, record, new_conn)
        except SQLAlchemyError as e:
            raise IdempotencyStoreError(f"Failed to store idempotency record: {e}") from e

    def _put_with_conn(
        self, scope_key: ScopeKey, record: IdempotencyRecord, conn: Connection
    ) -> None:
        """Store idempotency record using provided connection.

        Args:
            scope_key: Composite key.
            record: Idempotency record to store.
            conn: SQLAlchemy connection with tenant context set.

        Raises:
            SQLAlchemyError: If INSERT fails.
        """
        if isinstance(record.created_at, str):
            created_at_dt = datetime.fromisoformat(record.created_at.replace("Z", "+00:00"))
        else:
            created_at_dt = record.created_at

        conn.execute(
            self._INSERT_SQL,
            {
                "tenant_id": scope_key.tenant_id,
                "actor_id": scope_key.actor_id,
                "method": scope_key.method,
                "operation_id": scope_key.operation_id,
                "idempotency_key": scope_key.idempotency_key,
                "payload_sha256": record.payload_sha256,
                "status_code": record.status_code,
                "media_type": record.media_type,
                "body_bytes": record.body_bytes,
                "created_at": created_at_dt,
            },
        )

        logger.debug(
            "Stored idempotency record for %s/%s/%s",
            scope_key.tenant_id,
            scope_key.operation_id,
            scope_key.idempotency_key,
        )


def get_postgres_idempotency_store() -> PostgresIdempotencyStore:
    """Factory function to create a PostgreSQL idempotency store.

    Returns:
        PostgresIdempotencyStore instance.
    """
    return PostgresIdempotencyStore()
