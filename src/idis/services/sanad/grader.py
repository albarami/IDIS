"""Sanad Grader v2 — Integrated grade calculation with all methodology enhancements.

Combines source tiers, Dabt, Tawatur, Shudhudh, I'lal, and COI into unified grading.
All grading is deterministic and fail-closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from idis.services.sanad.coi import (
    COIDefect,
    COIEvaluationResult,
    collect_coi_defects,
    evaluate_all_sources_coi,
    get_coi_grade_cap,
)
from idis.services.sanad.dabt import DabtScore, calculate_dabt_score, extract_dabt_from_sanad
from idis.services.sanad.ilal import IlalDefect, detect_all_ilal
from idis.services.sanad.shudhudh import ShudhuhResult, detect_shudhudh
from idis.services.sanad.source_tiers import (
    SourceTier,
    assign_source_tier,
    check_tier_admissibility,
    tier_to_base_grade,
)
from idis.services.sanad.tawatur import TawaturResult, TawaturType, assess_tawatur

GRADE_ORDER = {"A": 0, "B": 1, "C": 2, "D": 3}


@dataclass
class DefectSummary:
    """Summary of a defect for grade explanation."""

    code: str
    severity: str
    description: str

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "code": self.code,
            "severity": self.severity,
            "description": self.description,
        }


@dataclass
class GradeExplanation:
    """Detailed explanation of grade calculation."""

    base_grade: str
    source_tier: str
    tier_weight: float
    dabt_score: float
    dabt_quality: str
    tawatur_status: str
    independent_count: int
    collusion_risk: float
    fatal_defects: list[DefectSummary]
    major_defects: list[DefectSummary]
    minor_defects: list[DefectSummary]
    grade_caps: list[str]
    upgrades_applied: list[str]
    downgrades_applied: list[str]
    final_grade: str
    summary: str

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "base_grade": self.base_grade,
            "source_tier": self.source_tier,
            "tier_weight": self.tier_weight,
            "dabt_score": self.dabt_score,
            "dabt_quality": self.dabt_quality,
            "tawatur_status": self.tawatur_status,
            "independent_count": self.independent_count,
            "collusion_risk": self.collusion_risk,
            "fatal_defects": [d.to_dict() for d in self.fatal_defects],
            "major_defects": [d.to_dict() for d in self.major_defects],
            "minor_defects": [d.to_dict() for d in self.minor_defects],
            "grade_caps": self.grade_caps,
            "upgrades_applied": self.upgrades_applied,
            "downgrades_applied": self.downgrades_applied,
            "final_grade": self.final_grade,
            "summary": self.summary,
        }


@dataclass
class SanadGradeResult:
    """Complete result of Sanad grading."""

    grade: str
    explanation: GradeExplanation
    source_tier: SourceTier
    dabt: DabtScore
    tawatur: TawaturResult
    shudhudh: ShudhuhResult | None
    ilal_defects: list[IlalDefect]
    coi_evaluations: list[COIEvaluationResult]
    all_defects: list[DefectSummary]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "grade": self.grade,
            "explanation": self.explanation.to_dict(),
            "source_tier": self.source_tier.value,
            "dabt": self.dabt.to_dict(),
            "tawatur": self.tawatur.to_dict(),
            "shudhudh": self.shudhudh.to_dict() if self.shudhudh else None,
            "ilal_defects": [d.to_dict() for d in self.ilal_defects],
            "coi_evaluations": [e.to_dict() for e in self.coi_evaluations],
            "all_defects": [d.to_dict() for d in self.all_defects],
        }


def _grade_order(grade: str) -> int:
    """Get numeric order for grade (lower is better)."""
    return GRADE_ORDER.get(grade.upper(), 3)


def _downgrade(grade: str) -> str:
    """Downgrade by one level (A→B→C→D)."""
    order = _grade_order(grade)
    if order >= 3:
        return "D"
    return ["A", "B", "C", "D"][order + 1]


def _upgrade(grade: str) -> str:
    """Upgrade by one level (D→C→B→A)."""
    order = _grade_order(grade)
    if order <= 0:
        return "A"
    return ["A", "B", "C", "D"][order - 1]


def _apply_grade_cap(grade: str, cap: str) -> str:
    """Apply grade cap (cannot be better than cap)."""
    if _grade_order(grade) < _grade_order(cap):
        return cap
    return grade


def _collect_defect_summaries(
    ilal_defects: list[IlalDefect],
    coi_defects: list[COIDefect],
    shudhudh: ShudhuhResult | None,
) -> tuple[list[DefectSummary], list[DefectSummary], list[DefectSummary]]:
    """Collect and categorize all defects by severity."""
    fatal: list[DefectSummary] = []
    major: list[DefectSummary] = []
    minor: list[DefectSummary] = []

    for defect in ilal_defects:
        summary = DefectSummary(
            code=defect.code.value,
            severity=defect.severity,
            description=defect.description,
        )
        if defect.severity == "FATAL":
            fatal.append(summary)
        elif defect.severity == "MAJOR":
            major.append(summary)
        else:
            minor.append(summary)

    for defect in coi_defects:
        summary = DefectSummary(
            code=defect.code.value,
            severity=defect.severity,
            description=defect.description,
        )
        if defect.severity == "FATAL":
            fatal.append(summary)
        elif defect.severity == "MAJOR":
            major.append(summary)
        else:
            minor.append(summary)

    if shudhudh and shudhudh.has_anomaly and shudhudh.defect_code:
        summary = DefectSummary(
            code=shudhudh.defect_code,
            severity=shudhudh.severity or "MAJOR",
            description=shudhudh.description or "Shudhudh anomaly detected",
        )
        if shudhudh.severity == "FATAL":
            fatal.append(summary)
        elif shudhudh.severity == "MAJOR":
            major.append(summary)
        else:
            minor.append(summary)

    return fatal, major, minor


def calculate_sanad_grade(
    sanad: dict[str, Any],
    sources: list[dict[str, Any]] | None = None,
    claim: dict[str, Any] | None = None,
    documents: list[dict[str, Any]] | None = None,
    evidence_ids: set[str] | None = None,
) -> SanadGradeResult:
    """Calculate Sanad grade using v2 methodology.

    ALGORITHM:
    1. Assign source tier → base grade
    2. Calculate Dabt score → potential grade cap
    3. Assess Tawatur → potential upgrade
    4. Detect I'lal defects → FATAL forces D, MAJOR downgrades
    5. Detect Shudhudh → MAJOR downgrades
    6. Evaluate COI → potential grade cap and defects
    7. Apply all modifiers deterministically

    FAIL-CLOSED:
    - Missing data → worst-case assumptions
    - Any FATAL defect → Grade D
    - Unknown source → lowest tier

    Args:
        sanad: Sanad dictionary with transmission_chain, primary_evidence, etc.
        sources: List of evidence item dictionaries
        claim: Optional claim dictionary for shudhudh/version drift
        documents: Optional document list for version drift detection
        evidence_ids: Optional set of valid evidence IDs

    Returns:
        SanadGradeResult with grade, explanation, and all component results
    """
    sources = sources or []
    upgrades: list[str] = []
    downgrades: list[str] = []
    grade_caps: list[str] = []

    primary_source = sanad.get("primary_source") or {}
    if not primary_source and sources:
        primary_source = sources[0]

    source_tier = assign_source_tier(primary_source)
    base_grade = tier_to_base_grade(source_tier)

    if claim:
        materiality = claim.get("materiality", "MEDIUM")
        admissible, reason = check_tier_admissibility(source_tier, materiality)
        if not admissible:
            grade_caps.append("C")

    dabt_dims = extract_dabt_from_sanad(sanad)
    dabt = calculate_dabt_score(dabt_dims)
    if dabt.score < 0.50:
        grade_caps.append("B")

    tawatur = assess_tawatur(sources) if sources else assess_tawatur([primary_source])

    ilal_defects = detect_all_ilal(
        sanad,
        claim=claim,
        documents=documents,
        evidence_ids=evidence_ids,
    )

    claim_values = []
    if claim:
        values = claim.get("values") or claim.get("value_struct")
        if values:
            if isinstance(values, list):
                claim_values = [{"value": v} if not isinstance(v, dict) else v for v in values]
            else:
                claim_values = [{"value": values}]
        elif sources:
            for src in sources:
                val = src.get("extracted_value") or src.get("value")
                if val is not None:
                    claim_values.append({"value": val, "source": src})

    shudhudh: ShudhuhResult | None = None
    if len(claim_values) >= 2 and sources:
        shudhudh = detect_shudhudh(claim_values, sources)

    coi_evaluations = evaluate_all_sources_coi(sources, tawatur) if sources else []
    coi_defects = collect_coi_defects(coi_evaluations)
    coi_cap = get_coi_grade_cap(coi_evaluations)
    if coi_cap:
        grade_caps.append(coi_cap)

    fatal, major, minor = _collect_defect_summaries(ilal_defects, coi_defects, shudhudh)
    all_defects = fatal + major + minor

    if fatal:
        final_grade = "D"
        summary = f"Grade D forced by {len(fatal)} FATAL defect(s): {fatal[0].code}"
    else:
        grade = base_grade

        for defect in major:
            grade = _downgrade(grade)
            downgrades.append(f"MAJOR defect {defect.code}")

        if not major and tawatur.status == TawaturType.MUTAWATIR:
            grade = _upgrade(grade)
            upgrades.append("MUTAWATIR corroboration upgrade")

        for cap in grade_caps:
            if _grade_order(grade) < _grade_order(cap):
                grade = cap
                downgrades.append(f"Grade cap applied: {cap}")

        final_grade = grade

        if downgrades:
            summary = f"Grade {final_grade} after {len(downgrades)} adjustment(s)"
        elif upgrades:
            summary = f"Grade {final_grade} with {len(upgrades)} upgrade(s)"
        else:
            summary = f"Grade {final_grade} from base {base_grade}"

    from idis.services.sanad.source_tiers import get_tier_weight

    explanation = GradeExplanation(
        base_grade=base_grade,
        source_tier=source_tier.value,
        tier_weight=get_tier_weight(source_tier),
        dabt_score=dabt.score,
        dabt_quality=dabt.quality_band,
        tawatur_status=tawatur.status.value,
        independent_count=tawatur.independent_count,
        collusion_risk=tawatur.collusion_risk,
        fatal_defects=fatal,
        major_defects=major,
        minor_defects=minor,
        grade_caps=grade_caps,
        upgrades_applied=upgrades,
        downgrades_applied=downgrades,
        final_grade=final_grade,
        summary=summary,
    )

    return SanadGradeResult(
        grade=final_grade,
        explanation=explanation,
        source_tier=source_tier,
        dabt=dabt,
        tawatur=tawatur,
        shudhudh=shudhudh,
        ilal_defects=ilal_defects,
        coi_evaluations=coi_evaluations,
        all_defects=all_defects,
    )


def grade_sanad_v2(
    sanad: dict[str, Any],
    sources: list[dict[str, Any]] | None = None,
    claim: dict[str, Any] | None = None,
    documents: list[dict[str, Any]] | None = None,
    evidence_ids: set[str] | None = None,
) -> SanadGradeResult:
    """Public API for Sanad v2 grading.

    Alias for calculate_sanad_grade with identical signature.
    """
    return calculate_sanad_grade(
        sanad=sanad,
        sources=sources,
        claim=claim,
        documents=documents,
        evidence_ids=evidence_ids,
    )
