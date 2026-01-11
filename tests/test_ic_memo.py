"""Tests for IC Memo Builder — v6.3 Phase 6.1

Tests:
- Memo sections are evidence-linked
- Stable dissent in input state produces a dissent section with refs
- Empty dissent refs are rejected
"""

from __future__ import annotations

import pytest

from idis.deliverables.memo import (
    ICMemoBuilder,
    ICMemoBuilderError,
    build_ic_memo,
)
from idis.models.deliverables import ICMemo
from idis.validators.deliverable import (
    DeliverableValidationError,
    validate_deliverable_no_free_facts,
)


class TestICMemoBuilder:
    """Tests for ICMemoBuilder."""

    def test_builder_creates_valid_memo(self) -> None:
        """Test that builder creates a valid ICMemo with all sections."""
        builder = ICMemoBuilder(
            deliverable_id="memo-001",
            tenant_id="tenant-001",
            deal_id="deal-001",
            deal_name="Acme Corp Series A",
            generated_at="2026-01-11T12:00:00Z",
        )

        builder.add_executive_summary_fact(
            text="Acme Corp is a high-growth fintech company.",
            claim_refs=["claim-001"],
        )
        builder.add_company_overview_fact(
            text="Founded in 2018 in San Francisco.",
            claim_refs=["claim-002"],
        )
        builder.add_market_analysis_fact(
            text="TAM of $50B in the payments space.",
            claim_refs=["claim-003"],
        )
        builder.add_financials_fact(
            text="ARR of $10M with 60% YoY growth.",
            claim_refs=["claim-004"],
            calc_refs=["calc-001"],
        )
        builder.add_team_assessment_fact(
            text="CEO has 15 years of fintech experience.",
            claim_refs=["claim-005"],
        )
        builder.add_risks_fact(
            text="Regulatory risk in key markets.",
            claim_refs=["claim-006"],
        )
        builder.add_recommendation_fact(
            text="Recommend investment at proposed terms.",
            claim_refs=["claim-007"],
        )
        builder.add_truth_dashboard_fact(
            text="85% of claims verified with Grade A/B.",
            claim_refs=["claim-008"],
        )

        memo = builder.build()

        assert isinstance(memo, ICMemo)
        assert memo.deliverable_type == "IC_MEMO"
        assert memo.deliverable_id == "memo-001"
        assert len(memo.executive_summary.facts) == 1
        assert len(memo.company_overview.facts) == 1
        assert len(memo.market_analysis.facts) == 1
        assert len(memo.financials.facts) == 1
        assert len(memo.team_assessment.facts) == 1
        assert len(memo.risks_and_mitigations.facts) == 1
        assert len(memo.recommendation.facts) == 1
        assert len(memo.truth_dashboard_summary.facts) == 1

    def test_all_sections_evidence_linked(self) -> None:
        """Test that all factual sections have evidence links."""
        builder = ICMemoBuilder(
            deliverable_id="memo-002",
            tenant_id="tenant-001",
            deal_id="deal-002",
            deal_name="Beta Corp",
            generated_at="2026-01-11T12:00:00Z",
        )

        for i, method in enumerate(
            [
                builder.add_executive_summary_fact,
                builder.add_company_overview_fact,
                builder.add_market_analysis_fact,
                builder.add_financials_fact,
                builder.add_team_assessment_fact,
                builder.add_risks_fact,
                builder.add_recommendation_fact,
                builder.add_truth_dashboard_fact,
            ]
        ):
            method(
                text=f"Fact {i} for section.",
                claim_refs=[f"claim-{i:03d}"],
            )

        memo = builder.build()

        sections = [
            memo.executive_summary,
            memo.company_overview,
            memo.market_analysis,
            memo.financials,
            memo.team_assessment,
            memo.risks_and_mitigations,
            memo.recommendation,
            memo.truth_dashboard_summary,
        ]

        for section in sections:
            for fact in section.facts:
                if fact.is_factual and not fact.is_subjective:
                    assert fact.claim_refs or fact.calc_refs, (
                        f"Section {section.title} has unlinked fact"
                    )


