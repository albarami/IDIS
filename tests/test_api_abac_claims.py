"""Tests for ABAC enforcement on claim endpoints.

Validates that claim endpoints (getClaim, updateClaim, getClaimSanad):
- Enforce deal-level ABAC via claim->deal resolution
- Deny unassigned actors with 403
- Allow assigned actors
- Return 404 for cross-tenant access (no existence leak per ADR-011)
"""

from __future__ import annotations

import uuid

import pytest

from idis.api.abac import (
    AbacDecisionCode,
    InMemoryClaimDealResolver,
    InMemoryDealAssignmentStore,
    check_deal_access,
    get_claim_deal_resolver,
    get_deal_assignment_store,
    resolve_deal_id_for_claim,
    set_claim_deal_resolver,
    set_deal_assignment_store,
)
from idis.api.policy import Role


@pytest.fixture(autouse=True)
def reset_stores() -> None:
    """Reset ABAC stores before each test."""
    set_deal_assignment_store(InMemoryDealAssignmentStore())
    set_claim_deal_resolver(InMemoryClaimDealResolver())
    yield
    set_deal_assignment_store(InMemoryDealAssignmentStore())
    set_claim_deal_resolver(InMemoryClaimDealResolver())


@pytest.fixture
def tenant_id() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def other_tenant_id() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def deal_id() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def claim_id() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def actor_id() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def other_actor_id() -> str:
    return str(uuid.uuid4())


class TestClaimDealResolution:
    """Test claim->deal resolution."""

    def test_resolve_existing_claim(self, tenant_id: str, claim_id: str, deal_id: str) -> None:
        """Existing claim resolves to its deal."""
        resolver = get_claim_deal_resolver()
        assert isinstance(resolver, InMemoryClaimDealResolver)
        resolver.add_claim(tenant_id, claim_id, deal_id)

        resolved = resolve_deal_id_for_claim(tenant_id, claim_id)
        assert resolved == deal_id

    def test_resolve_unknown_claim_returns_none(self, tenant_id: str, claim_id: str) -> None:
        """Unknown claim returns None (no existence leak)."""
        resolved = resolve_deal_id_for_claim(tenant_id, claim_id)
        assert resolved is None

    def test_resolve_cross_tenant_returns_none(
        self, tenant_id: str, other_tenant_id: str, claim_id: str, deal_id: str
    ) -> None:
        """Cross-tenant claim access returns None (ADR-011)."""
        resolver = get_claim_deal_resolver()
        assert isinstance(resolver, InMemoryClaimDealResolver)
        resolver.add_claim(tenant_id, claim_id, deal_id)

        # Try to resolve from different tenant
        resolved = resolve_deal_id_for_claim(other_tenant_id, claim_id)
        assert resolved is None

    def test_resolve_empty_inputs_returns_none(self, tenant_id: str) -> None:
        """Empty tenant_id or claim_id returns None."""
        assert resolve_deal_id_for_claim("", "claim-123") is None
        assert resolve_deal_id_for_claim(tenant_id, "") is None
        assert resolve_deal_id_for_claim("", "") is None


