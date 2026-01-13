"""Deals repository for Postgres persistence.

Provides tenant-scoped CRUD operations for deals with RLS enforcement.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from idis.persistence.db import is_postgres_configured, set_tenant_local

if TYPE_CHECKING:
    from sqlalchemy import Connection

logger = logging.getLogger(__name__)


class DealNotFoundError(Exception):
    """Raised when a deal is not found."""

    def __init__(self, deal_id: str, tenant_id: str) -> None:
        self.deal_id = deal_id
        self.tenant_id = tenant_id
        super().__init__(f"Deal {deal_id} not found for tenant {tenant_id}")


class DealsRepository:
    """Repository for deal persistence operations.

    All operations are tenant-scoped via RLS. The connection must have
    tenant context set before calling repository methods.
    """

    def __init__(self, conn: Connection, tenant_id: str) -> None:
        """Initialize repository with connection and tenant context.

        Args:
            conn: SQLAlchemy connection (must be in a transaction).
            tenant_id: Tenant UUID string for RLS scoping.
        """
        self._conn = conn
        self._tenant_id = tenant_id
        set_tenant_local(conn, tenant_id)

    def create(
        self,
        *,
        deal_id: str,
        name: str,
        company_name: str,
        status: str = "NEW",
        stage: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a new deal.

        Args:
            deal_id: UUID string for the deal.
            name: Deal name.
            company_name: Company name.
            status: Deal status (default: NEW).
            stage: Optional deal stage.
            tags: Optional list of tags.

        Returns:
            Created deal as dict.
        """
        now = datetime.now(UTC)
        tags_json = json.dumps(tags or [])

        self._conn.execute(
            text(
                """
                INSERT INTO deals (
                    deal_id, tenant_id, name, company_name, status,
                    stage, tags, created_at, updated_at
                ) VALUES (
                    :deal_id, :tenant_id, :name, :company_name, :status,
                    :stage, :tags::jsonb, :created_at, NULL
                )
                """
            ),
            {
                "deal_id": deal_id,
                "tenant_id": self._tenant_id,
                "name": name,
                "company_name": company_name,
                "status": status,
                "stage": stage,
                "tags": tags_json,
                "created_at": now,
            },
        )

        return {
            "deal_id": deal_id,
            "tenant_id": self._tenant_id,
            "name": name,
            "company_name": company_name,
            "status": status,
            "stage": stage,
            "tags": tags or [],
            "created_at": now.isoformat().replace("+00:00", "Z"),
            "updated_at": None,
        }

    def get(self, deal_id: str) -> dict[str, Any] | None:
        """Get a deal by ID.

        RLS ensures only deals for the current tenant are visible.

        Args:
            deal_id: UUID string of the deal.

        Returns:
            Deal as dict, or None if not found.
        """
        result = self._conn.execute(
            text(
                """
                SELECT deal_id, tenant_id, name, company_name, status,
                       stage, tags, created_at, updated_at
                FROM deals
                WHERE deal_id = :deal_id
                """
            ),
            {"deal_id": deal_id},
        ).fetchone()

        if result is None:
            return None

        return self._row_to_dict(result)

    def list(
        self,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """List deals for the current tenant.

        RLS ensures only tenant's deals are returned.

        Args:
            limit: Maximum number of deals to return.
            cursor: Pagination cursor (deal_id to start after).

        Returns:
            Tuple of (deals list, next_cursor or None).
        """
        effective_limit = min(max(1, limit), 200)

        if cursor:
            result = self._conn.execute(
                text(
                    """
                    SELECT deal_id, tenant_id, name, company_name, status,
                           stage, tags, created_at, updated_at
                    FROM deals
                    WHERE deal_id > :cursor
                    ORDER BY deal_id
                    LIMIT :limit
                    """
                ),
                {"cursor": cursor, "limit": effective_limit + 1},
            ).fetchall()
        else:
            result = self._conn.execute(
                text(
                    """
                    SELECT deal_id, tenant_id, name, company_name, status,
                           stage, tags, created_at, updated_at
                    FROM deals
                    ORDER BY deal_id
                    LIMIT :limit
                    """
                ),
                {"limit": effective_limit + 1},
            ).fetchall()

        deals = [self._row_to_dict(row) for row in result[:effective_limit]]

        next_cursor = None
        if len(result) > effective_limit:
            next_cursor = deals[-1]["deal_id"]

        return deals, next_cursor

    def delete(self, deal_id: str) -> bool:
        """Delete a deal by ID.

        Used for saga compensation.

        Args:
            deal_id: UUID string of the deal.

        Returns:
            True if deleted, False if not found.
        """
        result = self._conn.execute(
            text("DELETE FROM deals WHERE deal_id = :deal_id"),
            {"deal_id": deal_id},
        )
        return result.rowcount > 0

    def _row_to_dict(self, row: Any) -> dict[str, Any]:
        """Convert database row to dict."""
        created_at = row.created_at
        if hasattr(created_at, "isoformat"):
            created_at = created_at.isoformat().replace("+00:00", "Z")

        updated_at = row.updated_at
        if updated_at is not None and hasattr(updated_at, "isoformat"):
            updated_at = updated_at.isoformat().replace("+00:00", "Z")

        tags = row.tags
        if isinstance(tags, str):
            tags = json.loads(tags)

        return {
            "deal_id": str(row.deal_id),
            "tenant_id": str(row.tenant_id),
            "name": row.name,
            "company_name": row.company_name,
            "status": row.status,
            "stage": row.stage,
            "tags": tags or [],
            "created_at": created_at,
            "updated_at": updated_at,
        }


_in_memory_store: dict[str, dict[str, Any]] = {}


class InMemoryDealsRepository:
    """In-memory fallback repository for when Postgres is not configured.

    Used for development/testing without database dependency.
    """

    def __init__(self, tenant_id: str) -> None:
        """Initialize with tenant context."""
        self._tenant_id = tenant_id

    def create(
        self,
        *,
        deal_id: str,
        name: str,
        company_name: str,
        status: str = "NEW",
        stage: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a new deal in memory."""
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")

        deal = {
            "deal_id": deal_id,
            "tenant_id": self._tenant_id,
            "name": name,
            "company_name": company_name,
            "status": status,
            "stage": stage,
            "tags": tags or [],
            "created_at": now,
            "updated_at": None,
        }

        _in_memory_store[deal_id] = deal
        return deal

    def get(self, deal_id: str) -> dict[str, Any] | None:
        """Get a deal by ID from memory."""
        deal = _in_memory_store.get(deal_id)
        if deal is None or deal.get("tenant_id") != self._tenant_id:
            return None
        return deal

    def list(
        self,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """List deals from memory."""
        tenant_deals = [
            d for d in _in_memory_store.values() if d.get("tenant_id") == self._tenant_id
        ]
        tenant_deals.sort(key=lambda x: x["deal_id"])

        if cursor:
            tenant_deals = [d for d in tenant_deals if d["deal_id"] > cursor]

        effective_limit = min(max(1, limit), 200)
        items = tenant_deals[:effective_limit]

        next_cursor = None
        if len(tenant_deals) > effective_limit:
            next_cursor = items[-1]["deal_id"]

        return items, next_cursor

    def delete(self, deal_id: str) -> bool:
        """Delete a deal from memory."""
        deal = _in_memory_store.get(deal_id)
        if deal is None or deal.get("tenant_id") != self._tenant_id:
            return False
        del _in_memory_store[deal_id]
        return deal_id not in _in_memory_store


def clear_in_memory_store() -> None:
    """Clear the in-memory store. For testing only."""
    _in_memory_store.clear()


def get_deals_repository(
    conn: Connection | None,
    tenant_id: str,
) -> DealsRepository | InMemoryDealsRepository:
    """Factory to get appropriate deals repository.

    Returns Postgres repository if configured, otherwise in-memory fallback.

    Args:
        conn: SQLAlchemy connection (can be None for in-memory).
        tenant_id: Tenant UUID string.

    Returns:
        Repository instance.
    """
    if conn is not None and is_postgres_configured():
        return DealsRepository(conn, tenant_id)
    return InMemoryDealsRepository(tenant_id)
