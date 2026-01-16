"""Runs routes for IDIS API.

Provides POST /v1/deals/{dealId}/runs and GET /v1/runs/{runId} per OpenAPI spec.

Supports both Postgres persistence (when configured) and in-memory fallback.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from idis.api.auth import RequireTenantContext

router = APIRouter(prefix="/v1", tags=["Runs"])

_IN_MEMORY_RUNS: dict[str, dict[str, Any]] = {}


class StartRunRequest(BaseModel):
    """Request body for POST /v1/deals/{dealId}/runs."""

    mode: str


class RunRef(BaseModel):
    """Run reference returned by startRun (202)."""

    run_id: str
    status: str


class RunStatus(BaseModel):
    """Run status response for GET /v1/runs/{runId}."""

    run_id: str
    status: str
    started_at: str
    finished_at: str | None = None


def _get_run_from_postgres(conn: Any, run_id: str) -> dict[str, Any] | None:
    """Get run from Postgres."""
    from sqlalchemy import text

    result = conn.execute(
        text(
            """
            SELECT run_id, tenant_id, deal_id, mode, status, started_at, finished_at, created_at
            FROM runs
            WHERE run_id = :run_id
            """
        ),
        {"run_id": run_id},
    )
    row = result.fetchone()
    if row is None:
        return None
    return {
        "run_id": str(row.run_id),
        "tenant_id": str(row.tenant_id),
        "deal_id": str(row.deal_id),
        "mode": row.mode,
        "status": row.status,
        "started_at": row.started_at.isoformat().replace("+00:00", "Z") if row.started_at else None,
        "finished_at": row.finished_at.isoformat().replace("+00:00", "Z")
        if row.finished_at
        else None,
        "created_at": row.created_at.isoformat().replace("+00:00", "Z") if row.created_at else None,
    }


def _create_run_in_postgres(
    conn: Any,
    run_id: str,
    tenant_id: str,
    deal_id: str,
    mode: str,
    idempotency_key: str | None,
) -> dict[str, Any]:
    """Create run in Postgres."""
    from sqlalchemy import text

    now = datetime.now(UTC)
    conn.execute(
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
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "mode": mode,
            "started_at": now,
            "idempotency_key": idempotency_key,
            "created_at": now,
        },
    )
    return {
        "run_id": run_id,
        "tenant_id": tenant_id,
        "deal_id": deal_id,
        "mode": mode,
        "status": "QUEUED",
        "started_at": now.isoformat().replace("+00:00", "Z"),
        "finished_at": None,
        "created_at": now.isoformat().replace("+00:00", "Z"),
    }


def _deal_exists_in_postgres(conn: Any, deal_id: str) -> bool:
    """Check if deal exists in Postgres (RLS enforced)."""
    from sqlalchemy import text

    result = conn.execute(
        text("SELECT 1 FROM deals WHERE deal_id = :deal_id"),
        {"deal_id": deal_id},
    )
    return result.fetchone() is not None


@router.post("/deals/{deal_id}/runs", response_model=RunRef, status_code=202)
def start_run(
    deal_id: str,
    request_body: StartRunRequest,
    request: Request,
    tenant_ctx: RequireTenantContext,
) -> RunRef:
    """Start an IDIS pipeline run.

    Args:
        deal_id: UUID of the deal to run pipeline for.
        request_body: Run request with mode (SNAPSHOT or FULL).
        request: FastAPI request for DB connection access.
        tenant_ctx: Injected tenant context from auth dependency.

    Returns:
        RunRef with run_id and initial status.

    Raises:
        HTTPException: 400 if invalid mode, 404 if deal not found.
    """
    if request_body.mode not in ("SNAPSHOT", "FULL"):
        raise HTTPException(status_code=400, detail="Invalid mode; must be SNAPSHOT or FULL")

    run_id = str(uuid.uuid4())
    db_conn = getattr(request.state, "db_conn", None)
    idempotency_key = request.headers.get("Idempotency-Key")

    if db_conn is not None:
        if not _deal_exists_in_postgres(db_conn, deal_id):
            raise HTTPException(status_code=404, detail="Deal not found")

        run_data = _create_run_in_postgres(
            conn=db_conn,
            run_id=run_id,
            tenant_id=tenant_ctx.tenant_id,
            deal_id=deal_id,
            mode=request_body.mode,
            idempotency_key=idempotency_key,
        )
    else:
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        run_data = {
            "run_id": run_id,
            "tenant_id": tenant_ctx.tenant_id,
            "deal_id": deal_id,
            "mode": request_body.mode,
            "status": "QUEUED",
            "started_at": now,
            "finished_at": None,
            "created_at": now,
        }
        _IN_MEMORY_RUNS[run_id] = run_data

    request.state.audit_resource_id = run_id

    return RunRef(
        run_id=run_data["run_id"],
        status=run_data["status"],
    )


@router.get("/runs/{run_id}", response_model=RunStatus)
def get_run(
    run_id: str,
    request: Request,
    tenant_ctx: RequireTenantContext,
) -> RunStatus:
    """Get run status.

    Args:
        run_id: UUID of the run to retrieve.
        request: FastAPI request for DB connection access.
        tenant_ctx: Injected tenant context from auth dependency.

    Returns:
        RunStatus with run details.

    Raises:
        HTTPException: 404 if run not found or belongs to different tenant.
    """
    db_conn = getattr(request.state, "db_conn", None)

    if db_conn is not None:
        run_data = _get_run_from_postgres(db_conn, run_id)
    else:
        run_data = _IN_MEMORY_RUNS.get(run_id)
        if run_data is not None and run_data.get("tenant_id") != tenant_ctx.tenant_id:
            run_data = None

    if run_data is None:
        raise HTTPException(status_code=404, detail="Run not found")

    return RunStatus(
        run_id=run_data["run_id"],
        status=run_data["status"],
        started_at=run_data["started_at"],
        finished_at=run_data.get("finished_at"),
    )


def clear_runs_store() -> None:
    """Clear the in-memory runs store. For testing only."""
    _IN_MEMORY_RUNS.clear()
