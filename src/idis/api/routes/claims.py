"""Claims routes for IDIS API.

Provides:
- GET /v1/deals/{dealId}/truth-dashboard (Truth Dashboard)
- GET /v1/claims/{claimId} (Claim Detail)
- GET /v1/claims/{claimId}/sanad (Sanad Chain)

Phase 6.2: Frontend backend contracts implementation.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from idis.api.auth import RequireTenantContext

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


_claims_store: dict[str, dict[str, Any]] = {}
_sanad_store: dict[str, dict[str, Any]] = {}
_defects_store: dict[str, dict[str, Any]] = {}


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

    tenant_deal_claims = [
        c
        for c in _claims_store.values()
        if c.get("tenant_id") == tenant_ctx.tenant_id and c.get("deal_id") == deal_id
    ]

    tenant_deal_claims.sort(key=lambda c: c["claim_id"])

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
            defect = _defects_store.get(defect_id)
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
    "/claims/{claim_id}",
    response_model=ClaimResponse,
    response_model_exclude_none=True,
)
def get_claim(
    claim_id: str,
    tenant_ctx: RequireTenantContext,
) -> ClaimResponse:
    """Get a claim by ID.

    Args:
        claim_id: UUID of the claim to retrieve.
        tenant_ctx: Injected tenant context from auth dependency.

    Returns:
        Claim object if found.

    Raises:
        HTTPException: 404 if claim not found or belongs to different tenant.
    """
    claim_data = _claims_store.get(claim_id)

    if claim_data is None:
        raise HTTPException(status_code=404, detail="Claim not found")

    if claim_data.get("tenant_id") != tenant_ctx.tenant_id:
        raise HTTPException(status_code=404, detail="Claim not found")

    return _get_claim_response(claim_data)


@router.get(
    "/claims/{claim_id}/sanad",
    response_model=SanadResponse,
    response_model_exclude_none=True,
)
def get_claim_sanad(
    claim_id: str,
    tenant_ctx: RequireTenantContext,
) -> SanadResponse:
    """Get Sanad chain for a claim.

    Returns the evidence chain with deterministic ordering of nodes.

    Args:
        claim_id: UUID of the claim.
        tenant_ctx: Injected tenant context from auth dependency.

    Returns:
        Sanad chain if found.

    Raises:
        HTTPException: 404 if claim or sanad not found, or belongs to different tenant.
    """
    claim_data = _claims_store.get(claim_id)

    if claim_data is None:
        raise HTTPException(status_code=404, detail="Claim not found")

    if claim_data.get("tenant_id") != tenant_ctx.tenant_id:
        raise HTTPException(status_code=404, detail="Claim not found")

    sanad_id = claim_data.get("sanad_id")
    if not sanad_id:
        raise HTTPException(status_code=404, detail="Sanad not found for claim")

    sanad_data = _sanad_store.get(sanad_id)
    if sanad_data is None:
        raise HTTPException(status_code=404, detail="Sanad not found")

    if sanad_data.get("tenant_id") != tenant_ctx.tenant_id:
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


def seed_claim(claim_data: dict[str, Any]) -> None:
    """Seed a claim into the store. For testing only."""
    _claims_store[claim_data["claim_id"]] = claim_data


def seed_sanad(sanad_data: dict[str, Any]) -> None:
    """Seed a sanad into the store. For testing only."""
    _sanad_store[sanad_data["sanad_id"]] = sanad_data


def seed_defect(defect_data: dict[str, Any]) -> None:
    """Seed a defect into the store. For testing only."""
    _defects_store[defect_data["defect_id"]] = defect_data


def clear_claims_store() -> None:
    """Clear the in-memory claims store. For testing only."""
    _claims_store.clear()


def clear_sanad_store() -> None:
    """Clear the in-memory sanad store. For testing only."""
    _sanad_store.clear()


def clear_defects_store() -> None:
    """Clear the in-memory defects store. For testing only."""
    _defects_store.clear()


def clear_all_stores() -> None:
    """Clear all in-memory stores. For testing only."""
    clear_claims_store()
    clear_sanad_store()
    clear_defects_store()
