"""Sanad Methodology v2 — Evidence chain grading and defect detection.

This package implements the Sanad v2 methodology for IDIS, incorporating
concepts from Ilm al-Hadith adapted for financial evidence verification.

Components:
- source_tiers: Six-level source reliability hierarchy (Jarh wa Tadil)
- dabt: Multi-dimensional precision scoring
- tawatur: Multi-attestation with independence assessment
- shudhudh: Reconciliation-first anomaly detection
- ilal: Hidden defect detection
- coi: Conflict of interest handling
- defects: Unified defect interface
- grader: Integrated grade calculation
"""

from idis.services.sanad.coi import (
    COICureResult,
    COIDefect,
    COIDefectCode,
    COIEvaluationResult,
    COIMetadata,
    COISeverity,
    collect_coi_defects,
    evaluate_all_sources_coi,
    evaluate_coi_cure,
    evaluate_source_coi,
    extract_coi_metadata,
    get_coi_grade_cap,
)
from idis.services.sanad.dabt import (
    DabtDimensions,
    DabtScore,
    calculate_dabt_score,
    extract_dabt_from_sanad,
    get_dabt_grade_impact,
)
from idis.services.sanad.defects import (
    DefectCode,
    DefectResult,
    detect_ilal_chain_break,
    detect_ilal_chain_grafting,
    detect_ilal_chronology_impossible,
    detect_ilal_version_drift,
    detect_shudhudh,
)
from idis.services.sanad.grader import (
    GradeExplanation,
    SanadGradeResult,
    calculate_sanad_grade,
    grade_sanad_v2,
)
from idis.services.sanad.ilal import (
    IlalDefect,
    IlalDefectCode,
    detect_all_ilal,
)
from idis.services.sanad.shudhudh import (
    ReconciliationAttempt,
    ReconciliationType,
    ShudhuhResult,
)
from idis.services.sanad.source_tiers import (
    ConflictInfo,
    SourceTier,
    TierInfo,
    TierUsage,
    assign_source_tier,
    check_tier_admissibility,
    get_tier_usage,
    get_tier_weight,
    is_primary_eligible,
    tier_to_base_grade,
)
from idis.services.sanad.tawatur import (
    IndependenceFactors,
    TawaturResult,
    TawaturType,
    assess_tawatur,
    check_source_independence,
    compute_collusion_risk,
    compute_independence_key,
)

__all__ = [
    "SourceTier",
    "TierUsage",
    "TierInfo",
    "ConflictInfo",
    "assign_source_tier",
    "get_tier_weight",
    "get_tier_usage",
    "tier_to_base_grade",
    "is_primary_eligible",
    "check_tier_admissibility",
    "DabtScore",
    "DabtDimensions",
    "calculate_dabt_score",
    "extract_dabt_from_sanad",
    "get_dabt_grade_impact",
    "TawaturType",
    "TawaturResult",
    "IndependenceFactors",
    "assess_tawatur",
    "compute_independence_key",
    "compute_collusion_risk",
    "check_source_independence",
    "ReconciliationType",
    "ReconciliationAttempt",
    "ShudhuhResult",
    "detect_shudhudh",
    "IlalDefect",
    "IlalDefectCode",
    "detect_ilal_version_drift",
    "detect_ilal_chain_break",
    "detect_ilal_chain_grafting",
    "detect_ilal_chronology_impossible",
    "detect_all_ilal",
    "DefectCode",
    "DefectResult",
    "COISeverity",
    "COIMetadata",
    "COIDefect",
    "COIDefectCode",
    "COICureResult",
    "COIEvaluationResult",
    "extract_coi_metadata",
    "evaluate_coi_cure",
    "evaluate_source_coi",
    "evaluate_all_sources_coi",
    "get_coi_grade_cap",
    "collect_coi_defects",
    "GradeExplanation",
    "SanadGradeResult",
    "calculate_sanad_grade",
    "grade_sanad_v2",
]
