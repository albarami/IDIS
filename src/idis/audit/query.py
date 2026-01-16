"""Audit event query layer for IDIS.

Provides tenant-isolated, paginated retrieval of audit events from
either PostgreSQL or JSONL file sink backends.

Design Requirements (v6.3):
    - Tenant isolation: Always scope queries by tenant_id (fail closed)
    - Stable pagination: Deterministic ordering by occurred_at DESC, event_id DESC
    - Cursor-based: base64url-encoded JSON with last_occurred_at + last_event_id
    - Fail closed: Invalid cursor returns 400, not uncaught exception
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy.exc import SQLAlchemyError

from idis.api.errors import IdisHttpError

if TYPE_CHECKING:
    from sqlalchemy import Connection

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AuditEventItem:
    """Minimal audit event for API response per OpenAPI AuditEvent schema."""

    event_id: str
    event_type: str
    occurred_at: str


@dataclass(frozen=True, slots=True)
class AuditEventsPage:
    """Paginated audit events result."""

    items: list[AuditEventItem]
    next_cursor: str | None


def _encode_cursor(occurred_at: str, event_id: str) -> str:
    """Encode pagination cursor as base64url JSON."""
    payload = {"occurred_at": occurred_at, "event_id": event_id}
    json_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(json_bytes).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[str, str]:
    """Decode pagination cursor from base64url JSON.

    Returns:
        Tuple of (occurred_at, event_id).

    Raises:
        IdisHttpError: 400 if cursor is invalid.
    """
    try:
        json_bytes = base64.urlsafe_b64decode(cursor.encode("ascii"))
        payload = json.loads(json_bytes.decode("utf-8"))
        occurred_at = payload["occurred_at"]
        event_id = payload["event_id"]
        if not isinstance(occurred_at, str) or not isinstance(event_id, str):
            raise ValueError("Invalid cursor field types")
        return occurred_at, event_id
    except Exception as e:
        logger.warning("Invalid audit cursor: %s", e)
        raise IdisHttpError(
            status_code=400,
            code="INVALID_CURSOR",
            message="Invalid pagination cursor",
            details={"reason": "cursor_parse_failed"},
        ) from e


def _validate_limit(limit: int) -> int:
    """Validate and clamp limit parameter.

    Args:
        limit: Requested limit.

    Returns:
        Validated limit (1-200).

    Raises:
        IdisHttpError: 400 if limit is out of range.
    """
    if limit < 1 or limit > 200:
        raise IdisHttpError(
            status_code=400,
            code="INVALID_LIMIT",
            message="Limit must be between 1 and 200",
            details={"provided": limit, "min": 1, "max": 200},
        )
    return limit


class PostgresAuditQueryRepository:
    """Query audit events from PostgreSQL with RLS tenant isolation."""

    def __init__(self, conn: Connection, tenant_id: str) -> None:
        """Initialize with connection and tenant ID.

        Args:
            conn: SQLAlchemy connection with tenant context already set.
            tenant_id: Tenant UUID string for filtering.
        """
        self._conn = conn
        self._tenant_id = tenant_id

    def list_events(
        self,
        limit: int = 50,
        cursor: str | None = None,
        deal_id: str | None = None,
        event_type: str | None = None,
        after: datetime | None = None,
        before: datetime | None = None,
    ) -> AuditEventsPage:
        """List audit events with pagination and optional filters.

        Args:
            limit: Maximum events to return (1-200).
            cursor: Pagination cursor from previous response.
            deal_id: Optional filter by deal_id.
            event_type: Optional filter by event_type.
            after: Optional filter for events after this timestamp.
            before: Optional filter for events before this timestamp.

        Returns:
            AuditEventsPage with items and optional next_cursor.
        """
        from sqlalchemy import text

        limit = _validate_limit(limit)

        params: dict[str, Any] = {"tenant_id": self._tenant_id, "limit": limit + 1}

        where_clauses = ["tenant_id = :tenant_id"]

        if cursor:
            cursor_occurred_at, cursor_event_id = _decode_cursor(cursor)
            where_clauses.append(
                "(occurred_at, event_id) < "
                "(CAST(:cursor_occurred_at AS timestamptz), CAST(:cursor_event_id AS uuid))"
            )
            params["cursor_occurred_at"] = cursor_occurred_at
            params["cursor_event_id"] = cursor_event_id

        if deal_id:
            where_clauses.append("event::jsonb->'request'->>'deal_id' = :deal_id")
            params["deal_id"] = deal_id

        if event_type:
            where_clauses.append("event_type = :event_type")
            params["event_type"] = event_type

        if after:
            where_clauses.append("occurred_at > :after")
            params["after"] = after

        if before:
            where_clauses.append("occurred_at < :before")
            params["before"] = before

        where_sql = " AND ".join(where_clauses)

        query = text(
            f"""
            SELECT event_id, event_type, occurred_at
            FROM audit_events
            WHERE {where_sql}
            ORDER BY occurred_at DESC, event_id DESC
            LIMIT :limit
            """
        )

        try:
            result = self._conn.execute(query, params)
            rows = result.fetchall()
        except SQLAlchemyError as e:
            logger.exception(
                "Audit query backend error (tenant_id=%s): %s",
                self._tenant_id,
                type(e).__name__,
            )
            raise IdisHttpError(
                status_code=500,
                code="AUDIT_STORE_UNAVAILABLE",
                message="Audit store unavailable",
                details=None,
            ) from e

        items: list[AuditEventItem] = []
        for row in rows[:limit]:
            occurred_at_str = (
                row.occurred_at.isoformat()
                if hasattr(row.occurred_at, "isoformat")
                else str(row.occurred_at)
            )
            items.append(
                AuditEventItem(
                    event_id=str(row.event_id),
                    event_type=row.event_type,
                    occurred_at=occurred_at_str,
                )
            )

        next_cursor: str | None = None
        if len(rows) > limit:
            last_item = items[-1]
            next_cursor = _encode_cursor(last_item.occurred_at, last_item.event_id)

        return AuditEventsPage(items=items, next_cursor=next_cursor)


class JsonlAuditQueryRepository:
    """Query audit events from JSONL file with tenant filtering."""

    def __init__(self, file_path: Path, tenant_id: str) -> None:
        """Initialize with file path and tenant ID.

        Args:
            file_path: Path to JSONL audit log file.
            tenant_id: Tenant UUID string for filtering.
        """
        self._file_path = file_path
        self._tenant_id = tenant_id

    def list_events(
        self,
        limit: int = 50,
        cursor: str | None = None,
        deal_id: str | None = None,
        event_type: str | None = None,
        after: datetime | None = None,
        before: datetime | None = None,
    ) -> AuditEventsPage:
        """List audit events with pagination and optional filters.

        Reads JSONL file, filters by tenant, applies ordering and pagination.
        Returns empty list (not error) if file does not exist.

        Args:
            limit: Maximum events to return (1-200).
            cursor: Pagination cursor from previous response.
            deal_id: Optional filter by deal_id.
            event_type: Optional filter by event_type.
            after: Optional filter for events after this timestamp.
            before: Optional filter for events before this timestamp.

        Returns:
            AuditEventsPage with items and optional next_cursor.
        """
        limit = _validate_limit(limit)

        cursor_occurred_at: str | None = None
        cursor_event_id: str | None = None
        if cursor:
            cursor_occurred_at, cursor_event_id = _decode_cursor(cursor)

        if not self._file_path.exists():
            return AuditEventsPage(items=[], next_cursor=None)

        events: list[dict[str, Any]] = []
        try:
            with open(self._file_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    if event.get("tenant_id") != self._tenant_id:
                        continue

                    if event_type and event.get("event_type") != event_type:
                        continue

                    if deal_id:
                        req = event.get("request", {})
                        if req.get("deal_id") != deal_id:
                            continue

                    occurred_at_str = event.get("occurred_at", "")
                    if after:
                        try:
                            event_dt = datetime.fromisoformat(
                                occurred_at_str.replace("Z", "+00:00")
                            )
                            if event_dt <= after:
                                continue
                        except ValueError:
                            continue

                    if before:
                        try:
                            event_dt = datetime.fromisoformat(
                                occurred_at_str.replace("Z", "+00:00")
                            )
                            if event_dt >= before:
                                continue
                        except ValueError:
                            continue

                    events.append(event)

        except OSError as e:
            logger.warning("Failed to read audit log file %s: %s", self._file_path, e)
            return AuditEventsPage(items=[], next_cursor=None)

        events.sort(
            key=lambda ev: (ev.get("occurred_at", ""), ev.get("event_id", "")),
            reverse=True,
        )

        if cursor_occurred_at and cursor_event_id:
            filtered: list[dict[str, Any]] = []
            for ev in events:
                ev_occurred = ev.get("occurred_at", "")
                ev_id = ev.get("event_id", "")
                if (ev_occurred, ev_id) < (cursor_occurred_at, cursor_event_id):
                    filtered.append(ev)
            events = filtered

        page_events = events[: limit + 1]

        items: list[AuditEventItem] = []
        for ev in page_events[:limit]:
            items.append(
                AuditEventItem(
                    event_id=ev.get("event_id", ""),
                    event_type=ev.get("event_type", ""),
                    occurred_at=ev.get("occurred_at", ""),
                )
            )

        next_cursor: str | None = None
        if len(page_events) > limit:
            last_item = items[-1]
            next_cursor = _encode_cursor(last_item.occurred_at, last_item.event_id)

        return AuditEventsPage(items=items, next_cursor=next_cursor)
