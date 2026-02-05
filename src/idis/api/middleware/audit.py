"""Audit logging middleware for IDIS API.

Implements append-only audit event emission for all mutating /v1 requests.
Validates audit events via the Phase 1.4 validator and fails closed on any error.

Design requirements (v6.3):
- Applies to /v1 paths with methods POST, PUT, PATCH, DELETE
- Builds v6.3-compliant AuditEvent
- Validates via validate_audit_event (fail closed)
- Emits to append-only JSONL sink
- No raw body capture - only hashes/refs
- Tenant isolation: only emit when valid tenant context exists
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from idis.api.error_model import make_error_response_no_request
from idis.audit.sink import AuditSink, AuditSinkError, JsonlFileAuditSink
from idis.validators.audit_event_validator import validate_audit_event

try:
    from idis.audit.postgres_sink import PostgresAuditSink
except ImportError:
    PostgresAuditSink = None  # type: ignore[misc,assignment]

logger = logging.getLogger(__name__)

MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

OPERATION_ID_TO_EVENT_TYPE: dict[str, tuple[str, str, str]] = {
    "createDeal": ("deal.created", "MEDIUM", "deal"),
    "updateDeal": ("deal.updated", "MEDIUM", "deal"),
    "createDealDocument": ("document.created", "MEDIUM", "document"),
    "ingestDocument": ("document.ingestion.started", "LOW", "document"),
    "deleteDocument": ("document.deleted", "MEDIUM", "document"),
    "createClaim": ("claim.created", "MEDIUM", "claim"),
    "updateClaim": ("claim.updated", "MEDIUM", "claim"),
    "createSanad": ("sanad.created", "MEDIUM", "sanad"),
    "updateSanad": ("sanad.updated", "MEDIUM", "sanad"),
    "setSanadCorroboration": ("sanad.corroboration.changed", "MEDIUM", "sanad"),
    "createDefect": ("defect.created", "HIGH", "defect"),
    "waiveDefect": ("defect.waived", "HIGH", "defect"),
    "cureDefect": ("defect.cured", "MEDIUM", "defect"),
    "runCalc": ("calc.started", "LOW", "calc"),
    "startRun": ("deal.run.started", "LOW", "deal"),
    "startDebate": ("debate.started", "LOW", "debate"),
    "submitHumanGateAction": ("human_gate.action.submitted", "MEDIUM", "human_gate"),
    "createOverride": ("override.created", "HIGH", "override"),
    "generateDeliverable": ("deliverable.requested", "LOW", "deliverable"),
    "createWebhook": ("webhook.created", "MEDIUM", "webhook"),
}


def _build_error_response(
    code: str,
    message: str,
    request_id: str | None,
) -> JSONResponse:
    """Build a 500 error JSON response for audit failures using shared error model."""
    return make_error_response_no_request(
        code=code,
        message=message,
        http_status=500,
        request_id=request_id,
        details=None,
    )


def _build_audit_event(
    request: Request,
    response: Response,
    event_type: str,
    severity: str,
    resource_type: str,
) -> dict[str, Any] | None:
    """Build a v6.3-compliant AuditEvent dict.

    Args:
        request: The FastAPI request object
        response: The response object with status_code
        event_type: The audit event type (e.g., "deal.created")
        severity: The severity level (LOW, MEDIUM, HIGH, CRITICAL)
        resource_type: The resource type (deal, document, claim, etc.)

    Returns:
        AuditEvent dict ready for validation and emission
    """
    request_id: str = getattr(request.state, "request_id", str(uuid.uuid4()))
    tenant_ctx = getattr(request.state, "tenant_context", None)

    tenant_id = tenant_ctx.tenant_id if tenant_ctx else "unknown"

    actor_id = "unknown"
    actor_type = "SERVICE"
    roles: list[str] = []

    if tenant_ctx:
        actor_id = tenant_ctx.name
        actor_type = "SERVICE"
        roles = ["INTEGRATION_SERVICE"]

    idempotency_key = request.headers.get("Idempotency-Key")

    ip_address = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("User-Agent", "unknown")

    body_sha256 = getattr(request.state, "request_body_sha256", None)
    hashes: list[str] = []
    if body_sha256:
        hashes.append(body_sha256)

    # Priority for resource_id:
    # 1) request.state.audit_resource_id (set by route/service)
    # 2) Fail closed if missing for successful mutations
    resource_id = getattr(request.state, "audit_resource_id", None)
    if resource_id is None:
        # For successful mutations (2xx/3xx), we MUST have a real resource_id
        # Fail closed rather than fabricate an ID
        if response.status_code < 400:
            logger.error(
                "Audit resource_id missing for successful mutation: %s %s -> %d",
                request.method,
                request.url.path,
                response.status_code,
            )
            # Return None to signal fail-closed to caller
            return None
        # For error responses, use fallback value (event may still be useful for debugging)
        resource_id = "unknown"

    # Check for event type override (e.g., sanad.integrity.failed)
    event_type_override = getattr(request.state, "audit_event_type_override", None)
    severity_override = getattr(request.state, "audit_severity_override", None)
    if event_type_override:
        event_type = event_type_override
    if severity_override:
        severity = severity_override

    event: dict[str, Any] = {
        "event_id": str(uuid.uuid4()),
        "occurred_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "tenant_id": tenant_id,
        "actor": {
            "actor_type": actor_type,
            "actor_id": actor_id,
            "roles": roles,
            "ip": ip_address,
            "user_agent": user_agent,
        },
        "request": {
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
        },
        "resource": {
            "resource_type": resource_type,
            "resource_id": resource_id,
        },
        "event_type": event_type,
        "severity": severity,
        "summary": f"{event_type} via {request.method} {request.url.path}",
        "payload": {
            "hashes": hashes,
            "refs": [],
        },
    }

    if idempotency_key:
        event["request"]["idempotency_key"] = idempotency_key

    return event


class AuditMiddleware(BaseHTTPMiddleware):
    """Middleware that emits audit events for mutating /v1 requests.

    Behavior:
    - Applies to /v1 paths with methods POST, PUT, PATCH, DELETE
    - Skips audit if no tenant context (unauthorized requests)
    - Builds v6.3-compliant AuditEvent
    - Validates event via validate_audit_event
    - Emits to configured AuditSink (Postgres when db_conn available, else JSONL)
    - Fails closed: returns 500 AUDIT_EMIT_FAILED on validation/emission failure

    Ordering:
    - Must run after RequestIdMiddleware (needs request_id)
    - Must run after DBTransactionMiddleware (needs db_conn for Postgres sink)
    - Must run before OpenAPIValidationMiddleware (audit wraps validation)
    """

    def __init__(
        self,
        app: ASGIApp,
        sink: AuditSink | None = None,
        postgres_sink: PostgresAuditSink | None = None,
    ) -> None:
        """Initialize the audit middleware.

        Args:
            app: The ASGI application
            sink: Optional AuditSink instance for non-Postgres emission.
                  If None, uses JsonlFileAuditSink.
            postgres_sink: Optional PostgresAuditSink for in-transaction emission.
                          If None and Postgres is configured, creates one lazily.
        """
        super().__init__(app)
        self._sink = sink if sink is not None else JsonlFileAuditSink()
        self._postgres_sink = postgres_sink

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Process request and emit audit event for mutations."""
        path = request.url.path
        method = request.method
        request_id: str | None = getattr(request.state, "request_id", None)

        if not path.startswith("/v1") or method not in MUTATING_METHODS:
            return await call_next(request)

        response = await call_next(request)

        # Skip audit for idempotency replay - already audited on first request
        if response.headers.get("X-IDIS-Idempotency-Replay") == "true":
            return response

        tenant_ctx = getattr(request.state, "tenant_context", None)
        if tenant_ctx is None:
            return response

        operation_id = getattr(request.state, "openapi_operation_id", None)

        if operation_id is None or operation_id not in OPERATION_ID_TO_EVENT_TYPE:
            # operation_id is missing - this is an unsupported/unknown operation.
            # If the response already indicates an error (status >= 400), the request
            # was rejected by OpenAPI validation or another layer. Do not attempt
            # audit emission - just return the existing error response unchanged.
            # This prevents leaking internal audit details for unsupported methods.
            if response.status_code >= 400:
                return response

            # If status < 400 but operation_id is missing, a mutation succeeded
            # without an auditable operationId. This is dangerous - fail closed.
            logger.error(
                "Mutation succeeded without auditable operation_id: %s %s -> %d",
                method,
                path,
                response.status_code,
                extra={"request_id": request_id},
            )
            return _build_error_response(
                "AUDIT_EMIT_FAILED",
                "Audit required for mutation but operation not auditable",
                request_id,
            )

        event_type, severity, resource_type = OPERATION_ID_TO_EVENT_TYPE[operation_id]

        # For 4xx client error responses, no mutation occurred (request was rejected
        # due to validation, not found, etc.). Skip audit - nothing to record.
        # Only audit successful mutations (2xx) and potentially 5xx (where mutation
        # may have occurred before the error).
        if 400 <= response.status_code < 500:
            return response

        audit_event = _build_audit_event(
            request=request,
            response=response,
            event_type=event_type,
            severity=severity,
            resource_type=resource_type,
        )

        # _build_audit_event returns None if resource_id is missing for successful mutation
        if audit_event is None:
            return _build_error_response(
                "AUDIT_EMIT_FAILED",
                "Audit resource_id missing for mutation - fail closed",
                request_id,
            )

        validation_result = validate_audit_event(audit_event)
        if not validation_result.passed:
            error_codes = [e.code for e in validation_result.errors]
            logger.error(
                "Audit event validation failed: %s",
                error_codes,
                extra={"request_id": request_id},
            )
            return _build_error_response(
                "AUDIT_EMIT_FAILED",
                "Audit event validation failed",
                request_id,
            )

        db_conn = getattr(request.state, "db_conn", None)

        try:
            if db_conn is not None and PostgresAuditSink is not None:
                if self._postgres_sink is None:
                    self._postgres_sink = PostgresAuditSink()
                self._postgres_sink.emit_in_tx(db_conn, audit_event)
            else:
                self._sink.emit(audit_event)
        except AuditSinkError as e:
            logger.error(
                "Audit sink emission failed: %s",
                str(e),
                extra={"request_id": request_id},
            )
            return _build_error_response(
                "AUDIT_EMIT_FAILED",
                "Failed to emit audit event",
                request_id,
            )

        return response
