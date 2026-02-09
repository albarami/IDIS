"""Tests for Truth Dashboard Builder — v6.3 Phase 10

Tests:
- Builder creates valid TruthDashboard with deterministic row ordering
- Rows sorted by (dimension, assertion) for determinism
- Audit appendix includes all refs from rows
- NFF: truth rows without refs fail validation
- NFF: valid truth dashboard passes validation
"""

from __future__ import annotations

import pytest

from idis.deliverables.truth_dashboard import TruthDashboardBuilder
from idis.models.deliverables import TruthDashboard, TruthRow
from idis.validators.deliverable import (
    DeliverableValidationError,
    DeliverableValidator,
    validate_deliverable_no_free_facts,
)


class TestTruthDashboardBuilder:
    """Tests for TruthDashboardBuilder."""

    def test_builder_creates_valid_dashboard(self) -> None:
        """Test that builder creates a valid TruthDashboard with all fields."""
        builder = TruthDashboardBuilder(
            deliverable_id="td-001",
            tenant_id="tenant-001",
            deal_id="deal-001",
            deal_name="Acme Corp Series A",
            generated_at="2026-01-11T12:00:00Z",
        )

        builder.add_row(
            dimension="TEAM_QUALITY",
            assertion="Strong founding team with domain expertise.",
            verdict="CONFIRMED",
            claim_refs=["claim-001"],
        )
        builder.add_row(
            dimension="MARKET_ATTRACTIVENESS",
            assertion="Large TAM of $50B.",
            verdict="DISPUTED",
            claim_refs=["claim-002"],
            calc_refs=["calc-001"],
        )
        builder.add_summary_fact(
            text="2 dimensions evaluated.",
            is_subjective=True,
        )

        dashboard = builder.build()

        assert isinstance(dashboard, TruthDashboard)
        assert dashboard.deliverable_type == "TRUTH_DASHBOARD"
        assert dashboard.deliverable_id == "td-001"
        assert dashboard.tenant_id == "tenant-001"
        assert dashboard.deal_id == "deal-001"
        assert dashboard.deal_name == "Acme Corp Series A"
        assert len(dashboard.rows) == 2
        assert dashboard.summary_section is not None
        assert dashboard.audit_appendix is not None

    def test_rows_sorted_by_dimension_then_assertion(self) -> None:
        """Test that rows are deterministically ordered by (dimension, assertion)."""
        builder = TruthDashboardBuilder(
            deliverable_id="td-002",
            tenant_id="t",
            deal_id="d",
            deal_name="Test",
            generated_at="2026-01-11T12:00:00Z",
        )

        builder.add_row(
            dimension="TEAM_QUALITY",
            assertion="B assertion",
            verdict="CONFIRMED",
            claim_refs=["c1"],
        )
        builder.add_row(
            dimension="MARKET_ATTRACTIVENESS",
            assertion="A assertion",
            verdict="CONFIRMED",
            claim_refs=["c2"],
        )
        builder.add_row(
            dimension="MARKET_ATTRACTIVENESS",
            assertion="B assertion",
            verdict="DISPUTED",
            claim_refs=["c3"],
        )
        builder.add_summary_fact(text="summary", is_subjective=True)

        dashboard = builder.build()

        assert len(dashboard.rows) == 3
        assert dashboard.rows[0].dimension == "MARKET_ATTRACTIVENESS"
        assert dashboard.rows[0].assertion == "A assertion"
        assert dashboard.rows[1].dimension == "MARKET_ATTRACTIVENESS"
        assert dashboard.rows[1].assertion == "B assertion"
        assert dashboard.rows[2].dimension == "TEAM_QUALITY"

    def test_determinism_multiple_builds_identical(self) -> None:
        """Test that building twice produces identical output."""

        def make_dashboard() -> TruthDashboard:
            builder = TruthDashboardBuilder(
                deliverable_id="td-det",
                tenant_id="t",
                deal_id="d",
                deal_name="Det Test",
                generated_at="2026-01-11T12:00:00Z",
            )
            builder.add_row(
                dimension="RISK_PROFILE",
                assertion="High regulatory risk.",
                verdict="CONFIRMED",
                claim_refs=["c2", "c1"],
                calc_refs=["k2", "k1"],
            )
            builder.add_summary_fact(text="summary", is_subjective=True)
            return builder.build()

        d1 = make_dashboard()
        d2 = make_dashboard()

        assert d1 == d2
        assert d1.rows[0].claim_refs == ["c1", "c2"]
        assert d1.rows[0].calc_refs == ["k1", "k2"]

    def test_audit_appendix_includes_all_refs(self) -> None:
        """Test that audit appendix contains all unique refs from rows."""
        builder = TruthDashboardBuilder(
            deliverable_id="td-audit",
            tenant_id="t",
            deal_id="d",
            deal_name="Test",
            generated_at="2026-01-11T12:00:00Z",
        )

        builder.add_row(
            dimension="TEAM_QUALITY",
            assertion="test",
            verdict="CONFIRMED",
            claim_refs=["claim-001", "claim-002"],
            calc_refs=["calc-001"],
        )
        builder.add_row(
            dimension="MARKET_ATTRACTIVENESS",
            assertion="test2",
            verdict="DISPUTED",
            claim_refs=["claim-002", "claim-003"],
        )
        builder.add_summary_fact(text="summary", is_subjective=True)

        dashboard = builder.build()

        ref_ids = [e.ref_id for e in dashboard.audit_appendix.entries]
        assert "claim-001" in ref_ids
        assert "claim-002" in ref_ids
        assert "claim-003" in ref_ids
        assert "calc-001" in ref_ids

    def test_truth_row_refs_sorted(self) -> None:
        """Test that TruthRow sorts refs lexicographically."""
        row = TruthRow(
            dimension="TEAM_QUALITY",
            assertion="test",
            verdict="CONFIRMED",
            claim_refs=["c3", "c1", "c2"],
            calc_refs=["k2", "k1"],
        )
        assert row.claim_refs == ["c1", "c2", "c3"]
        assert row.calc_refs == ["k1", "k2"]


