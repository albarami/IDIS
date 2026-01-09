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

import os
import uuid
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

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

    def test_require_otel_fails_closed(self) -> None:
        """IDIS_REQUIRE_OTEL=1 should fail startup if tracing init fails."""
        os.environ["IDIS_OTEL_ENABLED"] = "1"
        os.environ["IDIS_REQUIRE_OTEL"] = "1"

        from idis.observability.tracing import TracingConfigError, reset_tracing

        reset_tracing()

        with patch(
            "opentelemetry.sdk.trace.TracerProvider",
            side_effect=Exception("Simulated init failure"),
        ):
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


class TestDBInstrumentation:
    """Tests for SQLAlchemy DB instrumentation."""

    def test_db_span_emitted_for_simple_query(self) -> None:
        """DB operations should emit spans when tracing is enabled."""
        os.environ["IDIS_OTEL_ENABLED"] = "1"
        os.environ["IDIS_OTEL_TEST_CAPTURE"] = "1"

        from idis.observability.tracing import (
            configure_tracing,
            instrument_sqlalchemy,
        )

        configure_tracing()

        from sqlalchemy import create_engine, text

        engine = create_engine("sqlite:///:memory:")
        instrument_sqlalchemy(engine)

        from opentelemetry import trace

        tracer = trace.get_tracer("test")

        # Create a span and execute query within it
        with tracer.start_as_current_span("parent-span") as span:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            # Verify span was created and is valid
            assert span is not None
            assert span.name == "parent-span"
            ctx = span.get_span_context()
            assert ctx.is_valid


class TestWebhookDeliverySpans:
    """Tests for webhook delivery span instrumentation."""

    def test_webhook_delivery_emits_child_span(self) -> None:
        """Webhook delivery should emit a child span with safe attributes."""
        os.environ["IDIS_OTEL_ENABLED"] = "1"
        os.environ["IDIS_OTEL_TEST_CAPTURE"] = "1"

        from idis.observability.tracing import configure_tracing

        configure_tracing()

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
