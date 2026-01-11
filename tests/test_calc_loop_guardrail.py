"""Tests for Calc Loop Guardrail enforcement.

Phase POST-5.2: Tests for CalcLoopGuard that prevents derived claims
from auto-triggering additional calculations (preventing infinite loops).
"""

from __future__ import annotations

import pytest

from idis.models.calc_sanad import SanadGrade
from idis.models.claim import (
    CalcLoopGuard,
    CalcLoopGuardError,
    Claim,
    ClaimAction,
    ClaimClass,
    ClaimType,
    ClaimVerdict,
)


def make_claim(
    claim_id: str,
    claim_type: ClaimType = ClaimType.PRIMARY,
    source_calc_id: str | None = None,
) -> Claim:
    """Create a test claim with minimal required fields."""
    return Claim(
        claim_id=claim_id,
        tenant_id="tenant-001",
        deal_id="deal-001",
        claim_class=ClaimClass.FINANCIAL,
        claim_text=f"Test claim {claim_id}",
        claim_type=claim_type,
        claim_grade=SanadGrade.B,
        claim_verdict=ClaimVerdict.VERIFIED,
        claim_action=ClaimAction.NONE,
        source_calc_id=source_calc_id,
    )


class TestCalcLoopGuardValidation:
    """Tests for CalcLoopGuard.validate_calc_trigger()."""

    def test_primary_claims_allowed(self) -> None:
        """Test that PRIMARY claims pass validation."""
        guard = CalcLoopGuard()
        claims = [
            make_claim("claim-001", ClaimType.PRIMARY),
            make_claim("claim-002", ClaimType.PRIMARY),
        ]
        result = guard.validate_calc_trigger(claims)
        assert len(result) == 2

    def test_derived_claims_rejected(self) -> None:
        """Test that DERIVED claims are rejected by default."""
        guard = CalcLoopGuard()
        claims = [
            make_claim("claim-001", ClaimType.DERIVED, "calc-001"),
        ]
        with pytest.raises(CalcLoopGuardError) as exc_info:
            guard.validate_calc_trigger(claims)

        assert len(exc_info.value.derived_claims) == 1
        assert "calc loop guardrail violation" in str(exc_info.value).lower()

    def test_mixed_claims_rejected(self) -> None:
        """Test that mixed primary/derived claims are rejected."""
        guard = CalcLoopGuard()
        claims = [
            make_claim("claim-001", ClaimType.PRIMARY),
            make_claim("claim-002", ClaimType.DERIVED, "calc-001"),
            make_claim("claim-003", ClaimType.PRIMARY),
        ]
        with pytest.raises(CalcLoopGuardError) as exc_info:
            guard.validate_calc_trigger(claims)

        # Only the derived claim should be in the error
        assert len(exc_info.value.derived_claims) == 1
        assert exc_info.value.derived_claims[0].claim_id == "claim-002"

    def test_allow_derived_override(self) -> None:
        """Test that allow_derived=True permits derived claims."""
        guard = CalcLoopGuard()
        claims = [
            make_claim("claim-001", ClaimType.DERIVED, "calc-001"),
        ]
        # Should not raise with allow_derived=True
        result = guard.validate_calc_trigger(claims, allow_derived=True)
        assert len(result) == 1

    def test_empty_claims_list(self) -> None:
        """Test that empty claims list passes validation."""
        guard = CalcLoopGuard()
        result = guard.validate_calc_trigger([])
        assert result == []


