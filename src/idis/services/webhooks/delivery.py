"""Webhook delivery service for IDIS.

Handles outbound webhook delivery with OpenTelemetry instrumentation per v6.3:
- API Contracts §6.1: Webhook delivery with HMAC signing
- Retry primitives from retry.py
- OpenTelemetry spans for each delivery attempt

Security (per docs/IDIS_Security_Threat_Model_v6_3.md):
- Never log or export secrets in span attributes
- Sanitize target URLs (no querystring/auth in spans)
- Each retry attempt creates a child span
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit, urlunsplit

import httpx

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_USER_AGENT = "IDIS-Webhook/6.3"


@dataclass(frozen=True)
class DeliveryResult:
    """Result of a webhook delivery attempt.

    Attributes:
        success: Whether delivery succeeded (2xx response).
        status_code: HTTP status code from target, or None if connection failed.
        error: Error message if delivery failed.
        attempt_id: UUID of this delivery attempt.
        duration_ms: Request duration in milliseconds.
    """

    success: bool
    status_code: int | None
    error: str | None
    attempt_id: str
    duration_ms: int


def _sanitize_url_for_span(url: str) -> str:
    """Sanitize URL for span attributes.

    Security: Removes all sensitive components:
    - NO userinfo (no "user:pass@")
    - NO querystring
    - NO fragment
    Preserves: scheme, host, optional port, and path only.

    Args:
        url: Raw URL potentially containing credentials/query/fragment.

    Returns:
        Sanitized URL safe for span attributes, or "unknown" if malformed.
    """
    try:
        parts = urlsplit(url)
        # Extract hostname and port separately to strip userinfo
        host = parts.hostname or ""
        if not host:
            return "unknown"
        port = f":{parts.port}" if parts.port else ""
        # Reconstruct netloc WITHOUT userinfo
        safe_netloc = f"{host}{port}"
        # Reconstruct URL: scheme, netloc (no userinfo), path, NO query, NO fragment
        safe_url = urlunsplit((parts.scheme, safe_netloc, parts.path, "", ""))
        return safe_url if safe_url else "unknown"
    except Exception:
        return "unknown"


def _get_host_from_url(url: str) -> str:
    """Extract host from URL for span attributes (no userinfo).

    Args:
        url: Raw URL.

    Returns:
        Hostname (and port if present), never includes userinfo.
    """
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if not host:
            return "unknown"
        port = f":{parts.port}" if parts.port else ""
        return f"{host}{port}"
    except Exception:
        return "unknown"


async def deliver_webhook(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    webhook_id: str,
    attempt_id: str,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> DeliveryResult:
    """Deliver a webhook payload to target URL.

    Creates an OpenTelemetry span for the delivery attempt with safe attributes.

    Args:
        url: Target webhook URL.
        payload: JSON payload to deliver.
        headers: HTTP headers including signature headers.
        webhook_id: UUID of the webhook subscription.
        attempt_id: UUID of this delivery attempt.
        timeout_seconds: Request timeout in seconds.

    Returns:
        DeliveryResult with success status and details.
    """
    import json
    import time

    from opentelemetry import trace

    tracer = trace.get_tracer("idis.webhooks")
    sanitized_url = _sanitize_url_for_span(url)
    target_host = _get_host_from_url(url)

    with tracer.start_as_current_span(
        "webhook.delivery",
        attributes={
            "idis.webhook_id": webhook_id,
            "idis.delivery_attempt_id": attempt_id,
            "http.method": "POST",
            "http.url": sanitized_url,
            "net.peer.name": target_host,
        },
    ) as span:
        start_time = time.monotonic()
        status_code: int | None = None
        error: str | None = None
        success = False

        try:
            safe_headers = {
                k: v for k, v in headers.items() if k.lower() not in ("authorization", "x-api-key")
            }
            safe_headers["User-Agent"] = DEFAULT_USER_AGENT
            safe_headers["Content-Type"] = "application/json"

            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                response = await client.post(
                    url,
                    content=json.dumps(payload),
                    headers=safe_headers,
                )
                status_code = response.status_code
                success = 200 <= status_code < 300

                span.set_attribute("http.status_code", status_code)

                if not success:
                    error = f"HTTP {status_code}"
                    span.set_status(
                        trace.StatusCode.ERROR,
                        f"Webhook delivery failed: {error}",
                    )

        except httpx.TimeoutException as e:
            error = f"Timeout: {e}"
            span.set_status(trace.StatusCode.ERROR, error)
            span.record_exception(e)

        except httpx.ConnectError as e:
            error = f"Connection error: {e}"
            span.set_status(trace.StatusCode.ERROR, error)
            span.record_exception(e)

        except Exception as e:
            error = f"Delivery error: {type(e).__name__}"
            span.set_status(trace.StatusCode.ERROR, error)
            span.record_exception(e)

        finally:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            span.set_attribute("idis.delivery_duration_ms", duration_ms)

        return DeliveryResult(
            success=success,
            status_code=status_code,
            error=error,
            attempt_id=attempt_id,
            duration_ms=duration_ms,
        )


def deliver_webhook_sync(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    webhook_id: str,
    attempt_id: str,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> DeliveryResult:
    """Synchronous wrapper for webhook delivery (for testing).

    Args:
        url: Target webhook URL.
        payload: JSON payload to deliver.
        headers: HTTP headers including signature headers.
        webhook_id: UUID of the webhook subscription.
        attempt_id: UUID of this delivery attempt.
        timeout_seconds: Request timeout in seconds.

    Returns:
        DeliveryResult with success status and details.
    """
    import json
    import time

    from opentelemetry import trace

    tracer = trace.get_tracer("idis.webhooks")
    sanitized_url = _sanitize_url_for_span(url)
    target_host = _get_host_from_url(url)

    with tracer.start_as_current_span(
        "webhook.delivery",
        attributes={
            "idis.webhook_id": webhook_id,
            "idis.delivery_attempt_id": attempt_id,
            "http.method": "POST",
            "http.url": sanitized_url,
            "net.peer.name": target_host,
        },
    ) as span:
        start_time = time.monotonic()
        status_code: int | None = None
        error: str | None = None
        success = False

        try:
            safe_headers = {
                k: v for k, v in headers.items() if k.lower() not in ("authorization", "x-api-key")
            }
            safe_headers["User-Agent"] = DEFAULT_USER_AGENT
            safe_headers["Content-Type"] = "application/json"

            with httpx.Client(timeout=timeout_seconds) as client:
                response = client.post(
                    url,
                    content=json.dumps(payload),
                    headers=safe_headers,
                )
                status_code = response.status_code
                success = 200 <= status_code < 300

                span.set_attribute("http.status_code", status_code)

                if not success:
                    error = f"HTTP {status_code}"
                    span.set_status(
                        trace.StatusCode.ERROR,
                        f"Webhook delivery failed: {error}",
                    )

        except httpx.TimeoutException as e:
            error = f"Timeout: {e}"
            span.set_status(trace.StatusCode.ERROR, error)
            span.record_exception(e)

        except httpx.ConnectError as e:
            error = f"Connection error: {e}"
            span.set_status(trace.StatusCode.ERROR, error)
            span.record_exception(e)

        except Exception as e:
            error = f"Delivery error: {type(e).__name__}"
            span.set_status(trace.StatusCode.ERROR, error)
            span.record_exception(e)

        finally:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            span.set_attribute("idis.delivery_duration_ms", duration_ms)

        return DeliveryResult(
            success=success,
            status_code=status_code,
            error=error,
            attempt_id=attempt_id,
            duration_ms=duration_ms,
        )
