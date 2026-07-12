"""Slice97 Task 3 — best-effort lifecycle-event webhook emitter (fan-out).

``emit_lifecycle_event`` lists the tenant's active webhook subscriptions matching the event type,
builds the safe event envelope (``build_webhook_event``, Task 1), and enqueues one durable outbox
row per matching subscription (Task 2). All matching subscriptions share the built event's
``event_id``, so the ``(webhook_id, event_id)`` outbox unique index makes re-emission idempotent.

It is BEST-EFFORT by contract: the entire body is wrapped so any failure — listing, payload build
(including a ``WebhookPayloadError`` on unsafe data), or enqueue — is logged and swallowed.
Emitting a lifecycle webhook must NEVER raise into, or roll back, the audited mutation/run it is
emitted alongside (acceptance A1). This mirrors ``emit_run_signal``'s never-break-the-operation
discipline.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

from idis.services.webhooks.events import build_webhook_event

if TYPE_CHECKING:
    from sqlalchemy import Connection

    from idis.persistence.repositories.webhook_outbox import WebhookOutboxRepository

logger = logging.getLogger(__name__)


class _WebhookLister(Protocol):
    """Minimal contract the emitter needs from the webhook service (RLS-scoped by ``conn``)."""

    def list_webhooks(self, conn: Any, active_only: bool = ...) -> list[Any]: ...


def emit_lifecycle_event(
    *,
    tenant_id: str,
    event_type: str,
    resource_type: str,
    resource_id: str,
    data: Mapping[str, Any] | None = None,
    webhook_service: _WebhookLister,
    outbox: WebhookOutboxRepository,
    conn: Connection | None = None,
    now: datetime | None = None,
    event_id: str | None = None,
) -> None:
    """Best-effort fan-out of a lifecycle event to the tenant's matching webhook subscriptions.

    Never raises: any error is logged and swallowed so the audited operation is unaffected (A1).
    On a real caller connection all webhook work (listing AND enqueue) runs inside a SAVEPOINT:
    a SQL failure rolls back to the savepoint — clearing the aborted state a swallow alone cannot
    clear — and the enqueue commits/rolls back WITH the caller's transaction (no ghost events).
    Without a caller connection the outbox enqueue is standalone best-effort (at-least-once).
    """

    def _fan_out() -> None:
        subscriptions = webhook_service.list_webhooks(conn, active_only=True)
        # Match on event type AND tenant: defense-in-depth beyond RLS, so a subscription whose
        # tenant_id differs from the emitted tenant is never enqueued (no cross-tenant delivery).
        matching = [
            s
            for s in subscriptions
            if event_type in getattr(s, "events", ()) and getattr(s, "tenant_id", None) == tenant_id
        ]
        if not matching:
            return

        event = build_webhook_event(
            event_type=event_type,
            tenant_id=tenant_id,
            resource_type=resource_type,
            resource_id=resource_id,
            data=dict(data or {}),
            event_id=event_id,
        )
        payload = event.model_dump()
        stamp = now or datetime.now(UTC)
        for subscription in matching:
            outbox.enqueue(
                webhook_id=subscription.webhook_id,
                tenant_id=tenant_id,
                event_id=event.event_id,
                event_type=event_type,
                payload=payload,
                now=stamp,
                conn=conn,  # transactional with the caller when a conn is supplied
            )

    try:
        begin_nested = getattr(conn, "begin_nested", None) if conn is not None else None
        if callable(begin_nested):
            # SAVEPOINT: a failure inside escapes this block (rolling back to the savepoint and
            # un-aborting the caller's transaction) and is then swallowed below (A1).
            with begin_nested():
                _fan_out()
        else:
            _fan_out()
    except Exception as exc:  # best-effort: a webhook must never break the audited operation
        logger.warning(
            "webhook lifecycle emit failed (event_type=%s resource_id=%s): %s",
            event_type,
            resource_id,
            exc,
        )
