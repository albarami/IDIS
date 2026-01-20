"""Tests for ABAC (Attribute-Based Access Control) and break-glass.

Required by Phase 7 Task 7.1 roadmap:
- Assigned actor can access deal; unassigned cannot
- Auditor read-only allowed; auditor mutation denied
- Admin unassigned denied without break-glass; allowed with break-glass and emits break_glass.used
- Cross-tenant attempt does not leak existence (ADR-011)
"""

from __future__ import annotations

import os
import time
from typing import Any
from unittest import mock

import pytest

from idis.api.abac import (
    AbacDecisionCode,
    InMemoryDealAssignmentStore,
    check_deal_access,
    check_deal_access_with_break_glass,
    get_deal_assignment_store,
    set_deal_assignment_store,
)
from idis.api.break_glass import (
    BREAK_GLASS_MAX_DURATION_SECONDS,
    BreakGlassToken,
    create_break_glass_token,
    validate_break_glass_token,
)
from idis.api.errors import IdisHttpError
from idis.api.policy import Role


class TestInMemoryDealAssignmentStore:
    """Tests for InMemoryDealAssignmentStore."""

    def test_add_and_check_assignment(self) -> None:
        """Can add and check direct assignment."""
        store = InMemoryDealAssignmentStore()
        store.add_assignment("tenant-1", "deal-1", "user-1")

        assert store.is_actor_assigned("tenant-1", "deal-1", "user-1") is True
        assert store.is_actor_assigned("tenant-1", "deal-1", "user-2") is False

    def test_remove_assignment(self) -> None:
        """Can remove assignment."""
        store = InMemoryDealAssignmentStore()
        store.add_assignment("tenant-1", "deal-1", "user-1")
        store.remove_assignment("tenant-1", "deal-1", "user-1")

        assert store.is_actor_assigned("tenant-1", "deal-1", "user-1") is False

    def test_add_and_check_group_membership(self) -> None:
        """Can add and check group membership."""
        store = InMemoryDealAssignmentStore()
        store.add_group_membership("tenant-1", "deal-1", "user-1")

        assert store.is_actor_in_deal_group("tenant-1", "deal-1", "user-1") is True
        assert store.is_actor_in_deal_group("tenant-1", "deal-1", "user-2") is False

    def test_tenant_isolation(self) -> None:
        """Assignments are tenant-scoped."""
        store = InMemoryDealAssignmentStore()
        store.add_assignment("tenant-1", "deal-1", "user-1")

        assert store.is_actor_assigned("tenant-1", "deal-1", "user-1") is True
        assert store.is_actor_assigned("tenant-2", "deal-1", "user-1") is False

    def test_clear(self) -> None:
        """Can clear all assignments."""
        store = InMemoryDealAssignmentStore()
        store.add_assignment("tenant-1", "deal-1", "user-1")
        store.add_group_membership("tenant-1", "deal-2", "user-2")
        store.clear()

        assert store.is_actor_assigned("tenant-1", "deal-1", "user-1") is False
        assert store.is_actor_in_deal_group("tenant-1", "deal-2", "user-2") is False


