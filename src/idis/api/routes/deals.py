"""Deals routes for IDIS API.

Provides POST /v1/deals and GET /v1/deals/{dealId} per OpenAPI spec.

Supports both Postgres persistence (when configured) and in-memory fallback.
"""

import uuid

from fastapi import APIRouter, Request
from pydantic import BaseModel

from idis.api.auth import RequireTenantContext
from idis.persistence.repositories.deals import (
    DealsRepository,
    InMemoryDealsRepository,
    clear_in_memory_store,
)

router = APIRouter(prefix="/v1", tags=["Deals"])


class CreateDealRequest(BaseModel):
    """Request body for POST /v1/deals."""

    name: str
    company_name: str
    stage: str | None = None
    tags: list[str] | None = None


class Deal(BaseModel):
    """Deal response model per OpenAPI spec."""

    deal_id: str
    name: str
    company_name: str
    status: str
    stage: str | None = None
    tags: list[str] | None = None
    created_at: str
    updated_at: str | None = None


def _get_repository(
    request: Request,
    tenant_id: str,
) -> InMemoryDealsRepository | DealsRepository:
    """Get deals repository from request state or create in-memory fallback.

    Args:
        request: FastAPI request object.
        tenant_id: Tenant UUID string.

    Returns:
        Repository instance (Postgres or in-memory).
    """
    db_conn = getattr(request.state, "db_conn", None)

    if db_conn is not None:
        return DealsRepository(db_conn, tenant_id)

    return InMemoryDealsRepository(tenant_id)


@router.post("/deals", response_model=Deal, status_code=201)
def create_deal(
    request_body: CreateDealRequest,
    request: Request,
    tenant_ctx: RequireTenantContext,
) -> Deal:
    """Create a new deal.

    Args:
        request_body: Deal creation request with name and company_name.
        request: FastAPI request for DB connection access.
        tenant_ctx: Injected tenant context from auth dependency.

    Returns:
        Created Deal object with generated deal_id.
    """
    deal_id = str(uuid.uuid4())
    repo = _get_repository(request, tenant_ctx.tenant_id)

    deal_data = repo.create(
        deal_id=deal_id,
        name=request_body.name,
        company_name=request_body.company_name,
        status="NEW",
        stage=request_body.stage,
        tags=request_body.tags,
    )

    # Set audit resource_id for middleware correlation
    request.state.audit_resource_id = deal_id

    return Deal(
        deal_id=deal_data["deal_id"],
        name=deal_data["name"],
        company_name=deal_data["company_name"],
        status=deal_data["status"],
        stage=deal_data.get("stage"),
        tags=deal_data.get("tags"),
        created_at=deal_data["created_at"],
        updated_at=deal_data.get("updated_at"),
    )


class PaginatedDealList(BaseModel):
    """Paginated list of deals per OpenAPI spec."""

    items: list[Deal]
    next_cursor: str | None = None


@router.get("/deals", response_model=PaginatedDealList)
def list_deals(
    request: Request,
    tenant_ctx: RequireTenantContext,
    limit: int = 50,
    cursor: str | None = None,
) -> PaginatedDealList:
    """List deals for the current tenant.

    Args:
        request: FastAPI request for DB connection access.
        tenant_ctx: Injected tenant context from auth dependency.
        limit: Maximum number of deals to return.
        cursor: Pagination cursor.

    Returns:
        Paginated list of deals belonging to the tenant.
    """
    repo = _get_repository(request, tenant_ctx.tenant_id)
    deals, next_cursor = repo.list(limit=limit, cursor=cursor)

    items = [
        Deal(
            deal_id=d["deal_id"],
            name=d["name"],
            company_name=d["company_name"],
            status=d["status"],
            stage=d.get("stage"),
            tags=d.get("tags"),
            created_at=d["created_at"],
            updated_at=d.get("updated_at"),
        )
        for d in deals
    ]
    return PaginatedDealList(items=items, next_cursor=next_cursor)


@router.get("/deals/{deal_id}", response_model=Deal)
def get_deal(
    deal_id: str,
    request: Request,
    tenant_ctx: RequireTenantContext,
) -> Deal:
    """Get a deal by ID.

    Args:
        deal_id: UUID of the deal to retrieve.
        request: FastAPI request for DB connection access.
        tenant_ctx: Injected tenant context from auth dependency.

    Returns:
        Deal object if found.

    Raises:
        HTTPException: 404 if deal not found or belongs to different tenant.
    """
    from fastapi import HTTPException

    repo = _get_repository(request, tenant_ctx.tenant_id)
    deal_data = repo.get(deal_id)

    if deal_data is None:
        raise HTTPException(status_code=404, detail="Deal not found")

    return Deal(
        deal_id=deal_data["deal_id"],
        name=deal_data["name"],
        company_name=deal_data["company_name"],
        status=deal_data["status"],
        stage=deal_data.get("stage"),
        tags=deal_data.get("tags"),
        created_at=deal_data["created_at"],
        updated_at=deal_data.get("updated_at"),
    )


def clear_deals_store() -> None:
    """Clear the in-memory deals store. For testing only."""
    clear_in_memory_store()
