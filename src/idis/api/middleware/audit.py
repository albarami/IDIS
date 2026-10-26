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
import os
import re
import uuid
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from idis.api.error_model import make_error_response_no_request
from idis.audit.sink import AuditSink, AuditSinkError, JsonlFileAuditSink
from idis.services.runs.strict_full_live import (
    IDIS_STRICT_DOTENV_PATH_ENV,
    is_strict_full_live_required,
)
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
    "uploadDealDocument": ("document.created", "MEDIUM", "document"),
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
    "importMethodologyVersion": ("methodology.version.imported", "MEDIUM", "methodology"),
    "freezeMethodologyVersion": ("methodology.version.frozen", "HIGH", "methodology"),
    "retryRun": ("deal.run.requeued", "LOW", "deal"),
    "resumeRun": ("deal.run.requeued", "LOW", "deal"),
    "cancelRun": ("deal.run.cancelled", "LOW", "deal"),
    "startDebate": ("debate.started", "LOW", "debate"),
    "submitHumanGateAction": ("human_gate.action.submitted", "MEDIUM", "human_gate"),
    "createOverride": ("override.created", "HIGH", "override"),
    "generateDeliverable": ("deliverable.requested", "LOW", "deliverable"),
    "createWebhook": ("webhook.created", "MEDIUM", "webhook"),
    "createDataRoomPackage": ("data_room_package.created", "MEDIUM", "data_room_package"),
    # Slice98 ABAC assignment/group management (mutation audit completeness).
    "createBreakGlassGrant": ("break_glass.issued", "CRITICAL", "deal"),
    "configureByokKey": ("byok.key.configured", "HIGH", "byok_key"),
    "rotateByokKey": ("byok.key.rotated", "HIGH", "byok_key"),
    "revokeByokKey": ("byok.key.revoked", "HIGH", "byok_key"),
    "applyLegalHold": ("legal_hold.applied", "CRITICAL", "legal_hold"),
    "liftLegalHold": ("legal_hold.lifted", "CRITICAL", "legal_hold"),
    "createErasureRequest": ("erasure.requested", "HIGH", "erasure_request"),
    "executeErasureRequest": ("erasure.executed", "CRITICAL", "erasure_request"),
    "createComplianceExport": ("export.created", "HIGH", "compliance_export"),
    "createDealAssignment": ("rbac.assignment.created", "MEDIUM", "deal"),
    "deleteDealAssignment": ("rbac.assignment.deleted", "MEDIUM", "deal"),
    "createGroup": ("rbac.group.created", "LOW", "group"),
    "addGroupMember": ("rbac.group.member_added", "MEDIUM", "group"),
    "removeGroupMember": ("rbac.group.member_removed", "MEDIUM", "group"),
    "assignGroupToDeal": ("rbac.group.assigned", "MEDIUM", "deal"),
    "unassignGroupFromDeal": ("rbac.group.unassigned", "MEDIUM", "deal"),
}

_TASK7_ERASURE_PROOF_FIELDS = frozenset(
    {
        "schema_version",
        "request_id",
        "attempt_id",
        "event_id",
        "event_type",
        "outcome",
        "durable",
        "replacement_event",
    }
)
_TASK7_ERASURE_EXECUTE_PATH = re.compile(
    r"/v1/erasure-requests/"
    r"(?P<request_id>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12})/execute"
)
_TASK7_ERASURE_TERMINAL_SAFE_FIELDS = frozenset(
    {
        "request_id",
        "attempt_id",
        "attempt_ordinal",
        "prior_status",
        "prior_attempt_event_id",
        "prior_attempt_event_hash",
        "outcome",
        "counts",
    }
)
_GENERIC_ERASURE_REQUEST_FIELDS = frozenset({"request_id", "method", "path", "status_code"})
_GENERIC_ERASURE_PAYLOAD_FIELDS = frozenset({"hashes", "refs"})


def _canonical_uuid_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError):
        return None
    return value if str(parsed) == value else None


