"""Tests for IDIS SLO dashboards per IDIS_SLO_SLA_Runbooks_v6_3.md §8.1.

Verifies:
- Exactly 10 golden dashboards exist
- All required dashboard titles/categories present
- All dashboards include tenant_id variable for tenant isolation
- Validation fails closed on duplicates/missing required fields
- Deterministic export produces stable output
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from idis.monitoring.slo_dashboard import (
    GOLDEN_DASHBOARD_TITLES,
    export_grafana_json_bundle,
    get_golden_dashboards,
    validate_golden_dashboards,
)
from idis.monitoring.types import (
    DashboardPanelSpec,
    DashboardSpec,
    MonitoringValidationError,
    validate_dashboard_specs,
)


class TestGoldenDashboardCount:
    """Test that exactly 10 golden dashboards exist per §8.1."""

    def test_exactly_10_dashboards_exist(self) -> None:
        """Verify the exact count of golden dashboards."""
        dashboards = get_golden_dashboards()
        assert len(dashboards) == 10, f"Expected 10 golden dashboards, got {len(dashboards)}"

    def test_dashboard_titles_match_slo_spec(self) -> None:
        """Verify all dashboard titles match the SLO spec categories."""
        dashboards = get_golden_dashboards()
        actual_titles = {d.title for d in dashboards}
        expected_titles = set(GOLDEN_DASHBOARD_TITLES)

        assert actual_titles == expected_titles, (
            f"Dashboard title mismatch.\n"
            f"Missing: {expected_titles - actual_titles}\n"
            f"Extra: {actual_titles - expected_titles}"
        )

    def test_all_required_categories_present(self) -> None:
        """Verify all 10 required dashboard categories from §8.1 are present."""
        required_categories = [
            "API Availability and Latency",
            "Ingestion Throughput and Error Rates",
            "Queue Depth and Backlog",
            "Claim Registry Writes and Validator Rejects",
            "Sanad Grading Distribution Drift",
            "Calc Success Rate and Reproducibility",
            "Debate Completion Rate and Max-Round Stops",
            "Deliverable Generation Success Rate",
            "Audit Event Ingestion Lag and Coverage",
            "Integration Health",
        ]
        dashboards = get_golden_dashboards()
        actual_titles = [d.title for d in dashboards]

        for category in required_categories:
            assert category in actual_titles, f"Missing required dashboard category: {category}"


class TestTenantIsolation:
    """Test that all dashboards enforce tenant isolation."""

    def test_all_dashboards_have_tenant_variable(self) -> None:
        """Verify every dashboard includes tenant_id in required_variables."""
        dashboards = get_golden_dashboards()

        for dashboard in dashboards:
            assert "tenant_id" in dashboard.required_variables, (
                f"Dashboard '{dashboard.title}' missing required tenant_id variable"
            )

    def test_dashboard_spec_requires_tenant_id(self) -> None:
        """Verify DashboardSpec validation enforces tenant_id variable."""
        with pytest.raises(ValueError, match="tenant_id"):
            DashboardSpec(
                uid="test-no-tenant",
                title="Test Dashboard",
                panels=(
                    DashboardPanelSpec(
                        id=1,
                        title="Test Panel",
                        expr="test_metric",
                    ),
                ),
                required_variables=("some_other_var",),  # Missing tenant_id
            )

    def test_panel_expressions_reference_tenant_id(self) -> None:
        """Verify panel expressions include tenant_id filtering."""
        dashboards = get_golden_dashboards()

        for dashboard in dashboards:
            for panel in dashboard.panels:
                assert "tenant_id" in panel.expr, (
                    f"Panel '{panel.title}' in dashboard '{dashboard.title}' "
                    f"missing tenant_id in expression"
                )


class TestValidationFailsClosed:
    """Test that validation fails closed on invalid specs."""

    def test_validate_golden_dashboards_succeeds(self) -> None:
        """Verify validate_golden_dashboards passes with valid dashboards."""
        validate_golden_dashboards()  # Should not raise

    def test_validation_fails_on_duplicate_uid(self) -> None:
        """Verify validation fails when dashboard UIDs are duplicated."""
        panel = DashboardPanelSpec(id=1, title="Test", expr='metric{tenant_id="$tenant_id"}')
        duplicate_dashboards = (
            DashboardSpec(uid="same-uid", title="Dashboard 1", panels=(panel,)),
            DashboardSpec(uid="same-uid", title="Dashboard 2", panels=(panel,)),
        )

        with pytest.raises(MonitoringValidationError) as exc_info:
            validate_dashboard_specs(duplicate_dashboards)

        assert "Duplicate dashboard UID" in str(exc_info.value.errors)

    def test_validation_fails_on_duplicate_title(self) -> None:
        """Verify validation fails when dashboard titles are duplicated."""
        panel = DashboardPanelSpec(id=1, title="Test", expr='metric{tenant_id="$tenant_id"}')
        duplicate_dashboards = (
            DashboardSpec(uid="uid-1", title="Same Title", panels=(panel,)),
            DashboardSpec(uid="uid-2", title="Same Title", panels=(panel,)),
        )

        with pytest.raises(MonitoringValidationError) as exc_info:
            validate_dashboard_specs(duplicate_dashboards)

        assert "Duplicate dashboard title" in str(exc_info.value.errors)

    def test_validation_fails_on_empty_collection(self) -> None:
        """Verify validation fails on empty dashboard collection."""
        with pytest.raises(MonitoringValidationError, match="cannot be empty"):
            validate_dashboard_specs(())

    def test_validation_fails_on_missing_tenant_variable(self) -> None:
        """Verify validation catches dashboards missing tenant_id variable."""
        # This should fail at DashboardSpec creation level
        with pytest.raises(ValueError, match="tenant_id"):
            DashboardSpec(
                uid="test-uid",
                title="Test",
                panels=(
                    DashboardPanelSpec(
                        id=1,
                        title="Test",
                        expr="metric",
                    ),
                ),
                required_variables=(),  # Empty - missing tenant_id
            )

    def test_validation_fails_on_duplicate_panel_ids(self) -> None:
        """Verify validation fails when panel IDs are duplicated within dashboard."""
        with pytest.raises(ValueError, match="Panel IDs must be unique"):
            DashboardSpec(
                uid="test-uid",
                title="Test Dashboard",
                panels=(
                    DashboardPanelSpec(
                        id=1, title="Panel 1", expr='metric{tenant_id="$tenant_id"}'
                    ),
                    DashboardPanelSpec(
                        id=1, title="Panel 2", expr='metric{tenant_id="$tenant_id"}'
                    ),
                ),
            )


class TestDeterministicExport:
    """Test that Grafana JSON export is deterministic."""

    def test_export_creates_10_files(self) -> None:
        """Verify export creates exactly 10 JSON files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            created_files = export_grafana_json_bundle(out_dir)

            assert len(created_files) == 10, f"Expected 10 files, got {len(created_files)}"
            for f in created_files:
                assert f.exists(), f"File {f} does not exist"
                assert f.suffix == ".json", f"File {f} is not a JSON file"

    def test_export_produces_valid_json(self) -> None:
        """Verify all exported files are valid JSON."""
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            created_files = export_grafana_json_bundle(out_dir)

            for f in created_files:
                with f.open() as fp:
                    data = json.load(fp)  # Should not raise
                    assert "uid" in data
                    assert "title" in data
                    assert "panels" in data
                    assert "templating" in data

    def test_export_includes_tenant_variable(self) -> None:
        """Verify exported JSON includes tenant_id template variable."""
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            created_files = export_grafana_json_bundle(out_dir)

            for f in created_files:
                with f.open() as fp:
                    data = json.load(fp)
                    templating = data.get("templating", {})
                    var_names = [v["name"] for v in templating.get("list", [])]
                    assert "tenant_id" in var_names, (
                        f"File {f.name} missing tenant_id template variable"
                    )

    def test_export_is_deterministic(self) -> None:
        """Verify multiple exports produce identical output."""
        with tempfile.TemporaryDirectory() as tmpdir1, tempfile.TemporaryDirectory() as tmpdir2:
            files1 = export_grafana_json_bundle(Path(tmpdir1))
            files2 = export_grafana_json_bundle(Path(tmpdir2))

            assert len(files1) == len(files2)

            for f1, f2 in zip(sorted(files1), sorted(files2), strict=True):
                content1 = f1.read_text()
                content2 = f2.read_text()
                assert content1 == content2, (
                    f"Export not deterministic for {f1.name}.\n"
                    f"First export:\n{content1[:500]}\n"
                    f"Second export:\n{content2[:500]}"
                )

    def test_export_json_has_sorted_keys(self) -> None:
        """Verify JSON export uses sorted keys for stability."""
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            created_files = export_grafana_json_bundle(out_dir)

            for f in created_files:
                content = f.read_text()
                data = json.loads(content)
                # Re-serialize with sorted keys and compare
                expected = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
                assert content == expected, f"File {f.name} does not have sorted keys"


