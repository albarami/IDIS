"""OpenTelemetry tracing configuration for IDIS.

Provides production-grade tracing baseline per v6.3 Tech Stack requirements:
- OpenTelemetry (MUST) per docs/07_IDIS_Tech_Stack_v6_3.md §1.5
- Correlation with request_id, tenant_id, actor_id, roles, operation_id
- DB and webhook outbound instrumentation
- Fail-closed when IDIS_REQUIRE_OTEL=1 and init fails

Environment Variables:
    IDIS_OTEL_ENABLED: Set to "1" to enable tracing (default: disabled)
    IDIS_REQUIRE_OTEL: Set to "1" to fail startup if tracing cannot initialize
    IDIS_OTEL_SERVICE_NAME: Service name for spans (default: "idis")
    IDIS_OTEL_EXPORTER: Exporter type - "otlp" or "console" (default: "otlp")
    IDIS_OTEL_EXPORTER_OTLP_ENDPOINT: OTLP endpoint URL (optional)
    IDIS_OTEL_EXPORTER_OTLP_PROTOCOL: "grpc" or "http" (default: "grpc")
    IDIS_OTEL_RESOURCE_ATTRS: Comma-separated k=v pairs for resource attributes
    IDIS_OTEL_TEST_CAPTURE: Set to "1" to use in-memory exporter for tests

Security (per docs/IDIS_Security_Threat_Model_v6_3.md):
    - Never export API keys, Authorization headers, request bodies, or secrets
    - No full SQL with bound parameters in span attributes
    - Tenant/actor IDs allowed as internal attributes (not propagated outbound)
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
    from opentelemetry.sdk.trace.export import SpanExporter

logger = logging.getLogger(__name__)

_tracer_provider: TracerProvider | None = None
_is_configured: bool = False
_test_spans: list[ReadableSpan] = []
_test_exporter: Any = None  # Reference to InMemorySpanExporter for testing


class TracingConfigError(Exception):
    """Raised when tracing configuration fails and IDIS_REQUIRE_OTEL=1."""

    pass


def _get_env_bool(key: str, default: bool = False) -> bool:
    """Get boolean from environment variable."""
    val = os.environ.get(key, "").strip().lower()
    if val in ("1", "true", "yes"):
        return True
    if val in ("0", "false", "no", ""):
        return default
    return default


def _get_env_str(key: str, default: str = "") -> str:
    """Get string from environment variable."""
    return os.environ.get(key, default).strip()


def _parse_resource_attrs(attrs_str: str) -> dict[str, str]:
    """Parse comma-separated k=v resource attributes."""
    result: dict[str, str] = {}
    if not attrs_str:
        return result
    for pair in attrs_str.split(","):
        pair = pair.strip()
        if "=" in pair:
            k, v = pair.split("=", 1)
            result[k.strip()] = v.strip()
    return result


def _create_test_exporter() -> SpanExporter:
    """Create in-memory exporter for tests."""
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    return InMemorySpanExporter()


def _create_otlp_exporter(protocol: str, endpoint: str | None) -> Any:
    """Create OTLP exporter based on protocol."""
    kwargs: dict[str, Any] = {}
    if endpoint:
        kwargs["endpoint"] = endpoint

    if protocol == "http":
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter as HTTPExporter,
        )

        return HTTPExporter(**kwargs)

    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
        OTLPSpanExporter as GRPCExporter,
    )

    return GRPCExporter(**kwargs)


def _create_console_exporter() -> SpanExporter:
    """Create console exporter for development."""
    from opentelemetry.sdk.trace.export import ConsoleSpanExporter

    return ConsoleSpanExporter()


def configure_tracing() -> bool:
    """Configure OpenTelemetry tracing for IDIS.

    Idempotent - safe to call multiple times.

    Returns:
        True if tracing is enabled and configured, False otherwise.

    Raises:
        TracingConfigError: If IDIS_REQUIRE_OTEL=1 and configuration fails.
    """
    global _tracer_provider, _is_configured, _test_spans, _test_exporter

    enabled = _get_env_bool("IDIS_OTEL_ENABLED", False)
    require_otel = _get_env_bool("IDIS_REQUIRE_OTEL", False)
    test_capture = _get_env_bool("IDIS_OTEL_TEST_CAPTURE", False)

    # If not enabled, always return False regardless of previous state
    if not enabled:
        _is_configured = True
        logger.debug("OpenTelemetry tracing disabled (IDIS_OTEL_ENABLED not set)")
        return False

    # If test exporter already exists and enabled+test_capture, reuse it
    if _test_exporter is not None and enabled and test_capture:
        return True

    # If already fully configured with a provider, return success
    if _is_configured and _tracer_provider is not None:
        return True

    _is_configured = True

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor

        service_name = _get_env_str("IDIS_OTEL_SERVICE_NAME", "idis")
        exporter_type = _get_env_str("IDIS_OTEL_EXPORTER", "otlp")
        endpoint = _get_env_str("IDIS_OTEL_EXPORTER_OTLP_ENDPOINT", "")
        protocol = _get_env_str("IDIS_OTEL_EXPORTER_OTLP_PROTOCOL", "grpc")
        resource_attrs_str = _get_env_str("IDIS_OTEL_RESOURCE_ATTRS", "")
        test_capture = _get_env_bool("IDIS_OTEL_TEST_CAPTURE", False)

        resource_attrs = {"service.name": service_name}
        resource_attrs.update(_parse_resource_attrs(resource_attrs_str))
        resource = Resource.create(resource_attrs)

        provider = TracerProvider(resource=resource)

        if test_capture:
            from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
                InMemorySpanExporter,
            )

            _test_exporter = InMemorySpanExporter()
            test_processor = SimpleSpanProcessor(_test_exporter)
            provider.add_span_processor(test_processor)
            _test_spans.clear()
        elif exporter_type == "console":
            console_exporter = _create_console_exporter()
            console_processor = SimpleSpanProcessor(console_exporter)
            provider.add_span_processor(console_processor)
        else:
            otlp_exporter = _create_otlp_exporter(protocol, endpoint or None)
            otlp_processor = BatchSpanProcessor(otlp_exporter)
            provider.add_span_processor(otlp_processor)

        trace.set_tracer_provider(provider)
        _tracer_provider = provider

        logger.info(
            "OpenTelemetry tracing configured: service=%s, exporter=%s",
            service_name,
            exporter_type if not test_capture else "in-memory",
        )
        return True

    except Exception as e:
        logger.error("Failed to configure OpenTelemetry tracing: %s", e)
        if require_otel:
            raise TracingConfigError(
                f"OpenTelemetry tracing required but configuration failed: {e}"
            ) from e
        return False


def instrument_fastapi(app: Any) -> None:
    """Instrument FastAPI application with OpenTelemetry.

    Args:
        app: FastAPI application instance.
    """
    if not _get_env_bool("IDIS_OTEL_ENABLED", False):
        return

    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(
            app,
            excluded_urls="health,healthz,ready,readyz",
        )
        logger.debug("FastAPI instrumented with OpenTelemetry")
    except Exception as e:
        logger.warning("Failed to instrument FastAPI: %s", e)


def instrument_sqlalchemy(engine: Any) -> None:
    """Instrument SQLAlchemy engine with OpenTelemetry.

    Args:
        engine: SQLAlchemy Engine instance.
    """
    if not _get_env_bool("IDIS_OTEL_ENABLED", False):
        return

    try:
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

        SQLAlchemyInstrumentor().instrument(engine=engine, enable_commenter=False)
        logger.debug("SQLAlchemy engine instrumented with OpenTelemetry")
    except Exception as e:
        logger.warning("Failed to instrument SQLAlchemy: %s", e)


def instrument_httpx() -> None:
    """Instrument httpx client with OpenTelemetry for webhook delivery."""
    if not _get_env_bool("IDIS_OTEL_ENABLED", False):
        return

    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument()
        logger.debug("httpx instrumented with OpenTelemetry")
    except Exception as e:
        logger.warning("Failed to instrument httpx: %s", e)


def get_current_trace_id() -> str | None:
    """Get the current trace ID for logging correlation.

    Returns:
        Hex string of current trace ID, or None if no active span.
    """
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        if span is None:
            return None
        ctx = span.get_span_context()
        if ctx is None or not ctx.is_valid:
            return None
        return format(ctx.trace_id, "032x")
    except Exception:
        return None


def get_current_span_id() -> str | None:
    """Get the current span ID for logging correlation.

    Returns:
        Hex string of current span ID, or None if no active span.
    """
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        if span is None:
            return None
        ctx = span.get_span_context()
        if ctx is None or not ctx.is_valid:
            return None
        return format(ctx.span_id, "016x")
    except Exception:
        return None


def set_span_attributes(attributes: dict[str, Any]) -> None:
    """Set attributes on the current span.

    Args:
        attributes: Dictionary of attribute key-value pairs.
    """
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        if span is not None and span.is_recording():
            for key, value in attributes.items():
                if value is not None:
                    if isinstance(value, list):
                        span.set_attribute(key, ",".join(str(v) for v in value))
                    else:
                        span.set_attribute(key, str(value))
    except Exception as e:
        logger.debug("Failed to set span attributes: %s", e)


def get_test_spans() -> list[ReadableSpan]:
    """Get captured spans from in-memory exporter (for testing).

    Returns:
        List of captured spans if IDIS_OTEL_TEST_CAPTURE=1, else empty list.
    """
    global _test_exporter
    if _test_exporter is not None and hasattr(_test_exporter, "get_finished_spans"):
        return list(_test_exporter.get_finished_spans())
    return []


def clear_test_spans() -> None:
    """Clear captured spans from in-memory exporter (for testing)."""
    global _test_exporter
    if _test_exporter is not None and hasattr(_test_exporter, "clear"):
        _test_exporter.clear()


def reset_tracing() -> None:
    """Reset tracing configuration (for testing).

    Note: OpenTelemetry TracerProvider cannot be replaced once set.
    This function clears the test exporter spans but keeps the exporter
    reference intact so subsequent configure_tracing() calls work.
    """
    global _tracer_provider, _is_configured, _test_spans, _test_exporter

    # Clear the test exporter spans but keep the exporter reference
    if _test_exporter is not None and hasattr(_test_exporter, "clear"):
        _test_exporter.clear()

    # Reset configured flag to allow reconfiguration
    # but DON'T clear _test_exporter - it needs to persist
    _is_configured = False
    _test_spans.clear()
