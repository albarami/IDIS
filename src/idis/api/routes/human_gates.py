"""Human Gates routes for IDIS API.

Provides GET/POST /v1/deals/{dealId}/human-gates per OpenAPI spec.

Supports both Postgres persistence (when configured) and in-memory fallback.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from idis.api.auth import RequireTenantContext
from idis.api.errors import IdisHttpError

router = APIRouter(prefix="/v1", tags=["Human Gates"])

_IN_MEMORY_GATES: dict[str, dict[str, Any]] = {}
_IN_MEMORY_ACTIONS: dict[str, dict[str, Any]] = {}


class SubmitHumanGateActionRequest(BaseModel):
    """Request body for POST /v1/deals/{dealId}/human-gates."""

    gate_id: str
    action: str
    notes: str | None = None


class HumanGate(BaseModel):
    """Human gate response model per OpenAPI spec."""

    gate_id: str
    deal_id: str
    gate_type: str
    status: str
    created_at: str


class HumanGateAction(BaseModel):
    """Human gate action response model per OpenAPI spec."""

    action_id: str
    gate_id: str
    action: str
    actor_id: str
    created_at: str


class PaginatedHumanGateList(BaseModel):
    """Paginated list of human gates per OpenAPI spec."""

    items: list[HumanGate]
    next_cursor: str | None = None


def _list_gates_from_postgres(
    conn: Any,
    deal_id: str,
    limit: int,
    cursor: str | None,
) -> tuple[list[dict[str, Any]], str | None]:
    """List human gates from Postgres with pagination."""
    from sqlalchemy import text

    query = """
        SELECT gate_id, tenant_id, deal_id, gate_type, status, created_at
        FROM human_gates
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
                "gate_id": str(row.gate_id),
                "tenant_id": str(row.tenant_id),
                "deal_id": str(row.deal_id),
                "gate_type": row.gate_type,
                "status": row.status,
                "created_at": row.created_at.isoformat().replace("+00:00", "Z")
                if row.created_at
                else None,
            }
        )

    return items, next_cursor


def _get_gate_from_postgres(conn: Any, gate_id: str) -> dict[str, Any] | None:
    """Get human gate from Postgres."""
    from sqlalchemy import text

    result = conn.execute(
        text(
            """
            SELECT gate_id, tenant_id, deal_id, gate_type, status, created_at
            FROM human_gates
            WHERE gate_id = :gate_id
            """
        ),
        {"gate_id": gate_id},
    )
    row = result.fetchone()
    if row is None:
        return None
    return {
        "gate_id": str(row.gate_id),
        "tenant_id": str(row.tenant_id),
        "deal_id": str(row.deal_id),
        "gate_type": row.gate_type,
        "status": row.status,
        "created_at": row.created_at.isoformat().replace("+00:00", "Z") if row.created_at else None,
    }


def _create_gate_action_in_postgres(
    conn: Any,
    action_id: str,
    tenant_id: str,
    gate_id: str,
    action: str,
    actor_id: str,
    notes: str | None,
    idempotency_key: str | None,
) -> dict[str, Any]:
    """Create human gate action in Postgres."""
    from sqlalchemy import text

    now = datetime.now(UTC)

    conn.execute(
        text(
            """
            INSERT INTO human_gate_actions
                (action_id, tenant_id, gate_id, action, actor_id, notes,
                 idempotency_key, created_at)
            VALUES
                (:action_id, :tenant_id, :gate_id, :action, :actor_id, :notes,
                 :idempotency_key, :created_at)
            """
        ),
        {
            "action_id": action_id,
            "tenant_id": tenant_id,
            "gate_id": gate_id,
            "action": action,
            "actor_id": actor_id,
            "notes": notes,
            "idempotency_key": idempotency_key,
            "created_at": now,
        },
    )

    status_map = {"APPROVE": "APPROVED", "REJECT": "REJECTED", "CORRECT": "CORRECTED"}
    new_status = status_map.get(action, "PENDING")
    conn.execute(
        text(
            """
            UPDATE human_gates
            SET status = :status, updated_at = :updated_at
            WHERE gate_id = :gate_id
            """
        ),
        {"status": new_status, "updated_at": now, "gate_id": gate_id},
    )

    return {
        "action_id": action_id,
        "tenant_id": tenant_id,
        "gate_id": gate_id,
        "action": action,
        "actor_id": actor_id,
        "notes": notes,
        "created_at": now.isoformat().replace("+00:00", "Z"),
    }


def _validate_cursor(cursor: str | None) -> str | None:
    """Validate cursor format. Returns cursor if valid, raises 400 if invalid."""
    if cursor is None:
        return None
    try:
        from datetime import datetime

        datetime.fromisoformat(cursor.replace("Z", "+00:00"))
        return cursor
    except (ValueError, AttributeError):
        raise IdisHttpError(
            status_code=400,
            code="INVALID_CURSOR",
            message="Invalid cursor format",
        ) from None


