"""Confidence scoring module for extraction pipeline.

Provides deterministic weighted confidence scoring per spec §6.1–6.3.
All computation uses Decimal for reproducibility.
"""

from idis.services.extraction.confidence.scorer import (
    CONFIDENCE_ACCEPT_WITH_FLAG,
    CONFIDENCE_AUTO_ACCEPT,
    CONFIDENCE_HUMAN_REVIEW,
    ConfidenceScorer,
    SourceTier,
)

__all__ = [
    "CONFIDENCE_ACCEPT_WITH_FLAG",
    "CONFIDENCE_AUTO_ACCEPT",
    "CONFIDENCE_HUMAN_REVIEW",
    "ConfidenceScorer",
    "SourceTier",
]
