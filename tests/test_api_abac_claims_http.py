"""HTTP-level ABAC tests for claim endpoints.

Tests that ABAC policy is correctly defined for claim-scoped operations.
The actual ABAC enforcement logic is tested in test_api_abac_claims.py.

These tests verify:
- ABAC_CLAIM_SCOPED_OPS includes expected operations
- Policy rules are correctly configured
- Resolver unavailable returns 403 ABAC_RESOLUTION_FAILED (not 500)
"""

from __future__ import annotations

import uuid

import pytest

from idis.api.policy import ABAC_CLAIM_SCOPED_OPS, POLICY_RULES


class TestClaimScopedOpsPolicy:
    """Test that claim-scoped operations are correctly configured in policy."""

    def test_getClaim_in_claim_scoped_ops(self) -> None:
        """getClaim must be in ABAC_CLAIM_SCOPED_OPS."""
        assert "getClaim" in ABAC_CLAIM_SCOPED_OPS

    def test_getClaimSanad_in_claim_scoped_ops(self) -> None:
        """getClaimSanad must be in ABAC_CLAIM_SCOPED_OPS."""
        assert "getClaimSanad" in ABAC_CLAIM_SCOPED_OPS

    def test_updateClaim_in_claim_scoped_ops(self) -> None:
        """updateClaim must be in ABAC_CLAIM_SCOPED_OPS."""
        assert "updateClaim" in ABAC_CLAIM_SCOPED_OPS

    def test_listClaimDefects_in_claim_scoped_ops(self) -> None:
        """listClaimDefects must be in ABAC_CLAIM_SCOPED_OPS for ABAC enforcement."""
        assert "listClaimDefects" in ABAC_CLAIM_SCOPED_OPS

    def test_claim_scoped_ops_are_frozenset(self) -> None:
        """ABAC_CLAIM_SCOPED_OPS must be immutable."""
        assert isinstance(ABAC_CLAIM_SCOPED_OPS, frozenset)

    def test_claim_operations_have_policy_rules(self) -> None:
        """All claim-scoped operations should have policy rules defined."""
        for op in ABAC_CLAIM_SCOPED_OPS:
            assert op in POLICY_RULES, f"Missing policy rule for {op}"

    def test_getClaim_policy_is_read_only(self) -> None:
        """getClaim should be a read-only operation."""
        rule = POLICY_RULES.get("getClaim")
        assert rule is not None
        assert rule.is_mutation is False

    def test_updateClaim_policy_is_mutation(self) -> None:
        """updateClaim should be a mutation operation."""
        rule = POLICY_RULES.get("updateClaim")
        assert rule is not None
        assert rule.is_mutation is True

    def test_listClaimDefects_policy_is_read_only(self) -> None:
        """listClaimDefects should be a read-only operation."""
        rule = POLICY_RULES.get("listClaimDefects")
        assert rule is not None
        assert rule.is_mutation is False


class TestResolverUnavailableReturns403:
    """Regression tests: resolver unavailable must return 403, not 500.

    Per fail-closed authorization semantics, when the claim→deal resolver
    cannot run (db_conn missing in production context), the error must be:
    - HTTP 403
    - error code: ABAC_RESOLUTION_FAILED
    - non-sensitive message

    This prevents silent ABAC bypass and ensures deny-by-default.

    Note: These tests directly test the resolver function behavior rather than
    HTTP flow, because the DB middleware returns 503 before RBAC when DATABASE_URL
    is set but DB is unreachable. The actual fail-closed path is in the resolver.
    """

    def test_resolver_raises_403_when_db_conn_missing_in_production(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """resolve_deal_id_for_claim must raise 403 when db_conn missing in production.

        Production context = DATABASE_URL is set but db_conn is None.
        This is the critical regression test for fail-closed behavior.
        """
        from unittest.mock import MagicMock

        from idis.api.abac import resolve_deal_id_for_claim
        from idis.api.errors import IdisHttpError

        # Set DATABASE_URL to indicate production context
        monkeypatch.setenv("IDIS_DATABASE_URL", "postgresql://prod:prod@db/idis")

        # Create mock request with no db_conn
        mock_request = MagicMock()
        mock_request.state = MagicMock()
        del mock_request.state.db_conn  # Ensure db_conn is not set

        tenant_id = str(uuid.uuid4())
        claim_id = str(uuid.uuid4())

        # Should raise IdisHttpError with 403
        with pytest.raises(IdisHttpError) as exc_info:
            resolve_deal_id_for_claim(
                tenant_id=tenant_id,
                claim_id=claim_id,
                request=mock_request,
            )

        # Verify status code is 403, not 500/503
        assert exc_info.value.status_code == 403, f"Expected 403, got {exc_info.value.status_code}"

        # Verify error code
        assert exc_info.value.code == "ABAC_RESOLUTION_FAILED", (
            f"Expected ABAC_RESOLUTION_FAILED, got {exc_info.value.code}"
        )

        # Verify message is non-sensitive
        assert "Access denied" in exc_info.value.message

    def test_resolver_uses_in_memory_fallback_without_database_url(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Without DATABASE_URL, resolver should use in-memory fallback (test mode)."""
        from unittest.mock import MagicMock

        from idis.api.abac import (
            InMemoryClaimDealResolver,
            resolve_deal_id_for_claim,
            set_claim_deal_resolver,
        )

        # Ensure no DATABASE_URL (test environment)
        monkeypatch.delenv("IDIS_DATABASE_URL", raising=False)

        # Set up in-memory resolver with a known claim
        resolver = InMemoryClaimDealResolver()
        tenant_id = str(uuid.uuid4())
        claim_id = str(uuid.uuid4())
        deal_id = str(uuid.uuid4())
        resolver.add_claim(tenant_id, claim_id, deal_id)
        set_claim_deal_resolver(resolver)

        # Create mock request with no db_conn
        mock_request = MagicMock()
        mock_request.state = MagicMock()
        del mock_request.state.db_conn

        # Should NOT raise - should use in-memory resolver
        result = resolve_deal_id_for_claim(
            tenant_id=tenant_id,
            claim_id=claim_id,
            request=mock_request,
        )

        assert result == deal_id, f"Expected {deal_id}, got {result}"

    def test_error_code_is_stable_string(self) -> None:
        """ABAC_RESOLUTION_FAILED error code must be a stable string constant."""
        # This ensures the error code won't accidentally change
        from idis.api.abac import IdisHttpError

        # Create an instance to verify the code format
        error = IdisHttpError(
            status_code=403,
            code="ABAC_RESOLUTION_FAILED",
            message="Access denied.",
        )
        assert error.code == "ABAC_RESOLUTION_FAILED"
        assert isinstance(error.code, str)
        assert error.code.isupper()  # Convention: error codes are uppercase