class TestCalcLoopGuardFiltering:
    """Tests for CalcLoopGuard.filter_triggerable()."""

    def test_filter_returns_only_primary(self) -> None:
        """Test that filter returns only PRIMARY claims."""
        guard = CalcLoopGuard()
        claims = [
            make_claim("claim-001", ClaimType.PRIMARY),
            make_claim("claim-002", ClaimType.DERIVED, "calc-001"),
            make_claim("claim-003", ClaimType.PRIMARY),
            make_claim("claim-004", ClaimType.DERIVED, "calc-002"),
        ]
        result = guard.filter_triggerable(claims)

        assert len(result) == 2
        assert all(c.claim_type == ClaimType.PRIMARY for c in result)
        assert {c.claim_id for c in result} == {"claim-001", "claim-003"}

    def test_filter_all_derived_returns_empty(self) -> None:
        """Test that filtering all derived claims returns empty list."""
        guard = CalcLoopGuard()
        claims = [
            make_claim("claim-001", ClaimType.DERIVED, "calc-001"),
            make_claim("claim-002", ClaimType.DERIVED, "calc-002"),
        ]
        result = guard.filter_triggerable(claims)
        assert result == []

    def test_filter_all_primary_returns_all(self) -> None:
        """Test that filtering all primary claims returns all."""
        guard = CalcLoopGuard()
        claims = [
            make_claim("claim-001", ClaimType.PRIMARY),
            make_claim("claim-002", ClaimType.PRIMARY),
        ]
        result = guard.filter_triggerable(claims)
        assert len(result) == 2


class TestCalcLoopGuardError:
    """Tests for CalcLoopGuardError exception."""

    def test_error_contains_claim_ids(self) -> None:
        """Test that error message contains claim IDs."""
        claims = [
            make_claim("claim-001", ClaimType.DERIVED, "calc-001"),
            make_claim("claim-002", ClaimType.DERIVED, "calc-002"),
        ]
        error = CalcLoopGuardError(claims)

        assert "claim-001" in str(error)
        assert "claim-002" in str(error)
        assert "2 derived claim(s)" in str(error)

    def test_error_has_derived_claims_attr(self) -> None:
        """Test that error has derived_claims attribute."""
        claims = [make_claim("claim-001", ClaimType.DERIVED, "calc-001")]
        error = CalcLoopGuardError(claims)

        assert hasattr(error, "derived_claims")
        assert len(error.derived_claims) == 1


class TestCalcLoopGuardIntegration:
    """Integration tests for calc loop prevention."""

    def test_scenario_calc_produces_derived_claim(self) -> None:
        """Test scenario: calc produces derived claim, which cannot re-trigger."""
        guard = CalcLoopGuard()

        # Step 1: Primary claims trigger a calculation
        primary_claims = [
            make_claim("revenue-claim", ClaimType.PRIMARY),
            make_claim("cost-claim", ClaimType.PRIMARY),
        ]
        validated = guard.validate_calc_trigger(primary_claims)
        assert len(validated) == 2

        # Step 2: Calculation produces a derived claim (e.g., gross margin)
        derived_claim = make_claim(
            "gross-margin-claim",
            ClaimType.DERIVED,
            source_calc_id="calc-gross-margin-001",
        )

        # Step 3: Derived claim cannot trigger another calculation
        with pytest.raises(CalcLoopGuardError):
            guard.validate_calc_trigger([derived_claim])

    def test_scenario_filter_for_batch_calc(self) -> None:
        """Test scenario: filter claims for batch calculation processing."""
        guard = CalcLoopGuard()

        # Mix of primary and derived claims in a deal
        all_claims = [
            make_claim("revenue-2023", ClaimType.PRIMARY),
            make_claim("revenue-2024", ClaimType.PRIMARY),
            make_claim("yoy-growth", ClaimType.DERIVED, "calc-growth-001"),
            make_claim("headcount", ClaimType.PRIMARY),
            make_claim("revenue-per-employee", ClaimType.DERIVED, "calc-rpe-001"),
        ]

        # Filter to get only triggerable claims
        triggerable = guard.filter_triggerable(all_claims)

        # Should only get primary claims
        assert len(triggerable) == 3
        claim_ids = {c.claim_id for c in triggerable}
        assert claim_ids == {"revenue-2023", "revenue-2024", "headcount"}

    def test_scenario_explicit_override_for_recalc(self) -> None:
        """Test scenario: explicit override allows recalculation on derived."""
        guard = CalcLoopGuard()

        # Derived claim from previous calc
        derived_claim = make_claim(
            "irr-claim",
            ClaimType.DERIVED,
            source_calc_id="calc-irr-v1",
        )

        # Normally would fail
        with pytest.raises(CalcLoopGuardError):
            guard.validate_calc_trigger([derived_claim])

        # With explicit override (e.g., human-triggered recalc), it passes
        result = guard.validate_calc_trigger([derived_claim], allow_derived=True)
        assert len(result) == 1
