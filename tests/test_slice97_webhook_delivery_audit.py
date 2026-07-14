"""Slice97 Task 6 - webhook delivery audit metadata + Prometheus counters.

RED-first. Each dispatched outbox row emits a ``webhook.delivery.succeeded`` / ``.failed`` audit
event carrying SAFE METADATA ONLY (webhook_id, event_id, event_type, attempt_count, status_code,
outcome - never url, secret, body, headers, or paths), validated by ``validate_audit_event``; and
increments the Prometheus counters the SLO dashboard already queries
(``webhook_delivery_success_total`` / ``webhook_delivery_attempts_total``, GLOBAL aggregates -
no tenant label, because the /metrics scrape surface is unauthenticated).
Audit/metrics are best-effort and must not change Task 5 dispatch semantics. PYTHONPATH pinned to
this worktree's src.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest

from idis.observability.metrics import (
    WEBHOOK_DELIVERY_ATTEMPTS_TOTAL,
    WEBHOOK_DELIVERY_SUCCESS_TOTAL,
    get_counter,
    render_prometheus_text,
    reset_metrics,
)
from idis.persistence.repositories.webhook_outbox import InMemoryWebhookOutboxRepository
from idis.services.webhooks.dispatcher import WebhookDispatcher
from idis.services.webhooks.retry import MAX_ATTEMPTS
from idis.validators.audit_event_validator import validate_audit_event

_TENANT = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_WEBHOOK = "wwwwwwww-wwww-wwww-wwww-wwwwwwwwwwww"
_URL = "https://example.test/hook"
_SECRET = "s97-audit-secret-XYZ"
_T0 = datetime(2026, 7, 10, 0, 0, 0, tzinfo=UTC)

_ALLOWED_SAFE_KEYS = {
    "webhook_id",
    "event_id",
    "event_type",
    "attempt_count",
    "status_code",
    "outcome",
}
_FORBIDDEN_SUBSTRINGS = (
    _SECRET,
    "example.test",
    "https://",
    "http://",
    "x-idis-webhook-signature",
    "x-idis-webhook-timestamp",
    "local_path",
    "/var/",
)


class _CapturingSink:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, event: dict[str, Any]) -> None:
        self.events.append(event)


class _FakeDelivery:
    def __init__(self, *, success: bool = True, status_code: int | None = 200) -> None:
        from idis.services.webhooks.delivery import DeliveryResult

        self._result = DeliveryResult(
            success=success,
            status_code=status_code,
            error=None if success else f"HTTP {status_code}" if status_code else "Timeout: x",
            attempt_id="",
            duration_ms=1,
        )

    def __call__(self, **_: Any) -> Any:
        return self._result


def _loader(conn: Any, webhook_id: str) -> tuple[str, str | None]:
    return (_URL, _SECRET)


@pytest.fixture(autouse=True)
def _clean_metrics() -> Iterator[None]:
    reset_metrics()
    yield
    reset_metrics()


def _enqueue(outbox: InMemoryWebhookOutboxRepository) -> str:
    event_id = str(uuid.uuid4())
    outbox.enqueue(
        webhook_id=_WEBHOOK,
        tenant_id=_TENANT,
        event_id=event_id,
        event_type="run.completed",
        payload={"event_type": "run.completed", "data": {"status": "COMPLETED"}},
        now=_T0,
    )
    return event_id


def _drain(
    *, success: bool = True, status_code: int | None = 200
) -> tuple[_CapturingSink, InMemoryWebhookOutboxRepository, str]:
    outbox = InMemoryWebhookOutboxRepository()
    event_id = _enqueue(outbox)
    sink = _CapturingSink()
    dispatcher = WebhookDispatcher(
        outbox=outbox,
        secret_loader=_loader,
        deliver_fn=_FakeDelivery(success=success, status_code=status_code),
        audit_sink=sink,
    )
    dispatcher.drain_once(tenant_id=_TENANT, now=_T0, limit=10)
    return sink, outbox, event_id


def test_success_emits_succeeded_audit_and_increments_counters() -> None:
    sink, _, event_id = _drain(success=True, status_code=200)

    (event,) = sink.events
    assert event["event_type"] == "webhook.delivery.succeeded"
    assert event["tenant_id"] == _TENANT
    assert validate_audit_event(event).passed  # full v6.3 validation
    safe = event["payload"]["safe"]
    assert safe["webhook_id"] == _WEBHOOK
    assert safe["event_id"] == event_id
    assert safe["event_type"] == "run.completed"
    assert safe["attempt_count"] == 1
    assert safe["status_code"] == 200
    assert safe["outcome"] == "succeeded"

    # Global aggregates by design: no tenant label on the unauthenticated scrape surface.
    assert get_counter(WEBHOOK_DELIVERY_ATTEMPTS_TOTAL) == 1
    assert get_counter(WEBHOOK_DELIVERY_SUCCESS_TOTAL) == 1


def test_failed_delivery_emits_failed_audit_and_increments_attempts_only() -> None:
    sink, outbox, _ = _drain(success=False, status_code=503)

    (event,) = sink.events
    assert event["event_type"] == "webhook.delivery.failed"
    assert validate_audit_event(event).passed
    safe = event["payload"]["safe"]
    assert safe["outcome"] == "failed" and safe["status_code"] == 503
    assert safe["attempt_count"] == 1

    # Global aggregates by design: no tenant label on the unauthenticated scrape surface.
    assert get_counter(WEBHOOK_DELIVERY_ATTEMPTS_TOTAL) == 1
    assert get_counter(WEBHOOK_DELIVERY_SUCCESS_TOTAL) == 0
    # Task 5 semantics preserved: the row is rescheduled, not terminal
    (row,) = outbox._rows.values()
    assert row.status == "pending" and row.attempt_count == 1


def test_exhausted_delivery_emits_failed_audit_with_outcome_exhausted() -> None:
    outbox = InMemoryWebhookOutboxRepository()
    _enqueue(outbox)
    for _ in range(MAX_ATTEMPTS - 1):  # 9 prior failed attempts via the public API
        (row,) = outbox.claim_due(tenant_id=_TENANT, now=_T0, limit=1)
        outbox.mark_failed(
            tenant_id=_TENANT,
            attempt_id=row.attempt_id,
            next_attempt_at=_T0,
            last_error="HTTP 503",
            now=_T0,
        )
    sink = _CapturingSink()
    dispatcher = WebhookDispatcher(
        outbox=outbox,
        secret_loader=_loader,
        deliver_fn=_FakeDelivery(success=False, status_code=503),
        audit_sink=sink,
    )
    dispatcher.drain_once(tenant_id=_TENANT, now=_T0, limit=10)

    (event,) = sink.events
    assert event["event_type"] == "webhook.delivery.failed"
    assert validate_audit_event(event).passed
    safe = event["payload"]["safe"]
    assert safe["outcome"] == "exhausted" and safe["attempt_count"] == MAX_ATTEMPTS


def test_audit_payload_is_safe_metadata_only() -> None:
    for success in (True, False):
        sink, _, _ = _drain(success=success, status_code=200 if success else 503)
        (event,) = sink.events
        safe = event["payload"]["safe"]
        assert set(safe.keys()) <= _ALLOWED_SAFE_KEYS  # nothing beyond the allowed metadata
        for value in safe.values():
            assert isinstance(value, str | int | bool)  # scalar-only
            assert len(str(value)) <= 128
        blob = json.dumps(event, sort_keys=True, default=str).lower()
        for forbidden in _FORBIDDEN_SUBSTRINGS:
            assert forbidden.lower() not in blob, f"{forbidden!r} leaked into the audit event"


def test_connection_error_without_status_code_omits_it() -> None:
    sink, _, _ = _drain(success=False, status_code=None)  # e.g. timeout / connection error
    (event,) = sink.events
    assert validate_audit_event(event).passed
    assert "status_code" not in event["payload"]["safe"]  # omitted entirely, not stored as None


def test_counters_render_in_prometheus_exposition_format() -> None:
    _drain(success=True, status_code=200)
    text = render_prometheus_text()
    assert "webhook_delivery_attempts_total 1" in text
    assert "webhook_delivery_success_total 1" in text
    assert _TENANT not in text, "no tenant UUID may reach the scrape surface"


def test_audit_and_metrics_failures_do_not_break_dispatch() -> None:
    class _BoomSink:
        def emit(self, event: dict[str, Any]) -> None:
            raise RuntimeError("audit sink down")

    outbox = InMemoryWebhookOutboxRepository()
    _enqueue(outbox)
    dispatcher = WebhookDispatcher(
        outbox=outbox,
        secret_loader=_loader,
        deliver_fn=_FakeDelivery(success=True),
        audit_sink=_BoomSink(),
    )
    summary = dispatcher.drain_once(tenant_id=_TENANT, now=_T0, limit=10)  # must not raise
    assert summary["succeeded"] == 1  # Task 5 semantics intact despite the broken sink
    (row,) = outbox._rows.values()
    assert row.status == "succeeded"
