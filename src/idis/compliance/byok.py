"""BYOK (Bring Your Own Key) policy enforcement for IDIS (v6.3 Task 7.5).

Implements customer-managed key policies per Data Residency Model v6.3 section 5.3:
- Tenant may supply KMS key alias for encryption
- Key rotation supported
- Key revocation locks tenant content access until re-keyed

Design principles:
- Fail closed: missing key state, invalid alias, or revoked key denies access
- Audit emission is fatal: mutations fail if audit write fails
- No Class2/3 data in logs (hashes/lengths only)
- Tenant isolation: key configs are tenant-scoped
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from idis.api.errors import IdisHttpError
from idis.validators.audit_event_validator import validate_audit_event

if TYPE_CHECKING:
    from idis.api.auth import TenantContext
    from idis.audit.sink import AuditSink

logger = logging.getLogger(__name__)

KEY_ALIAS_PATTERN = re.compile(r"^[a-zA-Z0-9_\-]{1,256}$")
KEY_ALIAS_MIN_LENGTH = 1
KEY_ALIAS_MAX_LENGTH = 256


class BYOKKeyState(StrEnum):
    """Key states for BYOK customer-managed keys."""

    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"


class DataClass(StrEnum):
    """Data classification per v6.3 Data Residency Model section 2."""

    CLASS_0 = "CLASS_0"
    CLASS_1 = "CLASS_1"
    CLASS_2 = "CLASS_2"
    CLASS_3 = "CLASS_3"


@dataclass
class BYOKPolicy:
    """BYOK policy configuration for a tenant.

    Attributes:
        tenant_id: The tenant this policy applies to.
        key_alias: The KMS key alias (safe identifier, not the actual key). Raw aliases live
            only in process memory: the durable store persists hash+length, so a policy loaded
            from Postgres carries key_alias="" and the fields below instead.
        key_state: Current state of the key (ACTIVE or REVOKED).
        created_at: When the key was configured.
        rotated_at: When the key was last rotated (None if never).
        revoked_at: When the key was revoked (None if active).
        key_alias_sha256: Full SHA-256 of the alias (set when loaded from the durable store).
        key_alias_length: Length of the raw alias (set when loaded from the durable store).
    """

    tenant_id: str
    key_alias: str
    key_state: BYOKKeyState = BYOKKeyState.ACTIVE
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    rotated_at: datetime | None = None
    revoked_at: datetime | None = None
    key_alias_sha256: str | None = None
    key_alias_length: int | None = None


def policy_alias_sha256(policy: BYOKPolicy) -> str:
    """Full SHA-256 hex of the policy's alias, from raw alias or the stored hash."""
    if policy.key_alias_sha256:
        return policy.key_alias_sha256
    return hashlib.sha256(policy.key_alias.encode()).hexdigest()


def policy_alias_length(policy: BYOKPolicy) -> int:
    """Length of the policy's raw alias, from the raw value or the stored length."""
    if policy.key_alias_length is not None:
        return policy.key_alias_length
    return len(policy.key_alias)


@runtime_checkable
class BYOKPolicyStore(Protocol):
    """Seam for tenant BYOK policy state (Slice98 Task 6).

    Implementations MUST raise on backend failure - a resolution error can never surface as
    "no policy" (which ``require_key_active`` treats as BYOK-not-configured = allow).
    """

    def get(self, tenant_id: str) -> BYOKPolicy | None:
        """Return the tenant's policy, or None if BYOK is not configured."""
        ...

    def set(self, policy: BYOKPolicy) -> None:
        """Persist (create or overwrite) the tenant's policy."""
        ...


class BYOKPolicyRegistry:
    """In-memory twin of the BYOK policy store (tests and non-Postgres deployments)."""

    def __init__(self) -> None:
        self._policies: dict[str, BYOKPolicy] = {}

    def get(self, tenant_id: str) -> BYOKPolicy | None:
        """Get BYOK policy for a tenant."""
        return self._policies.get(tenant_id)

    def set(self, policy: BYOKPolicy) -> None:
        """Set BYOK policy for a tenant."""
        self._policies[policy.tenant_id] = policy

    def clear(self) -> None:
        """Clear all policies (for testing)."""
        self._policies.clear()


