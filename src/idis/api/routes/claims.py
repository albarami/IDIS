"""Claims routes for IDIS API.

Provides:
- GET /v1/deals/{dealId}/truth-dashboard (Truth Dashboard)
- GET /v1/claims/{claimId} (Claim Detail)
- GET /v1/claims/{claimId}/sanad (Sanad Chain)

Phase 6.2: Frontend backend contracts implementation.
Supports both Postgres persistence (when configured) and in-memory fallback.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from idis.api.auth import RequireTenantContext
from idis.persistence.repositories.claims import (
    ClaimsRepository,
    DefectsRepository,
    InMemoryClaimsRepository,
    InMemoryDefectsRepository,
    InMemorySanadsRepository,
    SanadsRepository,
    clear_all_claims_stores,
    seed_claim_in_memory,
    seed_defect_in_memory,
    seed_sanad_in_memory,
)

router = APIRouter(prefix="/v1", tags=["Claims"])


class Quantity(BaseModel):
    """Typed value structure for numeric claims."""

    value: float
    unit: str
    currency: str | None = None
    as_of: str | None = None
    time_window: dict[str, Any] | None = None


class Corroboration(BaseModel):
    """Corroboration status for a claim."""

    level: str
    independent_chain_count: int = Field(..., ge=0)


class ClaimResponse(BaseModel):
    """Claim response model per OpenAPI spec."""

    claim_id: str
    deal_id: str
    claim_class: str
    claim_text: str
    predicate: str | None = None
    value: Quantity | None = None
    sanad_id: str | None = None
    claim_grade: str
    corroboration: Corroboration
    claim_verdict: str
    claim_action: str
    defect_ids: list[str] = Field(default_factory=list)
    materiality: str = "MEDIUM"
    ic_bound: bool = False
    created_at: str


class PaginatedClaimList(BaseModel):
    """Paginated list of claims per OpenAPI spec."""

    items: list[ClaimResponse]
    next_cursor: str | None = None


class TruthDashboardSummaryByGrade(BaseModel):
    """Grade counts for truth dashboard summary."""

    A: int = 0
    B: int = 0
    C: int = 0
    D: int = 0


class TruthDashboardSummaryByVerdict(BaseModel):
    """Verdict counts for truth dashboard summary."""

    VERIFIED: int = 0
    INFLATED: int = 0
    CONTRADICTED: int = 0
    UNVERIFIED: int = 0
    SUBJECTIVE: int = 0


class TruthDashboardSummary(BaseModel):
    """Summary statistics for truth dashboard."""

    total_claims: int = Field(..., ge=0)
    by_grade: TruthDashboardSummaryByGrade
    by_verdict: TruthDashboardSummaryByVerdict
    fatal_defects: int = Field(..., ge=0)


class TruthDashboard(BaseModel):
    """Truth dashboard response per OpenAPI spec."""

    deal_id: str
    summary: TruthDashboardSummary
    claims: PaginatedClaimList


class TransmissionNode(BaseModel):
    """Node in the Sanad transmission chain."""

    node_id: str
    node_type: str
    actor_type: str
    actor_id: str
    input_refs: list[dict[str, Any]] = Field(default_factory=list)
    output_refs: list[dict[str, Any]] = Field(default_factory=list)
    timestamp: str
    confidence: float | None = None
    dhabt_score: float | None = None
    verification_method: str | None = None
    notes: str | None = None


class SanadComputed(BaseModel):
    """Computed Sanad values."""

    grade: str
    grade_rationale: str | None = None
    corroboration_level: str
    independent_chain_count: int = Field(..., ge=0)


class SanadResponse(BaseModel):
    """Sanad chain response per OpenAPI spec."""

    sanad_id: str
    claim_id: str
    deal_id: str
    primary_evidence_id: str
    corroborating_evidence_ids: list[str] = Field(default_factory=list)
    transmission_chain: list[TransmissionNode]
    computed: SanadComputed


class CreateClaimRequest(BaseModel):
    """Request model for creating a claim per OpenAPI spec."""

    claim_class: str
    claim_text: str
    predicate: str | None = None
    value: Quantity | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    materiality: str = "MEDIUM"
    ic_bound: bool = False


def _get_claims_repository(
    request: Request,
    tenant_id: str,
) -> ClaimsRepository | InMemoryClaimsRepository:
    """Get claims repository based on DB availability.

    Uses Postgres repository when db_conn is available, in-memory fallback otherwise.
    """
    db_conn = getattr(request.state, "db_conn", None)
    if db_conn is not None:
        return ClaimsRepository(db_conn, tenant_id)
    return InMemoryClaimsRepository(tenant_id)


def _get_sanads_repository(
    request: Request,
    tenant_id: str,
) -> SanadsRepository | InMemorySanadsRepository:
    """Get sanads repository based on DB availability."""
    db_conn = getattr(request.state, "db_conn", None)
    if db_conn is not None:
        return SanadsRepository(db_conn, tenant_id)
    return InMemorySanadsRepository(tenant_id)


def _get_defects_repository(
    request: Request,
    tenant_id: str,
) -> DefectsRepository | InMemoryDefectsRepository:
    """Get defects repository based on DB availability."""
    db_conn = getattr(request.state, "db_conn", None)
    if db_conn is not None:
        return DefectsRepository(db_conn, tenant_id)
    return InMemoryDefectsRepository(tenant_id)


def _get_claim_response(claim_data: dict[str, Any]) -> ClaimResponse:
    """Convert internal claim data to API response model."""
    corroboration = claim_data.get("corroboration") or {
        "level": "AHAD",
        "independent_chain_count": 1,
    }
    if isinstance(corroboration, dict):
        corroboration = Corroboration(**corroboration)

    value = claim_data.get("value")
    if isinstance(value, dict):
        value = Quantity(**value)

    return ClaimResponse(
        claim_id=claim_data["claim_id"],
        deal_id=claim_data["deal_id"],
        claim_class=claim_data["claim_class"],
        claim_text=claim_data["claim_text"],
        predicate=claim_data.get("predicate"),
        value=value,
        sanad_id=claim_data.get("sanad_id"),
        claim_grade=claim_data["claim_grade"],
        corroboration=corroboration,
        claim_verdict=claim_data["claim_verdict"],
        claim_action=claim_data["claim_action"],
        defect_ids=claim_data.get("defect_ids", []),
        materiality=claim_data.get("materiality", "MEDIUM"),
        ic_bound=claim_data.get("ic_bound", False),
        created_at=claim_data["created_at"],
    )


@router.get(
    "/deals/{deal_id}/truth-dashboard",
    response_model=TruthDashboard,
    response_model_exclude_none=True,
)
def get_deal_truth_dashboard(
    deal_id: str,
    request: Request,
    tenant_ctx: RequireTenantContext,
    limit: int = 50,
    cursor: str | None = None,
) -> TruthDashboard:
    """Get truth dashboard for a deal.

    Returns aggregated claim statistics and paginated claims list.
    Claims are sorted by claim_id for stable ordering.

    Args:
        deal_id: UUID of the deal.
        request: FastAPI request for DB connection access.
        tenant_ctx: Injected tenant context from auth dependency.
        limit: Maximum number of claims to return.
        cursor: Pagination cursor (not implemented yet).

    Returns:
        TruthDashboard with summary and claims.

    Raises:
        HTTPException: 404 if deal not found or belongs to different tenant.
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

    claims_repo = _get_claims_repository(request, tenant_ctx.tenant_id)
    defects_repo = _get_defects_repository(request, tenant_ctx.tenant_id)

    tenant_deal_claims, _ = claims_repo.list_by_deal(deal_id, limit=1000)

    by_grade = TruthDashboardSummaryByGrade()
    by_verdict = TruthDashboardSummaryByVerdict()
    fatal_defects = 0

    for claim in tenant_deal_claims:
        grade = claim.get("claim_grade", "D")
        verdict = claim.get("claim_verdict", "UNVERIFIED")

        if grade == "A":
            by_grade.A += 1
        elif grade == "B":
            by_grade.B += 1
        elif grade == "C":
            by_grade.C += 1
        else:
            by_grade.D += 1

        if verdict == "VERIFIED":
            by_verdict.VERIFIED += 1
        elif verdict == "INFLATED":
            by_verdict.INFLATED += 1
        elif verdict == "CONTRADICTED":
            by_verdict.CONTRADICTED += 1
        elif verdict == "UNVERIFIED":
            by_verdict.UNVERIFIED += 1
        else:
            by_verdict.SUBJECTIVE += 1

        for defect_id in claim.get("defect_ids", []):
            defect = defects_repo.get(defect_id)
            if defect and defect.get("severity") == "FATAL":
                fatal_defects += 1

    claim_responses = [_get_claim_response(c) for c in tenant_deal_claims[:limit]]

    summary = TruthDashboardSummary(
        total_claims=len(tenant_deal_claims),
        by_grade=by_grade,
        by_verdict=by_verdict,
        fatal_defects=fatal_defects,
    )

    return TruthDashboard(
        deal_id=deal_id,
        summary=summary,
        claims=PaginatedClaimList(items=claim_responses, next_cursor=None),
    )


