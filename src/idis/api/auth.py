"""IDIS API authentication and tenant context extraction.

Implements API key auth via X-IDIS-API-Key header. Fails closed on missing
or invalid credentials. Bearer tokens are rejected if no verifier is configured.
"""

import hmac
import json
import logging
import os
from typing import Annotated

from fastapi import Depends, Request
from pydantic import BaseModel, ValidationError

from idis.api.errors import IdisHttpError

logger = logging.getLogger(__name__)

API_KEY_HEADER = "X-IDIS-API-Key"
BEARER_PREFIX = "Bearer "
IDIS_API_KEYS_ENV = "IDIS_API_KEYS_JSON"


class TenantContext(BaseModel):
    """Tenant context per OpenAPI TenantContext schema.

    Includes actor roles for RBAC enforcement per v6.3 security model.
    """

    tenant_id: str
    actor_id: str
    name: str
    timezone: str
    data_region: str
    roles: frozenset[str] = frozenset()


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

    return TenantContext(
        tenant_id=record.tenant_id,
        actor_id=record.actor_id,
        name=record.name,
        timezone=record.timezone,
        data_region=record.data_region,
        roles=frozenset(record.roles),
    )


def _check_bearer_token(request: Request) -> None:
    """Check if Authorization: Bearer is present; reject if so (fail closed).

    Bearer JWT verification is not yet implemented. If a Bearer token is
    provided without a configured verifier, we reject the request.

    Raises:
        IdisHttpError: 401 if Bearer token present but verification not configured.
    """
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith(BEARER_PREFIX):
        raise IdisHttpError(
            status_code=401,
            code="unauthorized",
            message="Bearer token verification not configured",
        )


async def require_tenant_context(request: Request) -> TenantContext:
    """FastAPI dependency that enforces tenant authentication.

    Auth flow (fail closed):
    1. If Authorization: Bearer is present but no verifier configured => 401.
    2. Extract tenant from X-IDIS-API-Key => 401 on missing/invalid.
    3. Store tenant context in request.state for downstream use.

    Returns:
        TenantContext extracted from valid credentials.

    Raises:
        IdisHttpError: 401 on any auth failure.
    """
    _check_bearer_token(request)

    tenant_ctx = _extract_tenant_from_api_key(request)
    request.state.tenant_context = tenant_ctx

    return tenant_ctx


RequireTenantContext = Annotated[TenantContext, Depends(require_tenant_context)]


def authenticate_request(request: Request) -> TenantContext:
    """Synchronous auth check for middleware use.

    This is a non-async version of require_tenant_context for use in middleware
    where we need to check auth before proceeding with request validation.

    Auth flow (fail closed):
    1. If Authorization: Bearer is present but no verifier configured => raises IdisHttpError 401.
    2. Extract tenant from X-IDIS-API-Key => raises IdisHttpError 401 on missing/invalid.

    Returns:
        TenantContext extracted from valid credentials.

    Raises:
        IdisHttpError: 401 on any auth failure.
    """
    _check_bearer_token(request)
    return _extract_tenant_from_api_key(request)