class PostgresBYOKPolicyRegistry:
    """Durable twin over ``byok_policies`` (migration 0029, guarded RLS).

    Stores POLICY METADATA only - the customer's key material lives solely in their KMS (see
    docs/architecture/slice98_byok_kms_decision.md for the recorded KMS-boundary seam). Reads
    fail CLOSED (403 BYOK_RESOLUTION_FAILED) on backend errors so a DB outage can never read as
    "no policy = allow"; writes fail loudly (500) and, being a single-statement transaction,
    leave no durable state behind on failure.
    """

    def get(self, tenant_id: str) -> BYOKPolicy | None:
        from sqlalchemy import text

        from idis.persistence.db import begin_app_conn, set_tenant_local

        try:
            with begin_app_conn() as conn:
                set_tenant_local(conn, tenant_id)
                row = conn.execute(
                    text(
                        "SELECT tenant_id, key_alias_sha256, key_alias_length, key_state, "
                        "created_at, rotated_at, revoked_at FROM byok_policies "
                        "WHERE tenant_id = CAST(:tenant_id AS uuid)"
                    ),
                    {"tenant_id": tenant_id},
                ).fetchone()
        except Exception as e:
            logger.error("PostgresBYOKPolicyRegistry get failed: %s", type(e).__name__)
            raise IdisHttpError(
                status_code=403,
                code="BYOK_RESOLUTION_FAILED",
                message="Access denied.",
            ) from e
        if row is None:
            return None
        return BYOKPolicy(
            tenant_id=str(row.tenant_id),
            key_alias="",  # raw aliases are never persisted; hash+length carry identity
            key_state=BYOKKeyState(row.key_state),
            created_at=row.created_at,
            rotated_at=row.rotated_at,
            revoked_at=row.revoked_at,
            key_alias_sha256=row.key_alias_sha256,
            key_alias_length=row.key_alias_length,
        )

    def set(self, policy: BYOKPolicy) -> None:
        from sqlalchemy import text

        from idis.persistence.db import begin_app_conn, set_tenant_local

        try:
            with begin_app_conn() as conn:
                set_tenant_local(conn, policy.tenant_id)
                conn.execute(
                    text(
                        """
                        INSERT INTO byok_policies (
                            tenant_id, key_alias_sha256, key_alias_length, key_state,
                            created_at, rotated_at, revoked_at
                        ) VALUES (
                            CAST(:tenant_id AS uuid), :key_alias_sha256, :key_alias_length,
                            :key_state, :created_at, :rotated_at, :revoked_at
                        )
                        ON CONFLICT (tenant_id) DO UPDATE SET
                            key_alias_sha256 = EXCLUDED.key_alias_sha256,
                            key_alias_length = EXCLUDED.key_alias_length,
                            key_state = EXCLUDED.key_state,
                            created_at = EXCLUDED.created_at,
                            rotated_at = EXCLUDED.rotated_at,
                            revoked_at = EXCLUDED.revoked_at
                        """
                    ),
                    {
                        "tenant_id": policy.tenant_id,
                        "key_alias_sha256": policy_alias_sha256(policy),
                        "key_alias_length": policy_alias_length(policy),
                        "key_state": policy.key_state.value,
                        "created_at": policy.created_at,
                        "rotated_at": policy.rotated_at,
                        "revoked_at": policy.revoked_at,
                    },
                )
        except Exception as e:
            logger.error("PostgresBYOKPolicyRegistry set failed: %s", type(e).__name__)
            raise IdisHttpError(
                status_code=500,
                code="BYOK_POLICY_WRITE_FAILED",
                message="BYOK policy could not be persisted",
            ) from e


_registry: BYOKPolicyStore | None = None


def build_default_byok_policy_registry() -> BYOKPolicyStore:
    """Select the durable Postgres store when configured, else the in-memory twin."""
    from idis.persistence.db import is_postgres_configured

    if is_postgres_configured():
        return PostgresBYOKPolicyRegistry()
    return BYOKPolicyRegistry()


