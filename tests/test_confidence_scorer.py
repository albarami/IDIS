"""Tests for ConfidenceScorer — deterministic weighted confidence scoring.

8 tests covering:
- Perfect scores near one
- All zeros minimum (tier floor at TIER_5)
- Tier 1 vs Tier 5 difference
- Weights sum to one
- Output clamped to unit interval
- Human review thresholds correct
- Deterministic Decimal output
- Invalid inputs clamped
"""

from __future__ import annotations

from decimal import Decimal

from idis.services.extraction.confidence.scorer import (
    CONFIDENCE_ACCEPT_WITH_FLAG,
    CONFIDENCE_AUTO_ACCEPT,
    CONFIDENCE_HUMAN_REVIEW,
    WEIGHT_CONTEXT_QUALITY,
    WEIGHT_EXTRACTION_CLARITY,
    WEIGHT_MODEL_CONFIDENCE,
    WEIGHT_SOURCE_TIER,
    WEIGHT_VALUE_PRECISION,
    ConfidenceScorer,
    SourceTier,
)

SCORER = ConfidenceScorer()


class TestConfidenceScorer:
    """Tests for ConfidenceScorer."""

    def test_perfect_scores_near_one(self) -> None:
        """All 1.0 inputs with TIER_1 produces score near 1.0."""
        result = SCORER.score(
            source_tier=SourceTier.TIER_1,
            extraction_clarity=Decimal("1.0"),
            value_precision=Decimal("1.0"),
            context_quality=Decimal("1.0"),
            model_confidence=Decimal("1.0"),
        )
        assert result == Decimal("1.0")

    def test_all_zeros_minimum(self) -> None:
        """All 0.0 inputs with TIER_5 gives minimum from tier weight only."""
        result = SCORER.score(
            source_tier=SourceTier.TIER_5,
            extraction_clarity=Decimal("0.0"),
            value_precision=Decimal("0.0"),
            context_quality=Decimal("0.0"),
            model_confidence=Decimal("0.0"),
        )
        expected = Decimal("0.30") * Decimal("0.4")
        assert result == expected
        assert result == Decimal("0.12") or result == Decimal("0.120")

    def test_tier_1_vs_tier_5_difference(self) -> None:
        """Higher tier produces meaningfully higher score."""
        shared_kwargs = {
            "extraction_clarity": Decimal("0.5"),
            "value_precision": Decimal("0.5"),
            "context_quality": Decimal("0.5"),
            "model_confidence": Decimal("0.5"),
        }
        tier_1_score = SCORER.score(source_tier=SourceTier.TIER_1, **shared_kwargs)
        tier_5_score = SCORER.score(source_tier=SourceTier.TIER_5, **shared_kwargs)

        assert tier_1_score > tier_5_score
        assert tier_1_score - tier_5_score >= Decimal("0.1")

    def test_weights_sum_to_one(self) -> None:
        """Factor weights sum to exactly 1.00."""
        total = (
            WEIGHT_SOURCE_TIER
            + WEIGHT_EXTRACTION_CLARITY
            + WEIGHT_VALUE_PRECISION
            + WEIGHT_CONTEXT_QUALITY
            + WEIGHT_MODEL_CONFIDENCE
        )
        assert total == Decimal("1.00")

    def test_output_clamped_to_unit_interval(self) -> None:
        """Result is always in [0, 1] even with extreme inputs."""
        result_high = SCORER.score(
            source_tier=SourceTier.TIER_1,
            extraction_clarity=Decimal("1.0"),
            value_precision=Decimal("1.0"),
            context_quality=Decimal("1.0"),
            model_confidence=Decimal("1.0"),
        )
        assert Decimal("0") <= result_high <= Decimal("1")

        result_low = SCORER.score(
            source_tier=SourceTier.TIER_5,
            extraction_clarity=Decimal("0.0"),
            value_precision=Decimal("0.0"),
            context_quality=Decimal("0.0"),
            model_confidence=Decimal("0.0"),
        )
        assert Decimal("0") <= result_low <= Decimal("1")

    def test_human_review_thresholds_correct(self) -> None:
        """Threshold constants match spec §6.3."""
        assert Decimal("0.95") == CONFIDENCE_AUTO_ACCEPT
        assert Decimal("0.80") == CONFIDENCE_ACCEPT_WITH_FLAG
        assert Decimal("0.50") == CONFIDENCE_HUMAN_REVIEW

        assert CONFIDENCE_AUTO_ACCEPT > CONFIDENCE_ACCEPT_WITH_FLAG
        assert CONFIDENCE_ACCEPT_WITH_FLAG > CONFIDENCE_HUMAN_REVIEW

    def test_deterministic_decimal_output(self) -> None:
        """Same inputs produce same Decimal output across calls."""
        kwargs = {
            "source_tier": SourceTier.TIER_2,
            "extraction_clarity": Decimal("0.75"),
            "value_precision": Decimal("0.60"),
            "context_quality": Decimal("0.80"),
            "model_confidence": Decimal("0.90"),
        }
        result_a = SCORER.score(**kwargs)
        result_b = SCORER.score(**kwargs)

        assert result_a == result_b
        assert isinstance(result_a, Decimal)

    def test_invalid_inputs_clamped(self) -> None:
        """Inputs >1.0 or <0.0 are clamped to [0, 1]."""
        result = SCORER.score(
            source_tier=SourceTier.TIER_1,
            extraction_clarity=Decimal("2.0"),
            value_precision=Decimal("-0.5"),
            context_quality=Decimal("1.5"),
            model_confidence=Decimal("-1.0"),
        )
        assert Decimal("0") <= result <= Decimal("1")

        result_normal = SCORER.score(
            source_tier=SourceTier.TIER_1,
            extraction_clarity=Decimal("1.0"),
            value_precision=Decimal("0.0"),
            context_quality=Decimal("1.0"),
            model_confidence=Decimal("0.0"),
        )
        assert result == result_normal
