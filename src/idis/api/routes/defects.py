"""Defects routes for IDIS API.

Provides:
- GET /v1/defects/{defectId} (Get Defect)
- GET /v1/deals/{dealId}/defects (List Defects for Deal)
- GET /v1/claims/{claimId}/defects (List Defects for Claim)
- POST /v1/deals/{dealId}/defects (Create Defect)
- POST /v1/defects/{defectId}/waive (Waive Defect)
- POST /v1/defects/{defectId}/cure (Cure Defect)

Phase 3.4: Defect services + API implementation per DEF-001 traceability.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from idis.api.auth import RequireTenantContext
from idis.services.defects.service import (
    CreateDefectInput,
    CureDefectInput,
    DefectNotFoundError,
    DefectService,
    InvalidStateTransitionError,
    WaiveDefectInput,
    WaiverRequiresActorReasonError,
)

router = APIRouter(prefix="/v1", tags=["Defects"])


class CreateDefectRequest(BaseModel):
    """Request model for creating a defect."""

    claim_id: str | None = None
    defect_type: str
    severity: str | None = None
    description: str = Field(..., min_length=1)
    cure_protocol: str


class WaiveDefectRequest(BaseModel):
    """Request model for waiving a defect."""

    actor: str = Field(..., min_length=1)
    reason: str = Field(..., min_length=1)


class CureDefectRequest(BaseModel):
    """Request model for curing a defect."""

    actor: str = Field(..., min_length=1)
    reason: str = Field(..., min_length=1)


class DefectResponse(BaseModel):
    """Defect response model."""

    defect_id: str
    tenant_id: str
    claim_id: str | None = None
    deal_id: str | None = None
    defect_type: str
    severity: str
    description: str
    cure_protocol: str
    status: str
    waiver_reason: str | None = None
    waived_by: str | None = None
    waived_at: str | None = None
    cured_by: str | None = None
    cured_reason: str | None = None
    cured_at: str | None = None
    created_at: str
    updated_at: str | None = None


class PaginatedDefectList(BaseModel):
    """Paginated list of defects."""

    items: list[DefectResponse]
    next_cursor: str | None = None


def _get_defect_service(request: Request, tenant_id: str) -> DefectService:
    """Get DefectService instance based on DB availability."""
    db_conn = getattr(request.state, "db_conn", None)
    return DefectService(tenant_id=tenant_id, db_conn=db_conn)


def _to_defect_response(defect_data: dict[str, Any]) -> DefectResponse:
    """Convert defect dict to API response model."""
    return DefectResponse(
        defect_id=defect_data["defect_id"],
        tenant_id=defect_data["tenant_id"],
        claim_id=defect_data.get("claim_id"),
        deal_id=defect_data.get("deal_id"),
        defect_type=defect_data["defect_type"],
        severity=defect_data["severity"],
        description=defect_data["description"],
        cure_protocol=defect_data["cure_protocol"],
        status=defect_data.get("status", "OPEN"),
        waiver_reason=defect_data.get("waiver_reason"),
        waived_by=defect_data.get("waived_by"),
        waived_at=defect_data.get("waived_at"),
        cured_by=defect_data.get("cured_by"),
        cured_reason=defect_data.get("cured_reason"),
        cured_at=defect_data.get("cured_at"),
        created_at=defect_data["created_at"],
        updated_at=defect_data.get("updated_at"),
    )


@router.get(
    "/defects/{defect_id}",
    response_model=DefectResponse,
    response_model_exclude_none=True,
    operation_id="getDefect",
)
def get_defect(
    defect_id: str,
    request: Request,
    tenant_ctx: RequireTenantContext,
) -> DefectResponse:
    """Get a defect by ID.

    Args:
        defect_id: UUID of the defect to retrieve.
        request: FastAPI request for DB connection access.
        tenant_ctx: Injected tenant context from auth dependency.

    Returns:
        Defect object if found.

    Raises:
        HTTPException: 404 if defect not found or belongs to different tenant.
    """
    service = _get_defect_service(request, tenant_ctx.tenant_id)

    try:
        defect_data = service.get(defect_id)
    except DefectNotFoundError:
        raise HTTPException(status_code=404, detail="Defect not found") from None

    return _to_defect_response(defect_data)


@router.get(
    "/deals/{deal_id}/defects",
    response_model=PaginatedDefectList,
    response_model_exclude_none=True,
    operation_id="listDealDefects",
)
def list_deal_defects(
    deal_id: str,
    request: Request,
    tenant_ctx: RequireTenantContext,
    limit: int = 50,
    cursor: str | None = None,
) -> PaginatedDefectList:
    """List defects for a deal.

    Tenant isolation: Returns 200 empty for cross-tenant or nonexistent deals
    to avoid existence leaks per TI-001 traceability.

    Args:
        deal_id: UUID of the deal.
        request: FastAPI request for DB connection access.
        tenant_ctx: Injected tenant context from auth dependency.
        limit: Maximum number of defects to return.
        cursor: Pagination cursor.

    Returns:
        Paginated list of defects. Empty list if deal not found or cross-tenant.
    """
    # Tenant-isolated list: query by (tenant_id, deal_id)
    # If no rows, return 200 empty - no existence leak
    service = _get_defect_service(request, tenant_ctx.tenant_id)
    defects, next_cursor = service.list_by_deal(deal_id, limit=limit, cursor=cursor)

    items = [_to_defect_response(d) for d in defects]
    return PaginatedDefectList(items=items, next_cursor=next_cursor)


@router.get(
    "/claims/{claim_id}/defects",
    response_model=PaginatedDefectList,
    response_model_exclude_none=True,
    operation_id="listClaimDefects",
)
def list_claim_defects(
    claim_id: str,
    request: Request,
    tenant_ctx: RequireTenantContext,
    limit: int = 50,
    cursor: str | None = None,
) -> PaginatedDefectList:
    """List defects for a claim.

    Args:
        claim_id: UUID of the claim.
        request: FastAPI request for DB connection access.
        tenant_ctx: Injected tenant context from auth dependency.
        limit: Maximum number of defects to return.
        cursor: Pagination cursor.

    Returns:
        Paginated list of defects.
    """
    service = _get_defect_service(request, tenant_ctx.tenant_id)
    defects, next_cursor = service.list_by_claim(claim_id, limit=limit, cursor=cursor)

    items = [_to_defect_response(d) for d in defects]
    return PaginatedDefectList(items=items, next_cursor=next_cursor)


@router.post(
    "/deals/{deal_id}/defects",
    response_model=DefectResponse,
    response_model_exclude_none=True,
    status_code=201,
    operation_id="createDefect",
)
def create_defect(
    deal_id: str,
    request: Request,
    tenant_ctx: RequireTenantContext,
    body: CreateDefectRequest,
) -> DefectResponse:
    """Create a defect for a deal.

    Args:
        deal_id: UUID of the deal.
        request: FastAPI request for DB connection access.
        tenant_ctx: Injected tenant context from auth dependency.
        body: Defect creation request.

    Returns:
        Created defect.

    Raises:
        HTTPException: 404 if deal not found, 400 if validation fails.
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

    service = _get_defect_service(request, tenant_ctx.tenant_id)
    request_id = getattr(request.state, "request_id", None)

    try:
        input_data = CreateDefectInput(
            claim_id=body.claim_id,
            deal_id=deal_id,
            defect_type=body.defect_type,
            severity=body.severity,
            description=body.description,
            cure_protocol=body.cure_protocol,
            request_id=request_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None

    defect_data = service.create(input_data)

    # Set audit resource_id for middleware correlation
    request.state.audit_resource_id = defect_data["defect_id"]

    return _to_defect_response(defect_data)


@router.post(
    "/defects/{defect_id}/waive",
    response_model=DefectResponse,
    response_model_exclude_none=True,
    operation_id="waiveDefect",
)
def waive_defect(
    defect_id: str,
    request: Request,
    tenant_ctx: RequireTenantContext,
    body: WaiveDefectRequest,
) -> DefectResponse:
    """Waive a defect with actor + reason.

    Per DEF-001 traceability, waiver requires actor and reason.
    Emits defect.waived audit event (HIGH severity).

    Args:
        defect_id: UUID of the defect to waive.
        request: FastAPI request for DB connection access.
        tenant_ctx: Injected tenant context from auth dependency.
        body: Waiver request with actor and reason.

    Returns:
        Updated defect with WAIVED status.

    Raises:
        HTTPException: 404 if defect not found, 400 if actor/reason missing.
    """
    # Set audit resource_id from path param for middleware correlation
    request.state.audit_resource_id = defect_id

    service = _get_defect_service(request, tenant_ctx.tenant_id)
    request_id = getattr(request.state, "request_id", None)

    input_data = WaiveDefectInput(
        actor=body.actor,
        reason=body.reason,
        request_id=request_id,
    )

    try:
        defect_data = service.waive(defect_id, input_data)
    except DefectNotFoundError:
        raise HTTPException(status_code=404, detail="Defect not found") from None
    except WaiverRequiresActorReasonError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    except InvalidStateTransitionError as e:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "DEFECT_INVALID_STATE_TRANSITION",
                "message": str(e),
                "current_status": e.current_status,
                "target_status": e.target_status,
            },
        ) from None

    return _to_defect_response(defect_data)


