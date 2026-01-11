"""Tests for Deliverable No-Free-Facts Validator — v6.3 Phase 6.1

Tests:
- Factual sentence with empty claim_refs raises with stable error code
- Valid deliverable passes validation
"""

from __future__ import annotations

import pytest

from idis.models.deliverables import (
    AuditAppendix,
    AuditAppendixEntry,
    DeliverableFact,
    DeliverableSection,
    RefType,
    ScreeningSnapshot,
)
from idis.validators.deliverable import (
    DeliverableValidationError,
    validate_deliverable_no_free_facts,
)


class TestDeliverableValidator:
    """Tests for DeliverableValidator."""

    def test_factual_fact_with_empty_refs_raises(self) -> None:
        """Test that a factual sentence with empty claim_refs raises with stable error code."""
        fact_no_refs = DeliverableFact(
            text="Revenue is $10M with 50% YoY growth.",
            claim_refs=[],
            calc_refs=[],
            is_factual=True,
            is_subjective=False,
        )

        section = DeliverableSection(
            section_id="section-001",
            title="Test Section",
            facts=[fact_no_refs],
            is_subjective=False,
        )

        appendix = AuditAppendix(
            entries=[],
            generated_at="2026-01-11T12:00:00Z",
            deal_id="deal-001",
            tenant_id="tenant-001",
        )

        snapshot = ScreeningSnapshot(
            deliverable_id="snap-test-001",
            tenant_id="tenant-001",
            deal_id="deal-001",
            deal_name="Test Corp",
            summary_section=section,
            key_metrics_section=DeliverableSection(section_id="metrics", title="Metrics", facts=[]),
            red_flags_section=DeliverableSection(section_id="flags", title="Red Flags", facts=[]),
            missing_info_section=DeliverableSection(
                section_id="missing",
                title="Missing Info",
                facts=[],
                is_subjective=True,
            ),
            audit_appendix=appendix,
            generated_at="2026-01-11T12:00:00Z",
        )

        with pytest.raises(DeliverableValidationError) as exc_info:
            validate_deliverable_no_free_facts(snapshot)

        assert exc_info.value.code == "NO_FREE_FACTS_VIOLATION"
        assert len(exc_info.value.violations) > 0

        violation = exc_info.value.violations[0]
        assert violation.code == "NO_FREE_FACTS_UNREFERENCED_FACT"
        assert "claim_refs" in violation.message.lower() or "refs" in violation.message.lower()

    def test_valid_deliverable_passes(self) -> None:
        """Test that a valid deliverable with proper refs passes validation."""
        fact_with_refs = DeliverableFact(
            text="Revenue is $10M with 50% YoY growth.",
            claim_refs=["claim-001"],
            calc_refs=["calc-001"],
            is_factual=True,
            is_subjective=False,
        )

        section = DeliverableSection(
            section_id="section-001",
            title="Test Section",
            facts=[fact_with_refs],
            is_subjective=False,
        )

        appendix = AuditAppendix(
            entries=[
                AuditAppendixEntry(ref_id="claim-001", ref_type=RefType.CLAIM),
                AuditAppendixEntry(ref_id="calc-001", ref_type=RefType.CALC),
            ],
            generated_at="2026-01-11T12:00:00Z",
            deal_id="deal-001",
            tenant_id="tenant-001",
        )

        snapshot = ScreeningSnapshot(
            deliverable_id="snap-valid-001",
            tenant_id="tenant-001",
            deal_id="deal-001",
            deal_name="Valid Corp",
            summary_section=section,
            key_metrics_section=DeliverableSection(section_id="metrics", title="Metrics", facts=[]),
            red_flags_section=DeliverableSection(section_id="flags", title="Red Flags", facts=[]),
            missing_info_section=DeliverableSection(
                section_id="missing",
                title="Missing Info",
                facts=[],
                is_subjective=True,
            ),
            audit_appendix=appendix,
            generated_at="2026-01-11T12:00:00Z",
        )

        result = validate_deliverable_no_free_facts(snapshot, raise_on_failure=False)

        assert result.passed is True
        assert len(result.violations) == 0

    def test_subjective_fact_with_factual_true_requires_refs(self) -> None:
        """Test that is_subjective does NOT bypass validation when is_factual=True.

        DG-DET-001 Hard Gate: is_factual=True MUST have refs, regardless of is_subjective.
        If content is truly subjective and shouldn't need refs, set is_factual=False.
        """
        subjective_but_factual = DeliverableFact(
            text="We believe the market opportunity is significant with $50B TAM.",
            claim_refs=[],
            calc_refs=[],
            is_factual=True,
            is_subjective=True,
        )

        section = DeliverableSection(
            section_id="section-subj",
            title="Subjective Section",
            facts=[subjective_but_factual],
            is_subjective=False,
        )

        appendix = AuditAppendix(
            entries=[],
            generated_at="2026-01-11T12:00:00Z",
            deal_id="deal-subj",
            tenant_id="tenant-001",
        )

        snapshot = ScreeningSnapshot(
            deliverable_id="snap-subj",
            tenant_id="tenant-001",
            deal_id="deal-subj",
            deal_name="Subjective Corp",
            summary_section=section,
            key_metrics_section=DeliverableSection(section_id="metrics", title="Metrics", facts=[]),
            red_flags_section=DeliverableSection(section_id="flags", title="Red Flags", facts=[]),
            missing_info_section=DeliverableSection(
                section_id="missing",
                title="Missing Info",
                facts=[],
            ),
            audit_appendix=appendix,
            generated_at="2026-01-11T12:00:00Z",
        )

        result = validate_deliverable_no_free_facts(snapshot, raise_on_failure=False)
        assert result.passed is False
        assert len(result.violations) == 1
        assert result.violations[0].code == "NO_FREE_FACTS_UNREFERENCED_FACT"

    def test_non_factual_subjective_fact_allowed_without_refs(self) -> None:
        """Test that is_factual=False facts can have empty refs (correct approach).

        If content is truly subjective/opinion, set is_factual=False.
        """
        subjective_opinion = DeliverableFact(
            text="We believe the market opportunity is significant.",
            claim_refs=[],
            calc_refs=[],
            is_factual=False,
            is_subjective=True,
        )

        section = DeliverableSection(
            section_id="section-subj",
            title="Subjective Section",
            facts=[subjective_opinion],
            is_subjective=False,
        )

        appendix = AuditAppendix(
            entries=[],
            generated_at="2026-01-11T12:00:00Z",
            deal_id="deal-subj",
            tenant_id="tenant-001",
        )

        snapshot = ScreeningSnapshot(
            deliverable_id="snap-subj",
            tenant_id="tenant-001",
            deal_id="deal-subj",
            deal_name="Subjective Corp",
            summary_section=section,
            key_metrics_section=DeliverableSection(section_id="metrics", title="Metrics", facts=[]),
            red_flags_section=DeliverableSection(section_id="flags", title="Red Flags", facts=[]),
            missing_info_section=DeliverableSection(
                section_id="missing",
                title="Missing Info",
                facts=[],
            ),
            audit_appendix=appendix,
            generated_at="2026-01-11T12:00:00Z",
        )

        result = validate_deliverable_no_free_facts(snapshot, raise_on_failure=False)
        assert result.passed is True

    def test_subjective_section_does_not_bypass_factual_validation(self) -> None:
        """Test that section.is_subjective does NOT bypass validation for factual facts.

        DG-DET-001 Hard Gate: section-level subjectivity cannot bypass per-fact is_factual check.
        """
        factual_in_subjective_section = DeliverableFact(
            text="Revenue is $100M.",
            claim_refs=[],
            calc_refs=[],
            is_factual=True,
            is_subjective=False,
        )

        subjective_section = DeliverableSection(
            section_id="section-all-subj",
            title="All Subjective Section",
            facts=[factual_in_subjective_section],
            is_subjective=True,
        )

        appendix = AuditAppendix(
            entries=[],
            generated_at="2026-01-11T12:00:00Z",
            deal_id="deal-all-subj",
            tenant_id="tenant-001",
        )

        snapshot = ScreeningSnapshot(
            deliverable_id="snap-all-subj",
            tenant_id="tenant-001",
            deal_id="deal-all-subj",
            deal_name="All Subjective Corp",
            summary_section=DeliverableSection(
                section_id="summary",
                title="Summary",
                facts=[
                    DeliverableFact(text="Valid fact.", claim_refs=["claim-001"], is_factual=True)
                ],
            ),
            key_metrics_section=subjective_section,
            red_flags_section=DeliverableSection(section_id="flags", title="Red Flags", facts=[]),
            missing_info_section=DeliverableSection(
                section_id="missing",
                title="Missing Info",
                facts=[],
            ),
            audit_appendix=appendix,
            generated_at="2026-01-11T12:00:00Z",
        )

        result = validate_deliverable_no_free_facts(snapshot, raise_on_failure=False)
        assert result.passed is False
        assert len(result.violations) == 1
        assert result.violations[0].code == "NO_FREE_FACTS_UNREFERENCED_FACT"

    def test_multiple_violations_collected(self) -> None:
        """Test that multiple violations are collected in result."""
        fact1_no_refs = DeliverableFact(
            text="Revenue is $10M.",
            claim_refs=[],
            is_factual=True,
        )
        fact2_no_refs = DeliverableFact(
            text="Growth rate is 50%.",
            claim_refs=[],
            is_factual=True,
        )

        section = DeliverableSection(
            section_id="section-multi",
            title="Multi Violation Section",
            facts=[fact1_no_refs, fact2_no_refs],
        )

        appendix = AuditAppendix(
            entries=[],
            generated_at="2026-01-11T12:00:00Z",
            deal_id="deal-multi",
            tenant_id="tenant-001",
        )

        snapshot = ScreeningSnapshot(
            deliverable_id="snap-multi",
            tenant_id="tenant-001",
            deal_id="deal-multi",
            deal_name="Multi Corp",
            summary_section=section,
            key_metrics_section=DeliverableSection(section_id="metrics", title="Metrics", facts=[]),
            red_flags_section=DeliverableSection(section_id="flags", title="Red Flags", facts=[]),
            missing_info_section=DeliverableSection(
                section_id="missing",
                title="Missing Info",
                facts=[],
                is_subjective=True,
            ),
            audit_appendix=appendix,
            generated_at="2026-01-11T12:00:00Z",
        )

        result = validate_deliverable_no_free_facts(snapshot, raise_on_failure=False)

        assert result.passed is False
        assert len(result.violations) == 2

    def test_calc_refs_satisfy_no_free_facts(self) -> None:
        """Test that calc_refs alone can satisfy No-Free-Facts (not just claim_refs)."""
        fact_with_calc_only = DeliverableFact(
            text="Runway is 18 months.",
            claim_refs=[],
            calc_refs=["calc-runway-001"],
            is_factual=True,
            is_subjective=False,
        )

        section = DeliverableSection(
            section_id="section-calc",
            title="Calc Section",
            facts=[fact_with_calc_only],
        )

        appendix = AuditAppendix(
            entries=[AuditAppendixEntry(ref_id="calc-runway-001", ref_type=RefType.CALC)],
            generated_at="2026-01-11T12:00:00Z",
            deal_id="deal-calc",
            tenant_id="tenant-001",
        )

        snapshot = ScreeningSnapshot(
            deliverable_id="snap-calc",
            tenant_id="tenant-001",
            deal_id="deal-calc",
            deal_name="Calc Corp",
            summary_section=section,
            key_metrics_section=DeliverableSection(section_id="metrics", title="Metrics", facts=[]),
            red_flags_section=DeliverableSection(section_id="flags", title="Red Flags", facts=[]),
            missing_info_section=DeliverableSection(
                section_id="missing",
                title="Missing Info",
                facts=[],
                is_subjective=True,
            ),
            audit_appendix=appendix,
            generated_at="2026-01-11T12:00:00Z",
        )

        result = validate_deliverable_no_free_facts(snapshot, raise_on_failure=False)
        assert result.passed is True


