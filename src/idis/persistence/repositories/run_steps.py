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
from idis.persistence.repositories.runs import (
    WorkerLeaseFencedError,
    assert_in_memory_ownerless_write_allowed,
    assert_in_memory_worker_lease,
)

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

    def create(
        self,
        step: RunStep,
        *,
        lease_owner_id: str | None = None,
        lease_fencing_token: int | None = None,
    ) -> RunStep: ...

    def get_by_run_id(self, run_id: str) -> list[RunStep]: ...

    def get_step(self, run_id: str, step_name: StepName) -> RunStep | None: ...

    def update(
        self,
        step: RunStep,
        *,
        lease_owner_id: str | None = None,
        lease_fencing_token: int | None = None,
    ) -> RunStep: ...


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

    def create(
        self,
        step: RunStep,
        *,
        lease_owner_id: str | None = None,
        lease_fencing_token: int | None = None,
    ) -> RunStep:
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
        self._assert_worker_lease(
            run_id=step.run_id,
            lease_owner_id=lease_owner_id,
            lease_fencing_token=lease_fencing_token,
        )
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

    def update(
        self,
        step: RunStep,
        *,
        lease_owner_id: str | None = None,
        lease_fencing_token: int | None = None,
    ) -> RunStep:
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
        persisted = _run_steps_store[step.step_id]
        if persisted.get("tenant_id") != self._tenant_id or persisted.get("run_id") != step.run_id:
            raise WorkerLeaseFencedError("worker lease does not match the persisted step run")
        self._assert_worker_lease(
            run_id=step.run_id,
            lease_owner_id=lease_owner_id,
            lease_fencing_token=lease_fencing_token,
        )
        _run_steps_store[step.step_id] = step.model_dump()
        return step

    def _assert_worker_lease(
        self,
        *,
        run_id: str,
        lease_owner_id: str | None,
        lease_fencing_token: int | None,
    ) -> None:
        """Fence a claimed-worker write while preserving legacy non-worker callers."""
        if lease_owner_id is None and lease_fencing_token is None:
            assert_in_memory_ownerless_write_allowed(
                tenant_id=self._tenant_id,
                run_id=run_id,
            )
            return
        if lease_owner_id is None or lease_fencing_token is None:
            raise WorkerLeaseFencedError("incomplete worker lease identity")
        assert_in_memory_worker_lease(
            tenant_id=self._tenant_id,
            run_id=run_id,
            lease_owner_id=lease_owner_id,
            lease_fencing_token=lease_fencing_token,
        )


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

    def create(
        self,
        step: RunStep,
        *,
        lease_owner_id: str | None = None,
        lease_fencing_token: int | None = None,
    ) -> RunStep:
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
        if (lease_owner_id is None) != (lease_fencing_token is None):
            raise WorkerLeaseFencedError("incomplete worker lease identity")
        lease_predicate = """
              AND (
                  :lease_owner_id IS NULL
                  AND runs.status <> 'RUNNING'
                  OR :lease_owner_id IS NOT NULL
                  AND runs.status = 'RUNNING'
                  AND runs.lease_owner_id = :lease_owner_id
                  AND runs.lease_fencing_token = :lease_fencing_token
                  AND runs.lease_expires_at > statement_timestamp()
              )
        """
        statement = f"""
            WITH eligible_run AS (
                SELECT runs.run_id
                FROM runs
                WHERE runs.tenant_id = :tenant_id
                  AND runs.run_id = :run_id
                  {lease_predicate}
                FOR UPDATE
            )
            INSERT INTO run_steps
                (step_id, tenant_id, run_id, step_name, step_order,
                 status, started_at, finished_at, retry_count,
                 result_summary, error_code, error_message,
                 pipeline_contract_version, pipeline_contract_hash,
                 semantic_rule_version, semantic_rule_hash)
            SELECT
                :step_id, :tenant_id, eligible_run.run_id, :step_name, :step_order,
                :status, :started_at, :finished_at, :retry_count,
                CAST(:result_summary AS JSONB), :error_code, :error_message,
                :pipeline_contract_version, :pipeline_contract_hash,
                :semantic_rule_version, :semantic_rule_hash
            FROM eligible_run
        """
        params = {
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
            "pipeline_contract_version": step.pipeline_contract_version,
            "pipeline_contract_hash": step.pipeline_contract_hash,
            "semantic_rule_version": step.semantic_rule_version,
            "semantic_rule_hash": step.semantic_rule_hash,
            "lease_owner_id": lease_owner_id,
            "lease_fencing_token": lease_fencing_token,
        }
        result = self._conn.execute(text(statement), params)
        if result.rowcount != 1:
            raise WorkerLeaseFencedError("worker lease fenced before step creation")
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
                       result_summary, error_code, error_message,
                       pipeline_contract_version, pipeline_contract_hash,
                       semantic_rule_version, semantic_rule_hash
                FROM run_steps
                WHERE run_id = :run_id
                ORDER BY step_order, step_id
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
                       result_summary, error_code, error_message,
                       pipeline_contract_version, pipeline_contract_hash,
                       semantic_rule_version, semantic_rule_hash
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

    def update(
        self,
        step: RunStep,
        *,
        lease_owner_id: str | None = None,
        lease_fencing_token: int | None = None,
    ) -> RunStep:
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
        if (lease_owner_id is None) != (lease_fencing_token is None):
            raise WorkerLeaseFencedError("incomplete worker lease identity")
        lease_predicate = """
              AND (
                  :lease_owner_id IS NULL
                  AND runs.status <> 'RUNNING'
                  OR :lease_owner_id IS NOT NULL
                  AND runs.status = 'RUNNING'
                  AND runs.lease_owner_id = :lease_owner_id
                  AND runs.lease_fencing_token = :lease_fencing_token
                  AND runs.lease_expires_at > statement_timestamp()
              )
        """
        result = self._conn.execute(
            text(
                f"""
                WITH eligible_run AS (
                    SELECT runs.run_id
                    FROM runs
                    WHERE runs.tenant_id = :tenant_id
                      AND runs.run_id = :run_id
                      {lease_predicate}
                    FOR UPDATE
                )
                UPDATE run_steps SET
                    status = :status,
                    started_at = :started_at,
                    finished_at = :finished_at,
                    retry_count = :retry_count,
                    result_summary = CAST(:result_summary AS JSONB),
                    error_code = :error_code,
                    error_message = :error_message
                FROM eligible_run
                WHERE run_steps.step_id = :step_id
                  AND run_steps.tenant_id = :tenant_id
                  AND run_steps.run_id = eligible_run.run_id
                """
            ),
            {
                "step_id": step.step_id,
                "run_id": step.run_id,
                "tenant_id": step.tenant_id,
                "status": step.status.value if hasattr(step.status, "value") else step.status,
                "started_at": step.started_at,
                "finished_at": step.finished_at,
                "retry_count": step.retry_count,
                "result_summary": json.dumps(step.result_summary),
                "error_code": step.error_code,
                "error_message": step.error_message,
                "lease_owner_id": lease_owner_id,
                "lease_fencing_token": lease_fencing_token,
            },
        )
        if result.rowcount != 1:
            raise WorkerLeaseFencedError("worker lease fenced before step update")
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
            pipeline_contract_version=str(row.pipeline_contract_version),
            pipeline_contract_hash=str(row.pipeline_contract_hash),
            semantic_rule_version=str(row.semantic_rule_version),
            semantic_rule_hash=str(row.semantic_rule_hash),
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
