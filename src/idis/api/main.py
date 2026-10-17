"""IDIS FastAPI application factory.

This module provides the create_app() factory for bootstrapping the IDIS API.
"""

import os
from collections.abc import Mapping
from copy import deepcopy
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute, RouteContext, iter_route_contexts

from idis.api.auth import validate_api_key_registry_config
from idis.api.errors import (
    IdisHttpError,
    generic_exception_handler,
    http_exception_handler,
    idis_http_error_handler,
    request_validation_error_handler,
)
from idis.api.middleware.audit import AuditMiddleware
from idis.api.middleware.db_tx import DBTransactionMiddleware
from idis.api.middleware.http_metrics import HttpMetricsMiddleware
from idis.api.middleware.idempotency import IdempotencyMiddleware
from idis.api.middleware.openapi_validate import OpenAPIValidationMiddleware
from idis.api.middleware.rate_limit import RateLimitMiddleware
from idis.api.middleware.rbac import RBACMiddleware
from idis.api.middleware.request_id import RequestIdMiddleware
from idis.api.middleware.residency import ResidencyMiddleware
from idis.api.middleware.task4_ingestion_scope import Task4IngestionScopeMiddleware
from idis.api.middleware.tracing import TracingEnrichmentMiddleware
from idis.api.middleware.upload_staging import UploadStagingMiddleware
from idis.api.routes.access_admin import router as access_admin_router
from idis.api.routes.audit import router as audit_router
from idis.api.routes.break_glass import router as break_glass_router
from idis.api.routes.claims import router as claims_router
from idis.api.routes.compliance_admin import router as compliance_admin_router
from idis.api.routes.data_room_packages import router as data_room_packages_router
from idis.api.routes.deals import router as deals_router
from idis.api.routes.debate import router as debate_router
from idis.api.routes.defects import router as defects_router
from idis.api.routes.deliverables import router as deliverables_router
from idis.api.routes.documents import router as documents_router
from idis.api.routes.enrichment import router as enrichment_router
from idis.api.routes.erasure_export import router as erasure_export_router
from idis.api.routes.health import router as health_router
from idis.api.routes.human_gates import router as human_gates_router
from idis.api.routes.methodology_versions import router as methodology_versions_router
from idis.api.routes.metrics import router as metrics_router
from idis.api.routes.overrides import router as overrides_router
from idis.api.routes.readiness import router as readiness_router
from idis.api.routes.runs import router as runs_router
from idis.api.routes.sanad import router as sanad_router
from idis.api.routes.tenancy import router as tenancy_router
from idis.api.routes.webhooks import router as webhooks_router
from idis.audit.sink import AuditSink, get_audit_sink
from idis.idempotency.store import SqliteIdempotencyStore
from idis.observability.tracing import configure_tracing, instrument_fastapi, instrument_httpx
from idis.pipeline.worker import start_worker, stop_worker
from idis.rate_limit.limiter import TenantRateLimiter, build_default_rate_limit_store
from idis.runtime.profile import (
    StrictProfileError,
    StrictRuntimeProfile,
    require_runtime_profile,
)
from idis.services.ingestion.defaults import build_default_ingestion_service
from idis.services.runs.strict_full_live import (
    IDIS_STRICT_DOTENV_PATH_ENV,
    is_strict_full_live_required,
    require_strict_process_role,
)
from idis.services.webhooks.dispatcher import (
    start_webhook_dispatcher_worker,
    stop_webhook_dispatcher_worker,
)

try:
    from idis.audit.postgres_sink import PostgresAuditSink
except ImportError:
    PostgresAuditSink = None  # type: ignore[misc,assignment]

try:
    from idis.idempotency.postgres_store import PostgresIdempotencyStore
except ImportError:
    PostgresIdempotencyStore = None  # type: ignore[misc,assignment]

IDIS_VERSION = "6.3"


