"""IDIS Core Alert Rules - Prometheus-compatible alert specifications.

Implements the core alerts from IDIS_SLO_SLA_Runbooks_v6_3.md §8.2:
- API 5xx > threshold for 5 min (SEV-2)
- Ingestion failure rate > 2% for 15 min (SEV-2)
- OCR queue time p95 > 60 min for 30 min (SEV-3)
- Audit ingestion lag > 5 min (SEV-2)
- Missing audit events for mutating endpoint detected (SEV-1)
- No-Free-Facts validator failure in deliverables pipeline (SEV-1)
- Tenant isolation violation signal (SEV-1)
- Calc reproducibility check failure > 0.1% in 24h (SEV-2)

All alerts include:
- Severity label (SEV-1, SEV-2, SEV-3)
- Runbook annotation pointing to docs/runbooks/RB-XX_*.md
- Summary and description annotations
- Tenant-safe expressions (filter by tenant_id where applicable)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from idis.monitoring.types import (
    AlertRuleSpec,
    MonitoringValidationError,
    Severity,
    validate_alert_specs,
)


def _create_api_5xx_alert() -> AlertRuleSpec:
    """Alert: API 5xx error rate exceeds threshold."""
    return AlertRuleSpec(
        name="IDISApi5xxErrorRate",
        severity="SEV-2",
        expr=(
            '(sum by (tenant_id) (rate(http_requests_total{status=~"5.."}[5m]))'
            " / sum by (tenant_id) (rate(http_requests_total[5m]))) * 100 > 1"
        ),
        for_duration="5m",
        labels={"team": "platform", "component": "api"},
        annotations={
            "summary": "API 5xx error rate exceeds 1% for tenant {{ $labels.tenant_id }}",
            "description": (
                "The API is returning more than 1% 5xx errors over the last 5 minutes. "
                'Current rate: {{ $value | printf "%.2f" }}%. '
                "Check API gateway health, database connectivity, and recent deployments."
            ),
            "runbook": "docs/runbooks/RB-01_api_outage.md",
        },
    )


def _create_ingestion_failure_alert() -> AlertRuleSpec:
    """Alert: Ingestion failure rate exceeds threshold."""
    return AlertRuleSpec(
        name="IDISIngestionFailureRate",
        severity="SEV-2",
        expr=(
            "(sum by (tenant_id) (rate(ingestion_errors_total[15m]))"
            " / sum by (tenant_id) (rate(ingestion_attempts_total[15m]))) * 100 > 2"
        ),
        for_duration="15m",
        labels={"team": "data", "component": "ingestion"},
        annotations={
            "summary": "Ingestion failure rate exceeds 2% for tenant {{ $labels.tenant_id }}",
            "description": (
                "Document ingestion is failing at more than 2% over the last 15 minutes. "
                'Current rate: {{ $value | printf "%.2f" }}%. '
                "Check OCR workers, object store access, and parser health."
            ),
            "runbook": "docs/runbooks/RB-02_ingestion_failure.md",
        },
    )


def _create_ocr_queue_time_alert() -> AlertRuleSpec:
    """Alert: OCR queue wait time exceeds threshold."""
    return AlertRuleSpec(
        name="IDISOCRQueueTimeHigh",
        severity="SEV-3",
        expr=(
            "histogram_quantile(0.95, "
            "sum by (tenant_id, le) (rate(ocr_queue_wait_seconds_bucket[30m]))) > 3600"
        ),
        for_duration="30m",
        labels={"team": "data", "component": "ocr"},
        annotations={
            "summary": (
                "OCR queue p95 wait time exceeds 60 minutes for tenant {{ $labels.tenant_id }}"
            ),
            "description": (
                "Documents are waiting more than 60 minutes (p95) in the OCR queue. "
                "Current p95: {{ $value | humanizeDuration }}. "
                "Check OCR worker capacity and scaling."
            ),
            "runbook": "docs/runbooks/RB-02_ingestion_failure.md",
        },
    )


def _create_audit_lag_alert() -> AlertRuleSpec:
    """Alert: Audit event ingestion lag exceeds threshold."""
    return AlertRuleSpec(
        name="IDISAuditIngestionLag",
        severity="SEV-2",
        expr="audit_ingestion_lag_seconds > 300",
        for_duration="5m",
        labels={"team": "security", "component": "audit"},
        annotations={
            "summary": "Audit event ingestion lag exceeds 5 minutes",
            "description": (
                "Audit events are being ingested with more than 5 minutes of lag. "
                "Current lag: {{ $value | humanizeDuration }}. "
                "This may indicate audit pipeline issues and potential compliance risk."
            ),
            "runbook": "docs/runbooks/RB-08_audit_lag.md",
            "tenant_scope": "global",
        },
    )


def _create_missing_audit_events_alert() -> AlertRuleSpec:
    """Alert: Missing audit events detected (SEV-1)."""
    return AlertRuleSpec(
        name="IDISMissingAuditEvents",
        severity="SEV-1",
        expr="increase(audit_events_missing_total[5m]) > 0",
        for_duration="1m",
        labels={"team": "security", "component": "audit"},
        annotations={
            "summary": "Missing audit events detected - potential compliance violation",
            "description": (
                "Mutating operations are occurring without corresponding audit events. "
                "This is a SEV-1 compliance incident. Immediate investigation required."
            ),
            "runbook": "docs/runbooks/RB-08_audit_lag.md",
            "tenant_scope": "global",
        },
    )


def _create_no_free_facts_violation_alert() -> AlertRuleSpec:
    """Alert: No-Free-Facts validator failure in deliverables (SEV-1)."""
    return AlertRuleSpec(
        name="IDISNoFreeFactsViolation",
        severity="SEV-1",
        expr=(
            'increase(deliverable_no_free_facts_failures_total{pipeline="ic_deliverables"}[5m]) > 0'
        ),
        for_duration="1m",
        labels={"team": "backend", "component": "deliverables"},
        annotations={
            "summary": "No-Free-Facts violation in IC deliverables pipeline",
            "description": (
                "A deliverable containing unlinked factual statements has been detected. "
                "This is a SEV-1 trust invariant violation. "
                "Affected deliverables must be blocked and investigated."
            ),
            "runbook": "docs/runbooks/RB-03_claim_validator_spike.md",
        },
    )


def _create_tenant_isolation_alert() -> AlertRuleSpec:
    """Alert: Tenant isolation violation detected (SEV-1)."""
    return AlertRuleSpec(
        name="IDISTenantIsolationViolation",
        severity="SEV-1",
        expr="increase(tenant_isolation_violations_total[5m]) > 0",
        for_duration="1m",
        labels={"team": "security", "component": "isolation"},
        annotations={
            "summary": "Tenant isolation violation detected - CRITICAL SECURITY INCIDENT",
            "description": (
                "Cross-tenant data access has been detected. "
                "This is a SEV-1 security incident requiring immediate containment. "
                "Freeze affected credentials, preserve evidence, notify security leads."
            ),
            "runbook": "docs/runbooks/RB-10_security_incident.md",
        },
    )


def _create_calc_reproducibility_alert() -> AlertRuleSpec:
    """Alert: Calc reproducibility failure rate exceeds threshold."""
    return AlertRuleSpec(
        name="IDISCalcReproducibilityFailure",
        severity="SEV-2",
        expr=(
            "(sum by (tenant_id) (increase(calc_reproducibility_failures_total[24h]))"
            " / sum by (tenant_id) (increase(calc_reproducibility_checks_total[24h])))"
            " * 100 > 0.1"
        ),
        for_duration="1h",
        labels={"team": "data", "component": "calc"},
        annotations={
            "summary": (
                "Calc reproducibility failure rate exceeds 0.1% for tenant {{ $labels.tenant_id }}"
            ),
            "description": (
                "Deterministic calculations are producing inconsistent results. "
                'Current failure rate: {{ $value | printf "%.3f" }}%. '
                "Check calc service dependencies, environment drift, and formula versions."
            ),
            "runbook": "docs/runbooks/RB-05_calc_failure.md",
        },
    )


def _create_sanad_retrieval_latency_alert() -> AlertRuleSpec:
    """Alert: Sanad retrieval latency exceeds SLO."""
    return AlertRuleSpec(
        name="IDISSanadRetrievalLatencyHigh",
        severity="SEV-2",
        expr=(
            "histogram_quantile(0.95, "
            "sum by (tenant_id, le) (rate(sanad_retrieval_duration_seconds_bucket[5m]))) > 1.2"
        ),
        for_duration="10m",
        labels={"team": "data", "component": "sanad"},
        annotations={
            "summary": (
                "Sanad retrieval p95 latency exceeds 1.2s for tenant {{ $labels.tenant_id }}"
            ),
            "description": (
                "Sanad graph queries are taking too long. "
                "Current p95: {{ $value | humanizeDuration }}. "
                "Check graph DB health, indexes, and query patterns."
            ),
            "runbook": "docs/runbooks/RB-04_sanad_degradation.md",
        },
    )


def _create_debate_completion_rate_alert() -> AlertRuleSpec:
    """Alert: Debate completion rate below SLO."""
    return AlertRuleSpec(
        name="IDISDebateCompletionRateLow",
        severity="SEV-2",
        expr=(
            "(sum by (tenant_id) (rate(debate_completed_total[1h]))"
            " / sum by (tenant_id) (rate(debate_started_total[1h]))) * 100 < 98"
        ),
        for_duration="30m",
        labels={"team": "ml", "component": "debate"},
        annotations={
            "summary": "Debate completion rate below 98% for tenant {{ $labels.tenant_id }}",
            "description": (
                "Multi-agent debates are not completing at the expected rate. "
                'Current rate: {{ $value | printf "%.1f" }}%. '
                "Check debate orchestrator, agent health, and evidence retrieval."
            ),
            "runbook": "docs/runbooks/RB-06_debate_stuck.md",
        },
    )


def _create_deliverable_failure_alert() -> AlertRuleSpec:
    """Alert: Deliverable generation failure rate exceeds threshold."""
    return AlertRuleSpec(
        name="IDISDeliverableGenerationFailure",
        severity="SEV-2",
        expr=(
            "(sum by (tenant_id) (rate(deliverable_failures_total[1h]))"
            " / sum by (tenant_id) (rate(deliverable_attempts_total[1h]))) * 100 > 1"
        ),
        for_duration="15m",
        labels={"team": "backend", "component": "deliverables"},
        annotations={
            "summary": (
                "Deliverable generation failure rate exceeds 1% for tenant {{ $labels.tenant_id }}"
            ),
            "description": (
                "PDF/DOCX deliverable generation is failing above threshold. "
                'Current failure rate: {{ $value | printf "%.2f" }}%. '
                "Check template engine, object store permissions, and claim/calc references."
            ),
            "runbook": "docs/runbooks/RB-07_deliverable_failure.md",
        },
    )


def _create_integration_error_alert() -> AlertRuleSpec:
    """Alert: Integration provider errors exceed threshold."""
    return AlertRuleSpec(
        name="IDISIntegrationProviderErrors",
        severity="SEV-3",
        expr="sum by (tenant_id, provider) (rate(integration_errors_total[15m])) > 0.1",
        for_duration="15m",
        labels={"team": "integrations", "component": "external"},
        annotations={
            "summary": (
                "Integration errors for provider {{ $labels.provider }} "
                "affecting tenant {{ $labels.tenant_id }}"
            ),
            "description": (
                "External integration is experiencing elevated error rates. "
                "Check provider status, rate limits, and authentication tokens."
            ),
            "runbook": "docs/runbooks/RB-09_integration_outage.md",
        },
    )


def get_core_alerts() -> tuple[AlertRuleSpec, ...]:
    """Return the core alert rules per SLO/Runbooks §8.2.

    Returns:
        Tuple of AlertRuleSpec objects with deterministic ordering.
    """
    return (
        _create_api_5xx_alert(),
        _create_ingestion_failure_alert(),
        _create_ocr_queue_time_alert(),
        _create_audit_lag_alert(),
        _create_missing_audit_events_alert(),
        _create_no_free_facts_violation_alert(),
        _create_tenant_isolation_alert(),
        _create_calc_reproducibility_alert(),
        _create_sanad_retrieval_latency_alert(),
        _create_debate_completion_rate_alert(),
        _create_deliverable_failure_alert(),
        _create_integration_error_alert(),
    )


def validate_core_alerts() -> None:
    """Validate all core alerts meet requirements.

    Raises:
        MonitoringValidationError: If validation fails (fail-closed).
    """
    alerts = get_core_alerts()

    # Verify minimum alert count
    if len(alerts) < 8:
        raise MonitoringValidationError(f"Expected at least 8 core alerts, got {len(alerts)}")

    # Verify SEV-1 alerts exist for critical trust invariants
    sev1_alerts = [a for a in alerts if a.severity == "SEV-1"]
    required_sev1_names = {
        "IDISMissingAuditEvents",
        "IDISNoFreeFactsViolation",
        "IDISTenantIsolationViolation",
    }
    actual_sev1_names = {a.name for a in sev1_alerts}
    missing_sev1 = required_sev1_names - actual_sev1_names
    if missing_sev1:
        raise MonitoringValidationError(f"Missing required SEV-1 alerts: {missing_sev1}")

    # Run standard collection validation
    validate_alert_specs(alerts)


def _alert_to_prometheus_rule(alert: AlertRuleSpec) -> dict[str, Any]:
    """Convert an AlertRuleSpec to Prometheus rule format."""
    labels: dict[str, str] = {"severity": alert.severity}
    labels.update(alert.labels)

    rule: dict[str, Any] = {
        "alert": alert.name,
        "annotations": dict(sorted(alert.annotations.items())),
        "expr": alert.expr,
        "for": alert.for_duration,
        "labels": dict(sorted(labels.items())),
    }
    return rule


def render_prometheus_rule_groups() -> list[dict[str, Any]]:
    """Render all core alerts as Prometheus rule groups.

    Returns:
        List of rule group dictionaries ready for YAML serialization.
    """
    alerts = get_core_alerts()

    # Group alerts by severity for organization
    sev1_alerts: list[AlertRuleSpec] = []
    sev2_alerts: list[AlertRuleSpec] = []
    sev3_alerts: list[AlertRuleSpec] = []

    for alert in alerts:
        if alert.severity == "SEV-1":
            sev1_alerts.append(alert)
        elif alert.severity == "SEV-2":
            sev2_alerts.append(alert)
        else:
            sev3_alerts.append(alert)

    groups: list[dict[str, Any]] = []

    if sev1_alerts:
        groups.append(
            {
                "name": "idis-critical-alerts",
                "rules": [_alert_to_prometheus_rule(a) for a in sev1_alerts],
            }
        )

    if sev2_alerts:
        groups.append(
            {
                "name": "idis-high-alerts",
                "rules": [_alert_to_prometheus_rule(a) for a in sev2_alerts],
            }
        )

    if sev3_alerts:
        groups.append(
            {
                "name": "idis-medium-alerts",
                "rules": [_alert_to_prometheus_rule(a) for a in sev3_alerts],
            }
        )

    return groups


def export_prometheus_rules(path: Path) -> Path:
    """Export all core alerts as a Prometheus rules YAML file.

    Args:
        path: Path to write the rules file to. Parent directories created if needed.

    Returns:
        Path to the created rules file.

    Raises:
        MonitoringValidationError: If validation fails before export.
    """
    # Validate before export (fail-closed)
    validate_core_alerts()

    path.parent.mkdir(parents=True, exist_ok=True)

    rule_groups = render_prometheus_rule_groups()
    rules_doc: dict[str, Any] = {"groups": rule_groups}

    # Use PyYAML with default_flow_style=False for readable output
    # Sort keys for deterministic output
    with path.open("w", encoding="utf-8") as f:
        yaml.dump(
            rules_doc,
            f,
            default_flow_style=False,
            sort_keys=True,
            allow_unicode=True,
            width=120,
        )

    return path


def get_alerts_by_severity(severity: Severity) -> tuple[AlertRuleSpec, ...]:
    """Filter core alerts by severity level.

    Args:
        severity: The severity level to filter by.

    Returns:
        Tuple of matching AlertRuleSpec objects.
    """
    return tuple(a for a in get_core_alerts() if a.severity == severity)
