"""Durable webhook delivery outbox repository (Slice97 Task 2).

The outbox is the durable, tenant-scoped queue of pending webhook deliveries backed by the
``webhook_delivery_attempts`` table (migration 0003) with its ``next_attempt_at`` drain index and
the ``(webhook_id, event_id)`` unique index (migration 0025) that makes ``enqueue`` idempotent even
under a race. The Postgres repository mirrors the idempotency store: every method either uses a
caller transaction (``conn``) or opens its own tenant-scoped connection (RLS via
``set_tenant_local``). A drainer claims due rows with ``FOR UPDATE SKIP LOCKED`` so replicas do not
double-deliver.

Status lifecycle: ``pending`` (retryable; carries the next ``next_attempt_at``) → ``succeeded`` or
``exhausted`` (terminal, ``next_attempt_at = NULL``). The in-memory repository is a dev/test twin.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

if TYPE_CHECKING:
    from sqlalchemy import Connection

logger = logging.getLogger(__name__)

STATUS_PENDING = "pending"
STATUS_SUCCEEDED = "succeeded"
STATUS_EXHAUSTED = "exhausted"
_TERMINAL_STATUSES = frozenset({STATUS_SUCCEEDED, STATUS_EXHAUSTED})


class WebhookOutboxError(Exception):
    """Raised when the durable outbox store is unavailable."""


@dataclass(frozen=True)
class WebhookOutboxRecord:
    """One durable webhook delivery attempt row."""

    attempt_id: str
    webhook_id: str
    tenant_id: str
    event_id: str
    event_type: str
    payload: dict[str, Any]
    attempt_count: int
    next_attempt_at: datetime | None
    last_attempt_at: datetime | None
    last_error: str | None
    status: str
    created_at: datetime
    updated_at: datetime


class WebhookOutboxRepository(Protocol):
    """Durable outbox contract shared by the in-memory and Postgres implementations."""

    def enqueue(
        self,
        *,
        webhook_id: str,
        tenant_id: str,
        event_id: str,
        event_type: str,
        payload: dict[str, Any],
        now: datetime,
        conn: Connection | None = None,
    ) -> bool: ...

    def claim_due(
        self, *, tenant_id: str, now: datetime, limit: int, conn: Connection | None = None
    ) -> list[WebhookOutboxRecord]: ...

    def mark_succeeded(
        self, *, tenant_id: str, attempt_id: str, now: datetime, conn: Connection | None = None
    ) -> None: ...

    def mark_failed(
        self,
        *,
        tenant_id: str,
        attempt_id: str,
        next_attempt_at: datetime,
        last_error: str,
        now: datetime,
        conn: Connection | None = None,
    ) -> None: ...

    def mark_exhausted(
        self,
        *,
        tenant_id: str,
        attempt_id: str,
        last_error: str,
        now: datetime,
        conn: Connection | None = None,
    ) -> None: ...

    def delete_terminal(
        self, *, tenant_id: str, older_than: datetime, conn: Connection | None = None
    ) -> int: ...


class InMemoryWebhookOutboxRepository:
    """Single-process in-memory outbox (dev/test). ``conn`` is ignored."""

    def __init__(self) -> None:
        self._rows: dict[str, WebhookOutboxRecord] = {}
        self._by_event: dict[tuple[str, str], str] = {}  # (webhook_id, event_id) -> attempt_id

    def enqueue(
        self,
        *,
        webhook_id: str,
        tenant_id: str,
        event_id: str,
        event_type: str,
        payload: dict[str, Any],
        now: datetime,
        conn: Connection | None = None,
    ) -> bool:
        if (webhook_id, event_id) in self._by_event:
            return False  # idempotent: at most one row per (webhook, event)
        attempt_id = str(uuid.uuid4())
        self._rows[attempt_id] = WebhookOutboxRecord(
            attempt_id=attempt_id,
            webhook_id=webhook_id,
            tenant_id=tenant_id,
            event_id=event_id,
            event_type=event_type,
            payload=dict(payload),
            attempt_count=0,
            next_attempt_at=now,
            last_attempt_at=None,
            last_error=None,
            status=STATUS_PENDING,
            created_at=now,
            updated_at=now,
        )
        self._by_event[(webhook_id, event_id)] = attempt_id
        return True

    def claim_due(
        self, *, tenant_id: str, now: datetime, limit: int, conn: Connection | None = None
    ) -> list[WebhookOutboxRecord]:
        due = [
            r
            for r in self._rows.values()
            if r.tenant_id == tenant_id
            and r.status == STATUS_PENDING
            and r.next_attempt_at is not None
            and r.next_attempt_at <= now
        ]
        due.sort(key=lambda r: (r.next_attempt_at, r.attempt_id))
        return due[:limit]

    def _get(self, tenant_id: str, attempt_id: str) -> WebhookOutboxRecord | None:
        record = self._rows.get(attempt_id)
        return record if record is not None and record.tenant_id == tenant_id else None

    def mark_succeeded(
        self, *, tenant_id: str, attempt_id: str, now: datetime, conn: Connection | None = None
    ) -> None:
        record = self._get(tenant_id, attempt_id)
        if record is None:
            return
        self._rows[attempt_id] = replace(
            record,
            status=STATUS_SUCCEEDED,
            next_attempt_at=None,
            last_attempt_at=now,
            updated_at=now,
            attempt_count=record.attempt_count + 1,
        )

    def mark_failed(
        self,
        *,
        tenant_id: str,
        attempt_id: str,
        next_attempt_at: datetime,
        last_error: str,
        now: datetime,
        conn: Connection | None = None,
    ) -> None:
        record = self._get(tenant_id, attempt_id)
        if record is None:
            return
        self._rows[attempt_id] = replace(
            record,
            status=STATUS_PENDING,  # retryable
            next_attempt_at=next_attempt_at,
            last_error=last_error,
            last_attempt_at=now,
            updated_at=now,
            attempt_count=record.attempt_count + 1,
        )

    def mark_exhausted(
        self,
        *,
        tenant_id: str,
        attempt_id: str,
        last_error: str,
        now: datetime,
        conn: Connection | None = None,
    ) -> None:
        record = self._get(tenant_id, attempt_id)
        if record is None:
            return
        self._rows[attempt_id] = replace(
            record,
            status=STATUS_EXHAUSTED,
            next_attempt_at=None,
            last_error=last_error,
            last_attempt_at=now,
            updated_at=now,
            attempt_count=record.attempt_count + 1,
        )

    def delete_terminal(
        self, *, tenant_id: str, older_than: datetime, conn: Connection | None = None
    ) -> int:
        removable = [
            attempt_id
            for attempt_id, record in self._rows.items()
            if record.tenant_id == tenant_id
            and record.status in _TERMINAL_STATUSES
            and record.updated_at < older_than
        ]
        for attempt_id in removable:
            record = self._rows.pop(attempt_id)
            self._by_event.pop((record.webhook_id, record.event_id), None)
        return len(removable)


def _parse_payload(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str | bytes | bytearray):
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _row_to_record(row: Any) -> WebhookOutboxRecord:
    return WebhookOutboxRecord(
        attempt_id=str(row.attempt_id),
        webhook_id=str(row.webhook_id),
        tenant_id=str(row.tenant_id),
        event_id=str(row.event_id),
        event_type=row.event_type,
        payload=_parse_payload(row.payload),
        attempt_count=int(row.attempt_count),
        next_attempt_at=row.next_attempt_at,
        last_attempt_at=row.last_attempt_at,
        last_error=row.last_error,
        status=row.status,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class PostgresWebhookOutboxRepository:
    """Postgres-backed outbox with RLS tenant isolation and race-safe idempotent enqueue."""

    _INSERT_SQL = text(
        """
        INSERT INTO webhook_delivery_attempts
            (attempt_id, webhook_id, tenant_id, event_id, event_type, payload,
             attempt_count, next_attempt_at, status, created_at, updated_at)
        VALUES
            (CAST(:attempt_id AS uuid), CAST(:webhook_id AS uuid), CAST(:tenant_id AS uuid),
             CAST(:event_id AS uuid), :event_type, CAST(:payload AS jsonb),
             0, :now, 'pending', :now, :now)
        ON CONFLICT (webhook_id, event_id) DO NOTHING
        """
    )

    _CLAIM_SQL = text(
        """
        SELECT attempt_id, webhook_id, tenant_id, event_id, event_type, payload, attempt_count,
               next_attempt_at, last_attempt_at, last_error, status, created_at, updated_at
        FROM webhook_delivery_attempts
        WHERE tenant_id = CAST(:tenant_id AS uuid)
            AND status = 'pending'
            AND next_attempt_at IS NOT NULL
            AND next_attempt_at <= :now
        ORDER BY next_attempt_at ASC
        LIMIT :limit
        FOR UPDATE SKIP LOCKED
        """
    )

    _MARK_SUCCEEDED_SQL = text(
        """
        UPDATE webhook_delivery_attempts
        SET status = 'succeeded', next_attempt_at = NULL, last_attempt_at = :now,
            updated_at = :now, attempt_count = attempt_count + 1
        WHERE attempt_id = CAST(:attempt_id AS uuid) AND tenant_id = CAST(:tenant_id AS uuid)
        """
    )

    _MARK_FAILED_SQL = text(
        """
        UPDATE webhook_delivery_attempts
        SET status = 'pending', next_attempt_at = :next_attempt_at, last_error = :last_error,
            last_attempt_at = :now, updated_at = :now, attempt_count = attempt_count + 1
        WHERE attempt_id = CAST(:attempt_id AS uuid) AND tenant_id = CAST(:tenant_id AS uuid)
        """
    )

    _MARK_EXHAUSTED_SQL = text(
        """
        UPDATE webhook_delivery_attempts
        SET status = 'exhausted', next_attempt_at = NULL, last_error = :last_error,
            last_attempt_at = :now, updated_at = :now, attempt_count = attempt_count + 1
        WHERE attempt_id = CAST(:attempt_id AS uuid) AND tenant_id = CAST(:tenant_id AS uuid)
        """
    )

    _DELETE_TERMINAL_SQL = text(
        """
        DELETE FROM webhook_delivery_attempts
        WHERE tenant_id = CAST(:tenant_id AS uuid)
            AND status IN ('succeeded', 'exhausted')
            AND updated_at < :older_than
        """
    )

    def _run(self, tenant_id: str, fn: Any, conn: Connection | None) -> Any:
        if conn is not None:
            return fn(conn)
        from idis.persistence.db import begin_app_conn, set_tenant_local

        try:
            with begin_app_conn() as new_conn:
                set_tenant_local(new_conn, tenant_id)
                return fn(new_conn)
        except SQLAlchemyError as exc:
            raise WebhookOutboxError(f"webhook outbox operation failed: {exc}") from exc

    def enqueue(
        self,
        *,
        webhook_id: str,
        tenant_id: str,
        event_id: str,
        event_type: str,
        payload: dict[str, Any],
        now: datetime,
        conn: Connection | None = None,
    ) -> bool:
        params = {
            "attempt_id": str(uuid.uuid4()),
            "webhook_id": webhook_id,
            "tenant_id": tenant_id,
            "event_id": event_id,
            "event_type": event_type,
            "payload": json.dumps(payload),
            "now": now,
        }
        result = self._run(tenant_id, lambda c: c.execute(self._INSERT_SQL, params), conn)
        return int(result.rowcount) > 0

    def claim_due(
        self, *, tenant_id: str, now: datetime, limit: int, conn: Connection | None = None
    ) -> list[WebhookOutboxRecord]:
        params = {"tenant_id": tenant_id, "now": now, "limit": limit}
        result = self._run(tenant_id, lambda c: c.execute(self._CLAIM_SQL, params), conn)
        return [_row_to_record(row) for row in result.fetchall()]

    def mark_succeeded(
        self, *, tenant_id: str, attempt_id: str, now: datetime, conn: Connection | None = None
    ) -> None:
        params = {"attempt_id": attempt_id, "tenant_id": tenant_id, "now": now}
        self._run(tenant_id, lambda c: c.execute(self._MARK_SUCCEEDED_SQL, params), conn)

    def mark_failed(
        self,
        *,
        tenant_id: str,
        attempt_id: str,
        next_attempt_at: datetime,
        last_error: str,
        now: datetime,
        conn: Connection | None = None,
    ) -> None:
        params = {
            "attempt_id": attempt_id,
            "tenant_id": tenant_id,
            "next_attempt_at": next_attempt_at,
            "last_error": last_error,
            "now": now,
        }
        self._run(tenant_id, lambda c: c.execute(self._MARK_FAILED_SQL, params), conn)

    def mark_exhausted(
        self,
        *,
        tenant_id: str,
        attempt_id: str,
        last_error: str,
        now: datetime,
        conn: Connection | None = None,
    ) -> None:
        params = {
            "attempt_id": attempt_id,
            "tenant_id": tenant_id,
            "last_error": last_error,
            "now": now,
        }
        self._run(tenant_id, lambda c: c.execute(self._MARK_EXHAUSTED_SQL, params), conn)

    def delete_terminal(
        self, *, tenant_id: str, older_than: datetime, conn: Connection | None = None
    ) -> int:
        params = {"tenant_id": tenant_id, "older_than": older_than}
        result = self._run(tenant_id, lambda c: c.execute(self._DELETE_TERMINAL_SQL, params), conn)
        return int(result.rowcount)


def build_default_webhook_outbox() -> WebhookOutboxRepository:
    """Durable Postgres outbox when configured, else the in-memory dev/test fallback."""
    from idis.persistence.db import is_postgres_configured

    if is_postgres_configured():
        return PostgresWebhookOutboxRepository()
    return InMemoryWebhookOutboxRepository()


_DEFAULT_OUTBOX: WebhookOutboxRepository | None = None
_DEFAULT_LOCK = threading.Lock()


def default_webhook_outbox() -> WebhookOutboxRepository:
    """Process-wide default outbox (lazy, thread-safe). The in-memory fallback MUST be a singleton
    so enqueue and a future drainer share one store; the Postgres repo is stateless."""
    global _DEFAULT_OUTBOX
    outbox = _DEFAULT_OUTBOX
    if outbox is None:
        with _DEFAULT_LOCK:
            outbox = _DEFAULT_OUTBOX
            if outbox is None:
                outbox = build_default_webhook_outbox()
                _DEFAULT_OUTBOX = outbox
    return outbox


def reset_default_webhook_outbox() -> None:
    """Reset the process default (tests only)."""
    global _DEFAULT_OUTBOX
    with _DEFAULT_LOCK:
        _DEFAULT_OUTBOX = None