class TestDashboardStructure:
    """Test dashboard structural requirements."""

    def test_all_dashboards_have_panels(self) -> None:
        """Verify every dashboard has at least one panel."""
        dashboards = get_golden_dashboards()

        for dashboard in dashboards:
            assert len(dashboard.panels) > 0, f"Dashboard '{dashboard.title}' has no panels"

    def test_all_dashboards_have_unique_uids(self) -> None:
        """Verify all dashboard UIDs are unique."""
        dashboards = get_golden_dashboards()
        uids = [d.uid for d in dashboards]

        assert len(uids) == len(set(uids)), "Dashboard UIDs are not unique"

    def test_all_dashboards_have_slo_tag(self) -> None:
        """Verify all dashboards have the 'slo' tag for discoverability."""
        dashboards = get_golden_dashboards()

        for dashboard in dashboards:
            assert "slo" in dashboard.tags, f"Dashboard '{dashboard.title}' missing 'slo' tag"

    def test_dashboards_have_descriptions(self) -> None:
        """Verify all dashboards have meaningful descriptions."""
        dashboards = get_golden_dashboards()

        for dashboard in dashboards:
            assert dashboard.description, f"Dashboard '{dashboard.title}' has empty description"
            assert len(dashboard.description) >= 20, (
                f"Dashboard '{dashboard.title}' description too short"
            )