@router.post(
    "/defects/{defect_id}/cure",
    response_model=DefectResponse,
    response_model_exclude_none=True,
    operation_id="cureDefect",
)
def cure_defect(
    defect_id: str,
    request: Request,
    tenant_ctx: RequireTenantContext,
    body: CureDefectRequest,
) -> DefectResponse:
    """Cure a defect with actor + reason.

    Per DEF-001 traceability, cure requires actor and reason.
    Emits defect.cured audit event (MEDIUM severity).

    Args:
        defect_id: UUID of the defect to cure.
        request: FastAPI request for DB connection access.
        tenant_ctx: Injected tenant context from auth dependency.
        body: Cure request with actor and reason.

    Returns:
        Updated defect with CURED status.

    Raises:
        HTTPException: 404 if defect not found, 400 if actor/reason missing.
    """
    # Set audit resource_id from path param for middleware correlation
    request.state.audit_resource_id = defect_id

    service = _get_defect_service(request, tenant_ctx.tenant_id)
    request_id = getattr(request.state, "request_id", None)

    input_data = CureDefectInput(
        actor=body.actor,
        reason=body.reason,
        request_id=request_id,
    )

    try:
        defect_data = service.cure(defect_id, input_data)
    except DefectNotFoundError:
        raise HTTPException(status_code=404, detail="Defect not found") from None
    except WaiverRequiresActorReasonError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    except InvalidStateTransitionError as e:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "DEFECT_INVALID_STATE_TRANSITION",
                "message": str(e),
                "current_status": e.current_status,
                "target_status": e.target_status,
            },
        ) from None

    return _to_defect_response(defect_data)
