"""Tests for Decline Letter Builder — v6.3 Phase 10

Tests:
- Builder creates valid DeclineLetter with all fields
- Audit appendix includes all refs
- NFF: factual facts without refs fail validation
- NFF: valid decline letter passes validation
- Deterministic output
"""

from __future__ import annotations

import pytest

from idis.deliverables.decline_letter import DeclineLetterBuilder
from idis.models.deliverables import DeclineLetter
from idis.validators.deliverable import (
    DeliverableValidationError,
    DeliverableValidator,
    validate_deliverable_no_free_facts,
)


class TestDeclineLetterBuilder:
    """Tests for DeclineLetterBuilder."""

    def test_builder_creates_valid_letter(self) -> None:
        """Test that builder creates a valid DeclineLetter with all fields."""
        builder = DeclineLetterBuilder(
            deliverable_id="dl-001",
            tenant_id="tenant-001",
            deal_id="deal-001",
            deal_name="Acme Corp Series A",
            generated_at="2026-01-11T12:00:00Z",
            composite_score=42.5,
            score_band="LOW",
        )

        builder.add_rationale_fact(
            text="Weak unit economics with negative contribution margin.",
            claim_refs=["claim-001"],
            calc_refs=["calc-001"],
        )
        builder.add_concern_fact(
            text="Regulatory risk in target market.",
            claim_refs=["claim-002"],
        )
        builder.add_missing_info(text="No audited financials provided.")

        letter = builder.build()

        assert isinstance(letter, DeclineLetter)
        assert letter.deliverable_type == "DECLINE_LETTER"
        assert letter.deliverable_id == "dl-001"
        assert letter.tenant_id == "tenant-001"
        assert letter.deal_id == "deal-001"
        assert letter.deal_name == "Acme Corp Series A"
        assert letter.composite_score == 42.5
        assert letter.score_band == "LOW"
        assert len(letter.rationale_section.facts) == 1
        assert len(letter.key_concerns_section.facts) == 1
        assert len(letter.missing_info_section.facts) == 1
        assert letter.audit_appendix is not None

    def test_audit_appendix_includes_all_refs(self) -> None:
        """Test that audit appendix contains all unique refs."""
        builder = DeclineLetterBuilder(
            deliverable_id="dl-audit",
            tenant_id="t",
            deal_id="d",
            deal_name="Test",
            generated_at="2026-01-11T12:00:00Z",
            composite_score=30.0,
            score_band="LOW",
        )

        builder.add_rationale_fact(
            text="Fact 1",
            claim_refs=["claim-001", "claim-002"],
            calc_refs=["calc-001"],
        )
        builder.add_concern_fact(
            text="Fact 2",
            claim_refs=["claim-002", "claim-003"],
        )

        letter = builder.build()

        ref_ids = [e.ref_id for e in letter.audit_appendix.entries]
        assert "claim-001" in ref_ids
        assert "claim-002" in ref_ids
        assert "claim-003" in ref_ids
        assert "calc-001" in ref_ids

    def test_determinism_multiple_builds_identical(self) -> None:
        """Test that building twice produces identical output."""

        def make_letter() -> DeclineLetter:
            builder = DeclineLetterBuilder(
                deliverable_id="dl-det",
                tenant_id="t",
                deal_id="d",
                deal_name="Det Test",
                generated_at="2026-01-11T12:00:00Z",
                composite_score=25.0,
                score_band="LOW",
            )
            builder.add_rationale_fact(
                text="Weak metrics.",
                claim_refs=["c2", "c1"],
                calc_refs=["k2", "k1"],
            )
            builder.add_concern_fact(
                text="High risk.",
                claim_refs=["c3"],
            )
            builder.add_missing_info(text="Missing data.")
            return builder.build()

        l1 = make_letter()
        l2 = make_letter()

        assert l1 == l2

    def test_additional_sections(self) -> None:
        """Test that custom sections are added correctly."""
        builder = DeclineLetterBuilder(
            deliverable_id="dl-sections",
            tenant_id="t",
            deal_id="d",
            deal_name="Test",
            generated_at="2026-01-11T12:00:00Z",
            composite_score=40.0,
            score_band="LOW",
        )

        builder.add_rationale_fact(text="Base rationale.", claim_refs=["c1"])
        builder.add_concern_fact(text="Base concern.", claim_refs=["c2"])
        builder.add_section(
            section_id="extra-1",
            title="Additional Context",
            facts=[{"text": "Extra info.", "claim_refs": ["c3"]}],
        )

        letter = builder.build()

        assert len(letter.additional_sections) == 1
        assert letter.additional_sections[0].title == "Additional Context"


