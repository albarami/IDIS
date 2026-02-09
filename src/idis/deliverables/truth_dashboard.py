"""Truth Dashboard Builder — v6.3 Phase 10

Builds claim-level truth matrix with evidence linking.

Trust invariants:
- Every truth row grounded to claim_id/calc_id
- Deterministic row ordering by (dimension, assertion)
- Audit appendix with all unique refs (stable ordering)
- No randomness in generation paths
"""

from __future__ import annotations

from idis.models.deliverables import (
    AuditAppendix,
    AuditAppendixEntry,
    DeliverableFact,
    DeliverableSection,
    RefType,
    TruthDashboard,
    TruthRow,
)


class TruthDashboardBuilderError(Exception):
    """Error during Truth Dashboard building."""

    def __init__(self, message: str, code: str = "BUILDER_ERROR") -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class TruthDashboardBuilder:
    """Builder for Truth Dashboard deliverables.

    Usage:
        builder = TruthDashboardBuilder(
            deliverable_id="td-001",
            tenant_id="tenant-001",
            deal_id="deal-001",
            deal_name="Acme Corp Series A",
            generated_at="2026-01-11T12:00:00Z",
        )
        builder.add_row(
            dimension="TEAM_QUALITY", assertion="...",
            verdict="CONFIRMED", claim_refs=["c1"],
        )
        dashboard = builder.build()
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
            deliverable_id: Unique deliverable identifier.
            tenant_id: Tenant scope.
            deal_id: Deal this dashboard is for.
            deal_name: Human-readable deal name.
            generated_at: ISO timestamp (must be passed in, not generated).
        """
        self._deliverable_id = deliverable_id
        self._tenant_id = tenant_id
        self._deal_id = deal_id
        self._deal_name = deal_name
        self._generated_at = generated_at

        self._rows: list[TruthRow] = []
        self._summary_facts: list[DeliverableFact] = []

        self._all_claim_refs: set[str] = set()
        self._all_calc_refs: set[str] = set()

    def add_row(
        self,
        dimension: str,
        assertion: str,
        verdict: str,
        claim_refs: list[str] | None = None,
        calc_refs: list[str] | None = None,
        sanad_grade: str | None = None,
        confidence: float | None = None,
    ) -> TruthDashboardBuilder:
        """Add a truth row to the dashboard.

        Args:
            dimension: Scorecard dimension this row belongs to.
            assertion: The truth assertion text.
            verdict: Verdict (CONFIRMED, DISPUTED, UNVERIFIED, REFUTED).
            claim_refs: Supporting claim_ids.
            calc_refs: Supporting calc_ids.
            sanad_grade: Grade of the primary supporting claim.
            confidence: Confidence in this assertion (0.0-1.0).
        """
        claim_refs = claim_refs or []
        calc_refs = calc_refs or []

        self._all_claim_refs.update(claim_refs)
        self._all_calc_refs.update(calc_refs)

        row = TruthRow(
            dimension=dimension,
            assertion=assertion,
            verdict=verdict,
            claim_refs=claim_refs,
            calc_refs=calc_refs,
            sanad_grade=sanad_grade,
            confidence=confidence,
        )
        self._rows.append(row)
        return self

    def add_summary_fact(
        self,
        text: str,
        claim_refs: list[str] | None = None,
        calc_refs: list[str] | None = None,
        is_subjective: bool = False,
    ) -> TruthDashboardBuilder:
        """Add a fact to the summary section.

        Args:
            text: The factual statement text.
            claim_refs: Referenced claim_ids.
            calc_refs: Referenced calc_ids.
            is_subjective: If True, no refs required.
        """
        claim_refs = claim_refs or []
        calc_refs = calc_refs or []

        self._all_claim_refs.update(claim_refs)
        self._all_calc_refs.update(calc_refs)

        fact = DeliverableFact(
            text=text,
            claim_refs=claim_refs,
            calc_refs=calc_refs,
            is_factual=not is_subjective,
            is_subjective=is_subjective,
        )
        self._summary_facts.append(fact)
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

    def build(self) -> TruthDashboard:
        """Build the Truth Dashboard.

        Returns:
            TruthDashboard with deterministically ordered rows and audit appendix.

        Note: This does NOT validate No-Free-Facts. Use the deliverable
        validator before export to enforce trust invariants.
        """
        sorted_rows = sorted(self._rows, key=lambda r: (r.dimension, r.assertion))

        summary_section = DeliverableSection(
            section_id=f"{self._deliverable_id}-summary",
            title="Truth Dashboard Summary",
            facts=self._summary_facts,
        )

        audit_appendix = self._build_audit_appendix()

        return TruthDashboard(
            deliverable_id=self._deliverable_id,
            tenant_id=self._tenant_id,
            deal_id=self._deal_id,
            deal_name=self._deal_name,
            rows=sorted_rows,
            summary_section=summary_section,
            audit_appendix=audit_appendix,
            generated_at=self._generated_at,
        )
