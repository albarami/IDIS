"""Audit routes for IDIS API.

Provides GET /v1/audit/events per OpenAPI spec (operationId: listAuditEvents).

Supports both Postgres persistence (when configured) and JSONL file fallback.
Tenant isolation is enforced via RLS (Postgres) or filtering (JSONL).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from idis.api.auth import RequireTenantContext
from idis.audit.query import (
    AuditEventsPage,
    JsonlAuditQueryRepository,
    PostgresAuditQueryRepository,
    _validate_limit,
)
from idis.audit.sink import AUDIT_LOG_PATH_ENV, DEFAULT_AUDIT_LOG_PATH

if TYPE_CHECKING:
    pass

import os

router = APIRouter(prefix="/v1", tags=["Audit"])


class AuditEventResponse(BaseModel):
    """Audit event per OpenAPI AuditEvent schema."""

    event_id: str
    event_type: str
    occurred_at: str


class PaginatedAuditEventList(BaseModel):
    """Paginated list of audit events per OpenAPI PaginatedAuditEventList schema."""

    items: list[AuditEventResponse]
    next_cursor: str | None = None


def _get_audit_log_path() -> Path:
    """Get the JSONL audit log file path from environment or default."""
    env_path = os.environ.get(AUDIT_LOG_PATH_ENV)
    if env_path:
        return Path(env_path)
    return Path(DEFAULT_AUDIT_LOG_PATH)


def _parse_datetime(value: str | None) -> datetime | None:
    """Parse ISO datetime string, returning None if invalid or missing."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _query_audit_events(
    request: Request,
    tenant_id: str,
    limit: int,
    cursor: str | None,
    deal_id: str | None,
    event_type: str | None,
    after: datetime | None,
    before: datetime | None,
) -> AuditEventsPage:
    """Query audit events from Postgres or JSONL fallback.

    Args:
        request: FastAPI request for DB connection access.
        tenant_id: Tenant UUID string.
        limit: Maximum events to return.
        cursor: Pagination cursor.
        deal_id: Optional deal ID filter.
        event_type: Optional event type filter.
        after: Optional timestamp filter (events after).
        before: Optional timestamp filter (events before).

    Returns:
        AuditEventsPage with items and next_cursor.
    """
    db_conn = getattr(request.state, "db_conn", None)

    if db_conn is not None:
        pg_repo = PostgresAuditQueryRepository(db_conn, tenant_id)
        return pg_repo.list_events(
            limit=limit,
            cursor=cursor,
            deal_id=deal_id,
            event_type=event_type,
            after=after,
            before=before,
        )

    audit_log_path = _get_audit_log_path()
    jsonl_repo = JsonlAuditQueryRepository(audit_log_path, tenant_id)
    return jsonl_repo.list_events(
        limit=limit,
        cursor=cursor,
        deal_id=deal_id,
        event_type=event_type,
        after=after,
        before=before,
    )


@router.get("/audit/events", response_model=PaginatedAuditEventList)
def list_audit_events(
    request: Request,
    tenant_ctx: RequireTenantContext,
    limit: int = Query(default=50),
    cursor: str | None = Query(default=None),
    dealId: str | None = Query(default=None, alias="dealId"),
    eventType: str | None = Query(default=None, alias="eventType"),
    after: str | None = Query(default=None),
    before: str | None = Query(default=None),
) -> PaginatedAuditEventList:
    """List audit events for the current tenant.

    Returns paginated audit events with stable ordering (occurred_at DESC, event_id DESC).
    Tenant isolation is enforced - only events for the authenticated tenant are returned.

    Args:
        request: FastAPI request for DB connection access.
        tenant_ctx: Injected tenant context from auth dependency.
        limit: Maximum number of events to return (1-200, default 50).
        cursor: Pagination cursor from previous response.
        dealId: Optional filter by deal_id.
        eventType: Optional filter by event_type.
        after: Optional filter for events after this ISO datetime.
        before: Optional filter for events before this ISO datetime.

    Returns:
        Paginated list of audit events belonging to the tenant.
    """
    validated_limit = _validate_limit(limit)
    after_dt = _parse_datetime(after)
    before_dt = _parse_datetime(before)

    page = _query_audit_events(
        request=request,
        tenant_id=tenant_ctx.tenant_id,
        limit=validated_limit,
        cursor=cursor,
        deal_id=dealId,
        event_type=eventType,
        after=after_dt,
        before=before_dt,
    )

    items = [
        AuditEventResponse(
            event_id=item.event_id,
            event_type=item.event_type,
            occurred_at=item.occurred_at,
        )
        for item in page.items
    ]

    return PaginatedAuditEventList(items=items, next_cursor=page.next_cursor)
