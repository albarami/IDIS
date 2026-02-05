"""Data residency enforcement middleware for IDIS API.

Implements region pinning per v6.3 Data Residency Model §3:
- Applies to all /v1 requests after tenant authentication
- Enforces tenant data_region matches service region
- Fails closed: missing config or mismatch returns 403

Middleware ordering (in main.py):
1. RequestIdMiddleware (outermost)
2. AuditMiddleware
3. OpenAPIValidationMiddleware (sets tenant_context)
4. ResidencyMiddleware (this middleware - region enforcement)
5. RBACMiddleware
6. IdempotencyMiddleware (innermost)
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from idis.api.auth import TenantContext
from idis.api.error_model import make_error_response_no_request
from idis.api.errors import IdisHttpError
from idis.compliance.residency import (
    IDIS_SERVICE_REGION_ENV,
    enforce_region_pin,
)

logger = logging.getLogger(__name__)


class ResidencyMiddleware(BaseHTTPMiddleware):
    """Middleware that enforces data residency region pinning.

    Behavior (FAIL-CLOSED):
    1. Skip non-/v1 paths (health, public endpoints)
    2. If no tenant_context, skip (auth middleware handles 401)
    3. Get service region from environment or constructor
    4. If service region not configured, DENY with 403 (fail closed)
    5. Call enforce_region_pin() - fails closed on mismatch

    All denials use the normative error envelope with X-Request-Id header.
    Error messages are generic to prevent existence leakage.
    """

    def __init__(
        self,
        app: ASGIApp,
        service_region: str | None = None,
    ) -> None:
        """Initialize the residency middleware.

        Args:
            app: The ASGI application.
            service_region: Override service region (for testing).
                           If None, reads from IDIS_SERVICE_REGION env var.
        """
        super().__init__(app)
        self._service_region = service_region

    def _get_service_region(self) -> str | None:
        """Get the service region, preferring constructor override."""
        if self._service_region is not None:
            return self._service_region
        return os.environ.get(IDIS_SERVICE_REGION_ENV, "").strip() or None

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Process request with residency enforcement."""
        path = request.url.path
        request_id: str | None = getattr(request.state, "request_id", None)

        if not path.startswith("/v1"):
            return await call_next(request)

        tenant_ctx: TenantContext | None = getattr(request.state, "tenant_context", None)
        if tenant_ctx is None:
            return await call_next(request)

        service_region = self._get_service_region()

        if not service_region:
            logger.error(
                "Residency enforcement DENIED: %s not configured (fail-closed)",
                IDIS_SERVICE_REGION_ENV,
                extra={"request_id": request_id},
            )
            return make_error_response_no_request(
                code="RESIDENCY_SERVICE_REGION_UNSET",
                message="Access denied",
                http_status=403,
                request_id=request_id,
                details=None,
            )

        try:
            enforce_region_pin(tenant_ctx, service_region)
        except IdisHttpError as e:
            logger.info(
                "Residency denied: code=%s tenant_id=%s",
                e.code,
                tenant_ctx.tenant_id,
                extra={"request_id": request_id},
            )
            return make_error_response_no_request(
                code=e.code,
                message=e.message,
                http_status=e.status_code,
                request_id=request_id,
                details=None,
            )

        return await call_next(request)
