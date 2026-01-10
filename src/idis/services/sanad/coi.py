"""COI (Conflict of Interest) Handling — Deterministic rules and cure protocols.

Implements conflict of interest detection, grade impacts, and cure evaluation.
All rules are deterministic and fail-closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class COISeverity(Enum):
    """Conflict of interest severity levels."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class COIDefectCode(Enum):
    """COI-related defect codes."""

    COI_HIGH_UNDISCLOSED = "COI_HIGH_UNDISCLOSED"
    COI_HIGH_UNCURED = "COI_HIGH_UNCURED"
    COI_DISCLOSURE_MISSING = "COI_DISCLOSURE_MISSING"


@dataclass
class COIMetadata:
    """Conflict of interest metadata for a source."""

    coi_present: bool = False
    coi_severity: COISeverity | str | None = None
    coi_disclosed: bool = False
    coi_type: str | None = None
    coi_description: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> COIMetadata:
        """Create COIMetadata from dictionary."""
        if not data:
            return cls()

        severity = data.get("coi_severity")
        if isinstance(severity, str):
            try:
                severity = COISeverity(severity.upper())
            except ValueError:
                severity = None

        return cls(
            coi_present=bool(data.get("coi_present", False)),
            coi_severity=severity,
            coi_disclosed=bool(data.get("coi_disclosed", False)),
            coi_type=data.get("coi_type"),
            coi_description=data.get("coi_description"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary representation."""
        severity_str = None
        if self.coi_severity:
            severity_str = (
                self.coi_severity.value
                if isinstance(self.coi_severity, COISeverity)
                else str(self.coi_severity)
            )

        return {
            "coi_present": self.coi_present,
            "coi_severity": severity_str,
            "coi_disclosed": self.coi_disclosed,
            "coi_type": self.coi_type,
            "coi_description": self.coi_description,
        }


@dataclass
class COIDefect:
    """COI-related defect."""

    code: COIDefectCode
    severity: str
    description: str
    cure_protocol: str
    metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "code": self.code.value,
            "severity": self.severity,
            "description": self.description,
            "cure_protocol": self.cure_protocol,
            "metadata": self.metadata or {},
        }


@dataclass
class COICureResult:
    """Result of COI cure evaluation."""

    cured: bool
    reason: str
    grade_cap: str | None = None
    requires_additional_corroboration: bool = False
    curing_sources: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "cured": self.cured,
            "reason": self.reason,
            "grade_cap": self.grade_cap,
            "requires_additional_corroboration": self.requires_additional_corroboration,
            "curing_sources": self.curing_sources,
        }


@dataclass
class COIEvaluationResult:
    """Complete COI evaluation result for a source."""

    source_id: str
    coi_metadata: COIMetadata
    defect: COIDefect | None
    cure_result: COICureResult

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "source_id": self.source_id,
            "coi_metadata": self.coi_metadata.to_dict(),
            "defect": self.defect.to_dict() if self.defect else None,
            "cure_result": self.cure_result.to_dict(),
        }


def extract_coi_metadata(source: dict[str, Any]) -> COIMetadata:
    """Extract COI metadata from source dictionary.

    Looks for coi_metadata nested object or top-level coi_* fields.

    Args:
        source: Evidence item dictionary

    Returns:
        COIMetadata object
    """
    coi_data = source.get("coi_metadata")
    if coi_data and isinstance(coi_data, dict):
        return COIMetadata.from_dict(coi_data)

    severity = source.get("coi_severity")
    if isinstance(severity, str):
        try:
            severity = COISeverity(severity.upper())
        except ValueError:
            severity = None

    return COIMetadata(
        coi_present=bool(source.get("coi_present", False)),
        coi_severity=severity,
        coi_disclosed=bool(source.get("coi_disclosed", False)),
        coi_type=source.get("coi_type"),
        coi_description=source.get("coi_description"),
    )


def _get_severity_enum(severity: COISeverity | str | None) -> COISeverity | None:
    """Convert severity to enum."""
    if severity is None:
        return None
    if isinstance(severity, COISeverity):
        return severity
    try:
        return COISeverity(str(severity).upper())
    except ValueError:
        return None


def _is_high_tier_source(source: dict[str, Any]) -> bool:
    """Check if source is high-tier (ATHBAT_AL_NAS or THIQAH_THABIT)."""
    from idis.services.sanad.source_tiers import SourceTier, assign_source_tier

    tier = assign_source_tier(source)
    return tier in {SourceTier.ATHBAT_AL_NAS, SourceTier.THIQAH_THABIT}


def _source_has_coi(source: dict[str, Any]) -> bool:
    """Check if source has COI present."""
    coi = extract_coi_metadata(source)
    return coi.coi_present


def evaluate_coi_cure(
    source: dict[str, Any],
    corroborating_sources: list[dict[str, Any]],
    tawatur_result: Any | None = None,
) -> COICureResult:
    """Evaluate if COI can be cured by independent corroboration.

    RULES:
    - HIGH + undisclosed → grade cap at C unless cured by independent high-tier
    - HIGH + disclosed → requires MUTAWATIR but not automatic block
    - MEDIUM/LOW → no cure needed

    Args:
        source: Primary evidence item with potential COI
        corroborating_sources: Other evidence items for corroboration
        tawatur_result: Optional TawaturResult for independence check

    Returns:
        COICureResult with cure status and requirements
    """
    coi = extract_coi_metadata(source)

    if not coi.coi_present:
        return COICureResult(cured=True, reason="No COI present")

    severity = _get_severity_enum(coi.coi_severity)

    if severity is None:
        return COICureResult(
            cured=False,
            reason="COI present but severity not specified - fail closed",
            grade_cap="C",
        )

    if severity == COISeverity.LOW:
        return COICureResult(cured=True, reason="LOW COI does not require cure")

    if severity == COISeverity.MEDIUM:
        if not coi.coi_disclosed:
            return COICureResult(
                cured=True,
                reason="MEDIUM undisclosed COI - warning flag only",
            )
        return COICureResult(cured=True, reason="MEDIUM disclosed COI - no penalty")

    source_id = source.get("evidence_id", "UNKNOWN")
    high_tier_independent = [
        s
        for s in corroborating_sources
        if s.get("evidence_id") != source_id and _is_high_tier_source(s) and not _source_has_coi(s)
    ]

    if severity == COISeverity.HIGH and not coi.coi_disclosed:
        if high_tier_independent:
            independence_pass = True
            if tawatur_result is not None:
                independence_pass = getattr(tawatur_result, "independence_pass", True)

            if independence_pass:
                return COICureResult(
                    cured=True,
                    reason=f"Cured by {len(high_tier_independent)} independent high-tier sources",
                    curing_sources=[s.get("evidence_id", "?") for s in high_tier_independent],
                )

        return COICureResult(
            cured=False,
            reason="HIGH undisclosed COI requires independent high-tier corroboration",
            grade_cap="C",
        )

    if severity == COISeverity.HIGH and coi.coi_disclosed:
        if tawatur_result is not None:
            from idis.services.sanad.tawatur import TawaturType

            status = getattr(tawatur_result, "status", None)
            if status == TawaturType.MUTAWATIR:
                return COICureResult(
                    cured=True,
                    reason="HIGH disclosed COI cured by MUTAWATIR corroboration",
                )

        if len(high_tier_independent) >= 2:
            return COICureResult(
                cured=True,
                reason=(
                    f"HIGH disclosed COI cured by {len(high_tier_independent)} high-tier sources"
                ),
                curing_sources=[s.get("evidence_id", "?") for s in high_tier_independent],
            )

        return COICureResult(
            cured=False,
            reason="HIGH disclosed COI requires MUTAWATIR or multiple high-tier corroboration",
            requires_additional_corroboration=True,
        )

    return COICureResult(cured=True, reason="COI evaluation passed")


def evaluate_source_coi(
    source: dict[str, Any],
    corroborating_sources: list[dict[str, Any]],
    tawatur_result: Any | None = None,
) -> COIEvaluationResult:
    """Evaluate COI for a single source with full result.

    Args:
        source: Evidence item to evaluate
        corroborating_sources: Other evidence items for corroboration
        tawatur_result: Optional TawaturResult

    Returns:
        COIEvaluationResult with metadata, defect (if any), and cure status
    """
    source_id = source.get("evidence_id", "UNKNOWN")
    coi = extract_coi_metadata(source)
    cure_result = evaluate_coi_cure(source, corroborating_sources, tawatur_result)

    defect: COIDefect | None = None

    if coi.coi_present:
        severity = _get_severity_enum(coi.coi_severity)

        if severity == COISeverity.HIGH and not coi.coi_disclosed:
            if not cure_result.cured:
                defect = COIDefect(
                    code=COIDefectCode.COI_HIGH_UNDISCLOSED,
                    severity="MAJOR",
                    description=(
                        f"Source {source_id} has HIGH undisclosed COI "
                        f"without sufficient independent corroboration"
                    ),
                    cure_protocol="REQUIRE_INDEPENDENT_CORROBORATION",
                    metadata={
                        "source_id": source_id,
                        "coi_type": coi.coi_type,
                        "coi_description": coi.coi_description,
                    },
                )

        elif severity == COISeverity.HIGH and coi.coi_disclosed:
            if not cure_result.cured:
                defect = COIDefect(
                    code=COIDefectCode.COI_HIGH_UNCURED,
                    severity="MAJOR",
                    description=(
                        f"Source {source_id} has HIGH disclosed COI without MUTAWATIR corroboration"
                    ),
                    cure_protocol="REQUIRE_MUTAWATIR_CORROBORATION",
                    metadata={
                        "source_id": source_id,
                        "coi_type": coi.coi_type,
                        "coi_description": coi.coi_description,
                    },
                )

        elif coi.coi_present and coi.coi_severity is None:
            defect = COIDefect(
                code=COIDefectCode.COI_DISCLOSURE_MISSING,
                severity="MINOR",
                description=f"Source {source_id} has COI but severity not specified",
                cure_protocol="REQUEST_COI_DETAILS",
                metadata={"source_id": source_id},
            )

    return COIEvaluationResult(
        source_id=source_id,
        coi_metadata=coi,
        defect=defect,
        cure_result=cure_result,
    )


def evaluate_all_sources_coi(
    sources: list[dict[str, Any]],
    tawatur_result: Any | None = None,
) -> list[COIEvaluationResult]:
    """Evaluate COI for all sources in a set.

    Args:
        sources: List of evidence items
        tawatur_result: Optional TawaturResult

    Returns:
        List of COIEvaluationResult for each source
    """
    results: list[COIEvaluationResult] = []

    for source in sources:
        other_sources = [s for s in sources if s.get("evidence_id") != source.get("evidence_id")]
        result = evaluate_source_coi(source, other_sources, tawatur_result)
        results.append(result)

    return results


def get_coi_grade_cap(evaluations: list[COIEvaluationResult]) -> str | None:
    """Get strictest grade cap from COI evaluations.

    Args:
        evaluations: List of COIEvaluationResult

    Returns:
        Grade cap string (e.g., "C") or None if no cap
    """
    grade_order = {"A": 0, "B": 1, "C": 2, "D": 3}
    strictest_cap: str | None = None

    for evaluation in evaluations:
        cap = evaluation.cure_result.grade_cap
        if cap and (
            strictest_cap is None or grade_order.get(cap, 0) > grade_order.get(strictest_cap, 0)
        ):
            strictest_cap = cap

    return strictest_cap


def collect_coi_defects(evaluations: list[COIEvaluationResult]) -> list[COIDefect]:
    """Collect all COI defects from evaluations.

    Args:
        evaluations: List of COIEvaluationResult

    Returns:
        List of COIDefect objects
    """
    return [e.defect for e in evaluations if e.defect is not None]
