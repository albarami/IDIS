"""Runs repository for Postgres persistence and in-memory fallback.

Provides tenant-scoped CRUD operations for pipeline runs with RLS enforcement.
Extracted from inline SQL in api/routes/runs.py for Phase 7.A persistence cutover.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from idis.persistence.db import is_postgres_configured, set_tenant_local

if TYPE_CHECKING:
    from sqlalchemy import Connection

logger = logging.getLogger(__name__)


class PostgresRunsRepository:
    """Tenant-scoped Postgres repository for pipeline run records.

    All operations enforce RLS via SET LOCAL idis.tenant_id.

    Args:
        conn: SQLAlchemy connection (must be in a transaction).
        tenant_id: Tenant UUID string for RLS scoping.
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
        run_id: str,
        deal_id: str,
        mode: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Create a new run record.

        Args:
            run_id: UUID string for the run.
            deal_id: UUID string for the deal.
            mode: Run mode (SNAPSHOT or FULL).
            idempotency_key: Optional idempotency key from request header.

        Returns:
            Created run as dict.
        """
        now = datetime.now(UTC)
        self._conn.execute(
            text(
                """
                INSERT INTO runs
                    (run_id, tenant_id, deal_id, mode, status, started_at,
                     idempotency_key, created_at)
                VALUES
                    (:run_id, :tenant_id, :deal_id, :mode, 'QUEUED', :started_at,
                     :idempotency_key, :created_at)
                """
            ),
            {
                "run_id": run_id,
                "tenant_id": self._tenant_id,
                "deal_id": deal_id,
                "mode": mode,
                "started_at": now,
                "idempotency_key": idempotency_key,
                "created_at": now,
            },
        )
        return {
            "run_id": run_id,
            "tenant_id": self._tenant_id,
            "deal_id": deal_id,
            "mode": mode,
            "status": "QUEUED",
            "started_at": now.isoformat().replace("+00:00", "Z"),
            "finished_at": None,
            "created_at": now.isoformat().replace("+00:00", "Z"),
        }

    def get(self, run_id: str) -> dict[str, Any] | None:
        """Get a run by ID.

        RLS ensures only runs for the current tenant are visible.

        Args:
            run_id: UUID string of the run.

        Returns:
            Run as dict, or None if not found.
        """
        result = self._conn.execute(
            text(
                """
                SELECT run_id, tenant_id, deal_id, mode, status,
                       started_at, finished_at, created_at
                FROM runs
                WHERE run_id = :run_id
                """
            ),
            {"run_id": run_id},
        )
        row = result.fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    def update_status(
        self,
        run_id: str,
        *,
        status: str,
        finished_at: str | None = None,
    ) -> None:
        """Update run status and optional finished_at timestamp.

        Args:
            run_id: UUID of the run.
            status: New status value.
            finished_at: ISO timestamp string or None.
        """
        if finished_at is not None:
            self._conn.execute(
                text(
                    """
                    UPDATE runs
                    SET status = :status, finished_at = :finished_at
                    WHERE run_id = :run_id
                    """
                ),
                {"run_id": run_id, "status": status, "finished_at": finished_at},
            )
        else:
            self._conn.execute(
                text(
                    """
                    UPDATE runs SET status = :status WHERE run_id = :run_id
                    """
                ),
                {"run_id": run_id, "status": status},
            )

    def deal_exists(self, deal_id: str) -> bool:
        """Check if deal exists (RLS enforced).

        Args:
            deal_id: UUID of the deal.

        Returns:
            True if deal visible to current tenant.
        """
        result = self._conn.execute(
            text("SELECT 1 FROM deals WHERE deal_id = :deal_id"),
            {"deal_id": deal_id},
        )
        return result.fetchone() is not None

    def _row_to_dict(self, row: Any) -> dict[str, Any]:
        """Convert database row to dict."""
        started_at = row.started_at
        if hasattr(started_at, "isoformat"):
            started_at = started_at.isoformat().replace("+00:00", "Z")

        finished_at = row.finished_at
        if finished_at is not None and hasattr(finished_at, "isoformat"):
            finished_at = finished_at.isoformat().replace("+00:00", "Z")

        created_at = row.created_at
        if hasattr(created_at, "isoformat"):
            created_at = created_at.isoformat().replace("+00:00", "Z")

        return {
            "run_id": str(row.run_id),
            "tenant_id": str(row.tenant_id),
            "deal_id": str(row.deal_id),
            "mode": row.mode,
            "status": row.status,
            "started_at": started_at,
            "finished_at": finished_at,
            "created_at": created_at,
        }


_in_memory_runs_store: dict[str, dict[str, Any]] = {}
"""Global in-memory store keyed by run_id."""


class InMemoryRunsRepository:
    """In-memory fallback repository for runs when Postgres is not configured.

    All reads filter by tenant_id so cross-tenant access returns None
    (no existence oracle).

    Args:
        tenant_id: Tenant UUID string for scoping.
    """

    def __init__(self, tenant_id: str) -> None:
        """Initialize with tenant context.

        Args:
            tenant_id: Tenant UUID for isolation.
        """
        self._tenant_id = tenant_id

    def create(
        self,
        *,
        run_id: str,
        deal_id: str,
        mode: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Create a new run record in memory.

        Args:
            run_id: UUID string for the run.
            deal_id: UUID string for the deal.
            mode: Run mode (SNAPSHOT or FULL).
            idempotency_key: Optional idempotency key (stored but not enforced).

        Returns:
            Created run as dict.
        """
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        run = {
            "run_id": run_id,
            "tenant_id": self._tenant_id,
            "deal_id": deal_id,
            "mode": mode,
            "status": "QUEUED",
            "started_at": now,
            "finished_at": None,
            "created_at": now,
        }
        _in_memory_runs_store[run_id] = run
        return run

    def get(self, run_id: str) -> dict[str, Any] | None:
        """Get a run by ID from memory.

        Returns None for cross-tenant access (no existence leak).

        Args:
            run_id: UUID string of the run.

        Returns:
            Run dict or None.
        """
        run = _in_memory_runs_store.get(run_id)
        if run is None or run.get("tenant_id") != self._tenant_id:
            return None
        return run

    def update_status(
        self,
        run_id: str,
        *,
        status: str,
        finished_at: str | None = None,
    ) -> None:
        """Update run status in memory.

        Args:
            run_id: UUID of the run.
            status: New status value.
            finished_at: ISO timestamp string or None.
        """
        run = _in_memory_runs_store.get(run_id)
        if run is not None and run.get("tenant_id") == self._tenant_id:
            run["status"] = status
            if finished_at is not None:
                run["finished_at"] = finished_at
            _in_memory_runs_store[run_id] = run

    def deal_exists(self, deal_id: str) -> bool:
        """Check if deal exists in memory.

        Delegates to InMemoryDealsRepository.

        Args:
            deal_id: UUID of the deal.

        Returns:
            True if deal visible to current tenant.
        """
        from idis.persistence.repositories.deals import InMemoryDealsRepository

        deals_repo = InMemoryDealsRepository(self._tenant_id)
        return deals_repo.get(deal_id) is not None


def clear_in_memory_runs_store() -> None:
    """Clear the in-memory runs store. For testing only."""
    _in_memory_runs_store.clear()


def get_runs_repository(
    conn: Connection | None,
    tenant_id: str,
) -> PostgresRunsRepository | InMemoryRunsRepository:
    """Factory to get appropriate runs repository.

    Returns Postgres repository if configured, otherwise in-memory fallback.

    Args:
        conn: SQLAlchemy connection (can be None for in-memory).
        tenant_id: Tenant UUID string.

    Returns:
        Repository instance.
    """
    if conn is not None and is_postgres_configured():
        return PostgresRunsRepository(conn, tenant_id)
    return InMemoryRunsRepository(tenant_id)
