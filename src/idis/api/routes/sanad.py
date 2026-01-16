"""Sanad routes for IDIS API.

Provides:
- GET /v1/sanads/{sanadId} (Get Sanad)
- GET /v1/deals/{dealId}/sanads (List Sanads for Deal)
- POST /v1/deals/{dealId}/sanads (Create Sanad)
- PATCH /v1/sanads/{sanadId} (Update Sanad)
- POST /v1/sanads/{sanadId}/corroboration (Set Corroboration)

Phase 3.4: Sanad Trust Framework services + API implementation.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from idis.api.auth import RequireTenantContext
from idis.services.sanad.service import (
    CreateSanadInput,
    SanadNotFoundError,
    SanadService,
    UpdateSanadInput,
)

router = APIRouter(prefix="/v1", tags=["Sanad"])


class TransmissionNodeRequest(BaseModel):
    """Transmission node input for API requests."""

    node_id: str | None = None
    node_type: str
    actor_type: str
    actor_id: str
    input_refs: list[dict[str, Any]] = Field(default_factory=list)
    output_refs: list[dict[str, Any]] = Field(default_factory=list)
    timestamp: str | None = None
    confidence: float | None = None


class CreateSanadRequest(BaseModel):
    """Request model for creating a sanad."""

    claim_id: str
    primary_evidence_id: str
    corroborating_evidence_ids: list[str] = Field(default_factory=list)
    transmission_chain: list[TransmissionNodeRequest] = Field(default_factory=list)
    extraction_confidence: float = Field(default=0.9, ge=0.0, le=1.0)


class UpdateSanadRequest(BaseModel):
    """Request model for updating a sanad."""

    corroborating_evidence_ids: list[str] | None = None
    transmission_chain: list[TransmissionNodeRequest] | None = None


class SetCorroborationRequest(BaseModel):
    """Request model for setting corroboration."""

    corroborating_evidence_ids: list[str]


class SanadComputedResponse(BaseModel):
    """Computed fields in sanad response."""

    grade: str
    grade_rationale: str | None = None
    corroboration_level: str
    independent_chain_count: int = Field(..., ge=0)


class TransmissionNodeResponse(BaseModel):
    """Transmission node in API response."""

    node_id: str
    node_type: str
    actor_type: str
    actor_id: str
    input_refs: list[dict[str, Any]] = Field(default_factory=list)
    output_refs: list[dict[str, Any]] = Field(default_factory=list)
    timestamp: str | None = None
    confidence: float | None = None


class SanadResponse(BaseModel):
    """Sanad response model."""

    sanad_id: str
    tenant_id: str
    claim_id: str
    deal_id: str
    primary_evidence_id: str
    corroborating_evidence_ids: list[str] = Field(default_factory=list)
    transmission_chain: list[TransmissionNodeResponse] = Field(default_factory=list)
    computed: SanadComputedResponse
    created_at: str
    updated_at: str | None = None


class PaginatedSanadList(BaseModel):
    """Paginated list of sanads."""

    items: list[SanadResponse]
    next_cursor: str | None = None


def _get_sanad_service(request: Request, tenant_id: str) -> SanadService:
    """Get SanadService instance based on DB availability."""
    db_conn = getattr(request.state, "db_conn", None)
    return SanadService(tenant_id=tenant_id, db_conn=db_conn)


def _to_sanad_response(sanad_data: dict[str, Any]) -> SanadResponse:
    """Convert sanad dict to API response model."""
    computed_data = sanad_data.get("computed", {})
    computed = SanadComputedResponse(
        grade=computed_data.get("grade", "D"),
        grade_rationale=computed_data.get("grade_rationale"),
        corroboration_level=computed_data.get("corroboration_level", "AHAD_1"),
        independent_chain_count=computed_data.get("independent_chain_count", 1),
    )

    chain = []
    for node in sanad_data.get("transmission_chain", []):
        chain.append(
            TransmissionNodeResponse(
                node_id=node.get("node_id", ""),
                node_type=node.get("node_type", ""),
                actor_type=node.get("actor_type", ""),
                actor_id=node.get("actor_id", ""),
                input_refs=node.get("input_refs", []),
                output_refs=node.get("output_refs", []),
                timestamp=node.get("timestamp"),
                confidence=node.get("confidence"),
            )
        )

    return SanadResponse(
        sanad_id=sanad_data["sanad_id"],
        tenant_id=sanad_data["tenant_id"],
        claim_id=sanad_data["claim_id"],
        deal_id=sanad_data["deal_id"],
        primary_evidence_id=sanad_data["primary_evidence_id"],
        corroborating_evidence_ids=sanad_data.get("corroborating_evidence_ids", []),
        transmission_chain=chain,
        computed=computed,
        created_at=sanad_data["created_at"],
        updated_at=sanad_data.get("updated_at"),
    )


@router.get(
    "/sanads/{sanad_id}",
    response_model=SanadResponse,
    response_model_exclude_none=True,
    operation_id="getSanad",
)
def get_sanad(
    sanad_id: str,
    request: Request,
    tenant_ctx: RequireTenantContext,
) -> SanadResponse:
    """Get a sanad by ID.

    Args:
        sanad_id: UUID of the sanad to retrieve.
        request: FastAPI request for DB connection access.
        tenant_ctx: Injected tenant context from auth dependency.

    Returns:
        Sanad object if found.

    Raises:
        HTTPException: 404 if sanad not found or belongs to different tenant.
    """
    service = _get_sanad_service(request, tenant_ctx.tenant_id)

    try:
        sanad_data = service.get(sanad_id)
    except SanadNotFoundError:
        raise HTTPException(status_code=404, detail="Sanad not found") from None

    return _to_sanad_response(sanad_data)


@router.get(
    "/deals/{deal_id}/sanads",
    response_model=PaginatedSanadList,
    response_model_exclude_none=True,
    operation_id="listDealSanads",
)
def list_deal_sanads(
    deal_id: str,
    request: Request,
    tenant_ctx: RequireTenantContext,
    limit: int = 50,
    cursor: str | None = None,
) -> PaginatedSanadList:
    """List sanads for a deal.

    Args:
        deal_id: UUID of the deal.
        request: FastAPI request for DB connection access.
        tenant_ctx: Injected tenant context from auth dependency.
        limit: Maximum number of sanads to return.
        cursor: Pagination cursor.

    Returns:
        Paginated list of sanads.
    """
    from idis.persistence.repositories.deals import (
        DealsRepository,
        InMemoryDealsRepository,
    )

    db_conn = getattr(request.state, "db_conn", None)
    deals_repo: DealsRepository | InMemoryDealsRepository
    if db_conn is not None:
        deals_repo = DealsRepository(db_conn, tenant_ctx.tenant_id)
    else:
        deals_repo = InMemoryDealsRepository(tenant_ctx.tenant_id)

    deal_data = deals_repo.get(deal_id)
    if deal_data is None:
        raise HTTPException(status_code=404, detail="Deal not found")

    service = _get_sanad_service(request, tenant_ctx.tenant_id)
    sanads, next_cursor = service.list_by_deal(deal_id, limit=limit, cursor=cursor)

    items = [_to_sanad_response(s) for s in sanads]
    return PaginatedSanadList(items=items, next_cursor=next_cursor)


@router.post(
    "/deals/{deal_id}/sanads",
    response_model=SanadResponse,
    response_model_exclude_none=True,
    status_code=201,
    operation_id="createSanad",
)
def create_sanad(
    deal_id: str,
    request: Request,
    tenant_ctx: RequireTenantContext,
    body: CreateSanadRequest,
) -> SanadResponse:
    """Create a sanad for a deal.

    Args:
        deal_id: UUID of the deal.
        request: FastAPI request for DB connection access.
        tenant_ctx: Injected tenant context from auth dependency.
        body: Sanad creation request.

    Returns:
        Created sanad.

    Raises:
        HTTPException: 404 if deal not found.
    """
    from idis.persistence.repositories.deals import (
        DealsRepository,
        InMemoryDealsRepository,
    )

    db_conn = getattr(request.state, "db_conn", None)
    deals_repo: DealsRepository | InMemoryDealsRepository
    if db_conn is not None:
        deals_repo = DealsRepository(db_conn, tenant_ctx.tenant_id)
    else:
        deals_repo = InMemoryDealsRepository(tenant_ctx.tenant_id)

    deal_data = deals_repo.get(deal_id)
    if deal_data is None:
        raise HTTPException(status_code=404, detail="Deal not found")

    service = _get_sanad_service(request, tenant_ctx.tenant_id)
    request_id = getattr(request.state, "request_id", None)

    chain = [node.model_dump() for node in body.transmission_chain]

    input_data = CreateSanadInput(
        claim_id=body.claim_id,
        deal_id=deal_id,
        primary_evidence_id=body.primary_evidence_id,
        corroborating_evidence_ids=body.corroborating_evidence_ids,
        transmission_chain=chain,
        extraction_confidence=body.extraction_confidence,
        request_id=request_id,
    )

    sanad_data = service.create(input_data)
    return _to_sanad_response(sanad_data)


@router.patch(
    "/sanads/{sanad_id}",
    response_model=SanadResponse,
    response_model_exclude_none=True,
    operation_id="updateSanad",
)
def update_sanad(
    sanad_id: str,
    request: Request,
    tenant_ctx: RequireTenantContext,
    body: UpdateSanadRequest,
) -> SanadResponse:
    """Update a sanad by ID.

    Args:
        sanad_id: UUID of the sanad to update.
        request: FastAPI request for DB connection access.
        tenant_ctx: Injected tenant context from auth dependency.
        body: Sanad update request.

    Returns:
        Updated sanad.

    Raises:
        HTTPException: 404 if sanad not found.
    """
    service = _get_sanad_service(request, tenant_ctx.tenant_id)
    request_id = getattr(request.state, "request_id", None)

    chain = None
    if body.transmission_chain is not None:
        chain = [node.model_dump() for node in body.transmission_chain]

    input_data = UpdateSanadInput(
        corroborating_evidence_ids=body.corroborating_evidence_ids,
        transmission_chain=chain,
        request_id=request_id,
    )

    try:
        sanad_data = service.update(sanad_id, input_data)
    except SanadNotFoundError:
        raise HTTPException(status_code=404, detail="Sanad not found") from None

    return _to_sanad_response(sanad_data)


@router.post(
    "/sanads/{sanad_id}/corroboration",
    response_model=SanadResponse,
    response_model_exclude_none=True,
    operation_id="setSanadCorroboration",
)
def set_sanad_corroboration(
    sanad_id: str,
    request: Request,
    tenant_ctx: RequireTenantContext,
    body: SetCorroborationRequest,
) -> SanadResponse:
    """Set corroboration for a sanad and re-compute grade.

    Args:
        sanad_id: UUID of the sanad.
        request: FastAPI request for DB connection access.
        tenant_ctx: Injected tenant context from auth dependency.
        body: Corroboration request with evidence IDs.

    Returns:
        Updated sanad with new grade.

    Raises:
        HTTPException: 404 if sanad not found.
    """
    service = _get_sanad_service(request, tenant_ctx.tenant_id)
    request_id = getattr(request.state, "request_id", None)

    try:
        sanad_data = service.set_corroboration(
            sanad_id,
            body.corroborating_evidence_ids,
            request_id=request_id,
        )
    except SanadNotFoundError:
        raise HTTPException(status_code=404, detail="Sanad not found") from None

    return _to_sanad_response(sanad_data)
