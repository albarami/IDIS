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

    def test_manifest_defines_100_deals(self, gdbs_dataset: GDBSDataset) -> None:
        """Manifest must define exactly 100 deals per v6.3 GDBS-F spec."""
        deals = gdbs_dataset.manifest.get("deals", [])
        assert len(deals) == 100, f"Expected 100 deals (GDBS-F spec), got {len(deals)}"

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

    def test_has_100_deals(self, gdbs_dataset: GDBSDataset) -> None:
        """Dataset must have exactly 100 deals per v6.3 GDBS-F spec."""
        assert len(gdbs_dataset.deals) == 100, f"Expected 100 deals, got {len(gdbs_dataset.deals)}"

    def test_deals_have_unique_ids(self, gdbs_dataset: GDBSDataset) -> None:
        """All deals must have unique deal_ids."""
        deal_ids = [d.deal_id for d in gdbs_dataset.deals]
        assert len(deal_ids) == len(set(deal_ids)), "Duplicate deal_ids found"

    def test_deals_cover_all_adversarial_scenarios(self, gdbs_dataset: GDBSDataset) -> None:
        """Deals 1-8 must cover all 8 adversarial scenarios."""
        expected_adversarial = {
            "clean",
            "contradiction",
            "unit_mismatch",
            "time_window_mismatch",
            "missing_evidence",
            "calc_conflict",
            "chain_break",
            "version_drift",
        }
        adversarial_deals = [d for d in gdbs_dataset.deals if int(d.deal_key.split("_")[1]) <= 8]
        actual_scenarios = {d.scenario for d in adversarial_deals}
        assert actual_scenarios == expected_adversarial, (
            f"Adversarial scenario mismatch: {actual_scenarios}"
        )

    def test_deals_9_to_100_are_clean(self, gdbs_dataset: GDBSDataset) -> None:
        """Deals 9-100 must all be clean scenarios."""
        clean_deals = [d for d in gdbs_dataset.deals if int(d.deal_key.split("_")[1]) > 8]
        assert len(clean_deals) == 92, f"Expected 92 clean deals, got {len(clean_deals)}"
        for deal in clean_deals:
            assert deal.scenario == "clean", (
                f"Deal {deal.deal_key} should be clean, got {deal.scenario}"
            )

    @pytest.mark.parametrize("deal_key", [f"deal_{i:03d}" for i in range(1, 101)])
    def test_deal_has_required_components(self, gdbs_dataset: GDBSDataset, deal_key: str) -> None:
        """Each deal must have artifacts, spans, claims, evidence, sanads, calcs."""
        deal = gdbs_dataset.get_deal(deal_key)
        assert deal is not None, f"Deal {deal_key} not found"
        assert len(deal.artifacts) >= 1, f"Deal {deal_key} missing artifacts"
        assert len(deal.spans) >= 1, f"Deal {deal_key} missing spans"
        assert len(deal.claims) == 7, f"Deal {deal_key} must have 7 claims (C1-C7)"

        # deal_005 is the "missing_evidence" adversarial scenario - intentionally has NO evidence
        if deal_key == "deal_005":
            assert len(deal.evidence) == 0, (
                f"Deal {deal_key} is missing_evidence scenario - must have ZERO evidence"
            )
        else:
            assert len(deal.evidence) >= 1, f"Deal {deal_key} missing evidence"

        assert len(deal.sanads) == 7, f"Deal {deal_key} must have 7 sanads"
        assert len(deal.calcs) >= 1, f"Deal {deal_key} missing mandatory calcs"


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

    def test_100_expected_outcomes_exist(self, gdbs_dataset: GDBSDataset) -> None:
        """Must have exactly 100 expected outcomes."""
        assert len(gdbs_dataset.expected_outcomes) == 100, (
            f"Expected 100 outcomes, got {len(gdbs_dataset.expected_outcomes)}"
        )