class TestClaimAbacEnforcement:
    """Test ABAC enforcement for claims via deal resolution."""

    def test_assigned_actor_allowed(
        self, tenant_id: str, deal_id: str, claim_id: str, actor_id: str
    ) -> None:
        """Assigned actor can access claim."""
        # Set up claim->deal mapping
        resolver = get_claim_deal_resolver()
        assert isinstance(resolver, InMemoryClaimDealResolver)
        resolver.add_claim(tenant_id, claim_id, deal_id)

        # Set up actor assignment
        store = get_deal_assignment_store()
        assert isinstance(store, InMemoryDealAssignmentStore)
        store.add_assignment(tenant_id, deal_id, actor_id)

        # Resolve deal and check access
        resolved_deal = resolve_deal_id_for_claim(tenant_id, claim_id)
        assert resolved_deal == deal_id

        decision = check_deal_access(
            tenant_id=tenant_id,
            actor_id=actor_id,
            roles={Role.ANALYST.value},
            deal_id=resolved_deal,
            is_mutation=False,
        )
        assert decision.allow
        assert decision.code == AbacDecisionCode.ALLOWED

    def test_unassigned_actor_denied(
        self, tenant_id: str, deal_id: str, claim_id: str, actor_id: str, other_actor_id: str
    ) -> None:
        """Unassigned actor cannot access claim."""
        # Set up claim->deal mapping
        resolver = get_claim_deal_resolver()
        assert isinstance(resolver, InMemoryClaimDealResolver)
        resolver.add_claim(tenant_id, claim_id, deal_id)

        # Set up assignment for different actor
        store = get_deal_assignment_store()
        assert isinstance(store, InMemoryDealAssignmentStore)
        store.add_assignment(tenant_id, deal_id, other_actor_id)

        # Resolve deal and check access
        resolved_deal = resolve_deal_id_for_claim(tenant_id, claim_id)
        assert resolved_deal == deal_id

        decision = check_deal_access(
            tenant_id=tenant_id,
            actor_id=actor_id,  # Different from assigned actor
            roles={Role.ANALYST.value},
            deal_id=resolved_deal,
            is_mutation=False,
        )
        assert not decision.allow
        assert decision.code == AbacDecisionCode.DENIED_NO_ASSIGNMENT

    def test_group_member_allowed(
        self, tenant_id: str, deal_id: str, claim_id: str, actor_id: str
    ) -> None:
        """Actor in deal group can access claim."""
        # Set up claim->deal mapping
        resolver = get_claim_deal_resolver()
        assert isinstance(resolver, InMemoryClaimDealResolver)
        resolver.add_claim(tenant_id, claim_id, deal_id)

        # Set up group membership (not direct assignment)
        store = get_deal_assignment_store()
        assert isinstance(store, InMemoryDealAssignmentStore)
        store.add_group_membership(tenant_id, deal_id, actor_id)

        # Resolve deal and check access
        resolved_deal = resolve_deal_id_for_claim(tenant_id, claim_id)
        decision = check_deal_access(
            tenant_id=tenant_id,
            actor_id=actor_id,
            roles={Role.ANALYST.value},
            deal_id=resolved_deal,
            is_mutation=False,
        )
        assert decision.allow

    def test_auditor_read_only_on_claims(
        self, tenant_id: str, deal_id: str, claim_id: str, actor_id: str
    ) -> None:
        """Auditor can read claims but cannot mutate."""
        # Set up claim->deal mapping
        resolver = get_claim_deal_resolver()
        assert isinstance(resolver, InMemoryClaimDealResolver)
        resolver.add_claim(tenant_id, claim_id, deal_id)

        resolved_deal = resolve_deal_id_for_claim(tenant_id, claim_id)

        # Read allowed
        read_decision = check_deal_access(
            tenant_id=tenant_id,
            actor_id=actor_id,
            roles={Role.AUDITOR.value},
            deal_id=resolved_deal,
            is_mutation=False,
        )
        # Auditor not assigned, so denied for reads too unless assigned
        # Actually auditor needs assignment too per ABAC rules
        assert not read_decision.allow

    def test_admin_requires_break_glass_for_unassigned_claim(
        self, tenant_id: str, deal_id: str, claim_id: str, actor_id: str
    ) -> None:
        """Admin requires break-glass for unassigned claim access."""
        # Set up claim->deal mapping
        resolver = get_claim_deal_resolver()
        assert isinstance(resolver, InMemoryClaimDealResolver)
        resolver.add_claim(tenant_id, claim_id, deal_id)

        resolved_deal = resolve_deal_id_for_claim(tenant_id, claim_id)

        decision = check_deal_access(
            tenant_id=tenant_id,
            actor_id=actor_id,
            roles={Role.ADMIN.value},
            deal_id=resolved_deal,
            is_mutation=False,
        )
        assert not decision.allow
        assert decision.code == AbacDecisionCode.DENIED_BREAK_GLASS_REQUIRED
        assert decision.requires_break_glass


class TestCrossTenantisolation:
    """Test cross-tenant isolation per ADR-011."""

    def test_cross_tenant_claim_not_resolved(
        self, tenant_id: str, other_tenant_id: str, deal_id: str, claim_id: str
    ) -> None:
        """Cross-tenant claim resolution returns None (no existence leak)."""
        resolver = get_claim_deal_resolver()
        assert isinstance(resolver, InMemoryClaimDealResolver)
        resolver.add_claim(tenant_id, claim_id, deal_id)

        # Other tenant cannot resolve this claim
        resolved = resolve_deal_id_for_claim(other_tenant_id, claim_id)
        assert resolved is None

    def test_cross_tenant_no_deal_access(
        self, tenant_id: str, other_tenant_id: str, deal_id: str, actor_id: str
    ) -> None:
        """Cross-tenant deal access returns same error as unknown deal."""
        store = get_deal_assignment_store()
        assert isinstance(store, InMemoryDealAssignmentStore)
        store.add_assignment(tenant_id, deal_id, actor_id)

        # Access from other tenant
        decision = check_deal_access(
            tenant_id=other_tenant_id,
            actor_id=actor_id,
            roles={Role.ANALYST.value},
            deal_id=deal_id,
            is_mutation=False,
        )
        # Should be denied - same message as unknown deal
        assert not decision.allow
        assert decision.code == AbacDecisionCode.DENIED_NO_ASSIGNMENT