class TestCheckDealAccess:
    """Tests for check_deal_access ABAC function."""

    @pytest.fixture
    def store(self) -> InMemoryDealAssignmentStore:
        """Create a fresh assignment store."""
        return InMemoryDealAssignmentStore()

    def test_assigned_actor_allowed(self, store: InMemoryDealAssignmentStore) -> None:
        """Assigned actor can access deal."""
        store.add_assignment("tenant-1", "deal-1", "user-1")

        decision = check_deal_access(
            tenant_id="tenant-1",
            actor_id="user-1",
            roles={Role.ANALYST.value},
            deal_id="deal-1",
            is_mutation=False,
            store=store,
        )

        assert decision.allow is True
        assert decision.code == AbacDecisionCode.ALLOWED

    def test_group_member_allowed(self, store: InMemoryDealAssignmentStore) -> None:
        """Actor in deal group can access deal."""
        store.add_group_membership("tenant-1", "deal-1", "user-1")

        decision = check_deal_access(
            tenant_id="tenant-1",
            actor_id="user-1",
            roles={Role.ANALYST.value},
            deal_id="deal-1",
            is_mutation=False,
            store=store,
        )

        assert decision.allow is True
        assert decision.code == AbacDecisionCode.ALLOWED

    def test_unassigned_actor_denied(self, store: InMemoryDealAssignmentStore) -> None:
        """Unassigned actor cannot access deal."""
        decision = check_deal_access(
            tenant_id="tenant-1",
            actor_id="user-1",
            roles={Role.ANALYST.value},
            deal_id="deal-1",
            is_mutation=False,
            store=store,
        )

        assert decision.allow is False
        assert decision.code == AbacDecisionCode.DENIED_NO_ASSIGNMENT

    def test_auditor_read_allowed_when_assigned(self, store: InMemoryDealAssignmentStore) -> None:
        """Auditor read-only allowed when assigned."""
        store.add_assignment("tenant-1", "deal-1", "auditor-1")

        decision = check_deal_access(
            tenant_id="tenant-1",
            actor_id="auditor-1",
            roles={Role.AUDITOR.value},
            deal_id="deal-1",
            is_mutation=False,
            store=store,
        )

        assert decision.allow is True

    def test_auditor_mutation_denied(self, store: InMemoryDealAssignmentStore) -> None:
        """Auditor mutation denied regardless of assignment."""
        store.add_assignment("tenant-1", "deal-1", "auditor-1")

        decision = check_deal_access(
            tenant_id="tenant-1",
            actor_id="auditor-1",
            roles={Role.AUDITOR.value},
            deal_id="deal-1",
            is_mutation=True,
            store=store,
        )

        assert decision.allow is False
        assert decision.code == AbacDecisionCode.DENIED_AUDITOR_MUTATION

    def test_admin_assigned_allowed(self, store: InMemoryDealAssignmentStore) -> None:
        """Admin assigned to deal is allowed."""
        store.add_assignment("tenant-1", "deal-1", "admin-1")

        decision = check_deal_access(
            tenant_id="tenant-1",
            actor_id="admin-1",
            roles={Role.ADMIN.value},
            deal_id="deal-1",
            is_mutation=True,
            store=store,
        )

        assert decision.allow is True

    def test_admin_unassigned_requires_break_glass(
        self, store: InMemoryDealAssignmentStore
    ) -> None:
        """Admin unassigned denied without break-glass."""
        decision = check_deal_access(
            tenant_id="tenant-1",
            actor_id="admin-1",
            roles={Role.ADMIN.value},
            deal_id="deal-1",
            is_mutation=False,
            store=store,
        )

        assert decision.allow is False
        assert decision.code == AbacDecisionCode.DENIED_BREAK_GLASS_REQUIRED
        assert decision.requires_break_glass is True

    def test_cross_tenant_no_existence_leak(self, store: InMemoryDealAssignmentStore) -> None:
        """Cross-tenant attempt does not leak existence (ADR-011)."""
        store.add_assignment("tenant-1", "deal-1", "user-1")

        decision = check_deal_access(
            tenant_id="tenant-2",
            actor_id="user-1",
            roles={Role.ANALYST.value},
            deal_id="deal-1",
            is_mutation=False,
            store=store,
        )

        assert decision.allow is False
        assert decision.code == AbacDecisionCode.DENIED_NO_ASSIGNMENT
        assert "exist" not in decision.message.lower()

    def test_missing_tenant_id_denied(self, store: InMemoryDealAssignmentStore) -> None:
        """Missing tenant_id is denied."""
        decision = check_deal_access(
            tenant_id="",
            actor_id="user-1",
            roles={Role.ANALYST.value},
            deal_id="deal-1",
            is_mutation=False,
            store=store,
        )

        assert decision.allow is False
        assert decision.code == AbacDecisionCode.DENIED_UNKNOWN_DEAL

    def test_missing_deal_id_denied(self, store: InMemoryDealAssignmentStore) -> None:
        """Missing deal_id is denied."""
        decision = check_deal_access(
            tenant_id="tenant-1",
            actor_id="user-1",
            roles={Role.ANALYST.value},
            deal_id="",
            is_mutation=False,
            store=store,
        )

        assert decision.allow is False
        assert decision.code == AbacDecisionCode.DENIED_UNKNOWN_DEAL


