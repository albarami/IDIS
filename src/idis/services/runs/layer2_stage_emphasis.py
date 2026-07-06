"""Layer-2 challenge-category stage emphasis (Slice93 Task 6, DEC-E).

Reuses the scoring ``stage_packs`` dimension weights to emphasize Layer-2 challenge
categories per deal stage. This is presentation-only: it reads ``get_stage_pack`` weights
and NEVER mutates, recomputes, or feeds the analysis/Layer-2 scorecard. Output is safe
(categories/counts/weights) and deterministic (sorted keys, fixed rounding).
"""

from __future__ import annotations

from idis.analysis.scoring.models import ScoreDimension, Stage
from idis.analysis.scoring.stage_packs import get_stage_pack
from idis.models.layer2_ic_challenge import Layer2ChallengeCategory

# The eight mapped categories cover all eight scorecard dimensions exactly; GENERAL is a
# catch-all with no dimension and is excluded from the weighted emphasis view.
CATEGORY_TO_DIMENSION: dict[Layer2ChallengeCategory, ScoreDimension] = {
    Layer2ChallengeCategory.MARKET_RISK: ScoreDimension.MARKET_ATTRACTIVENESS,
    Layer2ChallengeCategory.TEAM_RISK: ScoreDimension.TEAM_QUALITY,
    Layer2ChallengeCategory.PRODUCT_RISK: ScoreDimension.PRODUCT_DEFENSIBILITY,
    Layer2ChallengeCategory.TRACTION_RISK: ScoreDimension.TRACTION_VELOCITY,
    Layer2ChallengeCategory.THESIS_FIT_RISK: ScoreDimension.FUND_THESIS_FIT,
    Layer2ChallengeCategory.CAPITAL_EFFICIENCY_RISK: ScoreDimension.CAPITAL_EFFICIENCY,
    Layer2ChallengeCategory.SCALABILITY_RISK: ScoreDimension.SCALABILITY,
    Layer2ChallengeCategory.EXECUTION_RISK: ScoreDimension.RISK_PROFILE,
}

_EMPHASIS_ROUNDING = 6


def apply_layer2_stage_emphasis(stage: Stage, by_category: dict[str, int]) -> dict[str, object]:
    """Weight challenge-category counts by the stage's scorecard-dimension weights.

    Read-only reuse of ``get_stage_pack(stage).weights`` — this must never mutate or feed the
    scorecard (there is no ``scorecard`` parameter by design). Categories without a scorecard
    dimension (e.g. ``GENERAL``) are excluded from the weighted view. The result is
    deterministic: weighted values are sorted by key and rounded to a fixed precision, and the
    emphasis order is by descending weight then category name.
    """
    pack = get_stage_pack(stage)
    weighted: dict[str, float] = {}
    for category_value, count in by_category.items():
        try:
            category = Layer2ChallengeCategory(category_value)
        except ValueError:
            continue
        dimension = CATEGORY_TO_DIMENSION.get(category)
        if dimension is None:
            continue
        weighted[category_value] = round(int(count) * pack.weights[dimension], _EMPHASIS_ROUNDING)
    emphasized = [
        category for category, _weight in sorted(weighted.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    return {
        "stage": stage.value,
        "weighted_by_category": dict(sorted(weighted.items())),
        "emphasized_categories": emphasized,
    }