class TestTruthDashboardNFF:
    """Tests for Truth Dashboard No-Free-Facts validation."""

    def test_truth_row_without_refs_fails_validation(self) -> None:
        """Test that a truth row with no refs fails NFF validation."""
        builder = TruthDashboardBuilder(
            deliverable_id="td-nff",
            tenant_id="t",
            deal_id="d",
            deal_name="Test",
            generated_at="2026-01-11T12:00:00Z",
        )

        builder.add_row(
            dimension="TEAM_QUALITY",
            assertion="Ungrounded assertion.",
            verdict="CONFIRMED",
            claim_refs=[],
            calc_refs=[],
        )
        builder.add_summary_fact(text="summary", is_subjective=True)

        dashboard = builder.build()

        with pytest.raises(DeliverableValidationError) as exc_info:
            validate_deliverable_no_free_facts(dashboard)

        assert exc_info.value.code == "NO_FREE_FACTS_VIOLATION"
        assert len(exc_info.value.violations) > 0
        assert exc_info.value.violations[0].code == "NO_FREE_FACTS_UNREFERENCED_TRUTH_ROW"

    def test_valid_truth_dashboard_passes_nff(self) -> None:
        """Test that a valid truth dashboard passes NFF validation."""
        builder = TruthDashboardBuilder(
            deliverable_id="td-valid",
            tenant_id="t",
            deal_id="d",
            deal_name="Test",
            generated_at="2026-01-11T12:00:00Z",
        )

        builder.add_row(
            dimension="TEAM_QUALITY",
            assertion="Strong team.",
            verdict="CONFIRMED",
            claim_refs=["claim-001"],
        )
        builder.add_summary_fact(text="summary", is_subjective=True)

        dashboard = builder.build()

        result = validate_deliverable_no_free_facts(dashboard, raise_on_failure=False)
        assert result.passed

    def test_validator_dispatch_routes_truth_dashboard(self) -> None:
        """Test that validator dispatch recognizes TRUTH_DASHBOARD type."""
        validator = DeliverableValidator()
        builder = TruthDashboardBuilder(
            deliverable_id="td-dispatch",
            tenant_id="t",
            deal_id="d",
            deal_name="Test",
            generated_at="2026-01-11T12:00:00Z",
        )
        builder.add_row(
            dimension="TEAM_QUALITY",
            assertion="test",
            verdict="CONFIRMED",
            claim_refs=["c1"],
        )
        builder.add_summary_fact(text="summary", is_subjective=True)
        dashboard = builder.build()

        result = validator.validate(dashboard)
        assert result.passed
