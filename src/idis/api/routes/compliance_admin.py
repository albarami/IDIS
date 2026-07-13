"""BYOK key and legal-hold management routes (Slice98 Task 6).

ADMIN-only management surface over the EXISTING compliance cores (``compliance/byok.py`` and
``compliance/retention.py``) through their store seams - the same defaults the real
``ComplianceEnforcedStore`` boundary consults, so a route-driven revoke/hold governs actual
storage reads and deletions. Per the locked Task 6 decisions:

- KMS boundary: policy METADATA only; the raw key alias never appears in responses, audit
  events, or logs (hash+length only), and the durable store persists no raw aliases at all.
- Audit is dual-layer: the cores keep their audit-fatal domain emission (audit-before-write,
  so a write failure still leaves the attempt on the record while leaving NO durable state),
  and AuditMiddleware emits the validated request-shaped event via the operation map.
- Hold reasons arrive in the request body and are hashed immediately by the core; they are
  never echoed, audited, logged, or persisted in plaintext.
- Tenancy comes ONLY from ``RequireTenantContext``; bodies are ``additionalProperties: false``.
  Cross-tenant/unknown hold ids answer a uniform 404 (no existence oracle, ADR-011).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from idis.api.auth import RequireTenantContext
from idis.audit.sink import AuditSink
from idis.compliance.byok import (
    configure_key,
    policy_alias_sha256,
    revoke_key,
    rotate_key,
)
from idis.compliance.retention import HoldTarget, apply_hold, lift_hold

router = APIRouter(prefix="/v1", tags=["ComplianceAdmin"])


class _StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")  # never accept tenant_id (or anything else) here


class ByokKeyRequest(_StrictBody):
    key_alias: Annotated[str, Field(min_length=1, description="Customer KMS key alias")]


class ByokKeyResponse(BaseModel):
    key_alias_hash: str
    key_state: str


class ApplyHoldRequest(_StrictBody):
    target_type: Literal["DEAL", "DOCUMENT", "ARTIFACT"]
    target_id: Annotated[str, Field(min_length=1, description="Resource to hold")]
    reason: Annotated[str, Field(description="Hold reason; hashed immediately, never stored")]


class HoldResponse(BaseModel):
    hold_id: str
    target_type: str
    target_id: str
    applied_at: str


class LiftHoldResponse(BaseModel):
    hold_id: str
    lifted_at: str


def _sink(request: Request) -> AuditSink | None:
    """The app's audit sink for the cores' audit-fatal domain emission (None fails closed)."""
    sink: AuditSink | None = getattr(request.app.state, "audit_sink", None)
    return sink


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


@router.post("/byok/key", response_model=ByokKeyResponse, status_code=201)
def configure_byok_key(
    request_body: ByokKeyRequest,
    tenant_ctx: RequireTenantContext,
    request: Request,
) -> ByokKeyResponse:
    """Configure the tenant's BYOK key policy (ADMIN-only)."""
    policy = configure_key(tenant_ctx, request_body.key_alias, _sink(request))
    alias_hash = policy_alias_sha256(policy)[:16]
    request.state.audit_resource_id = alias_hash
    return ByokKeyResponse(key_alias_hash=alias_hash, key_state=policy.key_state.value)


@router.post("/byok/key/rotate", response_model=ByokKeyResponse, status_code=200)
def rotate_byok_key(
    request_body: ByokKeyRequest,
    tenant_ctx: RequireTenantContext,
    request: Request,
) -> ByokKeyResponse:
    """Rotate the tenant's BYOK key to a new alias (ADMIN-only)."""
    policy = rotate_key(tenant_ctx, request_body.key_alias, _sink(request))
    alias_hash = policy_alias_sha256(policy)[:16]
    request.state.audit_resource_id = alias_hash
    return ByokKeyResponse(key_alias_hash=alias_hash, key_state=policy.key_state.value)


@router.post("/byok/key/revoke", response_model=ByokKeyResponse, status_code=200)
def revoke_byok_key(
    tenant_ctx: RequireTenantContext,
    request: Request,
) -> ByokKeyResponse:
    """Revoke the tenant's BYOK key: Class2/3 access denies until re-keyed (ADMIN-only)."""
    policy = revoke_key(tenant_ctx, _sink(request))
    alias_hash = policy_alias_sha256(policy)[:16]
    request.state.audit_resource_id = alias_hash
    return ByokKeyResponse(key_alias_hash=alias_hash, key_state=policy.key_state.value)


@router.post("/legal-holds", response_model=HoldResponse, status_code=201)
def apply_legal_hold(
    request_body: ApplyHoldRequest,
    tenant_ctx: RequireTenantContext,
    request: Request,
) -> HoldResponse:
    """Apply a legal hold; the target cannot be deleted until lifted (ADMIN-only)."""
    hold = apply_hold(
        tenant_ctx,
        HoldTarget(request_body.target_type),
        request_body.target_id,
        request_body.reason,
        _sink(request),
    )
    request.state.audit_resource_id = hold.hold_id
    return HoldResponse(
        hold_id=hold.hold_id,
        target_type=hold.target_type.value,
        target_id=hold.target_id,
        applied_at=_iso(hold.applied_at),
    )


@router.post("/legal-holds/{hold_id}/lift", response_model=LiftHoldResponse, status_code=200)
def lift_legal_hold(
    hold_id: str,
    tenant_ctx: RequireTenantContext,
    request: Request,
) -> LiftHoldResponse:
    """Lift a legal hold (ADMIN-only). Unknown or cross-tenant ids answer a uniform 404."""
    hold = lift_hold(tenant_ctx, hold_id, _sink(request))
    request.state.audit_resource_id = hold.hold_id
    lifted_at = hold.lifted_at
    assert lifted_at is not None  # lift_hold always sets it on success
    return LiftHoldResponse(hold_id=hold.hold_id, lifted_at=_iso(lifted_at))
