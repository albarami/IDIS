"""Tawatur (Multi-Attestation) — Independence assessment and collusion detection.

Implements deterministic independence checking and collusion risk scoring.
All assessments are fail-closed: uncertain independence treated as dependent.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any


class TawaturType(Enum):
    """Corroboration status classification."""

    NONE = "NONE"
    AHAD_1 = "AHAD_1"
    AHAD_2 = "AHAD_2"
    MUTAWATIR = "MUTAWATIR"


@dataclass
class TawaturResult:
    """Result of Tawatur (multi-attestation) assessment."""

    status: TawaturType
    independent_count: int
    total_sources: int
    collusion_risk: float
    independence_pass: bool
    independence_keys: list[str]
    grouped_sources: dict[str, list[str]]
    explanation: str

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "status": self.status.value,
            "independent_count": self.independent_count,
            "total_sources": self.total_sources,
            "collusion_risk": self.collusion_risk,
            "independence_pass": self.independence_pass,
            "independence_keys": self.independence_keys,
            "grouped_sources": self.grouped_sources,
            "explanation": self.explanation,
        }


@dataclass
class IndependenceFactors:
    """Factors used in independence key computation."""

    source_system: str | None = None
    upstream_origin_id: str | None = None
    artifact_id: str | None = None
    timestamp: datetime | str | None = None
    preparer_id: str | None = None


MUTAWATIR_THRESHOLD = 3
COLLUSION_RISK_THRESHOLD = 0.30
DEFAULT_TIME_BUCKET_HOURS = 1


def _time_bucket(
    timestamp: datetime | str | None,
    bucket_hours: int = DEFAULT_TIME_BUCKET_HOURS,
) -> str:
    """Compute time bucket for timestamp.

    Args:
        timestamp: Timestamp to bucket
        bucket_hours: Bucket size in hours

    Returns:
        Time bucket string (ISO date + hour bucket)
    """
    if timestamp is None:
        return "UNKNOWN_TIME"

    if isinstance(timestamp, str):
        try:
            timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return "INVALID_TIME"

    bucket_start = timestamp.replace(
        minute=0,
        second=0,
        microsecond=0,
        hour=(timestamp.hour // bucket_hours) * bucket_hours,
    )
    return bucket_start.isoformat()


def compute_independence_key(
    source: dict[str, Any] | IndependenceFactors,
    *,
    bucket_hours: int = DEFAULT_TIME_BUCKET_HOURS,
) -> str:
    """Compute deterministic independence key for source grouping.

    Sources with the same independence key are considered dependent.

    FAIL-CLOSED: Missing fields use evidence_id or "UNKNOWN" markers.

    Args:
        source: Evidence item dictionary or IndependenceFactors
        bucket_hours: Time bucket size in hours

    Returns:
        Independence key string
    """
    if isinstance(source, IndependenceFactors):
        factors = source
        source_dict: dict[str, Any] = {}
    else:
        source_dict = source
        factors = IndependenceFactors(
            source_system=source_dict.get("source_system"),
            upstream_origin_id=source_dict.get("upstream_origin_id"),
            artifact_id=source_dict.get("artifact_id"),
            timestamp=source_dict.get("timestamp"),
            preparer_id=source_dict.get("preparer_id"),
        )

    evidence_id = source_dict.get("evidence_id", "NO_ID")

    components = [
        factors.source_system or "UNKNOWN_SYSTEM",
        factors.upstream_origin_id or evidence_id,
        factors.artifact_id or "NO_ARTIFACT",
        _time_bucket(factors.timestamp, bucket_hours),
    ]

    return "|".join(str(c) for c in components)


def _compute_time_clustering(timestamps: list[datetime | str | None]) -> float:
    """Compute time clustering factor for collusion risk.

    High clustering (sources created very close together) increases risk.

    Args:
        timestamps: List of source timestamps

    Returns:
        Clustering factor [0.0, 1.0] where 1.0 = high clustering
    """
    valid_times: list[datetime] = []
    for ts in timestamps:
        if ts is None:
            continue
        if isinstance(ts, str):
            try:
                valid_times.append(datetime.fromisoformat(ts.replace("Z", "+00:00")))
            except (ValueError, TypeError):
                continue
        else:
            valid_times.append(ts)

    if len(valid_times) < 2:
        return 0.0

    valid_times.sort()
    total_span = (valid_times[-1] - valid_times[0]).total_seconds()

    if total_span == 0:
        return 1.0

    min_gap = min(
        (valid_times[i + 1] - valid_times[i]).total_seconds() for i in range(len(valid_times) - 1)
    )

    if min_gap < 60:
        return 0.9
    elif min_gap < 300:
        return 0.7
    elif min_gap < 3600:
        return 0.4
    elif min_gap < 86400:
        return 0.2
    else:
        return 0.1


def _compute_chain_overlap(sources: list[dict[str, Any]]) -> float:
    """Compute chain overlap factor for collusion risk.

    Sources sharing transmission chain nodes suggest dependency.

    Args:
        sources: List of evidence item dictionaries

    Returns:
        Overlap factor [0.0, 1.0] where 1.0 = high overlap
    """
    all_node_ids: list[set[str]] = []

    for source in sources:
        chain = source.get("transmission_chain", [])
        if isinstance(chain, list):
            node_ids = {
                str(n.get("node_id", "")) for n in chain if isinstance(n, dict) and n.get("node_id")
            }
            all_node_ids.append(node_ids)
        else:
            all_node_ids.append(set())

    if len(all_node_ids) < 2:
        return 0.0

    overlap_count = 0
    pair_count = 0

    for i in range(len(all_node_ids)):
        for j in range(i + 1, len(all_node_ids)):
            pair_count += 1
            if all_node_ids[i] & all_node_ids[j]:
                overlap_count += 1

    if pair_count == 0:
        return 0.0

    return overlap_count / pair_count


def compute_collusion_risk(sources: list[dict[str, Any]]) -> float:
    """Compute deterministic collusion risk score.

    Factors:
    - Source system concentration (same system = higher risk)
    - Time clustering (created close together = higher risk)
    - Chain overlap (shared transmission nodes = higher risk)

    Args:
        sources: List of evidence item dictionaries

    Returns:
        Collusion risk score [0.0, 1.0]
    """
    if len(sources) <= 1:
        return 0.0

    systems = [s.get("source_system", "UNKNOWN") for s in sources]
    system_counts = Counter(systems)
    max_concentration = max(system_counts.values())
    system_concentration = max_concentration / len(sources)

    timestamps = [s.get("timestamp") for s in sources]
    time_cluster_factor = _compute_time_clustering(timestamps)

    chain_overlap_factor = _compute_chain_overlap(sources)

    collusion_risk = (
        0.40 * system_concentration + 0.30 * time_cluster_factor + 0.30 * chain_overlap_factor
    )

    return round(min(1.0, collusion_risk), 4)


def assess_tawatur(
    sources: list[dict[str, Any]],
    *,
    mutawatir_threshold: int = MUTAWATIR_THRESHOLD,
    collusion_threshold: float = COLLUSION_RISK_THRESHOLD,
    bucket_hours: int = DEFAULT_TIME_BUCKET_HOURS,
) -> TawaturResult:
    """Assess Tawatur (multi-attestation) status for a set of sources.

    RULES:
    - MUTAWATIR requires independent_count >= threshold AND collusion_risk <= threshold
    - High collusion_risk (> threshold) downgrades MUTAWATIR to AHAD_2
    - Independence assessment is deterministic and auditable

    Args:
        sources: List of evidence item dictionaries
        mutawatir_threshold: Minimum independent sources for MUTAWATIR
        collusion_threshold: Maximum collusion risk for MUTAWATIR
        bucket_hours: Time bucket size for independence key

    Returns:
        TawaturResult with status, counts, and diagnostics
    """
    if not sources:
        return TawaturResult(
            status=TawaturType.NONE,
            independent_count=0,
            total_sources=0,
            collusion_risk=0.0,
            independence_pass=False,
            independence_keys=[],
            grouped_sources={},
            explanation="No sources provided",
        )

    grouped: dict[str, list[str]] = {}
    keys: list[str] = []

    for source in sources:
        key = compute_independence_key(source, bucket_hours=bucket_hours)
        evidence_id = source.get("evidence_id", "UNKNOWN")

        if key not in grouped:
            grouped[key] = []
            keys.append(key)

        grouped[key].append(evidence_id)

    independent_count = len(grouped)
    collusion_risk = compute_collusion_risk(sources)
    independence_pass = collusion_risk <= collusion_threshold

    if independent_count == 0:
        status = TawaturType.NONE
        explanation = "No independent sources identified"
    elif independent_count == 1:
        status = TawaturType.AHAD_1
        explanation = "Single independent attestation"
    elif independent_count == 2:
        status = TawaturType.AHAD_2
        explanation = "Two independent attestations"
    elif independent_count >= mutawatir_threshold:
        if independence_pass:
            status = TawaturType.MUTAWATIR
            explanation = (
                f"{independent_count} independent attestations with "
                f"acceptable collusion risk ({collusion_risk:.2f})"
            )
        else:
            status = TawaturType.AHAD_2
            explanation = (
                f"{independent_count} attestations but high collusion risk "
                f"({collusion_risk:.2f} > {collusion_threshold}) downgrades to AHAD_2"
            )
    else:
        status = TawaturType.AHAD_2
        explanation = f"{independent_count} independent attestations (below MUTAWATIR threshold)"

    return TawaturResult(
        status=status,
        independent_count=independent_count,
        total_sources=len(sources),
        collusion_risk=collusion_risk,
        independence_pass=independence_pass,
        independence_keys=keys,
        grouped_sources=grouped,
        explanation=explanation,
    )


def check_source_independence(
    source_a: dict[str, Any],
    source_b: dict[str, Any],
    *,
    bucket_hours: int = DEFAULT_TIME_BUCKET_HOURS,
) -> tuple[bool, str]:
    """Check if two sources are independent.

    Args:
        source_a: First evidence item
        source_b: Second evidence item
        bucket_hours: Time bucket size

    Returns:
        Tuple of (is_independent, reason)
    """
    key_a = compute_independence_key(source_a, bucket_hours=bucket_hours)
    key_b = compute_independence_key(source_b, bucket_hours=bucket_hours)

    if key_a == key_b:
        return (False, f"Same independence key: {key_a}")

    origin_a = source_a.get("upstream_origin_id")
    origin_b = source_b.get("upstream_origin_id")
    if origin_a and origin_b and origin_a == origin_b:
        return (False, f"Same upstream_origin_id: {origin_a}")

    return (True, "Sources have different independence keys")
