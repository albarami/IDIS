"""Slice97 Task 3 — best-effort lifecycle-event webhook emitter (fan-out).

RED-first. ``emit_lifecycle_event`` lists the tenant's active webhook subscriptions whose ``events``
contain the event type, builds the Task 1 safe envelope, and enqueues one Task 2 outbox row per
matching subscription. It is BEST-EFFORT: any failure (listing, payload build, enqueue) is logged
and swallowed so emitting a webhook can NEVER raise into, or roll back, the audited mutation/run it
runs alongside (acceptance A1). PYTHONPATH pinned to this worktree's src.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from idis.persistence.repositories.webhook_outbox import InMemoryWebhookOutboxRepository
from idis.services.webhooks.emitter import emit_lifecycle_event
from idis.services.webhooks.service import WebhookSubscription

_TENANT_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_TENANT_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
_RUN = "11111111-1111-1111-1111-111111111111"
_T0 = datetime(2026, 7, 10, 0, 0, 0, tzinfo=UTC)


def _sub(
    webhook_id: str, events: list[str], *, tenant: str = _TENANT_A, active: bool = True
) -> WebhookSubscription:
    return WebhookSubscription(
        webhook_id=webhook_id,
        tenant_id=tenant,
        url="https://example.test/hook",
        events=list(events),
        active=active,
        created_at="2026-07-10T00:00:00Z",
        updated_at="2026-07-10T00:00:00Z",
    )


class _FakeWebhookService:
    """Stands in for WebhookService.list_webhooks(conn, active_only) in unit tests."""

    def __init__(self, subscriptions: list[WebhookSubscription], *, raises: bool = False) -> None:
        self._subscriptions = subscriptions
        self._raises = raises
        self.calls: list[tuple[Any, bool]] = []

    def list_webhooks(self, conn: Any, active_only: bool = False) -> list[WebhookSubscription]:
        self.calls.append((conn, active_only))
        if self._raises:
            raise RuntimeError("webhook service unavailable")
        return list(self._subscriptions)


def _emit(
    service: Any,
    outbox: Any,
    *,
    event_type: str = "run.completed",
    data: dict[str, Any] | None = None,
) -> None:
    emit_lifecycle_event(
        tenant_id=_TENANT_A,
        event_type=event_type,
        resource_type="run",
        resource_id=_RUN,
        data=data if data is not None else {"status": "COMPLETED"},
        webhook_service=service,
        outbox=outbox,
        now=_T0,
    )


def test_matching_subscription_enqueues_one_safe_payload() -> None:
    outbox = InMemoryWebhookOutboxRepository()
    service = _FakeWebhookService([_sub("w1", ["run.completed"])])
    # data carries a sensitive field that the Task 1 safe builder must strip.
    _emit(service, outbox, data={"status": "COMPLETED", "local_path": "/var/data/x.pdf"})

    rows = outbox.claim_due(tenant_id=_TENANT_A, now=_T0, limit=10)
    assert len(rows) == 1
    row = rows[0]
    assert row.webhook_id == "w1"
    assert row.event_type == "run.completed"
    payload = row.payload
    assert payload["event_type"] == "run.completed"
    assert payload["tenant_id"] == _TENANT_A
    assert payload["resource_type"] == "run" and payload["resource_id"] == _RUN
    assert payload["data"]["status"] == "COMPLETED"
    assert "local_path" not in payload["data"]  # sanitized via build_webhook_event


def test_non_matching_event_enqueues_nothing() -> None:
    outbox = InMemoryWebhookOutboxRepository()
    service = _FakeWebhookService([_sub("w1", ["run.failed"])])  # subscribes to a different event
    _emit(service, outbox, event_type="run.completed")
    assert outbox.claim_due(tenant_id=_TENANT_A, now=_T0, limit=10) == []


def test_matching_is_tenant_scoped() -> None:
    outbox = InMemoryWebhookOutboxRepository()
    service = _FakeWebhookService([_sub("w1", ["run.completed"], tenant=_TENANT_A)])
    _emit(service, outbox)
    rows_a = outbox.claim_due(tenant_id=_TENANT_A, now=_T0, limit=10)
    assert len(rows_a) == 1 and rows_a[0].tenant_id == _TENANT_A
    assert (
        outbox.claim_due(tenant_id=_TENANT_B, now=_T0, limit=10) == []
    )  # nothing for another tenant


def test_zero_subscriptions_is_a_noop() -> None:
    outbox = InMemoryWebhookOutboxRepository()
    service = _FakeWebhookService([])
    _emit(service, outbox)  # must not raise
    assert outbox.claim_due(tenant_id=_TENANT_A, now=_T0, limit=10) == []


def test_raising_webhook_service_does_not_raise() -> None:
    outbox = InMemoryWebhookOutboxRepository()
    service = _FakeWebhookService([], raises=True)
    _emit(service, outbox)  # best-effort: swallows the listing error, does NOT raise
    assert outbox.claim_due(tenant_id=_TENANT_A, now=_T0, limit=10) == []


def test_raising_outbox_does_not_raise() -> None:
    class _RaisingOutbox:
        def enqueue(self, **_: Any) -> bool:
            raise RuntimeError("outbox store unavailable")

    service = _FakeWebhookService([_sub("w1", ["run.completed"])])
    _emit(service, _RaisingOutbox())  # best-effort: swallows the enqueue error, does NOT raise


def test_cross_tenant_subscription_is_not_enqueued() -> None:
    # Defense-in-depth beyond RLS: a returned subscription whose tenant_id differs from the emitted
    # tenant must NOT be enqueued (never deliver tenant A's event to tenant B's webhook, nor stamp a
    # tenant-B webhook onto a tenant-A outbox row).
    outbox = InMemoryWebhookOutboxRepository()
    service = _FakeWebhookService([_sub("w1", ["run.completed"], tenant=_TENANT_B)])
    _emit(service, outbox)  # emits for tenant A
    assert outbox.claim_due(tenant_id=_TENANT_A, now=_T0, limit=10) == []
    assert outbox.claim_due(tenant_id=_TENANT_B, now=_T0, limit=10) == []


def test_list_webhooks_called_active_only_with_conn_forwarded() -> None:
    outbox = InMemoryWebhookOutboxRepository()
    service = _FakeWebhookService([_sub("w1", ["run.completed"])])
    sentinel_conn = object()
    emit_lifecycle_event(
        tenant_id=_TENANT_A,
        event_type="run.completed",
        resource_type="run",
        resource_id=_RUN,
        data={"status": "COMPLETED"},
        webhook_service=service,
        outbox=outbox,
        conn=sentinel_conn,
        now=_T0,
    )
    assert service.calls == [(sentinel_conn, True)]  # active_only=True, provided conn forwarded
