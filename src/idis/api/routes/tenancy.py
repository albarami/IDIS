"""Tenancy routes for IDIS API.

Provides GET /v1/tenants/me per OpenAPI spec.
"""

from fastapi import APIRouter

from idis.api.auth import RequireTenantContext, TenantContext

router = APIRouter(prefix="/v1", tags=["Tenancy"])


@router.get("/tenants/me", response_model=TenantContext)
def get_tenant_me(tenant_ctx: RequireTenantContext) -> TenantContext:
    """Get current tenant context.

    Returns the tenant context extracted from the authenticated API key.
    Requires valid X-IDIS-API-Key header.

    Args:
        tenant_ctx: Injected tenant context from auth dependency.

    Returns:
        TenantContext with tenant_id, name, timezone, and data_region.
    """
    return tenant_ctx
