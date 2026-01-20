"""IDIS Attribute-Based Access Control (ABAC) for deal-level access.

Implements deal-level ABAC per v6.3 Security Threat Model:
- Assignment or group membership required for deal-scoped resources
- Deny-by-default when no assignment exists
- Role-specific access rules:
  - Analysts/Partners: access only when assigned to deal
  - Admin: access when assigned, otherwise require break-glass
  - Auditor: read-only access (mutations denied regardless of assignment)
  - Integration Service: access when assigned

ADR-007: RBAC + deal-level ABAC
ADR-011: No cross-tenant existence checks (leakage rule)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Protocol

from idis.api.policy import Role

if TYPE_CHECKING:
    from collections.abc import Set

logger = logging.getLogger(__name__)


class AbacDecisionCode(str, Enum):
    """ABAC decision codes for audit and error responses."""

    ALLOWED = "ABAC_ALLOWED"
    DENIED_NO_ASSIGNMENT = "ABAC_DENIED_NO_ASSIGNMENT"
    DENIED_AUDITOR_MUTATION = "ABAC_DENIED_AUDITOR_MUTATION"
    DENIED_BREAK_GLASS_REQUIRED = "ABAC_DENIED_BREAK_GLASS_REQUIRED"
    DENIED_UNKNOWN_DEAL = "ABAC_DENIED_UNKNOWN_OR_OUT_OF_SCOPE"


@dataclass(frozen=True, slots=True)
class AbacDecision:
    """Result of ABAC evaluation.

    Attributes:
        allow: True if access is allowed.
        code: Machine-readable decision code.
        message: Human-readable message.
        requires_break_glass: True if access can be granted via break-glass.
    """

    allow: bool
    code: AbacDecisionCode
    message: str
    requires_break_glass: bool = False


class DealAssignmentStore(Protocol):
    """Protocol for deal assignment persistence.

    Implementations must be tenant-scoped and deny-by-default.
    """

    def is_actor_assigned(
        self,
        tenant_id: str,
        deal_id: str,
        actor_id: str,
    ) -> bool:
        """Check if actor is assigned to deal.

        Must be tenant-scoped. Returns False for unknown deals (no existence leak).
        """
        ...

    def is_actor_in_deal_group(
        self,
        tenant_id: str,
        deal_id: str,
        actor_id: str,
    ) -> bool:
        """Check if actor is in a group assigned to deal.

        Must be tenant-scoped. Returns False for unknown deals (no existence leak).
        """
        ...


class InMemoryDealAssignmentStore:
    """In-memory deal assignment store for testing and development.

    Production should use a database-backed implementation.
    """

    def __init__(self) -> None:
        self._assignments: dict[tuple[str, str, str], bool] = {}
        self._group_memberships: dict[tuple[str, str, str], bool] = {}

    def add_assignment(self, tenant_id: str, deal_id: str, actor_id: str) -> None:
        """Add a deal assignment for an actor."""
        self._assignments[(tenant_id, deal_id, actor_id)] = True

    def remove_assignment(self, tenant_id: str, deal_id: str, actor_id: str) -> None:
        """Remove a deal assignment for an actor."""
        self._assignments.pop((tenant_id, deal_id, actor_id), None)

    def add_group_membership(self, tenant_id: str, deal_id: str, actor_id: str) -> None:
        """Add group membership for an actor on a deal."""
        self._group_memberships[(tenant_id, deal_id, actor_id)] = True

    def remove_group_membership(self, tenant_id: str, deal_id: str, actor_id: str) -> None:
        """Remove group membership for an actor on a deal."""
        self._group_memberships.pop((tenant_id, deal_id, actor_id), None)

    def is_actor_assigned(
        self,
        tenant_id: str,
        deal_id: str,
        actor_id: str,
    ) -> bool:
        """Check if actor is directly assigned to deal."""
        return self._assignments.get((tenant_id, deal_id, actor_id), False)

    def is_actor_in_deal_group(
        self,
        tenant_id: str,
        deal_id: str,
        actor_id: str,
    ) -> bool:
        """Check if actor is in a group assigned to deal."""
        return self._group_memberships.get((tenant_id, deal_id, actor_id), False)

    def clear(self) -> None:
        """Clear all assignments. For testing only."""
        self._assignments.clear()
        self._group_memberships.clear()


_default_store: DealAssignmentStore | None = None


def get_deal_assignment_store() -> DealAssignmentStore:
    """Get the configured deal assignment store.

    Returns in-memory store by default. Production should configure
    a database-backed store.
    """
    global _default_store
    if _default_store is None:
        _default_store = InMemoryDealAssignmentStore()
    return _default_store


def set_deal_assignment_store(store: DealAssignmentStore) -> None:
    """Set the deal assignment store. For testing and configuration."""
    global _default_store
    _default_store = store


def check_deal_access(
    *,
    tenant_id: str,
    actor_id: str,
    roles: Set[str],
    deal_id: str,
    is_mutation: bool,
    store: DealAssignmentStore | None = None,
) -> AbacDecision:
    """Check ABAC access for a deal-scoped operation.

    Access rules per v6.3 Security Threat Model:
    1. AUDITOR can only read (mutations always denied)
    2. Assigned actors (direct or group) can access
    3. Unassigned ADMIN can access via break-glass
    4. All others denied

    ADR-011: Never leak deal existence to unauthorized actors.
    Response for unknown deal is same as unauthorized.

    Args:
        tenant_id: Tenant ID from auth context.
        actor_id: Actor ID from auth context.
        roles: Set of roles from auth context.
        deal_id: Deal ID being accessed.
        is_mutation: True if operation modifies state.
        store: Optional assignment store override.

    Returns:
        AbacDecision with allow status and reason.
    """
    if not tenant_id or not actor_id or not deal_id:
        return AbacDecision(
            allow=False,
            code=AbacDecisionCode.DENIED_UNKNOWN_DEAL,
            message="Access denied",
        )

    if store is None:
        store = get_deal_assignment_store()

    role_set = set(roles)

    is_auditor_only = role_set == {Role.AUDITOR.value}
    if is_auditor_only and is_mutation:
        return AbacDecision(
            allow=False,
            code=AbacDecisionCode.DENIED_AUDITOR_MUTATION,
            message="Auditor role cannot perform mutations",
        )

    is_assigned = store.is_actor_assigned(tenant_id, deal_id, actor_id)
    is_in_group = store.is_actor_in_deal_group(tenant_id, deal_id, actor_id)

    if is_assigned or is_in_group:
        return AbacDecision(
            allow=True,
            code=AbacDecisionCode.ALLOWED,
            message="Access granted via assignment",
        )

    has_admin = Role.ADMIN.value in role_set
    if has_admin:
        return AbacDecision(
            allow=False,
            code=AbacDecisionCode.DENIED_BREAK_GLASS_REQUIRED,
            message="Admin access to unassigned deal requires break-glass",
            requires_break_glass=True,
        )

    return AbacDecision(
        allow=False,
        code=AbacDecisionCode.DENIED_NO_ASSIGNMENT,
        message="Access denied",
    )


def check_deal_access_with_break_glass(
    *,
    tenant_id: str,
    actor_id: str,
    roles: Set[str],
    deal_id: str,
    is_mutation: bool,
    break_glass_valid: bool,
    store: DealAssignmentStore | None = None,
) -> AbacDecision:
    """Check ABAC access with break-glass override consideration.

    Same as check_deal_access but allows break-glass override for ADMIN.

    Args:
        tenant_id: Tenant ID from auth context.
        actor_id: Actor ID from auth context.
        roles: Set of roles from auth context.
        deal_id: Deal ID being accessed.
        is_mutation: True if operation modifies state.
        break_glass_valid: True if valid break-glass token provided.
        store: Optional assignment store override.

    Returns:
        AbacDecision with allow status and reason.
    """
    decision = check_deal_access(
        tenant_id=tenant_id,
        actor_id=actor_id,
        roles=roles,
        deal_id=deal_id,
        is_mutation=is_mutation,
        store=store,
    )

    if decision.requires_break_glass and break_glass_valid:
        return AbacDecision(
            allow=True,
            code=AbacDecisionCode.ALLOWED,
            message="Access granted via break-glass override",
        )

    return decision