def get_byok_policy_registry() -> BYOKPolicyStore:
    """Return the process-wide BYOK policy store, building the default on first use."""
    global _registry
    if _registry is None:
        _registry = build_default_byok_policy_registry()
    return _registry


def set_byok_policy_registry(store: BYOKPolicyStore) -> None:
    """Override the process-wide store (tests / explicit wiring)."""
    global _registry
    _registry = store


def reset_byok_policy_registry() -> None:
    """Clear the process-wide store so the next access rebuilds the default."""
    global _registry
    _registry = None


def _validate_key_alias(key_alias: str) -> None:
    """Validate key alias format.

    Key aliases must:
    - Be 1-256 characters
    - Contain only alphanumeric, underscore, hyphen

    Args:
        key_alias: The key alias to validate.

    Raises:
        IdisHttpError: 400 if key alias is invalid.
    """
    if not key_alias:
        raise IdisHttpError(
            status_code=400,
            code="BYOK_INVALID_KEY_ALIAS",
            message="Key alias cannot be empty",
        )

    if len(key_alias) < KEY_ALIAS_MIN_LENGTH or len(key_alias) > KEY_ALIAS_MAX_LENGTH:
        raise IdisHttpError(
            status_code=400,
            code="BYOK_INVALID_KEY_ALIAS",
            message=f"Key alias must be {KEY_ALIAS_MIN_LENGTH}-{KEY_ALIAS_MAX_LENGTH} characters",
        )

    if not KEY_ALIAS_PATTERN.match(key_alias):
        raise IdisHttpError(
            status_code=400,
            code="BYOK_INVALID_KEY_ALIAS",
            message="Key alias contains invalid characters",
        )


