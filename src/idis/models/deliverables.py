"""IDIS Deliverables Domain Models — v6.3 Phase 6.1

Pydantic models for evidence-linked deliverables generation.

Non-negotiables (Phase 6.1):
- Every DeliverableFact with is_factual=True MUST have non-empty claim_refs
- Stable ordering: claim_refs sorted lexicographically within each fact
- Audit appendix entries sorted by (ref_type, ref_id) for deterministic output
- No randomness: no uuid4/uuid1/random/datetime.now/utcnow in generation paths
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RefType(StrEnum):
    """Type of reference in audit appendix."""

    CLAIM = "claim"
    CALC = "calc"


class DeliverableFact(BaseModel):
    """A single fact in a deliverable with evidence linking.

    Trust invariant: if is_factual=True, claim_refs MUST be non-empty.
    This is enforced by the deliverable validator at export time.
    """

    model_config = ConfigDict(frozen=True)

    text: str = Field(..., description="The factual statement text")
    claim_refs: list[str] = Field(
        default_factory=list,
        description="Referenced claim_ids (sorted lexicographically)",
    )
    calc_refs: list[str] = Field(
        default_factory=list,
        description="Referenced calc_ids (sorted lexicographically)",
    )
    is_factual: bool = Field(
        default=True,
        description="If True, this is a factual assertion requiring refs",
    )
    is_subjective: bool = Field(
        default=False,
        description="If True, No-Free-Facts does not apply",
    )
    sanad_grade: str | None = Field(
        default=None,
        description="Grade of the primary supporting claim (A/B/C/D)",
    )
    confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Confidence in this fact (0.0-1.0)",
    )

    @field_validator("claim_refs", "calc_refs", mode="before")
    @classmethod
    def sort_refs(cls, v: list[str]) -> list[str]:
        """Sort refs lexicographically for stable output."""
        if isinstance(v, list):
            return sorted(v)
        return v


class DeliverableSection(BaseModel):
    """A section in a deliverable containing facts.

    Sections can contain multiple facts, each independently validated.
    """

    model_config = ConfigDict(frozen=True)

    section_id: str = Field(..., description="Unique section identifier")
    title: str = Field(..., description="Section title")
    facts: list[DeliverableFact] = Field(
        default_factory=list,
        description="Facts in this section (each validated independently)",
    )
    narrative: str | None = Field(
        default=None,
        description="Optional narrative summary (must be subjective or ref-backed)",
    )
    is_subjective: bool = Field(
        default=False,
        description="If True, entire section is subjective (no refs required)",
    )


class AuditAppendixEntry(BaseModel):
    """A single entry in the audit appendix for evidence tracing.

    Entries are sorted by (ref_type, ref_id) for stable output.
    """

    model_config = ConfigDict(frozen=True)

    ref_id: str = Field(..., description="Reference ID (claim_id or calc_id)")
    ref_type: RefType = Field(..., description="Type of reference")
    sanad_grade: str | None = Field(
        default=None,
        description="Grade if claim (A/B/C/D)",
    )
    source_summary: str | None = Field(
        default=None,
        description="Brief summary of source document/span",
    )
    reproducibility_hash: str | None = Field(
        default=None,
        description="Hash if calc (for reproducibility verification)",
    )


class AuditAppendix(BaseModel):
    """Audit appendix containing all evidence references.

    All entries are sorted by (ref_type, ref_id) for deterministic output.
    """

    model_config = ConfigDict(frozen=True)

    entries: list[AuditAppendixEntry] = Field(
        default_factory=list,
        description="All unique refs used in the deliverable (sorted)",
    )
    generated_at: str = Field(
        ...,
        description="ISO timestamp when appendix was generated (passed in, not generated)",
    )
    deal_id: str = Field(..., description="Deal this appendix belongs to")
    tenant_id: str = Field(..., description="Tenant scope")

    @field_validator("entries", mode="before")
    @classmethod
    def sort_entries(cls, v: list[AuditAppendixEntry] | list[dict[str, Any]]) -> list[Any]:
        """Sort entries by (ref_type, ref_id) for stable output."""
        if isinstance(v, list):
            return sorted(
                v,
                key=lambda e: (
                    e.ref_type.value
                    if isinstance(e, AuditAppendixEntry)
                    else e.get("ref_type", ""),
                    e.ref_id if isinstance(e, AuditAppendixEntry) else e.get("ref_id", ""),
                ),
            )
        return v


class DissentSection(BaseModel):
    """Dissent section for IC Memo when stable dissent exists.

    Per v6.3: if debate state indicates stable dissent, include it as a
    structured section with explicit refs.
    """

    model_config = ConfigDict(frozen=True)

    dissent_id: str = Field(..., description="Unique dissent identifier")
    agent_role: str = Field(..., description="Role of dissenting agent")
    position: str = Field(..., description="Dissenting position summary")
    rationale: str = Field(..., description="Evidence-backed rationale")
    claim_refs: list[str] = Field(
        default_factory=list,
        description="Claims supporting this dissent (sorted)",
    )
    calc_refs: list[str] = Field(
        default_factory=list,
        description="Calcs supporting this dissent (sorted)",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Dissenting agent's confidence",
    )

    @field_validator("claim_refs", "calc_refs", mode="before")
    @classmethod
    def sort_refs(cls, v: list[str]) -> list[str]:
        """Sort refs lexicographically for stable output."""
        if isinstance(v, list):
            return sorted(v)
        return v


class ScreeningSnapshot(BaseModel):
    """Screening Snapshot deliverable - partner-ready one pager.

    Per v6.3 Phase 6.1:
    - All facts linked to claim_id/calc_id
    - Includes top red flags + missing info requests
    - Audit appendix with all unique refs
    """

    model_config = ConfigDict(frozen=True)

    deliverable_type: Literal["SCREENING_SNAPSHOT"] = "SCREENING_SNAPSHOT"
    deliverable_id: str = Field(..., description="Unique deliverable identifier")
    tenant_id: str = Field(..., description="Tenant scope")
    deal_id: str = Field(..., description="Deal this snapshot is for")
    deal_name: str = Field(..., description="Human-readable deal name")

    summary_section: DeliverableSection = Field(..., description="Executive summary section")
    key_metrics_section: DeliverableSection = Field(
        ..., description="Key financial/operational metrics"
    )
    red_flags_section: DeliverableSection = Field(..., description="Top red flags and concerns")
    missing_info_section: DeliverableSection = Field(
        ..., description="Missing information requests"
    )

    additional_sections: list[DeliverableSection] = Field(
        default_factory=list,
        description="Additional custom sections",
    )

    audit_appendix: AuditAppendix = Field(..., description="Evidence appendix with all refs")

    generated_at: str = Field(..., description="ISO timestamp (passed in, not generated)")


class ICMemo(BaseModel):
    """IC Memo deliverable - full investment committee memo.

    Per v6.3 Phase 6.1:
    - Evidence-linked sections
    - Includes Truth Dashboard summary + Sanad grade distribution
    - Includes scenario table from deterministic engines
    - Dissent section when stable dissent exists
    - Audit appendix for compliance
    """

    model_config = ConfigDict(frozen=True)

    deliverable_type: Literal["IC_MEMO"] = "IC_MEMO"
    deliverable_id: str = Field(..., description="Unique deliverable identifier")
    tenant_id: str = Field(..., description="Tenant scope")
    deal_id: str = Field(..., description="Deal this memo is for")
    deal_name: str = Field(..., description="Human-readable deal name")

    executive_summary: DeliverableSection = Field(..., description="Executive summary")
    company_overview: DeliverableSection = Field(..., description="Company overview section")
    market_analysis: DeliverableSection = Field(..., description="Market analysis section")
    financials: DeliverableSection = Field(..., description="Financial analysis section")
    team_assessment: DeliverableSection = Field(..., description="Team assessment section")
    risks_and_mitigations: DeliverableSection = Field(
        ..., description="Risks and mitigations section"
    )
    recommendation: DeliverableSection = Field(..., description="Investment recommendation section")

    truth_dashboard_summary: DeliverableSection = Field(
        ..., description="Truth Dashboard verdict summary"
    )
    sanad_grade_distribution: dict[str, int] = Field(
        default_factory=dict,
        description="Grade distribution (A/B/C/D -> count)",
    )
    scenario_analysis: DeliverableSection | None = Field(
        default=None,
        description="Scenario table from calc engines",
    )

    dissent_section: DissentSection | None = Field(
        default=None,
        description="Dissent section if stable dissent exists",
    )

    additional_sections: list[DeliverableSection] = Field(
        default_factory=list,
        description="Additional custom sections",
    )

    audit_appendix: AuditAppendix = Field(..., description="Evidence appendix with all refs")

    generated_at: str = Field(..., description="ISO timestamp (passed in, not generated)")


class TruthRow(BaseModel):
    """A single row in the Truth Dashboard.

    Each row represents a claim-level truth assertion with a verdict,
    grounded to evidence via claim_refs/calc_refs.
    """

    model_config = ConfigDict(frozen=True)

    dimension: str = Field(..., description="Scorecard dimension this row belongs to")
    assertion: str = Field(..., description="The truth assertion text")
    verdict: str = Field(..., description="Verdict: CONFIRMED, DISPUTED, UNVERIFIED, REFUTED")
    claim_refs: list[str] = Field(
        default_factory=list,
        description="Supporting claim_ids (sorted lexicographically)",
    )
    calc_refs: list[str] = Field(
        default_factory=list,
        description="Supporting calc_ids (sorted lexicographically)",
    )
    sanad_grade: str | None = Field(
        default=None,
        description="Grade of the primary supporting claim (A/B/C/D)",
    )
    confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Confidence in this assertion (0.0-1.0)",
    )

    @field_validator("claim_refs", "calc_refs", mode="before")
    @classmethod
    def sort_refs(cls, v: list[str]) -> list[str]:
        """Sort refs lexicographically for stable output."""
        if isinstance(v, list):
            return sorted(v)
        return v


class TruthDashboard(BaseModel):
    """Truth Dashboard deliverable — claim-level truth matrix.

    Per v6.3: maps every key assertion to its evidence verdict,
    organized by scorecard dimension. Deterministic row ordering
    by (dimension, assertion) for stable output.
    """

    model_config = ConfigDict(frozen=True)

    deliverable_type: Literal["TRUTH_DASHBOARD"] = "TRUTH_DASHBOARD"
    deliverable_id: str = Field(..., description="Unique deliverable identifier")
    tenant_id: str = Field(..., description="Tenant scope")
    deal_id: str = Field(..., description="Deal this dashboard is for")
    deal_name: str = Field(..., description="Human-readable deal name")

    rows: list[TruthRow] = Field(
        default_factory=list,
        description="Truth rows sorted by (dimension, assertion)",
    )

    summary_section: DeliverableSection = Field(..., description="Dashboard summary section")

    audit_appendix: AuditAppendix = Field(..., description="Evidence appendix with all refs")

    generated_at: str = Field(..., description="ISO timestamp (passed in, not generated)")


class QAItem(BaseModel):
    """A single question-answer item in the QA Brief.

    Questions are extracted from agent reports and grouped by topic.
    Each item is grounded to evidence that prompted the question.
    """

    model_config = ConfigDict(frozen=True)

    agent_type: str = Field(..., description="Agent that raised this question")
    topic: str = Field(..., description="Topic/category for grouping")
    question: str = Field(..., description="The question for the founder")
    rationale: str = Field(
        default="",
        description="Why this question matters (evidence context)",
    )
    claim_refs: list[str] = Field(
        default_factory=list,
        description="Claims that prompted this question (sorted)",
    )
    calc_refs: list[str] = Field(
        default_factory=list,
        description="Calcs that prompted this question (sorted)",
    )
    priority: str = Field(
        default="MEDIUM",
        description="Priority: HIGH, MEDIUM, LOW",
    )

    @field_validator("claim_refs", "calc_refs", mode="before")
    @classmethod
    def sort_refs(cls, v: list[str]) -> list[str]:
        """Sort refs lexicographically for stable output."""
        if isinstance(v, list):
            return sorted(v)
        return v


class QABrief(BaseModel):
    """QA Brief deliverable — founder questions extracted from analysis.

    Per v6.3: compiles all questions_for_founder from agent reports,
    organized by topic with evidence grounding. Deterministic ordering
    by (topic, agent_type, question) for stable output.
    """

    model_config = ConfigDict(frozen=True)

    deliverable_type: Literal["QA_BRIEF"] = "QA_BRIEF"
    deliverable_id: str = Field(..., description="Unique deliverable identifier")
    tenant_id: str = Field(..., description="Tenant scope")
    deal_id: str = Field(..., description="Deal this brief is for")
    deal_name: str = Field(..., description="Human-readable deal name")

    items: list[QAItem] = Field(
        default_factory=list,
        description="QA items sorted by (topic, agent_type, question)",
    )

    summary_section: DeliverableSection = Field(..., description="Brief summary section")

    audit_appendix: AuditAppendix = Field(..., description="Evidence appendix with all refs")

    generated_at: str = Field(..., description="ISO timestamp (passed in, not generated)")


class DeclineLetter(BaseModel):
    """Decline Letter deliverable — evidence-backed decline rationale.

    Per v6.3: generated only when scorecard routing=DECLINE.
    Contains structured decline reasoning with evidence references.
    """

    model_config = ConfigDict(frozen=True)

    deliverable_type: Literal["DECLINE_LETTER"] = "DECLINE_LETTER"
    deliverable_id: str = Field(..., description="Unique deliverable identifier")
    tenant_id: str = Field(..., description="Tenant scope")
    deal_id: str = Field(..., description="Deal this letter is for")
    deal_name: str = Field(..., description="Human-readable deal name")

    rationale_section: DeliverableSection = Field(
        ..., description="Primary decline rationale with evidence"
    )
    key_concerns_section: DeliverableSection = Field(
        ..., description="Key concerns that drove the decline decision"
    )
    missing_info_section: DeliverableSection = Field(
        ..., description="Information gaps that contributed to decline"
    )

    composite_score: float = Field(
        ..., ge=0.0, le=100.0, description="Composite score that triggered decline"
    )
    score_band: str = Field(..., description="Score band (expected: LOW)")

    additional_sections: list[DeliverableSection] = Field(
        default_factory=list,
        description="Additional custom sections",
    )

    audit_appendix: AuditAppendix = Field(..., description="Evidence appendix with all refs")

    generated_at: str = Field(..., description="ISO timestamp (passed in, not generated)")


class DeliverablesBundle(BaseModel):
    """Bundle of all generated deliverables for a deal.

    Contains the 5 standard deliverables (DeclineLetter only when routing=DECLINE).
    All deliverables share the same deal/tenant/run context.
    """

    model_config = ConfigDict(frozen=True)

    deal_id: str = Field(..., description="Deal these deliverables are for")
    tenant_id: str = Field(..., description="Tenant scope")
    run_id: str = Field(..., description="Analysis run identifier")

    screening_snapshot: ScreeningSnapshot = Field(..., description="Partner-ready one-pager")
    ic_memo: ICMemo = Field(..., description="Full IC memo")
    truth_dashboard: TruthDashboard = Field(..., description="Claim-level truth matrix")
    qa_brief: QABrief = Field(..., description="Founder questions brief")
    decline_letter: DeclineLetter | None = Field(
        default=None,
        description="Decline letter (only when routing=DECLINE)",
    )

    generated_at: str = Field(..., description="ISO timestamp (passed in, not generated)")


class DeliverableExportFormat(StrEnum):
    """Supported export formats for deliverables."""

    PDF = "pdf"
    DOCX = "docx"


class DeliverableExportRequest(BaseModel):
    """Request to export a deliverable to a specific format."""

    deliverable_id: str = Field(..., description="ID of deliverable to export")
    format: DeliverableExportFormat = Field(..., description="Target format")
    include_audit_appendix: bool = Field(
        default=True,
        description="Whether to include audit appendix in export",
    )


class DeliverableExportResult(BaseModel):
    """Result of a deliverable export operation."""

    deliverable_id: str = Field(..., description="ID of exported deliverable")
    format: DeliverableExportFormat = Field(..., description="Export format")
    content_bytes: bytes = Field(..., description="Exported content as bytes")
    content_length: int = Field(..., description="Length in bytes")
    includes_audit_appendix: bool = Field(..., description="Whether audit appendix was included")
    export_timestamp: str = Field(..., description="ISO timestamp (passed in, not generated)")