class TestMandatoryCalcs:
    """Test mandatory calcs.json for all deals."""

    def test_all_deals_have_calcs(self, gdbs_dataset: GDBSDataset) -> None:
        """All 100 deals must have calcs.json with calc_sanads."""
        for deal in gdbs_dataset.deals:
            assert len(deal.calcs) >= 1, f"Deal {deal.deal_key} missing mandatory calcs"

    def test_calcs_have_required_fields(self, gdbs_dataset: GDBSDataset) -> None:
        """All calc_sanads must have required fields."""
        required = ["calc_sanad_id", "tenant_id", "calc_type", "inputs", "output", "calc_grade"]
        for deal in gdbs_dataset.deals:
            for calc in deal.calcs:
                for field in required:
                    assert field in calc, f"Calc missing {field} in deal {deal.deal_key}"

    def test_calcs_include_gm_and_runway(self, gdbs_dataset: GDBSDataset) -> None:
        """Each deal should have GM and runway calculations."""
        for deal in gdbs_dataset.deals:
            calc_types = {c.get("calc_type") for c in deal.calcs}
            assert "GROSS_MARGIN" in calc_types, f"Deal {deal.deal_key} missing GROSS_MARGIN calc"
            assert "RUNWAY" in calc_types, f"Deal {deal.deal_key} missing RUNWAY calc"

    def test_calcs_have_reproducibility_hash(self, gdbs_dataset: GDBSDataset) -> None:
        """All calcs must have reproducibility_hash for determinism."""
        for deal in gdbs_dataset.deals:
            for calc in deal.calcs:
                assert "reproducibility_hash" in calc, (
                    f"Calc {calc.get('calc_id')} missing reproducibility_hash in {deal.deal_key}"
                )
                assert calc["reproducibility_hash"], "reproducibility_hash must not be empty"


class TestAuditTaxonomyValidation:
    """Test audit taxonomy coverage and validation."""

    # Required event types per IDIS Audit Event Taxonomy v6.3
    REQUIRED_EVENT_TYPES = {
        "deal.created",
        "deal.updated",
        "deal.status.changed",
        "document.created",
        "claim.created",
        "claim.updated",
        "claim.verdict.changed",
        "claim.grade.changed",
        "sanad.created",
        "sanad.updated",
        "sanad.defect.added",
        "defect.created",
        "defect.cured",
        "defect.waived",
        "calc.started",
        "calc.completed",
        "calc.failed",
    }

    def test_audit_expectations_exist(self, gdbs_dataset: GDBSDataset) -> None:
        """Audit expectations file must exist and be loaded."""
        assert gdbs_dataset.audit_expectations is not None
        assert "event_categories" in gdbs_dataset.audit_expectations

    def test_required_event_types_defined(self, gdbs_dataset: GDBSDataset) -> None:
        """All required event types must be defined in audit expectations."""
        audit = gdbs_dataset.audit_expectations
        defined_events: set[str] = set()

        for category_data in audit.get("event_categories", {}).values():
            for event in category_data.get("required_events", []):
                defined_events.add(event.get("event_type", ""))

        missing = self.REQUIRED_EVENT_TYPES - defined_events
        assert not missing, f"Missing required audit event types: {missing}"

    def test_event_types_have_severity(self, gdbs_dataset: GDBSDataset) -> None:
        """All event types must have severity defined."""
        audit = gdbs_dataset.audit_expectations
        valid_severities = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}

        for cat_name, category_data in audit.get("event_categories", {}).items():
            for event in category_data.get("required_events", []):
                severity = event.get("severity")
                assert severity in valid_severities, (
                    f"Invalid severity {severity} for {event.get('event_type')} in {cat_name}"
                )

    def test_event_types_have_required_fields_spec(self, gdbs_dataset: GDBSDataset) -> None:
        """All event types must specify required_fields."""
        audit = gdbs_dataset.audit_expectations

        for cat_name, category_data in audit.get("event_categories", {}).items():
            for event in category_data.get("required_events", []):
                assert "required_fields" in event, (
                    f"Event {event.get('event_type')} in {cat_name} missing required_fields"
                )

    def test_per_deal_audit_expectations(self, gdbs_dataset: GDBSDataset) -> None:
        """Per-deal audit expectations must exist for adversarial deals."""
        audit = gdbs_dataset.audit_expectations
        per_deal = audit.get("per_deal_expected_events", {})

        # Check adversarial deals have audit expectations
        adversarial_keys = [
            "deal_001_clean",
            "deal_002_contradiction",
            "deal_003_unit_mismatch",
            "deal_004_time_window_mismatch",
            "deal_005_missing_evidence",
            "deal_006_calc_conflict",
            "deal_007_chain_break",
            "deal_008_version_drift",
        ]
        for key in adversarial_keys:
            assert key in per_deal, f"Missing per-deal audit expectations for {key}"

    def test_tenant_isolation_audit_events(self, gdbs_dataset: GDBSDataset) -> None:
        """Tenant isolation violation events must be defined."""
        audit = gdbs_dataset.audit_expectations
        security_events = audit.get("event_categories", {}).get("security_events", {})
        event_types = {e.get("event_type") for e in security_events.get("required_events", [])}

        assert "tenant.isolation.violation" in event_types, (
            "tenant.isolation.violation event type must be defined"
        )

    def test_required_events_accepted_by_validator(self, gdbs_dataset: GDBSDataset) -> None:
        """All required event types must be accepted by the validator (B3 Codex fix).

        Cross-checks audit expectations against the validator source of truth:
        src/idis/validators/audit_event_validator.py VALID_EVENT_PREFIXES.
        """
        from idis.validators.audit_event_validator import VALID_EVENT_PREFIXES

        audit = gdbs_dataset.audit_expectations

        # Collect all event types from audit expectations
        all_event_types: set[str] = set()
        for category_data in audit.get("event_categories", {}).values():
            for event in category_data.get("required_events", []):
                event_type = event.get("event_type", "")
                if event_type:
                    all_event_types.add(event_type)

        # Validate each event type starts with a valid prefix
        invalid_events: list[str] = []
        for event_type in all_event_types:
            valid = any(event_type.startswith(prefix) for prefix in VALID_EVENT_PREFIXES)
            if not valid:
                invalid_events.append(event_type)

        assert not invalid_events, (
            f"Event types not accepted by validator (invalid prefix): {invalid_events}. "
            f"Valid prefixes: {sorted(VALID_EVENT_PREFIXES)}"
        )

    def test_validator_prefixes_cover_required_categories(self, gdbs_dataset: GDBSDataset) -> None:
        """Validator VALID_EVENT_PREFIXES must cover all audit taxonomy categories."""
        from idis.validators.audit_event_validator import VALID_EVENT_PREFIXES

        # These prefixes are required per audit taxonomy
        required_prefixes = {"deal.", "claim.", "sanad.", "defect.", "calc.", "tenant."}

        missing = required_prefixes - VALID_EVENT_PREFIXES
        assert not missing, f"Validator missing required prefixes: {missing}"