class TestValidatorErrorCode:
    """Tests for stable error codes."""

    def test_error_code_is_stable(self) -> None:
        """Test that error code is stable and documented."""
        fact = DeliverableFact(
            text="ARR of $5M.",
            claim_refs=[],
            is_factual=True,
        )

        section = DeliverableSection(section_id="sec", title="Sec", facts=[fact])

        appendix = AuditAppendix(
            entries=[],
            generated_at="2026-01-11T12:00:00Z",
            deal_id="deal",
            tenant_id="tenant",
        )

        snapshot = ScreeningSnapshot(
            deliverable_id="snap",
            tenant_id="tenant",
            deal_id="deal",
            deal_name="Corp",
            summary_section=section,
            key_metrics_section=DeliverableSection(section_id="m", title="M", facts=[]),
            red_flags_section=DeliverableSection(section_id="f", title="F", facts=[]),
            missing_info_section=DeliverableSection(
                section_id="i", title="I", facts=[], is_subjective=True
            ),
            audit_appendix=appendix,
            generated_at="2026-01-11T12:00:00Z",
        )

        try:
            validate_deliverable_no_free_facts(snapshot)
            pytest.fail("Should have raised DeliverableValidationError")
        except DeliverableValidationError as e:
            assert e.code == "NO_FREE_FACTS_VIOLATION"
            assert e.violations[0].code == "NO_FREE_FACTS_UNREFERENCED_FACT"
