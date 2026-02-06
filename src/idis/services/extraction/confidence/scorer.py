"""Confidence scorer — deterministic weighted scoring per spec §6.1–6.3.

All computation uses Decimal for reproducibility.
Same inputs + same code version = identical output.

Weights:
    source_tier:        0.30
    extraction_clarity: 0.25
    value_precision:    0.20
    context_quality:    0.15
    model_confidence:   0.10
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum


class SourceTier(StrEnum):
    """Source document tier classification.

    Higher tiers indicate more trustworthy sources.
    """

    TIER_1 = "TIER_1"
    TIER_2 = "TIER_2"
    TIER_3 = "TIER_3"
    TIER_4 = "TIER_4"
    TIER_5 = "TIER_5"


TIER_WEIGHTS: dict[SourceTier, Decimal] = {
    SourceTier.TIER_1: Decimal("1.0"),
    SourceTier.TIER_2: Decimal("0.8"),
    SourceTier.TIER_3: Decimal("0.6"),
    SourceTier.TIER_4: Decimal("0.5"),
    SourceTier.TIER_5: Decimal("0.4"),
}

WEIGHT_SOURCE_TIER = Decimal("0.30")
WEIGHT_EXTRACTION_CLARITY = Decimal("0.25")
WEIGHT_VALUE_PRECISION = Decimal("0.20")
WEIGHT_CONTEXT_QUALITY = Decimal("0.15")
WEIGHT_MODEL_CONFIDENCE = Decimal("0.10")

CONFIDENCE_AUTO_ACCEPT = Decimal("0.95")
CONFIDENCE_ACCEPT_WITH_FLAG = Decimal("0.80")
CONFIDENCE_HUMAN_REVIEW = Decimal("0.50")

_ZERO = Decimal("0")
_ONE = Decimal("1")


def _clamp(value: Decimal) -> Decimal:
    """Clamp a Decimal value to [0.0, 1.0]."""
    if value < _ZERO:
        return _ZERO
    if value > _ONE:
        return _ONE
    return value


class ConfidenceScorer:
    """Deterministic weighted confidence scorer.

    Computes extraction confidence from five factors using Decimal arithmetic.
    All inputs are clamped to [0.0, 1.0]. Output is clamped to [0.0, 1.0].
    """

    def score(
        self,
        *,
        source_tier: SourceTier,
        extraction_clarity: Decimal,
        value_precision: Decimal,
        context_quality: Decimal,
        model_confidence: Decimal,
    ) -> Decimal:
        """Compute weighted confidence score.

        Args:
            source_tier: Document source tier (TIER_1 through TIER_5).
            extraction_clarity: How clearly structured the extraction is (0–1).
            value_precision: Explicit units/dates vs implied (0–1).
            context_quality: How well surrounding text supports claim (0–1).
            model_confidence: LLM self-reported confidence (0–1).

        Returns:
            Confidence score as Decimal in [0.0, 1.0].
        """
        tier_weight = TIER_WEIGHTS[source_tier]

        clamped_clarity = _clamp(extraction_clarity)
        clamped_precision = _clamp(value_precision)
        clamped_context = _clamp(context_quality)
        clamped_model = _clamp(model_confidence)

        confidence = (
            WEIGHT_SOURCE_TIER * tier_weight
            + WEIGHT_EXTRACTION_CLARITY * clamped_clarity
            + WEIGHT_VALUE_PRECISION * clamped_precision
            + WEIGHT_CONTEXT_QUALITY * clamped_context
            + WEIGHT_MODEL_CONFIDENCE * clamped_model
        )

        return _clamp(confidence)