def _should_suppress_generic_erasure_executed(
    request: Request,
    response: Response,
    generic_event: Mapping[str, Any],
) -> bool:
    """Return true only when Task 7 durable evidence replaces the generic event."""

    if (
        getattr(request.state, "openapi_operation_id", None) != "executeErasureRequest"
        or request.method != "POST"
        or response.status_code != 200
    ):
        return False

    path_match = _TASK7_ERASURE_EXECUTE_PATH.fullmatch(request.url.path)
    if path_match is None:
        return False
    path_request_id = path_match.group("request_id")
    if _canonical_uuid_text(path_request_id) is None:
        return False

    proof = getattr(request.state, "task7_erasure_terminal_lineage_proof", None)
    if not isinstance(proof, Mapping) or set(proof) != _TASK7_ERASURE_PROOF_FIELDS:
        return False
    replacement = proof.get("replacement_event")
    if not isinstance(replacement, Mapping):
        return False
    payload = replacement.get("payload")
    safe = payload.get("safe") if isinstance(payload, Mapping) else None
    return bool(
        isinstance(safe, Mapping)
        and proof.get("schema_version") == "task7-erasure-terminal-lineage-proof-v1"
        and proof.get("event_type") == "task7.erasure.succeeded"
        and proof.get("outcome") == "SUCCEEDED"
        and proof.get("durable") is True
        and _canonical_uuid_text(proof.get("request_id")) == path_request_id
        and _canonical_uuid_text(proof.get("attempt_id")) is not None
        and _canonical_uuid_text(proof.get("event_id")) is not None
        and replacement.get("event_id") == proof.get("event_id")
        and replacement.get("event_type") == proof.get("event_type")
        and safe.get("request_id") == proof.get("request_id")
        and safe.get("attempt_id") == proof.get("attempt_id")
        and safe.get("outcome") == proof.get("outcome")
        and _task7_erasure_replacement_covers_generic_event(
            generic_event,
            replacement,
        )
    )


def _semantic_actor(
    value: object,
) -> tuple[str, str, tuple[str, ...], str, str] | None:
    if not isinstance(value, Mapping):
        return None
    actor_type = value.get("actor_type")
    actor_id = value.get("actor_id")
    roles = value.get("roles")
    ip = value.get("ip")
    user_agent = value.get("user_agent")
    if (
        actor_type not in {"HUMAN", "SERVICE"}
        or not isinstance(actor_id, str)
        or not actor_id.strip()
        or actor_id != actor_id.strip()
        or not isinstance(roles, list)
        or not roles
        or not isinstance(ip, str)
        or not ip.strip()
        or ip != ip.strip()
        or not isinstance(user_agent, str)
        or not user_agent.strip()
        or user_agent != user_agent.strip()
        or any(
            not isinstance(role, str) or not role.strip() or role != role.strip() for role in roles
        )
    ):
        return None
    canonical_roles = tuple(sorted(set(roles)))
    if list(canonical_roles) != roles or "ADMIN" not in canonical_roles:
        return None
    return actor_type, actor_id, canonical_roles, ip, user_agent


