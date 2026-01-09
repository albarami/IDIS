"""OpenTelemetry span enrichment middleware for IDIS API.

Enriches request spans with IDIS-specific attributes per v6.3:
- idis.request_id (from RequestIdMiddleware)
- idis.tenant_id (from TenantContext)
- idis.actor_id (from TenantContext)
- idis.actor_roles (from TenantContext)
- idis.openapi_operation_id (from OpenAPIValidationMiddleware)

This middleware runs after OpenAPIValidationMiddleware to ensure tenant context
and operation_id are available for enrichment.

Security (per docs/IDIS_Security_Threat_Model_v6_3.md):
- Never adds secrets, Authorization headers, or request bodies to spans
- Tenant/actor IDs are internal attributes only (not propagated outbound)
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)


class TracingEnrichmentMiddleware(BaseHTTPMiddleware):
    """Middleware that enriches OpenTelemetry spans with IDIS context.

    Must be placed after OpenAPIValidationMiddleware in the middleware stack
    so that tenant_context and openapi_operation_id are available.

    Attributes added to current span:
    - idis.request_id: Request correlation ID
    - idis.tenant_id: Tenant UUID
    - idis.actor_id: Actor UUID
    - idis.actor_roles: Comma-separated role list
    - idis.openapi_operation_id: OpenAPI operation ID
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Enrich current span with IDIS attributes."""
        self._enrich_span(request)
        return await call_next(request)

    def _enrich_span(self, request: Request) -> None:
        """Add IDIS-specific attributes to current span."""
        try:
            from idis.observability.tracing import set_span_attributes

            attributes: dict[str, str | None] = {}

            request_id = getattr(request.state, "request_id", None)
            if request_id:
                attributes["idis.request_id"] = str(request_id)

            tenant_ctx = getattr(request.state, "tenant_context", None)
            if tenant_ctx is not None:
                if hasattr(tenant_ctx, "tenant_id") and tenant_ctx.tenant_id:
                    attributes["idis.tenant_id"] = str(tenant_ctx.tenant_id)
                if hasattr(tenant_ctx, "actor_id") and tenant_ctx.actor_id:
                    attributes["idis.actor_id"] = str(tenant_ctx.actor_id)
                if hasattr(tenant_ctx, "roles") and tenant_ctx.roles:
                    attributes["idis.actor_roles"] = ",".join(tenant_ctx.roles)

            operation_id = getattr(request.state, "openapi_operation_id", None)
            if operation_id:
                attributes["idis.openapi_operation_id"] = str(operation_id)

            path_template = getattr(request.state, "openapi_path_template", None)
            if path_template:
                attributes["http.route"] = str(path_template)

            if attributes:
                set_span_attributes(attributes)

        except ImportError:
            pass
        except Exception as e:
            logger.debug("Failed to enrich span: %s", e)
