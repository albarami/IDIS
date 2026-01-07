"""IDIS FastAPI application factory.

This module provides the create_app() factory for bootstrapping the IDIS API.
"""

from fastapi import FastAPI

from idis.api.errors import IdisHttpError, idis_http_error_handler
from idis.api.middleware.audit import AuditMiddleware
from idis.api.middleware.openapi_validate import OpenAPIValidationMiddleware
from idis.api.middleware.request_id import RequestIdMiddleware
from idis.api.routes.health import router as health_router
from idis.api.routes.tenancy import router as tenancy_router
from idis.audit.sink import AuditSink

IDIS_VERSION = "6.3"


def create_app(audit_sink: AuditSink | None = None) -> FastAPI:
    """Create and configure the IDIS FastAPI application.

    This factory:
    - Creates a FastAPI app with IDIS metadata
    - Registers middleware in order: RequestId -> OpenAPIValidation -> Audit
    - Registers the IdisHttpError exception handler
    - Mounts the health router (no auth required)
    - Mounts the /v1 tenancy router (auth required)

    Middleware ordering (outermost to innermost):
    1. AuditMiddleware - outermost, captures all responses including early returns
    2. RequestIdMiddleware - ensures request_id is set before any validation
    3. OpenAPIValidationMiddleware - innermost, handles auth, sets tenant_context, operation_id, body_sha256

    Note: Starlette middleware is added in reverse order (last added = outermost).
    AuditMiddleware is added last so it wraps everything and can emit audit events
    even when OpenAPIValidationMiddleware returns early (e.g., 400 INVALID_JSON).
    RequestIdMiddleware must run before OpenAPIValidationMiddleware so request_id
    is available even when validation fails.

    Args:
        audit_sink: Optional AuditSink instance for testing. If None, uses default.

    Returns:
        Configured FastAPI application instance.
    """
    app = FastAPI(
        title="IDIS API (VC Edition)",
        description="Institutional Deal Intelligence System - Enterprise API",
        version=IDIS_VERSION,
    )

    app.add_middleware(OpenAPIValidationMiddleware)
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(AuditMiddleware, sink=audit_sink)

    app.add_exception_handler(IdisHttpError, idis_http_error_handler)

    app.include_router(health_router)
    app.include_router(tenancy_router)

    return app
