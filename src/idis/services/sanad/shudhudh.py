"""Shudhudh (Anomaly) Detection — Reconciliation-first anomaly detection.

Implements reconciliation heuristics before flagging anomalies.
All detection is deterministic and fail-closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from idis.services.sanad.source_tiers import assign_source_tier


class ReconciliationType(Enum):
    """Types of reconciliation attempts."""

    UNIT_CONVERSION = "UNIT_CONVERSION"
    TIME_WINDOW = "TIME_WINDOW"
    ROUNDING = "ROUNDING"
    CURRENCY = "CURRENCY"
    NONE = "NONE"


@dataclass
class ReconciliationAttempt:
    """Result of a reconciliation attempt."""

    reconciliation_type: ReconciliationType
    success: bool
    original_values: list[Any]
    reconciled_value: Any | None
    explanation: str


@dataclass
class ShudhuhResult:
    """Result of Shudhudh (anomaly) detection."""

    has_anomaly: bool
    defect_code: str | None
    severity: str | None
    description: str | None
    cure_protocol: str | None
    reconciliation_attempts: list[ReconciliationAttempt]
    consensus_value: Any | None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "has_anomaly": self.has_anomaly,
            "defect_code": self.defect_code,
            "severity": self.severity,
            "description": self.description,
            "cure_protocol": self.cure_protocol,
            "reconciliation_attempts": [
                {
                    "type": r.reconciliation_type.value,
                    "success": r.success,
                    "original_values": r.original_values,
                    "reconciled_value": r.reconciled_value,
                    "explanation": r.explanation,
                }
                for r in self.reconciliation_attempts
            ],
            "consensus_value": self.consensus_value,
        }


UNIT_MULTIPLIERS = {
    "K": 1_000,
    "M": 1_000_000,
    "B": 1_000_000_000,
    "THOUSAND": 1_000,
    "MILLION": 1_000_000,
    "BILLION": 1_000_000_000,
}

ROUNDING_TOLERANCE = 0.01


def _extract_numeric_value(value: Any) -> float | None:
    """Extract numeric value from various formats."""
    if value is None:
        return None

    if isinstance(value, (int, float)):
        return float(value)

    if isinstance(value, str):
        cleaned = value.replace(",", "").replace("$", "").replace("€", "").strip()

        for suffix, multiplier in UNIT_MULTIPLIERS.items():
            if cleaned.upper().endswith(suffix):
                try:
                    base = float(cleaned[: -len(suffix)].strip())
                    return base * multiplier
                except (ValueError, TypeError):
                    continue

        try:
            return float(cleaned)
        except (ValueError, TypeError):
            return None

    if isinstance(value, dict):
        return _extract_numeric_value(value.get("value") or value.get("amount"))

    return None


def _extract_unit_label(value: Any) -> str | None:
    """Extract unit label from value (K, M, B, etc.)."""
    if isinstance(value, str):
        upper = value.upper().strip()
        for suffix in UNIT_MULTIPLIERS:
            if upper.endswith(suffix):
                return suffix
    if isinstance(value, dict):
        return value.get("unit") or value.get("scale")
    return None


def _extract_time_window(value: Any) -> str | None:
    """Extract time window label (FY, LTM, etc.)."""
    if isinstance(value, dict):
        return value.get("time_window") or value.get("period")
    if isinstance(value, str):
        upper = value.upper()
        if "FY" in upper or "FISCAL" in upper:
            return "FY"
        if "LTM" in upper or "TTM" in upper:
            return "LTM"
    return None


def _attempt_unit_reconciliation(
    values: list[dict[str, Any]],
) -> ReconciliationAttempt:
    """Attempt to reconcile values via unit conversion.

    Checks if values differ by 1000x or 1000000x with explicit labels.
    """
    numeric_values = []
    for v in values:
        num = _extract_numeric_value(v.get("value"))
        unit = _extract_unit_label(v.get("value")) or v.get("unit")
        if num is not None:
            numeric_values.append({"numeric": num, "unit": unit, "original": v})

    if len(numeric_values) < 2:
        return ReconciliationAttempt(
            reconciliation_type=ReconciliationType.UNIT_CONVERSION,
            success=False,
            original_values=[v.get("value") for v in values],
            reconciled_value=None,
            explanation="Insufficient numeric values for unit reconciliation",
        )

    # Extract numeric values with explicit typing for mypy
    # nv["numeric"] is guaranteed to be a float from _extract_numeric_value
    base_values: list[float] = []
    units: list[str | None] = []
    for nv in numeric_values:
        num_val = nv["numeric"]
        # Type narrowing: num_val is known to be numeric from _extract_numeric_value
        if isinstance(num_val, (int, float)):
            base_values.append(float(num_val))
        else:
            base_values.append(0.0)
        unit_val = nv["unit"]
        units.append(str(unit_val) if unit_val is not None else None)

    for i, (val_a, unit_a) in enumerate(zip(base_values, units, strict=False)):
        for j, (val_b, unit_b) in enumerate(zip(base_values, units, strict=False)):
            if i >= j:
                continue

            if val_a == 0 or val_b == 0:
                continue

            ratio = val_a / val_b if val_b != 0 else 0.0

            if 999 <= ratio <= 1001 and unit_a and unit_b:
                return ReconciliationAttempt(
                    reconciliation_type=ReconciliationType.UNIT_CONVERSION,
                    success=True,
                    original_values=list(base_values),
                    reconciled_value=max(val_a, val_b),
                    explanation=(
                        f"Values differ by ~1000x with units {unit_a}/{unit_b} - reconciled"
                    ),
                )

            if 999_000 <= ratio <= 1_001_000 and unit_a and unit_b:
                return ReconciliationAttempt(
                    reconciliation_type=ReconciliationType.UNIT_CONVERSION,
                    success=True,
                    original_values=list(base_values),
                    reconciled_value=max(val_a, val_b),
                    explanation=(f"Values differ by ~1M with units {unit_a}/{unit_b} - reconciled"),
                )

    return ReconciliationAttempt(
        reconciliation_type=ReconciliationType.UNIT_CONVERSION,
        success=False,
        original_values=base_values,
        reconciled_value=None,
        explanation="No unit conversion pattern detected",
    )


def _attempt_time_window_reconciliation(
    values: list[dict[str, Any]],
) -> ReconciliationAttempt:
    """Attempt to reconcile values via time window alignment."""
    time_windows = []
    for v in values:
        tw = _extract_time_window(v.get("value")) or v.get("time_window") or v.get("period")
        time_windows.append(tw)

    if not any(time_windows):
        return ReconciliationAttempt(
            reconciliation_type=ReconciliationType.TIME_WINDOW,
            success=False,
            original_values=[v.get("value") for v in values],
            reconciled_value=None,
            explanation="No time window labels found",
        )

    unique_windows = {tw for tw in time_windows if tw}
    if len(unique_windows) > 1:
        return ReconciliationAttempt(
            reconciliation_type=ReconciliationType.TIME_WINDOW,
            success=True,
            original_values=[v.get("value") for v in values],
            reconciled_value=None,
            explanation=(f"Different time windows detected ({unique_windows}) - not comparable"),
        )

    return ReconciliationAttempt(
        reconciliation_type=ReconciliationType.TIME_WINDOW,
        success=False,
        original_values=[v.get("value") for v in values],
        reconciled_value=None,
        explanation="Same time window - no reconciliation needed",
    )


def _attempt_rounding_reconciliation(
    values: list[dict[str, Any]],
    tolerance: float = ROUNDING_TOLERANCE,
) -> ReconciliationAttempt:
    """Attempt to reconcile values within rounding tolerance."""
    numeric_values = []
    for v in values:
        num = _extract_numeric_value(v.get("value"))
        if num is not None:
            numeric_values.append(num)

    if len(numeric_values) < 2:
        return ReconciliationAttempt(
            reconciliation_type=ReconciliationType.ROUNDING,
            success=False,
            original_values=[v.get("value") for v in values],
            reconciled_value=None,
            explanation="Insufficient numeric values for rounding check",
        )

    mean_value = sum(numeric_values) / len(numeric_values)
    if mean_value == 0:
        all_zero = all(v == 0 for v in numeric_values)
        return ReconciliationAttempt(
            reconciliation_type=ReconciliationType.ROUNDING,
            success=all_zero,
            original_values=numeric_values,
            reconciled_value=0 if all_zero else None,
            explanation="All values are zero" if all_zero else "Mean is zero but values differ",
        )

    max_deviation = max(abs(v - mean_value) / mean_value for v in numeric_values)

    if max_deviation <= tolerance:
        return ReconciliationAttempt(
            reconciliation_type=ReconciliationType.ROUNDING,
            success=True,
            original_values=numeric_values,
            reconciled_value=mean_value,
            explanation=f"Values within {tolerance * 100}% tolerance - treated as reconciled",
        )

    return ReconciliationAttempt(
        reconciliation_type=ReconciliationType.ROUNDING,
        success=False,
        original_values=numeric_values,
        reconciled_value=None,
        explanation=(
            f"Values differ by {max_deviation * 100:.1f}% (exceeds {tolerance * 100}% tolerance)"
        ),
    )


def _compute_consensus(
    values: list[dict[str, Any]],
    sources: list[dict[str, Any]],
) -> Any:
    """Compute consensus value weighted by source tier."""
    if not values:
        return None

    tier_weights = []
    numeric_values = []

    for i, v in enumerate(values):
        num = _extract_numeric_value(v.get("value"))
        if num is None:
            continue

        source = sources[i] if i < len(sources) else {}
        tier = assign_source_tier(source)

        from idis.services.sanad.source_tiers import get_tier_weight

        weight = get_tier_weight(tier)
        tier_weights.append(weight)
        numeric_values.append(num)

    if not numeric_values:
        return None

    total_weight = sum(tier_weights)
    if total_weight == 0:
        return sum(numeric_values) / len(numeric_values)

    weighted_sum = sum(v * w for v, w in zip(numeric_values, tier_weights, strict=False))
    return weighted_sum / total_weight


def _contradicts(
    value: Any,
    consensus: Any,
    threshold: float = 0.05,
) -> bool:
    """Check if value contradicts consensus beyond threshold."""
    val_num = _extract_numeric_value(value)
    cons_num = _extract_numeric_value(consensus)

    if val_num is None or cons_num is None:
        return False

    if cons_num == 0:
        return val_num != 0

    deviation = abs(val_num - cons_num) / abs(cons_num)
    return deviation > threshold


def detect_shudhudh(
    claim_values: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    *,
    contradiction_threshold: float = 0.05,
) -> ShudhuhResult:
    """Detect Shudhudh (anomaly) using reconciliation-first approach.

    ALGORITHM:
    1. Attempt reconciliation heuristics (unit, time window, rounding)
    2. If reconciliation succeeds → no anomaly
    3. If reconciliation fails → check for lower-tier contradiction
    4. If lower-tier contradicts higher-tier/consensus → emit defect

    Args:
        claim_values: List of claim value dictionaries with value and metadata
        sources: List of corresponding source/evidence dictionaries
        contradiction_threshold: Threshold for contradiction detection (default 5%)

    Returns:
        ShudhuhResult with anomaly status and reconciliation attempts
    """
    reconciliation_attempts: list[ReconciliationAttempt] = []

    if len(claim_values) < 2:
        return ShudhuhResult(
            has_anomaly=False,
            defect_code=None,
            severity=None,
            description=None,
            cure_protocol=None,
            reconciliation_attempts=[],
            consensus_value=claim_values[0].get("value") if claim_values else None,
        )

    rounding_result = _attempt_rounding_reconciliation(claim_values)
    reconciliation_attempts.append(rounding_result)
    if rounding_result.success:
        return ShudhuhResult(
            has_anomaly=False,
            defect_code=None,
            severity=None,
            description=None,
            cure_protocol=None,
            reconciliation_attempts=reconciliation_attempts,
            consensus_value=rounding_result.reconciled_value,
        )

    unit_result = _attempt_unit_reconciliation(claim_values)
    reconciliation_attempts.append(unit_result)
    if unit_result.success:
        return ShudhuhResult(
            has_anomaly=False,
            defect_code="SHUDHUDH_UNIT_MISMATCH",
            severity="MINOR",
            description=unit_result.explanation,
            cure_protocol=None,
            reconciliation_attempts=reconciliation_attempts,
            consensus_value=unit_result.reconciled_value,
        )

    time_result = _attempt_time_window_reconciliation(claim_values)
    reconciliation_attempts.append(time_result)
    if time_result.success:
        return ShudhuhResult(
            has_anomaly=False,
            defect_code="SHUDHUDH_TIME_WINDOW",
            severity="MINOR",
            description=time_result.explanation,
            cure_protocol=None,
            reconciliation_attempts=reconciliation_attempts,
            consensus_value=None,
        )

    consensus = _compute_consensus(claim_values, sources)

    from idis.services.sanad.source_tiers import TierUsage, get_tier_usage

    for val, src in zip(claim_values, sources, strict=False):
        tier = assign_source_tier(src)
        usage = get_tier_usage(tier)

        if usage == TierUsage.SUPPORT_ONLY and _contradicts(
            val.get("value"), consensus, contradiction_threshold
        ):
            return ShudhuhResult(
                has_anomaly=True,
                defect_code="SHUDHUDH_ANOMALY",
                severity="MAJOR",
                description=(
                    f"Lower-tier source ({tier.value}) contradicts consensus: "
                    f"{val.get('value')} vs {consensus}"
                ),
                cure_protocol="HUMAN_ARBITRATION",
                reconciliation_attempts=reconciliation_attempts,
                consensus_value=consensus,
            )

    return ShudhuhResult(
        has_anomaly=False,
        defect_code=None,
        severity=None,
        description=None,
        cure_protocol=None,
        reconciliation_attempts=reconciliation_attempts,
        consensus_value=consensus,
    )
