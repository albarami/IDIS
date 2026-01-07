"""IDIS FastAPI application factory.

This module provides the create_app() factory for bootstrapping the IDIS API.
"""

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError

from idis.api.errors import (
    IdisHttpError,
    generic_exception_handler,
    http_exception_handler,
    idis_http_error_handler,
    request_validation_error_handler,
)
from idis.api.middleware.audit import AuditMiddleware
from idis.api.middleware.idempotency import IdempotencyMiddleware
from idis.api.middleware.openapi_validate import OpenAPIValidationMiddleware
from idis.api.middleware.rate_limit import RateLimitMiddleware
from idis.api.middleware.rbac import RBACMiddleware
from idis.api.middleware.request_id import RequestIdMiddleware
from idis.api.routes.deals import router as deals_router
from idis.api.routes.health import router as health_router
from idis.api.routes.tenancy import router as tenancy_router
from idis.audit.sink import AuditSink
from idis.idempotency.store import SqliteIdempotencyStore
from idis.rate_limit.limiter import TenantRateLimiter

IDIS_VERSION = "6.3"


def create_app(
    audit_sink: AuditSink | None = None,
    idempotency_store: SqliteIdempotencyStore | None = None,
    rate_limiter: TenantRateLimiter | None = None,
) -> FastAPI:
    """Create and configure the IDIS FastAPI application.

    This factory:
    - Creates a FastAPI app with IDIS metadata
    - Registers middleware in correct order for request processing
    - Registers the IdisHttpError exception handler
    - Mounts the health router (no auth required)
    - Mounts the /v1 routers (auth required)

    Middleware ordering (outermost to innermost):
    1. RequestIdMiddleware - outermost, ensures request_id available everywhere
    2. AuditMiddleware - captures all responses including early returns
    3. OpenAPIValidationMiddleware - handles auth and sets tenant context + operation_id
    4. RateLimitMiddleware - tenant-scoped rate limiting (sees tenant + roles)
    5. RBACMiddleware - enforces deny-by-default authorization
    6. IdempotencyMiddleware - innermost, uses tenant context and operation_id

    Note: Starlette middleware is added in reverse order (last added = outermost).
    RequestIdMiddleware is added last so it runs first and sets request_id.
    AuditMiddleware wraps OpenAPIValidation to capture even 400 INVALID_JSON.
    RateLimitMiddleware runs after auth to have tenant context for rate limiting.
    RBACMiddleware requires tenant_context and operation_id from OpenAPIValidationMiddleware.
    IdempotencyMiddleware is innermost so it has access to tenant_context and
    openapi_operation_id set by OpenAPIValidationMiddleware.

    Args:
        audit_sink: Optional AuditSink instance for testing. If None, uses default.
        idempotency_store: Optional idempotency store for testing. If None, uses default.
        rate_limiter: Optional TenantRateLimiter for testing. If None, uses default.

    Returns:
        Configured FastAPI application instance.
    """
    app = FastAPI(
        title="IDIS API (VC Edition)",
        description="Institutional Deal Intelligence System - Enterprise API",
        version=IDIS_VERSION,
    )

    app.add_middleware(IdempotencyMiddleware, store=idempotency_store)
    app.add_middleware(RBACMiddleware)
    app.add_middleware(RateLimitMiddleware, limiter=rate_limiter)
    app.add_middleware(OpenAPIValidationMiddleware)
    app.add_middleware(AuditMiddleware, sink=audit_sink)
    app.add_middleware(RequestIdMiddleware)

    app.add_exception_handler(IdisHttpError, idis_http_error_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, request_validation_error_handler)
    app.add_exception_handler(Exception, generic_exception_handler)

    app.include_router(health_router)
    app.include_router(tenancy_router)
    app.include_router(deals_router)

    return app