def _restrict_task4_ingestion_routes(app: FastAPI) -> None:
    """Reduce a bootstrap API to the literal, plan-reviewed Task-4 surface."""

    from idis.runtime.task4_ingestion_scope import TASK4_SERVER_ROUTE_INVENTORY

    retained: list[RouteContext] = []
    observed: list[tuple[str, str]] = []
    for context in iter_route_contexts(app.router.routes):
        path = context.path
        methods = context.methods
        if not isinstance(path, str) or not methods:
            continue
        keys = tuple((str(method).upper(), path) for method in methods)
        admitted = tuple(key for key in keys if key in TASK4_SERVER_ROUTE_INVENTORY)
        if admitted:
            if (
                len(admitted) != len(keys)
                or not isinstance(context.original_route, APIRoute)
                or not callable(context.endpoint)
                or context.stream_item_type is not None
                or context.is_json_stream
                or context.is_sse_stream
            ):
                raise RuntimeError("STRICT_FULL_LIVE_BLOCKED")
            retained.append(context)
            observed.extend(admitted)
    if (
        len(observed) != len(TASK4_SERVER_ROUTE_INVENTORY)
        or frozenset(observed) != TASK4_SERVER_ROUTE_INVENTORY
    ):
        raise RuntimeError("STRICT_FULL_LIVE_BLOCKED")
    # Contexts are read facades, not ASGI routes. Rebuild through the public API
    # so nested include metadata and this app's override provider are compiled
    # together, without changing shared source routers or the app's lifecycle.
    builder = APIRouter(dependency_overrides_provider=app)
    try:
        for context in retained:
            original = context.original_route
            path = context.path
            endpoint = context.endpoint
            if not isinstance(original, APIRoute) or path is None or endpoint is None:
                raise RuntimeError("STRICT_FULL_LIVE_BLOCKED")
            builder.add_api_route(
                path,
                endpoint,
                response_model=context.response_model,
                status_code=context.status_code,
                tags=list(context.tags),
                dependencies=list(context.dependencies),
                summary=context.summary,
                description=context.description,
                response_description=context.response_description,
                responses=deepcopy(context.responses),
                deprecated=context.deprecated,
                methods=list(context.methods or ()),
                operation_id=context.operation_id,
                response_model_include=deepcopy(context.response_model_include),
                response_model_exclude=deepcopy(context.response_model_exclude),
                response_model_by_alias=context.response_model_by_alias,
                response_model_exclude_unset=context.response_model_exclude_unset,
                response_model_exclude_defaults=context.response_model_exclude_defaults,
                response_model_exclude_none=context.response_model_exclude_none,
                include_in_schema=context.include_in_schema,
                response_class=context.response_class,
                name=context.name,
                route_class_override=type(original),
                callbacks=list(context.callbacks) if context.callbacks is not None else None,
                openapi_extra=deepcopy(context.openapi_extra),
                generate_unique_id_function=context.generate_unique_id_function,
                strict_content_type=context.strict_content_type,
            )
    except Exception:
        raise RuntimeError("STRICT_FULL_LIVE_BLOCKED") from None
    rebuilt = [
        (method, route.path)
        for route in builder.routes
        if isinstance(route, APIRoute)
        for method in route.methods or ()
    ]
    if (
        len(rebuilt) != len(TASK4_SERVER_ROUTE_INVENTORY)
        or frozenset(rebuilt) != TASK4_SERVER_ROUTE_INVENTORY
        or any(not isinstance(route, APIRoute) for route in builder.routes)
        or any(
            (route.operation_id, route.unique_id) != (context.operation_id, context.unique_id)
            for route, context in zip(builder.routes, retained, strict=True)
            if isinstance(route, APIRoute)
        )
    ):
        raise RuntimeError("STRICT_FULL_LIVE_BLOCKED")
    app.router.routes[:] = builder.routes
    app.openapi_schema = None