@router.get("/deals/{deal_id}/human-gates", response_model=PaginatedHumanGateList)
def list_human_gates(
    deal_id: str,
    request: Request,
    tenant_ctx: RequireTenantContext,
    limit: int = Query(default=50),
    cursor: str | None = Query(default=None),
) -> PaginatedHumanGateList:
    """List human gates for a deal.

    Args:
        deal_id: UUID of the deal.
        request: FastAPI request for DB connection access.
        tenant_ctx: Injected tenant context from auth dependency.
        limit: Maximum number of items to return.
        cursor: Pagination cursor.

    Returns:
        Paginated list of human gates.
    """
    if limit < 1 or limit > 200:
        raise IdisHttpError(
            status_code=400,
            code="INVALID_LIMIT",
            message="limit must be between 1 and 200",
        )

    validated_cursor = _validate_cursor(cursor)

    db_conn = getattr(request.state, "db_conn", None)

    if db_conn is not None:
        items, next_cursor = _list_gates_from_postgres(db_conn, deal_id, limit, validated_cursor)
    else:
        all_items = [
            g
            for g in _IN_MEMORY_GATES.values()
            if g.get("deal_id") == deal_id and g.get("tenant_id") == tenant_ctx.tenant_id
        ]
        all_items.sort(key=lambda x: x["created_at"], reverse=True)
        items = all_items[:limit]
        next_cursor = None

    return PaginatedHumanGateList(
        items=[
            HumanGate(
                gate_id=g["gate_id"],
                deal_id=g["deal_id"],
                gate_type=g["gate_type"],
                status=g["status"],
                created_at=g["created_at"],
            )
            for g in items
        ],
        next_cursor=next_cursor,
    )


def _validate_submit_gate_action_body(body: dict[str, Any] | None) -> SubmitHumanGateActionRequest:
    """Validate submit gate action request body, returning 400 for missing required fields."""
    if body is None or not isinstance(body, dict):
        raise IdisHttpError(
            status_code=400,
            code="INVALID_REQUEST",
            message="Request body is required",
        )
    missing_fields = []
    if "gate_id" not in body:
        missing_fields.append("gate_id")
    if "action" not in body:
        missing_fields.append("action")
    if missing_fields:
        raise IdisHttpError(
            status_code=400,
            code="INVALID_REQUEST",
            message=f"Missing required fields: {', '.join(missing_fields)}",
            details={"missing_fields": missing_fields},
        )
    action = body["action"]
    if action not in ("APPROVE", "REJECT", "CORRECT"):
        raise IdisHttpError(
            status_code=400,
            code="INVALID_REQUEST",
            message="Invalid action; must be APPROVE, REJECT, or CORRECT",
        )
    return SubmitHumanGateActionRequest(
        gate_id=body["gate_id"],
        action=action,
        notes=body.get("notes"),
    )


@router.post("/deals/{deal_id}/human-gates", response_model=HumanGateAction, status_code=201)
async def submit_human_gate_action(
    deal_id: str,
    request: Request,
    tenant_ctx: RequireTenantContext,
) -> HumanGateAction:
    """Submit a human gate action.

    Args:
        deal_id: UUID of the deal.
        request: FastAPI request for DB connection and body access.
        tenant_ctx: Injected tenant context from auth dependency.

    Returns:
        HumanGateAction with action details.

    Raises:
        IdisHttpError: 400 if invalid action, 404 if gate not found.
    """
    try:
        body = await request.json()
    except Exception:
        body = None
    request_body = _validate_submit_gate_action_body(body)

    action_id = str(uuid.uuid4())
    db_conn = getattr(request.state, "db_conn", None)
    idempotency_key = request.headers.get("Idempotency-Key")
    actor_id = tenant_ctx.name

    if db_conn is not None:
        gate_data = _get_gate_from_postgres(db_conn, request_body.gate_id)
        if gate_data is None:
            raise IdisHttpError(status_code=404, code="NOT_FOUND", message="Human gate not found")

        if gate_data["deal_id"] != deal_id:
            raise IdisHttpError(
                status_code=400,
                code="INVALID_REQUEST",
                message="Gate does not belong to this deal",
            )

        action_data = _create_gate_action_in_postgres(
            conn=db_conn,
            action_id=action_id,
            tenant_id=tenant_ctx.tenant_id,
            gate_id=request_body.gate_id,
            action=request_body.action,
            actor_id=actor_id,
            notes=request_body.notes,
            idempotency_key=idempotency_key,
        )
    else:
        gate_data = _IN_MEMORY_GATES.get(request_body.gate_id)
        if gate_data is None or gate_data.get("tenant_id") != tenant_ctx.tenant_id:
            raise IdisHttpError(status_code=404, code="NOT_FOUND", message="Human gate not found")

        if gate_data["deal_id"] != deal_id:
            raise IdisHttpError(
                status_code=400,
                code="INVALID_REQUEST",
                message="Gate does not belong to this deal",
            )

        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        action_data = {
            "action_id": action_id,
            "tenant_id": tenant_ctx.tenant_id,
            "gate_id": request_body.gate_id,
            "action": request_body.action,
            "actor_id": actor_id,
            "notes": request_body.notes,
            "created_at": now,
        }
        _IN_MEMORY_ACTIONS[action_id] = action_data

        status_map = {"APPROVE": "APPROVED", "REJECT": "REJECTED", "CORRECT": "CORRECTED"}
        gate_data["status"] = status_map.get(request_body.action, "PENDING")

    request.state.audit_resource_id = action_id

    return HumanGateAction(
        action_id=action_data["action_id"],
        gate_id=action_data["gate_id"],
        action=action_data["action"],
        actor_id=action_data["actor_id"],
        created_at=action_data["created_at"],
    )


def clear_human_gates_store() -> None:
    """Clear the in-memory human gates store. For testing only."""
    _IN_MEMORY_GATES.clear()
    _IN_MEMORY_ACTIONS.clear()


def create_test_gate(
    gate_id: str,
    tenant_id: str,
    deal_id: str,
    gate_type: str = "CLAIM_VERIFICATION",
) -> dict[str, Any]:
    """Create a test gate in memory. For testing only."""
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    gate_data = {
        "gate_id": gate_id,
        "tenant_id": tenant_id,
        "deal_id": deal_id,
        "gate_type": gate_type,
        "status": "PENDING",
        "created_at": now,
    }
    _IN_MEMORY_GATES[gate_id] = gate_data
    return gate_data
