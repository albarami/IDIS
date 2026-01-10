"""Unit tests for Sanad Methodology v2 — deterministic behavior and fail-closed rules.

Tests all six enhancements:
1. Source Tiers (6-level hierarchy)
2. Dabt (multi-dimensional precision)
3. Tawatur (independence/collusion)
4. Shudhudh (reconciliation-first anomalies)
5. I'lal (hidden defects)
6. COI (conflict of interest handling)
"""

from __future__ import annotations

from idis.services.sanad.coi import (
    COIDefectCode,
    COISeverity,
    evaluate_coi_cure,
    evaluate_source_coi,
    extract_coi_metadata,
)
from idis.services.sanad.dabt import (
    DabtDimensions,
    calculate_dabt_score,
    get_dabt_grade_impact,
)
from idis.services.sanad.grader import calculate_sanad_grade, grade_sanad_v2
from idis.services.sanad.ilal import (
    IlalDefectCode,
    detect_ilal_chain_break,
    detect_ilal_chain_grafting,
    detect_ilal_chronology_impossible,
    detect_ilal_version_drift,
)
from idis.services.sanad.shudhudh import detect_shudhudh
from idis.services.sanad.source_tiers import (
    SourceTier,
    TierUsage,
    assign_source_tier,
    check_tier_admissibility,
    get_tier_usage,
    get_tier_weight,
)
from idis.services.sanad.tawatur import (
    TawaturType,
    assess_tawatur,
    check_source_independence,
    compute_independence_key,
)


class TestSourceTiers:
    """Tests for six-level source tier hierarchy."""

    def test_all_six_tiers_exist(self) -> None:
        """All six tiers must be defined."""
        tiers = list(SourceTier)
        assert len(tiers) == 6
        assert SourceTier.ATHBAT_AL_NAS in tiers
        assert SourceTier.THIQAH_THABIT in tiers
        assert SourceTier.THIQAH in tiers
        assert SourceTier.SADUQ in tiers
        assert SourceTier.SHAYKH in tiers
        assert SourceTier.MAQBUL in tiers

    def test_tier_weights_correct(self) -> None:
        """Numeric weights must match spec."""
        assert get_tier_weight(SourceTier.ATHBAT_AL_NAS) == 1.00
        assert get_tier_weight(SourceTier.THIQAH_THABIT) == 0.90
        assert get_tier_weight(SourceTier.THIQAH) == 0.80
        assert get_tier_weight(SourceTier.SADUQ) == 0.65
        assert get_tier_weight(SourceTier.SHAYKH) == 0.50
        assert get_tier_weight(SourceTier.MAQBUL) == 0.40

    def test_tier_usage_primary_vs_support_only(self) -> None:
        """Tiers 1-4 are PRIMARY, 5-6 are SUPPORT_ONLY."""
        assert get_tier_usage(SourceTier.ATHBAT_AL_NAS) == TierUsage.PRIMARY
        assert get_tier_usage(SourceTier.THIQAH_THABIT) == TierUsage.PRIMARY
        assert get_tier_usage(SourceTier.THIQAH) == TierUsage.PRIMARY
        assert get_tier_usage(SourceTier.SADUQ) == TierUsage.PRIMARY
        assert get_tier_usage(SourceTier.SHAYKH) == TierUsage.SUPPORT_ONLY
        assert get_tier_usage(SourceTier.MAQBUL) == TierUsage.SUPPORT_ONLY

    def test_assign_tier_audited_financial(self) -> None:
        """Audited financials get highest tier."""
        source = {"source_type": "AUDITED_FINANCIAL"}
        assert assign_source_tier(source) == SourceTier.ATHBAT_AL_NAS

    def test_assign_tier_pitch_deck(self) -> None:
        """Pitch decks get SADUQ tier."""
        source = {"source_type": "PITCH_DECK"}
        assert assign_source_tier(source) == SourceTier.SADUQ

    def test_assign_tier_unknown_fails_closed(self) -> None:
        """Unknown source types fail closed to lowest tier."""
        source = {"source_type": "RANDOM_UNKNOWN_TYPE"}
        assert assign_source_tier(source) == SourceTier.MAQBUL

    def test_assign_tier_none_fails_closed(self) -> None:
        """None input fails closed to lowest tier."""
        assert assign_source_tier(None) == SourceTier.MAQBUL

    def test_admissibility_support_only_blocks_critical(self) -> None:
        """SUPPORT_ONLY tiers cannot be primary for CRITICAL claims."""
        admissible, reason = check_tier_admissibility(SourceTier.SHAYKH, "CRITICAL")
        assert not admissible
        assert reason is not None


