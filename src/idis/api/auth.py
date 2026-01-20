"""IDIS API authentication and tenant context extraction.

Implements dual auth paths per v6.3 Security Threat Model:
- JWT bearer tokens for user sessions (SSO via OIDC)
- API keys for service-to-service calls (X-IDIS-API-Key header)

Fails closed on missing or invalid credentials. Unknown roles are rejected.
Tenant isolation enforced: errors do not leak tenant existence (ADR-011).
"""

import hmac
import json
import logging
import os
from typing import Annotated

from fastapi import Depends, Request
from pydantic import BaseModel, ValidationError

from idis.api.errors import IdisHttpError
from idis.api.policy import ALL_ROLES

logger = logging.getLogger(__name__)

API_KEY_HEADER = "X-IDIS-API-Key"
BEARER_PREFIX = "Bearer "
IDIS_API_KEYS_ENV = "IDIS_API_KEYS_JSON"


class TenantContext(BaseModel):
    """Tenant context per OpenAPI TenantContext schema.

    Includes actor roles for RBAC enforcement per v6.3 security model.
    actor_type distinguishes HUMAN (JWT) from SERVICE (API key) actors.
    """

    tenant_id: str
    actor_id: str
    name: str
    timezone: str
    data_region: str
    roles: frozenset[str] = frozenset()
    actor_type: str = "SERVICE"


class ApiKeyRecord(BaseModel):
    """API key registry entry containing tenant context fields.

    The actor_id is a stable, non-secret identifier for the API key holder.
    It should be unique per API key and used for idempotency scoping.
    Roles define RBAC permissions per v6.3 security model.
    """

    tenant_id: str
    actor_id: str
    name: str
    timezone: str
    data_region: str
    roles: list[str] = []


def _load_api_key_registry() -> dict[str, ApiKeyRecord]:
    """Load API key registry from environment variable.

    Returns:
        Dict mapping API key strings to ApiKeyRecord objects.
        Returns empty dict if env var missing or invalid JSON.
    """
    raw = os.environ.get(IDIS_API_KEYS_ENV)
    if not raw:
        return {}

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Failed to parse %s; treating as empty registry", IDIS_API_KEYS_ENV)
        return {}

    if not isinstance(parsed, dict):
        logger.warning("%s is not a dict; treating as empty registry", IDIS_API_KEYS_ENV)
        return {}

    registry: dict[str, ApiKeyRecord] = {}
    for key, value in parsed.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            continue
        try:
            registry[key] = ApiKeyRecord.model_validate(value)
        except ValidationError:
            continue

    return registry


def _constant_time_lookup(
    provided_key: str, registry: dict[str, ApiKeyRecord]
) -> ApiKeyRecord | None:
    """Look up API key using constant-time comparison to prevent timing attacks.

    Iterates all keys and uses hmac.compare_digest for each comparison.
    Returns the matching record if found, else None.
    """
    matched_record: ApiKeyRecord | None = None
    provided_bytes = provided_key.encode("utf-8")

    for registered_key, record in registry.items():
        registered_bytes = registered_key.encode("utf-8")
        if hmac.compare_digest(provided_bytes, registered_bytes):
            matched_record = record

    return matched_record


def _extract_tenant_from_api_key(request: Request) -> TenantContext:
    """Extract tenant context from X-IDIS-API-Key header.

    Raises:
        IdisHttpError: 401 if key missing, invalid, or registry empty.
    """
    api_key = request.headers.get(API_KEY_HEADER)
    if not api_key:
        raise IdisHttpError(
            status_code=401,
            code="unauthorized",
            message="Missing API key",
        )

    registry = _load_api_key_registry()
    if not registry:
        raise IdisHttpError(
            status_code=401,
            code="unauthorized",
            message="Invalid API key",
        )

    record = _constant_time_lookup(api_key, registry)
    if record is None:
        raise IdisHttpError(
            status_code=401,
            code="unauthorized",
            message="Invalid API key",
        )

    validated_roles = _normalize_roles(record.roles)

    return TenantContext(
        tenant_id=record.tenant_id,
        actor_id=record.actor_id,
        name=record.name,
        timezone=record.timezone,
        data_region=record.data_region,
        roles=validated_roles,
        actor_type="SERVICE",
    )


def _extract_bearer_token(request: Request) -> str | None:
    """Extract Bearer token from Authorization header if present.

    Returns:
        The raw JWT string (without prefix), or None if not present.
    """
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith(BEARER_PREFIX):
        return auth_header[len(BEARER_PREFIX) :].strip()
    return None


def _extract_tenant_from_jwt(token: str) -> TenantContext:
    """Extract tenant context from a validated JWT.

    Args:
        token: The raw JWT string.

    Returns:
        TenantContext from validated JWT claims.

    Raises:
        IdisHttpError: 401 on any validation failure (fail-closed).
    """
    from idis.api.auth_sso import validate_jwt

    identity = validate_jwt(token)

    return TenantContext(
        tenant_id=identity.tenant_id,
        actor_id=identity.user_id,
        name=identity.name or identity.email or identity.user_id,
        timezone="UTC",
        data_region=identity.data_region or "default",
        roles=identity.roles,
        actor_type="HUMAN",
    )


def _normalize_roles(roles: list[str]) -> frozenset[str]:
    """Normalize and validate roles, rejecting unknown roles (fail-closed).

    Args:
        roles: List of role strings from API key or JWT.

    Returns:
        Frozenset of normalized, validated role strings.

    Raises:
        IdisHttpError: 401 if any role is unknown.
    """
    normalized: set[str] = set()
    for role in roles:
        upper_role = role.upper().strip()
        if upper_role not in ALL_ROLES:
            raise IdisHttpError(
                status_code=401,
                code="unauthorized",
                message="Invalid credentials",
            )
        normalized.add(upper_role)
    return frozenset(normalized)


async def require_tenant_context(request: Request) -> TenantContext:
    """FastAPI dependency that enforces tenant authentication.

    Auth flow (fail closed, dual path):
    1. If Authorization: Bearer is present => validate JWT via OIDC.
    2. Else extract tenant from X-IDIS-API-Key => 401 on missing/invalid.
    3. Store tenant context in request.state for downstream use.

    JWT path is preferred for user sessions; API key for service-to-service.
    Errors do not leak tenant existence (ADR-011).

    Returns:
        TenantContext extracted from valid credentials.

    Raises:
        IdisHttpError: 401 on any auth failure.
    """
    bearer_token = _extract_bearer_token(request)

    if bearer_token:
        tenant_ctx = _extract_tenant_from_jwt(bearer_token)
    else:
        tenant_ctx = _extract_tenant_from_api_key(request)

    request.state.tenant_context = tenant_ctx

    return tenant_ctx


RequireTenantContext = Annotated[TenantContext, Depends(require_tenant_context)]


def authenticate_request(request: Request) -> TenantContext:
    """Synchronous auth check for middleware use.

    This is a non-async version of require_tenant_context for use in middleware
    where we need to check auth before proceeding with request validation.

    Auth flow (fail closed, dual path):
    1. If Authorization: Bearer is present => validate JWT via OIDC.
    2. Else extract tenant from X-IDIS-API-Key => raises IdisHttpError 401 on missing/invalid.

    Errors do not leak tenant existence (ADR-011).

    Returns:
        TenantContext extracted from valid credentials.

    Raises:
        IdisHttpError: 401 on any auth failure.
    """
    bearer_token = _extract_bearer_token(request)

    if bearer_token:
        return _extract_tenant_from_jwt(bearer_token)
    return _extract_tenant_from_api_key(request)