class TestCheckDealAccessWithBreakGlass:
    """Tests for check_deal_access_with_break_glass."""

    @pytest.fixture
    def store(self) -> InMemoryDealAssignmentStore:
        """Create a fresh assignment store."""
        return InMemoryDealAssignmentStore()

    def test_admin_with_break_glass_allowed(self, store: InMemoryDealAssignmentStore) -> None:
        """Admin unassigned allowed with valid break-glass."""
        decision = check_deal_access_with_break_glass(
            tenant_id="tenant-1",
            actor_id="admin-1",
            roles={Role.ADMIN.value},
            deal_id="deal-1",
            is_mutation=False,
            break_glass_valid=True,
            store=store,
        )

        assert decision.allow is True
        assert decision.code == AbacDecisionCode.ALLOWED
        assert "break-glass" in decision.message.lower()

    def test_admin_without_break_glass_denied(self, store: InMemoryDealAssignmentStore) -> None:
        """Admin unassigned denied without break-glass."""
        decision = check_deal_access_with_break_glass(
            tenant_id="tenant-1",
            actor_id="admin-1",
            roles={Role.ADMIN.value},
            deal_id="deal-1",
            is_mutation=False,
            break_glass_valid=False,
            store=store,
        )

        assert decision.allow is False
        assert decision.requires_break_glass is True

    def test_non_admin_break_glass_ignored(self, store: InMemoryDealAssignmentStore) -> None:
        """Non-admin cannot use break-glass."""
        decision = check_deal_access_with_break_glass(
            tenant_id="tenant-1",
            actor_id="user-1",
            roles={Role.ANALYST.value},
            deal_id="deal-1",
            is_mutation=False,
            break_glass_valid=True,
            store=store,
        )

        assert decision.allow is False
        assert decision.code == AbacDecisionCode.DENIED_NO_ASSIGNMENT


