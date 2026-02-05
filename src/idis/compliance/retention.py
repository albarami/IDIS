"""Retention and legal hold enforcement for IDIS (v6.3 Task 7.5).

Implements retention policy and legal hold per Data Residency Model v6.3 §6:
- Retention policy with deterministic evaluation
- Legal hold registry preventing deletion of held items
- All hold actions audited with CRITICAL severity
- Hold reason content never logged raw (hash/length only)

Design principles:
- Fail closed: missing hold registry denies deletion
- Audit emission is fatal for compliance mutations
- No Class2/3 leakage in logs
- Tenant isolation: holds are tenant-scoped
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Any

from idis.api.errors import IdisHttpError

if TYPE_CHECKING:
    from idis.api.auth import TenantContext
    from idis.audit.sink import AuditSink

logger = logging.getLogger(__name__)


class HoldTarget(str, Enum):
    """Types of resources that can be held."""

    DEAL = "DEAL"
    DOCUMENT = "DOCUMENT"
    ARTIFACT = "ARTIFACT"


class RetentionClass(str, Enum):
    """Retention period classes per v6.3 §6.1."""

    RAW_DOCUMENTS = "RAW_DOCUMENTS"
    DELIVERABLES = "DELIVERABLES"
    AUDIT_EVENTS = "AUDIT_EVENTS"


@dataclass(frozen=True)
class RetentionPolicy:
    """Retention policy configuration.

    Attributes:
        retention_class: The class of data this policy applies to.
        retention_days: Number of days to retain (0 = indefinite while active).
        hard_delete_allowed: Whether hard delete is allowed after retention.
        requires_admin_approval: Whether hard delete requires admin approval.
    """

    retention_class: RetentionClass
    retention_days: int
    hard_delete_allowed: bool = True
    requires_admin_approval: bool = True


DEFAULT_RETENTION_POLICIES: dict[RetentionClass, RetentionPolicy] = {
    RetentionClass.RAW_DOCUMENTS: RetentionPolicy(
        retention_class=RetentionClass.RAW_DOCUMENTS,
        retention_days=0,
        hard_delete_allowed=True,
        requires_admin_approval=True,
    ),
    RetentionClass.DELIVERABLES: RetentionPolicy(
        retention_class=RetentionClass.DELIVERABLES,
        retention_days=2555,
        hard_delete_allowed=True,
        requires_admin_approval=True,
    ),
    RetentionClass.AUDIT_EVENTS: RetentionPolicy(
        retention_class=RetentionClass.AUDIT_EVENTS,
        retention_days=2555,
        hard_delete_allowed=False,
        requires_admin_approval=True,
    ),
}


@dataclass
class LegalHold:
    """A legal hold preventing deletion of specific resources.

    Attributes:
        hold_id: Unique identifier for this hold.
        tenant_id: The tenant this hold applies to.
        target_type: Type of resource held (DEAL, DOCUMENT, ARTIFACT).
        target_id: ID of the held resource.
        reason_hash: SHA256 hash of the hold reason (never log raw reason).
        reason_length: Length of the hold reason (for audit without leakage).
        applied_at: When the hold was applied.
        applied_by: Actor who applied the hold.
        lifted_at: When the hold was lifted (None if still active).
        lifted_by: Actor who lifted the hold (None if still active).
    """

    hold_id: str
    tenant_id: str
    target_type: HoldTarget
    target_id: str
    reason_hash: str
    reason_length: int
    applied_at: datetime
    applied_by: str
    lifted_at: datetime | None = None
    lifted_by: str | None = None

    @property
    def is_active(self) -> bool:
        """Check if this hold is still active."""
        return self.lifted_at is None


class LegalHoldRegistry:
    """In-memory registry of legal holds for testing/dev.

    Production implementations should use a database-backed registry.
    Thread-safety note: This implementation is NOT thread-safe.
    """

    def __init__(self) -> None:
        self._holds: dict[str, LegalHold] = {}

    def get(self, hold_id: str) -> LegalHold | None:
        """Get a hold by ID."""
        return self._holds.get(hold_id)

    def list_active_for_target(
        self, tenant_id: str, target_type: HoldTarget, target_id: str
    ) -> list[LegalHold]:
        """List all active holds for a specific target."""
        return [
            h
            for h in self._holds.values()
            if h.tenant_id == tenant_id
            and h.target_type == target_type
            and h.target_id == target_id
            and h.is_active
        ]

    def has_active_hold(self, tenant_id: str, target_type: HoldTarget, target_id: str) -> bool:
        """Check if a target has any active holds."""
        return len(self.list_active_for_target(tenant_id, target_type, target_id)) > 0

    def add(self, hold: LegalHold) -> None:
        """Add a hold to the registry."""
        self._holds[hold.hold_id] = hold

    def update(self, hold: LegalHold) -> None:
        """Update a hold in the registry."""
        self._holds[hold.hold_id] = hold

    def clear(self) -> None:
        """Clear all holds (for testing)."""
        self._holds.clear()


_default_registry = LegalHoldRegistry()


def _hash_reason(reason: str) -> str:
    """Hash a hold reason for safe logging/audit.

    Never log or store raw hold reason content.
    """
    return hashlib.sha256(reason.encode("utf-8")).hexdigest()


def _build_hold_audit_event(
    tenant_id: str,
    actor_id: str,
    event_type: str,
    hold: LegalHold,
) -> dict[str, Any]:
    """Build a legal hold audit event with CRITICAL severity.

    Note: Hold reason content is NEVER included - only hash and length.
    """
    event: dict[str, Any] = {
        "event_id": str(uuid.uuid4()),
        "occurred_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "tenant_id": tenant_id,
        "actor": {
            "actor_type": "HUMAN",
            "actor_id": actor_id,
            "roles": ["ADMIN"],
            "ip": "internal",
            "user_agent": "idis-compliance",
        },
        "request": {
            "request_id": str(uuid.uuid4()),
            "method": "INTERNAL",
            "path": "/compliance/legal-hold",
            "status_code": 200,
        },
        "resource": {
            "resource_type": "legal_hold",
            "resource_id": hold.hold_id,
        },
        "event_type": event_type,
        "severity": "CRITICAL",
        "summary": f"{event_type}: {hold.target_type.value}/{hold.target_id}",
        "payload": {
            "hold_id": hold.hold_id,
            "target_type": hold.target_type.value,
            "target_id": hold.target_id,
            "reason_hash": hold.reason_hash,
            "reason_length": hold.reason_length,
            "hashes": [],
            "refs": [],
        },
    }

    return event


def _emit_critical_audit_or_fail(
    audit_sink: AuditSink | None,
    event: dict[str, Any],
    operation: str,
) -> None:
    """Emit CRITICAL audit event or fail the operation.

    Audit emission is fatal for legal hold operations per v6.3 §6.3.

    Args:
        audit_sink: The audit sink to emit to.
        event: The audit event to emit.
        operation: Description of the operation for error messages.

    Raises:
        IdisHttpError: 500 if audit emission fails.
    """
    if audit_sink is None:
        logger.error(
            "Legal hold audit sink not configured; %s BLOCKED (audit required)",
            operation,
        )
        raise IdisHttpError(
            status_code=500,
            code="HOLD_AUDIT_REQUIRED",
            message="Operation failed: audit requirement not met",
        )

    try:
        audit_sink.emit(event)
    except Exception as e:
        logger.error(
            "Legal hold audit emission failed for %s: %s",
            operation,
            type(e).__name__,
        )
        raise IdisHttpError(
            status_code=500,
            code="HOLD_AUDIT_FAILED",
            message="Operation failed: audit requirement not met",
        ) from None


def apply_hold(
    tenant_ctx: TenantContext,
    target_type: HoldTarget,
    target_id: str,
    reason: str,
    audit_sink: AuditSink | None = None,
    registry: LegalHoldRegistry | None = None,
) -> LegalHold:
    """Apply a legal hold to a resource.

    Held items cannot be deleted until the hold is lifted.
    This operation emits a CRITICAL severity audit event.

    Args:
        tenant_ctx: The tenant context.
        target_type: Type of resource to hold (DEAL, DOCUMENT, ARTIFACT).
        target_id: ID of the resource to hold.
        reason: Reason for the hold (stored as hash, never logged raw).
        audit_sink: Audit sink for emission (fatal if fails).
        registry: Hold registry (uses default if None).

    Returns:
        The created LegalHold.

    Raises:
        IdisHttpError: 400 if inputs invalid, 500 if audit fails.
    """
    if not target_id or not target_id.strip():
        raise IdisHttpError(
            status_code=400,
            code="HOLD_INVALID_TARGET",
            message="Target ID cannot be empty",
        )

    if not reason or not reason.strip():
        raise IdisHttpError(
            status_code=400,
            code="HOLD_INVALID_REASON",
            message="Hold reason cannot be empty",
        )

    reg = registry or _default_registry

    hold = LegalHold(
        hold_id=str(uuid.uuid4()),
        tenant_id=tenant_ctx.tenant_id,
        target_type=target_type,
        target_id=target_id.strip(),
        reason_hash=_hash_reason(reason),
        reason_length=len(reason),
        applied_at=datetime.now(UTC),
        applied_by=tenant_ctx.actor_id,
    )

    event = _build_hold_audit_event(
        tenant_id=tenant_ctx.tenant_id,
        actor_id=tenant_ctx.actor_id,
        event_type="legal_hold.applied",
        hold=hold,
    )

    _emit_critical_audit_or_fail(audit_sink, event, "apply_hold")

    reg.add(hold)

    logger.info(
        "Legal hold applied: hold_id=%s, target=%s/%s, reason_length=%d",
        hold.hold_id,
        target_type.value,
        target_id,
        len(reason),
    )

    return hold


def lift_hold(
    tenant_ctx: TenantContext,
    hold_id: str,
    audit_sink: AuditSink | None = None,
    registry: LegalHoldRegistry | None = None,
) -> LegalHold:
    """Lift a legal hold.

    This operation emits a CRITICAL severity audit event.

    Args:
        tenant_ctx: The tenant context.
        hold_id: ID of the hold to lift.
        audit_sink: Audit sink for emission (fatal if fails).
        registry: Hold registry (uses default if None).

    Returns:
        The updated LegalHold with lifted_at set.

    Raises:
        IdisHttpError: 404 if hold not found, 400 if already lifted,
                       403 if cross-tenant, 500 if audit fails.
    """
    reg = registry or _default_registry
    existing = reg.get(hold_id)

    if existing is None:
        raise IdisHttpError(
            status_code=404,
            code="HOLD_NOT_FOUND",
            message="Legal hold not found",
        )

    if existing.tenant_id != tenant_ctx.tenant_id:
        raise IdisHttpError(
            status_code=403,
            code="ACCESS_DENIED",
            message="Access denied",
        )

    if not existing.is_active:
        raise IdisHttpError(
            status_code=400,
            code="HOLD_ALREADY_LIFTED",
            message="Legal hold is already lifted",
        )

    lifted_hold = LegalHold(
        hold_id=existing.hold_id,
        tenant_id=existing.tenant_id,
        target_type=existing.target_type,
        target_id=existing.target_id,
        reason_hash=existing.reason_hash,
        reason_length=existing.reason_length,
        applied_at=existing.applied_at,
        applied_by=existing.applied_by,
        lifted_at=datetime.now(UTC),
        lifted_by=tenant_ctx.actor_id,
    )

    event = _build_hold_audit_event(
        tenant_id=tenant_ctx.tenant_id,
        actor_id=tenant_ctx.actor_id,
        event_type="legal_hold.lifted",
        hold=lifted_hold,
    )

    _emit_critical_audit_or_fail(audit_sink, event, "lift_hold")

    reg.update(lifted_hold)

    logger.info(
        "Legal hold lifted: hold_id=%s, target=%s/%s",
        hold_id,
        existing.target_type.value,
        existing.target_id,
    )

    return lifted_hold


def block_deletion_if_held(
    tenant_ctx: TenantContext,
    target_type: HoldTarget,
    target_id: str,
    registry: LegalHoldRegistry | None = None,
) -> None:
    """Block deletion if target has any active legal holds.

    This should be called by deletion paths before any purge/hard delete.

    Args:
        tenant_ctx: The tenant context.
        target_type: Type of resource being deleted.
        target_id: ID of the resource being deleted.
        registry: Hold registry (uses default if None).

    Raises:
        IdisHttpError: 403 if resource is under legal hold.
    """
    reg = registry or _default_registry

    if reg.has_active_hold(tenant_ctx.tenant_id, target_type, target_id):
        logger.warning(
            "Deletion blocked by legal hold: tenant_id=%s, target=%s/%s",
            tenant_ctx.tenant_id,
            target_type.value,
            target_id,
        )
        raise IdisHttpError(
            status_code=403,
            code="DELETION_BLOCKED_BY_HOLD",
            message="Access denied.",
        )


def evaluate_retention(
    retention_class: RetentionClass,
    created_at: datetime,
    policies: dict[RetentionClass, RetentionPolicy] | None = None,
) -> tuple[bool, datetime | None]:
    """Evaluate whether a resource is within retention period.

    Deterministic evaluation based on creation time and policy.

    Args:
        retention_class: The retention class of the resource.
        created_at: When the resource was created.
        policies: Retention policies (uses defaults if None).

    Returns:
        Tuple of (within_retention, earliest_delete_date).
        If within_retention is True, deletion is blocked.
        earliest_delete_date is None if retention is indefinite.
    """
    pol = (policies or DEFAULT_RETENTION_POLICIES).get(retention_class)

    if pol is None:
        return (False, None)

    if pol.retention_days == 0:
        return (False, None)

    earliest_delete = created_at + timedelta(days=pol.retention_days)
    now = datetime.now(UTC)

    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)

    return (now < earliest_delete, earliest_delete)
