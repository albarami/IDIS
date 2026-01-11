"""Tests for Screening Snapshot Builder — v6.3 Phase 6.1

Tests:
- All facts produced by builder include claim_id/calc_id references
- Missing refs fail-closed via validator
"""

from __future__ import annotations

import pytest

from idis.deliverables.screening import (
    ScreeningSnapshotBuilder,
    build_screening_snapshot,
)
from idis.models.deliverables import ScreeningSnapshot
from idis.validators.deliverable import (
    DeliverableValidationError,
    validate_deliverable_no_free_facts,
)


class TestScreeningSnapshotBuilder:
    """Tests for ScreeningSnapshotBuilder."""

    def test_builder_creates_valid_snapshot(self) -> None:
        """Test that builder creates a valid ScreeningSnapshot with all sections."""
        builder = ScreeningSnapshotBuilder(
            deliverable_id="snap-001",
            tenant_id="tenant-001",
            deal_id="deal-001",
            deal_name="Acme Corp Series A",
            generated_at="2026-01-11T12:00:00Z",
        )

        builder.add_summary_fact(
            text="Acme Corp is a B2B SaaS company in the fintech space.",
            claim_refs=["claim-001"],
        )
        builder.add_metric_fact(
            text="ARR of $5M with 40% YoY growth.",
            claim_refs=["claim-002"],
            calc_refs=["calc-001"],
        )
        builder.add_red_flag_fact(
            text="Customer concentration risk: top 3 customers = 60% of revenue.",
            claim_refs=["claim-003"],
        )
        builder.add_missing_info(text="Audited financials for FY2025 not yet received.")

        snapshot = builder.build()

        assert isinstance(snapshot, ScreeningSnapshot)
        assert snapshot.deliverable_type == "SCREENING_SNAPSHOT"
        assert snapshot.deliverable_id == "snap-001"
        assert snapshot.tenant_id == "tenant-001"
        assert snapshot.deal_id == "deal-001"
        assert snapshot.deal_name == "Acme Corp Series A"
        assert len(snapshot.summary_section.facts) == 1
        assert len(snapshot.key_metrics_section.facts) == 1
        assert len(snapshot.red_flags_section.facts) == 1
        assert len(snapshot.missing_info_section.facts) == 1

    def test_all_facts_include_claim_refs(self) -> None:
        """Test that all factual assertions include claim_id references."""
        builder = ScreeningSnapshotBuilder(
            deliverable_id="snap-002",
            tenant_id="tenant-001",
            deal_id="deal-002",
            deal_name="Beta Corp",
            generated_at="2026-01-11T12:00:00Z",
        )

        builder.add_summary_fact(
            text="Beta Corp founded in 2020.",
            claim_refs=["claim-010"],
        )
        builder.add_metric_fact(
            text="Gross margin of 75%.",
            claim_refs=["claim-011"],
            calc_refs=["calc-010"],
        )

        snapshot = builder.build()

        for fact in snapshot.summary_section.facts:
            if fact.is_factual and not fact.is_subjective:
                assert fact.claim_refs or fact.calc_refs, "Factual assertion must have refs"

        for fact in snapshot.key_metrics_section.facts:
            if fact.is_factual and not fact.is_subjective:
                assert fact.claim_refs or fact.calc_refs, "Factual assertion must have refs"

    def test_claim_refs_sorted_lexicographically(self) -> None:
        """Test that claim_refs are sorted lexicographically for stable output."""
        builder = ScreeningSnapshotBuilder(
            deliverable_id="snap-003",
            tenant_id="tenant-001",
            deal_id="deal-003",
            deal_name="Gamma Corp",
            generated_at="2026-01-11T12:00:00Z",
        )

        builder.add_summary_fact(
            text="Multiple sources confirm founding date.",
            claim_refs=["claim-zzz", "claim-aaa", "claim-mmm"],
        )

        snapshot = builder.build()
        fact = snapshot.summary_section.facts[0]

        assert fact.claim_refs == ["claim-aaa", "claim-mmm", "claim-zzz"]

    def test_audit_appendix_contains_all_refs(self) -> None:
        """Test that audit appendix contains all unique refs from deliverable."""
        builder = ScreeningSnapshotBuilder(
            deliverable_id="snap-004",
            tenant_id="tenant-001",
            deal_id="deal-004",
            deal_name="Delta Corp",
            generated_at="2026-01-11T12:00:00Z",
        )

        builder.add_summary_fact(text="Fact 1", claim_refs=["claim-001", "claim-002"])
        builder.add_metric_fact(text="Fact 2", claim_refs=["claim-002"], calc_refs=["calc-001"])
        builder.add_red_flag_fact(text="Fact 3", claim_refs=["claim-003"])

        snapshot = builder.build()

        ref_ids = [e.ref_id for e in snapshot.audit_appendix.entries]
        assert "claim-001" in ref_ids
        assert "claim-002" in ref_ids
        assert "claim-003" in ref_ids
        assert "calc-001" in ref_ids

    def test_audit_appendix_entries_sorted(self) -> None:
        """Test that audit appendix entries are sorted by (ref_type, ref_id)."""
        builder = ScreeningSnapshotBuilder(
            deliverable_id="snap-005",
            tenant_id="tenant-001",
            deal_id="deal-005",
            deal_name="Epsilon Corp",
            generated_at="2026-01-11T12:00:00Z",
        )

        builder.add_summary_fact(text="Fact 1", claim_refs=["claim-zzz"], calc_refs=["calc-aaa"])
        builder.add_metric_fact(text="Fact 2", claim_refs=["claim-aaa"])

        snapshot = builder.build()

        entries = snapshot.audit_appendix.entries
        ref_type_ids = [(e.ref_type.value, e.ref_id) for e in entries]

        expected = [("calc", "calc-aaa"), ("claim", "claim-aaa"), ("claim", "claim-zzz")]
        assert ref_type_ids == expected


