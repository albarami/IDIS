"""Minimal in-process Prometheus-style counters (Slice97 Task 6).

A tiny, thread-safe, label-aware counter registry whose names/labels match what the SLO dashboard
already queries (``monitoring/slo_dashboard.py``): ``webhook_delivery_success_total`` /
``webhook_delivery_attempts_total`` rated over the ``tenant_id`` label. ``render_prometheus_text``
emits the standard exposition format so a scrape endpoint can expose these verbatim when one is
wired. Deliberately dependency-free (no ``prometheus_client``); counters are per-process.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping

WEBHOOK_DELIVERY_SUCCESS_TOTAL = "webhook_delivery_success_total"
WEBHOOK_DELIVERY_ATTEMPTS_TOTAL = "webhook_delivery_attempts_total"

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


def render_prometheus_text() -> str:
    """Render all counters in the Prometheus exposition format."""
    with _LOCK:
        items = sorted(_COUNTERS.items())
    lines: list[str] = []
    for (name, labels), value in items:
        if labels:
            label_text = ",".join(f'{key}="{val}"' for key, val in labels)
            lines.append(f"{name}{{{label_text}}} {value}")
        else:
            lines.append(f"{name} {value}")
    return "\n".join(lines) + ("\n" if lines else "")
