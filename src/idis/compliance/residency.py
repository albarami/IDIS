"""Data residency enforcement for IDIS (v6.3 Task 7.5).

Implements fail-closed region pinning per Data Residency Model v6.3 section 3:
- Tenant data stays in assigned region
- Cross-region operations forbidden by default
- Missing service region config fails closed (deny, not "assume ok")

Design principles:
- No existence leakage: errors are generic "Access denied"
- Stable error codes for client handling
- Tenant isolation: region mismatch never reveals other tenant info
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from idis.api.errors import IdisHttpError

if TYPE_CHECKING:
    from idis.api.auth import TenantContext

logger = logging.getLogger(__name__)

IDIS_SERVICE_REGION_ENV = "IDIS_SERVICE_REGION"


class ResidencyConfigError(Exception):
    """Raised when service region configuration is missing or invalid.

    This is a startup/configuration error, not a request-time error.
    Services should fail to start if region config is invalid.
    """

    pass


class ResidencyViolationError(Exception):
    """Raised when a residency constraint is violated.

    This is used internally; API layer converts to IdisHttpError.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def get_service_region_from_env() -> str:
    """Get the service region from environment variable.

    Fails closed: missing or empty env var raises ResidencyConfigError.
    This function is strict - it does not default to "unknown" or any
    fallback value. Services must be explicitly configured.

    Returns:
        The service region string (e.g., "me-south-1").

    Raises:
        ResidencyConfigError: If IDIS_SERVICE_REGION is missing or empty.
    """
    region = os.environ.get(IDIS_SERVICE_REGION_ENV, "").strip()
    if not region:
        raise ResidencyConfigError(
            f"Missing required environment variable: {IDIS_SERVICE_REGION_ENV}. "
            "Service region must be explicitly configured for data residency enforcement."
        )
    return region


def _validate_tenant_region(tenant_ctx: TenantContext) -> str:
    """Validate that tenant context has a valid data_region.

    Fails closed: missing or empty data_region is treated as invalid.

    Args:
        tenant_ctx: The tenant context from authentication.

    Returns:
        The validated tenant data_region string.

    Raises:
        ResidencyViolationError: If data_region is missing or empty.
    """
    data_region_raw = getattr(tenant_ctx, "data_region", None)
    if not data_region_raw or not isinstance(data_region_raw, str):
        raise ResidencyViolationError(
            code="RESIDENCY_INVALID_TENANT_CONTEXT",
            message="Access denied",
        )
    data_region: str = data_region_raw.strip()
    if not data_region:
        raise ResidencyViolationError(
            code="RESIDENCY_INVALID_TENANT_CONTEXT",
            message="Access denied",
        )
    return data_region


def _enforce_region_match(
    tenant_region: str, service_region: str, *, tenant_id: str = "unknown"
) -> None:
    """Compare an already-resolved tenant region against the service region (fail-closed).

    The caller must have validated that ``service_region`` is non-empty. Comparison is
    case-insensitive and whitespace-trimmed; a mismatch denies with a generic message (no leak).
    """
    tenant_region_normalized = tenant_region.strip().lower()
    service_region_normalized = service_region.strip().lower()
    if tenant_region_normalized != service_region_normalized:
        logger.warning(
            "Residency violation: tenant_id=%s, tenant_region=%s, service_region=%s",
            tenant_id,
            tenant_region_normalized,
            service_region_normalized,
        )
        raise IdisHttpError(
            status_code=403,
            code="RESIDENCY_REGION_MISMATCH",
            message="Access denied",
        )


def enforce_region_pin(tenant_ctx: TenantContext, service_region: str) -> None:
    """Enforce that tenant data_region matches service region.

    This is the core enforcement function for data residency. It must be
    called for every /v1/* request after TenantContext is available.

    Behavior (fail closed):
    - If tenant_ctx.data_region is missing/empty: deny (403)
    - If tenant_ctx.data_region != service_region: deny (403)
    - Errors use generic message to prevent existence leakage

    Args:
        tenant_ctx: The tenant context from authentication.
        service_region: The region this service instance is deployed in.

    Raises:
        IdisHttpError: 403 with stable code RESIDENCY_REGION_MISMATCH or
                       RESIDENCY_INVALID_TENANT_CONTEXT.
    """
    if not service_region or not service_region.strip():
        logger.error(
            "Service region is empty in enforce_region_pin - this indicates "
            "a configuration error that should have been caught at startup"
        )
        raise IdisHttpError(
            status_code=403,
            code="RESIDENCY_CONFIG_ERROR",
            message="Access denied",
        )

    try:
        tenant_region = _validate_tenant_region(tenant_ctx)
    except ResidencyViolationError as e:
        logger.warning(
            "Residency enforcement: invalid tenant context (tenant_id=%s, code=%s)",
            getattr(tenant_ctx, "tenant_id", "unknown"),
            e.code,
        )
        raise IdisHttpError(
            status_code=403,
            code=e.code,
            message="Access denied",
        ) from None

    _enforce_region_match(tenant_region, service_region, tenant_id=tenant_ctx.tenant_id)