class TestDeclineLetterNFF:
    """Tests for Decline Letter No-Free-Facts validation."""

    def test_rationale_fact_without_refs_fails_validation(self) -> None:
        """Test that rationale fact with no refs fails NFF validation."""
        builder = DeclineLetterBuilder(
            deliverable_id="dl-nff",
            tenant_id="t",
            deal_id="d",
            deal_name="Test",
            generated_at="2026-01-11T12:00:00Z",
            composite_score=30.0,
            score_band="LOW",
        )

        builder.add_rationale_fact(
            text="Ungrounded decline reason.",
        )
        builder.add_concern_fact(
            text="Valid concern.",
            claim_refs=["c1"],
        )

        letter = builder.build()

        with pytest.raises(DeliverableValidationError) as exc_info:
            validate_deliverable_no_free_facts(letter)

        assert exc_info.value.code == "NO_FREE_FACTS_VIOLATION"
        assert len(exc_info.value.violations) > 0

    def test_concern_fact_without_refs_fails_validation(self) -> None:
        """Test that concern fact with no refs fails NFF validation."""
        builder = DeclineLetterBuilder(
            deliverable_id="dl-nff2",
            tenant_id="t",
            deal_id="d",
            deal_name="Test",
            generated_at="2026-01-11T12:00:00Z",
            composite_score=30.0,
            score_band="LOW",
        )

        builder.add_rationale_fact(text="Valid rationale.", claim_refs=["c1"])
        builder.add_concern_fact(text="Ungrounded concern.")

        letter = builder.build()

        with pytest.raises(DeliverableValidationError) as exc_info:
            validate_deliverable_no_free_facts(letter)

        assert exc_info.value.code == "NO_FREE_FACTS_VIOLATION"

    def test_valid_decline_letter_passes_nff(self) -> None:
        """Test that a valid decline letter passes NFF validation."""
        builder = DeclineLetterBuilder(
            deliverable_id="dl-valid",
            tenant_id="t",
            deal_id="d",
            deal_name="Test",
            generated_at="2026-01-11T12:00:00Z",
            composite_score=35.0,
            score_band="LOW",
        )

        builder.add_rationale_fact(text="Valid rationale.", claim_refs=["c1"])
        builder.add_concern_fact(text="Valid concern.", claim_refs=["c2"])
        builder.add_missing_info(text="Missing data.")

        letter = builder.build()

        result = validate_deliverable_no_free_facts(letter, raise_on_failure=False)
        assert result.passed

    def test_validator_dispatch_routes_decline_letter(self) -> None:
        """Test that validator dispatch recognizes DECLINE_LETTER type."""
        validator = DeliverableValidator()
        builder = DeclineLetterBuilder(
            deliverable_id="dl-dispatch",
            tenant_id="t",
            deal_id="d",
            deal_name="Test",
            generated_at="2026-01-11T12:00:00Z",
            composite_score=30.0,
            score_band="LOW",
        )
        builder.add_rationale_fact(text="test", claim_refs=["c1"])
        builder.add_concern_fact(text="test", claim_refs=["c2"])
        letter = builder.build()

        result = validator.validate(letter)
        assert result.passed
