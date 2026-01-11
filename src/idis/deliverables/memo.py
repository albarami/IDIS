"""IC Memo Builder — v6.3 Phase 6.1

Builds full investment committee memo with evidence linking.

Trust invariants:
- Evidence-linked sections
- Includes Truth Dashboard summary + Sanad grade distribution
- Dissent section when stable dissent exists
- Audit appendix for compliance
- No randomness in generation paths
"""

from __future__ import annotations

from typing import Any

from idis.models.deliverables import (
    AuditAppendix,
    AuditAppendixEntry,
    DeliverableFact,
    DeliverableSection,
    DissentSection,
    ICMemo,
    RefType,
)


class ICMemoBuilderError(Exception):
    """Error during IC Memo building."""

    def __init__(self, message: str, code: str = "BUILDER_ERROR") -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ICMemoBuilder:
    """Builder for IC Memo deliverables.

    Usage:
        builder = ICMemoBuilder(
            deliverable_id="memo-001",
            tenant_id="tenant-001",
            deal_id="deal-001",
            deal_name="Acme Corp Series A",
            generated_at="2026-01-11T12:00:00Z",
        )
        builder.add_executive_summary_fact(text="...", claim_refs=["claim-001"])
        builder.set_dissent(dissent_section=DissentSection(...))
        memo = builder.build()
    """

    def __init__(
        self,
        deliverable_id: str,
        tenant_id: str,
        deal_id: str,
        deal_name: str,
        generated_at: str,
    ) -> None:
        """Initialize the builder.

        Args:
            deliverable_id: Unique deliverable identifier
            tenant_id: Tenant scope
            deal_id: Deal this memo is for
            deal_name: Human-readable deal name
            generated_at: ISO timestamp (must be passed in, not generated)
        """
        self._deliverable_id = deliverable_id
        self._tenant_id = tenant_id
        self._deal_id = deal_id
        self._deal_name = deal_name
        self._generated_at = generated_at

        self._executive_summary_facts: list[DeliverableFact] = []
        self._company_overview_facts: list[DeliverableFact] = []
        self._market_analysis_facts: list[DeliverableFact] = []
        self._financials_facts: list[DeliverableFact] = []
        self._team_assessment_facts: list[DeliverableFact] = []
        self._risks_facts: list[DeliverableFact] = []
        self._recommendation_facts: list[DeliverableFact] = []
        self._truth_dashboard_facts: list[DeliverableFact] = []
        self._scenario_facts: list[DeliverableFact] = []

        self._sanad_grade_distribution: dict[str, int] = {"A": 0, "B": 0, "C": 0, "D": 0}
        self._dissent_section: DissentSection | None = None
        self._additional_sections: list[DeliverableSection] = []

        self._all_claim_refs: set[str] = set()
        self._all_calc_refs: set[str] = set()

    def _create_fact(
        self,
        text: str,
        claim_refs: list[str] | None = None,
        calc_refs: list[str] | None = None,
        is_factual: bool = True,
        is_subjective: bool = False,
        sanad_grade: str | None = None,
        confidence: float | None = None,
    ) -> DeliverableFact:
        """Create a fact and track refs."""
        claim_refs = claim_refs or []
        calc_refs = calc_refs or []

        self._all_claim_refs.update(claim_refs)
        self._all_calc_refs.update(calc_refs)

        return DeliverableFact(
            text=text,
            claim_refs=claim_refs,
            calc_refs=calc_refs,
            is_factual=is_factual,
            is_subjective=is_subjective,
            sanad_grade=sanad_grade,
            confidence=confidence,
        )

    def add_executive_summary_fact(
        self,
        text: str,
        claim_refs: list[str] | None = None,
        calc_refs: list[str] | None = None,
        sanad_grade: str | None = None,
        confidence: float | None = None,
    ) -> ICMemoBuilder:
        """Add a fact to the executive summary section."""
        fact = self._create_fact(
            text=text,
            claim_refs=claim_refs,
            calc_refs=calc_refs,
            sanad_grade=sanad_grade,
            confidence=confidence,
        )
        self._executive_summary_facts.append(fact)
        return self

    def add_company_overview_fact(
        self,
        text: str,
        claim_refs: list[str] | None = None,
        calc_refs: list[str] | None = None,
        sanad_grade: str | None = None,
        confidence: float | None = None,
    ) -> ICMemoBuilder:
        """Add a fact to the company overview section."""
        fact = self._create_fact(
            text=text,
            claim_refs=claim_refs,
            calc_refs=calc_refs,
            sanad_grade=sanad_grade,
            confidence=confidence,
        )
        self._company_overview_facts.append(fact)
        return self

    def add_market_analysis_fact(
        self,
        text: str,
        claim_refs: list[str] | None = None,
        calc_refs: list[str] | None = None,
        sanad_grade: str | None = None,
        confidence: float | None = None,
    ) -> ICMemoBuilder:
        """Add a fact to the market analysis section."""
        fact = self._create_fact(
            text=text,
            claim_refs=claim_refs,
            calc_refs=calc_refs,
            sanad_grade=sanad_grade,
            confidence=confidence,
        )
        self._market_analysis_facts.append(fact)
        return self

    def add_financials_fact(
        self,
        text: str,
        claim_refs: list[str] | None = None,
        calc_refs: list[str] | None = None,
        sanad_grade: str | None = None,
        confidence: float | None = None,
    ) -> ICMemoBuilder:
        """Add a fact to the financials section."""
        fact = self._create_fact(
            text=text,
            claim_refs=claim_refs,
            calc_refs=calc_refs,
            sanad_grade=sanad_grade,
            confidence=confidence,
        )
        self._financials_facts.append(fact)
        return self

    def add_team_assessment_fact(
        self,
        text: str,
        claim_refs: list[str] | None = None,
        calc_refs: list[str] | None = None,
        sanad_grade: str | None = None,
        confidence: float | None = None,
    ) -> ICMemoBuilder:
        """Add a fact to the team assessment section."""
        fact = self._create_fact(
            text=text,
            claim_refs=claim_refs,
            calc_refs=calc_refs,
            sanad_grade=sanad_grade,
            confidence=confidence,
        )
        self._team_assessment_facts.append(fact)
        return self

    def add_risks_fact(
        self,
        text: str,
        claim_refs: list[str] | None = None,
        calc_refs: list[str] | None = None,
        sanad_grade: str | None = None,
        confidence: float | None = None,
    ) -> ICMemoBuilder:
        """Add a fact to the risks and mitigations section."""
        fact = self._create_fact(
            text=text,
            claim_refs=claim_refs,
            calc_refs=calc_refs,
            sanad_grade=sanad_grade,
            confidence=confidence,
        )
        self._risks_facts.append(fact)
        return self

    def add_recommendation_fact(
        self,
        text: str,
        claim_refs: list[str] | None = None,
        calc_refs: list[str] | None = None,
        sanad_grade: str | None = None,
        confidence: float | None = None,
        is_subjective: bool = False,
    ) -> ICMemoBuilder:
        """Add a fact to the recommendation section."""
        fact = self._create_fact(
            text=text,
            claim_refs=claim_refs,
            calc_refs=calc_refs,
            sanad_grade=sanad_grade,
            confidence=confidence,
            is_subjective=is_subjective,
        )
        self._recommendation_facts.append(fact)
        return self

    def add_truth_dashboard_fact(
        self,
        text: str,
        claim_refs: list[str] | None = None,
        calc_refs: list[str] | None = None,
        sanad_grade: str | None = None,
    ) -> ICMemoBuilder:
        """Add a fact to the truth dashboard summary section."""
        fact = self._create_fact(
            text=text,
            claim_refs=claim_refs,
            calc_refs=calc_refs,
            sanad_grade=sanad_grade,
        )
        self._truth_dashboard_facts.append(fact)
        return self

    def add_scenario_fact(
        self,
        text: str,
        claim_refs: list[str] | None = None,
        calc_refs: list[str] | None = None,
    ) -> ICMemoBuilder:
        """Add a fact to the scenario analysis section."""
        fact = self._create_fact(
            text=text,
            claim_refs=claim_refs,
            calc_refs=calc_refs,
        )
        self._scenario_facts.append(fact)
        return self

    def set_sanad_grade_distribution(
        self,
        distribution: dict[str, int],
    ) -> ICMemoBuilder:
        """Set the Sanad grade distribution summary."""
        self._sanad_grade_distribution = {
            "A": distribution.get("A", 0),
            "B": distribution.get("B", 0),
            "C": distribution.get("C", 0),
            "D": distribution.get("D", 0),
        }
        return self

    def set_dissent(
        self,
        dissent_id: str,
        agent_role: str,
        position: str,
        rationale: str,
        claim_refs: list[str],
        calc_refs: list[str] | None = None,
        confidence: float = 0.5,
    ) -> ICMemoBuilder:
        """Set the dissent section (when stable dissent exists).

        Per v6.3: if debate state indicates stable dissent, include it as a
        structured section with explicit refs. Empty refs are NOT allowed.
        """
        if not claim_refs:
            raise ICMemoBuilderError(
                message="Dissent section must have non-empty claim_refs",
                code="DISSENT_MISSING_REFS",
            )

        self._all_claim_refs.update(claim_refs)
        calc_refs = calc_refs or []
        self._all_calc_refs.update(calc_refs)

        self._dissent_section = DissentSection(
            dissent_id=dissent_id,
            agent_role=agent_role,
            position=position,
            rationale=rationale,
            claim_refs=claim_refs,
            calc_refs=calc_refs,
            confidence=confidence,
        )
        return self

    def add_section(
        self,
        section_id: str,
        title: str,
        facts: list[dict[str, Any]],
        is_subjective: bool = False,
    ) -> ICMemoBuilder:
        """Add a custom section."""
        section_facts = []
        for f in facts:
            fact = self._create_fact(
                text=f.get("text", ""),
                claim_refs=f.get("claim_refs"),
                calc_refs=f.get("calc_refs"),
                is_factual=f.get("is_factual", True),
                is_subjective=f.get("is_subjective", is_subjective),
                sanad_grade=f.get("sanad_grade"),
                confidence=f.get("confidence"),
            )
            section_facts.append(fact)

        section = DeliverableSection(
            section_id=section_id,
            title=title,
            facts=section_facts,
            is_subjective=is_subjective,
        )
        self._additional_sections.append(section)
        return self

    def _build_section(
        self,
        section_id: str,
        title: str,
        facts: list[DeliverableFact],
        is_subjective: bool = False,
    ) -> DeliverableSection:
        """Build a section from facts."""
        return DeliverableSection(
            section_id=section_id,
            title=title,
            facts=facts,
            is_subjective=is_subjective,
        )

    def _build_audit_appendix(self) -> AuditAppendix:
        """Build the audit appendix from all collected refs."""
        entries: list[AuditAppendixEntry] = []

        for claim_id in sorted(self._all_claim_refs):
            entries.append(
                AuditAppendixEntry(
                    ref_id=claim_id,
                    ref_type=RefType.CLAIM,
                )
            )

        for calc_id in sorted(self._all_calc_refs):
            entries.append(
                AuditAppendixEntry(
                    ref_id=calc_id,
                    ref_type=RefType.CALC,
                )
            )

        return AuditAppendix(
            entries=entries,
            generated_at=self._generated_at,
            deal_id=self._deal_id,
            tenant_id=self._tenant_id,
        )

    def build(self) -> ICMemo:
        """Build the IC Memo.

        Returns:
            ICMemo with all sections and audit appendix.

        Note: This does NOT validate No-Free-Facts. Use the deliverable
        validator before export to enforce trust invariants.
        """
        executive_summary = self._build_section(
            f"{self._deliverable_id}-exec-summary",
            "Executive Summary",
            self._executive_summary_facts,
        )

        company_overview = self._build_section(
            f"{self._deliverable_id}-company",
            "Company Overview",
            self._company_overview_facts,
        )

        market_analysis = self._build_section(
            f"{self._deliverable_id}-market",
            "Market Analysis",
            self._market_analysis_facts,
        )

        financials = self._build_section(
            f"{self._deliverable_id}-financials",
            "Financial Analysis",
            self._financials_facts,
        )

        team_assessment = self._build_section(
            f"{self._deliverable_id}-team",
            "Team Assessment",
            self._team_assessment_facts,
        )

        risks = self._build_section(
            f"{self._deliverable_id}-risks",
            "Risks and Mitigations",
            self._risks_facts,
        )

        recommendation = self._build_section(
            f"{self._deliverable_id}-recommendation",
            "Investment Recommendation",
            self._recommendation_facts,
        )

        truth_dashboard = self._build_section(
            f"{self._deliverable_id}-truth",
            "Truth Dashboard Summary",
            self._truth_dashboard_facts,
        )

        scenario_analysis = None
        if self._scenario_facts:
            scenario_analysis = self._build_section(
                f"{self._deliverable_id}-scenario",
                "Scenario Analysis",
                self._scenario_facts,
            )

        audit_appendix = self._build_audit_appendix()

        return ICMemo(
            deliverable_id=self._deliverable_id,
            tenant_id=self._tenant_id,
            deal_id=self._deal_id,
            deal_name=self._deal_name,
            executive_summary=executive_summary,
            company_overview=company_overview,
            market_analysis=market_analysis,
            financials=financials,
            team_assessment=team_assessment,
            risks_and_mitigations=risks,
            recommendation=recommendation,
            truth_dashboard_summary=truth_dashboard,
            sanad_grade_distribution=self._sanad_grade_distribution,
            scenario_analysis=scenario_analysis,
            dissent_section=self._dissent_section,
            additional_sections=self._additional_sections,
            audit_appendix=audit_appendix,
            generated_at=self._generated_at,
        )