def _build_byok_audit_event(
    tenant_id: str,
    actor_id: str,
    event_type: str,
    key_alias_sha256: str,
    key_alias_length: int,
    key_state: BYOKKeyState,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a BYOK audit event.

    Note: only the alias hash/length are included - never the raw alias or key material.
    """
    key_alias_hash = key_alias_sha256[:16]

    event: dict[str, Any] = {
        "event_id": str(uuid.uuid4()),
        "occurred_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "tenant_id": tenant_id,
        "actor": {
            "actor_type": "SERVICE",
            "actor_id": actor_id,
            "roles": ["ADMIN"],
            "ip": "internal",
            "user_agent": "idis-compliance",
        },
        "request": {
            "request_id": str(uuid.uuid4()),
            "method": "POST",
            "path": "/internal/compliance/byok",
            "status_code": 200,
        },
        "resource": {
            "resource_type": "byok_key",
            "resource_id": f"{tenant_id}:{key_alias_hash}",
        },
        "event_type": event_type,
        "severity": "HIGH",
        "summary": f"{event_type} for tenant {tenant_id}",
        "payload": {
            "safe": {
                "key_alias_hash": key_alias_hash,
                "key_alias_length": key_alias_length,
                "key_state": key_state.value,
            },
            "hashes": [],
            "refs": [],
        },
    }

    if details:
        safe_details = {k: v for k, v in details.items() if k not in ("key", "secret")}
        event["payload"]["safe"].update(safe_details)

    return event


def _emit_audit_or_fail(
    audit_sink: AuditSink | None,
    event: dict[str, Any],
    operation: str,
) -> None:
    """Emit audit event or fail the operation.

    Audit emission is fatal per v6.3 requirements (FAIL-CLOSED).
    If audit_sink is None, the operation MUST fail.

    Args:
        audit_sink: The audit sink to emit to.
        event: The audit event to emit.
        operation: Description of the operation for error messages.

    Raises:
        IdisHttpError: 500 if audit sink missing or emission fails.
    """
    if audit_sink is None:
        logger.error(
            "BYOK audit sink not configured; %s BLOCKED (fail-closed)",
            operation,
        )
        raise IdisHttpError(
            status_code=500,
            code="BYOK_AUDIT_REQUIRED",
            message="Operation failed: audit requirement not met",
        )

    validation = validate_audit_event(event)
    if not validation.passed:
        logger.error(
            "BYOK audit event failed validation for %s (fail-closed): %s",
            operation,
            [error.code for error in validation.errors],
        )
        raise IdisHttpError(
            status_code=500,
            code="BYOK_AUDIT_FAILED",
            message="Operation failed: audit requirement not met",
        )

    try:
        audit_sink.emit(event)
    except Exception as e:
        logger.error(
            "BYOK audit emission failed for %s: %s",
            operation,
            type(e).__name__,
        )
        raise IdisHttpError(
            status_code=500,
            code="BYOK_AUDIT_FAILED",
            message="Operation failed: audit requirement not met",
        ) from None


def configure_key(
    tenant_ctx: TenantContext,
    key_alias: str,
    audit_sink: AuditSink | None = None,
    registry: BYOKPolicyStore | None = None,
) -> BYOKPolicy:
    """Configure a BYOK key for a tenant.

    Args:
        tenant_ctx: The tenant context.
        key_alias: The KMS key alias to configure.
        audit_sink: Audit sink for emission (fatal if fails).
        registry: Policy registry (uses default if None).

    Returns:
        The created BYOKPolicy.

    Raises:
        IdisHttpError: 400 if key alias invalid, 500 if audit fails.
    """
    _validate_key_alias(key_alias)

    reg = registry or get_byok_policy_registry()

    policy = BYOKPolicy(
        tenant_id=tenant_ctx.tenant_id,
        key_alias=key_alias,
        key_state=BYOKKeyState.ACTIVE,
        created_at=datetime.now(UTC),
    )

    event = _build_byok_audit_event(
        tenant_id=tenant_ctx.tenant_id,
        actor_id=tenant_ctx.actor_id,
        event_type="byok.key.configured",
        key_alias_sha256=policy_alias_sha256(policy),
        key_alias_length=policy_alias_length(policy),
        key_state=BYOKKeyState.ACTIVE,
    )

    _emit_audit_or_fail(audit_sink, event, "configure_key")

    reg.set(policy)

    logger.info(
        "BYOK key configured: tenant_id=%s, key_alias_length=%d",
        tenant_ctx.tenant_id,
        len(key_alias),
    )

    return policy


def rotate_key(
    tenant_ctx: TenantContext,
    new_key_alias: str,
    audit_sink: AuditSink | None = None,
    registry: BYOKPolicyStore | None = None,
) -> BYOKPolicy:
    """Rotate the BYOK key for a tenant.

    Args:
        tenant_ctx: The tenant context.
        new_key_alias: The new KMS key alias.
        audit_sink: Audit sink for emission (fatal if fails).
        registry: Policy registry (uses default if None).

    Returns:
        The updated BYOKPolicy.

    Raises:
        IdisHttpError: 400 if key alias invalid, 404 if no existing key,
                       500 if audit fails.
    """
    _validate_key_alias(new_key_alias)

    reg = registry or get_byok_policy_registry()
    existing = reg.get(tenant_ctx.tenant_id)

    if existing is None:
        raise IdisHttpError(
            status_code=404,
            code="BYOK_KEY_NOT_FOUND",
            message="No BYOK key configured for tenant",
        )

    policy = BYOKPolicy(
        tenant_id=tenant_ctx.tenant_id,
        key_alias=new_key_alias,
        key_state=BYOKKeyState.ACTIVE,
        created_at=existing.created_at,
        rotated_at=datetime.now(UTC),
    )

    event = _build_byok_audit_event(
        tenant_id=tenant_ctx.tenant_id,
        actor_id=tenant_ctx.actor_id,
        event_type="byok.key.rotated",
        key_alias_sha256=policy_alias_sha256(policy),
        key_alias_length=policy_alias_length(policy),
        key_state=BYOKKeyState.ACTIVE,
        details={"previous_key_alias_length": policy_alias_length(existing)},
    )

    _emit_audit_or_fail(audit_sink, event, "rotate_key")

    reg.set(policy)

    logger.info(
        "BYOK key rotated: tenant_id=%s, new_key_alias_length=%d",
        tenant_ctx.tenant_id,
        len(new_key_alias),
    )

    return policy


def revoke_key(
    tenant_ctx: TenantContext,
    audit_sink: AuditSink | None = None,
    registry: BYOKPolicyStore | None = None,
) -> BYOKPolicy:
    """Revoke the BYOK key for a tenant.

    After revocation, all Class2/3 access is denied until re-keyed.

    Args:
        tenant_ctx: The tenant context.
        audit_sink: Audit sink for emission (fatal if fails).
        registry: Policy registry (uses default if None).

    Returns:
        The updated BYOKPolicy with REVOKED state.

    Raises:
        IdisHttpError: 404 if no existing key, 500 if audit fails.
    """
    reg = registry or get_byok_policy_registry()
    existing = reg.get(tenant_ctx.tenant_id)

    if existing is None:
        raise IdisHttpError(
            status_code=404,
            code="BYOK_KEY_NOT_FOUND",
            message="No BYOK key configured for tenant",
        )

    policy = BYOKPolicy(
        tenant_id=tenant_ctx.tenant_id,
        key_alias=existing.key_alias,
        key_state=BYOKKeyState.REVOKED,
        created_at=existing.created_at,
        rotated_at=existing.rotated_at,
        revoked_at=datetime.now(UTC),
        key_alias_sha256=existing.key_alias_sha256,
        key_alias_length=existing.key_alias_length,
    )

    event = _build_byok_audit_event(
        tenant_id=tenant_ctx.tenant_id,
        actor_id=tenant_ctx.actor_id,
        event_type="byok.key.revoked",
        key_alias_sha256=policy_alias_sha256(existing),
        key_alias_length=policy_alias_length(existing),
        key_state=BYOKKeyState.REVOKED,
    )

    _emit_audit_or_fail(audit_sink, event, "revoke_key")

    reg.set(policy)

    logger.warning(
        "BYOK key revoked: tenant_id=%s - all Class2/3 access now denied",
        tenant_ctx.tenant_id,
    )

    return policy


def require_key_active(
    tenant_ctx: TenantContext,
    data_class: DataClass,
    registry: BYOKPolicyStore | None = None,
) -> None:
    """Require that BYOK key is active for Class2/3 data access.

    This should be called at storage boundaries before read/write of
    Class2/3 artifacts.

    Behavior:
    - Class0/Class1: No BYOK check required, returns immediately
    - Class2/Class3 with no BYOK policy: Access allowed (BYOK is optional)
    - Class2/Class3 with BYOK policy and ACTIVE key: Access allowed
    - Class2/Class3 with BYOK policy and REVOKED key: Access denied (403)

    Args:
        tenant_ctx: The tenant context.
        data_class: The data classification of the resource.
        registry: Policy registry (uses default if None).

    Raises:
        IdisHttpError: 403 if BYOK key is revoked for Class2/3 access.
    """
    if data_class in (DataClass.CLASS_0, DataClass.CLASS_1):
        return

    reg = registry or get_byok_policy_registry()
    policy = reg.get(tenant_ctx.tenant_id)

    if policy is None:
        return

    if policy.key_state == BYOKKeyState.REVOKED:
        logger.warning(
            "BYOK access denied: tenant_id=%s, data_class=%s, key_state=REVOKED",
            tenant_ctx.tenant_id,
            data_class.value,
        )
        raise IdisHttpError(
            status_code=403,
            code="BYOK_KEY_REVOKED",
            message="Access denied.",
        )


def get_key_metadata(
    tenant_ctx: TenantContext,
    registry: BYOKPolicyStore | None = None,
) -> dict[str, Any] | None:
    """Get BYOK key metadata for object storage headers.

    Returns safe metadata (no secrets) for attachment to stored objects.

    Args:
        tenant_ctx: The tenant context.
        registry: Policy registry (uses default if None).

    Returns:
        Dict with kms_key_alias (hashed) and key_state, or None if no BYOK.
    """
    reg = registry or get_byok_policy_registry()
    policy = reg.get(tenant_ctx.tenant_id)

    if policy is None:
        return None

    key_alias_hash = policy_alias_sha256(policy)[:16]

    return {
        "kms_key_alias_hash": key_alias_hash,
        "kms_key_state": policy.key_state.value,
    }
