"""Tests for QA Brief Builder — v6.3 Phase 10

Tests:
- Builder creates valid QABrief with deterministic item ordering
- Items sorted by (topic, agent_type, question) for determinism
- Audit appendix includes all refs from items
- NFF: summary section validation
- Valid QA brief passes validation
"""

from __future__ import annotations

from idis.deliverables.qa_brief import QABriefBuilder
from idis.models.deliverables import QABrief, QAItem
from idis.validators.deliverable import (
    DeliverableValidator,
    validate_deliverable_no_free_facts,
)


class TestQABriefBuilder:
    """Tests for QABriefBuilder."""

    def test_builder_creates_valid_brief(self) -> None:
        """Test that builder creates a valid QABrief with all fields."""
        builder = QABriefBuilder(
            deliverable_id="qa-001",
            tenant_id="tenant-001",
            deal_id="deal-001",
            deal_name="Acme Corp Series A",
            generated_at="2026-01-11T12:00:00Z",
        )

        builder.add_item(
            agent_type="financial_agent",
            topic="Revenue",
            question="What is your MRR breakdown by customer segment?",
            claim_refs=["claim-001"],
        )
        builder.add_item(
            agent_type="market_agent",
            topic="Competition",
            question="Who are your top 3 competitors?",
            claim_refs=["claim-002"],
        )
        builder.add_summary_fact(
            text="2 questions from 2 agents.",
            is_subjective=True,
        )

        brief = builder.build()

        assert isinstance(brief, QABrief)
        assert brief.deliverable_type == "QA_BRIEF"
        assert brief.deliverable_id == "qa-001"
        assert brief.tenant_id == "tenant-001"
        assert brief.deal_id == "deal-001"
        assert brief.deal_name == "Acme Corp Series A"
        assert len(brief.items) == 2
        assert brief.summary_section is not None
        assert brief.audit_appendix is not None

    def test_items_sorted_by_topic_agent_question(self) -> None:
        """Test that items are deterministically ordered by (topic, agent_type, question)."""
        builder = QABriefBuilder(
            deliverable_id="qa-002",
            tenant_id="t",
            deal_id="d",
            deal_name="Test",
            generated_at="2026-01-11T12:00:00Z",
        )

        builder.add_item(
            agent_type="team_agent",
            topic="Team",
            question="Z question",
            claim_refs=["c1"],
        )
        builder.add_item(
            agent_type="financial_agent",
            topic="Revenue",
            question="A question",
            claim_refs=["c2"],
        )
        builder.add_item(
            agent_type="financial_agent",
            topic="Revenue",
            question="B question",
            claim_refs=["c3"],
        )
        builder.add_summary_fact(text="summary", is_subjective=True)

        brief = builder.build()

        assert len(brief.items) == 3
        assert brief.items[0].topic == "Revenue"
        assert brief.items[0].question == "A question"
        assert brief.items[1].topic == "Revenue"
        assert brief.items[1].question == "B question"
        assert brief.items[2].topic == "Team"

    def test_determinism_multiple_builds_identical(self) -> None:
        """Test that building twice produces identical output."""

        def make_brief() -> QABrief:
            builder = QABriefBuilder(
                deliverable_id="qa-det",
                tenant_id="t",
                deal_id="d",
                deal_name="Det Test",
                generated_at="2026-01-11T12:00:00Z",
            )
            builder.add_item(
                agent_type="financial_agent",
                topic="Revenue",
                question="What is MRR?",
                claim_refs=["c2", "c1"],
                calc_refs=["k2", "k1"],
            )
            builder.add_summary_fact(text="summary", is_subjective=True)
            return builder.build()

        b1 = make_brief()
        b2 = make_brief()

        assert b1 == b2
        assert b1.items[0].claim_refs == ["c1", "c2"]
        assert b1.items[0].calc_refs == ["k1", "k2"]

    def test_audit_appendix_includes_all_refs(self) -> None:
        """Test that audit appendix contains all unique refs from items."""
        builder = QABriefBuilder(
            deliverable_id="qa-audit",
            tenant_id="t",
            deal_id="d",
            deal_name="Test",
            generated_at="2026-01-11T12:00:00Z",
        )

        builder.add_item(
            agent_type="financial_agent",
            topic="Revenue",
            question="Q1?",
            claim_refs=["claim-001", "claim-002"],
            calc_refs=["calc-001"],
        )
        builder.add_item(
            agent_type="market_agent",
            topic="Market",
            question="Q2?",
            claim_refs=["claim-002", "claim-003"],
        )
        builder.add_summary_fact(text="summary", is_subjective=True)

        brief = builder.build()

        ref_ids = [e.ref_id for e in brief.audit_appendix.entries]
        assert "claim-001" in ref_ids
        assert "claim-002" in ref_ids
        assert "claim-003" in ref_ids
        assert "calc-001" in ref_ids

    def test_qa_item_refs_sorted(self) -> None:
        """Test that QAItem sorts refs lexicographically."""
        item = QAItem(
            agent_type="financial_agent",
            topic="Revenue",
            question="What is MRR?",
            claim_refs=["c3", "c1", "c2"],
            calc_refs=["k2", "k1"],
        )
        assert item.claim_refs == ["c1", "c2", "c3"]
        assert item.calc_refs == ["k1", "k2"]


class TestQABriefNFF:
    """Tests for QA Brief No-Free-Facts validation."""

    def test_valid_qa_brief_passes_nff(self) -> None:
        """Test that a valid QA brief passes NFF validation."""
        builder = QABriefBuilder(
            deliverable_id="qa-valid",
            tenant_id="t",
            deal_id="d",
            deal_name="Test",
            generated_at="2026-01-11T12:00:00Z",
        )

        builder.add_item(
            agent_type="financial_agent",
            topic="Revenue",
            question="What is MRR?",
            claim_refs=["claim-001"],
        )
        builder.add_summary_fact(text="summary", is_subjective=True)

        brief = builder.build()

        result = validate_deliverable_no_free_facts(brief, raise_on_failure=False)
        assert result.passed

    def test_validator_dispatch_routes_qa_brief(self) -> None:
        """Test that validator dispatch recognizes QA_BRIEF type."""
        validator = DeliverableValidator()
        builder = QABriefBuilder(
            deliverable_id="qa-dispatch",
            tenant_id="t",
            deal_id="d",
            deal_name="Test",
            generated_at="2026-01-11T12:00:00Z",
        )
        builder.add_item(
            agent_type="financial_agent",
            topic="Revenue",
            question="Q?",
            claim_refs=["c1"],
        )
        builder.add_summary_fact(text="summary", is_subjective=True)
        brief = builder.build()

        result = validator.validate(brief)
        assert result.passed

    def test_qa_brief_with_empty_items_passes(self) -> None:
        """Test that QA brief with no items but valid summary passes."""
        builder = QABriefBuilder(
            deliverable_id="qa-empty",
            tenant_id="t",
            deal_id="d",
            deal_name="Test",
            generated_at="2026-01-11T12:00:00Z",
        )
        builder.add_summary_fact(text="No questions.", is_subjective=True)

        brief = builder.build()

        result = validate_deliverable_no_free_facts(brief, raise_on_failure=False)
        assert result.passed
