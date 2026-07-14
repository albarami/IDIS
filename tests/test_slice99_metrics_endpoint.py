"""Slice99 Task 6 - honest /metrics wiring + monitoring exports (RED-first, Q5 boundaries).

Pins the observability-wiring contract:

1. ``GET /metrics`` serves Prometheus exposition text from the real in-process counters.
2. HTTP request/latency/5xx counters increment from REAL requests through the real app
   middleware stack - no fabricated SLO metrics.
3. The exposition output is safe: label keys are allowlisted, no raw request paths, tenant
   content, secrets, object keys, or provider payloads appear.
4. Deploy truth: the k8s scrape annotation (``prometheus.io/path``) must be a route the app
   actually serves.
5. The committed ``deploy/monitoring/`` exports byte-match the in-code alert/dashboard
   definitions (deterministic regeneration).
6. The LIVE vs NOT-YET-EMITTED mapping doc exists, its LIVE claims exactly mirror the code's
   registered live metric names, and every metric referenced by alerts/dashboards that is not
   genuinely emitted is declared NOT-YET-EMITTED (unmeasured metrics can never be marked live).

PYTHONPATH pinned to this worktree's src.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from idis.api.main import create_app
from idis.monitoring.alerts import export_prometheus_rules, get_core_alerts
from idis.monitoring.slo_dashboard import export_grafana_json_bundle, get_golden_dashboards
from idis.observability.metrics import (
    LIVE_METRIC_NAMES,
    get_counter,
    reset_metrics,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_MAPPING_DOC = _REPO_ROOT / "docs" / "architecture" / "slice99_metrics_mapping.md"
_MONITORING_DIR = _REPO_ROOT / "deploy" / "monitoring"

_METRIC_LINE_PATTERN = re.compile(
    r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(?P<labels>[^}]*)\})?\s+(?P<value>-?\d+)$"
)
_EXPR_METRIC_PATTERN = re.compile(
    r"\b([a-z][a-z0-9_]*_(?:total|seconds|count|sum|ms|ratio|bytes|lag|failures))\b"
)

# Reviewer remediation: tenant_id is NOT an allowed scrape label - webhook counters are
# global aggregates so the unauthenticated /metrics surface exposes no tenant identifiers.
_ALLOWED_LABEL_KEYS = {"method", "status_class"}


def _client() -> TestClient:
    reset_metrics()
    return TestClient(create_app(service_region="us-east-1"), raise_server_exceptions=False)


def _parse_exposition(body: str) -> list[tuple[str, dict[str, str], int]]:
    parsed: list[tuple[str, dict[str, str], int]] = []
    for line in body.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        match = _METRIC_LINE_PATTERN.match(line)
        assert match, f"non-exposition line served from /metrics: {line!r}"
        labels: dict[str, str] = {}
        raw = match.group("labels")
        if raw:
            for pair in raw.split(","):
                key, _, value = pair.partition("=")
                labels[key.strip()] = value.strip().strip('"')
        parsed.append((match.group("name"), labels, int(match.group("value"))))
    return parsed


# ---------------------------------------------------------------------------
# 1. /metrics serves Prometheus text
# ---------------------------------------------------------------------------


def test_metrics_route_serves_prometheus_text() -> None:
    client = _client()

    response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    _parse_exposition(response.text)


# ---------------------------------------------------------------------------
# 2. HTTP counters increment from real requests
# ---------------------------------------------------------------------------


def test_http_counters_increment_from_real_requests() -> None:
    client = _client()

    assert client.get("/health").status_code == 200
    assert client.get("/health").status_code == 200
    unauthorized = client.get("/v1/deals")
    assert unauthorized.status_code in (401, 403)

    body = client.get("/metrics").text
    parsed = _parse_exposition(body)
    by_name: dict[str, int] = {}
    for name, labels, value in parsed:
        if name == "http_requests_total" and labels.get("method") == "GET":
            by_name[labels.get("status_class", "?")] = (
                by_name.get(labels.get("status_class", "?"), 0) + value
            )

    assert by_name.get("2xx", 0) >= 2, f"real 2xx requests must be counted, got {by_name}"
    assert by_name.get("4xx", 0) >= 1, f"real 4xx requests must be counted, got {by_name}"
    assert any(
        name == "http_request_duration_ms_total" and value >= 0 for name, _, value in parsed
    ), "request latency must be measured"


def test_5xx_counter_increments_through_the_real_middleware() -> None:
    from idis.api.middleware.http_metrics import HttpMetricsMiddleware

    reset_metrics()
    app = FastAPI()
    app.add_middleware(HttpMetricsMiddleware)

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("synthetic failure")

    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/boom").status_code == 500

    assert get_counter("http_request_5xx_total", labels={"method": "GET"}) == 1
    assert get_counter("http_requests_total", labels={"method": "GET", "status_class": "5xx"}) == 1


# ---------------------------------------------------------------------------
# 3. exposition output is secret/tenant/path safe
# ---------------------------------------------------------------------------


def test_metrics_output_is_safe() -> None:
    client = _client()
    tenant_ish = "11111111-1111-1111-1111-111111111111"
    client.get(f"/v1/deals/{tenant_ish}", headers={"X-IDIS-API-Key": "super-secret-key-value"})

    body = client.get("/metrics").text

    assert tenant_ish not in body, "request path ids must never reach /metrics"
    assert "/v1/deals" not in body, "raw request paths must never be metric labels"
    assert "super-secret-key-value" not in body
    for forbidden in ("password", "api_key", "apikey", "secret", "object_key"):
        assert forbidden not in body.lower()

    for _, labels, _ in _parse_exposition(body):
        assert set(labels) <= _ALLOWED_LABEL_KEYS, f"unexpected label keys: {labels}"


# ---------------------------------------------------------------------------
# 4. deploy truth: the k8s scrape path is actually served
# ---------------------------------------------------------------------------


def test_k8s_scrape_annotation_path_is_served() -> None:
    deployment = (_REPO_ROOT / "deploy" / "k8s" / "deployment.yaml").read_text(encoding="utf-8")
    annotations: dict[str, str] = {}
    for document in yaml.safe_load_all(deployment):
        if not isinstance(document, dict):
            continue
        template = document.get("spec", {}).get("template", {})
        annotations = template.get("metadata", {}).get("annotations", {}) or {}
        if annotations:
            break

    assert annotations.get("prometheus.io/scrape") == "true"
    scrape_path = annotations.get("prometheus.io/path", "")
    assert scrape_path, "deployment must declare a scrape path"

    client = _client()
    response = client.get(scrape_path)
    assert response.status_code == 200, (
        f"deploy annotates scrape path '{scrape_path}' but the app does not serve it"
    )


# ---------------------------------------------------------------------------
# 5. committed deploy/monitoring exports match the in-code definitions
# ---------------------------------------------------------------------------


def test_committed_monitoring_exports_match_definitions(tmp_path: Path) -> None:
    assert _MONITORING_DIR.is_dir(), "deploy/monitoring must contain the committed exports"

    regenerated_rules = export_prometheus_rules(tmp_path / "prometheus_alert_rules.yaml")
    committed_rules = _MONITORING_DIR / "prometheus_alert_rules.yaml"
    assert committed_rules.is_file()
    assert committed_rules.read_text(encoding="utf-8") == regenerated_rules.read_text(
        encoding="utf-8"
    ), "committed alert rules drifted from the in-code definitions"

    regenerated = {
        path.name: path.read_text(encoding="utf-8")
        for path in export_grafana_json_bundle(tmp_path / "dashboards")
    }
    committed_dir = _MONITORING_DIR / "dashboards"
    committed = {
        path.name: path.read_text(encoding="utf-8") for path in committed_dir.glob("*.json")
    }
    assert committed == regenerated, "committed dashboards drifted from the in-code definitions"
    assert len(regenerated) == 10


# ---------------------------------------------------------------------------
# 6. LIVE vs NOT-YET-EMITTED mapping doc (no unmeasured metric may claim live)
# ---------------------------------------------------------------------------


def _doc_section_tokens(text: str, heading: str) -> set[str]:
    section = text.split(heading, 1)
    assert len(section) == 2, f"mapping doc must contain the '{heading}' section"
    body = section[1].split("\n## ", 1)[0]
    return set(re.findall(r"`([a-z][a-z0-9_]*)`", body))


def _referenced_metric_names() -> set[str]:
    names: set[str] = set()
    for alert in get_core_alerts():
        names.update(_EXPR_METRIC_PATTERN.findall(alert.expr))
    for dashboard in get_golden_dashboards():
        for panel in dashboard.panels:
            names.update(_EXPR_METRIC_PATTERN.findall(panel.expr))
    return names


def test_mapping_doc_exists_and_is_honest() -> None:
    assert _MAPPING_DOC.is_file(), "LIVE vs NOT-YET-EMITTED mapping doc must exist"
    text = _MAPPING_DOC.read_text(encoding="utf-8")

    live_tokens = _doc_section_tokens(text, "## LIVE")
    pending_tokens = _doc_section_tokens(text, "## NOT YET EMITTED")

    assert live_tokens == set(LIVE_METRIC_NAMES), (
        "the doc's LIVE section must exactly mirror the code's registered live metrics; "
        f"doc-only={sorted(live_tokens - set(LIVE_METRIC_NAMES))}, "
        f"code-only={sorted(set(LIVE_METRIC_NAMES) - live_tokens)}"
    )
    assert not (live_tokens & pending_tokens), "a metric cannot be both LIVE and NOT YET EMITTED"

    referenced = _referenced_metric_names()
    assert referenced, "alerts/dashboards must reference at least one metric"
    unmeasured = referenced - set(LIVE_METRIC_NAMES)
    missing_declarations = unmeasured - pending_tokens
    assert not missing_declarations, (
        "every referenced-but-unmeasured metric must be declared NOT YET EMITTED: "
        f"{sorted(missing_declarations)}"
    )
    falsely_live = referenced & live_tokens - set(LIVE_METRIC_NAMES)
    assert not falsely_live, f"unmeasured metrics marked live: {sorted(falsely_live)}"
