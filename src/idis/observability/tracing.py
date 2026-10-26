"""OpenTelemetry tracing configuration for IDIS.

Provides production-grade tracing baseline per v6.3 Tech Stack requirements:
- OpenTelemetry (MUST) per docs/07_IDIS_Tech_Stack_v6_3.md §1.5
- Correlation with request_id, tenant_id, actor_id, roles, operation_id
- Database spans are IDIS-owned SQLAlchemy engine events. The pinned
  SQLAlchemy release is outside the vendor instrumentor's supported range,
  so this module does not import opentelemetry.instrumentation.sqlalchemy.
- Webhook outbound instrumentation
- Fail-closed when IDIS_REQUIRE_OTEL=1 and init or DB-span registration fails

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
    - Database spans record only db.system and db.operation. No statement
      text, parameters, table names, connection URL, credentials, or
      exception messages in attributes, events, or status descriptions.
    - Tenant/actor IDs allowed as internal attributes (not propagated outbound)
"""

from __future__ import annotations

import contextvars
import logging
import os
import weakref
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
    is_truthy = val in ("1", "true", "yes")
    is_falsy = val in ("0", "false", "no", "")
    if is_truthy:
        return is_truthy
    if is_falsy:
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
    already_has_test_exporter = _test_exporter is not None and enabled and test_capture
    if already_has_test_exporter:
        return already_has_test_exporter

    # If already fully configured with a provider, return success
    already_configured = _is_configured and _tracer_provider is not None
    if already_configured:
        return already_configured

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
        configuration_succeeded = _tracer_provider is not None
        return configuration_succeeded

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


_DB_TRACER_NAME = "idis.db"
_DB_SPAN_STACK: contextvars.ContextVar[tuple[tuple[Any, Any], ...]] = contextvars.ContextVar(
    "idis_db_span_stack"
)
_INSTRUMENTED_ENGINES: weakref.WeakSet[Any] = weakref.WeakSet()
# fmt: off
_DB_OPERATIONS = frozenset({
    "ALTER", "BEGIN", "CALL", "COMMIT", "COPY", "CREATE", "DELETE", "DROP",
    "EXECUTE", "EXPLAIN", "INSERT", "RELEASE", "ROLLBACK", "SAVEPOINT",
    "SELECT", "SET", "SHOW", "TRUNCATE", "UPDATE", "WITH",
})
# fmt: on
_LISTENER_FAILURE = "Database span listener failed: %s"


def _sql_operation(statement: str) -> str:
    remaining = statement.lstrip()
    while remaining.startswith("/*"):
        end = remaining.find("*/")
        if end < 0:
            return "OTHER"
        remaining = remaining[end + 2 :].lstrip()
    length = 0
    while length < len(remaining) and remaining[length].isalpha():
        length += 1
    keyword = remaining[:length].upper()
    if keyword in _DB_OPERATIONS:
        return keyword
    return "OTHER"


def _statement_key(context: Any, cursor: Any) -> Any:
    if context is not None:
        return context
    return cursor


def _push_db_entry(key: Any, span: Any) -> None:
    _DB_SPAN_STACK.set((*_DB_SPAN_STACK.get(()), (key, span)))


def _close_db_entry(key: Any, error_name: str | None) -> None:
    current = _DB_SPAN_STACK.get(())
    match_index = -1
    for index in range(len(current) - 1, -1, -1):
        if current[index][0] is key:
            match_index = index
            break
    if match_index < 0:
        return
    span = current[match_index][1]
    _DB_SPAN_STACK.set(current[:match_index] + current[match_index + 1 :])
    if span is None:
        return
    try:
        if error_name is not None:
            from opentelemetry.trace import Status, StatusCode

            span.set_status(Status(StatusCode.ERROR, description=error_name))
    finally:
        span.end()


def _run_listener(callback: Any, /, *args: Any) -> None:
    try:
        callback(*args)
    except Exception as exc:
        logger.debug(_LISTENER_FAILURE, type(exc).__name__)


def _start_db_span(conn: Any, statement: str) -> Any:
    from opentelemetry import trace

    operation = _sql_operation(statement)
    system = str(conn.engine.dialect.name).lower()
    return trace.get_tracer(_DB_TRACER_NAME).start_span(
        operation,
        kind=trace.SpanKind.CLIENT,
        attributes={"db.system": system, "db.operation": operation},
    )


def _before_cursor_execute(
    conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool
) -> None:
    def open_statement() -> None:
        key = _statement_key(context, cursor)
        span: Any = None
        try:
            span = _start_db_span(conn, statement if isinstance(statement, str) else "")
        finally:
            _push_db_entry(key, span)

    _run_listener(open_statement)


def _after_cursor_execute(
    conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool
) -> None:
    _run_listener(_close_db_entry, _statement_key(context, cursor), None)


def _handle_db_error(exception_context: Any) -> None:
    def close_failed_statement() -> None:
        if exception_context.connection is None:
            return
        execution_context = exception_context.execution_context
        if execution_context is not None:
            key = execution_context
        else:
            # SQLAlchemy 2.1.4 accepts a cursor argument but does not assign the slot.
            key = getattr(exception_context, "cursor", None)
            if key is None:
                return
        caught = exception_context.sqlalchemy_exception or exception_context.original_exception
        _close_db_entry(key, type(caught).__name__)

    _run_listener(close_failed_statement)


def instrument_sqlalchemy(engine: Any) -> bool:
    """Register IDIS-owned synchronous SQLAlchemy engine-event spans.

    The pinned SQLAlchemy release is outside the vendor instrumentor's
    range, so this does not import opentelemetry.instrumentation.sqlalchemy.
    Async engines are out of scope. Listeners are before_cursor_execute,
    after_cursor_execute, and handle_error. Spans come from tracer idis.db,
    use CLIENT kind, and are children of the active span. The span name is
    the SQL operation keyword only.

    Attributes are only db.system (dialect name lower-cased; sqlite and
    postgresql stay themselves) and db.operation (first keyword, upper-cased,
    closed set, otherwise OTHER). Leading whitespace and /* */ comments are
    skipped. No statement text, parameters, table names, connection URL,
    credentials, or exception message is recorded in any attribute, event,
    or status description. On error the status is ERROR and the description
    is only the exception class name.

    Registration is idempotent per engine. A listener failure logs one debug
    line and is not raised. Registration failure logs a warning and returns
    False, or raises TracingConfigError when IDIS_REQUIRE_OTEL=1.

    Args:
        engine: Synchronous SQLAlchemy Engine.

    Returns:
        True when listeners are registered, otherwise False.

    Raises:
        TracingConfigError: Registration failed and IDIS_REQUIRE_OTEL=1.
    """
    if not _get_env_bool("IDIS_OTEL_ENABLED", False):
        return False

    try:
        from sqlalchemy import event as sa_event

        if engine in _INSTRUMENTED_ENGINES:
            return True
        listeners = (
            ("before_cursor_execute", _before_cursor_execute),
            ("after_cursor_execute", _after_cursor_execute),
            ("handle_error", _handle_db_error),
        )
        for event_name, listener in listeners:
            if not sa_event.contains(engine, event_name, listener):
                sa_event.listen(engine, event_name, listener)
        _INSTRUMENTED_ENGINES.add(engine)
        return True
    except Exception as exc:
        logger.warning("Failed to instrument SQLAlchemy: %s", type(exc).__name__)
        if _get_env_bool("IDIS_REQUIRE_OTEL", False):
            raise TracingConfigError(
                "OpenTelemetry SQLAlchemy instrumentation required but registration failed"
            ) from exc
        return False


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
