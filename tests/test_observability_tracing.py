"""Tests for IDIS OpenTelemetry tracing baseline (OBS-001).

Per v6.3 requirements:
- Tracing OFF by default, ON via IDIS_OTEL_ENABLED=1
- Fail-closed only when IDIS_REQUIRE_OTEL=1 and init fails
- /v1 spans enriched with request_id, tenant_id, actor_id, roles, openapi_operation_id
- DB spans emitted via SQLAlchemy instrumentation
- Webhook outbound spans emitted and correlated; no secrets in spans
- Tests use in-memory exporter (no external collector required)
"""

from __future__ import annotations

import contextlib
import logging
import os
import sys
import uuid
from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    pass


@pytest.fixture(autouse=True)
def reset_tracing_env() -> Any:
    """Reset tracing environment and state before each test."""
    env_vars = [
        "IDIS_OTEL_ENABLED",
        "IDIS_REQUIRE_OTEL",
        "IDIS_OTEL_SERVICE_NAME",
        "IDIS_OTEL_EXPORTER",
        "IDIS_OTEL_TEST_CAPTURE",
        "IDIS_OTEL_EXPORTER_OTLP_ENDPOINT",
        "IDIS_OTEL_EXPORTER_OTLP_PROTOCOL",
        "IDIS_OTEL_RESOURCE_ATTRS",
    ]
    original_env = {k: os.environ.get(k) for k in env_vars}

    for k in env_vars:
        if k in os.environ:
            del os.environ[k]

    from idis.observability.tracing import reset_tracing

    reset_tracing()

    yield

    for k in env_vars:
        if k in os.environ:
            del os.environ[k]

    for k, v in original_env.items():
        if v is not None:
            os.environ[k] = v

    reset_tracing()


class TestTracingConfiguration:
    """Tests for tracing configuration behavior."""

    def test_tracing_disabled_by_default(self) -> None:
        """Tracing should be OFF when IDIS_OTEL_ENABLED is not set."""
        from idis.observability.tracing import configure_tracing, get_test_spans

        result = configure_tracing()
        assert result is False, "Tracing should be disabled by default"

        spans = get_test_spans()
        assert len(spans) == 0, "No spans should be captured when tracing is disabled"

    def test_tracing_enabled_with_env_var(self) -> None:
        """Tracing should be ON when IDIS_OTEL_ENABLED=1."""
        os.environ["IDIS_OTEL_ENABLED"] = "1"
        os.environ["IDIS_OTEL_TEST_CAPTURE"] = "1"

        from idis.observability.tracing import configure_tracing

        result = configure_tracing()
        assert result is True, "Tracing should be enabled when IDIS_OTEL_ENABLED=1"

    def test_tracing_idempotent(self) -> None:
        """configure_tracing() should be idempotent."""
        os.environ["IDIS_OTEL_ENABLED"] = "1"
        os.environ["IDIS_OTEL_TEST_CAPTURE"] = "1"

        from idis.observability.tracing import configure_tracing

        result1 = configure_tracing()
        result2 = configure_tracing()

        assert result1 == result2, "configure_tracing should be idempotent"

    def test_require_otel_fails_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """IDIS_REQUIRE_OTEL=1 should fail startup if tracing init fails."""
        os.environ["IDIS_OTEL_ENABLED"] = "1"
        os.environ["IDIS_REQUIRE_OTEL"] = "1"

        from idis.observability.tracing import TracingConfigError, reset_tracing

        reset_tracing()

        # Create a callable that raises an exception when called
        def failing_tracer_provider(*args: Any, **kwargs: Any) -> None:
            raise Exception("Simulated init failure")

        import opentelemetry.sdk.trace as otel_trace

        monkeypatch.setattr(otel_trace, "TracerProvider", failing_tracer_provider)

        from idis.observability import tracing

        tracing._is_configured = False

        with pytest.raises(TracingConfigError) as exc_info:
            tracing.configure_tracing()

        assert "configuration failed" in str(exc_info.value).lower()

    def test_service_name_configurable(self) -> None:
        """Service name should be configurable via IDIS_OTEL_SERVICE_NAME."""
        os.environ["IDIS_OTEL_ENABLED"] = "1"
        os.environ["IDIS_OTEL_TEST_CAPTURE"] = "1"
        os.environ["IDIS_OTEL_SERVICE_NAME"] = "test-service"

        from idis.observability.tracing import configure_tracing

        result = configure_tracing()
        assert result is True


