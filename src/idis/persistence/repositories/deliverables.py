"""Tenant-scoped deliverable persistence helpers."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

from sqlalchemy import text

from idis.persistence.db import set_tenant_local

if TYPE_CHECKING:
    from sqlalchemy import Connection


SAFE_OBJECT_URI_PATTERN = re.compile(r"^object:filesystem:[0-9a-f]{16}:[0-9a-f]{16}$")


class DeliverablesRepository(Protocol):
    """Persistence contract used by durable product export."""

    def create_completed(
        self,
        *,
        deliverable_id: str,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        deliverable_type: str,
        format_: str,
        uri: str,
    ) -> dict[str, Any]: ...

    def list_by_deal(
        self,
        *,
        deal_id: str,
        limit: int,
        cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]: ...


def deterministic_deliverable_row_id(
    *,
    tenant_id: str,
    run_id: str,
    deliverable_type: str,
    format_: str,
) -> str:
    """Return a stable row UUID scoped to tenant, run, type, and format."""
    stable_key = f"{tenant_id}:{run_id}:{deliverable_type}:{format_}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, stable_key))


def safe_public_deliverable_uri(uri: str | None) -> str | None:
    """Return a deliverable URI only when it matches the public object URI contract."""
    if uri is None:
        return None
    value = str(uri)
    return value if SAFE_OBJECT_URI_PATTERN.fullmatch(value) else None


class PostgresDeliverablesRepository:
    """Tenant-scoped repository for completed deliverable rows."""

    def __init__(self, conn: Connection, tenant_id: str) -> None:
        """Initialize with a transaction-scoped connection."""
        self._conn = conn
        self._tenant_id = tenant_id
        set_tenant_local(conn, tenant_id)

    def create_completed(
        self,
        *,
        deliverable_id: str,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        deliverable_type: str,
        format_: str,
        uri: str,
    ) -> dict[str, Any]:
        """Create or update a completed deliverable metadata row."""
        if tenant_id != self._tenant_id:
            raise ValueError("Tenant mismatch in deliverable creation")
        now = datetime.now(UTC)
        self._conn.execute(
            text(
                """
                INSERT INTO deliverables (
                    deliverable_id, tenant_id, deal_id, run_id, deliverable_type,
                    format, status, uri, created_at
                )
                VALUES (
                    :deliverable_id, :tenant_id, :deal_id, :run_id, :deliverable_type,
                    :format, 'COMPLETED', :uri, :created_at
                )
                ON CONFLICT (deliverable_id) DO UPDATE
                SET status = 'COMPLETED',
                    uri = EXCLUDED.uri
                """
            ),
            {
                "deliverable_id": deliverable_id,
                "tenant_id": tenant_id,
                "deal_id": deal_id,
                "run_id": run_id,
                "deliverable_type": deliverable_type,
                "format": format_,
                "uri": uri,
                "created_at": now,
            },
        )
        return {
            "deliverable_id": deliverable_id,
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "run_id": run_id,
            "deliverable_type": deliverable_type,
            "format": format_,
            "status": "COMPLETED",
            "uri": uri,
            "created_at": now.isoformat().replace("+00:00", "Z"),
        }

    def list_by_deal(
        self,
        *,
        deal_id: str,
        limit: int,
        cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """List deliverable rows for a deal in reverse creation order."""
        query = """
            SELECT deliverable_id, tenant_id, deal_id, deliverable_type, format, status, uri,
                   created_at
            FROM deliverables
            WHERE deal_id = :deal_id
        """
        params: dict[str, Any] = {"deal_id": deal_id, "limit": limit + 1}
        if cursor:
            query += " AND created_at < :cursor"
            params["cursor"] = cursor
        query += " ORDER BY created_at DESC LIMIT :limit"
        rows = self._conn.execute(text(query), params).fetchall()
        items: list[dict[str, Any]] = []
        next_cursor = None
        for index, row in enumerate(rows):
            if index >= limit:
                next_cursor = items[-1]["created_at"] if items else None
                break
            items.append(
                {
                    "deliverable_id": str(row.deliverable_id),
                    "tenant_id": str(row.tenant_id),
                    "deal_id": str(row.deal_id),
                    "deliverable_type": row.deliverable_type,
                    "format": row.format,
                    "status": row.status,
                    "uri": safe_public_deliverable_uri(row.uri),
                    "created_at": row.created_at.isoformat().replace("+00:00", "Z")
                    if row.created_at
                    else None,
                }
            )
        return items, next_cursor