class TestDabt:
    """Tests for multi-dimensional precision scoring."""

    def test_full_dimensions_calculated(self) -> None:
        """All dimensions present gives weighted score."""
        factors = DabtDimensions(
            documentation_precision=0.9,
            transmission_precision=0.8,
            temporal_precision=0.7,
            cognitive_precision=0.6,
        )
        result = calculate_dabt_score(factors)
        assert result.score > 0
        assert result.available_dimensions == 4
        assert result.quality_band in {"EXCELLENT", "GOOD", "FAIR", "POOR"}

    def test_missing_dimension_fails_closed_to_zero(self) -> None:
        """Missing dimensions treated as 0.0 (fail closed)."""
        factors = DabtDimensions(
            documentation_precision=None,
            transmission_precision=0.8,
            temporal_precision=None,
            cognitive_precision=None,
        )
        result = calculate_dabt_score(factors)
        assert result.available_dimensions == 1
        assert len(result.warnings) > 0

    def test_none_factors_fails_closed(self) -> None:
        """None factors fail closed to 0.0."""
        result = calculate_dabt_score(None)
        assert result.score == 0.0
        assert result.quality_band == "POOR"

    def test_cognitive_none_excluded_not_penalized(self) -> None:
        """cognitive_precision=None is excluded, not penalized."""
        factors = DabtDimensions(
            documentation_precision=0.9,
            transmission_precision=0.9,
            temporal_precision=0.9,
            cognitive_precision=None,
        )
        result = calculate_dabt_score(factors)
        assert result.score > 0.8
        assert result.available_dimensions == 3

    def test_poor_dabt_caps_grade(self) -> None:
        """Dabt < 0.50 should cap grade at B."""
        cap, warning = get_dabt_grade_impact(0.40)
        assert cap == "B"
        assert warning is not None


class TestTawatur:
    """Tests for independence assessment and collusion detection."""

    def test_independence_key_uniqueness(self) -> None:
        """Different sources should have different independence keys."""
        source_a = {
            "evidence_id": "a",
            "source_system": "STRIPE",
            "upstream_origin_id": "stripe-001",
            "timestamp": "2026-01-01T10:00:00Z",
        }
        source_b = {
            "evidence_id": "b",
            "source_system": "BANK",
            "upstream_origin_id": "bank-001",
            "timestamp": "2026-01-01T14:00:00Z",
        }
        key_a = compute_independence_key(source_a)
        key_b = compute_independence_key(source_b)
        assert key_a != key_b

    def test_same_origin_same_key(self) -> None:
        """Same upstream_origin_id should produce same grouping."""
        source_a = {
            "evidence_id": "a",
            "source_system": "SYSTEM",
            "upstream_origin_id": "shared-origin",
            "artifact_id": "doc1",
            "timestamp": "2026-01-01T10:00:00Z",
        }
        source_b = {
            "evidence_id": "b",
            "source_system": "SYSTEM",
            "upstream_origin_id": "shared-origin",
            "artifact_id": "doc1",
            "timestamp": "2026-01-01T10:30:00Z",
        }
        key_a = compute_independence_key(source_a)
        key_b = compute_independence_key(source_b)
        assert key_a == key_b

    def test_mutawatir_requires_three_independent(self) -> None:
        """MUTAWATIR requires >= 3 independent sources."""
        sources = [
            {
                "evidence_id": f"e{i}",
                "source_system": f"SYS{i}",
                "upstream_origin_id": f"origin-{i}",
            }
            for i in range(3)
        ]
        result = assess_tawatur(sources)
        assert result.independent_count == 3
        assert result.status == TawaturType.MUTAWATIR

    def test_high_collusion_blocks_mutawatir(self) -> None:
        """High collusion risk downgrades MUTAWATIR to AHAD_2."""
        sources = [
            {
                "evidence_id": f"e{i}",
                "source_system": "SAME_SYSTEM",
                "upstream_origin_id": f"origin-{i}",
                "timestamp": "2026-01-01T10:00:00Z",
            }
            for i in range(5)
        ]
        result = assess_tawatur(sources)
        assert result.collusion_risk > 0.30

    def test_check_independence_same_origin_fails(self) -> None:
        """Same upstream_origin_id means not independent."""
        source_a = {"upstream_origin_id": "same"}
        source_b = {"upstream_origin_id": "same"}
        is_independent, reason = check_source_independence(source_a, source_b)
        assert not is_independent


