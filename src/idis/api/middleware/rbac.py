"""RBAC/ABAC enforcement middleware for IDIS API.

Implements deny-by-default authorization per v6.3 Security Threat Model:
- Applies to all /v1 requests with authenticated tenant context
- Requires operation_id from OpenAPI middleware (fail-closed if missing)
- Extracts resource context from path parameters
- Calls policy_check() and returns 403 on denial

Middleware ordering (in main.py):
1. RequestIdMiddleware (outermost)
2. AuditMiddleware
3. OpenAPIValidationMiddleware (sets tenant_context + operation_id)
4. RBACMiddleware (this middleware)
5. IdempotencyMiddleware (innermost)
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from idis.api.auth import TenantContext
from idis.api.error_model import make_error_response_no_request
from idis.api.policy import policy_check

logger = logging.getLogger(__name__)


class RBACMiddleware(BaseHTTPMiddleware):
    """Deny-by-default RBAC/ABAC enforcement middleware.

    Behavior:
    1. Skip non-/v1 paths (handled elsewhere or public)
    2. If no tenant_context, skip (auth middleware handles 401)
    3. Require openapi_operation_id (fail-closed: deny if missing)
    4. Extract resource context from path params
    5. Call policy_check() and return 403 on denial

    All denials use the normative error envelope with X-Request-Id header.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Process request with RBAC enforcement."""
        path = request.url.path
        request_id: str | None = getattr(request.state, "request_id", None)

        if not path.startswith("/v1"):
            return await call_next(request)

        tenant_ctx: TenantContext | None = getattr(request.state, "tenant_context", None)
        if tenant_ctx is None:
            return await call_next(request)

        operation_id: str | None = getattr(request.state, "openapi_operation_id", None)
        if operation_id is None:
            # Missing operation_id means this method/path combination is not in
            # the OpenAPI spec. Treat as "method not allowed" (405), not as an
            # RBAC denial (403). This keeps unsupported methods in the OpenAPI-
            # invalid category rather than authorization-denied category.
            logger.warning(
                "Method not in OpenAPI spec: %s %s",
                request.method,
                path,
                extra={"request_id": request_id},
            )
            return make_error_response_no_request(
                code="METHOD_NOT_ALLOWED",
                message="Method not allowed for this resource",
                http_status=405,
                request_id=request_id,
                details=None,
            )

        resource_ctx = self._extract_resource_context(request)

        decision = policy_check(
            tenant_id=tenant_ctx.tenant_id,
            actor_id=tenant_ctx.actor_id,
            roles=tenant_ctx.roles,
            operation_id=operation_id,
            method=request.method,
            deal_id=resource_ctx.get("deal_id"),
            claim_id=resource_ctx.get("claim_id"),
            doc_id=resource_ctx.get("doc_id"),
            run_id=resource_ctx.get("run_id"),
            debate_id=resource_ctx.get("debate_id"),
        )

        if not decision.allow:
            logger.info(
                "RBAC denied: %s for actor=%s operation=%s",
                decision.message,
                tenant_ctx.actor_id,
                operation_id,
                extra={"request_id": request_id, "decision_code": decision.code},
            )
            return make_error_response_no_request(
                code=decision.code,
                message=decision.message,
                http_status=403,
                request_id=request_id,
                details=decision.details,
            )

        return await call_next(request)

    def _extract_resource_context(self, request: Request) -> dict[str, str | None]:
        """Extract resource IDs from path parameters.

        Maps OpenAPI path param names to policy_check kwargs:
        - dealId / deal_id -> deal_id
        - claimId / claim_id -> claim_id
        - docId -> doc_id
        - runId -> run_id
        - debateId -> debate_id
        - sanad_id -> sanad_id
        - defect_id -> defect_id

        Returns dict with None for missing params. Fail-closed on malformed values.
        """
        path_params: dict[str, str] = dict(request.path_params) if request.path_params else {}

        result: dict[str, str | None] = {
            "deal_id": None,
            "claim_id": None,
            "doc_id": None,
            "run_id": None,
            "debate_id": None,
            "sanad_id": None,
            "defect_id": None,
        }

        param_mapping = {
            "dealId": "deal_id",
            "deal_id": "deal_id",
            "claimId": "claim_id",
            "claim_id": "claim_id",
            "docId": "doc_id",
            "runId": "run_id",
            "debateId": "debate_id",
            "sanad_id": "sanad_id",
            "defect_id": "defect_id",
        }

        for openapi_name, policy_name in param_mapping.items():
            value = path_params.get(openapi_name)
            if value is not None and isinstance(value, str) and value.strip():
                result[policy_name] = value.strip()

        return result
