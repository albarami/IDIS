"""Runs repository for Postgres persistence and in-memory fallback.

Provides tenant-scoped CRUD operations for pipeline runs with RLS enforcement.
Extracted from inline SQL in api/routes/runs.py for Phase 7.A persistence cutover.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from idis.persistence.db import is_postgres_configured, set_tenant_local

if TYPE_CHECKING:
    from sqlalchemy import Connection

logger = logging.getLogger(__name__)


class RunAlreadyActiveError(Exception):
    """Raised on run creation when an active (QUEUED/RUNNING) run already exists for the deal.

    Enforces DEC-D "one active run per (tenant, deal)". The Postgres partial unique index
    ``ux_runs_one_active_per_deal`` (migration 0023) is the race-safe backstop; this pre-check
    surfaces the common case as a clean RUN_ALREADY_ACTIVE (409).
    """


# The partial unique index (migration 0023) enforcing one active run per (tenant, deal).
_ACTIVE_RUN_INDEX = "ux_runs_one_active_per_deal"


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
        source: dict[str, Any] | None = None,
        created_by_actor_id: str | None = None,
        created_by_actor_type: str | None = None,
    ) -> dict[str, Any]:
        """Create a new run record.

        Args:
            run_id: UUID string for the run.
            deal_id: UUID string for the deal.
            mode: Run mode (SNAPSHOT or FULL).
            idempotency_key: Optional idempotency key from request header.
            source: Optional run-source selection payload.
            created_by_actor_id: Authenticated actor that created the run.
            created_by_actor_type: Actor type (HUMAN or SERVICE) of the creator.

        Returns:
            Created run as dict.
        """
        if self.has_active_run(deal_id):
            raise RunAlreadyActiveError(deal_id)
        now = datetime.now(UTC)
        try:
            with self._conn.begin_nested():
                self._conn.execute(
                    text(
                        """
                        INSERT INTO runs
                            (run_id, tenant_id, deal_id, mode, status, started_at,
                             idempotency_key, source, created_at,
                             created_by_actor_id, created_by_actor_type)
                        VALUES
                            (:run_id, :tenant_id, :deal_id, :mode, 'QUEUED', :started_at,
                             :idempotency_key, CAST(:source AS JSONB), :created_at,
                             :created_by_actor_id, :created_by_actor_type)
                        """
                    ),
                    {
                        "run_id": run_id,
                        "tenant_id": self._tenant_id,
                        "deal_id": deal_id,
                        "mode": mode,
                        "started_at": now,
                        "idempotency_key": idempotency_key,
                        "source": json.dumps(source) if source is not None else None,
                        "created_at": now,
                        "created_by_actor_id": created_by_actor_id,
                        "created_by_actor_type": created_by_actor_type,
                    },
                )
        except IntegrityError as exc:
            # Concurrent race loser on the one-active-run partial unique index: the savepoint above
            # rolls back only this INSERT (the outer transaction stays usable) and we surface the
            # same safe RUN_ALREADY_ACTIVE the sequential pre-check does. Other constraint
            # violations (e.g. a duplicate run_id PK) propagate unchanged.
            if _ACTIVE_RUN_INDEX in str(exc.orig or exc):
                raise RunAlreadyActiveError(deal_id) from exc
            raise
        return {
            "run_id": run_id,
            "tenant_id": self._tenant_id,
            "deal_id": deal_id,
            "mode": mode,
            "status": "QUEUED",
            "started_at": now.isoformat().replace("+00:00", "Z"),
            "finished_at": None,
            "source": source,
            "created_at": now.isoformat().replace("+00:00", "Z"),
            "created_by_actor_id": created_by_actor_id,
            "created_by_actor_type": created_by_actor_type,
        }

    def has_active_run(self, deal_id: str) -> bool:
        """Return True when a QUEUED/RUNNING run already exists for the deal (RLS-scoped)."""
        result = self._conn.execute(
            text(
                """
                SELECT 1 FROM runs
                WHERE deal_id = :deal_id AND status IN ('QUEUED', 'RUNNING')
                LIMIT 1
                """
            ),
            {"deal_id": deal_id},
        )
        return result.fetchone() is not None

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
                       started_at, finished_at, source, created_at, cancel_requested_at,
                       created_by_actor_id, created_by_actor_type
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

    def list_by_deal(
        self,
        *,
        deal_id: str,
        limit: int,
        cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """List tenant-scoped runs for a deal, newest first, using a stable (created_at, run_id)
        composite cursor so rows sharing a created_at are never dropped across pages. RLS-scoped."""
        query = """
            SELECT run_id, tenant_id, deal_id, mode, status,
                   started_at, finished_at, source, created_at, cancel_requested_at,
                   created_by_actor_id, created_by_actor_type
            FROM runs
            WHERE deal_id = :deal_id
        """
        params: dict[str, Any] = {"deal_id": deal_id, "limit": limit + 1}
        decoded = _decode_run_cursor(cursor)
        if decoded is not None:
            params["cursor_created_at"], params["cursor_run_id"] = decoded
            query += (
                " AND (created_at < :cursor_created_at"
                " OR (created_at = :cursor_created_at AND run_id < :cursor_run_id))"
            )
        query += " ORDER BY created_at DESC, run_id DESC LIMIT :limit"
        rows = self._conn.execute(text(query), params).fetchall()
        items: list[dict[str, Any]] = []
        next_cursor: str | None = None
        for index, row in enumerate(rows):
            if index >= limit:
                if items:
                    next_cursor = _encode_run_cursor(items[-1]["created_at"], items[-1]["run_id"])
                break
            items.append(self._row_to_dict(row))
        return items, next_cursor

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

    def try_mark_running(self, run_id: str) -> bool:
        """Atomically transition a queued run to running.

        Returns:
            True when this caller claimed the run, False if it was already claimed
            or terminal.
        """
        now = datetime.now(UTC)
        result = self._conn.execute(
            text(
                """
                UPDATE runs
                SET status = 'RUNNING', started_at = :started_at
                WHERE run_id = :run_id AND status = 'QUEUED'
                RETURNING run_id
                """
            ),
            {"run_id": run_id, "started_at": now},
        )
        return result.fetchone() is not None

    def complete(
        self,
        run_id: str,
        *,
        status: str,
        finished_at: str | None,
    ) -> None:
        """Set a terminal run status and finished timestamp."""
        self.update_status(run_id, status=status, finished_at=finished_at)

    def try_complete_running(
        self,
        run_id: str,
        *,
        status: str,
        finished_at: str | None,
    ) -> bool:
        """Atomically set a terminal status only while the run is still RUNNING.

        Guards execution finalization against a cancellation that commits between the
        pre-complete check and the terminal write. Returns False if the run is no longer
        RUNNING (e.g. already CANCELLED), leaving the existing status untouched.
        """
        if finished_at is not None:
            result = self._conn.execute(
                text(
                    """
                    UPDATE runs
                    SET status = :status, finished_at = :finished_at
                    WHERE run_id = :run_id AND status = 'RUNNING'
                    RETURNING run_id
                    """
                ),
                {"run_id": run_id, "status": status, "finished_at": finished_at},
            )
        else:
            result = self._conn.execute(
                text(
                    """
                    UPDATE runs
                    SET status = :status
                    WHERE run_id = :run_id AND status = 'RUNNING'
                    RETURNING run_id
                    """
                ),
                {"run_id": run_id, "status": status},
            )
        return result.fetchone() is not None

    def try_complete_active(
        self,
        run_id: str,
        *,
        status: str,
        finished_at: str | None,
    ) -> bool:
        """Atomically set a terminal status only while the run is QUEUED or RUNNING.

        Used by the worker preflight blocker, which fails a run that has not yet been
        marked RUNNING. Returns False if the run is already terminal (e.g. CANCELLED
        after the claim batch released its row lock), leaving the status untouched.
        """
        if finished_at is not None:
            result = self._conn.execute(
                text(
                    """
                    UPDATE runs
                    SET status = :status, finished_at = :finished_at
                    WHERE run_id = :run_id AND status IN ('QUEUED', 'RUNNING')
                    RETURNING run_id
                    """
                ),
                {"run_id": run_id, "status": status, "finished_at": finished_at},
            )
        else:
            result = self._conn.execute(
                text(
                    """
                    UPDATE runs
                    SET status = :status
                    WHERE run_id = :run_id AND status IN ('QUEUED', 'RUNNING')
                    RETURNING run_id
                    """
                ),
                {"run_id": run_id, "status": status},
            )
        return result.fetchone() is not None

    def try_requeue_failed(self, run_id: str) -> bool:
        """Atomically transition a failed run back to queued."""
        result = self._conn.execute(
            text(
                """
                UPDATE runs
                SET status = 'QUEUED',
                    finished_at = NULL,
                    cancel_requested_at = NULL
                WHERE run_id = :run_id AND status = 'FAILED'
                RETURNING run_id
                """
            ),
            {"run_id": run_id},
        )
        return result.fetchone() is not None

    def try_cancel_active(self, run_id: str) -> bool:
        """Atomically transition QUEUED/RUNNING -> CANCELLED."""
        result = self._conn.execute(
            text(
                """
                UPDATE runs
                SET status = 'CANCELLED',
                    cancel_requested_at = NOW(),
                    finished_at = NOW()
                WHERE run_id = :run_id
                  AND status IN ('QUEUED', 'RUNNING')
                RETURNING run_id
                """
            ),
            {"run_id": run_id},
        )
        return result.fetchone() is not None

    def claim_queued_runs(self, *, limit: int = 10) -> list[dict[str, Any]]:
        """Return tenant-scoped queued run candidates with row locks.

        The caller still transitions each run to RUNNING through
        try_mark_running(), so API and worker paths share the same race guard.
        """
        result = self._conn.execute(
            text(
                """
                SELECT run_id, tenant_id, deal_id, mode, status,
                       started_at, finished_at, source, created_at, cancel_requested_at,
                       created_by_actor_id, created_by_actor_type
                FROM runs
                WHERE status = 'QUEUED'
                ORDER BY created_at ASC
                LIMIT :limit
                FOR UPDATE SKIP LOCKED
                """
            ),
            {"limit": limit},
        )
        return [self._row_to_dict(row) for row in result.fetchall()]

    def count_queued_runs(self) -> int:
        """Return the tenant-scoped count of QUEUED runs (queue depth; RLS enforced)."""
        result = self._conn.execute(text("SELECT COUNT(*) FROM runs WHERE status = 'QUEUED'"))
        row = result.fetchone()
        return int(row[0]) if row is not None else 0

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

        source = _row_value(row, "source")

        return {
            "run_id": str(row.run_id),
            "tenant_id": str(row.tenant_id),
            "deal_id": str(row.deal_id),
            "mode": row.mode,
            "status": row.status,
            "started_at": started_at,
            "finished_at": finished_at,
            "source": _json_value(source),
            "created_at": created_at,
            "cancel_requested_at": _iso_utc(_row_value(row, "cancel_requested_at")),
            "created_by_actor_id": _row_value(row, "created_by_actor_id"),
            "created_by_actor_type": _row_value(row, "created_by_actor_type"),
        }


_in_memory_runs_store: dict[str, dict[str, Any]] = {}
"""Global in-memory store keyed by run_id."""


_RUN_CURSOR_SEP = "|"


def _encode_run_cursor(created_at: str, run_id: str) -> str:
    """Encode a stable composite run-list cursor from (created_at, run_id)."""
    return f"{created_at}{_RUN_CURSOR_SEP}{run_id}"


def _decode_run_cursor(cursor: str | None) -> tuple[str, str] | None:
    """Decode a composite run cursor to (created_at, run_id); None if absent/malformed."""
    if not cursor:
        return None
    parts = cursor.split(_RUN_CURSOR_SEP, 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None
    return parts[0], parts[1]


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
        source: dict[str, Any] | None = None,
        created_by_actor_id: str | None = None,
        created_by_actor_type: str | None = None,
    ) -> dict[str, Any]:
        """Create a new run record in memory.

        Args:
            run_id: UUID string for the run.
            deal_id: UUID string for the deal.
            mode: Run mode (SNAPSHOT or FULL).
            idempotency_key: Optional idempotency key (stored but not enforced).
            source: Optional run-source selection payload.
            created_by_actor_id: Authenticated actor that created the run.
            created_by_actor_type: Actor type (HUMAN or SERVICE) of the creator.

        Returns:
            Created run as dict.
        """
        if self.has_active_run(deal_id):
            raise RunAlreadyActiveError(deal_id)
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        run = {
            "run_id": run_id,
            "tenant_id": self._tenant_id,
            "deal_id": deal_id,
            "mode": mode,
            "status": "QUEUED",
            "started_at": now,
            "finished_at": None,
            "source": dict(source) if source is not None else None,
            "created_at": now,
            "cancel_requested_at": None,
            "created_by_actor_id": created_by_actor_id,
            "created_by_actor_type": created_by_actor_type,
        }
        _in_memory_runs_store[run_id] = run
        return run

    def has_active_run(self, deal_id: str) -> bool:
        """Return True when a QUEUED/RUNNING run already exists for the deal (tenant-scoped)."""
        return any(
            run.get("tenant_id") == self._tenant_id
            and run.get("deal_id") == deal_id
            and run.get("status") in {"QUEUED", "RUNNING"}
            for run in _in_memory_runs_store.values()
        )

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

    def list_by_deal(
        self,
        *,
        deal_id: str,
        limit: int,
        cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """List tenant-scoped runs for a deal, newest first, using a stable (created_at, run_id)
        composite cursor so rows sharing a created_at are never dropped across pages."""
        matching = [
            run
            for run in _in_memory_runs_store.values()
            if run.get("tenant_id") == self._tenant_id and run.get("deal_id") == deal_id
        ]
        matching.sort(
            key=lambda run: (run.get("created_at") or "", run.get("run_id") or ""),
            reverse=True,
        )
        decoded = _decode_run_cursor(cursor)
        if decoded is not None:
            matching = [
                run
                for run in matching
                if (run.get("created_at") or "", run.get("run_id") or "") < decoded
            ]
        items: list[dict[str, Any]] = []
        next_cursor: str | None = None
        for index, run in enumerate(matching):
            if index >= limit:
                if items:
                    next_cursor = _encode_run_cursor(items[-1]["created_at"], items[-1]["run_id"])
                break
            items.append(dict(run))
        return items, next_cursor

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

    def try_mark_running(self, run_id: str) -> bool:
        """Atomically mark a queued in-memory run as running."""
        run = _in_memory_runs_store.get(run_id)
        if run is None or run.get("tenant_id") != self._tenant_id:
            return False
        if run.get("status") != "QUEUED":
            return False
        run["status"] = "RUNNING"
        run["started_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        _in_memory_runs_store[run_id] = run
        return True

    def complete(
        self,
        run_id: str,
        *,
        status: str,
        finished_at: str | None,
    ) -> None:
        """Set a terminal in-memory run status."""
        self.update_status(run_id, status=status, finished_at=finished_at)

    def try_complete_running(
        self,
        run_id: str,
        *,
        status: str,
        finished_at: str | None,
    ) -> bool:
        """Set a terminal status only while the in-memory run is still RUNNING.

        Mirrors the Postgres guard so execution finalization cannot overwrite a
        cancellation that won the race between the pre-complete check and the write.
        """
        run = _in_memory_runs_store.get(run_id)
        if run is None or run.get("tenant_id") != self._tenant_id:
            return False
        if run.get("status") != "RUNNING":
            return False
        run["status"] = status
        if finished_at is not None:
            run["finished_at"] = finished_at
        _in_memory_runs_store[run_id] = run
        return True

    def try_complete_active(
        self,
        run_id: str,
        *,
        status: str,
        finished_at: str | None,
    ) -> bool:
        """Set a terminal status only while the in-memory run is QUEUED or RUNNING.

        Mirrors the Postgres guard for the worker preflight blocker so a run cancelled
        after the claim batch released its lock is not overwritten.
        """
        run = _in_memory_runs_store.get(run_id)
        if run is None or run.get("tenant_id") != self._tenant_id:
            return False
        if run.get("status") not in {"QUEUED", "RUNNING"}:
            return False
        run["status"] = status
        if finished_at is not None:
            run["finished_at"] = finished_at
        _in_memory_runs_store[run_id] = run
        return True

    def try_requeue_failed(self, run_id: str) -> bool:
        """Atomically transition a failed in-memory run back to queued."""
        run = _in_memory_runs_store.get(run_id)
        if run is None or run.get("tenant_id") != self._tenant_id:
            return False
        if run.get("status") != "FAILED":
            return False
        run["status"] = "QUEUED"
        run["finished_at"] = None
        run["cancel_requested_at"] = None
        _in_memory_runs_store[run_id] = run
        return True

    def try_cancel_active(self, run_id: str) -> bool:
        """Atomically transition queued/running in-memory run to cancelled."""
        run = _in_memory_runs_store.get(run_id)
        if run is None or run.get("tenant_id") != self._tenant_id:
            return False
        if run.get("status") not in {"QUEUED", "RUNNING"}:
            return False
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        run["status"] = "CANCELLED"
        run["cancel_requested_at"] = now
        run["finished_at"] = now
        _in_memory_runs_store[run_id] = run
        return True

    def claim_queued_runs(self, *, limit: int = 10) -> list[dict[str, Any]]:
        """Return tenant-scoped queued in-memory run candidates."""
        queued = [
            run
            for run in _in_memory_runs_store.values()
            if run.get("tenant_id") == self._tenant_id and run.get("status") == "QUEUED"
        ]
        return sorted(queued, key=lambda item: item["created_at"])[:limit]

    def count_queued_runs(self) -> int:
        """Return the tenant-scoped count of QUEUED runs (queue depth)."""
        return sum(
            1
            for run in _in_memory_runs_store.values()
            if run.get("tenant_id") == self._tenant_id and run.get("status") == "QUEUED"
        )

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


def _json_value(value: Any) -> Any:
    """Return JSON/JSONB values as native Python data."""
    if value is None:
        return None
    if isinstance(value, str):
        return json.loads(value)
    return value


def _row_value(row: Any, key: str) -> Any:
    """Read optional row values without triggering MagicMock attribute creation."""
    mapping = getattr(row, "_mapping", None)
    if mapping is not None and key in mapping:
        return mapping[key]
    return getattr(row, key, None)


def _iso_utc(value: Any) -> str | None:
    """Convert optional datetime-like values to ISO UTC strings."""
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return str(isoformat()).replace("+00:00", "Z")
    return str(value)


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