class TestScreeningSnapshotValidation:
    """Tests for No-Free-Facts validation of Screening Snapshots."""

    def test_missing_refs_fail_closed(self) -> None:
        """Test that missing refs on factual assertions cause validation failure."""
        builder = ScreeningSnapshotBuilder(
            deliverable_id="snap-fail-001",
            tenant_id="tenant-001",
            deal_id="deal-fail-001",
            deal_name="Failing Corp",
            generated_at="2026-01-11T12:00:00Z",
        )

        builder.add_summary_fact(
            text="Revenue is $10M.",
            claim_refs=[],
        )

        snapshot = builder.build()

        with pytest.raises(DeliverableValidationError) as exc_info:
            validate_deliverable_no_free_facts(snapshot)

        assert exc_info.value.code == "NO_FREE_FACTS_VIOLATION"
        assert len(exc_info.value.violations) > 0

    def test_valid_snapshot_passes_validation(self) -> None:
        """Test that a properly referenced snapshot passes validation."""
        builder = ScreeningSnapshotBuilder(
            deliverable_id="snap-pass-001",
            tenant_id="tenant-001",
            deal_id="deal-pass-001",
            deal_name="Passing Corp",
            generated_at="2026-01-11T12:00:00Z",
        )

        builder.add_summary_fact(
            text="Founded in 2020 in San Francisco.",
            claim_refs=["claim-001"],
        )
        builder.add_metric_fact(
            text="ARR of $5M.",
            claim_refs=["claim-002"],
        )
        builder.add_red_flag_fact(
            text="Key person dependency on CEO.",
            claim_refs=["claim-003"],
        )
        builder.add_missing_info(text="Cap table not provided.")

        snapshot = builder.build()

        result = validate_deliverable_no_free_facts(snapshot, raise_on_failure=False)
        assert result.passed is True
        assert len(result.violations) == 0

    def test_subjective_sections_skip_validation(self) -> None:
        """Test that subjective sections skip No-Free-Facts validation."""
        builder = ScreeningSnapshotBuilder(
            deliverable_id="snap-subj-001",
            tenant_id="tenant-001",
            deal_id="deal-subj-001",
            deal_name="Subjective Corp",
            generated_at="2026-01-11T12:00:00Z",
        )

        builder.add_summary_fact(
            text="This is a factual statement.",
            claim_refs=["claim-001"],
        )

        builder.add_missing_info(text="This subjective text has $10M in it but no refs needed.")

        snapshot = builder.build()

        result = validate_deliverable_no_free_facts(snapshot, raise_on_failure=False)
        assert result.passed is True


class TestBuildScreeningSnapshotConvenience:
    """Tests for build_screening_snapshot convenience function."""

    def test_convenience_function_creates_valid_snapshot(self) -> None:
        """Test that the convenience function creates a valid snapshot."""
        snapshot = build_screening_snapshot(
            deliverable_id="snap-conv-001",
            tenant_id="tenant-001",
            deal_id="deal-conv-001",
            deal_name="Convenience Corp",
            generated_at="2026-01-11T12:00:00Z",
            summary_facts=[
                {"text": "Founded in 2019.", "claim_refs": ["claim-001"]},
            ],
            metric_facts=[
                {"text": "ARR of $3M.", "claim_refs": ["claim-002"], "calc_refs": ["calc-001"]},
            ],
            red_flag_facts=[
                {"text": "High burn rate.", "claim_refs": ["claim-003"]},
            ],
            missing_info=["Need audited financials."],
        )

        assert isinstance(snapshot, ScreeningSnapshot)
        assert snapshot.deliverable_id == "snap-conv-001"
        assert len(snapshot.summary_section.facts) == 1
        assert len(snapshot.key_metrics_section.facts) == 1
        assert len(snapshot.red_flags_section.facts) == 1
        assert len(snapshot.missing_info_section.facts) == 1

        result = validate_deliverable_no_free_facts(snapshot, raise_on_failure=False)
        assert result.passed is True
