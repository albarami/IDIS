"""Minimal in-process Prometheus-style counters (Slice97 Task 6; hardened Slice99).

A tiny, thread-safe, label-aware counter registry served at the unauthenticated ``GET /metrics``
scrape surface. Because that surface is unauthenticated, counters must carry only safe labels:
``webhook_delivery_success_total`` / ``webhook_delivery_attempts_total`` are GLOBAL aggregates
(no tenant label - no tenant UUID or per-tenant volume is scrapeable), and the HTTP counters are
labeled by method + status class only. ``render_prometheus_text`` emits the standard exposition
format with label values escaped per the Prometheus text spec. Deliberately dependency-free
(no ``prometheus_client``); counters are per-process.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping

WEBHOOK_DELIVERY_SUCCESS_TOTAL = "webhook_delivery_success_total"
WEBHOOK_DELIVERY_ATTEMPTS_TOTAL = "webhook_delivery_attempts_total"

# HTTP surface counters recorded by idis.api.middleware.http_metrics (Slice99 Task 6).
HTTP_REQUESTS_TOTAL = "http_requests_total"
HTTP_REQUEST_5XX_TOTAL = "http_request_5xx_total"
HTTP_REQUEST_DURATION_MS_TOTAL = "http_request_duration_ms_total"

# The metrics IDIS genuinely measures and serves at /metrics. The Slice99 mapping doc
# (docs/architecture/slice99_metrics_mapping.md) must mirror this exactly: SLO/dashboard
# metrics not listed here are NOT emitted yet and must never be presented as live.
LIVE_METRIC_NAMES: tuple[str, ...] = (
    HTTP_REQUEST_5XX_TOTAL,
    HTTP_REQUEST_DURATION_MS_TOTAL,
    HTTP_REQUESTS_TOTAL,
    WEBHOOK_DELIVERY_ATTEMPTS_TOTAL,
    WEBHOOK_DELIVERY_SUCCESS_TOTAL,
)

_LabelsKey = tuple[tuple[str, str], ...]

_COUNTERS: dict[tuple[str, _LabelsKey], int] = {}
_LOCK = threading.Lock()


def _labels_key(labels: Mapping[str, str] | None) -> _LabelsKey:
    return tuple(sorted((str(k), str(v)) for k, v in (labels or {}).items()))


def increment_counter(
    name: str, *, labels: Mapping[str, str] | None = None, value: int = 1
) -> None:
    """Increment a named counter (thread-safe)."""
    key = (name, _labels_key(labels))
    with _LOCK:
        _COUNTERS[key] = _COUNTERS.get(key, 0) + value


def get_counter(name: str, *, labels: Mapping[str, str] | None = None) -> int:
    """Current value of a counter (0 if never incremented)."""
    with _LOCK:
        return _COUNTERS.get((name, _labels_key(labels)), 0)


def reset_metrics() -> None:
    """Clear all counters (tests only)."""
    with _LOCK:
        _COUNTERS.clear()


def _escape_label_value(value: str) -> str:
    """Escape a label value per the Prometheus exposition spec (backslash, quote, newline)."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def render_prometheus_text() -> str:
    """Render all counters in the Prometheus exposition format."""
    with _LOCK:
        items = sorted(_COUNTERS.items())
    lines: list[str] = []
    for (name, labels), value in items:
        if labels:
            label_text = ",".join(f'{key}="{_escape_label_value(val)}"' for key, val in labels)
            lines.append(f"{name}{{{label_text}}} {value}")
        else:
            lines.append(f"{name} {value}")
    return "\n".join(lines) + ("\n" if lines else "")
