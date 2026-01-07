"""Deals routes for IDIS API.

Provides POST /v1/deals and GET /v1/deals/{dealId} per OpenAPI spec.
"""

import uuid
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from idis.api.auth import RequireTenantContext

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


_deals_store: dict[str, dict[str, Any]] = {}


@router.post("/deals", response_model=Deal, status_code=201)
def create_deal(
    request_body: CreateDealRequest,
    tenant_ctx: RequireTenantContext,
) -> Deal:
    """Create a new deal.

    Args:
        request_body: Deal creation request with name and company_name.
        tenant_ctx: Injected tenant context from auth dependency.

    Returns:
        Created Deal object with generated deal_id.
    """
    from datetime import UTC, datetime

    deal_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    deal_data = {
        "deal_id": deal_id,
        "tenant_id": tenant_ctx.tenant_id,
        "name": request_body.name,
        "company_name": request_body.company_name,
        "status": "NEW",
        "stage": request_body.stage,
        "tags": request_body.tags or [],
        "created_at": now,
        "updated_at": None,
    }

    _deals_store[deal_id] = deal_data

    return Deal(
        deal_id=deal_id,
        name=request_body.name,
        company_name=request_body.company_name,
        status="NEW",
        stage=request_body.stage,
        tags=request_body.tags,
        created_at=now,
        updated_at=None,
    )


@router.get("/deals/{deal_id}", response_model=Deal)
def get_deal(
    deal_id: str,
    tenant_ctx: RequireTenantContext,
) -> Deal:
    """Get a deal by ID.

    Args:
        deal_id: UUID of the deal to retrieve.
        tenant_ctx: Injected tenant context from auth dependency.

    Returns:
        Deal object if found.

    Raises:
        HTTPException: 404 if deal not found or belongs to different tenant.
    """
    from fastapi import HTTPException

    deal_data = _deals_store.get(deal_id)

    if deal_data is None:
        raise HTTPException(status_code=404, detail="Deal not found")

    if deal_data.get("tenant_id") != tenant_ctx.tenant_id:
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
    _deals_store.clear()
