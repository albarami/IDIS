"""Scoring framework domain models — Phase 9.

Defines the VC Investment Scorecard models:
- Stage: deal stage (PRE_SEED through GROWTH)
- ScoreDimension: 8 VC scorecard dimensions
- ScoreBand: HIGH / MEDIUM / LOW
- RoutingAction: INVEST / HOLD / DECLINE
- DimensionScore: per-dimension score with evidence + Muḥāsabah
- Scorecard: composite output with all 8 dimensions, band, routing
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from idis.analysis.models import AnalysisMuhasabahRecord, EnrichmentRef


class Stage(StrEnum):
    """Deal stage for stage-specific scoring weights."""

    PRE_SEED = "PRE_SEED"
    SEED = "SEED"
    SERIES_A = "SERIES_A"
    SERIES_B = "SERIES_B"
    GROWTH = "GROWTH"


class ScoreDimension(StrEnum):
    """VC Investment Scorecard dimensions (8 dimensions)."""

    MARKET_ATTRACTIVENESS = "MARKET_ATTRACTIVENESS"
    TEAM_QUALITY = "TEAM_QUALITY"
    PRODUCT_DEFENSIBILITY = "PRODUCT_DEFENSIBILITY"
    TRACTION_VELOCITY = "TRACTION_VELOCITY"
    FUND_THESIS_FIT = "FUND_THESIS_FIT"
    CAPITAL_EFFICIENCY = "CAPITAL_EFFICIENCY"
    SCALABILITY = "SCALABILITY"
    RISK_PROFILE = "RISK_PROFILE"


class ScoreBand(StrEnum):
    """Score band for routing decisions."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class RoutingAction(StrEnum):
    """Routing action derived from score band."""

    INVEST = "INVEST"
    HOLD = "HOLD"
    DECLINE = "DECLINE"


ALL_DIMENSIONS: frozenset[ScoreDimension] = frozenset(ScoreDimension)
_NUM_DIMENSIONS = 8


class DimensionScore(BaseModel):
    """Score for a single VC scorecard dimension with evidence grounding.

    Every factual reference must trace to known registries (NFF).
    Muḥāsabah self-accounting is required per TDD §4.4.
    """

    model_config = ConfigDict(frozen=True)

    dimension: ScoreDimension = Field(..., description="Scorecard dimension being scored")
    score: float = Field(..., ge=0.0, le=1.0, description="Dimension score 0.0-1.0 inclusive")
    rationale: str = Field(..., min_length=1, description="Justification for the score")
    supported_claim_ids: list[str] = Field(..., description="Claims supporting this score")
    supported_calc_ids: list[str] = Field(..., description="Calcs supporting this score")
    enrichment_refs: list[EnrichmentRef] = Field(
        default_factory=list,
        description="Enrichment references with provenance used for this score",
    )
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence in this score 0.0-1.0")
    confidence_justification: str = Field(
        ..., min_length=1, description="Why this confidence level"
    )
    muhasabah: AnalysisMuhasabahRecord = Field(
        ..., description="Required Muḥāsabah self-accounting record"
    )


class Scorecard(BaseModel):
    """Complete VC Investment Scorecard output.

    Contains all 8 dimension scores, stage-weighted composite score,
    score band, and routing action. Fail-closed: missing dimensions
    cause validation failure.
    """

    model_config = ConfigDict(frozen=True)

    stage: Stage = Field(..., description="Deal stage used for weight selection")
    dimension_scores: dict[ScoreDimension, DimensionScore] = Field(
        ..., description="All 8 dimension scores keyed by dimension"
    )
    composite_score: float = Field(
        ..., ge=0.0, le=100.0, description="Stage-weighted composite score 0-100"
    )
    score_band: ScoreBand = Field(..., description="Score band derived from composite score")
    routing: RoutingAction = Field(..., description="Routing action derived from score band")

    @model_validator(mode="after")
    def _require_all_dimensions(self) -> Scorecard:
        """Fail closed: every scorecard must include all 8 dimensions."""
        present = set(self.dimension_scores.keys())
        missing = ALL_DIMENSIONS - present
        if missing:
            missing_names = sorted(d.value for d in missing)
            raise ValueError(f"Scorecard missing required dimensions: {missing_names}")
        if len(self.dimension_scores) != _NUM_DIMENSIONS:
            raise ValueError(
                f"Scorecard must have exactly {_NUM_DIMENSIONS} dimensions, "
                f"got {len(self.dimension_scores)}"
            )
        return self