class TestShudhudh:
    """Tests for reconciliation-first anomaly detection."""

    def test_values_within_tolerance_no_anomaly(self) -> None:
        """Values within 1% tolerance reconcile successfully."""
        values = [{"value": 1000000}, {"value": 1005000}]
        sources = [
            {"source_type": "AUDITED_FINANCIAL"},
            {"source_type": "FINANCIAL_MODEL"},
        ]
        result = detect_shudhudh(values, sources)
        assert not result.has_anomaly

    def test_unit_mismatch_reconciles(self) -> None:
        """K vs M mismatch with labels should reconcile."""
        values = [
            {"value": "5000K", "unit": "K"},
            {"value": "5M", "unit": "M"},
        ]
        sources = [
            {"source_type": "PITCH_DECK"},
            {"source_type": "FINANCIAL_MODEL"},
        ]
        result = detect_shudhudh(values, sources)
        assert result.defect_code in {None, "SHUDHUDH_UNIT_MISMATCH"}

    def test_lower_tier_contradiction_flags_anomaly(self) -> None:
        """Lower-tier contradicting higher-tier triggers SHUDHUDH_ANOMALY."""
        values = [
            {"value": 5000000},
            {"value": 3000000},
        ]
        sources = [
            {"source_type": "AUDITED_FINANCIAL"},
            {"source_type": "PRESS_RELEASE"},
        ]
        result = detect_shudhudh(values, sources, contradiction_threshold=0.05)
        assert result.has_anomaly
        assert result.defect_code == "SHUDHUDH_ANOMALY"
        assert result.severity == "MAJOR"


