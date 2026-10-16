"""Runs repository for Postgres persistence and in-memory fallback.

Provides tenant-scoped CRUD operations for pipeline runs with RLS enforcement.
Extracted from inline SQL in api/routes/runs.py for Phase 7.A persistence cutover.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from idis.persistence.db import is_postgres_configured, set_tenant_local
from idis.pipeline.contract_registry import FULL_STRICT_V21_CONTRACT_HASH

if TYPE_CHECKING:
    from sqlalchemy import Connection

logger = logging.getLogger(__name__)


class RunAlreadyActiveError(Exception):
    """Raised on run creation when an active (QUEUED/RUNNING) run already exists for the deal.

    Enforces DEC-D "one active run per (tenant, deal)". The Postgres partial unique index
    ``ux_runs_one_active_per_deal`` (migration 0023) is the race-safe backstop; this pre-check
    surfaces the common case as a clean RUN_ALREADY_ACTIVE (409).
    """


class WorkerLeaseError(RuntimeError):
    """Base error for a durable worker-lease contract violation."""

    code = "WORKER_LEASE_ERROR"


class WorkerLeaseFencedError(WorkerLeaseError):
    """Raised when a stale or expired worker attempts a fenced mutation."""

    code = "WORKER_LEASE_FENCED"


class RunScopeNotFoundError(LookupError):
    """Raised when an exact tenant/deal/run scope is not durably visible."""


# The partial unique index (migration 0023) enforcing one active run per (tenant, deal).
_ACTIVE_RUN_INDEX = "ux_runs_one_active_per_deal"
_LEGACY_COMPAT_CONTRACT_HASH = "6365adbfe07f1818fe9e8e634587feb59a6bda5238b625481940d9ea9efce723"
_LEGACY_RESUME_ERROR = "LEGACY_RUN_RESUME_UNSAFE"
_RUN_RESUME_STATES = frozenset({"RESUMABLE", "HISTORICAL_TERMINAL", "NON_RESUMABLE"})


def _resolve_run_contract_identity(
    *,
    mode: str,
    pipeline_contract_version: str | None,
    pipeline_contract_hash: str | None,
    pipeline_resume_state: str | None,
    pipeline_resume_error_code: str | None,
) -> tuple[str, str, str, str | None]:
    """Validate an explicit identity or select the closed legacy compatibility identity.

    The compatibility branch preserves pre-Task-7 repository callers while
    classifying their newly created rows as non-resumable. Strict admission
    always supplies all four values and never enters this branch.
    """
    if (
        pipeline_contract_version is None
        and pipeline_contract_hash is None
        and pipeline_resume_state is None
        and pipeline_resume_error_code is None
    ):
        legacy_version = "snapshot-v1" if mode == "SNAPSHOT" else "full-v1"
        return (
            legacy_version,
            _LEGACY_COMPAT_CONTRACT_HASH,
            "NON_RESUMABLE",
            _LEGACY_RESUME_ERROR,
        )

    if (
        pipeline_contract_version is None
        or pipeline_contract_hash is None
        or pipeline_resume_state is None
    ):
        raise ValueError("run contract identity must be supplied as one complete tuple")
    if not pipeline_contract_version.strip():
        raise ValueError("pipeline_contract_version must be non-empty")
    if len(pipeline_contract_hash) != 64 or any(
        char not in "0123456789abcdef" for char in pipeline_contract_hash
    ):
        raise ValueError("pipeline_contract_hash must be lowercase SHA-256 hex")
    if pipeline_resume_state not in _RUN_RESUME_STATES:
        raise ValueError("pipeline_resume_state is not recognized")
    if pipeline_resume_state == "NON_RESUMABLE":
        if pipeline_resume_error_code != _LEGACY_RESUME_ERROR:
            raise ValueError("NON_RESUMABLE runs require LEGACY_RUN_RESUME_UNSAFE")
    elif pipeline_resume_error_code is not None:
        raise ValueError("resumable or historical runs cannot carry a resume error")
    return (
        pipeline_contract_version,
        pipeline_contract_hash,
        pipeline_resume_state,
        pipeline_resume_error_code,
    )


def _resolve_worker_claim_contract_filter(
    *,
    lease_owner_id: str | None,
    required_pipeline_contract_version: str | None,
    required_pipeline_contract_hash: str | None,
    required_pipeline_resume_state: str | None,
) -> tuple[str | None, str | None, str | None]:
    values = (
        required_pipeline_contract_version,
        required_pipeline_contract_hash,
        required_pipeline_resume_state,
    )
    if all(value is None for value in values):
        return (None, None, None)
    if any(value is None for value in values):
        raise ValueError("worker claim contract filter must be supplied as one complete tuple")
    if lease_owner_id is None:
        raise ValueError("worker claim contract filter requires a durable worker owner")
    assert required_pipeline_contract_version is not None
    assert required_pipeline_contract_hash is not None
    assert required_pipeline_resume_state is not None
    if (
        not required_pipeline_contract_version.strip()
        or len(required_pipeline_contract_hash) != 64
        or any(char not in "0123456789abcdef" for char in required_pipeline_contract_hash)
        or required_pipeline_resume_state != "RESUMABLE"
    ):
        raise ValueError("worker claim contract filter is invalid")
    return (
        required_pipeline_contract_version,
        required_pipeline_contract_hash,
        required_pipeline_resume_state,
    )


class PostgresRunsRepository:
    """Tenant-scoped Postgres repository for pipeline run records.

    All operations enforce RLS via SET LOCAL idis.tenant_id.

    Args:
        conn: SQLAlchemy connection (must be in a transaction).
        tenant_id: Tenant UUID string for RLS scoping.
    """

    is_durable = True
    backend = "postgresql"

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
        pipeline_contract_version: str | None = None,
        pipeline_contract_hash: str | None = None,
        pipeline_resume_state: str | None = None,
        pipeline_resume_error_code: str | None = None,
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
        (
            pipeline_contract_version,
            pipeline_contract_hash,
            pipeline_resume_state,
            pipeline_resume_error_code,
        ) = _resolve_run_contract_identity(
            mode=mode,
            pipeline_contract_version=pipeline_contract_version,
            pipeline_contract_hash=pipeline_contract_hash,
            pipeline_resume_state=pipeline_resume_state,
            pipeline_resume_error_code=pipeline_resume_error_code,
        )
        now = datetime.now(UTC)
        try:
            with self._conn.begin_nested():
                self._conn.execute(
                    text(
                        """
                        INSERT INTO runs
                            (run_id, tenant_id, deal_id, mode, status, started_at,
                             idempotency_key, source, created_at,
                             created_by_actor_id, created_by_actor_type,
                             pipeline_contract_version, pipeline_contract_hash,
                             pipeline_resume_state, pipeline_resume_error_code)
                        VALUES
                            (:run_id, :tenant_id, :deal_id, :mode, 'QUEUED', :started_at,
                             :idempotency_key, CAST(:source AS JSONB), :created_at,
                             :created_by_actor_id, :created_by_actor_type,
                             :pipeline_contract_version, :pipeline_contract_hash,
                             :pipeline_resume_state, :pipeline_resume_error_code)
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
                        "pipeline_contract_version": pipeline_contract_version,
                        "pipeline_contract_hash": pipeline_contract_hash,
                        "pipeline_resume_state": pipeline_resume_state,
                        "pipeline_resume_error_code": pipeline_resume_error_code,
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
            "strict_preflight_proof_id": None,
            "lease_owner_id": None,
            "lease_fencing_token": 0,
            "lease_expires_at": None,
            "last_heartbeat_at": None,
            "pipeline_contract_version": pipeline_contract_version,
            "pipeline_contract_hash": pipeline_contract_hash,
            "pipeline_resume_state": pipeline_resume_state,
            "pipeline_resume_error_code": pipeline_resume_error_code,
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
                       created_by_actor_id, created_by_actor_type,
                       strict_preflight_proof_id, lease_owner_id,
                       lease_fencing_token, lease_expires_at, last_heartbeat_at,
                       pipeline_contract_version, pipeline_contract_hash,
                       pipeline_resume_state, pipeline_resume_error_code
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

    def require_run_scope(self, *, tenant_id: str, deal_id: str, run_id: str) -> None:
        """Require one exact tenant-scoped run without exposing foreign-row existence."""
        if tenant_id != self._tenant_id:
            raise RunScopeNotFoundError
        run = self.get(run_id)
        if run is None or str(run.get("deal_id")) != deal_id:
            raise RunScopeNotFoundError

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
                   created_by_actor_id, created_by_actor_type,
                   strict_preflight_proof_id, lease_owner_id,
                   lease_fencing_token, lease_expires_at, last_heartbeat_at,
                   pipeline_contract_version, pipeline_contract_hash,
                   pipeline_resume_state, pipeline_resume_error_code
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
        if status == "RUNNING":
            raise WorkerLeaseFencedError(
                "legacy status update cannot claim RUNNING; use the atomic lease claim"
            )
        if finished_at is not None:
            result = self._conn.execute(
                text(
                    """
                    UPDATE runs
                    SET status = :status,
                        finished_at = :finished_at,
                        lease_owner_id = NULL,
                        lease_expires_at = NULL
                    WHERE run_id = :run_id
                      AND NOT (
                          status = 'RUNNING'
                          AND lease_owner_id IS NOT NULL
                      )
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
                    SET status = :status,
                        lease_owner_id = NULL,
                        lease_expires_at = NULL
                    WHERE run_id = :run_id
                      AND NOT (
                          status = 'RUNNING'
                          AND lease_owner_id IS NOT NULL
                      )
                    RETURNING run_id
                    """
                ),
                {"run_id": run_id, "status": status},
            )
        if result.rowcount != 1:
            raise WorkerLeaseFencedError(
                "unfenced status update cannot mutate a leased RUNNING row"
            )

    def try_mark_running(self, run_id: str) -> bool:
        """Reject the retired select-then-mark claim surface with a stable lease code."""
        _ = run_id
        raise WorkerLeaseFencedError(
            "legacy try_mark_running is retired; use the atomic worker lease claim"
        )

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
        lease_owner_id: str | None = None,
        lease_fencing_token: int | None = None,
    ) -> bool:
        """Atomically set a terminal status only while the run is still RUNNING.

        Guards execution finalization against a cancellation that commits between the
        pre-complete check and the terminal write. Returns False if the run is no longer
        RUNNING (e.g. already CANCELLED), leaving the existing status untouched.
        """
        if (lease_owner_id is None) != (lease_fencing_token is None):
            raise ValueError("lease owner and fencing token must be supplied together")

        if lease_owner_id is not None and lease_fencing_token is not None:
            result = self._conn.execute(
                text(
                    """
                    UPDATE runs
                    SET status = :status,
                        finished_at = :finished_at,
                        lease_owner_id = NULL,
                        lease_expires_at = NULL
                    WHERE tenant_id = :tenant_id
                      AND run_id = :run_id
                      AND status = 'RUNNING'
                      AND lease_owner_id = :lease_owner_id
                      AND lease_fencing_token = :lease_fencing_token
                      AND lease_expires_at > statement_timestamp()
                    RETURNING run_id
                    """
                ),
                {
                    "tenant_id": self._tenant_id,
                    "run_id": run_id,
                    "status": status,
                    "finished_at": finished_at,
                    "lease_owner_id": lease_owner_id,
                    "lease_fencing_token": lease_fencing_token,
                },
            )
        elif finished_at is not None:
            result = self._conn.execute(
                text(
                    """
                    UPDATE runs
                    SET status = :status,
                        finished_at = :finished_at,
                        lease_owner_id = NULL,
                        lease_expires_at = NULL
                    WHERE run_id = :run_id
                      AND status = 'RUNNING'
                      AND lease_owner_id IS NULL
                      AND lease_expires_at IS NULL
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
                    SET status = :status,
                        lease_owner_id = NULL,
                        lease_expires_at = NULL
                    WHERE run_id = :run_id
                      AND status = 'RUNNING'
                      AND lease_owner_id IS NULL
                      AND lease_expires_at IS NULL
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
                    SET status = :status, finished_at = :finished_at,
                        lease_owner_id = NULL, lease_expires_at = NULL
                    WHERE run_id = :run_id
                      AND status IN ('QUEUED', 'RUNNING')
                      AND (
                          status = 'QUEUED'
                          OR (lease_owner_id IS NULL AND lease_expires_at IS NULL)
                      )
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
                    SET status = :status,
                        lease_owner_id = NULL, lease_expires_at = NULL
                    WHERE run_id = :run_id
                      AND status IN ('QUEUED', 'RUNNING')
                      AND (
                          status = 'QUEUED'
                          OR (lease_owner_id IS NULL AND lease_expires_at IS NULL)
                      )
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
                    cancel_requested_at = NULL,
                    lease_owner_id = NULL,
                    lease_expires_at = NULL
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
                    cancel_requested_at = statement_timestamp(),
                    finished_at = statement_timestamp(),
                    lease_owner_id = NULL,
                    lease_expires_at = NULL
                WHERE tenant_id = :tenant_id
                  AND run_id = :run_id
                  AND status IN ('QUEUED', 'RUNNING')
                RETURNING run_id
                """
            ),
            {"tenant_id": self._tenant_id, "run_id": run_id},
        )
        return result.fetchone() is not None

    def claim_queued_runs(
        self,
        *,
        limit: int = 10,
        lease_owner_id: str | None = None,
        lease_seconds: int | None = None,
        lease_audit_emitter: Callable[..., None] | None = None,
        required_pipeline_contract_version: str | None = None,
        required_pipeline_contract_hash: str | None = None,
        required_pipeline_resume_state: str | None = None,
    ) -> list[dict[str, Any]]:
        """Inspect queued rows or atomically claim one with a durable lease.

        Supplying ``lease_owner_id`` selects the production worker path.  Its
        single UPDATE chooses one locked QUEUED row and commits RUNNING plus all
        lease state together, closing the historical select-then-mark window.
        The owner-less branch remains a compatibility queue-inspection API for
        non-worker callers and never transitions state.
        """
        (
            required_pipeline_contract_version,
            required_pipeline_contract_hash,
            required_pipeline_resume_state,
        ) = _resolve_worker_claim_contract_filter(
            lease_owner_id=lease_owner_id,
            required_pipeline_contract_version=required_pipeline_contract_version,
            required_pipeline_contract_hash=required_pipeline_contract_hash,
            required_pipeline_resume_state=required_pipeline_resume_state,
        )
        if lease_owner_id is None:
            if lease_seconds is not None:
                raise ValueError("lease_seconds requires lease_owner_id")
            result = self._conn.execute(
                text(
                    """
                    SELECT run_id, tenant_id, deal_id, mode, status,
                           started_at, finished_at, source, created_at,
                           cancel_requested_at, created_by_actor_id,
                           created_by_actor_type, strict_preflight_proof_id,
                           lease_owner_id, lease_fencing_token,
                           lease_expires_at, last_heartbeat_at,
                           pipeline_contract_version, pipeline_contract_hash,
                           pipeline_resume_state, pipeline_resume_error_code
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

        if limit != 1:
            raise ValueError("durable worker claim requires limit=1")
        if lease_seconds is None or lease_seconds <= 0:
            raise ValueError("lease_seconds must be a positive integer")

        result = self._conn.execute(
            text(
                """
                WITH candidate AS (
                    SELECT queued.run_id
                    FROM runs AS queued
                    WHERE queued.tenant_id = :tenant_id
                      AND queued.status = :queued_status
                      AND queued.pipeline_contract_version
                          <> :superseded_full_strict_v16_version
                      AND queued.pipeline_contract_version
                          <> :superseded_full_strict_v17_version
                      AND queued.pipeline_contract_version
                          <> :superseded_full_strict_v18_version
                      AND queued.pipeline_contract_version
                          <> :superseded_full_strict_v20_version
                      AND (
                          :required_pipeline_contract_version IS NULL
                          OR (
                              queued.pipeline_contract_version =
                                  :required_pipeline_contract_version
                              AND queued.pipeline_contract_hash =
                                  :required_pipeline_contract_hash
                              AND queued.pipeline_resume_state =
                                  :required_pipeline_resume_state
                          )
                      )
                      AND (
                          queued.pipeline_contract_version
                              <> :full_strict_v21_version
                          OR (
                              queued.pipeline_contract_hash
                                  = :full_strict_v21_hash
                              AND queued.pipeline_resume_state = 'RESUMABLE'
                              AND EXISTS (
                                  SELECT 1
                                  FROM run_input_bindings AS package_binding
                                  JOIN data_room_packages AS package
                                    ON package.tenant_id =
                                       package_binding.tenant_id
                                   AND package.deal_id =
                                       package_binding.deal_id
                                   AND package.package_id =
                                       package_binding.corpus_receipt_id
                                  WHERE package_binding.tenant_id =
                                        queued.tenant_id
                                    AND package_binding.deal_id =
                                        queued.deal_id
                                    AND package_binding.run_id =
                                        queued.run_id
                                    AND package_binding.source_object_kind =
                                        'PACKAGE'
                                    AND package_binding.source_disposition =
                                        'CORPUS'
                                    AND package_binding.binding_purpose =
                                        'INVENTORY'
                                    AND package.status = 'SEALED'
                                    AND package.source_binding_state = 'BOUND'
                              )
                              AND NOT EXISTS (
                                  SELECT 1
                                  FROM run_input_bindings AS binding
                                  WHERE binding.tenant_id = queued.tenant_id
                                    AND binding.deal_id = queued.deal_id
                                    AND binding.run_id = queued.run_id
                                    AND NOT EXISTS (
                                        SELECT 1
                                        FROM run_input_bindings AS package_binding
                                        WHERE package_binding.tenant_id =
                                              binding.tenant_id
                                          AND package_binding.deal_id =
                                              binding.deal_id
                                          AND package_binding.run_id =
                                              binding.run_id
                                          AND package_binding.corpus_receipt_id =
                                              binding.corpus_receipt_id
                                          AND package_binding.source_object_kind =
                                              'PACKAGE'
                                    )
                              )
                              AND NOT EXISTS (
                                  SELECT 1
                                  FROM source_lineage_objects AS source
                                  WHERE source.tenant_id = queued.tenant_id
                                    AND source.deal_id = queued.deal_id
                                    AND EXISTS (
                                        SELECT 1
                                        FROM run_input_bindings AS package_binding
                                        WHERE package_binding.tenant_id =
                                              queued.tenant_id
                                          AND package_binding.deal_id =
                                              queued.deal_id
                                          AND package_binding.run_id =
                                              queued.run_id
                                          AND package_binding.source_object_kind =
                                              'PACKAGE'
                                          AND package_binding.corpus_receipt_id =
                                              source.corpus_receipt_id
                                    )
                                    AND NOT EXISTS (
                                        SELECT 1
                                        FROM run_input_bindings AS binding
                                        WHERE binding.tenant_id =
                                              source.tenant_id
                                          AND binding.deal_id =
                                              source.deal_id
                                          AND binding.run_id = queued.run_id
                                          AND binding.source_lineage_object_id =
                                              source.source_lineage_object_id
                                    )
                              )
                          )
                      )
                    ORDER BY queued.created_at ASC
                    LIMIT :limit
                    FOR UPDATE SKIP LOCKED
                )
                UPDATE runs AS claimed
                SET status = :running_status,
                    lease_owner_id = :lease_owner_id,
                    lease_fencing_token = claimed.lease_fencing_token + 1,
                    lease_expires_at = statement_timestamp()
                        + make_interval(secs => :lease_seconds),
                    last_heartbeat_at = statement_timestamp(),
                    started_at = COALESCE(claimed.started_at, statement_timestamp())
                FROM candidate
                WHERE claimed.run_id = candidate.run_id
                  AND claimed.tenant_id = :tenant_id
                RETURNING claimed.run_id, claimed.tenant_id, claimed.deal_id,
                          claimed.mode, claimed.status, claimed.started_at,
                          claimed.finished_at, claimed.source, claimed.created_at,
                          claimed.cancel_requested_at, claimed.created_by_actor_id,
                          claimed.created_by_actor_type,
                          claimed.strict_preflight_proof_id,
                          claimed.lease_owner_id, claimed.lease_fencing_token,
                          claimed.lease_expires_at, claimed.last_heartbeat_at,
                          claimed.pipeline_contract_version,
                          claimed.pipeline_contract_hash,
                          claimed.pipeline_resume_state,
                          claimed.pipeline_resume_error_code
                """
            ),
            {
                "tenant_id": self._tenant_id,
                "queued_status": "QUEUED",
                "running_status": "RUNNING",
                "limit": 1,
                "lease_owner_id": lease_owner_id,
                "lease_seconds": lease_seconds,
                "required_pipeline_contract_version": (required_pipeline_contract_version),
                "required_pipeline_contract_hash": required_pipeline_contract_hash,
                "required_pipeline_resume_state": required_pipeline_resume_state,
                "superseded_full_strict_v16_version": "full-strict-v16",
                "superseded_full_strict_v17_version": "full-strict-v17",
                "superseded_full_strict_v18_version": "full-strict-v18",
                "superseded_full_strict_v20_version": "full-strict-v20",
                "full_strict_v21_version": "full-strict-v21",
                "full_strict_v21_hash": FULL_STRICT_V21_CONTRACT_HASH,
            },
        )
        rows = [self._row_to_dict(row) for row in result.fetchall()]
        if rows and lease_audit_emitter is not None:
            claimed = rows[0]
            lease_audit_emitter(
                db_conn=self._conn,
                code="WORKER_LEASE_CLAIMED",
                run_id=claimed["run_id"],
                tenant_id=claimed["tenant_id"],
                lease_owner_id=claimed["lease_owner_id"],
                new_fencing_token=claimed["lease_fencing_token"],
                lease_expires_at=claimed["lease_expires_at"],
            )
        return rows

    def renew_worker_lease(
        self,
        *,
        run_id: str,
        lease_owner_id: str,
        lease_fencing_token: int,
        lease_seconds: int,
        lease_audit_emitter: Callable[..., None] | None = None,
    ) -> bool:
        """Renew an unexpired lease only for its exact current owner and token."""
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be a positive integer")
        result = self._conn.execute(
            text(
                """
                UPDATE runs
                SET last_heartbeat_at = statement_timestamp(),
                    lease_expires_at = statement_timestamp()
                        + make_interval(secs => :lease_seconds)
                WHERE tenant_id = :tenant_id
                  AND run_id = :run_id
                  AND status = :running_status
                  AND lease_owner_id = :lease_owner_id
                  AND lease_fencing_token = :lease_fencing_token
                  AND lease_expires_at > statement_timestamp()
                RETURNING run_id, tenant_id, lease_expires_at
                """
            ),
            {
                "tenant_id": self._tenant_id,
                "run_id": run_id,
                "running_status": "RUNNING",
                "lease_owner_id": lease_owner_id,
                "lease_fencing_token": lease_fencing_token,
                "lease_seconds": lease_seconds,
            },
        )
        row = result.fetchone()
        if row is None:
            return False
        if lease_audit_emitter is not None:
            lease_audit_emitter(
                db_conn=self._conn,
                code="WORKER_LEASE_HEARTBEAT_RENEWED",
                run_id=str(_row_value(row, "run_id")),
                tenant_id=str(_row_value(row, "tenant_id")),
                lease_owner_id=lease_owner_id,
                current_fencing_token=lease_fencing_token,
                lease_expires_at=_iso_utc(_row_value(row, "lease_expires_at")),
            )
        return True

    def require_active_worker_lease(
        self,
        *,
        deal_id: str,
        run_id: str,
        lease_owner_id: str,
        lease_fencing_token: int,
    ) -> None:
        """Lock and validate the exact live lease for an output-write transaction."""
        if (
            not isinstance(lease_owner_id, str)
            or not lease_owner_id.strip()
            or not isinstance(lease_fencing_token, int)
            or isinstance(lease_fencing_token, bool)
            or lease_fencing_token <= 0
        ):
            raise WorkerLeaseFencedError("worker output write has no valid lease identity")
        row = self._conn.execute(
            text(
                """
                SELECT run_id
                FROM runs
                WHERE tenant_id = :tenant_id
                  AND deal_id = :deal_id
                  AND run_id = :run_id
                  AND status = 'RUNNING'
                  AND lease_owner_id = :lease_owner_id
                  AND lease_fencing_token = :lease_fencing_token
                  AND lease_expires_at > statement_timestamp()
                FOR UPDATE
                """
            ),
            {
                "tenant_id": self._tenant_id,
                "deal_id": deal_id,
                "run_id": run_id,
                "lease_owner_id": lease_owner_id,
                "lease_fencing_token": lease_fencing_token,
            },
        ).fetchone()
        if row is None:
            raise WorkerLeaseFencedError("worker output write was fenced")

    def requeue_expired_worker_leases(
        self,
        *,
        limit: int = 1,
        lease_audit_emitter: Callable[..., None] | None = None,
    ) -> list[dict[str, Any]]:
        """Atomically move expired RUNNING leases back to QUEUED.

        Requeue increments the token once and clears ownership/expiry.  A later
        ordinary claim increments it again, making both the expiry transition
        and the new owner independently fenced epochs.
        """
        if limit <= 0:
            raise ValueError("limit must be positive")
        result = self._conn.execute(
            text(
                """
                WITH expired AS (
                    SELECT run_id,
                           lease_owner_id AS prior_lease_owner_id,
                           lease_fencing_token AS prior_fencing_token
                    FROM runs
                    WHERE tenant_id = :tenant_id
                      AND status = :running_status
                      AND lease_expires_at <= statement_timestamp()
                    ORDER BY lease_expires_at ASC, created_at ASC
                    LIMIT :limit
                    FOR UPDATE SKIP LOCKED
                )
                UPDATE runs AS reclaimed
                SET status = :queued_status,
                    lease_owner_id = NULL,
                    lease_expires_at = NULL,
                    lease_fencing_token = reclaimed.lease_fencing_token + 1
                FROM expired
                WHERE reclaimed.run_id = expired.run_id
                  AND reclaimed.tenant_id = :tenant_id
                RETURNING reclaimed.run_id, reclaimed.tenant_id,
                          reclaimed.deal_id, reclaimed.mode, reclaimed.status,
                          reclaimed.started_at, reclaimed.finished_at,
                          reclaimed.source, reclaimed.created_at,
                          reclaimed.cancel_requested_at,
                          reclaimed.created_by_actor_id,
                          reclaimed.created_by_actor_type,
                          reclaimed.strict_preflight_proof_id,
                          reclaimed.lease_owner_id,
                          reclaimed.lease_fencing_token,
                          reclaimed.lease_expires_at,
                          reclaimed.last_heartbeat_at,
                          reclaimed.pipeline_contract_version,
                          reclaimed.pipeline_contract_hash,
                          reclaimed.pipeline_resume_state,
                          reclaimed.pipeline_resume_error_code,
                          expired.prior_lease_owner_id,
                          expired.prior_fencing_token
                """
            ),
            {
                "tenant_id": self._tenant_id,
                "running_status": "RUNNING",
                "queued_status": "QUEUED",
                "limit": limit,
            },
        )
        raw_rows = result.fetchall()
        rows = [self._row_to_dict(row) for row in raw_rows]
        if lease_audit_emitter is not None:
            for raw, requeued in zip(raw_rows, rows, strict=True):
                event = {
                    "db_conn": self._conn,
                    "run_id": requeued["run_id"],
                    "tenant_id": requeued["tenant_id"],
                    "prior_lease_owner_id": str(_row_value(raw, "prior_lease_owner_id")),
                    "prior_fencing_token": int(_row_value(raw, "prior_fencing_token")),
                    "new_fencing_token": requeued["lease_fencing_token"],
                }
                lease_audit_emitter(code="WORKER_LEASE_EXPIRED", **event)
                lease_audit_emitter(code="WORKER_LEASE_RECLAIMED", **event)
        return rows

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
        strict_preflight_proof_id = _row_value(row, "strict_preflight_proof_id")
        lease_owner_id = _row_value(row, "lease_owner_id")
        lease_fencing_token = _row_value(row, "lease_fencing_token")
        pipeline_contract_version = _row_value(row, "pipeline_contract_version")
        pipeline_contract_hash = _row_value(row, "pipeline_contract_hash")
        pipeline_resume_state = _row_value(row, "pipeline_resume_state")
        pipeline_resume_error_code = _row_value(row, "pipeline_resume_error_code")
        if (
            pipeline_contract_version is None
            and pipeline_contract_hash is None
            and pipeline_resume_state is None
            and pipeline_resume_error_code is None
        ):
            (
                pipeline_contract_version,
                pipeline_contract_hash,
                pipeline_resume_state,
                pipeline_resume_error_code,
            ) = _resolve_run_contract_identity(
                mode=str(row.mode),
                pipeline_contract_version=None,
                pipeline_contract_hash=None,
                pipeline_resume_state=None,
                pipeline_resume_error_code=None,
            )

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
            "strict_preflight_proof_id": (
                str(strict_preflight_proof_id) if strict_preflight_proof_id is not None else None
            ),
            "lease_owner_id": str(lease_owner_id) if lease_owner_id is not None else None,
            "lease_fencing_token": int(lease_fencing_token or 0),
            "lease_expires_at": _iso_utc(_row_value(row, "lease_expires_at")),
            "last_heartbeat_at": _iso_utc(_row_value(row, "last_heartbeat_at")),
            "pipeline_contract_version": str(pipeline_contract_version),
            "pipeline_contract_hash": str(pipeline_contract_hash),
            "pipeline_resume_state": str(pipeline_resume_state),
            "pipeline_resume_error_code": pipeline_resume_error_code,
        }


_in_memory_runs_store: dict[str, dict[str, Any]] = {}
"""Global in-memory store keyed by run_id."""


def assert_in_memory_worker_lease(
    *,
    tenant_id: str,
    run_id: str,
    lease_owner_id: str,
    lease_fencing_token: int,
) -> None:
    """Fail closed unless an in-memory write carries the live run lease.

    This is the shared persistence-twin guard used by run and run-step writes;
    keeping it here prevents other repositories from reaching into the global
    run store or drifting from the canonical owner/token/expiry predicates.
    """
    run = _in_memory_runs_store.get(run_id)
    now = datetime.now(UTC)
    if (
        run is None
        or run.get("tenant_id") != tenant_id
        or run.get("status") != "RUNNING"
        or run.get("lease_owner_id") != lease_owner_id
        or int(run.get("lease_fencing_token") or 0) != lease_fencing_token
        or _parse_utc(run.get("lease_expires_at")) <= now
    ):
        raise WorkerLeaseFencedError("worker lease is stale, expired, or no longer active")


def assert_in_memory_ownerless_write_allowed(*, tenant_id: str, run_id: str) -> None:
    """Reject an ownerless persistence write only when its parent has a live lease."""
    run = _in_memory_runs_store.get(run_id)
    if (
        run is not None
        and run.get("tenant_id") == tenant_id
        and run.get("status") == "RUNNING"
        and run.get("lease_owner_id") is not None
    ):
        raise WorkerLeaseFencedError("ownerless write cannot mutate a leased RUNNING row")


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

    is_durable = False
    backend = "memory"

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
        pipeline_contract_version: str | None = None,
        pipeline_contract_hash: str | None = None,
        pipeline_resume_state: str | None = None,
        pipeline_resume_error_code: str | None = None,
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
        (
            pipeline_contract_version,
            pipeline_contract_hash,
            pipeline_resume_state,
            pipeline_resume_error_code,
        ) = _resolve_run_contract_identity(
            mode=mode,
            pipeline_contract_version=pipeline_contract_version,
            pipeline_contract_hash=pipeline_contract_hash,
            pipeline_resume_state=pipeline_resume_state,
            pipeline_resume_error_code=pipeline_resume_error_code,
        )
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
            "strict_preflight_proof_id": None,
            "lease_owner_id": None,
            "lease_fencing_token": 0,
            "lease_expires_at": None,
            "last_heartbeat_at": None,
            "pipeline_contract_version": pipeline_contract_version,
            "pipeline_contract_hash": pipeline_contract_hash,
            "pipeline_resume_state": pipeline_resume_state,
            "pipeline_resume_error_code": pipeline_resume_error_code,
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
        if status == "RUNNING":
            raise WorkerLeaseFencedError(
                "legacy status update cannot claim RUNNING; use the atomic lease claim"
            )
        run = _in_memory_runs_store.get(run_id)
        if run is None or run.get("tenant_id") != self._tenant_id:
            return
        assert_in_memory_ownerless_write_allowed(
            tenant_id=self._tenant_id,
            run_id=run_id,
        )
        run["status"] = status
        if finished_at is not None:
            run["finished_at"] = finished_at
        run["lease_owner_id"] = None
        run["lease_expires_at"] = None
        _in_memory_runs_store[run_id] = run

    def try_mark_running(self, run_id: str) -> bool:
        """Reject the retired select-then-mark claim surface with a stable lease code."""
        _ = run_id
        raise WorkerLeaseFencedError(
            "legacy try_mark_running is retired; use the atomic worker lease claim"
        )

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
        lease_owner_id: str | None = None,
        lease_fencing_token: int | None = None,
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
        if (lease_owner_id is None) != (lease_fencing_token is None):
            raise ValueError("lease owner and fencing token must be supplied together")
        if lease_owner_id is not None and lease_fencing_token is not None:
            assert_in_memory_worker_lease(
                tenant_id=self._tenant_id,
                run_id=run_id,
                lease_owner_id=lease_owner_id,
                lease_fencing_token=lease_fencing_token,
            )
        else:
            try:
                assert_in_memory_ownerless_write_allowed(
                    tenant_id=self._tenant_id,
                    run_id=run_id,
                )
            except WorkerLeaseFencedError:
                return False
        run["status"] = status
        if finished_at is not None:
            run["finished_at"] = finished_at
        run["lease_owner_id"] = None
        run["lease_expires_at"] = None
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
        if run.get("status") == "RUNNING":
            try:
                assert_in_memory_ownerless_write_allowed(
                    tenant_id=self._tenant_id,
                    run_id=run_id,
                )
            except WorkerLeaseFencedError:
                return False
        run["status"] = status
        if finished_at is not None:
            run["finished_at"] = finished_at
        run["lease_owner_id"] = None
        run["lease_expires_at"] = None
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
        run["lease_owner_id"] = None
        run["lease_expires_at"] = None
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
        run["lease_owner_id"] = None
        run["lease_expires_at"] = None
        _in_memory_runs_store[run_id] = run
        return True

    def claim_queued_runs(
        self,
        *,
        limit: int = 10,
        lease_owner_id: str | None = None,
        lease_seconds: int | None = None,
        lease_audit_emitter: Callable[..., None] | None = None,
        required_pipeline_contract_version: str | None = None,
        required_pipeline_contract_hash: str | None = None,
        required_pipeline_resume_state: str | None = None,
    ) -> list[dict[str, Any]]:
        """Inspect queued rows or atomically claim one in the persistence twin."""
        (
            required_pipeline_contract_version,
            required_pipeline_contract_hash,
            required_pipeline_resume_state,
        ) = _resolve_worker_claim_contract_filter(
            lease_owner_id=lease_owner_id,
            required_pipeline_contract_version=required_pipeline_contract_version,
            required_pipeline_contract_hash=required_pipeline_contract_hash,
            required_pipeline_resume_state=required_pipeline_resume_state,
        )
        queued = [
            run
            for run in _in_memory_runs_store.values()
            if run.get("tenant_id") == self._tenant_id
            and run.get("status") == "QUEUED"
            and (
                required_pipeline_contract_version is None
                or (
                    run.get("pipeline_contract_version") == required_pipeline_contract_version
                    and run.get("pipeline_contract_hash") == required_pipeline_contract_hash
                    and run.get("pipeline_resume_state") == required_pipeline_resume_state
                )
            )
        ]
        candidates = sorted(queued, key=lambda item: item["created_at"])[:limit]
        if lease_owner_id is None:
            if lease_seconds is not None:
                raise ValueError("lease_seconds requires lease_owner_id")
            return candidates
        if limit != 1:
            raise ValueError("durable worker claim requires limit=1")
        if lease_seconds is None or lease_seconds <= 0:
            raise ValueError("lease_seconds must be a positive integer")
        if not candidates:
            return []

        now = datetime.now(UTC)
        run = candidates[0]
        run["status"] = "RUNNING"
        run["lease_owner_id"] = lease_owner_id
        run["lease_fencing_token"] = int(run.get("lease_fencing_token") or 0) + 1
        run["last_heartbeat_at"] = _iso_datetime(now)
        run["lease_expires_at"] = _iso_datetime(now + timedelta(seconds=lease_seconds))
        _in_memory_runs_store[str(run["run_id"])] = run
        if lease_audit_emitter is not None:
            lease_audit_emitter(
                db_conn=None,
                code="WORKER_LEASE_CLAIMED",
                run_id=run["run_id"],
                tenant_id=run["tenant_id"],
                lease_owner_id=lease_owner_id,
                new_fencing_token=run["lease_fencing_token"],
                lease_expires_at=run["lease_expires_at"],
            )
        return [run]

    def claim_queued_run_for_execution(
        self,
        *,
        run_id: str,
        lease_owner_id: str,
        lease_seconds: int,
    ) -> dict[str, Any] | None:
        """Atomically claim one exact in-memory row for the legacy inline seam.

        The strict production path never uses this seam; it exists so local
        non-database execution follows the lease invariant after retirement of
        ``try_mark_running``.
        """
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be a positive integer")
        run = _in_memory_runs_store.get(run_id)
        if run is None or run.get("tenant_id") != self._tenant_id or run.get("status") != "QUEUED":
            return None
        now = datetime.now(UTC)
        run["status"] = "RUNNING"
        run["lease_owner_id"] = lease_owner_id
        run["lease_fencing_token"] = int(run.get("lease_fencing_token") or 0) + 1
        run["last_heartbeat_at"] = _iso_datetime(now)
        run["lease_expires_at"] = _iso_datetime(now + timedelta(seconds=lease_seconds))
        _in_memory_runs_store[run_id] = run
        return dict(run)

    def renew_worker_lease(
        self,
        *,
        run_id: str,
        lease_owner_id: str,
        lease_fencing_token: int,
        lease_seconds: int,
        lease_audit_emitter: Callable[..., None] | None = None,
    ) -> bool:
        """Renew the exact current in-memory owner/token while unexpired."""
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be a positive integer")
        try:
            assert_in_memory_worker_lease(
                tenant_id=self._tenant_id,
                run_id=run_id,
                lease_owner_id=lease_owner_id,
                lease_fencing_token=lease_fencing_token,
            )
        except WorkerLeaseFencedError:
            return False
        now = datetime.now(UTC)
        run = _in_memory_runs_store[run_id]
        run["last_heartbeat_at"] = _iso_datetime(now)
        run["lease_expires_at"] = _iso_datetime(now + timedelta(seconds=lease_seconds))
        if lease_audit_emitter is not None:
            lease_audit_emitter(
                db_conn=None,
                code="WORKER_LEASE_HEARTBEAT_RENEWED",
                run_id=run_id,
                tenant_id=self._tenant_id,
                lease_owner_id=lease_owner_id,
                current_fencing_token=lease_fencing_token,
                lease_expires_at=run["lease_expires_at"],
            )
        return True

    def requeue_expired_worker_leases(
        self,
        *,
        limit: int = 1,
        lease_audit_emitter: Callable[..., None] | None = None,
    ) -> list[dict[str, Any]]:
        """Requeue expired in-memory RUNNING leases and advance their tokens."""
        if limit <= 0:
            raise ValueError("limit must be positive")
        now = datetime.now(UTC)
        expired = sorted(
            (
                run
                for run in _in_memory_runs_store.values()
                if run.get("tenant_id") == self._tenant_id
                and run.get("status") == "RUNNING"
                and _parse_utc(run.get("lease_expires_at")) <= now
            ),
            key=lambda item: (item.get("lease_expires_at") or "", item["created_at"]),
        )[:limit]
        for run in expired:
            prior_owner = run.get("lease_owner_id")
            prior_token = int(run.get("lease_fencing_token") or 0)
            run["status"] = "QUEUED"
            run["lease_owner_id"] = None
            run["lease_expires_at"] = None
            run["lease_fencing_token"] = prior_token + 1
            if lease_audit_emitter is not None:
                event = {
                    "db_conn": None,
                    "run_id": run["run_id"],
                    "tenant_id": run["tenant_id"],
                    "prior_lease_owner_id": prior_owner,
                    "prior_fencing_token": prior_token,
                    "new_fencing_token": run["lease_fencing_token"],
                }
                lease_audit_emitter(code="WORKER_LEASE_EXPIRED", **event)
                lease_audit_emitter(code="WORKER_LEASE_RECLAIMED", **event)
        return expired

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


def _iso_datetime(value: datetime) -> str:
    """Serialize an aware datetime in the repository's canonical UTC form."""
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_utc(value: Any) -> datetime:
    """Parse persisted lease timestamps; missing/malformed values are expired."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return datetime.min.replace(tzinfo=UTC)
    else:
        return datetime.min.replace(tzinfo=UTC)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


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