@router.get(
    "/deals/{deal_id}/claims",
    response_model=PaginatedClaimList,
    response_model_exclude_none=True,
)
def list_deal_claims(
    deal_id: str,
    request: Request,
    tenant_ctx: RequireTenantContext,
    limit: int = 50,
    cursor: str | None = None,
) -> PaginatedClaimList:
    """List claims for a deal.

    Args:
        deal_id: UUID of the deal.
        request: FastAPI request for DB connection access.
        tenant_ctx: Injected tenant context from auth dependency.
        limit: Maximum number of claims to return.
        cursor: Pagination cursor.

    Returns:
        Paginated list of claims.

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

    claims_repo = _get_claims_repository(request, tenant_ctx.tenant_id)
    claims, next_cursor = claims_repo.list_by_deal(deal_id, limit=limit, cursor=cursor)

    claim_responses = [_get_claim_response(c) for c in claims]
    return PaginatedClaimList(items=claim_responses, next_cursor=next_cursor)


@router.post(
    "/deals/{deal_id}/claims",
    response_model=ClaimResponse,
    response_model_exclude_none=True,
    status_code=201,
    operation_id="createClaim",
)
def create_claim(
    deal_id: str,
    request: Request,
    tenant_ctx: RequireTenantContext,
    body: CreateClaimRequest,
) -> ClaimResponse:
    """Create a claim for a deal.

    All claim mutations go through ClaimService to enforce invariants
    (No-Free-Facts, tenant isolation, audit events).

    Args:
        deal_id: UUID of the deal.
        request: FastAPI request for DB connection access.
        tenant_ctx: Injected tenant context from auth dependency.
        body: Claim creation request.

    Returns:
        Created claim.

    Raises:
        HTTPException: 404 if deal not found, 400 if validation fails.
    """
    from idis.persistence.repositories.deals import (
        DealsRepository,
        InMemoryDealsRepository,
    )
    from idis.services.claims.service import (
        CreateClaimInput,
        NoFreeFactsViolationError,
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

    service = _get_claim_service(request, tenant_ctx.tenant_id)
    value_dict = body.value.model_dump() if body.value else None
    request_id = getattr(request.state, "request_id", None)

    create_input = CreateClaimInput(
        deal_id=deal_id,
        claim_class=body.claim_class,
        claim_text=body.claim_text,
        predicate=body.predicate,
        value=value_dict,
        materiality=body.materiality,
        ic_bound=body.ic_bound,
        request_id=request_id,
    )

    try:
        claim_data = service.create(create_input)
    except NoFreeFactsViolationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None

    # Set audit resource_id for middleware correlation
    request.state.audit_resource_id = claim_data["claim_id"]

    return _get_claim_response(claim_data)


@router.get(
    "/claims/{claim_id}",
    response_model=ClaimResponse,
    response_model_exclude_none=True,
)
def get_claim(
    claim_id: str,
    request: Request,
    tenant_ctx: RequireTenantContext,
) -> ClaimResponse:
    """Get a claim by ID.

    Args:
        claim_id: UUID of the claim to retrieve.
        request: FastAPI request for DB connection access.
        tenant_ctx: Injected tenant context from auth dependency.

    Returns:
        Claim object if found.

    Raises:
        HTTPException: 404 if claim not found or belongs to different tenant.
    """
    claims_repo = _get_claims_repository(request, tenant_ctx.tenant_id)
    claim_data = claims_repo.get(claim_id)

    if claim_data is None:
        raise HTTPException(status_code=404, detail="Claim not found")

    return _get_claim_response(claim_data)


@router.get(
    "/claims/{claim_id}/sanad",
    response_model=SanadResponse,
    response_model_exclude_none=True,
)
def get_claim_sanad(
    claim_id: str,
    request: Request,
    tenant_ctx: RequireTenantContext,
) -> SanadResponse:
    """Get Sanad chain for a claim.

    Returns the evidence chain with deterministic ordering of nodes.

    Args:
        claim_id: UUID of the claim.
        request: FastAPI request for DB connection access.
        tenant_ctx: Injected tenant context from auth dependency.

    Returns:
        Sanad chain if found.

    Raises:
        HTTPException: 404 if claim or sanad not found, or belongs to different tenant.
    """
    claims_repo = _get_claims_repository(request, tenant_ctx.tenant_id)
    sanads_repo = _get_sanads_repository(request, tenant_ctx.tenant_id)

    claim_data = claims_repo.get(claim_id)

    if claim_data is None:
        raise HTTPException(status_code=404, detail="Claim not found")

    sanad_id = claim_data.get("sanad_id")
    if not sanad_id:
        raise HTTPException(status_code=404, detail="Sanad not found for claim")

    sanad_data = sanads_repo.get(sanad_id)
    if sanad_data is None:
        raise HTTPException(status_code=404, detail="Sanad not found")

    transmission_chain = sanad_data.get("transmission_chain", [])
    transmission_chain.sort(key=lambda n: n.get("node_id", ""))

    nodes = [TransmissionNode(**n) for n in transmission_chain]

    computed_data = sanad_data.get("computed", {})
    computed = SanadComputed(
        grade=computed_data.get("grade", "D"),
        grade_rationale=computed_data.get("grade_rationale"),
        corroboration_level=computed_data.get("corroboration_level", "AHAD"),
        independent_chain_count=computed_data.get("independent_chain_count", 1),
    )

    return SanadResponse(
        sanad_id=sanad_id,
        claim_id=claim_id,
        deal_id=claim_data["deal_id"],
        primary_evidence_id=sanad_data.get("primary_evidence_id", ""),
        corroborating_evidence_ids=sanad_data.get("corroborating_evidence_ids", []),
        transmission_chain=nodes,
        computed=computed,
    )


class UpdateClaimRequest(BaseModel):
    """Request model for updating a claim per OpenAPI spec."""

    claim_text: str | None = None
    claim_grade: str | None = None
    claim_verdict: str | None = None
    claim_action: str | None = None
    defect_ids: list[str] | None = None
    materiality: str | None = None
    ic_bound: bool | None = None
    sanad_id: str | None = None
    corroboration: Corroboration | None = None


def _get_claim_service(
    request: Request,
    tenant_id: str,
) -> Any:
    """Get ClaimService instance based on DB availability."""
    from idis.services.claims.service import ClaimService

    db_conn = getattr(request.state, "db_conn", None)
    return ClaimService(tenant_id=tenant_id, db_conn=db_conn)


@router.patch(
    "/claims/{claim_id}",
    response_model=ClaimResponse,
    response_model_exclude_none=True,
    operation_id="updateClaim",
)
def update_claim(
    claim_id: str,
    request: Request,
    tenant_ctx: RequireTenantContext,
    body: UpdateClaimRequest,
) -> ClaimResponse:
    """Update a claim by ID (PATCH per OpenAPI v6.3).

    Args:
        claim_id: UUID of the claim to update.
        request: FastAPI request for DB connection access.
        tenant_ctx: Injected tenant context from auth dependency.
        body: Claim update request.

    Returns:
        Updated claim.

    Raises:
        HTTPException: 404 if claim not found, 400 if No-Free-Facts violation.
    """
    from idis.services.claims.service import (
        ClaimNotFoundError,
        NoFreeFactsViolationError,
        UpdateClaimInput,
    )

    service = _get_claim_service(request, tenant_ctx.tenant_id)

    corroboration_dict = None
    if body.corroboration:
        corroboration_dict = body.corroboration.model_dump()

    request_id = getattr(request.state, "request_id", None)

    update_input = UpdateClaimInput(
        claim_text=body.claim_text,
        claim_grade=body.claim_grade,
        claim_verdict=body.claim_verdict,
        claim_action=body.claim_action,
        defect_ids=body.defect_ids,
        materiality=body.materiality,
        ic_bound=body.ic_bound,
        sanad_id=body.sanad_id,
        corroboration=corroboration_dict,
        request_id=request_id,
    )

    # Set audit resource_id from path param for middleware correlation
    request.state.audit_resource_id = claim_id

    try:
        claim_data = service.update(claim_id, update_input)
    except ClaimNotFoundError:
        raise HTTPException(status_code=404, detail="Claim not found") from None
    except NoFreeFactsViolationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None

    return _get_claim_response(claim_data)


def seed_claim(claim_data: dict[str, Any]) -> None:
    """Seed a claim into the store. For testing only."""
    seed_claim_in_memory(claim_data)


def seed_sanad(sanad_data: dict[str, Any]) -> None:
    """Seed a sanad into the store. For testing only."""
    seed_sanad_in_memory(sanad_data)


def seed_defect(defect_data: dict[str, Any]) -> None:
    """Seed a defect into the store. For testing only."""
    seed_defect_in_memory(defect_data)


def clear_claims_store() -> None:
    """Clear the in-memory claims store. For testing only."""
    from idis.persistence.repositories.claims import clear_claims_in_memory_store

    clear_claims_in_memory_store()


def clear_sanad_store() -> None:
    """Clear the in-memory sanad store. For testing only."""
    from idis.persistence.repositories.claims import clear_sanad_in_memory_store

    clear_sanad_in_memory_store()


def clear_defects_store() -> None:
    """Clear the in-memory defects store. For testing only."""
    from idis.persistence.repositories.claims import clear_defects_in_memory_store

    clear_defects_in_memory_store()


def clear_all_stores() -> None:
    """Clear all in-memory stores. For testing only."""
    clear_all_claims_stores()
