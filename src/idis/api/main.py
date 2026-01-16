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
from idis.api.middleware.db_tx import DBTransactionMiddleware
from idis.api.middleware.idempotency import IdempotencyMiddleware
from idis.api.middleware.openapi_validate import OpenAPIValidationMiddleware
from idis.api.middleware.rate_limit import RateLimitMiddleware
from idis.api.middleware.rbac import RBACMiddleware
from idis.api.middleware.request_id import RequestIdMiddleware
from idis.api.middleware.tracing import TracingEnrichmentMiddleware
from idis.api.routes.audit import router as audit_router
from idis.api.routes.claims import router as claims_router
from idis.api.routes.deals import router as deals_router
from idis.api.routes.defects import router as defects_router
from idis.api.routes.documents import router as documents_router
from idis.api.routes.health import router as health_router
from idis.api.routes.sanad import router as sanad_router
from idis.api.routes.tenancy import router as tenancy_router
from idis.api.routes.webhooks import router as webhooks_router
from idis.audit.sink import AuditSink
from idis.idempotency.store import SqliteIdempotencyStore
from idis.observability.tracing import configure_tracing, instrument_fastapi, instrument_httpx
from idis.rate_limit.limiter import TenantRateLimiter

try:
    from idis.audit.postgres_sink import PostgresAuditSink
except ImportError:
    PostgresAuditSink = None  # type: ignore[misc,assignment]

try:
    from idis.idempotency.postgres_store import PostgresIdempotencyStore
except ImportError:
    PostgresIdempotencyStore = None  # type: ignore[misc,assignment]

IDIS_VERSION = "6.3"


def create_app(
    audit_sink: AuditSink | None = None,
    idempotency_store: SqliteIdempotencyStore | None = None,
    rate_limiter: TenantRateLimiter | None = None,
    postgres_audit_sink: PostgresAuditSink | None = None,
    postgres_idempotency_store: PostgresIdempotencyStore | None = None,
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
    2. DBTransactionMiddleware - opens DB connection when Postgres configured
    3. AuditMiddleware - captures all responses including early returns
    4. OpenAPIValidationMiddleware - handles auth, sets tenant context + DB tenant
    5. RateLimitMiddleware - tenant-scoped rate limiting (sees tenant + roles)
    6. RBACMiddleware - enforces deny-by-default authorization
    7. IdempotencyMiddleware - innermost, uses tenant context and operation_id

    Note: Starlette middleware is added in reverse order (last added = outermost).
    RequestIdMiddleware is added last so it runs first and sets request_id.
    DBTransactionMiddleware runs early to provide db_conn for downstream middleware.
    AuditMiddleware wraps OpenAPIValidation to capture even 400 INVALID_JSON.
    RateLimitMiddleware runs after auth to have tenant context for rate limiting.
    RBACMiddleware requires tenant_context and operation_id from OpenAPIValidationMiddleware.
    IdempotencyMiddleware is innermost so it has access to tenant_context and
    openapi_operation_id set by OpenAPIValidationMiddleware.

    Args:
        audit_sink: Optional AuditSink instance for testing. If None, uses default.
        idempotency_store: Optional SQLite idempotency store for testing.
        rate_limiter: Optional TenantRateLimiter for testing. If None, uses default.
        postgres_audit_sink: Optional PostgresAuditSink for in-transaction audit.
        postgres_idempotency_store: Optional PostgresIdempotencyStore for in-tx idempotency.

    Returns:
        Configured FastAPI application instance.
    """
    app = FastAPI(
        title="IDIS API (VC Edition)",
        description="Institutional Deal Intelligence System - Enterprise API",
        version=IDIS_VERSION,
    )

    app.state.audit_sink = audit_sink

    configure_tracing()
    instrument_httpx()

    app.add_middleware(
        IdempotencyMiddleware,
        store=idempotency_store,
        postgres_store=postgres_idempotency_store,
    )
    app.add_middleware(RBACMiddleware)
    app.add_middleware(RateLimitMiddleware, limiter=rate_limiter)
    app.add_middleware(TracingEnrichmentMiddleware)
    app.add_middleware(OpenAPIValidationMiddleware)
    app.add_middleware(AuditMiddleware, sink=audit_sink, postgres_sink=postgres_audit_sink)
    app.add_middleware(DBTransactionMiddleware)
    app.add_middleware(RequestIdMiddleware)

    instrument_fastapi(app)

    app.add_exception_handler(IdisHttpError, idis_http_error_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, request_validation_error_handler)
    app.add_exception_handler(Exception, generic_exception_handler)

    app.include_router(health_router)
    app.include_router(tenancy_router)
    app.include_router(deals_router)
    app.include_router(documents_router)
    app.include_router(claims_router)
    app.include_router(sanad_router)
    app.include_router(defects_router)
    app.include_router(webhooks_router)
    app.include_router(audit_router)

    return app
