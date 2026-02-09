"""QA Brief Builder — v6.3 Phase 10

Builds founder questions brief with evidence linking.

Trust invariants:
- Questions grounded to claim_id/calc_id that prompted them
- Deterministic ordering by (topic, agent_type, question)
- Audit appendix with all unique refs (stable ordering)
- No randomness in generation paths
"""

from __future__ import annotations

from idis.models.deliverables import (
    AuditAppendix,
    AuditAppendixEntry,
    DeliverableFact,
    DeliverableSection,
    QABrief,
    QAItem,
    RefType,
)


class QABriefBuilderError(Exception):
    """Error during QA Brief building."""

    def __init__(self, message: str, code: str = "BUILDER_ERROR") -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class QABriefBuilder:
    """Builder for QA Brief deliverables.

    Usage:
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
            question="What is your MRR breakdown?",
            claim_refs=["claim-001"],
        )
        brief = builder.build()
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
            deal_id: Deal this brief is for.
            deal_name: Human-readable deal name.
            generated_at: ISO timestamp (must be passed in, not generated).
        """
        self._deliverable_id = deliverable_id
        self._tenant_id = tenant_id
        self._deal_id = deal_id
        self._deal_name = deal_name
        self._generated_at = generated_at

        self._items: list[QAItem] = []
        self._summary_facts: list[DeliverableFact] = []

        self._all_claim_refs: set[str] = set()
        self._all_calc_refs: set[str] = set()

    def add_item(
        self,
        agent_type: str,
        topic: str,
        question: str,
        rationale: str = "",
        claim_refs: list[str] | None = None,
        calc_refs: list[str] | None = None,
        priority: str = "MEDIUM",
    ) -> QABriefBuilder:
        """Add a QA item to the brief.

        Args:
            agent_type: Agent that raised this question.
            topic: Topic/category for grouping.
            question: The question for the founder.
            rationale: Why this question matters.
            claim_refs: Claims that prompted this question.
            calc_refs: Calcs that prompted this question.
            priority: Priority (HIGH, MEDIUM, LOW).
        """
        claim_refs = claim_refs or []
        calc_refs = calc_refs or []

        self._all_claim_refs.update(claim_refs)
        self._all_calc_refs.update(calc_refs)

        item = QAItem(
            agent_type=agent_type,
            topic=topic,
            question=question,
            rationale=rationale,
            claim_refs=claim_refs,
            calc_refs=calc_refs,
            priority=priority,
        )
        self._items.append(item)
        return self

    def add_summary_fact(
        self,
        text: str,
        claim_refs: list[str] | None = None,
        calc_refs: list[str] | None = None,
        is_subjective: bool = False,
    ) -> QABriefBuilder:
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

    def build(self) -> QABrief:
        """Build the QA Brief.

        Returns:
            QABrief with deterministically ordered items and audit appendix.

        Note: This does NOT validate No-Free-Facts. Use the deliverable
        validator before export to enforce trust invariants.
        """
        sorted_items = sorted(self._items, key=lambda i: (i.topic, i.agent_type, i.question))

        summary_section = DeliverableSection(
            section_id=f"{self._deliverable_id}-summary",
            title="QA Brief Summary",
            facts=self._summary_facts,
        )

        audit_appendix = self._build_audit_appendix()

        return QABrief(
            deliverable_id=self._deliverable_id,
            tenant_id=self._tenant_id,
            deal_id=self._deal_id,
            deal_name=self._deal_name,
            items=sorted_items,
            summary_section=summary_section,
            audit_appendix=audit_appendix,
            generated_at=self._generated_at,
        )