def enforce_region_pin_strict(tenant_ctx: TenantContext, service_region: str | None) -> None:
    """Enforce region pin with fail-closed behavior for missing service region.

    This is the production-safe enforcement function. It NEVER skips enforcement.
    If service_region is None or empty, it fails closed with 403.

    Per v6.3 compliance requirements, missing configuration must deny access,
    not silently skip enforcement.

    Args:
        tenant_ctx: The tenant context from authentication.
        service_region: The region this service instance is deployed in, or None.

    Raises:
        IdisHttpError: 403 if service region not configured or tenant region mismatch.
    """
    if not service_region or not service_region.strip():
        logger.error(
            "Residency enforcement DENIED: service region not configured. "
            "Set %s environment variable. Fail-closed: denying request.",
            IDIS_SERVICE_REGION_ENV,
        )
        raise IdisHttpError(
            status_code=403,
            code="RESIDENCY_SERVICE_REGION_UNSET",
            message="Access denied",
        )

    enforce_region_pin(tenant_ctx, service_region)


def resolve_durable_tenant_region(tenant_ctx: TenantContext) -> str:
    """Resolve the tenant's region from the durable store (residency source of truth).

    Fail-closed: an invalid tenant context, a missing/empty durable region (no row or NULL column),
    or a store/backend failure each raise ResidencyViolationError so the caller denies. The durable
    value - not the request claim - is authoritative when durable residency is enabled.
    """
    from idis.compliance.tenant_region import get_tenant_region_store

    tenant_id = getattr(tenant_ctx, "tenant_id", None)
    if not tenant_id or not isinstance(tenant_id, str):
        raise ResidencyViolationError(
            code="RESIDENCY_INVALID_TENANT_CONTEXT",
            message="Access denied",
        )
    try:
        region = get_tenant_region_store().get_data_region(tenant_id)
    except Exception:
        logger.warning("Durable residency resolution failed for tenant_id=%s", tenant_id)
        raise ResidencyViolationError(
            code="RESIDENCY_RESOLUTION_FAILED",
            message="Access denied",
        ) from None
    if not region or not isinstance(region, str) or not region.strip():
        raise ResidencyViolationError(
            code="RESIDENCY_TENANT_REGION_UNSET",
            message="Access denied",
        )
    return region.strip()


def enforce_residency(tenant_ctx: TenantContext, service_region: str) -> None:
    """Enforce data residency, preferring the durable tenant region when the cutover flag is on.

    Flag off (default): the request claim region is authoritative - identical to
    ``enforce_region_pin`` (legacy behavior, unchanged).

    Flag on (``IDIS_ENABLE_DURABLE_RESIDENCY``): the tenant's region is read from the durable store
    (the ``tenants.data_region`` source of truth) and the request claim is ignored; a missing/empty
    durable region or a resolution failure denies fail-closed. Service-region config is always
    required (empty -> deny).
    """
    from idis.compliance.tenant_region import is_durable_residency_enabled

    if not is_durable_residency_enabled():
        enforce_region_pin(tenant_ctx, service_region)
        return

    if not service_region or not service_region.strip():
        logger.error(
            "Residency enforcement DENIED: service region not configured (durable path). "
            "Fail-closed: denying request."
        )
        raise IdisHttpError(
            status_code=403,
            code="RESIDENCY_CONFIG_ERROR",
            message="Access denied",
        )

    try:
        tenant_region = resolve_durable_tenant_region(tenant_ctx)
    except ResidencyViolationError as e:
        raise IdisHttpError(
            status_code=403,
            code=e.code,
            message="Access denied",
        ) from None

    _enforce_region_match(tenant_region, service_region, tenant_id=tenant_ctx.tenant_id)
