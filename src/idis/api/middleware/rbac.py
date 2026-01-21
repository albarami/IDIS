"""RBAC/ABAC enforcement middleware for IDIS API.

Implements deny-by-default authorization per v6.3 Security Threat Model:
- Applies to all /v1 requests with authenticated tenant context
- Requires operation_id from OpenAPI middleware (fail-closed if missing)
- Extracts resource context from path parameters
- Calls policy_check() for RBAC and returns 403 on denial
- For deal-scoped operations, enforces ABAC (assignment or group membership)
- Supports break-glass admin override with mandatory audit emission

Middleware ordering (in main.py):
1. RequestIdMiddleware (outermost)
2. AuditMiddleware
3. OpenAPIValidationMiddleware (sets tenant_context + operation_id)
4. RBACMiddleware (this middleware - RBAC + ABAC + break-glass)
5. IdempotencyMiddleware (innermost)
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from idis.api.abac import (
    AbacDecisionCode,
    check_deal_access_with_break_glass,
    resolve_deal_id_for_claim,
)
from idis.api.auth import TenantContext
from idis.api.break_glass import (
    emit_break_glass_audit_event,
    extract_break_glass_token,
    validate_actor_binding,
    validate_break_glass_token,
)
from idis.api.error_model import make_error_response_no_request
from idis.api.errors import IdisHttpError
from idis.api.policy import ABAC_CLAIM_SCOPED_OPS, POLICY_RULES, policy_check

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

        abac_response = await self._check_abac(
            request=request,
            tenant_ctx=tenant_ctx,
            operation_id=operation_id,
            resource_ctx=resource_ctx,
            request_id=request_id,
        )
        if abac_response is not None:
            return abac_response

        return await call_next(request)

    async def _check_abac(
        self,
        request: Request,
        tenant_ctx: TenantContext,
        operation_id: str,
        resource_ctx: dict[str, str | None],
        request_id: str | None,
    ) -> Response | None:
        """Check ABAC for deal-scoped operations.

        For claim endpoints (getClaim, updateClaim, getClaimSanad, listClaimDefects),
        resolves deal_id from claim_id when deal_id is not in path.

        ABAC is enforced for:
        1. Operations with is_deal_scoped=True and deal_id in path
        2. Operations in ABAC_CLAIM_SCOPED_OPS with claim_id (resolve deal first)

        Returns:
            Response if ABAC denied, None if allowed (continue processing).
        """
        deal_id = resource_ctx.get("deal_id")
        claim_id = resource_ctx.get("claim_id")
        is_claim_scoped_op = operation_id in ABAC_CLAIM_SCOPED_OPS

        # For claim-scoped operations without deal_id in path, resolve deal from claim
        if not deal_id and claim_id and is_claim_scoped_op:
            try:
                resolved_deal_id = resolve_deal_id_for_claim(
                    tenant_id=tenant_ctx.tenant_id,
                    claim_id=claim_id,
                    request=request,
                )
            except IdisHttpError as e:
                # Fail-closed: resolver error denies access
                logger.error(
                    "Claim->deal resolution failed: %s",
                    str(e),
                    extra={"request_id": request_id, "claim_id": claim_id},
                )
                return make_error_response_no_request(
                    code="ABAC_RESOLUTION_FAILED",
                    message="Access denied: resource resolution failed",
                    http_status=500,
                    request_id=request_id,
                    details=None,
                )

            if resolved_deal_id:
                deal_id = resolved_deal_id
            else:
                # Claim not found or not accessible - let route return 404
                # Do not pre-deny to avoid existence leak (ADR-011)
                return None

        # Determine if ABAC enforcement is required
        rule = POLICY_RULES.get(operation_id)

        # ABAC required if: deal_id present AND (operation is deal-scoped OR claim-scoped)
        requires_abac = deal_id and (
            (rule is not None and rule.is_deal_scoped) or is_claim_scoped_op
        )

        if not requires_abac:
            return None

        # At this point, deal_id is guaranteed to be non-None (checked in requires_abac)
        # and rule is guaranteed for claim-scoped ops
        assert deal_id is not None, "deal_id must be set when requires_abac is True"

        # For claim-scoped ops, rule may be None but we default is_mutation to False
        is_mutation = rule.is_mutation if rule is not None else False

        break_glass_token_str = extract_break_glass_token(request)
        break_glass_valid = False
        break_glass_token = None

        if break_glass_token_str:
            validation = validate_break_glass_token(
                break_glass_token_str,
                expected_tenant_id=tenant_ctx.tenant_id,
                expected_deal_id=deal_id,
            )
            if validation.valid and validation.token:
                # Validate actor binding - token must be for current actor
                if validate_actor_binding(validation.token, tenant_ctx.actor_id):
                    break_glass_valid = True
                    break_glass_token = validation.token
                else:
                    logger.warning(
                        "Break-glass actor mismatch: token_actor=%s, request_actor=%s",
                        validation.token.actor_id,
                        tenant_ctx.actor_id,
                        extra={"request_id": request_id},
                    )
                    return make_error_response_no_request(
                        code="BREAK_GLASS_ACTOR_MISMATCH",
                        message="Break-glass token not valid for this actor",
                        http_status=403,
                        request_id=request_id,
                        details=None,
                    )

        abac_decision = check_deal_access_with_break_glass(
            tenant_id=tenant_ctx.tenant_id,
            actor_id=tenant_ctx.actor_id,
            roles=tenant_ctx.roles,
            deal_id=deal_id,
            is_mutation=is_mutation,
            break_glass_valid=break_glass_valid,
        )

        if abac_decision.allow:
            if break_glass_valid and break_glass_token:
                already_emitted = getattr(request.state, "break_glass_audit_emitted", False)
                if not already_emitted:
                    try:
                        resource_id = deal_id
                        emit_break_glass_audit_event(
                            request=request,
                            token=break_glass_token,
                            resource_type="deal",
                            resource_id=resource_id,
                            operation_id=operation_id,
                        )
                    except IdisHttpError as e:
                        logger.error(
                            "Break-glass audit emission failed, denying access: %s",
                            str(e),
                            extra={"request_id": request_id},
                        )
                        return make_error_response_no_request(
                            code="BREAK_GLASS_AUDIT_FAILED",
                            message="Break-glass denied: audit emission failed",
                            http_status=500,
                            request_id=request_id,
                            details=None,
                        )
            return None

        http_status = 403
        if abac_decision.code == AbacDecisionCode.DENIED_UNKNOWN_DEAL:
            http_status = 403

        logger.info(
            "ABAC denied: %s for actor=%s deal=%s operation=%s",
            abac_decision.message,
            tenant_ctx.actor_id,
            deal_id,
            operation_id,
            extra={"request_id": request_id, "decision_code": abac_decision.code.value},
        )

        return make_error_response_no_request(
            code=abac_decision.code.value,
            message=abac_decision.message,
            http_status=http_status,
            request_id=request_id,
            details={"requires_break_glass": abac_decision.requires_break_glass}
            if abac_decision.requires_break_glass
            else None,
        )

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