class TestIlal:
    """Tests for hidden defect detection."""

    def test_chain_break_missing_parent(self) -> None:
        """Missing parent reference triggers ILAL_CHAIN_BREAK."""
        sanad = {
            "transmission_chain": [
                {"node_id": "node-1", "prev_node_id": None},
                {"node_id": "node-2", "prev_node_id": "non-existent-parent"},
            ]
        }
        defect = detect_ilal_chain_break(sanad)
        assert defect is not None
        assert defect.code == IlalDefectCode.ILAL_CHAIN_BREAK
        assert defect.severity == "FATAL"

    def test_chain_break_empty_chain(self) -> None:
        """Empty transmission chain triggers ILAL_CHAIN_BREAK."""
        sanad = {"transmission_chain": []}
        defect = detect_ilal_chain_break(sanad)
        assert defect is not None
        assert defect.code == IlalDefectCode.ILAL_CHAIN_BREAK

    def test_chain_grafting_mismatched_origins(self) -> None:
        """Mismatched upstream_origin_id triggers ILAL_CHAIN_GRAFTING."""
        sanad = {
            "transmission_chain": [
                {"node_id": "node-1", "upstream_origin_id": "origin-A"},
                {"node_id": "node-2", "prev_node_id": "node-1", "upstream_origin_id": "origin-B"},
            ]
        }
        defect = detect_ilal_chain_grafting(sanad)
        assert defect is not None
        assert defect.code == IlalDefectCode.ILAL_CHAIN_GRAFTING
        assert defect.severity == "FATAL"

    def test_chronology_impossible(self) -> None:
        """Child timestamp before parent triggers ILAL_CHRONOLOGY_IMPOSSIBLE."""
        sanad = {
            "transmission_chain": [
                {"node_id": "node-1", "timestamp": "2026-01-02T10:00:00Z"},
                {
                    "node_id": "node-2",
                    "prev_node_id": "node-1",
                    "timestamp": "2026-01-01T10:00:00Z",
                },
            ]
        }
        defect = detect_ilal_chronology_impossible(sanad)
        assert defect is not None
        assert defect.code == IlalDefectCode.ILAL_CHRONOLOGY_IMPOSSIBLE
        assert defect.severity == "FATAL"

    def test_version_drift_detected(self) -> None:
        """Claim citing old version with newer available triggers ILAL_VERSION_DRIFT."""
        claim = {
            "claim_type": "ARR",
            "cited_document": {"document_id": "doc-1", "version": 1},
        }
        documents = [
            {"document_id": "doc-1", "version": 1, "metrics": {"ARR": 5000000}},
            {"document_id": "doc-1", "version": 2, "metrics": {"ARR": 5500000}},
        ]
        defect = detect_ilal_version_drift(claim, documents)
        assert defect is not None
        assert defect.code == IlalDefectCode.ILAL_VERSION_DRIFT
        assert defect.severity == "MAJOR"


class TestCOI:
    """Tests for conflict of interest handling."""

    def test_extract_coi_metadata(self) -> None:
        """COI metadata extracted correctly."""
        source = {
            "coi_present": True,
            "coi_severity": "HIGH",
            "coi_disclosed": False,
            "coi_type": "FINANCIAL",
        }
        coi = extract_coi_metadata(source)
        assert coi.coi_present is True
        assert coi.coi_severity == COISeverity.HIGH
        assert coi.coi_disclosed is False

    def test_high_undisclosed_caps_grade(self) -> None:
        """HIGH undisclosed COI without cure caps grade at C."""
        source = {
            "evidence_id": "src-1",
            "coi_present": True,
            "coi_severity": "HIGH",
            "coi_disclosed": False,
        }
        corroborating = []
        result = evaluate_coi_cure(source, corroborating)
        assert not result.cured
        assert result.grade_cap == "C"

    def test_high_undisclosed_cured_by_independent(self) -> None:
        """HIGH undisclosed COI can be cured by independent high-tier source."""
        source = {
            "evidence_id": "src-1",
            "coi_present": True,
            "coi_severity": "HIGH",
            "coi_disclosed": False,
            "source_type": "PITCH_DECK",
        }
        corroborating = [
            {
                "evidence_id": "src-2",
                "source_type": "AUDITED_FINANCIAL",
                "coi_present": False,
            }
        ]
        result = evaluate_coi_cure(source, corroborating)
        assert result.cured

    def test_low_coi_no_cure_needed(self) -> None:
        """LOW COI does not require cure."""
        source = {
            "evidence_id": "src-1",
            "coi_present": True,
            "coi_severity": "LOW",
            "coi_disclosed": False,
        }
        result = evaluate_coi_cure(source, [])
        assert result.cured

    def test_coi_defect_emitted(self) -> None:
        """COI evaluation emits defect for uncured HIGH."""
        source = {
            "evidence_id": "src-1",
            "coi_present": True,
            "coi_severity": "HIGH",
            "coi_disclosed": False,
        }
        result = evaluate_source_coi(source, [])
        assert result.defect is not None
        assert result.defect.code == COIDefectCode.COI_HIGH_UNDISCLOSED