def build_ic_memo(
    deliverable_id: str,
    tenant_id: str,
    deal_id: str,
    deal_name: str,
    generated_at: str,
    executive_summary_facts: list[dict[str, Any]],
    company_facts: list[dict[str, Any]],
    market_facts: list[dict[str, Any]],
    financial_facts: list[dict[str, Any]],
    team_facts: list[dict[str, Any]],
    risk_facts: list[dict[str, Any]],
    recommendation_facts: list[dict[str, Any]],
    truth_dashboard_facts: list[dict[str, Any]],
    sanad_grade_distribution: dict[str, int] | None = None,
    scenario_facts: list[dict[str, Any]] | None = None,
    dissent: dict[str, Any] | None = None,
) -> ICMemo:
    """Convenience function to build an IC Memo.

    Args:
        deliverable_id: Unique deliverable identifier
        tenant_id: Tenant scope
        deal_id: Deal this memo is for
        deal_name: Human-readable deal name
        generated_at: ISO timestamp (passed in, not generated)
        executive_summary_facts: Facts for executive summary
        company_facts: Facts for company overview
        market_facts: Facts for market analysis
        financial_facts: Facts for financial analysis
        team_facts: Facts for team assessment
        risk_facts: Facts for risks and mitigations
        recommendation_facts: Facts for recommendation
        truth_dashboard_facts: Facts for truth dashboard summary
        sanad_grade_distribution: Optional grade distribution
        scenario_facts: Optional scenario analysis facts
        dissent: Optional dissent section dict

    Returns:
        ICMemo ready for validation and export.
    """
    builder = ICMemoBuilder(
        deliverable_id=deliverable_id,
        tenant_id=tenant_id,
        deal_id=deal_id,
        deal_name=deal_name,
        generated_at=generated_at,
    )

    def add_facts(method: Any, facts: list[dict[str, Any]]) -> None:
        for f in facts:
            method(
                text=f.get("text", ""),
                claim_refs=f.get("claim_refs"),
                calc_refs=f.get("calc_refs"),
                sanad_grade=f.get("sanad_grade"),
                confidence=f.get("confidence"),
            )

    add_facts(builder.add_executive_summary_fact, executive_summary_facts)
    add_facts(builder.add_company_overview_fact, company_facts)
    add_facts(builder.add_market_analysis_fact, market_facts)
    add_facts(builder.add_financials_fact, financial_facts)
    add_facts(builder.add_team_assessment_fact, team_facts)
    add_facts(builder.add_risks_fact, risk_facts)

    for f in recommendation_facts:
        builder.add_recommendation_fact(
            text=f.get("text", ""),
            claim_refs=f.get("claim_refs"),
            calc_refs=f.get("calc_refs"),
            sanad_grade=f.get("sanad_grade"),
            confidence=f.get("confidence"),
            is_subjective=f.get("is_subjective", False),
        )

    for f in truth_dashboard_facts:
        builder.add_truth_dashboard_fact(
            text=f.get("text", ""),
            claim_refs=f.get("claim_refs"),
            calc_refs=f.get("calc_refs"),
            sanad_grade=f.get("sanad_grade"),
        )

    if sanad_grade_distribution:
        builder.set_sanad_grade_distribution(sanad_grade_distribution)

    if scenario_facts:
        for f in scenario_facts:
            builder.add_scenario_fact(
                text=f.get("text", ""),
                claim_refs=f.get("claim_refs"),
                calc_refs=f.get("calc_refs"),
            )

    if dissent:
        builder.set_dissent(
            dissent_id=dissent.get("dissent_id", ""),
            agent_role=dissent.get("agent_role", ""),
            position=dissent.get("position", ""),
            rationale=dissent.get("rationale", ""),
            claim_refs=dissent.get("claim_refs", []),
            calc_refs=dissent.get("calc_refs"),
            confidence=dissent.get("confidence", 0.5),
        )

    return builder.build()