def _aware_utc_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def _task7_erasure_replacement_covers_generic_event(
    generic_event: Mapping[str, Any],
    replacement_event: Mapping[str, Any],
) -> bool:
    """Prove field parity before a Task 7 terminal event replaces generic audit."""

    if (
        generic_event.get("event_type") != "erasure.executed"
        or replacement_event.get("event_type") != "task7.erasure.succeeded"
        or generic_event.get("severity") != "CRITICAL"
        or replacement_event.get("severity") != "CRITICAL"
    ):
        return False
    if generic_event.get("tenant_id") != replacement_event.get("tenant_id"):
        return False
    generic_actor = _semantic_actor(generic_event.get("actor"))
    replacement_actor = _semantic_actor(replacement_event.get("actor"))
    if generic_actor is None or generic_actor != replacement_actor:
        return False

    generic_request = generic_event.get("request")
    replacement_request = replacement_event.get("request")
    if not isinstance(generic_request, Mapping) or not isinstance(
        replacement_request,
        Mapping,
    ):
        return False
    if (
        set(generic_request) != _GENERIC_ERASURE_REQUEST_FIELDS
        or set(replacement_request) != _GENERIC_ERASURE_REQUEST_FIELDS
        or not isinstance(generic_request.get("request_id"), str)
        or not generic_request["request_id"].strip()
    ):
        # Optional generic evidence (for example the hashed idempotency key)
        # cannot be discarded by a Task-7 event whose frozen request shape
        # does not carry it.  Retain the generic event in that case.
        return False
    request_projection = ("method", "path", "status_code")
    if any(
        generic_request.get(field) != replacement_request.get(field) for field in request_projection
    ):
        return False
    if generic_request.get("status_code") != 200:
        return False

    generic_resource = generic_event.get("resource")
    replacement_resource = replacement_event.get("resource")
    if not isinstance(generic_resource, Mapping) or not isinstance(
        replacement_resource,
        Mapping,
    ):
        return False
    if any(
        generic_resource.get(field) != replacement_resource.get(field)
        for field in ("resource_type", "resource_id")
    ):
        return False
    deal_id = _canonical_uuid_text(replacement_resource.get("deal_id"))
    if deal_id is None:
        return False
    generic_deal_id = generic_resource.get("deal_id")
    if generic_deal_id is not None and generic_deal_id != deal_id:
        return False

    generic_time = _aware_utc_timestamp(generic_event.get("occurred_at"))
    replacement_time = _aware_utc_timestamp(replacement_event.get("occurred_at"))
    if generic_time is None or replacement_time is None or replacement_time > generic_time:
        return False

    generic_payload = generic_event.get("payload")
    replacement_payload = replacement_event.get("payload")
    if not isinstance(generic_payload, Mapping) or not isinstance(replacement_payload, Mapping):
        return False
    replacement_safe = replacement_payload.get("safe")
    if (
        set(generic_payload) != _GENERIC_ERASURE_PAYLOAD_FIELDS
        or generic_payload.get("hashes") != []
        or generic_payload.get("refs") != []
    ):
        # A body digest, reference, or future generic payload field is distinct
        # audit evidence.  Suppression is legal only for the exact empty
        # payload emitted by this no-body route.
        return False
    if (
        not isinstance(replacement_safe, Mapping)
        or set(replacement_safe) != _TASK7_ERASURE_TERMINAL_SAFE_FIELDS
        or replacement_safe.get("outcome") != "SUCCEEDED"
    ):
        return False

    request_id = replacement_safe.get("request_id")
    if (
        _canonical_uuid_text(request_id) is None
        or request_id != replacement_resource.get("resource_id")
        or _canonical_uuid_text(replacement_safe.get("attempt_id")) is None
        or _canonical_uuid_text(replacement_event.get("event_id")) is None
    ):
        return False
    attempt_ordinal = replacement_safe.get("attempt_ordinal")
    if (
        not isinstance(attempt_ordinal, int)
        or isinstance(attempt_ordinal, bool)
        or attempt_ordinal < 1
    ):
        return False

    prior_event_id = replacement_safe.get("prior_attempt_event_id")
    prior_event_hash = replacement_safe.get("prior_attempt_event_hash")
    if (prior_event_id is None) != (prior_event_hash is None):
        return False
    if prior_event_id is not None and (
        _canonical_uuid_text(prior_event_id) is None
        or not isinstance(prior_event_hash, str)
        or len(prior_event_hash) != 64
        or any(character not in "0123456789abcdef" for character in prior_event_hash)
    ):
        return False

    counts = replacement_safe.get("counts")
    return (
        bool(counts)
        and isinstance(counts, Mapping)
        and all(
            isinstance(key, str)
            and key
            and isinstance(value, int)
            and not isinstance(value, bool)
            and value >= 0
            for key, value in counts.items()
        )
    )


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
        actor_id = tenant_ctx.actor_id
        actor_type = tenant_ctx.actor_type
        roles = sorted(tenant_ctx.roles)

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
        # Product mutations that persist an audit-event reference allocate the
        # identifier before writing and bind this same id into the durable audit
        # row.  Ordinary routes retain middleware-generated identities.
        "event_id": str(getattr(request.state, "audit_event_id", uuid.uuid4())),
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
        event["request"]["idempotency_key_sha256"] = sha256(
            idempotency_key.encode("utf-8")
        ).hexdigest()

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

        # Pure replay was already audited on the first request.  The narrowly
        # lineage-proven createDeal compatibility path may, however, insert one
        # missing creator assignment in this transaction; that real mutation
        # must be audited before DBTransactionMiddleware commits it.
        if response.headers.get("X-IDIS-Idempotency-Replay") == "true" and not getattr(
            request.state,
            "audit_idempotency_replay_repair_occurred",
            False,
        ):
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

        # For ordinary 4xx client error responses, no mutation occurred (request was
        # rejected due to validation, not found, etc.). Some routes persist lifecycle
        # evidence before returning a client-visible conflict; those must opt in after
        # the side effect succeeds.
        if 400 <= response.status_code < 500 and not getattr(
            request.state,
            "audit_mutation_occurred_on_error",
            False,
        ):
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

        # Construct the generic event exactly once.  Route-scoped replacement
        # is allowed only when the persisted Task-7 terminal event covers this
        # same would-be emission; otherwise this exact object is validated and
        # emitted below.
        if _should_suppress_generic_erasure_executed(
            request,
            response,
            audit_event,
        ):
            return response

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
        durable_available = db_conn is not None and PostgresAuditSink is not None

        # Strict mode requires a durable (Postgres) audit sink. Fail closed before
        # emitting to a non-durable JSONL file sink. Test/in-memory sinks are not
        # the production JSONL file sink and are unaffected.
        if (
            not durable_available
            and isinstance(self._sink, JsonlFileAuditSink)
            and is_strict_full_live_required(
                dotenv_path=os.environ.get(IDIS_STRICT_DOTENV_PATH_ENV)
            )
        ):
            logger.error(
                "Strict mode requires a durable audit sink; refusing JSONL-only emission",
                extra={"request_id": request_id},
            )
            return _build_error_response(
                "AUDIT_EMIT_FAILED",
                "Strict mode requires a durable audit sink",
                request_id,
            )

        try:
            if durable_available:
                # durable_available implies db_conn is not None (see above); narrow for mypy.
                assert db_conn is not None
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