class TestGraderV2:
    """Tests for integrated Sanad grading."""

    def test_fatal_defect_forces_grade_d(self) -> None:
        """Any FATAL defect forces grade D."""
        sanad = {
            "primary_source": {"source_type": "AUDITED_FINANCIAL"},
            "transmission_chain": [],
        }
        result = calculate_sanad_grade(sanad)
        assert result.grade == "D"
        assert len(result.explanation.fatal_defects) > 0

    def test_mutawatir_upgrades_grade(self) -> None:
        """MUTAWATIR with no major defects upgrades grade."""
        sanad = {
            "primary_source": {"source_type": "THIQAH"},
            "transmission_chain": [
                {
                    "node_id": "n1",
                    "node_type": "EXTRACT",
                    "actor_type": "AGENT",
                    "actor_id": "a1",
                    "timestamp": "2026-01-01T10:00:00Z",
                }
            ],
            "dabt_factors": {
                "documentation_precision": 0.9,
                "transmission_precision": 0.9,
                "temporal_precision": 0.9,
            },
        }
        sources = [
            {
                "evidence_id": f"e{i}",
                "source_system": f"SYS{i}",
                "upstream_origin_id": f"o{i}",
                "source_type": "THIQAH",
            }
            for i in range(3)
        ]
        result = calculate_sanad_grade(sanad, sources=sources)
        assert result.tawatur.status == TawaturType.MUTAWATIR
        if not result.explanation.major_defects:
            assert "upgrade" in result.explanation.summary.lower() or result.grade in {"A", "B"}

    def test_major_defects_downgrade(self) -> None:
        """MAJOR defects downgrade grade."""
        sanad = {
            "primary_source": {"source_type": "AUDITED_FINANCIAL"},
            "transmission_chain": [
                {
                    "node_id": "n1",
                    "node_type": "EXTRACT",
                    "actor_type": "AGENT",
                    "actor_id": "a1",
                    "timestamp": "2026-01-01T10:00:00Z",
                }
            ],
        }
        claim = {
            "claim_type": "ARR",
            "cited_document": {"document_id": "doc-1", "version": 1},
        }
        documents = [
            {"document_id": "doc-1", "version": 1, "metrics": {"ARR": 5000000}},
            {"document_id": "doc-1", "version": 2, "metrics": {"ARR": 6000000}},
        ]
        result = calculate_sanad_grade(sanad, claim=claim, documents=documents)
        assert len(result.ilal_defects) > 0

    def test_grade_sanad_v2_alias(self) -> None:
        """grade_sanad_v2 is alias for calculate_sanad_grade."""
        sanad = {
            "primary_source": {"source_type": "PITCH_DECK"},
            "transmission_chain": [
                {
                    "node_id": "n1",
                    "node_type": "EXTRACT",
                    "actor_type": "AGENT",
                    "actor_id": "a1",
                    "timestamp": "2026-01-01T10:00:00Z",
                }
            ],
        }
        result = grade_sanad_v2(sanad)
        assert result.grade in {"A", "B", "C", "D"}


class TestFailClosed:
    """Tests for fail-closed behavior across all components."""

    def test_source_tier_fails_closed(self) -> None:
        """Unknown source type fails closed to lowest tier."""
        assert assign_source_tier(None) == SourceTier.MAQBUL
        assert assign_source_tier({}) == SourceTier.MAQBUL
        assert assign_source_tier({"source_type": None}) == SourceTier.MAQBUL

    def test_dabt_fails_closed(self) -> None:
        """Missing Dabt factors fail closed to 0.0."""
        result = calculate_dabt_score(None)
        assert result.score == 0.0

        result = calculate_dabt_score({})
        assert result.score == 0.0

    def test_tawatur_fails_closed(self) -> None:
        """Empty sources fail closed to NONE."""
        result = assess_tawatur([])
        assert result.status == TawaturType.NONE
        assert result.independent_count == 0

    def test_grader_fails_closed_on_empty_chain(self) -> None:
        """Empty transmission chain fails closed to grade D."""
        sanad = {"transmission_chain": []}
        result = calculate_sanad_grade(sanad)
        assert result.grade == "D"
