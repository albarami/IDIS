"""Tenant-scoped rate limiting middleware for IDIS API.

Implements rate limiting per v6.3 API contracts (§4.3):
- User endpoints: 600 req/min/tenant (burst 2x)
- Integration endpoints: 1200 req/min/tenant (burst 2x)

Rate limit tier is classified by actor role:
- INTEGRATION_SERVICE role => integration tier
- All other roles => user tier

Middleware ordering (in main.py):
1. RequestIdMiddleware (outermost)
2. AuditMiddleware
3. OpenAPIValidationMiddleware (sets tenant_context + operation_id)
4. RateLimitMiddleware (this middleware) - NEW
5. RBACMiddleware
6. IdempotencyMiddleware (innermost)
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from idis.api.auth import TenantContext
from idis.api.error_model import make_error_response_no_request
from idis.rate_limit.limiter import (
    RateLimitConfig,
    TenantRateLimiter,
    classify_tier,
    load_rate_limit_config,
)

logger = logging.getLogger(__name__)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Tenant-scoped rate limiting middleware.

    Behavior:
    1. Skip non-/v1 paths
    2. If no tenant_context, skip (auth middleware handles 401)
    3. Classify tier from roles (INTEGRATION_SERVICE => integration, else user)
    4. Consume token from limiter
    5. If allowed: proceed with optional rate limit headers
    6. If denied: return 429 RATE_LIMIT_EXCEEDED with normative error envelope

    Fails closed: internal limiter errors return 500 RATE_LIMITER_FAILED.
    """

    def __init__(
        self,
        app: ASGIApp,
        limiter: TenantRateLimiter | None = None,
        config: RateLimitConfig | None = None,
    ) -> None:
        """Initialize rate limit middleware.

        Args:
            app: The ASGI application.
            limiter: Optional pre-configured limiter (for testing).
            config: Optional config (for testing). Ignored if limiter provided.

        Raises:
            RateLimitConfigError: If configuration is invalid at startup.
        """
        super().__init__(app)

        if limiter is not None:
            self._limiter = limiter
        else:
            if config is None:
                config = load_rate_limit_config()
            self._limiter = TenantRateLimiter(config)

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Process request with rate limiting."""
        path = request.url.path
        request_id: str | None = getattr(request.state, "request_id", None)

        if not path.startswith("/v1"):
            return await call_next(request)

        tenant_ctx: TenantContext | None = getattr(request.state, "tenant_context", None)
        if tenant_ctx is None:
            return await call_next(request)

        try:
            tier = classify_tier(tenant_ctx.roles)
            decision = self._limiter.check(tenant_ctx.tenant_id, tier)
        except Exception:
            logger.exception(
                "Rate limiter internal error for tenant=%s",
                tenant_ctx.tenant_id,
                extra={"request_id": request_id},
            )
            return make_error_response_no_request(
                code="RATE_LIMITER_FAILED",
                message="Rate limiter internal error",
                http_status=500,
                request_id=request_id,
            )

        if decision.allowed:
            response = await call_next(request)

            response.headers["X-IDIS-RateLimit-Limit"] = str(decision.limit_rpm)
            response.headers["X-IDIS-RateLimit-Remaining"] = str(decision.remaining_tokens)

            return response

        logger.info(
            "Rate limit exceeded: tenant=%s tier=%s limit=%d retry_after=%d",
            tenant_ctx.tenant_id,
            decision.tier.value,
            decision.limit_rpm,
            decision.retry_after_seconds,
            extra={"request_id": request_id},
        )

        retry_after = decision.retry_after_seconds or 1

        response = make_error_response_no_request(
            code="RATE_LIMIT_EXCEEDED",
            message="Rate limit exceeded",
            http_status=429,
            request_id=request_id,
            details={
                "limit_rpm": decision.limit_rpm,
                "tier": decision.tier.value,
                "retry_after_seconds": retry_after,
            },
        )
        response.headers["Retry-After"] = str(retry_after)

        return response
