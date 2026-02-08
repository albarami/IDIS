"""Analysis agent framework domain models — Phase 8.A.

Defines the Layer 2 (IC mode) analysis framework models per TDD §10.2:
- EnrichmentRef: enrichment reference with required provenance
- AnalysisContext: input context for analysis agents
- Risk: identified risk with evidence links
- AnalysisMuhasabahRecord: self-accounting record per TDD §4.4
- AgentReport: structured agent output (TDD §10.2 schema)
- AnalysisBundle: collection result from the analysis engine
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RiskSeverity(StrEnum):
    """Risk severity levels."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class EnrichmentRef(BaseModel):
    """Reference to an enrichment result with required provenance.

    The NFF validator requires both provider_id and source_id to be present
    for any enrichment reference. Missing provenance = fail closed.
    """

    model_config = ConfigDict(frozen=True)

    ref_id: str = Field(..., min_length=1, description="Unique enrichment reference ID")
    provider_id: str = Field(..., min_length=1, description="Provider that supplied the data")
    source_id: str = Field(
        ...,
        min_length=1,
        description="Stable source identifier from EnrichmentResult.provenance",
    )


class AnalysisContext(BaseModel):
    """Input context for analysis agents.

    Contains the validated evidence package and enrichment data
    that agents consume when producing their analysis.
    """

    model_config = ConfigDict(frozen=True)

    deal_id: str = Field(..., min_length=1, description="Deal being analyzed")
    tenant_id: str = Field(..., min_length=1, description="Tenant scope for isolation")
    run_id: str = Field(..., min_length=1, description="Analysis run identifier")
    claim_ids: frozenset[str] = Field(..., description="Known valid claim IDs from claim registry")
    calc_ids: frozenset[str] = Field(..., description="Known valid calc IDs from calc engine")
    enrichment_refs: dict[str, EnrichmentRef] = Field(
        default_factory=dict,
        description="Enrichment references keyed by ref_id, each with full provenance",
    )
    company_name: str = Field(default="", description="Company name for deal context")
    stage: str = Field(default="", description="Deal stage (e.g. Series A, Seed)")
    sector: str = Field(default="", description="Company sector / industry")


class Risk(BaseModel):
    """A risk identified by an analysis agent, with evidence links.

    Per TDD §10.2, each risk must include evidence links (claim_ids,
    calc_ids, or enrichment_ref_ids). At least one link is required.
    """

    model_config = ConfigDict(frozen=True)

    risk_id: str = Field(..., min_length=1, description="Unique risk identifier")
    description: str = Field(..., min_length=1, description="Risk description")
    severity: RiskSeverity = Field(..., description="Risk severity: HIGH, MEDIUM, or LOW")
    claim_ids: list[str] = Field(default_factory=list, description="Supporting claim references")
    calc_ids: list[str] = Field(default_factory=list, description="Supporting calc references")
    enrichment_ref_ids: list[str] = Field(
        default_factory=list, description="Supporting enrichment references"
    )

    @model_validator(mode="after")
    def _require_evidence_links(self) -> Risk:
        """Fail closed: every risk must have at least one evidence link."""
        if not self.claim_ids and not self.calc_ids and not self.enrichment_ref_ids:
            raise ValueError(
                f"Risk '{self.risk_id}' must include at least one evidence link "
                f"(claim_ids, calc_ids, or enrichment_ref_ids)"
            )
        return self


class AnalysisMuhasabahRecord(BaseModel):
    """Muḥāsabah self-accounting record for analysis agents.

    Per TDD §4.4 and §10.2 Appendix B. Compatible with
    validate_muhasabah() from idis.validators.muhasabah.
    """

    model_config = ConfigDict(frozen=True)

    agent_id: str = Field(..., min_length=1, description="Agent that produced this record")
    output_id: str = Field(..., min_length=1, description="Associated output identifier")
    supported_claim_ids: list[str] = Field(..., description="Claims supporting the output")
    supported_calc_ids: list[str] = Field(
        default_factory=list, description="Calcs supporting the output"
    )
    evidence_summary: str = Field(..., min_length=1, description="Summary of strongest evidence")
    counter_hypothesis: str = Field(..., min_length=1, description="Alternative explanation")
    falsifiability_tests: list[dict[str, Any]] = Field(
        default_factory=list, description="Tests that could falsify the output"
    )
    uncertainties: list[dict[str, Any]] = Field(
        default_factory=list, description="Registered uncertainties"
    )
    failure_modes: list[str] = Field(default_factory=list, description="Identified failure modes")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence score 0.0-1.0")
    confidence_justification: str = Field(
        ..., min_length=1, description="Why this confidence level"
    )
    timestamp: str = Field(..., min_length=1, description="ISO 8601 timestamp")
    is_subjective: bool = Field(
        default=False,
        description="True if output contains no factual assertions",
    )

    def to_validator_dict(self) -> dict[str, Any]:
        """Convert to dict format expected by validate_muhasabah().

        Returns:
            Dict compatible with idis.validators.muhasabah.validate_muhasabah().
        """
        return {
            "agent_id": self.agent_id,
            "output_id": self.output_id,
            "supported_claim_ids": list(self.supported_claim_ids),
            "supported_calc_ids": list(self.supported_calc_ids),
            "falsifiability_tests": list(self.falsifiability_tests),
            "uncertainties": list(self.uncertainties),
            "failure_modes": list(self.failure_modes),
            "confidence": self.confidence,
            "confidence_justification": self.confidence_justification,
            "timestamp": self.timestamp,
            "is_subjective": self.is_subjective,
        }


class AgentReport(BaseModel):
    """Structured output from an analysis agent per TDD §10.2.

    All fields are required (no defaults except enrichment_ref_ids).
    Missing fields cause Pydantic validation failure (fail-closed).
    The framework validates muhasabah and NFF after construction.
    """

    model_config = ConfigDict(frozen=True)

    agent_id: str = Field(..., min_length=1, description="Agent that produced this report")
    agent_type: str = Field(..., min_length=1, description="Agent type (e.g., financial_agent)")
    supported_claim_ids: list[str] = Field(..., description="Claims supporting the analysis")
    supported_calc_ids: list[str] = Field(..., description="Calcs supporting the analysis")
    analysis_sections: dict[str, Any] = Field(
        ...,
        description="Structured analysis content keyed to deliverable template sections",
    )
    risks: list[Risk] = Field(..., description="Identified risks with evidence links")
    questions_for_founder: list[str] = Field(..., description="Questions to ask the founder")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence score 0.0-1.0")
    confidence_justification: str = Field(
        ..., min_length=1, description="Why this confidence level"
    )
    muhasabah: AnalysisMuhasabahRecord = Field(
        ..., description="Required Muḥāsabah self-accounting record"
    )
    enrichment_ref_ids: list[str] = Field(
        default_factory=list,
        description="Enrichment references used in this report",
    )


class AnalysisBundle(BaseModel):
    """Collection result from the analysis engine.

    Contains all validated agent reports for a single analysis run.
    """

    model_config = ConfigDict(frozen=True)

    deal_id: str = Field(..., min_length=1, description="Deal that was analyzed")
    tenant_id: str = Field(..., min_length=1, description="Tenant scope")
    run_id: str = Field(..., min_length=1, description="Analysis run identifier")
    reports: list[AgentReport] = Field(..., description="Validated agent reports")
    timestamp: str = Field(..., min_length=1, description="ISO 8601 completion timestamp")
