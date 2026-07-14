# Slice99 Task 6 - SLO Metrics: LIVE vs NOT YET EMITTED

The SLO alert rules (`src/idis/monitoring/alerts.py`) and the 10 golden dashboards
(`src/idis/monitoring/slo_dashboard.py`) - exported deterministically under
`deploy/monitoring/` - query more metrics than IDIS currently measures. This doc is the
honest boundary: a metric may be listed LIVE only if the code genuinely records and serves it
at `GET /metrics` (registered in `idis.observability.metrics.LIVE_METRIC_NAMES`; the pairing
is test-enforced by `tests/test_slice99_metrics_endpoint.py`). Everything else the alerts or
dashboards reference is NOT YET EMITTED: the definitions are forward-looking specifications,
not evidence.

`GET /metrics` is an operational scrape surface like `/health` (non-/v1, excluded from the
public OpenAPI contract) and is UNAUTHENTICATED: anything exposed there is readable by any
client that can reach the API port. Counters therefore carry only non-identifying labels
(`method`, `status_class`) - never request paths, tenant identifiers, tenant content, secrets,
object keys, or provider payloads. The webhook delivery counters are GLOBAL aggregates (no
tenant label; reviewer remediation) so no tenant UUID or per-tenant volume is scrapeable;
per-tenant delivery evidence lives in the tenant-scoped audit events. Label values are escaped
per the Prometheus exposition spec. Deployments should still network-restrict `/metrics` to the
monitoring plane as defense in depth.

## LIVE

Measured in-process and served at `/metrics` today:

| Metric | Labels | Recorded by |
| --- | --- | --- |
| `http_requests_total` | method, status_class | `idis.api.middleware.http_metrics` (every request) |
| `http_request_5xx_total` | method | `idis.api.middleware.http_metrics` (5xx + unhandled exceptions) |
| `http_request_duration_ms_total` | method | `idis.api.middleware.http_metrics` (wall-clock ms sum) |
| `webhook_delivery_attempts_total` | (none - global aggregate) | webhook dispatcher (Slice97; de-tenanted in the Slice99 reviewer remediation) |
| `webhook_delivery_success_total` | (none - global aggregate) | webhook dispatcher (Slice97; de-tenanted in the Slice99 reviewer remediation) |

## NOT YET EMITTED

Referenced by alert rules and/or golden dashboards but not yet measured anywhere in the
codebase. Alerts/panels over these names will show no data until a future slice wires real
counters; they must never be presented as live evidence:

- `audit_events_emitted_total`
- `audit_events_missing_total`
- `audit_ingestion_lag_seconds`
- `calc_attempts_total`
- `calc_extraction_gate_blocks_total`
- `calc_reproducibility_checks_total`
- `calc_reproducibility_failures_total`
- `calc_success_total`
- `claims_created_total`
- `debate_completed_total`
- `debate_started_total`
- `deliverable_attempts_total`
- `deliverable_failures_total`
- `deliverable_no_free_facts_failures_total`
- `deliverable_success_total`
- `extraction_gate_attempts_total`
- `extraction_gate_pass_total`
- `ingestion_attempts_total`
- `ingestion_errors_total`
- `ingestion_success_total`
- `integration_attempts_total`
- `integration_errors_total`
- `integration_success_total`
- `muhasabah_gate_rejects_total`
- `mutating_operations_total`
- `no_free_facts_violations_total`
- `sanad_corroboration_total`
- `sanad_grades_total`
- `tenant_isolation_violations_total`
- `validator_checks_total`
- `validator_rejects_total`