class TestSpanEnrichment:
    """Tests for span attribute enrichment."""

    def test_tracing_emits_request_span_with_correlation_attrs(self) -> None:
        """Request spans should include IDIS correlation attributes."""
        os.environ["IDIS_OTEL_ENABLED"] = "1"
        os.environ["IDIS_OTEL_TEST_CAPTURE"] = "1"

        from idis.observability.tracing import configure_tracing

        configure_tracing()

        # Verify tracing middleware enriches spans by testing the helper directly
        from opentelemetry import trace

        tracer = trace.get_tracer("test")

        with tracer.start_as_current_span("test-request") as span:
            from idis.observability.tracing import set_span_attributes

            # Simulate what TracingEnrichmentMiddleware does
            set_span_attributes(
                {
                    "idis.request_id": "test-request-id-12345",
                    "idis.tenant_id": "11111111-1111-1111-1111-111111111111",
                    "idis.actor_id": "22222222-2222-2222-2222-222222222222",
                    "idis.actor_roles": "ADMIN,USER",
                    "idis.openapi_operation_id": "getTenantInfo",
                }
            )

            # Verify attributes were set
            attrs = dict(span.attributes) if span.attributes else {}
            assert attrs.get("idis.request_id") == "test-request-id-12345"
            assert attrs.get("idis.tenant_id") == "11111111-1111-1111-1111-111111111111"
            assert attrs.get("idis.actor_id") == "22222222-2222-2222-2222-222222222222"
            assert attrs.get("idis.actor_roles") == "ADMIN,USER"
            assert attrs.get("idis.openapi_operation_id") == "getTenantInfo"

    def test_set_span_attributes_helper(self) -> None:
        """set_span_attributes should safely set attributes on current span."""
        os.environ["IDIS_OTEL_ENABLED"] = "1"
        os.environ["IDIS_OTEL_TEST_CAPTURE"] = "1"

        from idis.observability.tracing import (
            configure_tracing,
            set_span_attributes,
        )

        configure_tracing()

        from opentelemetry import trace

        tracer = trace.get_tracer("test")

        with tracer.start_as_current_span("test-span") as span:
            set_span_attributes(
                {
                    "idis.test_attr": "test_value",
                    "idis.tenant_id": "11111111-1111-1111-1111-111111111111",
                    "idis.roles": ["ADMIN", "USER"],
                }
            )
            # Verify attributes were set on current span
            attrs = dict(span.attributes) if span.attributes else {}
            assert attrs.get("idis.test_attr") == "test_value"
            assert attrs.get("idis.tenant_id") == "11111111-1111-1111-1111-111111111111"
            assert attrs.get("idis.roles") == "ADMIN,USER"


class TestTraceIdHelpers:
    """Tests for trace ID helper functions."""

    def test_get_current_trace_id_returns_none_without_span(self) -> None:
        """get_current_trace_id should return None when no active span."""
        os.environ["IDIS_OTEL_ENABLED"] = "1"
        os.environ["IDIS_OTEL_TEST_CAPTURE"] = "1"

        from idis.observability.tracing import configure_tracing, get_current_trace_id

        configure_tracing()

        trace_id = get_current_trace_id()
        assert trace_id is None or trace_id == "0" * 32

    def test_get_current_trace_id_returns_hex_within_span(self) -> None:
        """get_current_trace_id should return hex trace ID within active span."""
        os.environ["IDIS_OTEL_ENABLED"] = "1"
        os.environ["IDIS_OTEL_TEST_CAPTURE"] = "1"

        from idis.observability.tracing import configure_tracing, get_current_trace_id

        configure_tracing()

        from opentelemetry import trace

        tracer = trace.get_tracer("test")

        with tracer.start_as_current_span("test-span"):
            trace_id = get_current_trace_id()
            assert trace_id is not None
            assert len(trace_id) == 32
            int(trace_id, 16)


