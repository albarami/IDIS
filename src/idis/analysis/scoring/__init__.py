"""IDIS Scoring Framework — Phase 9.

Converts 8 specialist agent reports into:
- 8 dimension scores (VC scorecard)
- 1 composite score (stage-weighted)
- Routing (INVEST / HOLD / DECLINE) + score band (HIGH / MEDIUM / LOW)

Deterministic weighting and routing, strict NFF + Muḥāsabah enforcement.
"""

from idis.analysis.scoring.engine import ScoringEngine, ScoringEngineError
from idis.analysis.scoring.llm_scorecard_runner import LLMScorecardRunner
from idis.analysis.scoring.models import (
    DimensionScore,
    RoutingAction,
    ScoreBand,
    Scorecard,
    ScoreDimension,
    Stage,
)
from idis.analysis.scoring.stage_packs import StagePack, StagePackNotFoundError, get_stage_pack

__all__ = [
    "DimensionScore",
    "LLMScorecardRunner",
    "RoutingAction",
    "ScoreBand",
    "Scorecard",
    "ScoreDimension",
    "ScoringEngine",
    "ScoringEngineError",
    "Stage",
    "StagePack",
    "StagePackNotFoundError",
    "get_stage_pack",
]