class TestBreakGlassToken:
    """Tests for break-glass token creation and validation."""

    @pytest.fixture(autouse=True)
    def setup_secret(self) -> Any:
        """Set up break-glass secret for tests."""
        with mock.patch.dict(
            os.environ,
            {"IDIS_BREAK_GLASS_SECRET": "test-secret-key-at-least-32-chars"},
        ):
            yield

    def test_create_token_success(self) -> None:
        """Can create break-glass token with valid params."""
        token = create_break_glass_token(
            actor_id="admin-1",
            tenant_id="tenant-1",
            justification="Emergency access for incident response",
        )

        assert token is not None
        assert len(token) > 0

    def test_create_token_short_justification_fails(self) -> None:
        """Short justification fails."""
        with pytest.raises(IdisHttpError) as exc_info:
            create_break_glass_token(
                actor_id="admin-1",
                tenant_id="tenant-1",
                justification="short",
            )

        assert exc_info.value.status_code == 400
        assert "justification" in exc_info.value.message.lower()

    def test_create_token_duration_capped(self) -> None:
        """Duration is capped at max."""
        token = create_break_glass_token(
            actor_id="admin-1",
            tenant_id="tenant-1",
            justification="Emergency access for incident response",
            duration_seconds=9999999,
        )

        validation = validate_break_glass_token(
            token,
            expected_tenant_id="tenant-1",
        )

        assert validation.valid is True
        assert validation.token is not None
        max_exp = time.time() + BREAK_GLASS_MAX_DURATION_SECONDS + 60
        assert validation.token.expires_at <= max_exp

    def test_validate_token_success(self) -> None:
        """Can validate a valid token."""
        token = create_break_glass_token(
            actor_id="admin-1",
            tenant_id="tenant-1",
            justification="Emergency access for incident response",
            deal_id="deal-1",
        )

        validation = validate_break_glass_token(
            token,
            expected_tenant_id="tenant-1",
            expected_deal_id="deal-1",
        )

        assert validation.valid is True
        assert validation.token is not None
        assert validation.token.actor_id == "admin-1"
        assert validation.token.tenant_id == "tenant-1"
        assert validation.token.deal_id == "deal-1"
        assert "Emergency access" in validation.token.justification

    def test_validate_token_wrong_tenant_fails(self) -> None:
        """Token with wrong tenant fails."""
        token = create_break_glass_token(
            actor_id="admin-1",
            tenant_id="tenant-1",
            justification="Emergency access for incident response",
        )

        validation = validate_break_glass_token(
            token,
            expected_tenant_id="tenant-2",
        )

        assert validation.valid is False
        assert validation.error_code == "tenant_mismatch"

    def test_validate_token_wrong_deal_fails(self) -> None:
        """Token with wrong deal fails."""
        token = create_break_glass_token(
            actor_id="admin-1",
            tenant_id="tenant-1",
            justification="Emergency access for incident response",
            deal_id="deal-1",
        )

        validation = validate_break_glass_token(
            token,
            expected_tenant_id="tenant-1",
            expected_deal_id="deal-2",
        )

        assert validation.valid is False
        assert validation.error_code == "deal_mismatch"

    def test_validate_token_expired_fails(self) -> None:
        """Expired token fails."""
        token = create_break_glass_token(
            actor_id="admin-1",
            tenant_id="tenant-1",
            justification="Emergency access for incident response",
            duration_seconds=60,
        )

        with mock.patch("idis.api.break_glass.time.time", return_value=time.time() + 120):
            validation = validate_break_glass_token(
                token,
                expected_tenant_id="tenant-1",
            )

        assert validation.valid is False
        assert validation.error_code == "token_expired"

    def test_validate_token_tampered_fails(self) -> None:
        """Tampered token fails."""
        token = create_break_glass_token(
            actor_id="admin-1",
            tenant_id="tenant-1",
            justification="Emergency access for incident response",
        )

        tampered_token = token[:-5] + "xxxxx"

        validation = validate_break_glass_token(
            tampered_token,
            expected_tenant_id="tenant-1",
        )

        assert validation.valid is False

    def test_validate_token_malformed_fails(self) -> None:
        """Malformed token fails."""
        validation = validate_break_glass_token(
            "not-a-valid-token!!!",
            expected_tenant_id="tenant-1",
        )

        assert validation.valid is False
        assert validation.error_code == "invalid_token"

    def test_validate_token_no_secret_fails(self) -> None:
        """Missing secret config fails."""
        with mock.patch.dict(os.environ, {}, clear=True):
            validation = validate_break_glass_token(
                "any-token",
                expected_tenant_id="tenant-1",
            )

        assert validation.valid is False
        assert validation.error_code == "break_glass_not_configured"


class TestBreakGlassTokenDataclass:
    """Tests for BreakGlassToken dataclass."""

    def test_immutable(self) -> None:
        """BreakGlassToken is immutable."""
        token = BreakGlassToken(
            token_id="token-1",
            actor_id="admin-1",
            tenant_id="tenant-1",
            deal_id="deal-1",
            justification="test",
            issued_at=time.time(),
            expires_at=time.time() + 900,
            token_hash="abc123",
        )

        with pytest.raises(AttributeError):
            token.actor_id = "admin-2"  # type: ignore[misc]

    def test_token_hash_stored(self) -> None:
        """Token hash is stored for audit."""
        token = BreakGlassToken(
            token_id="token-1",
            actor_id="admin-1",
            tenant_id="tenant-1",
            deal_id=None,
            justification="test justification",
            issued_at=time.time(),
            expires_at=time.time() + 900,
            token_hash="abc123def456",
        )

        assert token.token_hash == "abc123def456"


class TestGlobalAssignmentStore:
    """Tests for global assignment store management."""

    def test_get_default_store(self) -> None:
        """get_deal_assignment_store returns in-memory store by default."""
        set_deal_assignment_store(None)  # type: ignore[arg-type]
        store = get_deal_assignment_store()
        assert isinstance(store, InMemoryDealAssignmentStore)

    def test_set_custom_store(self) -> None:
        """Can set a custom assignment store."""
        custom_store = InMemoryDealAssignmentStore()
        custom_store.add_assignment("tenant-1", "deal-1", "user-1")

        set_deal_assignment_store(custom_store)

        retrieved = get_deal_assignment_store()
        assert retrieved.is_actor_assigned("tenant-1", "deal-1", "user-1") is True

        set_deal_assignment_store(None)  # type: ignore[arg-type]
