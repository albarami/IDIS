"""
GDBS-FULL Dataset Expected Outcomes Tests.

Validates that dataset structure matches expected adversarial outcomes.
These tests verify the deterministic nature of the dataset.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from idis.testing.gdbs_loader import GDBSDataset, GDBSLoader

GDBS_PATH = Path(__file__).parent.parent / "datasets" / "gdbs_full"


@pytest.fixture(scope="module")
def gdbs_dataset() -> GDBSDataset:
    """Load GDBS-FULL dataset once for all tests."""
    if not GDBS_PATH.exists():
        pytest.skip(f"GDBS-FULL dataset not found at {GDBS_PATH}")
    loader = GDBSLoader(GDBS_PATH)
    return loader.load()


class TestDeal001Clean:
    """Test deal_001 (clean) expected outcomes."""

    def test_scenario_is_clean(self, gdbs_dataset: GDBSDataset) -> None:
        """Deal 001 must be clean scenario."""
        deal = gdbs_dataset.get_deal("deal_001")
        assert deal is not None
        assert deal.scenario == "clean"

    def test_no_defects(self, gdbs_dataset: GDBSDataset) -> None:
        """Clean deal must have no defects."""
        deal = gdbs_dataset.get_deal("deal_001")
        assert deal is not None
        assert len(deal.defects) == 0

    def test_all_claims_have_sanads(self, gdbs_dataset: GDBSDataset) -> None:
        """All claims must have corresponding sanads."""
        deal = gdbs_dataset.get_deal("deal_001")
        assert deal is not None
        assert len(deal.sanads) == 7

    def test_expected_grades(self, gdbs_dataset: GDBSDataset) -> None:
        """Clean deal grades match expected."""
        outcome = gdbs_dataset.get_expected_outcome("deal_001")
        assert outcome is not None
        assert outcome.expected_claims["C1"]["claim_grade"] == "B"
        assert outcome.expected_claims["C1"]["defect_count"] == 0


class TestDeal002Contradiction:
    """Test deal_002 (contradiction) expected outcomes."""

    def test_scenario_is_contradiction(self, gdbs_dataset: GDBSDataset) -> None:
        """Deal 002 must be contradiction scenario."""
        deal = gdbs_dataset.get_deal("deal_002")
        assert deal is not None
        assert deal.scenario == "contradiction"

    def test_has_inconsistency_defect(self, gdbs_dataset: GDBSDataset) -> None:
        """Must have INCONSISTENCY defect."""
        deal = gdbs_dataset.get_deal("deal_002")
        assert deal is not None
        assert len(deal.defects) == 1
        assert deal.defects[0]["defect_type"] == "INCONSISTENCY"

    def test_c1_is_grade_d(self, gdbs_dataset: GDBSDataset) -> None:
        """C1 (ARR) must be grade D due to contradiction."""
        deal = gdbs_dataset.get_deal("deal_002")
        assert deal is not None
        c1_sanad = next((s for s in deal.sanads if s.get("claim_id", "").endswith("20101")), None)
        assert c1_sanad is not None
        assert c1_sanad["sanad_grade"] == "D"

    def test_expected_c1_contradicted(self, gdbs_dataset: GDBSDataset) -> None:
        """Expected outcome shows C1 contradicted."""
        outcome = gdbs_dataset.get_expected_outcome("deal_002")
        assert outcome is not None
        assert outcome.expected_claims["C1"]["claim_verdict"] == "CONTRADICTED"


class TestDeal003UnitMismatch:
    """Test deal_003 (unit_mismatch) expected outcomes."""

    def test_scenario_is_unit_mismatch(self, gdbs_dataset: GDBSDataset) -> None:
        """Deal 003 must be unit_mismatch scenario."""
        deal = gdbs_dataset.get_deal("deal_003")
        assert deal is not None
        assert deal.scenario == "unit_mismatch"

    def test_has_unit_mismatch_defect(self, gdbs_dataset: GDBSDataset) -> None:
        """Must have UNIT_MISMATCH defect."""
        deal = gdbs_dataset.get_deal("deal_003")
        assert deal is not None
        assert len(deal.defects) == 1
        assert deal.defects[0]["defect_type"] == "UNIT_MISMATCH"

    def test_expected_c1_inflated(self, gdbs_dataset: GDBSDataset) -> None:
        """Expected outcome shows C1 inflated."""
        outcome = gdbs_dataset.get_expected_outcome("deal_003")
        assert outcome is not None
        assert outcome.expected_claims["C1"]["claim_verdict"] == "INFLATED"


class TestDeal004TimeWindowMismatch:
    """Test deal_004 (time_window_mismatch) expected outcomes."""

    def test_scenario_is_time_window_mismatch(self, gdbs_dataset: GDBSDataset) -> None:
        """Deal 004 must be time_window_mismatch scenario."""
        deal = gdbs_dataset.get_deal("deal_004")
        assert deal is not None
        assert deal.scenario == "time_window_mismatch"

    def test_has_time_window_defect(self, gdbs_dataset: GDBSDataset) -> None:
        """Must have TIME_WINDOW_MISMATCH defect."""
        deal = gdbs_dataset.get_deal("deal_004")
        assert deal is not None
        assert len(deal.defects) == 1
        assert deal.defects[0]["defect_type"] == "TIME_WINDOW_MISMATCH"

    def test_expected_c1_unverified(self, gdbs_dataset: GDBSDataset) -> None:
        """Expected outcome shows C1 unverified."""
        outcome = gdbs_dataset.get_expected_outcome("deal_004")
        assert outcome is not None
        assert outcome.expected_claims["C1"]["claim_verdict"] == "UNVERIFIED"


class TestDeal005MissingEvidence:
    """Test deal_005 (missing_evidence) expected outcomes."""

    def test_scenario_is_missing_evidence(self, gdbs_dataset: GDBSDataset) -> None:
        """Deal 005 must be missing_evidence scenario."""
        deal = gdbs_dataset.get_deal("deal_005")
        assert deal is not None
        assert deal.scenario == "missing_evidence"

    def test_has_missing_link_defect(self, gdbs_dataset: GDBSDataset) -> None:
        """Must have MISSING_LINK defect."""
        deal = gdbs_dataset.get_deal("deal_005")
        assert deal is not None
        assert len(deal.defects) == 1
        assert deal.defects[0]["defect_type"] == "MISSING_LINK"

    def test_c6_missing_span(self, gdbs_dataset: GDBSDataset) -> None:
        """C6 claim must have null primary_span_id."""
        deal = gdbs_dataset.get_deal("deal_005")
        assert deal is not None
        c6 = next((c for c in deal.claims if c.get("claim_key") == "C6"), None)
        assert c6 is not None
        assert c6.get("primary_span_id") is None

    def test_expected_c6_grade_d(self, gdbs_dataset: GDBSDataset) -> None:
        """Expected outcome shows C6 grade D."""
        outcome = gdbs_dataset.get_expected_outcome("deal_005")
        assert outcome is not None
        assert outcome.expected_claims["C6"]["claim_grade"] == "D"

    def test_c6_is_blocked_no_free_facts(self, gdbs_dataset: GDBSDataset) -> None:
        """C6 must be explicitly BLOCKED due to No-Free-Facts violation."""
        outcome = gdbs_dataset.get_expected_outcome("deal_005")
        assert outcome is not None
        assert outcome.expected_claims["C6"]["claim_verdict"] == "BLOCKED"
        assert outcome.expected_claims["C6"]["claim_action"] == "REJECT_NO_FREE_FACTS"

    def test_blocked_claims_list_exists(self, gdbs_dataset: GDBSDataset) -> None:
        """Expected outcome must have blocked_claims list."""
        outcome = gdbs_dataset.get_expected_outcome("deal_005")
        assert outcome is not None
        blocked = outcome.raw.get("blocked_claims", [])
        assert len(blocked) >= 1, "Must have at least one blocked claim"

    def test_blocked_claim_has_no_free_facts_reason(self, gdbs_dataset: GDBSDataset) -> None:
        """Blocked claim must have NO_FREE_FACTS_MISSING_EVIDENCE reason."""
        outcome = gdbs_dataset.get_expected_outcome("deal_005")
        assert outcome is not None
        blocked = outcome.raw.get("blocked_claims", [])
        c6_blocked = next((b for b in blocked if b.get("claim_key") == "C6"), None)
        assert c6_blocked is not None, "C6 must be in blocked_claims"
        assert c6_blocked["reason"] == "NO_FREE_FACTS_MISSING_EVIDENCE"

    def test_blocked_claim_has_exact_claim_id(self, gdbs_dataset: GDBSDataset) -> None:
        """Blocked claim must have exact claim_id matching claims.json (B2 Codex fix).

        The claim_id in blocked_claims must match the actual claim record from
        deal_005_missing_evidence/claims.json for C6 (NRR with null primary_span_id).
        """
        import json

        # Load claims.json to get the actual C6 claim_id
        deal_dir = gdbs_dataset.dataset_path / "deals" / "deal_005_missing_evidence"
        claims_json = deal_dir / "claims.json"
        claims_data = json.loads(claims_json.read_text(encoding="utf-8"))

        # Find C6 claim (NRR with missing evidence)
        c6_claim = next((c for c in claims_data["claims"] if c.get("claim_key") == "C6"), None)
        assert c6_claim is not None, "C6 claim not found in claims.json"
        expected_claim_id = c6_claim["claim_id"]

        # Verify the blocked_claims entry uses this exact claim_id
        outcome = gdbs_dataset.get_expected_outcome("deal_005")
        assert outcome is not None
        blocked = outcome.raw.get("blocked_claims", [])
        c6_blocked = next((b for b in blocked if b.get("claim_key") == "C6"), None)
        assert c6_blocked is not None, "C6 must be in blocked_claims"

        actual_claim_id = c6_blocked.get("claim_id")
        assert actual_claim_id == expected_claim_id, (
            f"blocked_claims claim_id mismatch: expected {expected_claim_id}, got {actual_claim_id}"
        )

    def test_no_free_facts_enforcement_active(self, gdbs_dataset: GDBSDataset) -> None:
        """No-Free-Facts enforcement must be explicitly ACTIVE."""
        outcome = gdbs_dataset.get_expected_outcome("deal_005")
        assert outcome is not None
        enforcement = outcome.raw.get("no_free_facts_enforcement", {})
        assert enforcement.get("enforcement_status") == "ACTIVE"
        assert enforcement.get("blocked_count") == 1
        assert "C6" in enforcement.get("blocked_claim_keys", [])

    def test_validation_rules_include_no_free_facts(self, gdbs_dataset: GDBSDataset) -> None:
        """Validation rules must include no_free_facts_violation_detected."""
        outcome = gdbs_dataset.get_expected_outcome("deal_005")
        assert outcome is not None
        rules = {r["rule"]: r["expected"] for r in outcome.validation_rules}
        assert rules.get("no_free_facts_violation_detected") is True
        assert rules.get("c6_blocked_from_ic_output") is True


class TestDeal006CalcConflict:
    """Test deal_006 (calc_conflict) expected outcomes."""

    def test_scenario_is_calc_conflict(self, gdbs_dataset: GDBSDataset) -> None:
        """Deal 006 must be calc_conflict scenario."""
        deal = gdbs_dataset.get_deal("deal_006")
        assert deal is not None
        assert deal.scenario == "calc_conflict"

    def test_has_inconsistency_defect(self, gdbs_dataset: GDBSDataset) -> None:
        """Must have INCONSISTENCY defect for calc vs stated."""
        deal = gdbs_dataset.get_deal("deal_006")
        assert deal is not None
        assert len(deal.defects) == 1
        assert deal.defects[0]["defect_type"] == "INCONSISTENCY"

    def test_has_calc_sanad(self, gdbs_dataset: GDBSDataset) -> None:
        """Must have calc_sanad for GM calculation."""
        deal = gdbs_dataset.get_deal("deal_006")
        assert deal is not None
        assert len(deal.calcs) >= 1
        gm_calc = deal.calcs[0]
        assert gm_calc["calc_type"] == "GROSS_MARGIN"
        assert gm_calc["output"]["gross_margin_percent"] == 68.50

    def test_expected_c2_contradicted(self, gdbs_dataset: GDBSDataset) -> None:
        """Expected outcome shows C2 (GM) contradicted."""
        outcome = gdbs_dataset.get_expected_outcome("deal_006")
        assert outcome is not None
        assert outcome.expected_claims["C2"]["claim_verdict"] == "CONTRADICTED"


class TestDeal007ChainBreak:
    """Test deal_007 (chain_break) expected outcomes."""

    def test_scenario_is_chain_break(self, gdbs_dataset: GDBSDataset) -> None:
        """Deal 007 must be chain_break scenario."""
        deal = gdbs_dataset.get_deal("deal_007")
        assert deal is not None
        assert deal.scenario == "chain_break"

    def test_has_broken_chain_defect(self, gdbs_dataset: GDBSDataset) -> None:
        """Must have BROKEN_CHAIN defect."""
        deal = gdbs_dataset.get_deal("deal_007")
        assert deal is not None
        assert len(deal.defects) == 1
        assert deal.defects[0]["defect_type"] == "BROKEN_CHAIN"
        assert deal.defects[0]["severity"] == "FATAL"

    def test_expected_fatal_defect(self, gdbs_dataset: GDBSDataset) -> None:
        """Expected outcome must show fatal defect detected."""
        outcome = gdbs_dataset.get_expected_outcome("deal_007")
        assert outcome is not None
        rules = {r["rule"]: r["expected"] for r in outcome.validation_rules}
        assert rules.get("fatal_defect_detected") is True


class TestDeal008VersionDrift:
    """Test deal_008 (version_drift) expected outcomes."""

    def test_scenario_is_version_drift(self, gdbs_dataset: GDBSDataset) -> None:
        """Deal 008 must be version_drift scenario."""
        deal = gdbs_dataset.get_deal("deal_008")
        assert deal is not None
        assert deal.scenario == "version_drift"

    def test_has_staleness_defect(self, gdbs_dataset: GDBSDataset) -> None:
        """Must have STALENESS defect."""
        deal = gdbs_dataset.get_deal("deal_008")
        assert deal is not None
        assert len(deal.defects) == 1
        assert deal.defects[0]["defect_type"] == "STALENESS"

    def test_multiple_artifact_versions(self, gdbs_dataset: GDBSDataset) -> None:
        """Must have v1 and v2 of deck artifact."""
        deal = gdbs_dataset.get_deal("deal_008")
        assert deal is not None
        deck_artifacts = [a for a in deal.artifacts if a.get("artifact_type") == "PITCH_DECK"]
        assert len(deck_artifacts) == 2
        versions = {a.get("version_label") for a in deck_artifacts}
        assert "v1" in versions
        assert "v2" in versions

    def test_expected_c1_unverified(self, gdbs_dataset: GDBSDataset) -> None:
        """Expected outcome shows C1 unverified due to staleness."""
        outcome = gdbs_dataset.get_expected_outcome("deal_008")
        assert outcome is not None
        assert outcome.expected_claims["C1"]["claim_verdict"] == "UNVERIFIED"


class TestAdversarialCoverage:
    """Test that adversarial scenarios are properly covered."""

    def test_each_scenario_has_unique_defect_type(self, gdbs_dataset: GDBSDataset) -> None:
        """Adversarial deals should demonstrate different defect types."""
        defect_types_by_deal: dict[str, str | None] = {}
        for deal in gdbs_dataset.deals:
            if deal.defects:
                defect_types_by_deal[deal.deal_key] = deal.defects[0]["defect_type"]
            else:
                defect_types_by_deal[deal.deal_key] = None

        assert defect_types_by_deal["deal_001"] is None  # clean
        assert defect_types_by_deal["deal_002"] == "INCONSISTENCY"
        assert defect_types_by_deal["deal_003"] == "UNIT_MISMATCH"
        assert defect_types_by_deal["deal_004"] == "TIME_WINDOW_MISMATCH"
        assert defect_types_by_deal["deal_005"] == "MISSING_LINK"
        assert defect_types_by_deal["deal_006"] == "INCONSISTENCY"
        assert defect_types_by_deal["deal_007"] == "BROKEN_CHAIN"
        assert defect_types_by_deal["deal_008"] == "STALENESS"

    def test_seven_of_eight_deals_have_defects(self, gdbs_dataset: GDBSDataset) -> None:
        """Exactly 7 deals should have defects (all except clean)."""
        deals_with_defects = [d for d in gdbs_dataset.deals if d.defects]
        assert len(deals_with_defects) == 7

    def test_grade_d_distribution(self, gdbs_dataset: GDBSDataset) -> None:
        """Check grade D claims are in adversarial deals only."""
        for deal in gdbs_dataset.deals:
            grade_d_sanads = [s for s in deal.sanads if s.get("sanad_grade") == "D"]
            outcome = gdbs_dataset.get_expected_outcome(deal.deal_key)
            if outcome:
                expected_d_count = outcome.raw.get("expected_grade_d_count", 0)
                assert len(grade_d_sanads) == expected_d_count, (
                    f"Deal {deal.deal_key}: expected {expected_d_count} "
                    f"grade D, got {len(grade_d_sanads)}"
                )


class TestDeterministicRounding:
    """Test that numeric values follow deterministic rounding rules."""

    def test_arr_rounded_to_thousands(self, gdbs_dataset: GDBSDataset) -> None:
        """ARR values should be rounded to nearest $1,000."""
        for deal in gdbs_dataset.deals:
            c1 = next((c for c in deal.claims if c.get("claim_key") == "C1"), None)
            if c1 and c1.get("value"):
                arr_value = c1["value"].get("value", 0)
                assert arr_value % 1000 == 0, (
                    f"ARR {arr_value} not rounded to $1000 in {deal.deal_key}"
                )

    def test_percentages_two_decimals(self, gdbs_dataset: GDBSDataset) -> None:
        """Percentage values should have at most 2 decimal places."""
        for deal in gdbs_dataset.deals:
            for claim in deal.claims:
                if claim.get("value", {}).get("unit") == "percent":
                    value = claim["value"]["value"]
                    rounded = round(value, 2)
                    assert value == rounded, (
                        f"Percentage {value} has >2 decimals in {deal.deal_key}"
                    )

    def test_runway_one_decimal(self, gdbs_dataset: GDBSDataset) -> None:
        """Runway values should have at most 1 decimal place."""
        for deal in gdbs_dataset.deals:
            c4 = next((c for c in deal.claims if c.get("claim_key") == "C4"), None)
            if c4 and c4.get("value"):
                runway = c4["value"].get("value", 0)
                rounded = round(runway, 1)
                assert runway == rounded, f"Runway {runway} has >1 decimal in {deal.deal_key}"
