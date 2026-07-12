"""Slice97 Task 5 — webhook dispatcher / drainer: claim -> sign -> deliver -> retry.

RED-first. ``WebhookDispatcher.drain_once`` claims due pending outbox rows, loads the webhook
secret ONLY at dispatch time via the dedicated ``load_webhook_dispatch_target`` path, signs the
exact delivered body with ``sign_webhook_payload``, delivers via the (injectable) delivery
function, and applies the Task 2 outbox transitions using the retry policy: 2xx -> succeeded,
failure -> ``next_attempt_at`` reschedule, exhausted attempts -> exhausted. The secret must never
appear in logs or delivered payloads. Postgres RLS / SKIP LOCKED behavior is proven env-gated in
``test_slice97_webhook_dispatcher_postgres.py``. PYTHONPATH pinned to this worktree's src.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from idis.persistence.repositories.webhook_outbox import InMemoryWebhookOutboxRepository
from idis.services.webhooks.dispatcher import WebhookDispatcher, WebhookDispatcherWorker
from idis.services.webhooks.retry import MAX_ATTEMPTS
from idis.services.webhooks.signing import verify_webhook_signature

_TENANT = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_WEBHOOK = "wwwwwwww-wwww-wwww-wwww-wwwwwwwwwwww"
_URL = "https://example.test/hook"
_SECRET = "s97-dispatch-secret-XYZ"
_T0 = datetime(2026, 7, 10, 0, 0, 0, tzinfo=UTC)


class _FakeDelivery:
    """Records every delivery call; returns a configurable DeliveryResult-like outcome."""

    def __init__(self, *, success: bool = True, status_code: int = 200) -> None:
        from idis.services.webhooks.delivery import DeliveryResult

        self._result = DeliveryResult(
            success=success,
            status_code=status_code,
            error=None if success else f"HTTP {status_code}",
            attempt_id="",
            duration_ms=1,
        )
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        webhook_id: str,
        attempt_id: str,
        timeout_seconds: int = 30,
    ) -> Any:
        self.calls.append(
            {
                "url": url,
                "payload": payload,
                "headers": dict(headers),
                "webhook_id": webhook_id,
                "attempt_id": attempt_id,
            }
        )
        return self._result


class _RecordingLoader:
    """Dedicated secret-load path stand-in: records when the secret is actually loaded."""

    def __init__(self, secret: str | None = _SECRET) -> None:
        self._secret = secret
        self.calls: list[str] = []

    def __call__(self, conn: Any, webhook_id: str) -> tuple[str, str | None] | None:
        self.calls.append(webhook_id)
        return (_URL, self._secret)


def _enqueue(
    outbox: InMemoryWebhookOutboxRepository,
    *,
    event_id: str | None = None,
    now: datetime = _T0,
) -> str:
    event_id = event_id or str(uuid.uuid4())
    outbox.enqueue(
        webhook_id=_WEBHOOK,
        tenant_id=_TENANT,
        event_id=event_id,
        event_type="run.completed",
        payload={"event_type": "run.completed", "data": {"status": "COMPLETED"}},
        now=now,
    )
    return event_id


def _dispatcher(
    outbox: InMemoryWebhookOutboxRepository,
    delivery: _FakeDelivery,
    loader: _RecordingLoader | None = None,
) -> tuple[WebhookDispatcher, _RecordingLoader]:
    loader = loader or _RecordingLoader()
    return (
        WebhookDispatcher(outbox=outbox, secret_loader=loader, deliver_fn=delivery),
        loader,
    )


def test_drain_once_claims_and_delivers_only_due_pending() -> None:
    outbox = InMemoryWebhookOutboxRepository()
    due_1 = _enqueue(outbox, now=_T0)
    due_2 = _enqueue(outbox, now=_T0 + timedelta(seconds=1))
    _future = _enqueue(outbox, now=_T0 + timedelta(hours=1))  # not yet due
    delivery = _FakeDelivery(success=True)
    dispatcher, _ = _dispatcher(outbox, delivery)

    summary = dispatcher.drain_once(tenant_id=_TENANT, now=_T0 + timedelta(seconds=1), limit=10)

    assert summary["claimed"] == 2 and summary["succeeded"] == 2
    delivered_events = {json.dumps(c["payload"], sort_keys=True) for c in delivery.calls}
    assert len(delivered_events) <= 2 and len(delivery.calls) == 2
    # the future row is still pending and claimable later
    later = outbox.claim_due(tenant_id=_TENANT, now=_T0 + timedelta(hours=2), limit=10)
    assert [r.event_id for r in later] == [_future]
    assert due_1 != due_2  # sanity


def test_secret_loaded_only_at_dispatch_time_via_dedicated_loader() -> None:
    outbox = InMemoryWebhookOutboxRepository()
    _enqueue(outbox)  # enqueue must involve NO secret loading
    delivery = _FakeDelivery(success=True)
    dispatcher, loader = _dispatcher(outbox, delivery)
    assert loader.calls == []  # nothing loaded at enqueue time

    dispatcher.drain_once(tenant_id=_TENANT, now=_T0, limit=10)
    assert loader.calls == [_WEBHOOK]  # loaded exactly once, at dispatch time


def test_delivery_is_signed_and_signature_verifies() -> None:
    outbox = InMemoryWebhookOutboxRepository()
    _enqueue(outbox)
    delivery = _FakeDelivery(success=True)
    dispatcher, _ = _dispatcher(outbox, delivery)

    dispatcher.drain_once(tenant_id=_TENANT, now=_T0, limit=10)

    (call,) = delivery.calls
    headers = call["headers"]
    assert "X-IDIS-Webhook-Timestamp" in headers and "X-IDIS-Webhook-Signature" in headers
    # verify over the EXACT bytes the delivery layer sends: json.dumps(payload)
    body = json.dumps(call["payload"]).encode("utf-8")
    timestamp = int(headers["X-IDIS-Webhook-Timestamp"])
    assert verify_webhook_signature(_SECRET, timestamp, body, headers["X-IDIS-Webhook-Signature"])
    assert not verify_webhook_signature(
        "wrong-secret", timestamp, body, headers["X-IDIS-Webhook-Signature"]
    )


def test_2xx_delivery_marks_row_succeeded() -> None:
    outbox = InMemoryWebhookOutboxRepository()
    _enqueue(outbox)
    dispatcher, _ = _dispatcher(outbox, _FakeDelivery(success=True, status_code=200))

    summary = dispatcher.drain_once(tenant_id=_TENANT, now=_T0, limit=10)

    assert summary == {"claimed": 1, "succeeded": 1, "failed": 0, "exhausted": 0}
    (row,) = outbox._rows.values()
    assert row.status == "succeeded" and row.attempt_count == 1
    assert outbox.claim_due(tenant_id=_TENANT, now=_T0 + timedelta(days=9), limit=10) == []


def test_failed_delivery_schedules_retry_via_retry_policy() -> None:
    outbox = InMemoryWebhookOutboxRepository()
    _enqueue(outbox)
    dispatcher, _ = _dispatcher(outbox, _FakeDelivery(success=False, status_code=503))

    summary = dispatcher.drain_once(tenant_id=_TENANT, now=_T0, limit=10)

    assert summary == {"claimed": 1, "succeeded": 0, "failed": 1, "exhausted": 0}
    (row,) = outbox._rows.values()
    assert row.status == "pending" and row.attempt_count == 1
    assert row.last_error == "HTTP 503"
    assert row.next_attempt_at == _T0 + timedelta(seconds=60)  # first backoff step, jitter-free


def test_exhausted_retry_count_marks_exhausted() -> None:
    outbox = InMemoryWebhookOutboxRepository()
    _enqueue(outbox)
    # 9 prior failed attempts via the public transition API
    for _ in range(MAX_ATTEMPTS - 1):
        (row,) = outbox.claim_due(tenant_id=_TENANT, now=_T0, limit=1)
        outbox.mark_failed(
            tenant_id=_TENANT,
            attempt_id=row.attempt_id,
            next_attempt_at=_T0,
            last_error="HTTP 503",
            now=_T0,
        )
    dispatcher, _ = _dispatcher(outbox, _FakeDelivery(success=False, status_code=503))

    summary = dispatcher.drain_once(tenant_id=_TENANT, now=_T0, limit=10)  # attempt #10

    assert summary == {"claimed": 1, "succeeded": 0, "failed": 0, "exhausted": 1}
    (row,) = outbox._rows.values()
    assert row.status == "exhausted" and row.attempt_count == MAX_ATTEMPTS
    assert outbox.claim_due(tenant_id=_TENANT, now=_T0 + timedelta(days=9), limit=10) == []


def test_secret_never_appears_in_logs_or_delivered_payload(
    caplog: pytest.LogCaptureFixture,
) -> None:
    outbox = InMemoryWebhookOutboxRepository()
    _enqueue(outbox)
    delivery = _FakeDelivery(success=False, status_code=500)  # failure path logs the most
    dispatcher, _ = _dispatcher(outbox, delivery)

    with caplog.at_level("DEBUG"):
        dispatcher.drain_once(tenant_id=_TENANT, now=_T0, limit=10)

    assert _SECRET not in caplog.text  # never logged
    (call,) = delivery.calls
    assert _SECRET not in json.dumps(call["payload"])  # never in the delivered body
    assert all(_SECRET not in value for value in call["headers"].values())  # only the HMAC
    (row,) = outbox._rows.values()
    assert _SECRET not in json.dumps(row.payload)  # never persisted to the outbox
    assert _SECRET not in (row.last_error or "")


def test_dispatcher_worker_drains_each_worker_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    from idis.services.webhooks import dispatcher as dispatcher_mod

    drained: list[str] = []

    class _FakeDispatcher:
        def drain_once(self, *, tenant_id: str, **_: Any) -> dict[str, int]:
            drained.append(tenant_id)
            return {"claimed": 0, "succeeded": 0, "failed": 0, "exhausted": 0}

    monkeypatch.setattr(dispatcher_mod, "get_worker_tenant_ids", lambda: ["tenant-1", "tenant-2"])
    worker = WebhookDispatcherWorker(dispatcher=_FakeDispatcher())
    worker._drain_all_tenants()
    assert drained == ["tenant-1", "tenant-2"]


def test_slow_drain_does_not_block_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    # F1 remediation: the worker must run its blocking drain (DB + up-to-30s HTTP deliveries) OFF
    # the asyncio loop (asyncio.to_thread, mirroring the pipeline worker). A slow subscriber must
    # not freeze the API's event loop. RED before the fix: the sync drain runs ON the loop, so the
    # ticking coroutine starves while the drain sleeps. Written as a sync test driving the loop
    # via asyncio.run() so it needs no async pytest plugin (portable to the project's own deps).
    import time

    from idis.services.webhooks import dispatcher as dispatcher_mod

    monkeypatch.setattr(dispatcher_mod, "get_worker_tenant_ids", lambda: ["tenant-1"])

    class _SlowDispatcher:
        def drain_once(self, *, tenant_id: str, **_: Any) -> dict[str, int]:
            time.sleep(0.6)  # blocking delivery work (tarpit subscriber stand-in)
            return {"claimed": 0, "succeeded": 0, "failed": 0, "exhausted": 0}

    async def _scenario() -> int:
        worker = WebhookDispatcherWorker(poll_interval=999, dispatcher=_SlowDispatcher())
        await worker.start()
        try:
            ticks = 0
            start = time.monotonic()
            while time.monotonic() - start < 0.5:  # overlaps the first (blocking) drain
                await asyncio.sleep(0.02)
                ticks += 1
            return ticks
        finally:
            await worker.stop()

    ticks = asyncio.run(_scenario())
    # With the drain off-loop the ticker runs freely (~25 ticks); ON-loop it starves (~0-2).
    assert ticks >= 10, f"event loop starved during drain (ticks={ticks})"


def test_worker_swallows_drain_errors_and_keeps_polling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # F1 guard: moving the drain off-loop must preserve the swallowed-error poll behavior.
    # Sync test driving the loop via asyncio.run() (no async pytest plugin required).
    from idis.services.webhooks import dispatcher as dispatcher_mod

    monkeypatch.setattr(dispatcher_mod, "get_worker_tenant_ids", lambda: ["tenant-1"])
    calls: list[int] = []

    class _BoomDispatcher:
        def drain_once(self, *, tenant_id: str, **_: Any) -> dict[str, int]:
            calls.append(1)
            raise RuntimeError("drain boom")

    async def _scenario() -> None:
        worker = WebhookDispatcherWorker(poll_interval=0, dispatcher=_BoomDispatcher())
        await worker.start()
        try:
            for _ in range(50):
                await asyncio.sleep(0.01)
                if len(calls) >= 2:
                    break
        finally:
            await worker.stop()

    asyncio.run(_scenario())
    assert len(calls) >= 2  # errors swallowed; the loop kept polling


def test_app_startup_wires_dispatcher_worker() -> None:
    # Wiring pin: the app startup must start the webhook dispatcher worker alongside the pipeline
    # worker when Postgres is configured (behavior itself is unit-proven above).
    import inspect

    from idis.api import main as main_mod

    source = inspect.getsource(main_mod)
    assert "start_webhook_dispatcher_worker" in source
    assert "stop_webhook_dispatcher_worker" in source
