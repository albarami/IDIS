"""Screening Snapshot Builder — v6.3 Phase 6.1

Builds partner-ready one-pager with evidence linking.

Trust invariants:
- All facts linked to claim_id/calc_id
- Includes top red flags + missing info requests
- Audit appendix with all unique refs (stable ordering)
- No randomness in generation paths
"""

from __future__ import annotations

from typing import Any

from idis.models.deliverables import (
    AuditAppendix,
    AuditAppendixEntry,
    DeliverableFact,
    DeliverableSection,
    RefType,
    ScreeningSnapshot,
)


class ScreeningSnapshotBuilderError(Exception):
    """Error during screening snapshot building."""

    def __init__(self, message: str, code: str = "BUILDER_ERROR") -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ScreeningSnapshotBuilder:
    """Builder for Screening Snapshot deliverables.

    Usage:
        builder = ScreeningSnapshotBuilder(
            deliverable_id="del-001",
            tenant_id="tenant-001",
            deal_id="deal-001",
            deal_name="Acme Corp Series A",
            generated_at="2026-01-11T12:00:00Z",
        )
        builder.add_summary_fact(text="...", claim_refs=["claim-001"])
        builder.add_metric_fact(text="...", claim_refs=["claim-002"], calc_refs=["calc-001"])
        snapshot = builder.build()
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
            deal_id: Deal this snapshot is for
            deal_name: Human-readable deal name
            generated_at: ISO timestamp (must be passed in, not generated)
        """
        self._deliverable_id = deliverable_id
        self._tenant_id = tenant_id
        self._deal_id = deal_id
        self._deal_name = deal_name
        self._generated_at = generated_at

        self._summary_facts: list[DeliverableFact] = []
        self._metric_facts: list[DeliverableFact] = []
        self._red_flag_facts: list[DeliverableFact] = []
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

    def add_summary_fact(
        self,
        text: str,
        claim_refs: list[str] | None = None,
        calc_refs: list[str] | None = None,
        sanad_grade: str | None = None,
        confidence: float | None = None,
    ) -> ScreeningSnapshotBuilder:
        """Add a fact to the summary section."""
        fact = self._create_fact(
            text=text,
            claim_refs=claim_refs,
            calc_refs=calc_refs,
            sanad_grade=sanad_grade,
            confidence=confidence,
        )
        self._summary_facts.append(fact)
        return self

    def add_metric_fact(
        self,
        text: str,
        claim_refs: list[str] | None = None,
        calc_refs: list[str] | None = None,
        sanad_grade: str | None = None,
        confidence: float | None = None,
    ) -> ScreeningSnapshotBuilder:
        """Add a fact to the key metrics section."""
        fact = self._create_fact(
            text=text,
            claim_refs=claim_refs,
            calc_refs=calc_refs,
            sanad_grade=sanad_grade,
            confidence=confidence,
        )
        self._metric_facts.append(fact)
        return self

    def add_red_flag_fact(
        self,
        text: str,
        claim_refs: list[str] | None = None,
        calc_refs: list[str] | None = None,
        sanad_grade: str | None = None,
        confidence: float | None = None,
    ) -> ScreeningSnapshotBuilder:
        """Add a fact to the red flags section."""
        fact = self._create_fact(
            text=text,
            claim_refs=claim_refs,
            calc_refs=calc_refs,
            sanad_grade=sanad_grade,
            confidence=confidence,
        )
        self._red_flag_facts.append(fact)
        return self

    def add_missing_info(
        self,
        text: str,
    ) -> ScreeningSnapshotBuilder:
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
    ) -> ScreeningSnapshotBuilder:
        """Add a custom section.

        Args:
            section_id: Unique section identifier
            title: Section title
            facts: List of fact dicts with keys: text, claim_refs, calc_refs, etc.
            is_subjective: If True, section is subjective (no refs required)
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

    def build(self) -> ScreeningSnapshot:
        """Build the screening snapshot.

        Returns:
            ScreeningSnapshot with all sections and audit appendix.

        Note: This does NOT validate No-Free-Facts. Use the deliverable
        validator before export to enforce trust invariants.
        """
        summary_section = DeliverableSection(
            section_id=f"{self._deliverable_id}-summary",
            title="Executive Summary",
            facts=self._summary_facts,
        )

        metrics_section = DeliverableSection(
            section_id=f"{self._deliverable_id}-metrics",
            title="Key Metrics",
            facts=self._metric_facts,
        )

        red_flags_section = DeliverableSection(
            section_id=f"{self._deliverable_id}-red-flags",
            title="Red Flags & Concerns",
            facts=self._red_flag_facts,
        )

        missing_info_section = DeliverableSection(
            section_id=f"{self._deliverable_id}-missing-info",
            title="Missing Information",
            facts=self._missing_info_facts,
            is_subjective=True,
        )

        audit_appendix = self._build_audit_appendix()

        return ScreeningSnapshot(
            deliverable_id=self._deliverable_id,
            tenant_id=self._tenant_id,
            deal_id=self._deal_id,
            deal_name=self._deal_name,
            summary_section=summary_section,
            key_metrics_section=metrics_section,
            red_flags_section=red_flags_section,
            missing_info_section=missing_info_section,
            additional_sections=self._additional_sections,
            audit_appendix=audit_appendix,
            generated_at=self._generated_at,
        )


def build_screening_snapshot(
    deliverable_id: str,
    tenant_id: str,
    deal_id: str,
    deal_name: str,
    generated_at: str,
    summary_facts: list[dict[str, Any]],
    metric_facts: list[dict[str, Any]],
    red_flag_facts: list[dict[str, Any]] | None = None,
    missing_info: list[str] | None = None,
) -> ScreeningSnapshot:
    """Convenience function to build a screening snapshot.

    Args:
        deliverable_id: Unique deliverable identifier
        tenant_id: Tenant scope
        deal_id: Deal this snapshot is for
        deal_name: Human-readable deal name
        generated_at: ISO timestamp (passed in, not generated)
        summary_facts: List of fact dicts for summary section
        metric_facts: List of fact dicts for metrics section
        red_flag_facts: Optional list of fact dicts for red flags
        missing_info: Optional list of missing info request strings

    Returns:
        ScreeningSnapshot ready for validation and export.
    """
    builder = ScreeningSnapshotBuilder(
        deliverable_id=deliverable_id,
        tenant_id=tenant_id,
        deal_id=deal_id,
        deal_name=deal_name,
        generated_at=generated_at,
    )

    for f in summary_facts:
        builder.add_summary_fact(
            text=f.get("text", ""),
            claim_refs=f.get("claim_refs"),
            calc_refs=f.get("calc_refs"),
            sanad_grade=f.get("sanad_grade"),
            confidence=f.get("confidence"),
        )

    for f in metric_facts:
        builder.add_metric_fact(
            text=f.get("text", ""),
            claim_refs=f.get("claim_refs"),
            calc_refs=f.get("calc_refs"),
            sanad_grade=f.get("sanad_grade"),
            confidence=f.get("confidence"),
        )

    for f in red_flag_facts or []:
        builder.add_red_flag_fact(
            text=f.get("text", ""),
            claim_refs=f.get("claim_refs"),
            calc_refs=f.get("calc_refs"),
            sanad_grade=f.get("sanad_grade"),
            confidence=f.get("confidence"),
        )

    for info in missing_info or []:
        builder.add_missing_info(text=info)

    return builder.build()
