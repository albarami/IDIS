"""Overrides routes for IDIS API.

Provides POST /v1/deals/{dealId}/overrides per OpenAPI spec.

Supports both Postgres persistence (when configured) and in-memory fallback.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from idis.api.auth import RequireTenantContext

router = APIRouter(prefix="/v1", tags=["Overrides"])

_IN_MEMORY_OVERRIDES: dict[str, dict[str, Any]] = {}


class CreateOverrideRequest(BaseModel):
    """Request body for POST /v1/deals/{dealId}/overrides."""

    override_type: str
    justification: str


class Override(BaseModel):
    """Override response model per OpenAPI spec."""

    override_id: str
    deal_id: str
    override_type: str
    justification: str
    status: str
    created_at: str


def _create_override_in_postgres(
    conn: Any,
    override_id: str,
    tenant_id: str,
    deal_id: str,
    override_type: str,
    justification: str,
    actor_id: str,
    idempotency_key: str | None,
) -> dict[str, Any]:
    """Create override in Postgres."""
    from sqlalchemy import text

    now = datetime.now(UTC)
    conn.execute(
        text(
            """
            INSERT INTO overrides
                (override_id, tenant_id, deal_id, override_type,
                 justification, status, actor_id, idempotency_key, created_at)
            VALUES
                (:override_id, :tenant_id, :deal_id, :override_type,
                 :justification, 'ACTIVE', :actor_id, :idempotency_key,
                 :created_at)
            """
        ),
        {
            "override_id": override_id,
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "override_type": override_type,
            "justification": justification,
            "actor_id": actor_id,
            "idempotency_key": idempotency_key,
            "created_at": now,
        },
    )
    return {
        "override_id": override_id,
        "tenant_id": tenant_id,
        "deal_id": deal_id,
        "override_type": override_type,
        "justification": justification,
        "status": "ACTIVE",
        "actor_id": actor_id,
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


@router.post("/deals/{deal_id}/overrides", response_model=Override, status_code=201)
def create_override(
    deal_id: str,
    request_body: CreateOverrideRequest,
    request: Request,
    tenant_ctx: RequireTenantContext,
) -> Override:
    """Create a partner override.

    Args:
        deal_id: UUID of the deal.
        request_body: Override request with type and justification.
        request: FastAPI request for DB connection access.
        tenant_ctx: Injected tenant context from auth dependency.

    Returns:
        Override with override details.

    Raises:
        HTTPException: 400 if missing justification, 404 if deal not found.
    """
    if not request_body.justification or len(request_body.justification.strip()) == 0:
        raise HTTPException(status_code=400, detail="justification is required and cannot be empty")

    if not request_body.override_type or len(request_body.override_type.strip()) == 0:
        raise HTTPException(status_code=400, detail="override_type is required and cannot be empty")

    override_id = str(uuid.uuid4())
    db_conn = getattr(request.state, "db_conn", None)
    idempotency_key = request.headers.get("Idempotency-Key")
    actor_id = tenant_ctx.name

    if db_conn is not None:
        if not _deal_exists_in_postgres(db_conn, deal_id):
            raise HTTPException(status_code=404, detail="Deal not found")

        override_data = _create_override_in_postgres(
            conn=db_conn,
            override_id=override_id,
            tenant_id=tenant_ctx.tenant_id,
            deal_id=deal_id,
            override_type=request_body.override_type,
            justification=request_body.justification,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
        )
    else:
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        override_data = {
            "override_id": override_id,
            "tenant_id": tenant_ctx.tenant_id,
            "deal_id": deal_id,
            "override_type": request_body.override_type,
            "justification": request_body.justification,
            "status": "ACTIVE",
            "actor_id": actor_id,
            "created_at": now,
        }
        _IN_MEMORY_OVERRIDES[override_id] = override_data

    request.state.audit_resource_id = override_id

    return Override(
        override_id=override_data["override_id"],
        deal_id=override_data["deal_id"],
        override_type=override_data["override_type"],
        justification=override_data["justification"],
        status=override_data["status"],
        created_at=override_data["created_at"],
    )


def clear_overrides_store() -> None:
    """Clear the in-memory overrides store. For testing only."""
    _IN_MEMORY_OVERRIDES.clear()
