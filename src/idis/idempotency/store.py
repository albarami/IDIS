"""SQLite-backed idempotency store for IDIS API.

Provides tenant-scoped, actor-scoped idempotency key storage for safe API retries.
Stores only successful (2xx) responses to avoid replaying stale error responses.

Design requirements (v6.3 API Contracts §4.1):
- Scope by tenant_id + actor_id + method + operation_id + idempotency_key
- Store payload SHA-256 digest (not raw body)
- Store exact response bytes for deterministic replay
- Fail closed when store is unavailable and Idempotency-Key is present
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

logger = logging.getLogger(__name__)

IDIS_IDEMPOTENCY_DB_PATH_ENV = "IDIS_IDEMPOTENCY_DB_PATH"
DEFAULT_IDEMPOTENCY_DB_PATH = "./var/idempotency/idempotency.sqlite3"


class ScopeKey(NamedTuple):
    """Composite key for idempotency record lookup.

    Scoped by tenant + actor + method + operation + idempotency_key to ensure:
    - No cross-tenant replay
    - No cross-actor replay
    - No cross-operation replay
    """

    tenant_id: str
    actor_id: str
    method: str
    operation_id: str
    idempotency_key: str


@dataclass(frozen=True)
class IdempotencyRecord:
    """Stored idempotency record for response replay.

    Attributes:
        payload_sha256: SHA-256 digest of request body (format: sha256:<hex>)
        status_code: HTTP status code of stored response
        media_type: Content-Type of stored response
        body_bytes: Exact response body bytes for replay
        created_at: UTC ISO-8601 timestamp when record was created
    """

    payload_sha256: str
    status_code: int
    media_type: str
    body_bytes: bytes
    created_at: str


class IdempotencyStoreError(Exception):
    """Raised when idempotency store operations fail.

    This error indicates the store is unavailable or corrupted.
    Middleware should fail closed (500) when this occurs.
    """

    pass


class SqliteIdempotencyStore:
    """SQLite-backed idempotency store with thread-safe access.

    Creates database and parent directories on first use.
    Uses WAL mode for better concurrent read performance.

    Environment:
        IDIS_IDEMPOTENCY_DB_PATH: Path to SQLite database file.
            Default: ./var/idempotency/idempotency.sqlite3
    """

    _CREATE_TABLE_SQL = """
        CREATE TABLE IF NOT EXISTS idempotency_records (
            tenant_id TEXT NOT NULL,
            actor_id TEXT NOT NULL,
            method TEXT NOT NULL,
            operation_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            payload_sha256 TEXT NOT NULL,
            status_code INTEGER NOT NULL,
            media_type TEXT NOT NULL,
            body_bytes BLOB NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, actor_id, method, operation_id, idempotency_key)
        )
    """

    _SELECT_SQL = """
        SELECT payload_sha256, status_code, media_type, body_bytes, created_at
        FROM idempotency_records
        WHERE tenant_id = ? AND actor_id = ? AND method = ?
            AND operation_id = ? AND idempotency_key = ?
    """

    _INSERT_SQL = """
        INSERT OR REPLACE INTO idempotency_records
        (tenant_id, actor_id, method, operation_id, idempotency_key,
         payload_sha256, status_code, media_type, body_bytes, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    def __init__(self, db_path: str | None = None) -> None:
        """Initialize the idempotency store.

        Args:
            db_path: Path to SQLite database file. If None, uses environment
                variable IDIS_IDEMPOTENCY_DB_PATH or default path.

        Raises:
            IdempotencyStoreError: If database cannot be initialized.
        """
        if db_path is None:
            db_path = os.environ.get(IDIS_IDEMPOTENCY_DB_PATH_ENV, DEFAULT_IDEMPOTENCY_DB_PATH)

        self._db_path = db_path
        self._local = threading.local()
        self._initialized = False
        self._init_lock = threading.Lock()

    def _get_connection(self) -> sqlite3.Connection:
        """Get or create a thread-local database connection.

        Returns:
            Thread-local SQLite connection.

        Raises:
            IdempotencyStoreError: If connection cannot be established.
        """
        if not self._initialized:
            with self._init_lock:
                if not self._initialized:
                    self._ensure_database()
                    self._initialized = True

        conn = getattr(self._local, "conn", None)
        if conn is None:
            try:
                conn = sqlite3.connect(self._db_path, check_same_thread=False)
                conn.row_factory = sqlite3.Row
                self._local.conn = conn
            except sqlite3.Error as e:
                raise IdempotencyStoreError(f"Failed to connect to idempotency store: {e}") from e

        return conn

    def _ensure_database(self) -> None:
        """Create database file and table if they don't exist.

        Raises:
            IdempotencyStoreError: If database cannot be created.
        """
        try:
            db_path = Path(self._db_path)

            if db_path.is_dir():
                raise IdempotencyStoreError(
                    f"Idempotency store path is a directory: {self._db_path}"
                )

            db_path.parent.mkdir(parents=True, exist_ok=True)

            conn = sqlite3.connect(self._db_path)
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute(self._CREATE_TABLE_SQL)
                conn.commit()
            finally:
                conn.close()

            logger.info("Initialized idempotency store at %s", self._db_path)

        except sqlite3.Error as e:
            raise IdempotencyStoreError(f"Failed to initialize idempotency store: {e}") from e
        except OSError as e:
            raise IdempotencyStoreError(f"Failed to create idempotency store directory: {e}") from e

    def get(self, scope_key: ScopeKey) -> IdempotencyRecord | None:
        """Look up an idempotency record by scope key.

        Args:
            scope_key: Composite key (tenant_id, actor_id, method, operation_id, idempotency_key)

        Returns:
            IdempotencyRecord if found, None otherwise.

        Raises:
            IdempotencyStoreError: If lookup fails due to store error.
        """
        try:
            conn = self._get_connection()
            cursor = conn.execute(
                self._SELECT_SQL,
                (
                    scope_key.tenant_id,
                    scope_key.actor_id,
                    scope_key.method,
                    scope_key.operation_id,
                    scope_key.idempotency_key,
                ),
            )
            row = cursor.fetchone()

            if row is None:
                return None

            return IdempotencyRecord(
                payload_sha256=row["payload_sha256"],
                status_code=row["status_code"],
                media_type=row["media_type"],
                body_bytes=row["body_bytes"],
                created_at=row["created_at"],
            )

        except sqlite3.Error as e:
            raise IdempotencyStoreError(f"Failed to lookup idempotency record: {e}") from e

    def put(self, scope_key: ScopeKey, record: IdempotencyRecord) -> None:
        """Store an idempotency record.

        Args:
            scope_key: Composite key (tenant_id, actor_id, method, operation_id, idempotency_key)
            record: Idempotency record to store

        Raises:
            IdempotencyStoreError: If storage fails.
        """
        try:
            conn = self._get_connection()
            conn.execute(
                self._INSERT_SQL,
                (
                    scope_key.tenant_id,
                    scope_key.actor_id,
                    scope_key.method,
                    scope_key.operation_id,
                    scope_key.idempotency_key,
                    record.payload_sha256,
                    record.status_code,
                    record.media_type,
                    record.body_bytes,
                    record.created_at,
                ),
            )
            conn.commit()

        except sqlite3.Error as e:
            raise IdempotencyStoreError(f"Failed to store idempotency record: {e}") from e

    def close(self) -> None:
        """Close the thread-local database connection if open."""
        import contextlib

        conn = getattr(self._local, "conn", None)
        if conn is not None:
            with contextlib.suppress(sqlite3.Error):
                conn.close()
            self._local.conn = None


def create_idempotency_store(db_path: str | None = None) -> SqliteIdempotencyStore:
    """Factory function to create an idempotency store.

    Args:
        db_path: Optional path to SQLite database. If None, uses environment
            variable or default path.

    Returns:
        Configured SqliteIdempotencyStore instance.
    """
    return SqliteIdempotencyStore(db_path=db_path)


def get_current_timestamp() -> str:
    """Get current UTC timestamp in ISO-8601 format.

    Returns:
        UTC timestamp string (e.g., "2026-01-07T19:00:00Z")
    """
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
