"""IDIS Golden SLO Dashboards - Grafana-compatible dashboard specifications.

Implements the 10 required golden dashboards from IDIS_SLO_SLA_Runbooks_v6_3.md §8.1:
1. API Availability/Latency
2. Ingestion Throughput + Error Rates
3. Queue Depth/Backlog
4. Claim Registry Writes + Validator Rejects
5. Sanad Grading Distribution Drift
6. Calc Success Rate + Reproducibility Checks
7. Debate Completion Rate + Max-Round Stops
8. Deliverable Generation Success Rate
9. Audit Event Ingestion Lag + Coverage Checks
10. Integration Health (CRM/Docs/Providers)

All dashboards include:
- Tenant isolation via required tenant_id variable
- Deterministic JSON export with stable key ordering
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from idis.monitoring.types import (
    DashboardPanelSpec,
    DashboardSpec,
    MonitoringValidationError,
    validate_dashboard_specs,
)

GOLDEN_DASHBOARD_TITLES = (
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
)


def _create_api_availability_dashboard() -> DashboardSpec:
    """Dashboard 1: API Availability and Latency."""
    return DashboardSpec(
        uid="idis-api-availability",
        title="API Availability and Latency",
        description="Monitors API uptime, error rates, and latency SLOs per tenant.",
        tags=("slo", "api", "availability"),
        panels=(
            DashboardPanelSpec(
                id=1,
                title="API Availability (30d rolling)",
                panel_type="stat",
                expr=(
                    'sum(rate(http_requests_total{status=~"2..|3..",tenant_id="$tenant_id"}[30d]))'
                    ' / sum(rate(http_requests_total{tenant_id="$tenant_id"}[30d])) * 100'
                ),
                description="API availability percentage (target: 99.9%)",
            ),
            DashboardPanelSpec(
                id=2,
                title="5xx Error Rate",
                panel_type="timeseries",
                expr=(
                    'sum(rate(http_requests_total{status=~"5..",tenant_id="$tenant_id"}[5m]))'
                    ' / sum(rate(http_requests_total{tenant_id="$tenant_id"}[5m])) * 100'
                ),
                description="Percentage of 5xx errors over time",
            ),
            DashboardPanelSpec(
                id=3,
                title="API Latency p95 (GET)",
                panel_type="timeseries",
                expr=(
                    "histogram_quantile(0.95, sum(rate("
                    'http_request_duration_seconds_bucket{method="GET",tenant_id="$tenant_id"}[5m]'
                    ")) by (le))"
                ),
                description="95th percentile GET latency (target: <300ms)",
            ),
            DashboardPanelSpec(
                id=4,
                title="API Latency p95 (POST/PATCH)",
                panel_type="timeseries",
                expr=(
                    "histogram_quantile(0.95, sum(rate("
                    'http_request_duration_seconds_bucket{method=~"POST|PATCH",tenant_id="$tenant_id"}[5m]'
                    ")) by (le))"
                ),
                description="95th percentile POST/PATCH latency (target: <600ms)",
            ),
        ),
        required_variables=("tenant_id",),
    )


def _create_ingestion_dashboard() -> DashboardSpec:
    """Dashboard 2: Ingestion Throughput and Error Rates."""
    return DashboardSpec(
        uid="idis-ingestion",
        title="Ingestion Throughput and Error Rates",
        description="Monitors document ingestion pipeline health and success rates.",
        tags=("slo", "ingestion", "pipeline"),
        panels=(
            DashboardPanelSpec(
                id=1,
                title="Ingestion Success Rate",
                panel_type="stat",
                expr=(
                    'sum(rate(ingestion_success_total{tenant_id="$tenant_id"}[1h]))'
                    ' / sum(rate(ingestion_attempts_total{tenant_id="$tenant_id"}[1h])) * 100'
                ),
                description="Ingestion success rate (target: >=99%)",
            ),
            DashboardPanelSpec(
                id=2,
                title="Documents Ingested (Rate)",
                panel_type="timeseries",
                expr='sum(rate(ingestion_success_total{tenant_id="$tenant_id"}[5m]))',
                description="Documents successfully ingested per second",
            ),
            DashboardPanelSpec(
                id=3,
                title="Ingestion Errors by Type",
                panel_type="timeseries",
                expr=(
                    'sum by (error_type) (rate(ingestion_errors_total{tenant_id="$tenant_id"}[5m]))'
                ),
                description="Ingestion errors broken down by type",
            ),
            DashboardPanelSpec(
                id=4,
                title="Extraction Gate Pass Rate",
                panel_type="stat",
                expr=(
                    'sum(rate(extraction_gate_pass_total{tenant_id="$tenant_id"}[1h]))'
                    ' / sum(rate(extraction_gate_attempts_total{tenant_id="$tenant_id"}[1h])) * 100'
                ),
                description="Extraction gate pass rate (target: >=95%)",
            ),
        ),
        required_variables=("tenant_id",),
    )


def _create_queue_depth_dashboard() -> DashboardSpec:
    """Dashboard 3: Queue Depth and Backlog."""
    return DashboardSpec(
        uid="idis-queue-depth",
        title="Queue Depth and Backlog",
        description="Monitors queue depths and processing backlogs across services.",
        tags=("slo", "queue", "backlog"),
        panels=(
            DashboardPanelSpec(
                id=1,
                title="Ingestion Queue Depth",
                panel_type="timeseries",
                expr='ingestion_queue_depth{tenant_id="$tenant_id"}',
                description="Number of documents waiting in ingestion queue",
            ),
            DashboardPanelSpec(
                id=2,
                title="Ingestion Queue Time p95",
                panel_type="stat",
                expr=(
                    "histogram_quantile(0.95, sum(rate("
                    'ingestion_queue_wait_seconds_bucket{tenant_id="$tenant_id"}[15m])) by (le))'
                ),
                description="95th percentile queue wait time (target: <10min)",
            ),
            DashboardPanelSpec(
                id=3,
                title="OCR Queue Depth",
                panel_type="timeseries",
                expr='ocr_queue_depth{tenant_id="$tenant_id"}',
                description="Number of documents waiting for OCR processing",
            ),
            DashboardPanelSpec(
                id=4,
                title="OCR Queue Time p95",
                panel_type="stat",
                expr=(
                    "histogram_quantile(0.95, sum(rate("
                    'ocr_queue_wait_seconds_bucket{tenant_id="$tenant_id"}[30m])) by (le))'
                ),
                description="95th percentile OCR queue wait time (target: <30min)",
            ),
            DashboardPanelSpec(
                id=5,
                title="Run Scheduling Delay p95",
                panel_type="stat",
                expr=(
                    "histogram_quantile(0.95, sum(rate("
                    'run_scheduling_delay_seconds_bucket{tenant_id="$tenant_id"}[15m])) by (le))'
                ),
                description="95th percentile run scheduling delay (target: <5min)",
            ),
        ),
        required_variables=("tenant_id",),
    )


def _create_claim_registry_dashboard() -> DashboardSpec:
    """Dashboard 4: Claim Registry Writes and Validator Rejects."""
    return DashboardSpec(
        uid="idis-claim-registry",
        title="Claim Registry Writes and Validator Rejects",
        description="Monitors claim creation and validation pipeline health.",
        tags=("slo", "claims", "validation"),
        panels=(
            DashboardPanelSpec(
                id=1,
                title="Claims Created (Rate)",
                panel_type="timeseries",
                expr='sum(rate(claims_created_total{tenant_id="$tenant_id"}[5m]))',
                description="Claims created per second",
            ),
            DashboardPanelSpec(
                id=2,
                title="Validator Rejection Rate",
                panel_type="timeseries",
                expr=(
                    'sum(rate(validator_rejects_total{tenant_id="$tenant_id"}[5m]))'
                    ' / sum(rate(validator_checks_total{tenant_id="$tenant_id"}[5m])) * 100'
                ),
                description="Percentage of claims rejected by validators",
            ),
            DashboardPanelSpec(
                id=3,
                title="No-Free-Facts Violations",
                panel_type="stat",
                expr='sum(increase(no_free_facts_violations_total{tenant_id="$tenant_id"}[24h]))',
                description="No-Free-Facts violations in last 24h (target: 0)",
            ),
            DashboardPanelSpec(
                id=4,
                title="Rejections by Validator Type",
                panel_type="timeseries",
                expr=(
                    'sum by (validator) (rate(validator_rejects_total{tenant_id="$tenant_id"}[5m]))'
                ),
                description="Rejection rate broken down by validator",
            ),
        ),
        required_variables=("tenant_id",),
    )


def _create_sanad_grading_dashboard() -> DashboardSpec:
    """Dashboard 5: Sanad Grading Distribution Drift."""
    return DashboardSpec(
        uid="idis-sanad-grading",
        title="Sanad Grading Distribution Drift",
        description="Monitors Sanad grade distribution and potential drift indicators.",
        tags=("slo", "sanad", "grading"),
        panels=(
            DashboardPanelSpec(
                id=1,
                title="Grade Distribution",
                panel_type="timeseries",
                expr='sum by (grade) (sanad_grades_total{tenant_id="$tenant_id"})',
                description="Current distribution of Sanad grades (A/B/C/D)",
            ),
            DashboardPanelSpec(
                id=2,
                title="Grade A Percentage",
                panel_type="stat",
                expr=(
                    'sum(sanad_grades_total{grade="A",tenant_id="$tenant_id"})'
                    ' / sum(sanad_grades_total{tenant_id="$tenant_id"}) * 100'
                ),
                description="Percentage of claims with grade A",
            ),
            DashboardPanelSpec(
                id=3,
                title="Corroboration Status Distribution",
                panel_type="timeseries",
                expr='sum by (status) (sanad_corroboration_total{tenant_id="$tenant_id"})',
                description="Distribution of corroboration statuses",
            ),
            DashboardPanelSpec(
                id=4,
                title="Sanad Retrieval Latency p95",
                panel_type="timeseries",
                expr=(
                    "histogram_quantile(0.95, sum(rate("
                    'sanad_retrieval_duration_seconds_bucket{tenant_id="$tenant_id"}[5m])) by (le))'
                ),
                description="95th percentile Sanad retrieval latency (target: <1.2s)",
            ),
        ),
        required_variables=("tenant_id",),
    )


def _create_calc_dashboard() -> DashboardSpec:
    """Dashboard 6: Calc Success Rate and Reproducibility."""
    return DashboardSpec(
        uid="idis-calc-success",
        title="Calc Success Rate and Reproducibility",
        description="Monitors deterministic calculation engine health and reproducibility.",
        tags=("slo", "calc", "reproducibility"),
        panels=(
            DashboardPanelSpec(
                id=1,
                title="Calc Run Success Rate",
                panel_type="stat",
                expr=(
                    'sum(rate(calc_success_total{tenant_id="$tenant_id"}[1h]))'
                    ' / sum(rate(calc_attempts_total{tenant_id="$tenant_id"}[1h])) * 100'
                ),
                description="Calc run success rate (target: >=99.5%)",
            ),
            DashboardPanelSpec(
                id=2,
                title="Reproducibility Check Failures (24h)",
                panel_type="stat",
                expr=(
                    "sum(increase(calc_reproducibility_failures_total{"
                    'tenant_id="$tenant_id"}[24h]))'
                    " / sum(increase(calc_reproducibility_checks_total{"
                    'tenant_id="$tenant_id"}[24h]))'
                    " * 100"
                ),
                description="Reproducibility failure rate (target: <=0.1%)",
            ),
            DashboardPanelSpec(
                id=3,
                title="Calc Runs by Formula",
                panel_type="timeseries",
                expr='sum by (formula_name) (rate(calc_success_total{tenant_id="$tenant_id"}[5m]))',
                description="Successful calc runs by formula type",
            ),
            DashboardPanelSpec(
                id=4,
                title="Extraction Gate Blocks",
                panel_type="timeseries",
                expr='sum(rate(calc_extraction_gate_blocks_total{tenant_id="$tenant_id"}[5m]))',
                description="Calcs blocked due to low extraction confidence",
            ),
        ),
        required_variables=("tenant_id",),
    )


def _create_debate_dashboard() -> DashboardSpec:
    """Dashboard 7: Debate Completion Rate and Max-Round Stops."""
    return DashboardSpec(
        uid="idis-debate-completion",
        title="Debate Completion Rate and Max-Round Stops",
        description="Monitors debate orchestration health and completion patterns.",
        tags=("slo", "debate", "orchestration"),
        panels=(
            DashboardPanelSpec(
                id=1,
                title="Debate Completion Rate",
                panel_type="stat",
                expr=(
                    'sum(rate(debate_completed_total{tenant_id="$tenant_id"}[1h]))'
                    ' / sum(rate(debate_started_total{tenant_id="$tenant_id"}[1h])) * 100'
                ),
                description="Debate completion rate (target: >=98%)",
            ),
            DashboardPanelSpec(
                id=2,
                title="Stop Reason Distribution",
                panel_type="timeseries",
                expr=(
                    "sum by (stop_reason) "
                    '(rate(debate_completed_total{tenant_id="$tenant_id"}[5m]))'
                ),
                description="Debates by stop reason (CONSENSUS, STABLE_DISSENT, MAX_ROUNDS, etc.)",
            ),
            DashboardPanelSpec(
                id=3,
                title="Max-Rounds Stop Percentage",
                panel_type="stat",
                expr=(
                    'sum(rate(debate_completed_total{stop_reason="MAX_ROUNDS",'
                    'tenant_id="$tenant_id"}[1h]))'
                    ' / sum(rate(debate_completed_total{tenant_id="$tenant_id"}[1h])) * 100'
                ),
                description="Percentage of debates stopped due to max rounds",
            ),
            DashboardPanelSpec(
                id=4,
                title="Muhasabah Gate Rejects",
                panel_type="timeseries",
                expr='sum(rate(muhasabah_gate_rejects_total{tenant_id="$tenant_id"}[5m]))',
                description="Outputs rejected by Muhasabah validator",
            ),
        ),
        required_variables=("tenant_id",),
    )


def _create_deliverable_dashboard() -> DashboardSpec:
    """Dashboard 8: Deliverable Generation Success Rate."""
    return DashboardSpec(
        uid="idis-deliverable-success",
        title="Deliverable Generation Success Rate",
        description="Monitors deliverable (PDF/DOCX) generation pipeline health.",
        tags=("slo", "deliverables", "generation"),
        panels=(
            DashboardPanelSpec(
                id=1,
                title="Deliverable Success Rate",
                panel_type="stat",
                expr=(
                    'sum(rate(deliverable_success_total{tenant_id="$tenant_id"}[1h]))'
                    ' / sum(rate(deliverable_attempts_total{tenant_id="$tenant_id"}[1h])) * 100'
                ),
                description="Deliverable generation success rate (target: >=99%)",
            ),
            DashboardPanelSpec(
                id=2,
                title="Deliverables Generated (Rate)",
                panel_type="timeseries",
                expr='sum(rate(deliverable_success_total{tenant_id="$tenant_id"}[5m]))',
                description="Deliverables generated per second",
            ),
            DashboardPanelSpec(
                id=3,
                title="Deliverables by Type",
                panel_type="timeseries",
                expr=(
                    "sum by (deliverable_type) "
                    '(rate(deliverable_success_total{tenant_id="$tenant_id"}[5m]))'
                ),
                description="Deliverables generated by type (IC_MEMO, SNAPSHOT, etc.)",
            ),
            DashboardPanelSpec(
                id=4,
                title="No-Free-Facts Validation Failures",
                panel_type="timeseries",
                expr='sum(rate(deliverable_no_free_facts_failures_total{tenant_id="$tenant_id"}[5m]))',
                description="Deliverables blocked by No-Free-Facts validator",
            ),
        ),
        required_variables=("tenant_id",),
    )


def _create_audit_dashboard() -> DashboardSpec:
    """Dashboard 9: Audit Event Ingestion Lag and Coverage."""
    return DashboardSpec(
        uid="idis-audit-coverage",
        title="Audit Event Ingestion Lag and Coverage",
        description="Monitors audit event pipeline health and coverage compliance.",
        tags=("slo", "audit", "compliance"),
        panels=(
            DashboardPanelSpec(
                id=1,
                title="Audit Event Ingestion Lag",
                panel_type="stat",
                expr='audit_ingestion_lag_seconds{tenant_id="$tenant_id"}',
                description="Current audit event ingestion lag (target: <5min)",
            ),
            DashboardPanelSpec(
                id=2,
                title="Audit Coverage",
                panel_type="stat",
                expr=(
                    'sum(rate(audit_events_emitted_total{tenant_id="$tenant_id"}[1h]))'
                    ' / sum(rate(mutating_operations_total{tenant_id="$tenant_id"}[1h])) * 100'
                ),
                description="Audit event coverage (target: 100%)",
            ),
            DashboardPanelSpec(
                id=3,
                title="Missing Audit Events (24h)",
                panel_type="stat",
                expr='sum(increase(audit_events_missing_total{tenant_id="$tenant_id"}[24h]))',
                description="Missing audit events in last 24h (target: 0)",
            ),
            DashboardPanelSpec(
                id=4,
                title="Audit Events by Type",
                panel_type="timeseries",
                expr=(
                    "sum by (event_type) "
                    '(rate(audit_events_emitted_total{tenant_id="$tenant_id"}[5m]))'
                ),
                description="Audit events emitted by event type",
            ),
        ),
        required_variables=("tenant_id",),
    )


def _create_integration_health_dashboard() -> DashboardSpec:
    """Dashboard 10: Integration Health (CRM/Docs/Providers)."""
    return DashboardSpec(
        uid="idis-integration-health",
        title="Integration Health",
        description="Monitors external integration (CRM, docs, enrichment) health.",
        tags=("slo", "integrations", "external"),
        panels=(
            DashboardPanelSpec(
                id=1,
                title="Integration Success Rate (All)",
                panel_type="stat",
                expr=(
                    'sum(rate(integration_success_total{tenant_id="$tenant_id"}[1h]))'
                    ' / sum(rate(integration_attempts_total{tenant_id="$tenant_id"}[1h])) * 100'
                ),
                description="Overall integration success rate",
            ),
            DashboardPanelSpec(
                id=2,
                title="Integration Errors by Provider",
                panel_type="timeseries",
                expr=(
                    'sum by (provider) (rate(integration_errors_total{tenant_id="$tenant_id"}[5m]))'
                ),
                description="Integration errors by provider (CRM, docs, enrichment)",
            ),
            DashboardPanelSpec(
                id=3,
                title="Webhook Delivery Success Rate",
                panel_type="stat",
                expr=(
                    'sum(rate(webhook_delivery_success_total{tenant_id="$tenant_id"}[1h]))'
                    " / sum(rate(webhook_delivery_attempts_total{"
                    'tenant_id="$tenant_id"}[1h])) * 100'
                ),
                description="Webhook delivery success rate",
            ),
            DashboardPanelSpec(
                id=4,
                title="Integration Latency by Provider",
                panel_type="timeseries",
                expr=(
                    "histogram_quantile(0.95, sum by (provider, le) (rate("
                    'integration_duration_seconds_bucket{tenant_id="$tenant_id"}[5m])))'
                ),
                description="95th percentile latency by integration provider",
            ),
        ),
        required_variables=("tenant_id",),
    )


def get_golden_dashboards() -> tuple[DashboardSpec, ...]:
    """Return the 10 required golden dashboards per SLO/Runbooks §8.1.

    Returns:
        Tuple of 10 DashboardSpec objects with deterministic ordering.
    """
    return (
        _create_api_availability_dashboard(),
        _create_ingestion_dashboard(),
        _create_queue_depth_dashboard(),
        _create_claim_registry_dashboard(),
        _create_sanad_grading_dashboard(),
        _create_calc_dashboard(),
        _create_debate_dashboard(),
        _create_deliverable_dashboard(),
        _create_audit_dashboard(),
        _create_integration_health_dashboard(),
    )


def validate_golden_dashboards() -> None:
    """Validate all golden dashboards meet requirements.

    Raises:
        MonitoringValidationError: If validation fails (fail-closed).
    """
    dashboards = get_golden_dashboards()

    # Verify we have exactly 10 dashboards
    if len(dashboards) != 10:
        raise MonitoringValidationError(f"Expected 10 golden dashboards, got {len(dashboards)}")

    # Verify all required titles are present
    actual_titles = {d.title for d in dashboards}
    expected_titles = set(GOLDEN_DASHBOARD_TITLES)
    missing = expected_titles - actual_titles
    if missing:
        raise MonitoringValidationError(f"Missing required dashboard titles: {missing}")

    # Run standard collection validation
    validate_dashboard_specs(dashboards)


def _dashboard_to_grafana_json(dashboard: DashboardSpec) -> dict[str, Any]:
    """Convert a DashboardSpec to Grafana JSON format.

    Uses stable key ordering for deterministic output.
    """
    panels_json: list[dict[str, Any]] = []
    for panel in dashboard.panels:
        panel_json: dict[str, Any] = {
            "datasource": {"type": "prometheus", "uid": "prometheus"},
            "description": panel.description,
            "fieldConfig": {"defaults": {}, "overrides": []},
            "gridPos": {
                "h": 8,
                "w": 12,
                "x": ((panel.id - 1) % 2) * 12,
                "y": ((panel.id - 1) // 2) * 8,
            },
            "id": panel.id,
            "options": {},
            "targets": [
                {
                    "expr": panel.expr,
                    "legendFormat": "{{label_name}}",
                    "refId": "A",
                }
            ],
            "title": panel.title,
            "type": panel.panel_type,
        }
        panels_json.append(panel_json)

    # Build templating variables
    templating_list: list[dict[str, Any]] = []
    for var in dashboard.required_variables:
        var_def: dict[str, Any] = {
            "current": {"selected": False, "text": "", "value": ""},
            "hide": 0,
            "label": var.replace("_", " ").title(),
            "name": var,
            "options": [],
            "query": "",
            "refresh": 1,
            "type": "textbox",
        }
        templating_list.append(var_def)

    grafana_json: dict[str, Any] = {
        "annotations": {"list": []},
        "description": dashboard.description,
        "editable": True,
        "fiscalYearStartMonth": 0,
        "graphTooltip": 0,
        "id": None,
        "links": [],
        "panels": panels_json,
        "schemaVersion": 39,
        "tags": list(dashboard.tags),
        "templating": {"list": templating_list},
        "time": {"from": "now-6h", "to": "now"},
        "timepicker": {},
        "timezone": "utc",
        "title": dashboard.title,
        "uid": dashboard.uid,
        "version": 1,
    }

    return grafana_json


def export_grafana_json_bundle(out_dir: Path) -> list[Path]:
    """Export all golden dashboards as Grafana JSON files.

    Creates one JSON file per dashboard with canonical serialization
    (stable key ordering, no timestamps).

    Args:
        out_dir: Directory to write JSON files to. Created if not exists.

    Returns:
        List of paths to created JSON files.

    Raises:
        MonitoringValidationError: If validation fails before export.
    """
    # Validate before export (fail-closed)
    validate_golden_dashboards()

    out_dir.mkdir(parents=True, exist_ok=True)

    created_files: list[Path] = []
    dashboards = get_golden_dashboards()

    for dashboard in dashboards:
        grafana_json = _dashboard_to_grafana_json(dashboard)
        file_path = out_dir / f"{dashboard.uid}.json"

        # Deterministic JSON: sort_keys=True, consistent indent
        with file_path.open("w", encoding="utf-8") as f:
            json.dump(grafana_json, f, indent=2, sort_keys=True, ensure_ascii=False)
            f.write("\n")

        created_files.append(file_path)

    return created_files
