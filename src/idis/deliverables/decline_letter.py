"""Decline Letter Builder — v6.3 Phase 10

Builds evidence-backed decline rationale when scorecard routing=DECLINE.

Trust invariants:
- All decline reasoning grounded to claim_id/calc_id
- Includes key concerns and information gaps
- Audit appendix with all unique refs (stable ordering)
- No randomness in generation paths
"""

from __future__ import annotations

from typing import Any

from idis.models.deliverables import (
    AuditAppendix,
    AuditAppendixEntry,
    DeclineLetter,
    DeliverableFact,
    DeliverableSection,
    RefType,
)


class DeclineLetterBuilderError(Exception):
    """Error during Decline Letter building."""

    def __init__(self, message: str, code: str = "BUILDER_ERROR") -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class DeclineLetterBuilder:
    """Builder for Decline Letter deliverables.

    Usage:
        builder = DeclineLetterBuilder(
            deliverable_id="dl-001",
            tenant_id="tenant-001",
            deal_id="deal-001",
            deal_name="Acme Corp Series A",
            generated_at="2026-01-11T12:00:00Z",
            composite_score=42.5,
            score_band="LOW",
        )
        builder.add_rationale_fact(text="...", claim_refs=["claim-001"])
        builder.add_concern_fact(text="...", claim_refs=["claim-002"])
        letter = builder.build()
    """

    def __init__(
        self,
        deliverable_id: str,
        tenant_id: str,
        deal_id: str,
        deal_name: str,
        generated_at: str,
        composite_score: float,
        score_band: str,
    ) -> None:
        """Initialize the builder.

        Args:
            deliverable_id: Unique deliverable identifier.
            tenant_id: Tenant scope.
            deal_id: Deal this letter is for.
            deal_name: Human-readable deal name.
            generated_at: ISO timestamp (must be passed in, not generated).
            composite_score: Composite score that triggered decline.
            score_band: Score band (expected: LOW).
        """
        self._deliverable_id = deliverable_id
        self._tenant_id = tenant_id
        self._deal_id = deal_id
        self._deal_name = deal_name
        self._generated_at = generated_at
        self._composite_score = composite_score
        self._score_band = score_band

        self._rationale_facts: list[DeliverableFact] = []
        self._concern_facts: list[DeliverableFact] = []
        self._missing_info_facts: list[DeliverableFact] = []
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

    def add_rationale_fact(
        self,
        text: str,
        claim_refs: list[str] | None = None,
        calc_refs: list[str] | None = None,
        sanad_grade: str | None = None,
        confidence: float | None = None,
    ) -> DeclineLetterBuilder:
        """Add a fact to the decline rationale section."""
        fact = self._create_fact(
            text=text,
            claim_refs=claim_refs,
            calc_refs=calc_refs,
            sanad_grade=sanad_grade,
            confidence=confidence,
        )
        self._rationale_facts.append(fact)
        return self

    def add_concern_fact(
        self,
        text: str,
        claim_refs: list[str] | None = None,
        calc_refs: list[str] | None = None,
        sanad_grade: str | None = None,
        confidence: float | None = None,
    ) -> DeclineLetterBuilder:
        """Add a fact to the key concerns section."""
        fact = self._create_fact(
            text=text,
            claim_refs=claim_refs,
            calc_refs=calc_refs,
            sanad_grade=sanad_grade,
            confidence=confidence,
        )
        self._concern_facts.append(fact)
        return self

    def add_missing_info(
        self,
        text: str,
    ) -> DeclineLetterBuilder:
        """Add a missing info request (subjective, no refs required)."""
        fact = self._create_fact(
            text=text,
            is_factual=False,
            is_subjective=True,
        )
        self._missing_info_facts.append(fact)
        return self

    def add_section(
        self,
        section_id: str,
        title: str,
        facts: list[dict[str, Any]],
        is_subjective: bool = False,
    ) -> DeclineLetterBuilder:
        """Add a custom section.

        Args:
            section_id: Unique section identifier.
            title: Section title.
            facts: List of fact dicts with keys: text, claim_refs, calc_refs, etc.
            is_subjective: If True, section is subjective (no refs required).
        """
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

    def build(self) -> DeclineLetter:
        """Build the Decline Letter.

        Returns:
            DeclineLetter with all sections and audit appendix.

        Note: This does NOT validate No-Free-Facts. Use the deliverable
        validator before export to enforce trust invariants.
        """
        rationale_section = DeliverableSection(
            section_id=f"{self._deliverable_id}-rationale",
            title="Decline Rationale",
            facts=self._rationale_facts,
        )

        concerns_section = DeliverableSection(
            section_id=f"{self._deliverable_id}-concerns",
            title="Key Concerns",
            facts=self._concern_facts,
        )

        missing_info_section = DeliverableSection(
            section_id=f"{self._deliverable_id}-missing-info",
            title="Information Gaps",
            facts=self._missing_info_facts,
            is_subjective=True,
        )

        audit_appendix = self._build_audit_appendix()

        return DeclineLetter(
            deliverable_id=self._deliverable_id,
            tenant_id=self._tenant_id,
            deal_id=self._deal_id,
            deal_name=self._deal_name,
            rationale_section=rationale_section,
            key_concerns_section=concerns_section,
            missing_info_section=missing_info_section,
            composite_score=self._composite_score,
            score_band=self._score_band,
            additional_sections=self._additional_sections,
            audit_appendix=audit_appendix,
            generated_at=self._generated_at,
        )