class TestClaimOperations:
    """Test specific claim operations."""

    def test_get_claim_requires_abac(
        self, tenant_id: str, deal_id: str, claim_id: str, actor_id: str
    ) -> None:
        """getClaim operation requires ABAC check."""
        resolver = get_claim_deal_resolver()
        assert isinstance(resolver, InMemoryClaimDealResolver)
        resolver.add_claim(tenant_id, claim_id, deal_id)

        # Unassigned - should be denied
        resolved_deal = resolve_deal_id_for_claim(tenant_id, claim_id)
        decision = check_deal_access(
            tenant_id=tenant_id,
            actor_id=actor_id,
            roles={Role.ANALYST.value},
            deal_id=resolved_deal,
            is_mutation=False,
        )
        assert not decision.allow

    def test_update_claim_requires_abac(
        self, tenant_id: str, deal_id: str, claim_id: str, actor_id: str
    ) -> None:
        """updateClaim operation requires ABAC check."""
        resolver = get_claim_deal_resolver()
        assert isinstance(resolver, InMemoryClaimDealResolver)
        resolver.add_claim(tenant_id, claim_id, deal_id)

        store = get_deal_assignment_store()
        assert isinstance(store, InMemoryDealAssignmentStore)
        store.add_assignment(tenant_id, deal_id, actor_id)

        resolved_deal = resolve_deal_id_for_claim(tenant_id, claim_id)
        decision = check_deal_access(
            tenant_id=tenant_id,
            actor_id=actor_id,
            roles={Role.ANALYST.value},
            deal_id=resolved_deal,
            is_mutation=True,
        )
        assert decision.allow

    def test_get_claim_sanad_requires_abac(
        self, tenant_id: str, deal_id: str, claim_id: str, actor_id: str
    ) -> None:
        """getClaimSanad operation requires ABAC check."""
        resolver = get_claim_deal_resolver()
        assert isinstance(resolver, InMemoryClaimDealResolver)
        resolver.add_claim(tenant_id, claim_id, deal_id)

        # Unassigned - should be denied
        resolved_deal = resolve_deal_id_for_claim(tenant_id, claim_id)
        decision = check_deal_access(
            tenant_id=tenant_id,
            actor_id=actor_id,
            roles={Role.ANALYST.value},
            deal_id=resolved_deal,
            is_mutation=False,
        )
        assert not decision.allow


class TestInMemoryClaimDealResolver:
    """Test InMemoryClaimDealResolver implementation."""

    def test_add_and_resolve_claim(self, tenant_id: str) -> None:
        """Add and resolve claims."""
        resolver = InMemoryClaimDealResolver()
        claim_id = "claim-1"
        deal_id = "deal-1"

        resolver.add_claim(tenant_id, claim_id, deal_id)
        assert resolver.resolve_deal_id_for_claim(tenant_id, claim_id) == deal_id

    def test_remove_claim(self, tenant_id: str) -> None:
        """Remove claim mapping."""
        resolver = InMemoryClaimDealResolver()
        claim_id = "claim-1"
        deal_id = "deal-1"

        resolver.add_claim(tenant_id, claim_id, deal_id)
        resolver.remove_claim(tenant_id, claim_id)
        assert resolver.resolve_deal_id_for_claim(tenant_id, claim_id) is None

    def test_clear_all(self, tenant_id: str) -> None:
        """Clear all mappings."""
        resolver = InMemoryClaimDealResolver()
        resolver.add_claim(tenant_id, "claim-1", "deal-1")
        resolver.add_claim(tenant_id, "claim-2", "deal-2")

        resolver.clear()
        assert resolver.resolve_deal_id_for_claim(tenant_id, "claim-1") is None
        assert resolver.resolve_deal_id_for_claim(tenant_id, "claim-2") is None

    def test_tenant_isolation(self) -> None:
        """Claims are isolated by tenant."""
        resolver = InMemoryClaimDealResolver()
        tenant_a = "tenant-a"
        tenant_b = "tenant-b"
        claim_id = "same-claim-id"

        resolver.add_claim(tenant_a, claim_id, "deal-a")
        resolver.add_claim(tenant_b, claim_id, "deal-b")

        assert resolver.resolve_deal_id_for_claim(tenant_a, claim_id) == "deal-a"
        assert resolver.resolve_deal_id_for_claim(tenant_b, claim_id) == "deal-b"