class TestRealArtifacts:
    """Test that real PDF/XLSX artifacts exist."""

    def test_artifacts_have_sha256(self, gdbs_dataset: GDBSDataset) -> None:
        """All artifacts must have sha256 hash."""
        for deal in gdbs_dataset.deals:
            for artifact in deal.artifacts:
                assert "sha256" in artifact, f"Artifact missing sha256 in {deal.deal_key}"
                sha = artifact["sha256"]
                assert len(sha) == 64, f"Invalid sha256 length in {deal.deal_key}"

    def test_artifacts_have_file_size(self, gdbs_dataset: GDBSDataset) -> None:
        """All artifacts must have file_size_bytes."""
        for deal in gdbs_dataset.deals:
            for artifact in deal.artifacts:
                size = artifact.get("file_size_bytes")
                if size is not None:
                    assert size > 0, f"Invalid file size in {deal.deal_key}"

    def test_artifact_files_exist(self, gdbs_dataset: GDBSDataset) -> None:
        """Real artifact files must exist on disk."""

        for deal in gdbs_dataset.deals:
            deal_num = int(deal.deal_key.split("_")[1])
            if deal_num <= 8:
                deal_dir_suffix = {
                    1: "clean",
                    2: "contradiction",
                    3: "unit_mismatch",
                    4: "time_window_mismatch",
                    5: "missing_evidence",
                    6: "calc_conflict",
                    7: "chain_break",
                    8: "version_drift",
                }[deal_num]
                deal_dir = f"deal_{deal_num:03d}_{deal_dir_suffix}"
            else:
                deal_dir = f"deal_{deal_num:03d}_clean"

            artifacts_dir = gdbs_dataset.dataset_path / "deals" / deal_dir / "artifacts"

            # Check PDF exists (deal_008 has v1/v2 versioned PDFs)
            if deal_num == 8:
                pdf_v1 = artifacts_dir / "pitch_deck_v1.pdf"
                pdf_v2 = artifacts_dir / "pitch_deck_v2.pdf"
                assert pdf_v1.exists(), f"Missing pitch_deck_v1.pdf for {deal.deal_key}"
                assert pdf_v2.exists(), f"Missing pitch_deck_v2.pdf for {deal.deal_key}"
            else:
                pdf_path = artifacts_dir / "pitch_deck.pdf"
                assert pdf_path.exists(), f"Missing pitch_deck.pdf for {deal.deal_key}"
                assert pdf_path.stat().st_size > 0, f"Empty pitch_deck.pdf for {deal.deal_key}"

            # Check XLSX exists
            xlsx_path = artifacts_dir / "financials.xlsx"
            assert xlsx_path.exists(), f"Missing financials.xlsx for {deal.deal_key}"
            assert xlsx_path.stat().st_size > 0, f"Empty financials.xlsx for {deal.deal_key}"