class TestICMemoDissentSection:
    """Tests for dissent section handling."""

    def test_stable_dissent_produces_dissent_section(self) -> None:
        """Test that stable dissent creates a dissent section with refs."""
        builder = ICMemoBuilder(
            deliverable_id="memo-dissent-001",
            tenant_id="tenant-001",
            deal_id="deal-dissent-001",
            deal_name="Dissent Corp",
            generated_at="2026-01-11T12:00:00Z",
        )

        builder.add_executive_summary_fact(
            text="Company has strong growth.",
            claim_refs=["claim-001"],
        )
        builder.add_company_overview_fact(
            text="Founded in 2020.",
            claim_refs=["claim-002"],
        )
        builder.add_market_analysis_fact(
            text="Large TAM.",
            claim_refs=["claim-003"],
        )
        builder.add_financials_fact(
            text="Positive unit economics.",
            claim_refs=["claim-004"],
        )
        builder.add_team_assessment_fact(
            text="Experienced team.",
            claim_refs=["claim-005"],
        )
        builder.add_risks_fact(
            text="Market risk.",
            claim_refs=["claim-006"],
        )
        builder.add_recommendation_fact(
            text="Recommend proceed.",
            claim_refs=["claim-007"],
        )
        builder.add_truth_dashboard_fact(
            text="High verification rate.",
            claim_refs=["claim-008"],
        )

        builder.set_dissent(
            dissent_id="dissent-001",
            agent_role="risk_officer",
            position="Recommend pass due to regulatory uncertainty.",
            rationale="Key regulatory approvals pending in 3 jurisdictions.",
            claim_refs=["claim-100", "claim-101"],
            calc_refs=["calc-100"],
            confidence=0.75,
        )

        memo = builder.build()

        assert memo.dissent_section is not None
        assert memo.dissent_section.dissent_id == "dissent-001"
        assert memo.dissent_section.agent_role == "risk_officer"
        assert memo.dissent_section.claim_refs == ["claim-100", "claim-101"]
        assert memo.dissent_section.calc_refs == ["calc-100"]
        assert memo.dissent_section.confidence == 0.75

    def test_dissent_refs_sorted(self) -> None:
        """Test that dissent claim_refs are sorted lexicographically."""
        builder = ICMemoBuilder(
            deliverable_id="memo-dissent-sort",
            tenant_id="tenant-001",
            deal_id="deal-dissent-sort",
            deal_name="Sort Corp",
            generated_at="2026-01-11T12:00:00Z",
        )

        self._add_minimal_sections(builder)

        builder.set_dissent(
            dissent_id="dissent-sort",
            agent_role="sanad_breaker",
            position="Concerns about source quality.",
            rationale="Multiple claims rely on unverified sources.",
            claim_refs=["claim-zzz", "claim-aaa", "claim-mmm"],
            confidence=0.60,
        )

        memo = builder.build()

        assert memo.dissent_section is not None
        assert memo.dissent_section.claim_refs == [
            "claim-aaa",
            "claim-mmm",
            "claim-zzz",
        ]

    def test_empty_dissent_refs_rejected(self) -> None:
        """Test that empty claim_refs on dissent raises error."""
        builder = ICMemoBuilder(
            deliverable_id="memo-dissent-fail",
            tenant_id="tenant-001",
            deal_id="deal-dissent-fail",
            deal_name="Fail Corp",
            generated_at="2026-01-11T12:00:00Z",
        )

        with pytest.raises(ICMemoBuilderError) as exc_info:
            builder.set_dissent(
                dissent_id="dissent-fail",
                agent_role="contradiction_finder",
                position="Position without evidence.",
                rationale="No claims to back this up.",
                claim_refs=[],
                confidence=0.50,
            )

        assert exc_info.value.code == "DISSENT_MISSING_REFS"

    def test_dissent_included_in_audit_appendix(self) -> None:
        """Test that dissent refs are included in audit appendix."""
        builder = ICMemoBuilder(
            deliverable_id="memo-dissent-audit",
            tenant_id="tenant-001",
            deal_id="deal-dissent-audit",
            deal_name="Audit Corp",
            generated_at="2026-01-11T12:00:00Z",
        )

        self._add_minimal_sections(builder)

        builder.set_dissent(
            dissent_id="dissent-audit",
            agent_role="risk_officer",
            position="Risk concerns.",
            rationale="Detailed rationale.",
            claim_refs=["claim-dissent-001"],
            calc_refs=["calc-dissent-001"],
            confidence=0.65,
        )

        memo = builder.build()

        ref_ids = [e.ref_id for e in memo.audit_appendix.entries]
        assert "claim-dissent-001" in ref_ids
        assert "calc-dissent-001" in ref_ids

    def _add_minimal_sections(self, builder: ICMemoBuilder) -> None:
        """Add minimal required sections for a valid memo."""
        builder.add_executive_summary_fact(text="Summary.", claim_refs=["claim-001"])
        builder.add_company_overview_fact(text="Overview.", claim_refs=["claim-002"])
        builder.add_market_analysis_fact(text="Market.", claim_refs=["claim-003"])
        builder.add_financials_fact(text="Financials.", claim_refs=["claim-004"])
        builder.add_team_assessment_fact(text="Team.", claim_refs=["claim-005"])
        builder.add_risks_fact(text="Risks.", claim_refs=["claim-006"])
        builder.add_recommendation_fact(text="Recommendation.", claim_refs=["claim-007"])
        builder.add_truth_dashboard_fact(text="Dashboard.", claim_refs=["claim-008"])


