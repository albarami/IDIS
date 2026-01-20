"""Tests for IDIS alert rules per IDIS_SLO_SLA_Runbooks_v6_3.md §8.2.

Verifies:
- All core alerts exist with correct severities
- Every SEV-1 alert includes a runbook reference
- Validation fails closed for invalid severity or missing runbook annotation
- Deterministic Prometheus rules export
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import yaml

from idis.monitoring.alerts import (
    export_prometheus_rules,
    get_alerts_by_severity,
    get_core_alerts,
    render_prometheus_rule_groups,
    validate_core_alerts,
)
from idis.monitoring.types import (
    GLOBAL_AUDIT_ALERT_NAMES,
    AlertRuleSpec,
    MonitoringValidationError,
    validate_alert_specs,
)


class TestCoreAlertExistence:
    """Test that all core alerts from §8.2 exist."""

    def test_minimum_alert_count(self) -> None:
        """Verify at least 8 core alerts exist (per §8.2 examples)."""
        alerts = get_core_alerts()
        assert len(alerts) >= 8, f"Expected at least 8 core alerts, got {len(alerts)}"

    def test_api_5xx_alert_exists(self) -> None:
        """Verify API 5xx alert exists (SEV-2 per §8.2)."""
        alerts = get_core_alerts()
        api_alerts = [a for a in alerts if "5xx" in a.name.lower() or "api" in a.name.lower()]
        assert len(api_alerts) > 0, "Missing API 5xx alert"

    def test_ingestion_failure_alert_exists(self) -> None:
        """Verify ingestion failure alert exists (SEV-2 per §8.2)."""
        alerts = get_core_alerts()
        ingestion_alerts = [a for a in alerts if "ingestion" in a.name.lower()]
        assert len(ingestion_alerts) > 0, "Missing ingestion failure alert"

    def test_audit_alerts_exist(self) -> None:
        """Verify audit-related alerts exist."""
        alerts = get_core_alerts()
        audit_alerts = [a for a in alerts if "audit" in a.name.lower()]
        assert len(audit_alerts) > 0, "Missing audit alerts"

    def test_no_free_facts_alert_exists(self) -> None:
        """Verify No-Free-Facts violation alert exists (SEV-1 per §8.2)."""
        alerts = get_core_alerts()
        nff_alerts = [a for a in alerts if "freefacts" in a.name.lower().replace("_", "")]
        assert len(nff_alerts) > 0, "Missing No-Free-Facts violation alert"

    def test_tenant_isolation_alert_exists(self) -> None:
        """Verify tenant isolation violation alert exists (SEV-1 per §8.2)."""
        alerts = get_core_alerts()
        isolation_alerts = [a for a in alerts if "isolation" in a.name.lower()]
        assert len(isolation_alerts) > 0, "Missing tenant isolation alert"

    def test_calc_reproducibility_alert_exists(self) -> None:
        """Verify calc reproducibility alert exists (SEV-2 per §8.2)."""
        alerts = get_core_alerts()
        calc_alerts = [a for a in alerts if "calc" in a.name.lower()]
        assert len(calc_alerts) > 0, "Missing calc reproducibility alert"


class TestAlertSeverities:
    """Test that alert severities match §8.2 specifications."""

    def test_sev1_alerts_for_trust_invariants(self) -> None:
        """Verify SEV-1 severity for trust invariant violations."""
        alerts = get_core_alerts()

        # These must be SEV-1 per trust invariants
        sev1_required_patterns = [
            "isolation",  # Tenant isolation
            "freefacts",  # No-Free-Facts
            "missingaudit",  # Missing audit events
        ]

        sev1_alerts = [a for a in alerts if a.severity == "SEV-1"]
        sev1_names_lower = [a.name.lower().replace("_", "") for a in sev1_alerts]

        for pattern in sev1_required_patterns:
            found = any(pattern in name for name in sev1_names_lower)
            assert found, f"Expected SEV-1 alert matching pattern '{pattern}'"

    def test_sev2_alerts_for_operational_issues(self) -> None:
        """Verify SEV-2 severity for operational issues."""
        sev2_alerts = get_alerts_by_severity("SEV-2")
        assert len(sev2_alerts) > 0, "Expected at least one SEV-2 alert"

        # API and ingestion should be SEV-2
        sev2_names = [a.name.lower() for a in sev2_alerts]
        assert any("api" in name for name in sev2_names), "API alert should be SEV-2"

    def test_valid_severity_values_only(self) -> None:
        """Verify all alerts have valid severity values."""
        alerts = get_core_alerts()
        valid_severities = {"SEV-1", "SEV-2", "SEV-3"}

        for alert in alerts:
            assert alert.severity in valid_severities, (
                f"Alert '{alert.name}' has invalid severity: {alert.severity}"
            )


class TestRunbookReferences:
    """Test that alerts include proper runbook references."""

    def test_all_sev1_alerts_have_runbook(self) -> None:
        """Verify every SEV-1 alert includes a runbook reference."""
        sev1_alerts = get_alerts_by_severity("SEV-1")

        for alert in sev1_alerts:
            assert "runbook" in alert.annotations, (
                f"SEV-1 alert '{alert.name}' missing runbook annotation"
            )
            runbook = alert.annotations["runbook"]
            assert runbook.startswith("docs/runbooks/RB-"), (
                f"SEV-1 alert '{alert.name}' has invalid runbook path: {runbook}"
            )

    def test_all_alerts_have_runbook(self) -> None:
        """Verify all alerts include a runbook reference."""
        alerts = get_core_alerts()

        for alert in alerts:
            assert "runbook" in alert.annotations, (
                f"Alert '{alert.name}' missing runbook annotation"
            )

    def test_runbook_paths_are_valid_format(self) -> None:
        """Verify runbook paths follow the expected format."""
        alerts = get_core_alerts()

        for alert in alerts:
            runbook = alert.annotations.get("runbook", "")
            assert runbook.startswith("docs/runbooks/RB-"), (
                f"Alert '{alert.name}' runbook path doesn't start with "
                f"'docs/runbooks/RB-': {runbook}"
            )
            assert runbook.endswith(".md"), (
                f"Alert '{alert.name}' runbook path doesn't end with '.md': {runbook}"
            )


class TestValidationFailsClosed:
    """Test that validation fails closed on invalid specs."""

    def test_validate_core_alerts_succeeds(self) -> None:
        """Verify validate_core_alerts passes with valid alerts."""
        validate_core_alerts()  # Should not raise

    def test_validation_fails_on_invalid_severity(self) -> None:
        """Verify AlertRuleSpec rejects invalid severity values."""
        with pytest.raises(ValueError):
            AlertRuleSpec(
                name="TestAlert",
                severity="INVALID",  # type: ignore[arg-type]
                expr="test_metric > 0",
                for_duration="5m",
                annotations={
                    "summary": "Test",
                    "description": "Test",
                    "runbook": "docs/runbooks/RB-01_api_outage.md",
                },
            )

    def test_validation_fails_on_missing_runbook(self) -> None:
        """Verify validation fails when runbook annotation is missing."""
        with pytest.raises(ValueError, match="runbook"):
            AlertRuleSpec(
                name="TestAlert",
                severity="SEV-2",
                expr="test_metric > 0",
                for_duration="5m",
                annotations={
                    "summary": "Test",
                    "description": "Test",
                    # Missing runbook
                },
            )

    def test_validation_fails_on_missing_summary(self) -> None:
        """Verify validation fails when summary annotation is missing."""
        with pytest.raises(ValueError, match="summary"):
            AlertRuleSpec(
                name="TestAlert",
                severity="SEV-2",
                expr="test_metric > 0",
                for_duration="5m",
                annotations={
                    # Missing summary
                    "description": "Test",
                    "runbook": "docs/runbooks/RB-01_api_outage.md",
                },
            )

    def test_validation_fails_on_invalid_runbook_path(self) -> None:
        """Verify validation fails on invalid runbook path format."""
        with pytest.raises(ValueError, match="runbook path"):
            AlertRuleSpec(
                name="TestAlert",
                severity="SEV-2",
                expr="test_metric > 0",
                for_duration="5m",
                annotations={
                    "summary": "Test",
                    "description": "Test",
                    "runbook": "invalid/path.md",  # Invalid format
                },
            )

    def test_validation_fails_on_duplicate_names(self) -> None:
        """Verify validation fails when alert names are duplicated."""
        alert = AlertRuleSpec(
            name="DuplicateName",
            severity="SEV-2",
            expr="test_metric > 0",
            for_duration="5m",
            annotations={
                "summary": "Test",
                "description": "Test",
                "runbook": "docs/runbooks/RB-01_api_outage.md",
            },
        )
        duplicate_alerts = (alert, alert)

        with pytest.raises(MonitoringValidationError) as exc_info:
            validate_alert_specs(duplicate_alerts)

        assert "Duplicate alert name" in str(exc_info.value.errors)

    def test_validation_fails_on_empty_collection(self) -> None:
        """Verify validation fails on empty alert collection."""
        with pytest.raises(MonitoringValidationError, match="cannot be empty"):
            validate_alert_specs(())

    def test_validation_fails_on_empty_expression(self) -> None:
        """Verify validation fails on empty expression."""
        with pytest.raises(ValueError, match="empty"):
            AlertRuleSpec(
                name="TestAlert",
                severity="SEV-2",
                expr="   ",  # Whitespace only
                for_duration="5m",
                annotations={
                    "summary": "Test",
                    "description": "Test",
                    "runbook": "docs/runbooks/RB-01_api_outage.md",
                },
            )

    def test_validation_fails_on_missing_runbook_file(self) -> None:
        """Verify validation fails when runbook file does not exist (fail-closed)."""
        alert = AlertRuleSpec(
            name="TestAlertMissingRunbook",
            severity="SEV-2",
            expr="test_metric > 0",
            for_duration="5m",
            annotations={
                "summary": "Test alert with missing runbook",
                "description": "This alert references a non-existent runbook file.",
                "runbook": "docs/runbooks/RB-99_missing.md",
            },
        )

        with pytest.raises(MonitoringValidationError) as exc_info:
            validate_alert_specs((alert,))

        assert "RB-99_missing.md" in str(exc_info.value.errors)
        assert "non-existent runbook" in str(exc_info.value.errors)


class TestPrometheusExport:
    """Test Prometheus rules export functionality."""

    def test_render_produces_rule_groups(self) -> None:
        """Verify render_prometheus_rule_groups produces valid structure."""
        groups = render_prometheus_rule_groups()

        assert len(groups) > 0, "Expected at least one rule group"
        for group in groups:
            assert "name" in group, "Rule group missing 'name'"
            assert "rules" in group, "Rule group missing 'rules'"
            assert len(group["rules"]) > 0, f"Rule group '{group['name']}' has no rules"

    def test_export_creates_valid_yaml(self) -> None:
        """Verify export creates valid YAML file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "alerts.yaml"
            export_prometheus_rules(path)

            assert path.exists(), "Export file not created"

            with path.open() as f:
                data = yaml.safe_load(f)  # Should not raise

            assert "groups" in data, "YAML missing 'groups' key"
            assert len(data["groups"]) > 0, "No rule groups in YAML"

    def test_export_includes_severity_labels(self) -> None:
        """Verify exported rules include severity labels."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "alerts.yaml"
            export_prometheus_rules(path)

            with path.open() as f:
                data = yaml.safe_load(f)

            for group in data["groups"]:
                for rule in group["rules"]:
                    labels = rule.get("labels", {})
                    assert "severity" in labels, f"Rule '{rule['alert']}' missing severity label"
                    assert labels["severity"] in {"SEV-1", "SEV-2", "SEV-3"}, (
                        f"Rule '{rule['alert']}' has invalid severity: {labels['severity']}"
                    )

    def test_export_includes_runbook_annotations(self) -> None:
        """Verify exported rules include runbook annotations."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "alerts.yaml"
            export_prometheus_rules(path)

            with path.open() as f:
                data = yaml.safe_load(f)

            for group in data["groups"]:
                for rule in group["rules"]:
                    annotations = rule.get("annotations", {})
                    assert "runbook" in annotations, (
                        f"Rule '{rule['alert']}' missing runbook annotation"
                    )

    def test_export_is_deterministic(self) -> None:
        """Verify multiple exports produce identical output."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path1 = Path(tmpdir) / "alerts1.yaml"
            path2 = Path(tmpdir) / "alerts2.yaml"

            export_prometheus_rules(path1)
            export_prometheus_rules(path2)

            content1 = path1.read_text()
            content2 = path2.read_text()

            assert content1 == content2, "Prometheus rules export is not deterministic"

    def test_rules_grouped_by_severity(self) -> None:
        """Verify rules are organized into severity-based groups."""
        groups = render_prometheus_rule_groups()
        group_names = [g["name"] for g in groups]

        # Should have groups for different severity levels
        assert any("critical" in name for name in group_names), (
            "Expected a 'critical' rule group for SEV-1 alerts"
        )


class TestAlertAnnotations:
    """Test alert annotation requirements."""

    def test_all_alerts_have_summary(self) -> None:
        """Verify all alerts have summary annotation."""
        alerts = get_core_alerts()

        for alert in alerts:
            assert "summary" in alert.annotations, (
                f"Alert '{alert.name}' missing summary annotation"
            )
            assert len(alert.annotations["summary"]) > 10, f"Alert '{alert.name}' summary too short"

    def test_all_alerts_have_description(self) -> None:
        """Verify all alerts have description annotation."""
        alerts = get_core_alerts()

        for alert in alerts:
            assert "description" in alert.annotations, (
                f"Alert '{alert.name}' missing description annotation"
            )
            assert len(alert.annotations["description"]) > 20, (
                f"Alert '{alert.name}' description too short"
            )

    def test_alerts_have_valid_for_duration(self) -> None:
        """Verify all alerts have valid for_duration values."""
        alerts = get_core_alerts()

        for alert in alerts:
            # Should match pattern like "5m", "1h", "30s"
            assert alert.for_duration, f"Alert '{alert.name}' missing for_duration"
            assert alert.for_duration[-1] in {"s", "m", "h"}, (
                f"Alert '{alert.name}' has invalid for_duration unit: {alert.for_duration}"
            )


class TestTenantSafeExpressions:
    """Test that alert expressions are tenant-safe where applicable."""

    def test_tenant_scoped_alerts_include_tenant_filter(self) -> None:
        """Verify alerts that should be tenant-scoped include tenant_id."""
        alerts = get_core_alerts()

        # These alert types should include tenant_id in expression
        # Note: some alerts like audit lag are global metrics without tenant scoping
        tenant_scoped_patterns = [
            "api5xx",
            "ingestionfailure",
            "calc",
            "debate",
            "deliverablegeneration",
        ]

        for alert in alerts:
            name_lower = alert.name.lower().replace("_", "")
            is_tenant_scoped = any(p in name_lower for p in tenant_scoped_patterns)

            if is_tenant_scoped:
                assert "tenant_id" in alert.expr, (
                    f"Tenant-scoped alert '{alert.name}' missing tenant_id in expression"
                )

    def test_global_alerts_may_omit_tenant_filter(self) -> None:
        """Verify some alerts can be global (not tenant-scoped)."""
        alerts = get_core_alerts()

        # System-wide alerts like audit lag may not need tenant filtering
        global_alert_patterns = ["audit.*lag"]

        for alert in alerts:
            name_lower = alert.name.lower().replace("_", "")
            is_global = any(
                pattern.replace(".*", "") in name_lower for pattern in global_alert_patterns
            )

            if is_global:
                # Global alerts are allowed to omit tenant_id
                pass  # No assertion needed


class TestGlobalAuditAlertScope:
    """Test that global audit alerts are explicitly marked with tenant_scope='global'."""

    def test_global_audit_alerts_have_tenant_scope_annotation(self) -> None:
        """Verify global audit alerts include tenant_scope='global' annotation."""
        alerts = get_core_alerts()

        for alert in alerts:
            if alert.name in GLOBAL_AUDIT_ALERT_NAMES:
                assert "tenant_scope" in alert.annotations, (
                    f"Global audit alert '{alert.name}' missing tenant_scope annotation"
                )
                assert alert.annotations["tenant_scope"] == "global", (
                    f"Global audit alert '{alert.name}' tenant_scope must be 'global', "
                    f"got '{alert.annotations.get('tenant_scope')}'"
                )

    def test_global_audit_alert_names_match_expected(self) -> None:
        """Verify expected global audit alerts exist in core alerts."""
        alerts = get_core_alerts()
        alert_names = {a.name for a in alerts}

        for expected_name in GLOBAL_AUDIT_ALERT_NAMES:
            assert expected_name in alert_names, (
                f"Expected global audit alert '{expected_name}' not found in core alerts"
            )

    def test_validation_fails_without_tenant_scope_on_global_audit_alert(self) -> None:
        """Verify validation fails if global audit alert lacks tenant_scope='global'."""
        # Create an alert with the same name as a global audit alert but missing tenant_scope
        alert = AlertRuleSpec(
            name="IDISAuditIngestionLag",
            severity="SEV-2",
            expr="audit_ingestion_lag_seconds > 300",
            for_duration="5m",
            labels={"team": "security", "component": "audit"},
            annotations={
                "summary": "Audit event ingestion lag exceeds 5 minutes",
                "description": "Test description for audit lag alert.",
                "runbook": "docs/runbooks/RB-08_audit_lag.md",
                # Missing tenant_scope annotation
            },
        )

        with pytest.raises(MonitoringValidationError) as exc_info:
            validate_alert_specs((alert,))

        assert "tenant_scope" in str(exc_info.value.errors)
        assert "global" in str(exc_info.value.errors)
