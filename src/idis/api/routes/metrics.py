"""Prometheus scrape endpoint (Slice99 Task 6).

Serves the in-process counter registry verbatim in the Prometheus exposition format. This is
an operational surface like ``/health`` (non-/v1, no tenant data): it exposes only the
genuinely measured counters registered in ``idis.observability.metrics`` - counts, latencies,
and safe identifier labels; never request paths, secrets, tenant content, object keys, or
provider payloads. The k8s deployment's ``prometheus.io/path`` annotation points here.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from idis.observability.metrics import render_prometheus_text

router = APIRouter(tags=["Observability"])

PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


@router.get("/metrics", include_in_schema=False)
def get_metrics() -> PlainTextResponse:
    """Prometheus exposition of the in-process counters."""
    return PlainTextResponse(render_prometheus_text(), media_type=PROMETHEUS_CONTENT_TYPE)