class TestICMemoValidation:
    """Tests for IC Memo validation."""

    def test_valid_memo_passes_validation(self) -> None:
        """Test that a properly referenced memo passes validation."""
        builder = ICMemoBuilder(
            deliverable_id="memo-valid",
            tenant_id="tenant-001",
            deal_id="deal-valid",
            deal_name="Valid Corp",
            generated_at="2026-01-11T12:00:00Z",
        )

        builder.add_executive_summary_fact(text="Valid summary.", claim_refs=["claim-001"])
        builder.add_company_overview_fact(text="Valid overview.", claim_refs=["claim-002"])
        builder.add_market_analysis_fact(text="Valid market.", claim_refs=["claim-003"])
        builder.add_financials_fact(text="Valid financials.", claim_refs=["claim-004"])
        builder.add_team_assessment_fact(text="Valid team.", claim_refs=["claim-005"])
        builder.add_risks_fact(text="Valid risks.", claim_refs=["claim-006"])
        builder.add_recommendation_fact(text="Valid recommendation.", claim_refs=["claim-007"])
        builder.add_truth_dashboard_fact(text="Valid dashboard.", claim_refs=["claim-008"])

        memo = builder.build()

        result = validate_deliverable_no_free_facts(memo, raise_on_failure=False)
        assert result.passed is True

    def test_missing_refs_fails_validation(self) -> None:
        """Test that missing refs on factual sections fail validation."""
        builder = ICMemoBuilder(
            deliverable_id="memo-invalid",
            tenant_id="tenant-001",
            deal_id="deal-invalid",
            deal_name="Invalid Corp",
            generated_at="2026-01-11T12:00:00Z",
        )

        builder.add_executive_summary_fact(
            text="Revenue is $10M.",
            claim_refs=[],
        )
        builder.add_company_overview_fact(text="Overview.", claim_refs=["claim-002"])
        builder.add_market_analysis_fact(text="Market.", claim_refs=["claim-003"])
        builder.add_financials_fact(text="Financials.", claim_refs=["claim-004"])
        builder.add_team_assessment_fact(text="Team.", claim_refs=["claim-005"])
        builder.add_risks_fact(text="Risks.", claim_refs=["claim-006"])
        builder.add_recommendation_fact(text="Recommendation.", claim_refs=["claim-007"])
        builder.add_truth_dashboard_fact(text="Dashboard.", claim_refs=["claim-008"])

        memo = builder.build()

        with pytest.raises(DeliverableValidationError):
            validate_deliverable_no_free_facts(memo)


class TestBuildICMemoConvenience:
    """Tests for build_ic_memo convenience function."""

    def test_convenience_function_creates_valid_memo(self) -> None:
        """Test that the convenience function creates a valid memo."""
        memo = build_ic_memo(
            deliverable_id="memo-conv-001",
            tenant_id="tenant-001",
            deal_id="deal-conv-001",
            deal_name="Convenience Corp",
            generated_at="2026-01-11T12:00:00Z",
            executive_summary_facts=[
                {"text": "Summary fact.", "claim_refs": ["claim-001"]},
            ],
            company_facts=[
                {"text": "Company fact.", "claim_refs": ["claim-002"]},
            ],
            market_facts=[
                {"text": "Market fact.", "claim_refs": ["claim-003"]},
            ],
            financial_facts=[
                {"text": "Financial fact.", "claim_refs": ["claim-004"]},
            ],
            team_facts=[
                {"text": "Team fact.", "claim_refs": ["claim-005"]},
            ],
            risk_facts=[
                {"text": "Risk fact.", "claim_refs": ["claim-006"]},
            ],
            recommendation_facts=[
                {"text": "Recommendation.", "claim_refs": ["claim-007"]},
            ],
            truth_dashboard_facts=[
                {"text": "Dashboard fact.", "claim_refs": ["claim-008"]},
            ],
            sanad_grade_distribution={"A": 10, "B": 5, "C": 2, "D": 1},
        )

        assert isinstance(memo, ICMemo)
        assert memo.sanad_grade_distribution == {"A": 10, "B": 5, "C": 2, "D": 1}

        result = validate_deliverable_no_free_facts(memo, raise_on_failure=False)
        assert result.passed is True

    def test_convenience_function_with_dissent(self) -> None:
        """Test convenience function with dissent section."""
        memo = build_ic_memo(
            deliverable_id="memo-conv-dissent",
            tenant_id="tenant-001",
            deal_id="deal-conv-dissent",
            deal_name="Dissent Convenience Corp",
            generated_at="2026-01-11T12:00:00Z",
            executive_summary_facts=[{"text": "Summary.", "claim_refs": ["claim-001"]}],
            company_facts=[{"text": "Company.", "claim_refs": ["claim-002"]}],
            market_facts=[{"text": "Market.", "claim_refs": ["claim-003"]}],
            financial_facts=[{"text": "Financial.", "claim_refs": ["claim-004"]}],
            team_facts=[{"text": "Team.", "claim_refs": ["claim-005"]}],
            risk_facts=[{"text": "Risk.", "claim_refs": ["claim-006"]}],
            recommendation_facts=[{"text": "Recommend.", "claim_refs": ["claim-007"]}],
            truth_dashboard_facts=[{"text": "Dashboard.", "claim_refs": ["claim-008"]}],
            dissent={
                "dissent_id": "dissent-conv",
                "agent_role": "risk_officer",
                "position": "Dissenting position.",
                "rationale": "Dissenting rationale.",
                "claim_refs": ["claim-dissent"],
                "confidence": 0.70,
            },
        )

        assert memo.dissent_section is not None
        assert memo.dissent_section.claim_refs == ["claim-dissent"]
