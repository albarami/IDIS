"""Per-deal erasure + per-tenant compliance-export routes (Slice98 Task 8).

ADMIN-only management surface over the erasure/export cores. Erasure is a two-step deliberate
workflow (request, then execute - the section 6.2 admin-approval step); execution is hold-aware
(deal-level check + the executor's artifact scan, both BEFORE the CRITICAL audit and any
destruction) and results in FULL removal including the deals row, while audit events survive
with their deal_id references. Reasons are hashed by the core and never echoed, audited, or
logged raw. Tenancy comes ONLY from ``RequireTenantContext``; unknown deals and cross-tenant
request ids answer uniform 404s (no existence oracle, ADR-011).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from idis.api.auth import RequireTenantContext
from idis.audit.sink import AuditSink
from idis.compliance.compliance_export import build_compliance_export
from idis.compliance.erasure import request_erasure
from idis.compliance.retention import HoldTarget, block_deletion_if_held
from idis.compliance.task7_erasure import execute_erasure_for_api

router = APIRouter(prefix="/v1", tags=["Erasure"])


class _StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")  # never accept tenant_id (or anything else) here


class ErasureRequestBody(_StrictBody):
    deal_id: Annotated[str, Field(min_length=1, description="Deal to erase")]
    reason: Annotated[str, Field(description="Erasure reason; hashed immediately, never stored")]


class ErasureRequestResponse(BaseModel):
    request_id: str
    deal_id: str
    status: str
    requested_at: str


class ErasureExecuteResponse(BaseModel):
    request_id: str
    status: str
    executed_at: str
    counts: dict[str, int]


class ComplianceExportResponse(BaseModel):
    export_id: str
    object_key: str
    manifest_sha256: str
    counts: dict[str, int]


def _sink(request: Request) -> AuditSink | None:
    sink: AuditSink | None = getattr(request.app.state, "audit_sink", None)
    return sink


def _require_deal(request: Request, tenant_id: str, deal_id: str) -> None:
    """404 if the deal does not exist for this tenant (no existence oracle)."""
    from idis.persistence.repositories.runs import get_runs_repository

    db_conn = getattr(request.state, "db_conn", None)
    if not get_runs_repository(db_conn, tenant_id).deal_exists(deal_id):
        raise HTTPException(status_code=404, detail="Deal not found")


@router.post("/erasure-requests", response_model=ErasureRequestResponse, status_code=201)
def create_erasure_request(
    request_body: ErasureRequestBody,
    tenant_ctx: RequireTenantContext,
    request: Request,
) -> ErasureRequestResponse:
    """Create a durable per-deal erasure request (ADMIN-only; executed separately)."""
    _require_deal(request, tenant_ctx.tenant_id, request_body.deal_id)
    created = request_erasure(tenant_ctx, request_body.deal_id, request_body.reason, _sink(request))
    request.state.audit_resource_id = created.request_id
    return ErasureRequestResponse(
        request_id=created.request_id,
        deal_id=created.deal_id,
        status=created.status.value,
        requested_at=created.requested_at.isoformat().replace("+00:00", "Z"),
    )


@router.post(
    "/erasure-requests/{request_id}/execute",
    response_model=ErasureExecuteResponse,
    status_code=200,
)
def execute_erasure_request(
    request_id: str,
    tenant_ctx: RequireTenantContext,
    request: Request,
) -> ErasureExecuteResponse:
    """Execute a pending erasure request (ADMIN-only; the deliberate approval step)."""

    def _deal_hold_checker(tenant_id: str, deal_id: str) -> None:
        block_deletion_if_held(tenant_ctx, HoldTarget.DEAL, deal_id)

    executed = execute_erasure_for_api(
        tenant_ctx,
        request_id,
        _sink(request),
        hold_checker=_deal_hold_checker,
        client_ip=request.client.host if request.client else "unknown",
        user_agent=request.headers.get("User-Agent", "unknown"),
    )
    request.state.audit_resource_id = executed.request_id
    if executed.terminal_lineage_proof is not None:
        request.state.task7_erasure_terminal_lineage_proof = executed.terminal_lineage_proof
    return ErasureExecuteResponse(
        request_id=executed.request_id,
        status=executed.status,
        executed_at=executed.executed_at.isoformat().replace("+00:00", "Z"),
        counts=dict(executed.counts),
    )


@router.post("/compliance-exports", response_model=ComplianceExportResponse, status_code=201)
def create_compliance_export(
    tenant_ctx: RequireTenantContext,
    request: Request,
) -> ComplianceExportResponse:
    """Build the tenant's sanitized compliance export bundle (ADMIN-only)."""
    descriptor: dict[str, Any] = build_compliance_export(tenant_ctx, _sink(request))
    request.state.audit_resource_id = descriptor["export_id"]
    return ComplianceExportResponse(
        export_id=descriptor["export_id"],
        object_key=descriptor["object_key"],
        manifest_sha256=descriptor["manifest_sha256"],
        counts=descriptor["counts"],
    )
