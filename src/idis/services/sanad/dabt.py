"""Dabt (Precision) — Multi-dimensional precision scoring.

Implements deterministic precision scoring across four dimensions.
All scoring is fail-closed: missing dimensions default to 0.0.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class DabtDimensions:
    """Multi-dimensional precision factors for Dabt scoring.

    All dimensions are in range [0.0, 1.0].
    None indicates dimension is not available (fail-closed to 0.0).
    """

    documentation_precision: float | None = None
    transmission_precision: float | None = None
    temporal_precision: float | None = None
    cognitive_precision: float | None = None

    def to_dict(self) -> dict[str, float | None]:
        """Convert to dictionary representation."""
        return {
            "documentation_precision": self.documentation_precision,
            "transmission_precision": self.transmission_precision,
            "temporal_precision": self.temporal_precision,
            "cognitive_precision": self.cognitive_precision,
        }


@dataclass
class DabtScore:
    """Result of Dabt scoring calculation."""

    score: float
    dimensions: DabtDimensions
    quality_band: str
    available_dimensions: int
    total_dimensions: int
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "score": self.score,
            "dimensions": self.dimensions.to_dict(),
            "quality_band": self.quality_band,
            "available_dimensions": self.available_dimensions,
            "total_dimensions": self.total_dimensions,
            "warnings": self.warnings,
        }


DIMENSION_WEIGHTS: dict[str, float] = {
    "documentation_precision": 0.30,
    "transmission_precision": 0.30,
    "temporal_precision": 0.25,
    "cognitive_precision": 0.15,
}

QUALITY_BANDS: list[tuple[float, str]] = [
    (0.90, "EXCELLENT"),
    (0.75, "GOOD"),
    (0.50, "FAIR"),
    (0.00, "POOR"),
]


def _clamp(value: float, min_val: float = 0.0, max_val: float = 1.0) -> float:
    """Clamp value to range [min_val, max_val]."""
    return max(min_val, min(max_val, value))


def _get_quality_band(score: float) -> str:
    """Get quality band for a Dabt score."""
    for threshold, band in QUALITY_BANDS:
        if score >= threshold:
            return band
    return "POOR"


def calculate_dabt_score(
    factors: DabtDimensions | dict[str, Any] | None,
) -> DabtScore:
    """Calculate Dabt (precision) score from multi-dimensional factors.

    FAIL-CLOSED BEHAVIOR:
    - Missing dimension → treated as 0.0 (never increases score)
    - cognitive_precision = None → excluded from calculation, does not penalize
    - If all dimensions missing → dabt_score = 0.0
    - Invalid values (outside [0,1]) → clamped to range

    Args:
        factors: DabtDimensions object or dictionary with precision factors

    Returns:
        DabtScore with computed score, quality band, and diagnostics
    """
    warnings: list[str] = []

    if factors is None:
        return DabtScore(
            score=0.0,
            dimensions=DabtDimensions(),
            quality_band="POOR",
            available_dimensions=0,
            total_dimensions=4,
            warnings=["No factors provided - fail closed to 0.0"],
        )

    if isinstance(factors, dict):
        dimensions = DabtDimensions(
            documentation_precision=factors.get("documentation_precision"),
            transmission_precision=factors.get("transmission_precision"),
            temporal_precision=factors.get("temporal_precision"),
            cognitive_precision=factors.get("cognitive_precision"),
        )
    else:
        dimensions = factors

    # FAIL-CLOSED: Required dimensions ALWAYS contribute to denominator.
    # Missing required dim → value 0.0, weight still in denominator (score decreases).
    # cognitive_precision is optional: only contributes when present.
    REQUIRED_DIMS = ("documentation_precision", "transmission_precision", "temporal_precision")
    OPTIONAL_DIMS = ("cognitive_precision",)

    weighted_sum = 0.0
    available_count = 0

    # Calculate fixed denominator for required dimensions
    required_weight = sum(DIMENSION_WEIGHTS[d] for d in REQUIRED_DIMS)
    total_weight = required_weight  # Start with required dims always in denominator

    for dim_name in REQUIRED_DIMS:
        weight = DIMENSION_WEIGHTS[dim_name]
        value = getattr(dimensions, dim_name, None)

        if value is None:
            warnings.append(
                f"{dim_name} missing - fail closed to 0.0 (weight {weight} in denominator)"
            )
            # Value is 0.0, weight already in total_weight
            continue

        if not isinstance(value, (int, float)):
            warnings.append(f"{dim_name} invalid type {type(value).__name__} - fail closed to 0.0")
            # Value is 0.0, weight already in total_weight
            continue

        clamped = _clamp(float(value))
        if clamped != value:
            warnings.append(f"{dim_name} clamped from {value} to {clamped}")

        weighted_sum += clamped * weight
        available_count += 1

    # Optional dimensions: only add to numerator AND denominator if present and valid
    for dim_name in OPTIONAL_DIMS:
        weight = DIMENSION_WEIGHTS[dim_name]
        value = getattr(dimensions, dim_name, None)

        if value is None:
            # Optional dim missing: do not add to numerator or denominator (score unchanged)
            continue

        if not isinstance(value, (int, float)):
            warnings.append(
                f"{dim_name} invalid type {type(value).__name__} - excluded from calculation"
            )
            continue

        clamped = _clamp(float(value))
        if clamped != value:
            warnings.append(f"{dim_name} clamped from {value} to {clamped}")

        weighted_sum += clamped * weight
        total_weight += weight
        available_count += 1

    # Denominator is always >= required_weight (0.85), never zero
    score = weighted_sum / total_weight

    return DabtScore(
        score=round(score, 4),
        dimensions=dimensions,
        quality_band=_get_quality_band(score),
        available_dimensions=available_count,
        total_dimensions=4,
        warnings=warnings,
    )


def get_dabt_grade_impact(dabt_score: float) -> tuple[str | None, str | None]:
    """Determine grade impact from Dabt score.

    Args:
        dabt_score: Computed Dabt score [0.0, 1.0]

    Returns:
        Tuple of (grade_cap, warning_message)
        grade_cap is None if no cap applies
    """
    if dabt_score < 0.50:
        return ("B", f"Poor Dabt score ({dabt_score:.2f}) caps grade at B")

    if dabt_score < 0.75:
        return (None, f"Fair Dabt score ({dabt_score:.2f}) - warning flag")

    return (None, None)


def extract_dabt_from_sanad(sanad: dict[str, Any]) -> DabtDimensions:
    """Extract Dabt dimensions from a Sanad record.

    Looks for dabt_factors or individual dimension fields.

    Args:
        sanad: Sanad dictionary

    Returns:
        DabtDimensions with available values
    """
    dabt_factors = sanad.get("dabt_factors")
    if dabt_factors and isinstance(dabt_factors, dict):
        return DabtDimensions(
            documentation_precision=dabt_factors.get("documentation_precision"),
            transmission_precision=dabt_factors.get("transmission_precision"),
            temporal_precision=dabt_factors.get("temporal_precision"),
            cognitive_precision=dabt_factors.get("cognitive_precision"),
        )

    return DabtDimensions(
        documentation_precision=sanad.get("documentation_precision"),
        transmission_precision=sanad.get("transmission_precision"),
        temporal_precision=sanad.get("temporal_precision"),
        cognitive_precision=sanad.get("cognitive_precision"),
    )