def _instrumented_sqlite_engine(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Enable in-memory capture and register DB spans on a new SQLite engine."""
    monkeypatch.setenv("IDIS_OTEL_ENABLED", "1")
    monkeypatch.setenv("IDIS_OTEL_TEST_CAPTURE", "1")

    from sqlalchemy import create_engine

    from idis.observability.tracing import (
        clear_test_spans,
        configure_tracing,
        instrument_sqlalchemy,
    )

    configure_tracing()
    clear_test_spans()
    engine = create_engine("sqlite:///:memory:")
    instrument_sqlalchemy(engine)
    return engine


def _spans_with_db_system(spans: list[Any]) -> list[Any]:
    """Return captured spans that carry a db.system attribute."""
    selected: list[Any] = []
    for span in spans:
        attributes = dict(span.attributes) if span.attributes else {}
        if "db.system" in attributes:
            selected.append(span)
    return selected


def _recorded_span_text(span: Any) -> str:
    """Join the span name, attributes, events, and status description."""
    parts = [str(span.name)]
    attributes = dict(span.attributes) if span.attributes else {}
    for key, value in attributes.items():
        parts.append(str(key))
        parts.append(str(value))
    for event in span.events:
        parts.append(str(event.name))
        event_attributes = dict(event.attributes) if event.attributes else {}
        for key, value in event_attributes.items():
            parts.append(str(key))
            parts.append(str(value))
    description = span.status.description if span.status is not None else None
    if description:
        parts.append(str(description))
    return "\n".join(parts)


def _engine_listener_count(engine: Any) -> int:
    """Count the engine-event listeners this instrumentation registers."""
    return (
        len(engine.dispatch.before_cursor_execute)
        + len(engine.dispatch.after_cursor_execute)
        + len(engine.dialect.dispatch.handle_error)
    )


class _SpanEndRecord:
    """Counts end() calls on one database span."""

    def __init__(self, span: Any) -> None:
        self.span = span
        self.ends = 0


class _FakeDialect:
    name = "sqlite"


class _FakeEngine:
    dialect = _FakeDialect()


class _FakeConnection:
    engine = _FakeEngine()


class _FakeExceptionContext:
    """Small stand-in for a SQLAlchemy ExceptionContext."""

    def __init__(
        self,
        connection: Any,
        execution_context: Any,
        cursor: Any,
        exception: BaseException,
    ) -> None:
        self.connection = connection
        self.execution_context = execution_context
        self.cursor = cursor
        self.sqlalchemy_exception = exception
        self.original_exception = exception


def _configured_db_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enable in-memory span capture without registering an engine."""
    monkeypatch.setenv("IDIS_OTEL_ENABLED", "1")
    monkeypatch.setenv("IDIS_OTEL_TEST_CAPTURE", "1")

    from idis.observability.tracing import clear_test_spans, configure_tracing

    configure_tracing()
    clear_test_spans()


def _record_db_spans(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fail_on_idis_db_call: int | None = None,
) -> list[_SpanEndRecord]:
    """Wrap idis.db span creation and count each span's end() calls."""
    from opentelemetry import trace

    real_get_tracer = trace.get_tracer
    records: list[_SpanEndRecord] = []
    state = {"calls": 0, "wrapped": False}

    def get_tracer(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "idis.db":
            state["calls"] += 1
            if fail_on_idis_db_call is not None and state["calls"] == fail_on_idis_db_call:
                raise RuntimeError("span creation failed")
        tracer = real_get_tracer(name, *args, **kwargs)
        if name != "idis.db" or state["wrapped"]:
            return tracer
        state["wrapped"] = True
        real_start = tracer.start_span

        def start_span(*span_args: Any, **span_kwargs: Any) -> Any:
            span = real_start(*span_args, **span_kwargs)
            record = _SpanEndRecord(span)
            original_end = span.end

            def end(*end_args: Any, **end_kwargs: Any) -> None:
                record.ends += 1
                original_end(*end_args, **end_kwargs)

            monkeypatch.setattr(span, "end", end)
            records.append(record)
            return span

        monkeypatch.setattr(tracer, "start_span", start_span)
        return tracer

    monkeypatch.setattr(trace, "get_tracer", get_tracer)
    return records


def _direct_db_spans(monkeypatch: pytest.MonkeyPatch) -> list[_SpanEndRecord]:
    """Configure capture and record spans created by direct listener calls."""
    _configured_db_capture(monkeypatch)
    return _record_db_spans(monkeypatch)


class TestDBInstrumentation:
    """Tests for SQLAlchemy DB instrumentation."""

    def test_db_span_emitted_for_simple_query(self) -> None:
        """DB operations should emit spans with db.system attribute."""
        os.environ["IDIS_OTEL_ENABLED"] = "1"
        os.environ["IDIS_OTEL_TEST_CAPTURE"] = "1"

        from idis.observability.tracing import (
            clear_test_spans,
            configure_tracing,
            get_test_spans,
            instrument_sqlalchemy,
        )

        configure_tracing()
        clear_test_spans()

        from sqlalchemy import create_engine, text

        engine = create_engine("sqlite:///:memory:")
        instrument_sqlalchemy(engine)

        from opentelemetry import trace

        tracer = trace.get_tracer("test")

        # Create a parent span and execute query within it
        with tracer.start_as_current_span("parent-span") as parent_span:
            parent_trace_id = parent_span.get_span_context().trace_id
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))

        # Fetch captured spans and validate DB span existence
        spans = get_test_spans()
        assert len(spans) >= 1, f"Expected at least 1 span, got {len(spans)}"

        # Find DB span with db.system attribute
        db_spans = []
        for s in spans:
            attrs = dict(s.attributes) if s.attributes else {}
            if "db.system" in attrs:
                db_spans.append(s)

        assert len(db_spans) >= 1, (
            f"Expected at least one DB span with db.system attribute. "
            f"Captured span names: {[s.name for s in spans]}"
        )

        # Verify DB span attributes
        db_span = db_spans[0]
        db_attrs = dict(db_span.attributes) if db_span.attributes else {}
        assert "db.system" in db_attrs, "DB span must have db.system attribute"
        # SQLite should report "sqlite" as db.system
        assert db_attrs["db.system"] in ("sqlite", "postgresql"), (
            f"db.system should be sqlite or postgresql, got {db_attrs['db.system']}"
        )

        # Verify DB span is descendant of parent (same trace_id)
        db_span_trace_id = db_span.get_span_context().trace_id
        assert db_span_trace_id == parent_trace_id, (
            "DB span should be in the same trace as parent span"
        )

    def test_db_span_emitted_without_vendor_sqlalchemy_instrumentor(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DB spans are emitted when the vendor SQLAlchemy instrumentor cannot be imported."""
        monkeypatch.setitem(sys.modules, "opentelemetry.instrumentation.sqlalchemy", None)
        engine = _instrumented_sqlite_engine(monkeypatch)

        from sqlalchemy import text

        from idis.observability.tracing import get_test_spans

        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))

        db_spans = _spans_with_db_system(get_test_spans())
        assert len(db_spans) >= 1, (
            "DB span must be emitted without opentelemetry.instrumentation.sqlalchemy"
        )

    def test_db_span_attributes_omit_statement_parameters_url_and_table(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A SELECT span carries only db.system and db.operation, with no query details."""
        engine = _instrumented_sqlite_engine(monkeypatch)
        statement = "SELECT token FROM secret_table_z9 WHERE token = :token"
        marker = "bind_marker_z9"
        table_name = "secret_table_z9"

        from sqlalchemy import text

        from idis.observability.tracing import clear_test_spans, get_test_spans

        clear_test_spans()
        with engine.connect() as conn:
            conn.execute(text("CREATE TABLE secret_table_z9 (token TEXT)"))
            conn.execute(text(statement), {"token": marker})

        db_spans = _spans_with_db_system(get_test_spans())
        select_spans = [
            span for span in db_spans if dict(span.attributes or {}).get("db.operation") == "SELECT"
        ]
        assert len(select_spans) == 1
        select_span = select_spans[0]
        attributes = dict(select_span.attributes or {})
        assert attributes["db.system"] == "sqlite"
        assert set(attributes) == {"db.system", "db.operation"}
        assert select_span.name == "SELECT"
        forbidden = (statement, marker, table_name, "memory")
        for span in db_spans:
            recorded = _recorded_span_text(span).lower()
            for secret in forbidden:
                assert secret.lower() not in recorded

    def test_db_span_parent_is_the_active_span(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The DB span is a CLIENT child of the span active during execution."""
        engine = _instrumented_sqlite_engine(monkeypatch)

        from opentelemetry import trace
        from opentelemetry.trace import SpanKind
        from sqlalchemy import text

        from idis.observability.tracing import get_test_spans

        tracer = trace.get_tracer("test")
        with tracer.start_as_current_span("parent-span") as parent:
            parent_context = parent.get_span_context()
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))

        db_spans = _spans_with_db_system(get_test_spans())
        assert len(db_spans) == 1
        db_span = db_spans[0]
        assert db_span.kind == SpanKind.CLIENT
        assert db_span.context.trace_id == parent_context.trace_id
        assert db_span.parent is not None
        assert db_span.parent.span_id == parent_context.span_id

    def test_second_instrument_call_does_not_duplicate_db_span(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two registrations of one engine emit one DB span for one statement."""
        monkeypatch.setenv("IDIS_OTEL_ENABLED", "1")
        monkeypatch.setenv("IDIS_OTEL_TEST_CAPTURE", "1")

        from sqlalchemy import create_engine, text

        from idis.observability.tracing import (
            clear_test_spans,
            configure_tracing,
            get_test_spans,
            instrument_sqlalchemy,
        )

        configure_tracing()
        clear_test_spans()
        engine = create_engine("sqlite:///:memory:")
        assert instrument_sqlalchemy(engine) is True
        assert instrument_sqlalchemy(engine) is True
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))

        assert len(get_test_spans()) == 1
        assert len(_spans_with_db_system(get_test_spans())) == 1

    def test_failing_statement_sets_error_status_to_exception_class_name(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A syntax error ends the span with ERROR and the exception class name only."""
        engine = _instrumented_sqlite_engine(monkeypatch)
        statement = "SELECT FROM MARKER_SYNTAX"

        from opentelemetry.trace import StatusCode
        from sqlalchemy import text
        from sqlalchemy.exc import OperationalError

        from idis.observability.tracing import clear_test_spans, get_test_spans

        clear_test_spans()
        with engine.connect() as conn, pytest.raises(OperationalError) as exc_info:
            conn.execute(text(statement))

        assert "MARKER_SYNTAX" in str(exc_info.value)
        db_spans = _spans_with_db_system(get_test_spans())
        assert len(db_spans) == 1
        db_span = db_spans[0]
        assert db_span.status.status_code == StatusCode.ERROR
        assert db_span.status.description == type(exc_info.value).__name__
        assert "MARKER_SYNTAX" not in (db_span.status.description or "")
        assert "MARKER_SYNTAX" not in _recorded_span_text(db_span)

    def test_disabled_tracing_returns_false_without_listeners(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Unset IDIS_OTEL_ENABLED returns False and does not register listeners."""
        monkeypatch.delenv("IDIS_OTEL_ENABLED", raising=False)

        from sqlalchemy import create_engine

        from idis.observability.tracing import instrument_sqlalchemy

        engine = create_engine("sqlite:///:memory:")
        before = _engine_listener_count(engine)
        assert instrument_sqlalchemy(engine) is False
        assert _engine_listener_count(engine) == before

    def test_registration_failure_returns_false_when_require_otel_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A listener-registration failure returns False when IDIS_REQUIRE_OTEL is unset."""
        monkeypatch.setenv("IDIS_OTEL_ENABLED", "1")
        monkeypatch.delenv("IDIS_REQUIRE_OTEL", raising=False)

        from sqlalchemy import create_engine

        from idis.observability.tracing import instrument_sqlalchemy

        engine = create_engine("sqlite:///:memory:")

        def fail_listen(*args: object, **kwargs: object) -> None:
            raise RuntimeError("registration failed")

        monkeypatch.setattr("sqlalchemy.event.listen", fail_listen)
        assert instrument_sqlalchemy(engine) is False

    def test_registration_failure_raises_when_require_otel_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A listener-registration failure raises when IDIS_REQUIRE_OTEL=1."""
        monkeypatch.setenv("IDIS_OTEL_ENABLED", "1")
        monkeypatch.setenv("IDIS_REQUIRE_OTEL", "1")

        from sqlalchemy import create_engine

        from idis.observability.tracing import TracingConfigError, instrument_sqlalchemy

        engine = create_engine("sqlite:///:memory:")

        def fail_listen(*args: object, **kwargs: object) -> None:
            raise RuntimeError("registration failed")

        monkeypatch.setattr("sqlalchemy.event.listen", fail_listen)
        with pytest.raises(TracingConfigError):
            instrument_sqlalchemy(engine)

    def test_tracer_failure_inside_listener_does_not_break_execute(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A tracer failure inside a listener is logged and the statement still runs."""
        engine = _instrumented_sqlite_engine(monkeypatch)

        def fail_get_tracer(*args: object, **kwargs: object) -> None:
            raise RuntimeError("tracer failed")

        monkeypatch.setattr("opentelemetry.trace.get_tracer", fail_get_tracer)

        from sqlalchemy import text

        with (
            caplog.at_level(logging.DEBUG, logger="idis.observability.tracing"),
            engine.connect() as conn,
        ):
            value = conn.execute(text("SELECT 1")).scalar_one()

        assert value == 1
        failures = [
            record
            for record in caplog.records
            if record.name == "idis.observability.tracing"
            and record.levelno == logging.DEBUG
            and record.getMessage().startswith("Database span listener failed:")
        ]
        assert len(failures) == 1

    @pytest.mark.parametrize(
        ("statement", "operation"),
        [
            pytest.param("   SELECT 1", "SELECT", id="leading-whitespace"),
            pytest.param("/* x */ select 1", "SELECT", id="block-comment"),
            pytest.param("select 1", "SELECT", id="lower-case"),
            pytest.param(
                "with t as (select 1 as a) select a from t",
                "WITH",
                id="with",
            ),
            pytest.param("NOT_A_KEYWORD", "OTHER", id="unknown-word"),
        ],
    )
    def test_db_operation_mapping(
        self,
        monkeypatch: pytest.MonkeyPatch,
        statement: str,
        operation: str,
    ) -> None:
        """The first SQL keyword maps to the closed operation set, otherwise OTHER."""
        engine = _instrumented_sqlite_engine(monkeypatch)

        from sqlalchemy import text
        from sqlalchemy.exc import OperationalError

        from idis.observability.tracing import clear_test_spans, get_test_spans

        clear_test_spans()
        with engine.connect() as conn, contextlib.suppress(OperationalError):
            conn.execute(text(statement))

        db_spans = _spans_with_db_system(get_test_spans())
        assert len(db_spans) == 1
        attributes = dict(db_spans[0].attributes or {})
        assert attributes["db.operation"] == operation
        assert db_spans[0].name == operation


class TestDBSpanPairing:
    """Keyed pairing of database spans across nested executes and errors."""

    @pytest.fixture(autouse=True)
    def _reset_db_span_stack(self) -> Any:
        from idis.observability.tracing import _DB_SPAN_STACK

        _DB_SPAN_STACK.set(())
        yield
        _DB_SPAN_STACK.set(())

    def test_inner_tracer_failure_does_not_end_the_outer_span(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failed inner span stays absent and the outer span ends on its own after."""
        from opentelemetry.trace import StatusCode
        from sqlalchemy import event, text

        from idis.observability.tracing import get_test_spans

        engine = _instrumented_sqlite_engine(monkeypatch)
        records = _record_db_spans(monkeypatch, fail_on_idis_db_call=2)
        during: dict[str, Any] = {}

        def run_inner_statement(
            conn: Any,
            cursor: Any,
            statement: str,
            parameters: Any,
            context: Any,
            executemany: bool,
        ) -> None:
            if statement != "SELECT 1":
                return
            with engine.connect() as inner_conn:
                inner_conn.execute(text("SELECT 2"))
            during["ends"] = [record.ends for record in records]
            during["finished"] = len(_spans_with_db_system(get_test_spans()))

        event.listen(engine, "before_cursor_execute", run_inner_statement)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))

        assert during["ends"] == [0]
        assert during["finished"] == 0
        assert [record.ends for record in records] == [1]
        finished = _spans_with_db_system(get_test_spans())
        assert len(finished) == 1
        assert finished[0].name == "SELECT"
        assert finished[0].status.status_code != StatusCode.ERROR

    def test_raising_inner_after_listener_does_not_end_the_outer_span(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """handle_error after the inner after leaves the outer span open until its own after."""
        from opentelemetry.trace import StatusCode
        from sqlalchemy import event, text

        engine = _instrumented_sqlite_engine(monkeypatch)
        records = _record_db_spans(monkeypatch)
        during: dict[str, list[int]] = {}

        def run_inner_statement(
            conn: Any,
            cursor: Any,
            statement: str,
            parameters: Any,
            context: Any,
            executemany: bool,
        ) -> None:
            if statement != "SELECT 1":
                return
            with engine.connect() as inner_conn, contextlib.suppress(RuntimeError):
                inner_conn.execute(text("SELECT 2"))
            during["ends"] = [record.ends for record in records]

        def raise_for_inner_statement(
            conn: Any,
            cursor: Any,
            statement: str,
            parameters: Any,
            context: Any,
            executemany: bool,
        ) -> None:
            if statement == "SELECT 2":
                raise RuntimeError("after listener failed")

        event.listen(engine, "before_cursor_execute", run_inner_statement)
        event.listen(engine, "after_cursor_execute", raise_for_inner_statement)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))

        assert during["ends"] == [0, 1]
        assert [record.ends for record in records] == [1, 1]
        assert records[0].span.status.status_code != StatusCode.ERROR
        assert records[1].span.status.status_code != StatusCode.ERROR
        assert records[1].span.status.description in (None, "")

    def test_handle_error_for_unmatched_key_leaves_stack_unchanged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An error whose key has no entry does not close another statement."""
        from idis.observability.tracing import (
            _DB_SPAN_STACK,
            _before_cursor_execute,
            _handle_db_error,
        )

        records = _direct_db_spans(monkeypatch)
        context = object()
        connection = _FakeConnection()
        _before_cursor_execute(connection, object(), "SELECT 1", None, context, False)
        stack_before = _DB_SPAN_STACK.get(())

        _handle_db_error(
            _FakeExceptionContext(
                connection=object(),
                execution_context=object(),
                cursor=object(),
                exception=RuntimeError("unmatched"),
            )
        )

        assert records[0].ends == 0
        assert _DB_SPAN_STACK.get(()) is stack_before

    def test_handle_error_without_connection_leaves_stack_unchanged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """handle_error with connection None does not close the open statement."""
        from idis.observability.tracing import (
            _DB_SPAN_STACK,
            _before_cursor_execute,
            _handle_db_error,
        )

        records = _direct_db_spans(monkeypatch)
        context = object()
        cursor = object()
        connection = _FakeConnection()
        _before_cursor_execute(connection, cursor, "SELECT 1", None, context, False)
        stack_before = _DB_SPAN_STACK.get(())

        _handle_db_error(
            _FakeExceptionContext(
                connection=None,
                execution_context=context,
                cursor=cursor,
                exception=RuntimeError("no connection"),
            )
        )

        assert records[0].ends == 0
        assert _DB_SPAN_STACK.get(()) is stack_before

    def test_handle_error_without_context_or_cursor_leaves_stack_unchanged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """handle_error does nothing when both the context and the cursor are None."""
        from idis.observability.tracing import (
            _DB_SPAN_STACK,
            _before_cursor_execute,
            _handle_db_error,
        )

        records = _direct_db_spans(monkeypatch)
        connection = _FakeConnection()
        _before_cursor_execute(connection, None, "SELECT 1", None, None, False)
        stack_before = _DB_SPAN_STACK.get(())

        _handle_db_error(
            _FakeExceptionContext(
                connection=connection,
                execution_context=None,
                cursor=None,
                exception=RuntimeError("no key"),
            )
        )

        assert records[0].ends == 0
        assert _DB_SPAN_STACK.get(()) is stack_before

    def test_after_pairs_by_cursor_when_execution_context_is_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A missing execution context pairs the after event by cursor identity."""
        from opentelemetry.trace import StatusCode

        from idis.observability.tracing import (
            _DB_SPAN_STACK,
            _after_cursor_execute,
            _before_cursor_execute,
        )

        records = _direct_db_spans(monkeypatch)
        cursor = object()
        other_cursor = object()
        connection = _FakeConnection()
        _before_cursor_execute(connection, cursor, "SELECT 1", None, None, False)
        _after_cursor_execute(connection, other_cursor, "SELECT 1", None, None, False)

        assert records[0].ends == 0
        stack = _DB_SPAN_STACK.get(())
        assert len(stack) == 1
        assert stack[0][0] is cursor

        _after_cursor_execute(connection, cursor, "SELECT 1", None, None, False)
        assert records[0].ends == 1
        assert records[0].span.status.status_code != StatusCode.ERROR
        assert _DB_SPAN_STACK.get(()) == ()

        _after_cursor_execute(connection, cursor, "SELECT 1", None, None, False)
        assert records[0].ends == 1

    def test_handle_error_pairs_by_cursor_when_execution_context_is_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A missing execution context closes only the entry with the same cursor."""
        from opentelemetry.trace import StatusCode

        from idis.observability.tracing import (
            _DB_SPAN_STACK,
            _before_cursor_execute,
            _handle_db_error,
        )

        records = _direct_db_spans(monkeypatch)
        cursor_a = object()
        cursor_b = object()
        connection = _FakeConnection()
        _before_cursor_execute(connection, cursor_a, "SELECT 1", None, None, False)
        _before_cursor_execute(connection, cursor_b, "SELECT 2", None, None, False)

        _handle_db_error(
            _FakeExceptionContext(
                connection=connection,
                execution_context=None,
                cursor=cursor_a,
                exception=RuntimeError("cursor fault"),
            )
        )

        assert [record.ends for record in records] == [1, 0]
        assert records[0].span.status.status_code == StatusCode.ERROR
        assert records[0].span.status.description == "RuntimeError"
        stack = _DB_SPAN_STACK.get(())
        assert len(stack) == 1
        assert stack[0][0] is cursor_b


class TestWebhookDeliverySpans:
    """Tests for webhook delivery span instrumentation."""

    def test_webhook_delivery_emits_child_span(self) -> None:
        """Webhook delivery should emit a span with safe attributes and identifiers."""
        os.environ["IDIS_OTEL_ENABLED"] = "1"
        os.environ["IDIS_OTEL_TEST_CAPTURE"] = "1"

        from idis.observability.tracing import (
            clear_test_spans,
            configure_tracing,
            get_test_spans,
        )

        configure_tracing()
        clear_test_spans()

        from idis.services.webhooks.delivery import deliver_webhook_sync

        webhook_id = str(uuid.uuid4())
        attempt_id = str(uuid.uuid4())

        # Call delivery - it will fail to connect but span should still be created
        result = deliver_webhook_sync(
            url="http://localhost:9999/nonexistent",
            payload={"event": "test.event", "data": {"id": "123"}},
            headers={"X-Custom-Header": "test"},
            webhook_id=webhook_id,
            attempt_id=attempt_id,
            timeout_seconds=1,
        )

        # Verify result structure
        assert result.success is False
        assert result.error is not None
        assert result.attempt_id == attempt_id

        # Fetch captured spans and validate webhook delivery span
        spans = get_test_spans()
        assert len(spans) >= 1, f"Expected at least 1 span, got {len(spans)}"

        # Find webhook delivery span
        webhook_spans = [s for s in spans if "webhook" in s.name.lower()]
        assert len(webhook_spans) >= 1, (
            f"Expected at least one webhook delivery span. "
            f"Captured spans: {[s.name for s in spans]}"
        )

        # Verify webhook span attributes
        webhook_span = webhook_spans[0]
        attrs = dict(webhook_span.attributes) if webhook_span.attributes else {}

        # Assert identifiers are present
        assert attrs.get("idis.webhook_id") == webhook_id, (
            f"Expected idis.webhook_id={webhook_id}, got {attrs.get('idis.webhook_id')}"
        )
        assert attrs.get("idis.delivery_attempt_id") == attempt_id, (
            f"Expected idis.delivery_attempt_id={attempt_id}, "
            f"got {attrs.get('idis.delivery_attempt_id')}"
        )

        # Assert URL attribute is sanitized (no credentials/query/fragment)
        url_attr = attrs.get("http.url", "")
        assert "@" not in url_attr, f"URL should not contain userinfo (@): {url_attr}"
        assert "?" not in url_attr, f"URL should not contain query (?): {url_attr}"
        assert "#" not in url_attr, f"URL should not contain fragment (#): {url_attr}"

    def test_webhook_delivery_sanitizes_url(self) -> None:
        """Webhook delivery spans should not include querystring or auth in URL."""
        from idis.services.webhooks.delivery import _sanitize_url_for_span

        # Test URL sanitization directly
        url_with_secret = "http://localhost:9999/webhook?secret=mysecret&token=abc123"
        sanitized = _sanitize_url_for_span(url_with_secret)

        assert "secret" not in sanitized.lower()
        assert "token" not in sanitized.lower()
        assert "?" not in sanitized
        assert sanitized == "http://localhost:9999/webhook"

    def test_webhook_url_sanitization_strips_userinfo(self) -> None:
        """URL sanitization must strip userinfo (credentials) from URLs."""
        from idis.services.webhooks.delivery import _sanitize_url_for_span

        # Test URLs with userinfo (credentials)
        test_cases = [
            ("http://user:pass@example.com/webhook", "http://example.com/webhook"),
            (
                "https://admin:secret123@api.example.com:8443/hook",
                "https://api.example.com:8443/hook",
            ),
            ("http://user@example.com/path", "http://example.com/path"),
            ("http://user:pass@host:9999/path?query=1#frag", "http://host:9999/path"),
        ]

        for raw_url, expected in test_cases:
            sanitized = _sanitize_url_for_span(raw_url)
            # Must not contain @ (userinfo separator)
            assert "@" not in sanitized, f"Sanitized URL contains @: {sanitized} (from {raw_url})"
            # Must not contain query
            assert "?" not in sanitized, f"Sanitized URL contains ?: {sanitized} (from {raw_url})"
            # Must not contain fragment
            assert "#" not in sanitized, f"Sanitized URL contains #: {sanitized} (from {raw_url})"
            # Should match expected
            assert sanitized == expected, f"Expected {expected}, got {sanitized} (from {raw_url})"

    def test_webhook_span_url_attribute_is_safe(self) -> None:
        """Webhook span URL attributes must never contain credentials/query/fragment."""
        os.environ["IDIS_OTEL_ENABLED"] = "1"
        os.environ["IDIS_OTEL_TEST_CAPTURE"] = "1"

        from idis.observability.tracing import (
            clear_test_spans,
            configure_tracing,
            get_test_spans,
        )

        configure_tracing()
        clear_test_spans()

        from idis.services.webhooks.delivery import deliver_webhook_sync

        # Use a URL with credentials, query, and fragment
        dangerous_url = "http://user:pass@localhost:9999/webhook?secret=abc&token=xyz#section"

        deliver_webhook_sync(
            url=dangerous_url,
            payload={"event": "test"},
            headers={},
            webhook_id=str(uuid.uuid4()),
            attempt_id=str(uuid.uuid4()),
            timeout_seconds=1,
        )

        spans = get_test_spans()
        webhook_spans = [s for s in spans if "webhook" in s.name.lower()]
        assert len(webhook_spans) >= 1, "Expected webhook span"

        # Check all URL-related attributes
        for span in webhook_spans:
            attrs = dict(span.attributes) if span.attributes else {}
            for key in ("http.url", "url.full", "http.target"):
                if key in attrs:
                    url_val = str(attrs[key])
                    assert "@" not in url_val, f"{key} contains @: {url_val}"
                    assert "?" not in url_val, f"{key} contains ?: {url_val}"
                    assert "#" not in url_val, f"{key} contains #: {url_val}"
                    assert "user" not in url_val.lower(), f"{key} contains 'user': {url_val}"
                    assert "pass" not in url_val.lower(), f"{key} contains 'pass': {url_val}"
                    assert "secret" not in url_val.lower(), f"{key} contains 'secret': {url_val}"


class TestTracingWithAppDisabled:
    """Tests to ensure app works correctly when tracing is disabled."""

    def test_app_functions_without_tracing(self) -> None:
        """Application should function normally when tracing is disabled."""
        from idis.api.main import create_app

        app = create_app()
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/health")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "ok"

    def test_no_errors_when_tracing_disabled(self) -> None:
        """No errors should occur when calling tracing helpers with tracing disabled."""
        from idis.observability.tracing import (
            get_current_span_id,
            get_current_trace_id,
            get_test_spans,
            set_span_attributes,
        )

        trace_id = get_current_trace_id()
        span_id = get_current_span_id()
        spans = get_test_spans()

        set_span_attributes({"test": "value"})

        assert trace_id is None or isinstance(trace_id, str)
        assert span_id is None or isinstance(span_id, str)
        assert isinstance(spans, list)
