"""Deliverables routes for IDIS API.

Provides GET/POST /v1/deals/{dealId}/deliverables per OpenAPI spec.

Supports both Postgres persistence (when configured) and in-memory fallback.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from idis.api.auth import RequireTenantContext

router = APIRouter(prefix="/v1", tags=["Deliverables"])

_IN_MEMORY_DELIVERABLES: dict[str, dict[str, Any]] = {}


class GenerateDeliverableRequest(BaseModel):
    """Request body for POST /v1/deals/{dealId}/deliverables."""

    deliverable_type: str
    format: str = "PDF"


class RunRef(BaseModel):
    """Run reference returned by generateDeliverable (202)."""

    run_id: str
    status: str


class Deliverable(BaseModel):
    """Deliverable response model per OpenAPI spec."""

    deliverable_id: str
    deal_id: str
    deliverable_type: str
    status: str
    uri: str | None = None
    created_at: str


class PaginatedDeliverableList(BaseModel):
    """Paginated list of deliverables per OpenAPI spec."""

    items: list[Deliverable]
    next_cursor: str | None = None


def _list_deliverables_from_postgres(
    conn: Any,
    deal_id: str,
    limit: int,
    cursor: str | None,
) -> tuple[list[dict[str, Any]], str | None]:
    """List deliverables from Postgres with pagination."""
    from sqlalchemy import text

    query = """
        SELECT deliverable_id, tenant_id, deal_id, deliverable_type, format, status, uri, created_at
        FROM deliverables
        WHERE deal_id = :deal_id
    """
    params: dict[str, Any] = {"deal_id": deal_id, "limit": limit + 1}

    if cursor:
        query += " AND created_at < :cursor"
        params["cursor"] = cursor

    query += " ORDER BY created_at DESC LIMIT :limit"

    result = conn.execute(text(query), params)
    rows = result.fetchall()

    items: list[dict[str, Any]] = []
    next_cursor = None

    for i, row in enumerate(rows):
        if i >= limit:
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
                "uri": row.uri,
                "created_at": row.created_at.isoformat().replace("+00:00", "Z")
                if row.created_at
                else None,
            }
        )

    return items, next_cursor


def _create_deliverable_in_postgres(
    conn: Any,
    deliverable_id: str,
    tenant_id: str,
    deal_id: str,
    deliverable_type: str,
    format_: str,
    idempotency_key: str | None,
) -> dict[str, Any]:
    """Create deliverable in Postgres."""
    from sqlalchemy import text

    now = datetime.now(UTC)
    conn.execute(
        text(
            """
            INSERT INTO deliverables
                (deliverable_id, tenant_id, deal_id, deliverable_type,
                 format, status, idempotency_key, created_at)
            VALUES
                (:deliverable_id, :tenant_id, :deal_id, :deliverable_type,
                 :format, 'QUEUED', :idempotency_key, :created_at)
            """
        ),
        {
            "deliverable_id": deliverable_id,
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "deliverable_type": deliverable_type,
            "format": format_,
            "idempotency_key": idempotency_key,
            "created_at": now,
        },
    )
    return {
        "deliverable_id": deliverable_id,
        "tenant_id": tenant_id,
        "deal_id": deal_id,
        "deliverable_type": deliverable_type,
        "format": format_,
        "status": "QUEUED",
        "uri": None,
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


@router.get("/deals/{deal_id}/deliverables", response_model=PaginatedDeliverableList)
def list_deliverables(
    deal_id: str,
    request: Request,
    tenant_ctx: RequireTenantContext,
    limit: int = Query(default=50),
    cursor: str | None = Query(default=None),
) -> PaginatedDeliverableList:
    """List deliverables for a deal.

    Args:
        deal_id: UUID of the deal.
        request: FastAPI request for DB connection access.
        tenant_ctx: Injected tenant context from auth dependency.
        limit: Maximum number of items to return.
        cursor: Pagination cursor.

    Returns:
        Paginated list of deliverables.
    """
    if limit < 1 or limit > 200:
        from idis.api.errors import IdisHttpError

        raise IdisHttpError(
            status_code=400,
            code="INVALID_LIMIT",
            message="limit must be between 1 and 200",
        )

    db_conn = getattr(request.state, "db_conn", None)

    if db_conn is not None:
        items, next_cursor = _list_deliverables_from_postgres(db_conn, deal_id, limit, cursor)
    else:
        all_items = [
            d
            for d in _IN_MEMORY_DELIVERABLES.values()
            if d.get("deal_id") == deal_id and d.get("tenant_id") == tenant_ctx.tenant_id
        ]
        all_items.sort(key=lambda x: x["created_at"], reverse=True)
        items = all_items[:limit]
        next_cursor = None

    return PaginatedDeliverableList(
        items=[
            Deliverable(
                deliverable_id=d["deliverable_id"],
                deal_id=d["deal_id"],
                deliverable_type=d["deliverable_type"],
                status=d["status"],
                uri=d.get("uri"),
                created_at=d["created_at"],
            )
            for d in items
        ],
        next_cursor=next_cursor,
    )


@router.post("/deals/{deal_id}/deliverables", response_model=RunRef, status_code=202)
def generate_deliverable(
    deal_id: str,
    request_body: GenerateDeliverableRequest,
    request: Request,
    tenant_ctx: RequireTenantContext,
) -> RunRef:
    """Generate a deliverable.

    Args:
        deal_id: UUID of the deal.
        request_body: Deliverable request with type and format.
        request: FastAPI request for DB connection access.
        tenant_ctx: Injected tenant context from auth dependency.

    Returns:
        RunRef with deliverable_id (as run_id) and initial status.

    Raises:
        HTTPException: 400 if invalid format, 404 if deal not found.
    """
    if request_body.format not in ("PDF", "DOCX", "JSON"):
        raise HTTPException(status_code=400, detail="Invalid format; must be PDF, DOCX, or JSON")

    deliverable_id = str(uuid.uuid4())
    db_conn = getattr(request.state, "db_conn", None)
    idempotency_key = request.headers.get("Idempotency-Key")

    if db_conn is not None:
        if not _deal_exists_in_postgres(db_conn, deal_id):
            raise HTTPException(status_code=404, detail="Deal not found")

        deliverable_data = _create_deliverable_in_postgres(
            conn=db_conn,
            deliverable_id=deliverable_id,
            tenant_id=tenant_ctx.tenant_id,
            deal_id=deal_id,
            deliverable_type=request_body.deliverable_type,
            format_=request_body.format,
            idempotency_key=idempotency_key,
        )
    else:
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        deliverable_data = {
            "deliverable_id": deliverable_id,
            "tenant_id": tenant_ctx.tenant_id,
            "deal_id": deal_id,
            "deliverable_type": request_body.deliverable_type,
            "format": request_body.format,
            "status": "QUEUED",
            "uri": None,
            "created_at": now,
        }
        _IN_MEMORY_DELIVERABLES[deliverable_id] = deliverable_data

    request.state.audit_resource_id = deliverable_id

    return RunRef(
        run_id=deliverable_data["deliverable_id"],
        status=deliverable_data["status"],
    )


def clear_deliverables_store() -> None:
    """Clear the in-memory deliverables store. For testing only."""
    _IN_MEMORY_DELIVERABLES.clear()
