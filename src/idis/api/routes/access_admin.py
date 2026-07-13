"""ABAC assignment/group management routes for IDIS API (Slice98 Task 2).

ADMIN-only routes that manage the durable ABAC state through the EXISTING assignment-store seam
(``get_manageable_deal_assignment_store`` - the same default store the RBAC/ABAC decision path
consults). Per v6.3 / .windsurf invariants:

- Tenancy comes ONLY from ``RequireTenantContext``; a ``tenant_id`` in the body is rejected by the
  OpenAPI schema (``additionalProperties: false``).
- ADMIN-only via RBAC (policy.py); NOT deal-scoped, so an admin can assign actors to deals they are
  not themselves assigned to (ABAC would otherwise block this).
- Target deal/group existence is verified under the tenant; missing or cross-tenant resources
  return 404 (same as nonexistent - no existence oracle, ADR-011).
- Every mutation is audited by AuditMiddleware via the ``rbac.*`` operation map; the route sets
  ``request.state.audit_resource_id`` and audit-write failure fails the request.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from idis.api.abac import get_manageable_deal_assignment_store
from idis.api.auth import RequireTenantContext

router = APIRouter(prefix="/v1", tags=["AccessAdmin"])


class _StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")  # never accept tenant_id (or anything else) here


class AssignmentRequest(_StrictBody):
    actor_id: Annotated[str, Field(min_length=1, description="Actor to assign to the deal")]


class GroupRequest(_StrictBody):
    name: Annotated[str, Field(default="", description="Human-readable group name")] = ""


class GroupMemberRequest(_StrictBody):
    actor_id: Annotated[str, Field(min_length=1, description="Actor to add to the group")]


class GroupAssignmentRequest(_StrictBody):
    group_id: Annotated[str, Field(min_length=1, description="Group to assign to the deal")]


class GroupResponse(BaseModel):
    group_id: str
    name: str


class OkResponse(BaseModel):
    status: str = "ok"


def _require_deal(request: Request, tenant_id: str, deal_id: str) -> None:
    """404 if the deal does not exist for this tenant (no existence oracle)."""
    from idis.persistence.repositories.runs import get_runs_repository

    db_conn = getattr(request.state, "db_conn", None)
    if not get_runs_repository(db_conn, tenant_id).deal_exists(deal_id):
        raise HTTPException(status_code=404, detail="Deal not found")


@router.post("/deals/{deal_id}/assignments", response_model=OkResponse, status_code=201)
def create_deal_assignment(
    deal_id: str,
    request_body: AssignmentRequest,
    tenant_ctx: RequireTenantContext,
    request: Request,
) -> OkResponse:
    """Assign an actor directly to a deal (ADMIN-only, idempotent)."""
    _require_deal(request, tenant_ctx.tenant_id, deal_id)
    get_manageable_deal_assignment_store().add_assignment(
        tenant_ctx.tenant_id, deal_id, request_body.actor_id
    )
    request.state.audit_resource_id = deal_id
    return OkResponse()


@router.delete("/deals/{deal_id}/assignments/{actor_id}", status_code=204)
def delete_deal_assignment(
    deal_id: str,
    actor_id: str,
    tenant_ctx: RequireTenantContext,
    request: Request,
) -> Response:
    """Remove a direct deal assignment (ADMIN-only)."""
    _require_deal(request, tenant_ctx.tenant_id, deal_id)
    get_manageable_deal_assignment_store().remove_assignment(
        tenant_ctx.tenant_id, deal_id, actor_id
    )
    request.state.audit_resource_id = deal_id
    return Response(status_code=204)


@router.post("/groups", response_model=GroupResponse, status_code=201)
def create_group(
    request_body: GroupRequest,
    tenant_ctx: RequireTenantContext,
    request: Request,
) -> GroupResponse:
    """Create a tenant-scoped group (ADMIN-only). The group id is server-generated."""
    import uuid

    group_id = str(uuid.uuid4())
    get_manageable_deal_assignment_store().create_group(
        tenant_ctx.tenant_id, group_id, request_body.name
    )
    request.state.audit_resource_id = group_id
    return GroupResponse(group_id=group_id, name=request_body.name)


def _require_group(store: Any, tenant_id: str, group_id: str) -> None:
    if not store.group_exists(tenant_id, group_id):
        raise HTTPException(status_code=404, detail="Group not found")


@router.post("/groups/{group_id}/members", response_model=OkResponse, status_code=201)
def add_group_member(
    group_id: str,
    request_body: GroupMemberRequest,
    tenant_ctx: RequireTenantContext,
    request: Request,
) -> OkResponse:
    """Add an actor to a group (ADMIN-only, idempotent)."""
    store = get_manageable_deal_assignment_store()
    _require_group(store, tenant_ctx.tenant_id, group_id)
    store.add_group_member(tenant_ctx.tenant_id, group_id, request_body.actor_id)
    request.state.audit_resource_id = group_id
    return OkResponse()


@router.delete("/groups/{group_id}/members/{actor_id}", status_code=204)
def remove_group_member(
    group_id: str,
    actor_id: str,
    tenant_ctx: RequireTenantContext,
    request: Request,
) -> Response:
    """Remove an actor from a group (ADMIN-only)."""
    store = get_manageable_deal_assignment_store()
    _require_group(store, tenant_ctx.tenant_id, group_id)
    store.remove_group_member(tenant_ctx.tenant_id, group_id, actor_id)
    request.state.audit_resource_id = group_id
    return Response(status_code=204)


@router.post("/deals/{deal_id}/group-assignments", response_model=OkResponse, status_code=201)
def assign_group_to_deal(
    deal_id: str,
    request_body: GroupAssignmentRequest,
    tenant_ctx: RequireTenantContext,
    request: Request,
) -> OkResponse:
    """Assign a group to a deal (ADMIN-only, idempotent)."""
    _require_deal(request, tenant_ctx.tenant_id, deal_id)
    store = get_manageable_deal_assignment_store()
    _require_group(store, tenant_ctx.tenant_id, request_body.group_id)
    store.assign_group_to_deal(tenant_ctx.tenant_id, deal_id, request_body.group_id)
    request.state.audit_resource_id = deal_id
    return OkResponse()


@router.delete("/deals/{deal_id}/group-assignments/{group_id}", status_code=204)
def unassign_group_from_deal(
    deal_id: str,
    group_id: str,
    tenant_ctx: RequireTenantContext,
    request: Request,
) -> Response:
    """Remove a group's deal assignment (ADMIN-only)."""
    _require_deal(request, tenant_ctx.tenant_id, deal_id)
    get_manageable_deal_assignment_store().unassign_group_from_deal(
        tenant_ctx.tenant_id, deal_id, group_id
    )
    request.state.audit_resource_id = deal_id
    return Response(status_code=204)
