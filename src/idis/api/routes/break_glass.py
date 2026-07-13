"""Break-glass grant issuance route (Slice98 Task 5).

ADMIN-only, deliberately NOT deal-scoped: the requester is by definition unassigned to the deal
(that is what break-glass is for), so ABAC must not gate issuance - the access-admin precedent.
The route mints a token through the EXISTING core (``create_break_glass_token``: actor+deal bound,
justification >= 20 chars, duration clamped <= 3600s) for the REQUESTING admin only (self-issuance,
no delegation), and records the grant through the grant-store seam with the FULL SHA-256 of the raw
token. Tenancy comes ONLY from ``RequireTenantContext``; the body schema is
``additionalProperties: false``. Unknown deals return 404 via the tenant-scoped existence check
(same as nonexistent - no oracle, ADR-011). The route sets
``request.state.audit_resource_id = deal_id`` so AuditMiddleware's ``break_glass.issued`` CRITICAL
event carries the deal as its resource; the token and plaintext justification never enter audit
events or logs.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from idis.api.auth import RequireTenantContext
from idis.api.break_glass import create_break_glass_token, validate_break_glass_token
from idis.api.break_glass_grants import BreakGlassGrant, get_break_glass_grant_store
from idis.api.errors import IdisHttpError

router = APIRouter(prefix="/v1", tags=["BreakGlass"])


class BreakGlassGrantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")  # never accept tenant_id/actor_id here

    deal_id: Annotated[str, Field(min_length=1, description="Deal the grant is bound to")]
    justification: Annotated[str, Field(description="Why break-glass access is needed (>= 20)")]
    duration_seconds: Annotated[
        int | None, Field(default=None, description="Validity window; clamped to <= 3600")
    ] = None


class BreakGlassGrantResponse(BaseModel):
    grant_id: str
    token: str
    expires_at: str
    deal_id: str


def _require_deal(request: Request, tenant_id: str, deal_id: str) -> None:
    """404 if the deal does not exist for this tenant (no existence oracle)."""
    from idis.persistence.repositories.runs import get_runs_repository

    db_conn = getattr(request.state, "db_conn", None)
    if not get_runs_repository(db_conn, tenant_id).deal_exists(deal_id):
        raise HTTPException(status_code=404, detail="Deal not found")


@router.post("/break-glass/grants", response_model=BreakGlassGrantResponse, status_code=201)
def create_break_glass_grant(
    request_body: BreakGlassGrantRequest,
    tenant_ctx: RequireTenantContext,
    request: Request,
) -> BreakGlassGrantResponse:
    """Issue a single-use break-glass grant for the requesting admin (ADMIN-only)."""
    _require_deal(request, tenant_ctx.tenant_id, request_body.deal_id)

    kwargs: dict[str, int] = {}
    if request_body.duration_seconds is not None:
        kwargs["duration_seconds"] = request_body.duration_seconds
    token = create_break_glass_token(
        actor_id=tenant_ctx.actor_id,
        tenant_id=tenant_ctx.tenant_id,
        justification=request_body.justification,
        deal_id=request_body.deal_id,
        **kwargs,
    )

    validation = validate_break_glass_token(
        token,
        expected_tenant_id=tenant_ctx.tenant_id,
        expected_deal_id=request_body.deal_id,
    )
    if not validation.valid or validation.token is None:
        # A token this route just minted must validate; anything else is a config/core fault.
        raise IdisHttpError(
            status_code=500,
            code="break_glass_grant_record_failed",
            message="Break-glass grant could not be issued",
        )

    grant = BreakGlassGrant(
        grant_id=validation.token.token_id,
        tenant_id=tenant_ctx.tenant_id,
        deal_id=request_body.deal_id,
        actor_id=tenant_ctx.actor_id,
        justification=request_body.justification.strip(),
        token_sha256=hashlib.sha256(token.encode("utf-8")).hexdigest(),
        issued_at=validation.token.issued_at,
        expires_at=validation.token.expires_at,
    )
    get_break_glass_grant_store().record_grant(grant)

    request.state.audit_resource_id = request_body.deal_id
    return BreakGlassGrantResponse(
        grant_id=grant.grant_id,
        token=token,
        expires_at=datetime.fromtimestamp(grant.expires_at, tz=UTC)
        .isoformat()
        .replace("+00:00", "Z"),
        deal_id=grant.deal_id,
    )
