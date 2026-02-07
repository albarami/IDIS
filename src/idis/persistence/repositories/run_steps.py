"""RunStep repository — tenant-scoped persistence for step ledger.

Provides both Postgres and in-memory implementations.
Mirrors the pattern of DealsRepository: Postgres with RLS,
and in-memory fallback with module-level dict store.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from sqlalchemy import text

from idis.models.run_step import RunStep, StepName
from idis.persistence.db import is_postgres_configured, set_tenant_local

if TYPE_CHECKING:
    from sqlalchemy import Connection

logger = logging.getLogger(__name__)


@runtime_checkable
class RunStepsRepo(Protocol):
    """Structural interface for RunStep repositories.

    Both InMemoryRunStepsRepository and PostgresRunStepsRepository
    satisfy this protocol. Use this type in function signatures
    that accept either backend.
    """

    def create(self, step: RunStep) -> RunStep: ...

    def get_by_run_id(self, run_id: str) -> list[RunStep]: ...

    def get_step(self, run_id: str, step_name: StepName) -> RunStep | None: ...

    def update(self, step: RunStep) -> RunStep: ...


_run_steps_store: dict[str, dict[str, Any]] = {}
"""Global in-memory store keyed by step_id."""


class InMemoryRunStepsRepository:
    """Tenant-scoped in-memory repository for RunStep records.

    All reads filter by tenant_id so cross-tenant access returns empty
    results (no existence oracle).

    Args:
        tenant_id: Tenant UUID string for scoping.
    """

    def __init__(self, tenant_id: str) -> None:
        """Initialize with tenant context.

        Args:
            tenant_id: Tenant UUID for isolation.
        """
        self._tenant_id = tenant_id

    def create(self, step: RunStep) -> RunStep:
        """Persist a new RunStep record.

        Args:
            step: RunStep instance to store.

        Returns:
            The stored RunStep.

        Raises:
            ValueError: If step.tenant_id does not match repository tenant.
        """
        if step.tenant_id != self._tenant_id:
            raise ValueError("Tenant mismatch in RunStep creation")
        _run_steps_store[step.step_id] = step.model_dump()
        return step

    def get_by_run_id(self, run_id: str) -> list[RunStep]:
        """Return all steps for a run, ordered by step_order.

        Only returns steps belonging to the repository tenant.
        Cross-tenant run_ids silently return empty list (no existence leak).

        Args:
            run_id: Run UUID to query.

        Returns:
            List of RunStep sorted by step_order ascending.
        """
        steps = [
            RunStep.model_validate(data)
            for data in _run_steps_store.values()
            if data["run_id"] == run_id and data["tenant_id"] == self._tenant_id
        ]
        steps.sort(key=lambda s: s.step_order)
        return steps

    def get_step(self, run_id: str, step_name: StepName) -> RunStep | None:
        """Get a specific step by run_id and step_name.

        Returns None for cross-tenant access (no existence leak).

        Args:
            run_id: Run UUID.
            step_name: Canonical step name.

        Returns:
            RunStep if found and tenant matches, else None.
        """
        for data in _run_steps_store.values():
            if (
                data["run_id"] == run_id
                and data["step_name"] == step_name.value
                and data["tenant_id"] == self._tenant_id
            ):
                return RunStep.model_validate(data)
        return None

    def update(self, step: RunStep) -> RunStep:
        """Update an existing RunStep record.

        Args:
            step: RunStep with updated fields.

        Returns:
            The updated RunStep.

        Raises:
            ValueError: If step.tenant_id does not match repository tenant.
            KeyError: If step_id not found in store.
        """
        if step.tenant_id != self._tenant_id:
            raise ValueError("Tenant mismatch in RunStep update")
        if step.step_id not in _run_steps_store:
            raise KeyError(f"RunStep {step.step_id} not found")
        _run_steps_store[step.step_id] = step.model_dump()
        return step


class PostgresRunStepsRepository:
    """Tenant-scoped Postgres repository for RunStep records.

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

    def create(self, step: RunStep) -> RunStep:
        """Persist a new RunStep record.

        Args:
            step: RunStep instance to store.

        Returns:
            The stored RunStep.

        Raises:
            ValueError: If step.tenant_id does not match repository tenant.
        """
        if step.tenant_id != self._tenant_id:
            raise ValueError("Tenant mismatch in RunStep creation")
        self._conn.execute(
            text(
                """
                INSERT INTO run_steps
                    (step_id, tenant_id, run_id, step_name, step_order,
                     status, started_at, finished_at, retry_count,
                     result_summary, error_code, error_message)
                VALUES
                    (:step_id, :tenant_id, :run_id, :step_name, :step_order,
                     :status, :started_at, :finished_at, :retry_count,
                     CAST(:result_summary AS JSONB), :error_code, :error_message)
                """
            ),
            {
                "step_id": step.step_id,
                "tenant_id": step.tenant_id,
                "run_id": step.run_id,
                "step_name": (
                    step.step_name.value if hasattr(step.step_name, "value") else step.step_name
                ),
                "step_order": step.step_order,
                "status": step.status.value if hasattr(step.status, "value") else step.status,
                "started_at": step.started_at,
                "finished_at": step.finished_at,
                "retry_count": step.retry_count,
                "result_summary": json.dumps(step.result_summary),
                "error_code": step.error_code,
                "error_message": step.error_message,
            },
        )
        return step

    def get_by_run_id(self, run_id: str) -> list[RunStep]:
        """Return all steps for a run, ordered by step_order.

        RLS ensures only steps for the current tenant are visible.
        Cross-tenant run_ids silently return empty list (no existence leak).

        Args:
            run_id: Run UUID to query.

        Returns:
            List of RunStep sorted by step_order ascending.
        """
        result = self._conn.execute(
            text(
                """
                SELECT step_id, tenant_id, run_id, step_name, step_order,
                       status, started_at, finished_at, retry_count,
                       result_summary, error_code, error_message
                FROM run_steps
                WHERE run_id = :run_id
                ORDER BY step_order
                """
            ),
            {"run_id": run_id},
        )
        return [self._row_to_model(row) for row in result.fetchall()]

    def get_step(self, run_id: str, step_name: StepName) -> RunStep | None:
        """Get a specific step by run_id and step_name.

        Returns None for cross-tenant access (no existence leak).

        Args:
            run_id: Run UUID.
            step_name: Canonical step name.

        Returns:
            RunStep if found and tenant matches, else None.
        """
        step_name_val = step_name.value if hasattr(step_name, "value") else step_name
        result = self._conn.execute(
            text(
                """
                SELECT step_id, tenant_id, run_id, step_name, step_order,
                       status, started_at, finished_at, retry_count,
                       result_summary, error_code, error_message
                FROM run_steps
                WHERE run_id = :run_id AND step_name = :step_name
                """
            ),
            {"run_id": run_id, "step_name": step_name_val},
        )
        row = result.fetchone()
        if row is None:
            return None
        return self._row_to_model(row)

    def update(self, step: RunStep) -> RunStep:
        """Update an existing RunStep record.

        Args:
            step: RunStep with updated fields.

        Returns:
            The updated RunStep.

        Raises:
            ValueError: If step.tenant_id does not match repository tenant.
        """
        if step.tenant_id != self._tenant_id:
            raise ValueError("Tenant mismatch in RunStep update")
        self._conn.execute(
            text(
                """
                UPDATE run_steps SET
                    status = :status,
                    started_at = :started_at,
                    finished_at = :finished_at,
                    retry_count = :retry_count,
                    result_summary = CAST(:result_summary AS JSONB),
                    error_code = :error_code,
                    error_message = :error_message
                WHERE step_id = :step_id
                """
            ),
            {
                "step_id": step.step_id,
                "status": step.status.value if hasattr(step.status, "value") else step.status,
                "started_at": step.started_at,
                "finished_at": step.finished_at,
                "retry_count": step.retry_count,
                "result_summary": json.dumps(step.result_summary),
                "error_code": step.error_code,
                "error_message": step.error_message,
            },
        )
        return step

    def _row_to_model(self, row: Any) -> RunStep:
        """Convert database row to RunStep model."""
        started_at = row.started_at
        if started_at is not None and hasattr(started_at, "isoformat"):
            started_at = started_at.isoformat().replace("+00:00", "Z")

        finished_at = row.finished_at
        if finished_at is not None and hasattr(finished_at, "isoformat"):
            finished_at = finished_at.isoformat().replace("+00:00", "Z")

        result_summary = row.result_summary
        if isinstance(result_summary, str):
            result_summary = json.loads(result_summary)

        return RunStep(
            step_id=str(row.step_id),
            run_id=str(row.run_id),
            tenant_id=str(row.tenant_id),
            step_name=row.step_name,
            step_order=row.step_order,
            status=row.status,
            started_at=started_at,
            finished_at=finished_at,
            retry_count=row.retry_count,
            result_summary=result_summary or {},
            error_code=row.error_code,
            error_message=row.error_message,
        )


def clear_run_steps_store() -> None:
    """Clear the in-memory run steps store. For testing only."""
    _run_steps_store.clear()


def get_run_steps_repository(
    conn: Connection | None,
    tenant_id: str,
) -> PostgresRunStepsRepository | InMemoryRunStepsRepository:
    """Factory to get appropriate run steps repository.

    Returns Postgres repository if configured, otherwise in-memory fallback.

    Args:
        conn: SQLAlchemy connection (can be None for in-memory).
        tenant_id: Tenant UUID string.

    Returns:
        Repository instance.
    """
    if conn is not None and is_postgres_configured():
        return PostgresRunStepsRepository(conn, tenant_id)
    return InMemoryRunStepsRepository(tenant_id)
