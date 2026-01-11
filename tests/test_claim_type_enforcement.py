"""Tests for Claim type enforcement (PRIMARY vs DERIVED).

Phase POST-5.2: Tests for claim_type field and its role in the
calc loop guardrail enforcement.
"""

from __future__ import annotations

from decimal import Decimal

from idis.models.calc_sanad import SanadGrade
from idis.models.claim import (
    Claim,
    ClaimAction,
    ClaimClass,
    ClaimType,
    ClaimVerdict,
)
from idis.models.value_structs import Currency, MonetaryValue


def make_claim(
    claim_id: str = "claim-001",
    claim_type: ClaimType = ClaimType.PRIMARY,
    source_calc_id: str | None = None,
) -> Claim:
    """Create a test claim with minimal required fields."""
    return Claim(
        claim_id=claim_id,
        tenant_id="tenant-001",
        deal_id="deal-001",
        claim_class=ClaimClass.FINANCIAL,
        claim_text="Revenue is $5M ARR",
        claim_type=claim_type,
        claim_grade=SanadGrade.B,
        claim_verdict=ClaimVerdict.VERIFIED,
        claim_action=ClaimAction.NONE,
        source_calc_id=source_calc_id,
    )


class TestClaimTypeField:
    """Tests for claim_type field on Claim model."""

    def test_default_is_primary(self) -> None:
        """Test that claim_type defaults to PRIMARY."""
        claim = Claim(
            claim_id="claim-001",
            tenant_id="tenant-001",
            deal_id="deal-001",
            claim_class=ClaimClass.FINANCIAL,
            claim_text="Revenue is $5M ARR",
            claim_grade=SanadGrade.B,
            claim_verdict=ClaimVerdict.VERIFIED,
            claim_action=ClaimAction.NONE,
        )
        assert claim.claim_type == ClaimType.PRIMARY

    def test_explicit_primary(self) -> None:
        """Test creating an explicit PRIMARY claim."""
        claim = make_claim(claim_type=ClaimType.PRIMARY)
        assert claim.claim_type == ClaimType.PRIMARY

    def test_explicit_derived(self) -> None:
        """Test creating an explicit DERIVED claim."""
        claim = make_claim(
            claim_type=ClaimType.DERIVED,
            source_calc_id="calc-001",
        )
        assert claim.claim_type == ClaimType.DERIVED
        assert claim.source_calc_id == "calc-001"

    def test_derived_from_calc_has_source_calc_id(self) -> None:
        """Test that DERIVED claims track their source calculation."""
        claim = make_claim(
            claim_type=ClaimType.DERIVED,
            source_calc_id="calc-irr-001",
        )
        assert claim.source_calc_id == "calc-irr-001"

    def test_primary_no_source_calc_id(self) -> None:
        """Test that PRIMARY claims have no source_calc_id."""
        claim = make_claim(claim_type=ClaimType.PRIMARY)
        assert claim.source_calc_id is None


class TestCanTriggerCalc:
    """Tests for can_trigger_calc() method."""

    def test_primary_can_trigger(self) -> None:
        """Test that PRIMARY claims can trigger calculations."""
        claim = make_claim(claim_type=ClaimType.PRIMARY)
        assert claim.can_trigger_calc() is True

    def test_derived_cannot_trigger(self) -> None:
        """Test that DERIVED claims cannot trigger calculations."""
        claim = make_claim(claim_type=ClaimType.DERIVED)
        assert claim.can_trigger_calc() is False


class TestClaimWithValueStruct:
    """Tests for Claim with typed ValueStruct values."""

    def test_claim_with_monetary_value(self) -> None:
        """Test creating a claim with MonetaryValue."""
        value = MonetaryValue(
            amount=Decimal("5000000"),
            currency=Currency.USD,
        )
        claim = Claim(
            claim_id="claim-001",
            tenant_id="tenant-001",
            deal_id="deal-001",
            claim_class=ClaimClass.FINANCIAL,
            claim_text="ARR is $5M",
            claim_type=ClaimType.PRIMARY,
            value=value,
            claim_grade=SanadGrade.A,
            claim_verdict=ClaimVerdict.VERIFIED,
            claim_action=ClaimAction.NONE,
        )
        assert claim.value is not None
        assert isinstance(claim.value, MonetaryValue)
        assert claim.value.amount == Decimal("5000000")

    def test_claim_to_db_dict_with_value(self) -> None:
        """Test serialization of claim with ValueStruct."""
        value = MonetaryValue(
            amount=Decimal("5000000"),
            currency=Currency.USD,
        )
        claim = Claim(
            claim_id="claim-001",
            tenant_id="tenant-001",
            deal_id="deal-001",
            claim_class=ClaimClass.FINANCIAL,
            claim_text="ARR is $5M",
            value=value,
            claim_grade=SanadGrade.A,
            claim_verdict=ClaimVerdict.VERIFIED,
            claim_action=ClaimAction.NONE,
        )
        db_dict = claim.to_db_dict()
        assert db_dict["value"] is not None
        assert db_dict["value"]["type"] == "monetary"
        assert db_dict["value"]["amount"] == "5000000"


class TestClaimTypeEnumValues:
    """Tests for ClaimType enum values."""

    def test_primary_value(self) -> None:
        """Test PRIMARY enum value."""
        assert ClaimType.PRIMARY.value == "primary"

    def test_derived_value(self) -> None:
        """Test DERIVED enum value."""
        assert ClaimType.DERIVED.value == "derived"

    def test_string_comparison(self) -> None:
        """Test string comparison works."""
        assert ClaimType.PRIMARY == "primary"
        assert ClaimType.DERIVED == "derived"


class TestClaimSerialization:
    """Tests for Claim serialization with claim_type."""

    def test_to_db_dict_includes_claim_type(self) -> None:
        """Test that to_db_dict includes claim_type."""
        claim = make_claim(claim_type=ClaimType.DERIVED)
        db_dict = claim.to_db_dict()
        assert db_dict["claim_type"] == "derived"

    def test_to_db_dict_primary(self) -> None:
        """Test to_db_dict for PRIMARY claim."""
        claim = make_claim(claim_type=ClaimType.PRIMARY)
        db_dict = claim.to_db_dict()
        assert db_dict["claim_type"] == "primary"
        assert db_dict["source_calc_id"] is None

    def test_to_db_dict_derived_with_source(self) -> None:
        """Test to_db_dict for DERIVED claim with source_calc_id."""
        claim = make_claim(
            claim_type=ClaimType.DERIVED,
            source_calc_id="calc-moic-001",
        )
        db_dict = claim.to_db_dict()
        assert db_dict["claim_type"] == "derived"
        assert db_dict["source_calc_id"] == "calc-moic-001"
