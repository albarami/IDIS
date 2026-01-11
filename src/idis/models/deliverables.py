"""IDIS Deliverables Domain Models — v6.3 Phase 6.1

Pydantic models for evidence-linked deliverables generation.

Non-negotiables (Phase 6.1):
- Every DeliverableFact with is_factual=True MUST have non-empty claim_refs
- Stable ordering: claim_refs sorted lexicographically within each fact
- Audit appendix entries sorted by (ref_type, ref_id) for deterministic output
- No randomness: no uuid4/uuid1/random/datetime.now/utcnow in generation paths
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RefType(str, Enum):
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


class DeliverableExportFormat(str, Enum):
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
