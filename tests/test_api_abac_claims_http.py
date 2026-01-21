"""HTTP-level ABAC tests for claim endpoints.

Tests that ABAC policy is correctly defined for claim-scoped operations.
The actual ABAC enforcement logic is tested in test_api_abac_claims.py.

These tests verify:
- ABAC_CLAIM_SCOPED_OPS includes expected operations
- Policy rules are correctly configured
"""

from __future__ import annotations

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
