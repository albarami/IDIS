"""HTTP request metrics middleware (Slice99 Task 6).

Records only genuinely measured, tenant-safe counters into the in-process registry
(``idis.observability.metrics``):

- ``http_requests_total{method, status_class}``
- ``http_request_5xx_total{method}``
- ``http_request_duration_ms_total{method}`` (sum of wall-clock ms; pair with
  ``http_requests_total`` for averages)

Labels are restricted to the HTTP method and a coarse status class - NEVER the request path
(paths embed tenant/deal/run identifiers), headers, query strings, or payloads. An unhandled
exception counts as a 5xx before re-raising.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from idis.observability.metrics import (
    HTTP_REQUEST_5XX_TOTAL,
    HTTP_REQUEST_DURATION_MS_TOTAL,
    HTTP_REQUESTS_TOTAL,
    increment_counter,
)


def _status_class(status_code: int) -> str:
    return f"{status_code // 100}xx"


class HttpMetricsMiddleware(BaseHTTPMiddleware):
    """Count every request by method + status class and accumulate latency."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        method = request.method.upper()
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            self._record(method, 500, started)
            raise
        self._record(method, response.status_code, started)
        return response

    @staticmethod
    def _record(method: str, status_code: int, started: float) -> None:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        increment_counter(
            HTTP_REQUESTS_TOTAL,
            labels={"method": method, "status_class": _status_class(status_code)},
        )
        increment_counter(
            HTTP_REQUEST_DURATION_MS_TOTAL, labels={"method": method}, value=elapsed_ms
        )
        if status_code >= 500:
            increment_counter(HTTP_REQUEST_5XX_TOTAL, labels={"method": method})
