"""IDIS Break-Glass Access Control and Audit.

Implements break-glass admin override per v6.3 Security Threat Model:
- Time-bound token validation
- Required justification
- Mandatory audit event emission (break_glass.used, severity CRITICAL)
- Fail-closed: if audit emission fails, override is denied

ADR-007: Break-glass requires justification and audit event
ADR-012: Overrides always explicit and audited
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from idis.api.errors import IdisHttpError
from idis.api.policy import Role

if TYPE_CHECKING:
    from fastapi import Request

logger = logging.getLogger(__name__)

BREAK_GLASS_SECRET_ENV = "IDIS_BREAK_GLASS_SECRET"
BREAK_GLASS_MAX_DURATION_SECONDS = 3600
BREAK_GLASS_DEFAULT_DURATION_SECONDS = 900
MIN_JUSTIFICATION_LENGTH = 20


@dataclass(frozen=True, slots=True)
class BreakGlassToken:
    """Validated break-glass token data.

    Attributes:
        token_id: Unique identifier for this break-glass session.
        actor_id: Actor who requested break-glass.
        tenant_id: Tenant scope for the break-glass.
        deal_id: Optional deal ID if scoped to specific deal.
        justification: Required justification text.
        issued_at: Unix timestamp when token was issued.
        expires_at: Unix timestamp when token expires.
        token_hash: SHA256 hash of the raw token for audit logging.
    """

    token_id: str
    actor_id: str
    tenant_id: str
    deal_id: str | None
    justification: str
    issued_at: float
    expires_at: float
    token_hash: str


@dataclass(frozen=True, slots=True)
class BreakGlassValidation:
    """Result of break-glass token validation.

    Attributes:
        valid: True if token is valid and not expired.
        token: The validated token data if valid.
        error_code: Error code if invalid.
        error_message: Error message if invalid.
    """

    valid: bool
    token: BreakGlassToken | None = None
    error_code: str | None = None
    error_message: str | None = None


def _get_secret() -> bytes:
    """Get the break-glass signing secret.

    Raises:
        IdisHttpError: If secret not configured (fail-closed).
    """
    secret = os.environ.get(BREAK_GLASS_SECRET_ENV)
    if not secret:
        raise IdisHttpError(
            status_code=500,
            code="break_glass_not_configured",
            message="Break-glass not configured",
        )
    return secret.encode("utf-8")


def _sign_token(payload: dict[str, Any], secret: bytes) -> str:
    """Create HMAC signature for token payload."""
    payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    signature = hmac.new(secret, payload_json.encode("utf-8"), hashlib.sha256).hexdigest()
    return signature


def create_break_glass_token(
    *,
    actor_id: str,
    tenant_id: str,
    justification: str,
    deal_id: str | None = None,
    duration_seconds: int = BREAK_GLASS_DEFAULT_DURATION_SECONDS,
) -> str:
    """Create a time-bound break-glass token.

    Args:
        actor_id: Actor requesting break-glass access.
        tenant_id: Tenant scope for the break-glass.
        justification: Required justification (min 10 chars).
        deal_id: Optional deal ID to scope the break-glass.
        duration_seconds: Token validity duration (max 1 hour).

    Returns:
        Encoded break-glass token string.

    Raises:
        IdisHttpError: On validation failure or missing config.
    """
    if not justification or len(justification.strip()) < MIN_JUSTIFICATION_LENGTH:
        raise IdisHttpError(
            status_code=400,
            code="invalid_justification",
            message=f"Justification must be at least {MIN_JUSTIFICATION_LENGTH} characters",
        )

    if duration_seconds > BREAK_GLASS_MAX_DURATION_SECONDS:
        duration_seconds = BREAK_GLASS_MAX_DURATION_SECONDS

    if duration_seconds < 60:
        duration_seconds = 60

    secret = _get_secret()
    now = time.time()

    token_id = str(uuid.uuid4())
    payload = {
        "token_id": token_id,
        "actor_id": actor_id,
        "tenant_id": tenant_id,
        "deal_id": deal_id,
        "justification": justification.strip(),
        "iat": int(now),
        "exp": int(now + duration_seconds),
    }

    signature = _sign_token(payload, secret)
    payload["sig"] = signature

    import base64

    token_json = json.dumps(payload, separators=(",", ":"))
    token = base64.urlsafe_b64encode(token_json.encode("utf-8")).decode("utf-8")

    return token


def validate_break_glass_token(
    token: str,
    *,
    expected_tenant_id: str,
    expected_deal_id: str | None = None,
) -> BreakGlassValidation:
    """Validate a break-glass token.

    Validation checks (fail-closed):
    1. Token is well-formed and decodable
    2. Signature is valid
    3. Token is not expired
    4. Tenant matches expected tenant
    5. Deal matches expected deal (if specified)

    Args:
        token: The raw break-glass token string.
        expected_tenant_id: Tenant ID to validate against.
        expected_deal_id: Optional deal ID to validate against.

    Returns:
        BreakGlassValidation with valid status and token data or error.
    """
    try:
        secret = _get_secret()
    except IdisHttpError:
        return BreakGlassValidation(
            valid=False,
            error_code="break_glass_not_configured",
            error_message="Break-glass not configured",
        )

    try:
        import base64

        padding = 4 - len(token) % 4
        if padding != 4:
            token += "=" * padding
        token_json = base64.urlsafe_b64decode(token.encode("utf-8")).decode("utf-8")
        payload = json.loads(token_json)
    except Exception:
        return BreakGlassValidation(
            valid=False,
            error_code="invalid_token",
            error_message="Malformed break-glass token",
        )

    required_fields = ["token_id", "actor_id", "tenant_id", "justification", "iat", "exp", "sig"]
    for field in required_fields:
        if field not in payload:
            return BreakGlassValidation(
                valid=False,
                error_code="invalid_token",
                error_message="Invalid break-glass token",
            )

    provided_sig = payload.pop("sig")
    expected_sig = _sign_token(payload, secret)

    if not hmac.compare_digest(provided_sig, expected_sig):
        return BreakGlassValidation(
            valid=False,
            error_code="invalid_signature",
            error_message="Invalid break-glass token signature",
        )

    now = time.time()
    exp = payload["exp"]
    if now > exp:
        return BreakGlassValidation(
            valid=False,
            error_code="token_expired",
            error_message="Break-glass token has expired",
        )

    if payload["tenant_id"] != expected_tenant_id:
        return BreakGlassValidation(
            valid=False,
            error_code="tenant_mismatch",
            error_message="Break-glass token tenant mismatch",
        )

    token_deal_id = payload.get("deal_id")
    if (
        expected_deal_id is not None
        and token_deal_id is not None
        and token_deal_id != expected_deal_id
    ):
        return BreakGlassValidation(
            valid=False,
            error_code="deal_mismatch",
            error_message="Break-glass token deal mismatch",
        )

    # Validate justification quality - must be at least MIN_JUSTIFICATION_LENGTH after strip
    justification = payload.get("justification", "")
    if not isinstance(justification, str):
        return BreakGlassValidation(
            valid=False,
            error_code="invalid_justification",
            error_message="Break-glass token has invalid justification",
        )

    justification_stripped = justification.strip()
    if len(justification_stripped) < MIN_JUSTIFICATION_LENGTH:
        logger.warning(
            "Break-glass token rejected: justification too short (%d < %d)",
            len(justification_stripped),
            MIN_JUSTIFICATION_LENGTH,
        )
        return BreakGlassValidation(
            valid=False,
            error_code="invalid_justification",
            error_message=(
                f"Break-glass justification must be at least {MIN_JUSTIFICATION_LENGTH} characters"
            ),
        )

    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]

    return BreakGlassValidation(
        valid=True,
        token=BreakGlassToken(
            token_id=payload["token_id"],
            actor_id=payload["actor_id"],
            tenant_id=payload["tenant_id"],
            deal_id=token_deal_id,
            justification=payload["justification"],
            issued_at=payload["iat"],
            expires_at=payload["exp"],
            token_hash=token_hash,
        ),
    )


def emit_break_glass_audit_event(
    *,
    request: Request,
    token: BreakGlassToken,
    resource_type: str,
    resource_id: str,
    operation_id: str,
) -> None:
    """Emit break_glass.used audit event.

    CRITICAL: This function must succeed for break-glass to be allowed.
    If audit emission fails, the caller MUST deny the request (fail-closed).

    Args:
        request: The FastAPI request object.
        token: The validated break-glass token.
        resource_type: Type of resource being accessed.
        resource_id: ID of resource being accessed.
        operation_id: OpenAPI operationId of the operation.

    Raises:
        IdisHttpError: On audit emission failure (fail-closed).
    """
    from idis.audit.sink import AuditSinkError, JsonlFileAuditSink
    from idis.validators.audit_event_validator import validate_audit_event

    postgres_sink_class: type | None = None
    try:
        from idis.audit.postgres_sink import PostgresAuditSink as PgSink

        postgres_sink_class = PgSink
    except ImportError:
        pass

    request_id: str = getattr(request.state, "request_id", str(uuid.uuid4()))

    actor_id = token.actor_id
    tenant_id = token.tenant_id
    ip_address = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("User-Agent", "unknown")

    justification_hash = hashlib.sha256(token.justification.encode("utf-8")).hexdigest()

    refs: list[str] = [f"operation:{operation_id}"]
    if token.deal_id:
        refs.append(f"deal_id:{token.deal_id}")

    event: dict[str, Any] = {
        "event_id": str(uuid.uuid4()),
        "occurred_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "tenant_id": tenant_id,
        "actor": {
            "actor_type": "HUMAN",
            "actor_id": actor_id,
            "roles": [Role.ADMIN.value],
            "ip": ip_address,
            "user_agent": user_agent,
        },
        "request": {
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status_code": 200,
        },
        "resource": {
            "resource_type": resource_type,
            "resource_id": resource_id,
            "deal_id": token.deal_id,
        },
        "event_type": "break_glass.used",
        "severity": "CRITICAL",
        "summary": f"Break-glass access to {resource_type}/{resource_id} via {operation_id}",
        "payload": {
            "safe": {
                "scope": token.deal_id or "tenant-wide",
                "expires_at": datetime.fromtimestamp(token.expires_at, tz=UTC)
                .isoformat()
                .replace("+00:00", "Z"),
                "justification_len": len(token.justification),
            },
            "hashes": [
                f"token_sha256:{token.token_hash}",
                f"justification_sha256:{justification_hash}",
            ],
            "refs": refs,
        },
    }

    validation_result = validate_audit_event(event)
    if not validation_result.passed:
        error_codes = [e.code for e in validation_result.errors]
        logger.error("Break-glass audit event validation failed: %s", error_codes)
        raise IdisHttpError(
            status_code=500,
            code="audit_emit_failed",
            message="Break-glass denied: audit validation failed",
        )

    db_conn = getattr(request.state, "db_conn", None)

    try:
        if db_conn is not None and postgres_sink_class is not None:
            pg_sink = postgres_sink_class()
            pg_sink.emit_in_tx(db_conn, event)
        else:
            jsonl_sink = JsonlFileAuditSink()
            jsonl_sink.emit(event)
    except AuditSinkError as e:
        logger.error("Break-glass audit emission failed: %s", str(e))
        raise IdisHttpError(
            status_code=500,
            code="audit_emit_failed",
            message="Break-glass denied: audit emission failed",
        ) from e

    request.state.break_glass_audit_emitted = True

    logger.info(
        "Break-glass access granted: actor=%s, resource=%s/%s, token_id=%s",
        actor_id,
        resource_type,
        resource_id,
        token.token_id,
        extra={"request_id": request_id},
    )


def extract_break_glass_token(request: Request) -> str | None:
    """Extract break-glass token from request header.

    Args:
        request: The FastAPI request object.

    Returns:
        The raw token string, or None if not present.
    """
    return request.headers.get("X-IDIS-Break-Glass")


def validate_actor_binding(
    token: BreakGlassToken,
    expected_actor_id: str,
) -> bool:
    """Validate that break-glass token is bound to expected actor.

    Per security threat model, break-glass tokens are actor-bound.
    A token issued to actor A cannot be used by actor B.

    Args:
        token: The validated break-glass token.
        expected_actor_id: Actor ID from current request context.

    Returns:
        True if actor matches, False otherwise.
    """
    return token.actor_id == expected_actor_id


def validate_deal_binding(
    token: BreakGlassToken,
    expected_deal_id: str | None,
) -> bool:
    """Validate that break-glass token deal scope matches.

    If token has a deal_id, it must match the expected deal_id.
    If token has no deal_id (tenant-wide), any deal is allowed.

    Args:
        token: The validated break-glass token.
        expected_deal_id: Deal ID from current request context.

    Returns:
        True if deal matches or token is tenant-wide, False otherwise.
    """
    token_is_tenant_wide = token.deal_id is None
    request_has_no_deal = expected_deal_id is None
    deals_match = token.deal_id == expected_deal_id

    return token_is_tenant_wide or request_has_no_deal or deals_match