class TestArtifactHashVerification:
    """Test that artifact sha256 and file_size_bytes match actual files (B1 Codex fix)."""

    DEAL_DIR_SUFFIX = {
        1: "clean",
        2: "contradiction",
        3: "unit_mismatch",
        4: "time_window_mismatch",
        5: "missing_evidence",
        6: "calc_conflict",
        7: "chain_break",
        8: "version_drift",
    }

    def _get_deal_dir(self, gdbs_dataset: GDBSDataset, deal_num: int) -> Path:
        """Get the directory path for a deal."""
        if deal_num <= 8:
            suffix = self.DEAL_DIR_SUFFIX[deal_num]
            deal_dir = f"deal_{deal_num:03d}_{suffix}"
        else:
            deal_dir = f"deal_{deal_num:03d}_clean"
        return gdbs_dataset.dataset_path / "deals" / deal_dir

    def _compute_sha256(self, file_path: Path) -> str:
        """Compute SHA256 hash of a file."""
        import hashlib

        content = file_path.read_bytes()
        return hashlib.sha256(content).hexdigest()

    @pytest.mark.parametrize("deal_num", list(range(1, 9)))
    def test_adversarial_artifact_hash_match(
        self, gdbs_dataset: GDBSDataset, deal_num: int
    ) -> None:
        """Adversarial deals 001-008: artifact sha256 and size must match actual files."""
        deal_dir = self._get_deal_dir(gdbs_dataset, deal_num)
        artifacts_dir = deal_dir / "artifacts"

        # Load artifacts.json
        import json

        artifacts_json = deal_dir / "artifacts.json"
        assert artifacts_json.exists(), f"Missing artifacts.json for deal_{deal_num:03d}"
        data = json.loads(artifacts_json.read_text(encoding="utf-8"))

        for artifact in data.get("artifacts", []):
            filename = artifact.get("filename")
            expected_sha256 = artifact.get("sha256")
            expected_size = artifact.get("file_size_bytes")

            assert filename, f"Artifact missing filename in deal_{deal_num:03d}"
            assert expected_sha256, f"Artifact {filename} missing sha256"
            assert expected_size, f"Artifact {filename} missing file_size_bytes"

            # Verify file exists
            file_path = artifacts_dir / filename
            assert file_path.exists(), (
                f"Artifact file {filename} does not exist for deal_{deal_num:03d}"
            )

            # Verify size matches
            actual_size = file_path.stat().st_size
            assert actual_size == expected_size, (
                f"Size mismatch for {filename} in deal_{deal_num:03d}: "
                f"expected {expected_size}, got {actual_size}"
            )

            # Verify sha256 matches
            actual_sha256 = self._compute_sha256(file_path)
            assert actual_sha256 == expected_sha256, (
                f"SHA256 mismatch for {filename} in deal_{deal_num:03d}: "
                f"expected {expected_sha256}, got {actual_sha256}"
            )

    def test_all_100_deals_have_artifacts_json(self, gdbs_dataset: GDBSDataset) -> None:
        """All 100 deals must have a valid artifacts.json file."""
        import json

        for deal in gdbs_dataset.deals:
            deal_num = int(deal.deal_key.split("_")[1])
            deal_dir = self._get_deal_dir(gdbs_dataset, deal_num)
            artifacts_json = deal_dir / "artifacts.json"

            assert artifacts_json.exists(), f"Missing artifacts.json for {deal.deal_key}"
            data = json.loads(artifacts_json.read_text(encoding="utf-8"))
            assert "artifacts" in data, f"Invalid artifacts.json for {deal.deal_key}"
            assert len(data["artifacts"]) >= 1, f"No artifacts in {deal.deal_key}"
