"""Stage-specific scoring weight packs — Phase 9.

Defines deterministic weight mappings and routing thresholds per deal stage.
Weight values are sourced from the VC Analyst doc v6.3 scorecard tables.
Seed weights are an interim default (see inline comment).

Fail-closed: unknown stage raises StagePackNotFoundError.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from idis.analysis.scoring.models import (
    ALL_DIMENSIONS,
    RoutingAction,
    ScoreBand,
    ScoreDimension,
    Stage,
)

_WEIGHT_SUM_TOLERANCE = 1e-9


class StagePackNotFoundError(Exception):
    """Raised when no stage pack exists for the requested stage."""


class StagePack(BaseModel):
    """Stage-specific scoring configuration.

    Contains dimension weights (sum to 1.0), band thresholds, and
    routing rules. All fields are immutable after construction.
    """

    model_config = ConfigDict(frozen=True)

    stage: Stage = Field(..., description="Deal stage this pack applies to")
    weights: dict[ScoreDimension, float] = Field(
        ..., description="Dimension weights (must cover all 8, sum to 1.0)"
    )
    band_thresholds: dict[str, float] = Field(
        ..., description="Composite score thresholds for band assignment"
    )
    routing_by_band: dict[ScoreBand, RoutingAction] = Field(
        ..., description="Routing action for each score band"
    )

    @model_validator(mode="after")
    def _validate_weights(self) -> StagePack:
        """Fail closed: weights must cover all 8 dimensions and sum to 1.0."""
        present = set(self.weights.keys())
        missing = ALL_DIMENSIONS - present
        if missing:
            missing_names = sorted(d.value for d in missing)
            raise ValueError(f"Weights missing dimensions: {missing_names}")
        weight_sum = sum(self.weights.values())
        if abs(weight_sum - 1.0) > _WEIGHT_SUM_TOLERANCE:
            raise ValueError(f"Weights must sum to 1.0 (got {weight_sum:.10f})")
        return self


_DEFAULT_BAND_THRESHOLDS: dict[str, float] = {
    "HIGH": 75.0,
    "MEDIUM": 55.0,
}

_DEFAULT_ROUTING_BY_BAND: dict[ScoreBand, RoutingAction] = {
    ScoreBand.HIGH: RoutingAction.INVEST,
    ScoreBand.MEDIUM: RoutingAction.HOLD,
    ScoreBand.LOW: RoutingAction.DECLINE,
}


def _build_weights(mapping: dict[ScoreDimension, float]) -> dict[ScoreDimension, float]:
    """Build a complete weight dict covering all 8 dimensions.

    Args:
        mapping: Non-zero weights. Missing dimensions default to 0.0.

    Returns:
        Dict with all 8 ScoreDimension keys.
    """
    return {dim: mapping.get(dim, 0.0) for dim in ScoreDimension}


_PRE_SEED_WEIGHTS = _build_weights(
    {
        ScoreDimension.TEAM_QUALITY: 0.40,
        ScoreDimension.MARKET_ATTRACTIVENESS: 0.30,
        ScoreDimension.PRODUCT_DEFENSIBILITY: 0.15,
        ScoreDimension.TRACTION_VELOCITY: 0.15,
    }
)

# Seed weights are an interim default until the doc specifies exact weights.
# Blends Pre-Seed team focus with early Series A product signal.
_SEED_WEIGHTS = _build_weights(
    {
        ScoreDimension.TEAM_QUALITY: 0.30,
        ScoreDimension.MARKET_ATTRACTIVENESS: 0.25,
        ScoreDimension.PRODUCT_DEFENSIBILITY: 0.20,
        ScoreDimension.TRACTION_VELOCITY: 0.15,
        ScoreDimension.CAPITAL_EFFICIENCY: 0.10,
    }
)

_SERIES_A_WEIGHTS = _build_weights(
    {
        ScoreDimension.PRODUCT_DEFENSIBILITY: 0.30,
        ScoreDimension.CAPITAL_EFFICIENCY: 0.25,
        ScoreDimension.TEAM_QUALITY: 0.20,
        ScoreDimension.MARKET_ATTRACTIVENESS: 0.15,
        ScoreDimension.TRACTION_VELOCITY: 0.10,
    }
)

_SERIES_B_WEIGHTS = _build_weights(
    {
        ScoreDimension.CAPITAL_EFFICIENCY: 0.25,
        ScoreDimension.SCALABILITY: 0.25,
        ScoreDimension.TEAM_QUALITY: 0.20,
        ScoreDimension.MARKET_ATTRACTIVENESS: 0.15,
        ScoreDimension.PRODUCT_DEFENSIBILITY: 0.15,
    }
)

_GROWTH_WEIGHTS = _build_weights(
    {
        ScoreDimension.RISK_PROFILE: 0.30,
        ScoreDimension.FUND_THESIS_FIT: 0.25,
        ScoreDimension.CAPITAL_EFFICIENCY: 0.25,
        ScoreDimension.MARKET_ATTRACTIVENESS: 0.20,
    }
)


def _make_pack(stage: Stage, weights: dict[ScoreDimension, float]) -> StagePack:
    """Create a StagePack with default thresholds and routing.

    Args:
        stage: Deal stage.
        weights: Dimension weights (must cover all 8, sum to 1.0).

    Returns:
        Validated StagePack.
    """
    return StagePack(
        stage=stage,
        weights=weights,
        band_thresholds=_DEFAULT_BAND_THRESHOLDS,
        routing_by_band=_DEFAULT_ROUTING_BY_BAND,
    )


_STAGE_PACKS: dict[Stage, StagePack] = {
    Stage.PRE_SEED: _make_pack(Stage.PRE_SEED, _PRE_SEED_WEIGHTS),
    Stage.SEED: _make_pack(Stage.SEED, _SEED_WEIGHTS),
    Stage.SERIES_A: _make_pack(Stage.SERIES_A, _SERIES_A_WEIGHTS),
    Stage.SERIES_B: _make_pack(Stage.SERIES_B, _SERIES_B_WEIGHTS),
    Stage.GROWTH: _make_pack(Stage.GROWTH, _GROWTH_WEIGHTS),
}


def get_stage_pack(stage: Stage) -> StagePack:
    """Retrieve the stage pack for a given stage. Fail-closed.

    Args:
        stage: Deal stage to look up.

    Returns:
        StagePack with weights, thresholds, and routing rules.

    Raises:
        StagePackNotFoundError: If no pack is defined for the stage.
    """
    pack = _STAGE_PACKS.get(stage)
    if pack is None:
        raise StagePackNotFoundError(f"No stage pack defined for stage: {stage}")
    return pack