def create_app(
    audit_sink: AuditSink | None = None,
    idempotency_store: SqliteIdempotencyStore | None = None,
    rate_limiter: TenantRateLimiter | None = None,
    postgres_audit_sink: PostgresAuditSink | None = None,
    postgres_idempotency_store: PostgresIdempotencyStore | None = None,
    service_region: str | None = None,
    ingestion_service: Any | None = None,
    strict_profile: StrictRuntimeProfile | None = None,
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
    5. ResidencyMiddleware - enforces data residency region pinning
    6. RateLimitMiddleware - tenant-scoped rate limiting (sees tenant + roles)
    7. RBACMiddleware - enforces deny-by-default authorization
    8. IdempotencyMiddleware - innermost, uses tenant context and operation_id

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
        service_region: Optional service region for residency enforcement. If None,
            reads from IDIS_SERVICE_REGION env var (fails closed with 403 if unset).
        ingestion_service: Optional IngestionService for testing with custom BYOK/store.

    Returns:
        Configured FastAPI application instance.
    """
    strict_values: Mapping[str, str]
    task4_ingestion_scope = False
    strict_dotenv_path = os.environ.get(IDIS_STRICT_DOTENV_PATH_ENV)
    ambient_strict = is_strict_full_live_required(dotenv_path=strict_dotenv_path)
    if strict_profile is None:
        if ambient_strict:
            raise RuntimeError("STRICT_PROFILE_LOAD_FAILED")
        strict_values = os.environ
        strict_full_live = False
    else:
        try:
            require_runtime_profile(strict_profile, command="api")
        except StrictProfileError as exc:
            raise RuntimeError(exc.code) from exc
        strict_values = strict_profile.values
        strict_full_live = True
        task4_ingestion_scope = strict_profile.generation == "bootstrap-v1"

    # The strict composition root has one durable audit sink shared by request middleware,
    # application services, and background workers.  Keep explicit JSONL/in-memory injection for
    # hermetic and non-strict usage, but never let it become the audit authority of a configured
    # strict production process.
    effective_audit_sink: AuditSink
    effective_postgres_audit_sink: PostgresAuditSink | None
    if strict_full_live:
        if not strict_values.get("IDIS_DATABASE_URL", "").strip():
            raise RuntimeError("AUDIT_FAILURE: strict mode requires PostgreSQL audit storage")
        if PostgresAuditSink is None:
            raise RuntimeError("AUDIT_FAILURE: PostgreSQL audit sink is unavailable")
        if audit_sink is None and postgres_audit_sink is not None:
            durable_audit_sink = postgres_audit_sink
        elif audit_sink is None:
            durable_audit_sink = PostgresAuditSink()
        elif not isinstance(audit_sink, PostgresAuditSink):
            raise RuntimeError("AUDIT_FAILURE: strict mode requires PostgresAuditSink")
        else:
            durable_audit_sink = audit_sink
        if postgres_audit_sink is not None and postgres_audit_sink is not durable_audit_sink:
            raise RuntimeError("AUDIT_FAILURE: strict mode received mismatched audit sinks")
        effective_audit_sink = durable_audit_sink
        effective_postgres_audit_sink = durable_audit_sink
    else:
        effective_audit_sink = audit_sink if audit_sink is not None else get_audit_sink()
        effective_postgres_audit_sink = postgres_audit_sink

    app = FastAPI(
        title="IDIS API (VC Edition)",
        description="Institutional Deal Intelligence System - Enterprise API",
        version=IDIS_VERSION,
    )
    # Safe, opaque identity for proving that a later health response came from a
    # newly constructed server instance. It contains no host, process, or path data.
    app.state.boot_id = str(uuid4())
    app.state.strict_runtime_profile = strict_profile
    app.state.task4_ingestion_scope = task4_ingestion_scope

    # Default to the configured audit sink when no test sink is injected. Production invokes
    # create_app() with no args (Dockerfile --factory / idis/app.py), so without this the
    # observability signals (rate-limit denials, idempotency cleanup) and the run-lifecycle
    # events read app.state.audit_sink == None and become silent no-ops. This effective sink is
    # shared by app.state and the AuditMiddleware; the default ingestion wiring keeps its prior
    # audit_sink behavior (unchanged) to avoid an out-of-scope change to ingestion audit.
    app.state.audit_sink = effective_audit_sink
    if ingestion_service is not None:
        app.state.ingestion_service = ingestion_service
    elif strict_full_live:
        app.state.ingestion_service = build_default_ingestion_service(
            audit_sink=effective_audit_sink,
            env=strict_values,
            strict=True,
        )
    else:
        app.state.ingestion_service = build_default_ingestion_service(
            audit_sink=effective_audit_sink
        )

    validate_api_key_registry_config(env=strict_values)

    configure_tracing()
    instrument_httpx()

    app.add_middleware(
        IdempotencyMiddleware,
        store=idempotency_store,
        postgres_store=postgres_idempotency_store,
    )
    # Request order is reversed from registration order: auth/RBAC/rate-limit run
    # before this stage, then idempotency consumes its trusted streaming digest.
    app.add_middleware(UploadStagingMiddleware)
    app.add_middleware(RBACMiddleware)
    if rate_limiter is None:
        # DEC-A: Redis cross-replica store when IDIS_REDIS_URL is set, else in-memory default.
        rate_limiter = TenantRateLimiter(store=build_default_rate_limit_store())
    app.add_middleware(RateLimitMiddleware, limiter=rate_limiter)
    effective_service_region = (
        strict_values.get("IDIS_SERVICE_REGION") if strict_full_live else service_region
    )
    app.add_middleware(ResidencyMiddleware, service_region=effective_service_region)
    app.add_middleware(TracingEnrichmentMiddleware)
    app.add_middleware(OpenAPIValidationMiddleware)
    app.add_middleware(
        AuditMiddleware,
        sink=effective_audit_sink,
        postgres_sink=effective_postgres_audit_sink,
    )
    app.add_middleware(DBTransactionMiddleware)
    app.add_middleware(RequestIdMiddleware)
    # Outermost (added last): measures every request through the full stack with safe labels
    # (method + status class only - never paths/tenant content). Served at GET /metrics.
    app.add_middleware(HttpMetricsMiddleware)

    instrument_fastapi(app)

    # Lifecycle hooks for pipeline worker + webhook dispatcher worker + compliance janitor
    @app.on_event("startup")
    async def startup_event() -> None:
        """Start background workers on app startup (janitor additionally needs its own flag)."""
        from idis.persistence.db import is_postgres_configured
        from idis.services.compliance.janitor import start_compliance_janitor_worker

        if strict_full_live:
            require_strict_process_role("api", env=strict_values)
        if task4_ingestion_scope:
            return
        if is_postgres_configured():
            if not strict_full_live:
                await start_worker()
            await start_webhook_dispatcher_worker()
            await start_compliance_janitor_worker(audit_sink=effective_audit_sink)

    @app.on_event("shutdown")
    async def shutdown_event() -> None:
        """Stop background workers on app shutdown."""
        from idis.services.compliance.janitor import stop_compliance_janitor_worker

        if task4_ingestion_scope:
            return
        if not strict_full_live:
            await stop_worker()
        await stop_webhook_dispatcher_worker()
        await stop_compliance_janitor_worker()

    app.add_exception_handler(IdisHttpError, idis_http_error_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, request_validation_error_handler)
    app.add_exception_handler(Exception, generic_exception_handler)

    app.include_router(health_router)
    app.include_router(metrics_router)
    app.include_router(tenancy_router)
    app.include_router(deals_router)
    app.include_router(documents_router)
    app.include_router(data_room_packages_router)
    app.include_router(claims_router)
    app.include_router(sanad_router)
    app.include_router(defects_router)
    app.include_router(webhooks_router)
    app.include_router(access_admin_router)
    app.include_router(break_glass_router)
    app.include_router(compliance_admin_router)
    app.include_router(erasure_export_router)
    app.include_router(audit_router)
    app.include_router(methodology_versions_router)
    app.include_router(runs_router)
    app.include_router(readiness_router)
    app.include_router(debate_router)
    app.include_router(deliverables_router)
    app.include_router(human_gates_router)
    app.include_router(overrides_router)
    app.include_router(enrichment_router)

    if task4_ingestion_scope:
        _restrict_task4_ingestion_routes(app)
        # Added last so the scope guard is outermost and an absent full-live route
        # cannot leak through auth/OpenAPI validation before the router returns 404.
        app.add_middleware(Task4IngestionScopeMiddleware)

    return app
