"""
GDBS-FULL Dataset Contract Tests.

Validates dataset structure, schema compliance, and invariants.
Tests are deterministic and fail-closed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from idis.testing.gdbs_loader import GDBSDataset, GDBSLoader, GDBSLoadError

GDBS_PATH = Path(__file__).parent.parent / "datasets" / "gdbs_full"


@pytest.fixture(scope="module")
def gdbs_dataset() -> GDBSDataset:
    """Load GDBS-FULL dataset once for all tests."""
    if not GDBS_PATH.exists():
        pytest.skip(f"GDBS-FULL dataset not found at {GDBS_PATH}")
    loader = GDBSLoader(GDBS_PATH)
    return loader.load()


class TestGDBSLoaderContract:
    """Test loader fail-closed behavior."""

    def test_loader_rejects_nonexistent_path(self) -> None:
        """Loader must fail-closed on non-existent path."""
        with pytest.raises(GDBSLoadError) as exc_info:
            GDBSLoader("/nonexistent/path/to/dataset")
        assert "does not exist" in str(exc_info.value)

    def test_loader_loads_valid_dataset(self, gdbs_dataset: GDBSDataset) -> None:
        """Loader must successfully load valid dataset."""
        assert gdbs_dataset is not None
        assert gdbs_dataset.manifest is not None
        assert gdbs_dataset.tenant is not None


class TestManifestContract:
    """Test manifest structure and required fields."""

    def test_manifest_has_required_fields(self, gdbs_dataset: GDBSDataset) -> None:
        """Manifest must have all required fields."""
        manifest = gdbs_dataset.manifest
        required = [
            "manifest_version",
            "dataset_id",
            "version",
            "deals",
            "rounding_rules",
            "claim_set",
        ]
        for field in required:
            assert field in manifest, f"Missing required field: {field}"

    def test_manifest_version_is_valid(self, gdbs_dataset: GDBSDataset) -> None:
        """Manifest version must be semver format."""
        version = gdbs_dataset.manifest.get("manifest_version", "")
        parts = version.split(".")
        assert len(parts) == 3, f"Invalid version format: {version}"

    def test_manifest_defines_eight_deals(self, gdbs_dataset: GDBSDataset) -> None:
        """Manifest must define exactly 8 deals."""
        deals = gdbs_dataset.manifest.get("deals", [])
        assert len(deals) == 8, f"Expected 8 deals, got {len(deals)}"

    def test_manifest_defines_seven_claims(self, gdbs_dataset: GDBSDataset) -> None:
        """Manifest must define C1-C7 claim set."""
        claim_set = gdbs_dataset.manifest.get("claim_set", [])
        assert len(claim_set) == 7, f"Expected 7 claims in claim_set, got {len(claim_set)}"
        claim_keys = {c.get("claim_key") for c in claim_set}
        expected_keys = {"C1", "C2", "C3", "C4", "C5", "C6", "C7"}
        assert claim_keys == expected_keys, f"Claim keys mismatch: {claim_keys}"


class TestTenantContract:
    """Test tenant isolation and structure."""

    def test_tenant_has_required_fields(self, gdbs_dataset: GDBSDataset) -> None:
        """Tenant must have required fields."""
        tenant = gdbs_dataset.tenant
        assert tenant.tenant_id is not None
        assert tenant.name is not None
        assert len(tenant.tenant_id) == 36  # UUID format

    def test_tenant_id_is_consistent(self, gdbs_dataset: GDBSDataset) -> None:
        """All deals must reference same tenant_id."""
        expected_tenant_id = gdbs_dataset.tenant.tenant_id
        for deal in gdbs_dataset.deals:
            assert deal.tenant_id == expected_tenant_id, (
                f"Deal {deal.deal_key} has wrong tenant_id: {deal.tenant_id}"
            )


class TestActorsContract:
    """Test actor structure and permissions."""

    def test_has_required_actors(self, gdbs_dataset: GDBSDataset) -> None:
        """Dataset must have analyst_1, analyst_2, admin_1."""
        actors = gdbs_dataset.actors
        assert len(actors) >= 3
        roles = {a.role for a in actors}
        assert "ANALYST" in roles
        assert "ADMIN" in roles

    def test_actors_belong_to_tenant(self, gdbs_dataset: GDBSDataset) -> None:
        """All actors must belong to the dataset tenant."""
        expected_tenant_id = gdbs_dataset.tenant.tenant_id
        for actor in gdbs_dataset.actors:
            assert actor.tenant_id == expected_tenant_id


class TestDealsContract:
    """Test deal structure and coverage."""

    def test_has_eight_deals(self, gdbs_dataset: GDBSDataset) -> None:
        """Dataset must have exactly 8 deals."""
        assert len(gdbs_dataset.deals) == 8

    def test_deals_have_unique_ids(self, gdbs_dataset: GDBSDataset) -> None:
        """All deals must have unique deal_ids."""
        deal_ids = [d.deal_id for d in gdbs_dataset.deals]
        assert len(deal_ids) == len(set(deal_ids)), "Duplicate deal_ids found"

    def test_deals_cover_all_scenarios(self, gdbs_dataset: GDBSDataset) -> None:
        """Deals must cover all 8 adversarial scenarios."""
        expected_scenarios = {
            "clean",
            "contradiction",
            "unit_mismatch",
            "time_window_mismatch",
            "missing_evidence",
            "calc_conflict",
            "chain_break",
            "version_drift",
        }
        actual_scenarios = {d.scenario for d in gdbs_dataset.deals}
        assert actual_scenarios == expected_scenarios, f"Scenario mismatch: {actual_scenarios}"

    @pytest.mark.parametrize("deal_key", [f"deal_{i:03d}" for i in range(1, 9)])
    def test_deal_has_required_components(self, gdbs_dataset: GDBSDataset, deal_key: str) -> None:
        """Each deal must have artifacts, spans, claims, evidence, sanads."""
        deal = gdbs_dataset.get_deal(deal_key)
        assert deal is not None, f"Deal {deal_key} not found"
        assert len(deal.artifacts) >= 1, f"Deal {deal_key} missing artifacts"
        assert len(deal.spans) >= 1, f"Deal {deal_key} missing spans"
        assert len(deal.claims) == 7, f"Deal {deal_key} must have 7 claims (C1-C7)"
        assert len(deal.evidence) >= 1, f"Deal {deal_key} missing evidence"
        assert len(deal.sanads) == 7, f"Deal {deal_key} must have 7 sanads"


class TestClaimsContract:
    """Test claim structure and consistency."""

    def test_all_claims_have_required_fields(self, gdbs_dataset: GDBSDataset) -> None:
        """All claims must have required fields."""
        required_fields = [
            "claim_id",
            "tenant_id",
            "deal_id",
            "claim_key",
            "claim_class",
            "claim_text",
        ]
        for deal in gdbs_dataset.deals:
            for claim in deal.claims:
                for field in required_fields:
                    assert field in claim, f"Claim missing {field} in deal {deal.deal_key}"

    def test_claims_cover_c1_through_c7(self, gdbs_dataset: GDBSDataset) -> None:
        """Each deal must have claims C1-C7."""
        expected_keys = {"C1", "C2", "C3", "C4", "C5", "C6", "C7"}
        for deal in gdbs_dataset.deals:
            claim_keys = {c.get("claim_key") for c in deal.claims}
            assert claim_keys == expected_keys, (
                f"Deal {deal.deal_key} missing claims: {expected_keys - claim_keys}"
            )

    def test_claims_have_valid_materiality(self, gdbs_dataset: GDBSDataset) -> None:
        """All claims must have valid materiality values."""
        valid_materiality = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
        for deal in gdbs_dataset.deals:
            for claim in deal.claims:
                materiality = claim.get("materiality")
                assert materiality in valid_materiality, (
                    f"Invalid materiality {materiality} in deal {deal.deal_key}"
                )


class TestSanadsContract:
    """Test Sanad structure and integrity."""

    def test_sanads_have_required_fields(self, gdbs_dataset: GDBSDataset) -> None:
        """All sanads must have required fields."""
        required = ["sanad_id", "tenant_id", "claim_id", "sanad_grade", "transmission_chain"]
        for deal in gdbs_dataset.deals:
            for sanad in deal.sanads:
                for field in required:
                    assert field in sanad, f"Sanad missing {field} in deal {deal.deal_key}"

    def test_sanad_grades_are_valid(self, gdbs_dataset: GDBSDataset) -> None:
        """All sanad grades must be A/B/C/D."""
        valid_grades = {"A", "B", "C", "D"}
        for deal in gdbs_dataset.deals:
            for sanad in deal.sanads:
                grade = sanad.get("sanad_grade")
                assert grade in valid_grades, f"Invalid grade {grade} in deal {deal.deal_key}"

    def test_sanad_corroboration_status_valid(self, gdbs_dataset: GDBSDataset) -> None:
        """Corroboration status must be valid enum."""
        valid_status = {"NONE", "AHAD_1", "AHAD_2", "MUTAWATIR"}
        for deal in gdbs_dataset.deals:
            for sanad in deal.sanads:
                status = sanad.get("corroboration_status")
                assert status in valid_status, (
                    f"Invalid corroboration {status} in deal {deal.deal_key}"
                )


class TestDefectsContract:
    """Test defect structure and types."""

    def test_defects_have_required_fields(self, gdbs_dataset: GDBSDataset) -> None:
        """All defects must have required fields."""
        required = ["defect_id", "defect_type", "severity", "cure_protocol", "affected_claim_ids"]
        for deal in gdbs_dataset.deals:
            for defect in deal.defects:
                for field in required:
                    assert field in defect, f"Defect missing {field} in deal {deal.deal_key}"

    def test_defect_types_are_valid(self, gdbs_dataset: GDBSDataset) -> None:
        """Defect types must be from taxonomy."""
        valid_types = {
            "BROKEN_CHAIN",
            "MISSING_LINK",
            "UNKNOWN_SOURCE",
            "CONCEALMENT",
            "INCONSISTENCY",
            "ANOMALY_VS_STRONGER_SOURCES",
            "CHRONO_IMPOSSIBLE",
            "CHAIN_GRAFTING",
            "CIRCULARITY",
            "STALENESS",
            "UNIT_MISMATCH",
            "TIME_WINDOW_MISMATCH",
            "SCOPE_DRIFT",
            "IMPLAUSIBILITY",
        }
        for deal in gdbs_dataset.deals:
            for defect in deal.defects:
                dtype = defect.get("defect_type")
                assert dtype in valid_types, f"Invalid defect type {dtype} in deal {deal.deal_key}"

    def test_defect_severity_valid(self, gdbs_dataset: GDBSDataset) -> None:
        """Defect severity must be FATAL/MAJOR/MINOR."""
        valid_severity = {"FATAL", "MAJOR", "MINOR"}
        for deal in gdbs_dataset.deals:
            for defect in deal.defects:
                severity = defect.get("severity")
                assert severity in valid_severity, (
                    f"Invalid severity {severity} in deal {deal.deal_key}"
                )

    def test_cure_protocols_valid(self, gdbs_dataset: GDBSDataset) -> None:
        """Cure protocols must be from taxonomy."""
        valid_protocols = {
            "REQUEST_SOURCE",
            "REQUIRE_REAUDIT",
            "HUMAN_ARBITRATION",
            "RECONSTRUCT_CHAIN",
            "DISCARD_CLAIM",
        }
        for deal in gdbs_dataset.deals:
            for defect in deal.defects:
                protocol = defect.get("cure_protocol")
                assert protocol in valid_protocols, f"Invalid cure protocol {protocol}"


class TestTenantIsolation:
    """Test tenant isolation enforcement."""

    def test_wrong_tenant_yields_no_data(self, gdbs_dataset: GDBSDataset) -> None:
        """Accessing with wrong tenant_id must return no matching data."""
        wrong_tenant = "00000000-0000-0000-0000-000000000099"
        loader = GDBSLoader(GDBS_PATH)
        assert loader.validate_tenant_isolation(gdbs_dataset, wrong_tenant) is True

    def test_all_entities_have_correct_tenant(self, gdbs_dataset: GDBSDataset) -> None:
        """All entities must have the correct tenant_id."""
        expected = gdbs_dataset.tenant.tenant_id
        for deal in gdbs_dataset.deals:
            assert deal.tenant_id == expected
            for claim in deal.claims:
                assert claim.get("tenant_id") == expected
            for sanad in deal.sanads:
                assert sanad.get("tenant_id") == expected
            for evidence in deal.evidence:
                assert evidence.get("tenant_id") == expected


class TestExpectedOutcomes:
    """Test expected outcomes structure."""

    def test_all_deals_have_expected_outcomes(self, gdbs_dataset: GDBSDataset) -> None:
        """Every deal must have a corresponding expected outcome."""
        for deal in gdbs_dataset.deals:
            outcome = gdbs_dataset.get_expected_outcome(deal.deal_key)
            assert outcome is not None, f"No expected outcome for {deal.deal_key}"

    def test_expected_outcomes_have_all_claims(self, gdbs_dataset: GDBSDataset) -> None:
        """Expected outcomes must define expectations for C1-C7."""
        expected_keys = {"C1", "C2", "C3", "C4", "C5", "C6", "C7"}
        for deal_key, outcome in gdbs_dataset.expected_outcomes.items():
            claim_keys = set(outcome.expected_claims.keys())
            assert claim_keys == expected_keys, f"Outcome {deal_key} missing claims"
