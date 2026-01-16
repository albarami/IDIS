"""Debate routes for IDIS API.

Provides POST /v1/deals/{dealId}/debate and GET /v1/debate/{debateId} per OpenAPI spec.

Supports both Postgres persistence (when configured) and in-memory fallback.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from idis.api.auth import RequireTenantContext
from idis.api.errors import IdisHttpError

router = APIRouter(prefix="/v1", tags=["Debate"])

_IN_MEMORY_DEBATES: dict[str, dict[str, Any]] = {}


class StartDebateRequest(BaseModel):
    """Request body for POST /v1/deals/{dealId}/debate."""

    protocol_version: str = "v1"
    max_rounds: int = 5


class RunRef(BaseModel):
    """Run reference returned by startDebate (202)."""

    run_id: str
    status: str


class DebateSession(BaseModel):
    """Debate session response for GET /v1/debate/{debateId}."""

    debate_id: str
    deal_id: str
    protocol_version: str
    rounds: list[dict[str, Any]]
    created_at: str


def _get_debate_from_postgres(conn: Any, debate_id: str) -> dict[str, Any] | None:
    """Get debate session from Postgres."""
    from sqlalchemy import text

    result = conn.execute(
        text(
            """
            SELECT debate_id, tenant_id, deal_id, protocol_version, rounds, status, created_at
            FROM debate_sessions
            WHERE debate_id = :debate_id
            """
        ),
        {"debate_id": debate_id},
    )
    row = result.fetchone()
    if row is None:
        return None
    return {
        "debate_id": str(row.debate_id),
        "tenant_id": str(row.tenant_id),
        "deal_id": str(row.deal_id),
        "protocol_version": row.protocol_version,
        "rounds": row.rounds if row.rounds else [],
        "status": row.status,
        "created_at": row.created_at.isoformat().replace("+00:00", "Z") if row.created_at else None,
    }


def _create_debate_in_postgres(
    conn: Any,
    debate_id: str,
    tenant_id: str,
    deal_id: str,
    protocol_version: str,
    max_rounds: int,
    idempotency_key: str | None,
) -> dict[str, Any]:
    """Create debate session in Postgres."""
    from sqlalchemy import text

    now = datetime.now(UTC)
    conn.execute(
        text(
            """
            INSERT INTO debate_sessions
                (debate_id, tenant_id, deal_id, protocol_version, max_rounds,
                 rounds, status, idempotency_key, created_at)
            VALUES
                (:debate_id, :tenant_id, :deal_id, :protocol_version,
                 :max_rounds, '[]'::jsonb, 'QUEUED', :idempotency_key,
                 :created_at)
            """
        ),
        {
            "debate_id": debate_id,
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "protocol_version": protocol_version,
            "max_rounds": max_rounds,
            "idempotency_key": idempotency_key,
            "created_at": now,
        },
    )
    return {
        "debate_id": debate_id,
        "tenant_id": tenant_id,
        "deal_id": deal_id,
        "protocol_version": protocol_version,
        "rounds": [],
        "status": "QUEUED",
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


def _validate_start_debate_body(body: dict[str, Any] | None) -> StartDebateRequest:
    """Validate start debate request body, returning 400 for invalid fields."""
    if body is None:
        body = {}
    if not isinstance(body, dict):
        raise IdisHttpError(
            status_code=400,
            code="INVALID_REQUEST",
            message="Request body must be a JSON object",
        )
    protocol_version = body.get("protocol_version", "v1")
    max_rounds = body.get("max_rounds", 5)
    if not isinstance(max_rounds, int) or max_rounds < 1 or max_rounds > 10:
        raise IdisHttpError(
            status_code=400,
            code="INVALID_REQUEST",
            message="max_rounds must be an integer between 1 and 10",
        )
    return StartDebateRequest(protocol_version=protocol_version, max_rounds=max_rounds)


@router.post("/deals/{deal_id}/debate", response_model=RunRef, status_code=202)
async def start_debate(
    deal_id: str,
    request: Request,
    tenant_ctx: RequireTenantContext,
) -> RunRef:
    """Start a LangGraph debate session.

    Args:
        deal_id: UUID of the deal to debate.
        request: FastAPI request for DB connection and body access.
        tenant_ctx: Injected tenant context from auth dependency.

    Returns:
        RunRef with debate_id (as run_id) and initial status.

    Raises:
        IdisHttpError: 400 if invalid params, 404 if deal not found.
    """
    try:
        body = await request.json()
    except Exception:
        body = None
    request_body = _validate_start_debate_body(body)

    debate_id = str(uuid.uuid4())
    db_conn = getattr(request.state, "db_conn", None)
    idempotency_key = request.headers.get("Idempotency-Key")

    if db_conn is not None:
        if not _deal_exists_in_postgres(db_conn, deal_id):
            raise IdisHttpError(status_code=404, code="NOT_FOUND", message="Deal not found")

        debate_data = _create_debate_in_postgres(
            conn=db_conn,
            debate_id=debate_id,
            tenant_id=tenant_ctx.tenant_id,
            deal_id=deal_id,
            protocol_version=request_body.protocol_version,
            max_rounds=request_body.max_rounds,
            idempotency_key=idempotency_key,
        )
    else:
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        debate_data = {
            "debate_id": debate_id,
            "tenant_id": tenant_ctx.tenant_id,
            "deal_id": deal_id,
            "protocol_version": request_body.protocol_version,
            "max_rounds": request_body.max_rounds,
            "rounds": [],
            "status": "QUEUED",
            "created_at": now,
        }
        _IN_MEMORY_DEBATES[debate_id] = debate_data

    request.state.audit_resource_id = debate_id

    return RunRef(
        run_id=debate_data["debate_id"],
        status=debate_data["status"],
    )


@router.get("/debate/{debate_id}", response_model=DebateSession)
def get_debate(
    debate_id: str,
    request: Request,
    tenant_ctx: RequireTenantContext,
) -> DebateSession:
    """Get debate session with transcript.

    Args:
        debate_id: UUID of the debate to retrieve.
        request: FastAPI request for DB connection access.
        tenant_ctx: Injected tenant context from auth dependency.

    Returns:
        DebateSession with debate details and rounds.

    Raises:
        HTTPException: 404 if debate not found or belongs to different tenant.
    """
    db_conn = getattr(request.state, "db_conn", None)

    if db_conn is not None:
        debate_data = _get_debate_from_postgres(db_conn, debate_id)
    else:
        debate_data = _IN_MEMORY_DEBATES.get(debate_id)
        if debate_data is not None and debate_data.get("tenant_id") != tenant_ctx.tenant_id:
            debate_data = None

    if debate_data is None:
        raise IdisHttpError(status_code=404, code="NOT_FOUND", message="Debate not found")

    return DebateSession(
        debate_id=debate_data["debate_id"],
        deal_id=debate_data["deal_id"],
        protocol_version=debate_data["protocol_version"],
        rounds=debate_data["rounds"],
        created_at=debate_data["created_at"],
    )


def clear_debates_store() -> None:
    """Clear the in-memory debates store. For testing only."""
    _IN_MEMORY_DEBATES.clear()
