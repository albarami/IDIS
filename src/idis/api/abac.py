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
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol

from idis.api.errors import IdisHttpError
from idis.api.policy import Role

if TYPE_CHECKING:
    from collections.abc import Set

    from fastapi import Request

logger = logging.getLogger(__name__)


class AbacDecisionCode(StrEnum):
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


class ClaimDealResolver(Protocol):
    """Protocol for resolving claim_id to deal_id.

    Implementations must be tenant-scoped (use RLS).
    Returns None for unknown claims (no existence leak per ADR-011).
    """

    def resolve_deal_id_for_claim(
        self,
        tenant_id: str,
        claim_id: str,
    ) -> str | None:
        """Resolve claim_id to its parent deal_id.

        Must execute under tenant RLS. Returns None if claim not found
        or not accessible (no cross-tenant existence checks per ADR-011).
        """
        ...


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


class InMemoryClaimDealResolver:
    """In-memory claim->deal resolver for testing and development.

    Production should use a database-backed implementation with RLS.
    """

    def __init__(self) -> None:
        self._claim_deals: dict[tuple[str, str], str] = {}

    def add_claim(self, tenant_id: str, claim_id: str, deal_id: str) -> None:
        """Register a claim's parent deal."""
        self._claim_deals[(tenant_id, claim_id)] = deal_id

    def remove_claim(self, tenant_id: str, claim_id: str) -> None:
        """Remove a claim registration."""
        self._claim_deals.pop((tenant_id, claim_id), None)

    def resolve_deal_id_for_claim(
        self,
        tenant_id: str,
        claim_id: str,
    ) -> str | None:
        """Resolve claim to deal. Returns None if not found (no existence leak)."""
        return self._claim_deals.get((tenant_id, claim_id))

    def clear(self) -> None:
        """Clear all mappings. For testing only."""
        self._claim_deals.clear()


class PostgresClaimDealResolver:
    """Postgres-backed claim->deal resolver for production use.

    Queries the claims table to resolve claim_id to deal_id under tenant RLS.
    Fail-closed: raises IdisHttpError if DB query fails when DB is available.
    """

    def resolve_deal_id_for_claim(
        self,
        tenant_id: str,
        claim_id: str,
        db_conn: Any = None,
    ) -> str | None:
        """Resolve claim_id to deal_id via database query.

        Must execute under tenant RLS context.
        Per ADR-011: Returns None for unknown claims (no existence leak).

        Args:
            tenant_id: Tenant ID for RLS scoping.
            claim_id: Claim ID to resolve.
            db_conn: Database connection from request.state.db_conn.

        Returns:
            deal_id if claim found, None otherwise.

        Raises:
            IdisHttpError: If DB is available but query fails (fail-closed).
        """
        if db_conn is None:
            # No DB connection - cannot resolve, return None (caller handles)
            return None

        try:
            # Query claims table for deal_id
            # RLS ensures tenant isolation automatically
            # Note: Using SQLAlchemy text() for proper parameterization
            from sqlalchemy import text

            cursor = db_conn.execute(
                text(
                    """
                    SELECT deal_id FROM claims
                    WHERE claim_id = :claim_id
                    LIMIT 1
                    """
                ),
                {"claim_id": claim_id},
            )
            row = cursor.fetchone()
            if row:
                return str(row[0])
            return None
        except Exception as e:
            # Fail-closed: DB error means we cannot verify access
            logger.error(
                "PostgresClaimDealResolver query failed: %s",
                str(e),
                extra={"tenant_id": tenant_id, "claim_id": claim_id},
            )
            raise IdisHttpError(
                status_code=500,
                code="claim_resolution_failed",
                message="Failed to resolve claim access",
            ) from e


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
_default_claim_resolver: ClaimDealResolver | None = None


def get_claim_deal_resolver() -> ClaimDealResolver:
    """Get the configured claim->deal resolver.

    Returns in-memory resolver by default. Production should configure
    a database-backed resolver.
    """
    global _default_claim_resolver
    if _default_claim_resolver is None:
        _default_claim_resolver = InMemoryClaimDealResolver()
    return _default_claim_resolver


def set_claim_deal_resolver(resolver: ClaimDealResolver) -> None:
    """Set the claim->deal resolver. For testing and configuration."""
    global _default_claim_resolver
    _default_claim_resolver = resolver


def resolve_deal_id_for_claim(
    tenant_id: str,
    claim_id: str,
    resolver: ClaimDealResolver | None = None,
    request: Request | None = None,
) -> str | None:
    """Resolve a claim_id to its parent deal_id.

    This function is the main entry point for claim->deal resolution.
    Must execute under tenant RLS context.

    Resolution strategy:
    1. If request has db_conn, use PostgresClaimDealResolver (production)
    2. Otherwise, use configured resolver (in-memory for tests)

    Per ADR-011: Returns None for unknown claims (no cross-tenant existence leak).

    Args:
        tenant_id: Tenant ID from auth context.
        claim_id: Claim ID to resolve.
        resolver: Optional resolver override (for testing).
        request: Optional FastAPI request for DB connection access.

    Returns:
        deal_id if claim found and accessible, None otherwise.

    Raises:
        IdisHttpError: If DB is available but query fails (fail-closed).
    """
    if not tenant_id or not claim_id:
        return None

    import os

    # Production path: use Postgres resolver when DB connection is available
    if request is not None:
        db_conn = getattr(request.state, "db_conn", None)
        if db_conn is not None:
            postgres_resolver = PostgresClaimDealResolver()
            return postgres_resolver.resolve_deal_id_for_claim(tenant_id, claim_id, db_conn=db_conn)

        # Check if Postgres is expected (DATABASE_URL configured)
        # If yes, fail-closed when db_conn is missing
        # If no, allow fallback to in-memory resolver for test environments
        database_url = os.environ.get("IDIS_DATABASE_URL")
        if database_url:
            # Fail-closed: production context requires db_conn for claim resolution
            # Return 403 (not 500/503) - this is an authorization denial
            logger.warning(
                "Claim resolution unavailable: db_conn missing but DATABASE_URL set",
                extra={"tenant_id": tenant_id, "claim_id": claim_id},
            )
            raise IdisHttpError(
                status_code=403,
                code="ABAC_RESOLUTION_FAILED",
                message="Access denied.",
            )
        # No DATABASE_URL = test environment, allow fallback to in-memory resolver

    # Test/fallback path: use configured resolver
    if resolver is None:
        resolver = get_claim_deal_resolver()

    return resolver.resolve_deal_id_for_claim(tenant_id, claim_id)


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
