"""Data residency enforcement for IDIS (v6.3 Task 7.5).

Implements fail-closed region pinning per Data Residency Model v6.3 §3:
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

    service_region_normalized = service_region.strip().lower()
    tenant_region_normalized = tenant_region.lower()

    if tenant_region_normalized != service_region_normalized:
        logger.warning(
            "Residency violation: tenant_id=%s, tenant_region=%s, service_region=%s",
            tenant_ctx.tenant_id,
            tenant_region_normalized,
            service_region_normalized,
        )
        raise IdisHttpError(
            status_code=403,
            code="RESIDENCY_REGION_MISMATCH",
            message="Access denied",
        )


def enforce_region_pin_safe(tenant_ctx: TenantContext, service_region: str | None) -> None:
    """Enforce region pin with graceful handling of missing service region.

    This variant is for use in middleware where we want to enforce residency
    but need to handle the case where service region might not be configured
    (e.g., during development or testing).

    If service_region is None or empty, enforcement is skipped with a warning.
    In production, service_region should always be set.

    Args:
        tenant_ctx: The tenant context from authentication.
        service_region: The region this service instance is deployed in, or None.

    Raises:
        IdisHttpError: 403 if tenant region doesn't match service region.
    """
    if not service_region or not service_region.strip():
        logger.warning(
            "Residency enforcement skipped: service region not configured. "
            "Set %s environment variable for production.",
            IDIS_SERVICE_REGION_ENV,
        )
        return

    enforce_region_pin(tenant_ctx, service_region)
