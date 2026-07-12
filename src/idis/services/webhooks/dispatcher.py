"""Slice97 Task 5 — webhook dispatcher / drainer: claim -> sign -> deliver -> retry.

``WebhookDispatcher.drain_once`` claims due pending rows from the durable outbox (Task 2), loads
the webhook's (url, secret) ONLY at dispatch time via ``load_webhook_dispatch_target`` (the one
place the stored secret is read; RLS-scoped, never logged), signs the exact bytes the delivery
layer will send (``json.dumps(payload)``) with ``sign_webhook_payload``, delivers via
``deliver_webhook_sync`` (injectable), and applies the retry policy (``retry.py``): 2xx ->
``mark_succeeded``; failure -> ``next_attempt_at`` reschedule via ``mark_failed``; attempts
exhausted -> ``mark_exhausted``. On Postgres the whole drain runs on one tenant-scoped connection,
so the ``FOR UPDATE SKIP LOCKED`` claim lock is held across claim -> deliver -> mark and concurrent
drainers can never double-deliver a row.

``WebhookDispatcherWorker`` mirrors the pipeline worker: an asyncio poll loop over the configured
worker tenants (``get_worker_tenant_ids`` — fail-safe: empty means no global scan), errors
swallowed so the loop survives. It is started with the app when Postgres is configured.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from idis.observability.metrics import (
    WEBHOOK_DELIVERY_ATTEMPTS_TOTAL,
    WEBHOOK_DELIVERY_SUCCESS_TOTAL,
    increment_counter,
)
from idis.persistence.repositories.webhook_outbox import (
    WebhookOutboxRepository,
    default_webhook_outbox,
)
from idis.pipeline.worker import get_worker_tenant_ids
from idis.services.webhooks.delivery import deliver_webhook_sync
from idis.services.webhooks.retry import next_attempt_at
from idis.services.webhooks.signing import sign_webhook_payload
from idis.validators.audit_event_validator import validate_audit_event

if TYPE_CHECKING:
    from sqlalchemy import Connection

    from idis.audit.sink import AuditSink

logger = logging.getLogger(__name__)

WEBHOOK_DELIVERY_SUCCEEDED = "webhook.delivery.succeeded"
WEBHOOK_DELIVERY_FAILED = "webhook.delivery.failed"

_TARGET_SQL = text(
    """
    SELECT url, secret, active
    FROM webhooks
    WHERE webhook_id = :webhook_id
    """
)


def load_webhook_dispatch_target(
    conn: Connection, webhook_id: str
) -> tuple[str, str | None] | None:
    """Dispatch-time ONLY secret-load path: return (url, secret) for an active webhook.

    This is the single place the stored webhook secret is read. The connection must be
    tenant-scoped (RLS hides other tenants' webhooks). The secret is returned to the caller for
    in-memory signing and must never be logged, persisted, or included in any payload. Returns
    ``None`` when the webhook is missing (e.g. deleted) or inactive.
    """
    row = conn.execute(_TARGET_SQL, {"webhook_id": webhook_id}).fetchone()
    if row is None or not row.active:
        return None
    return (row.url, row.secret)


class WebhookDispatcher:
    """Drains the durable webhook outbox: claim -> sign -> deliver -> mark with retry policy."""

    def __init__(
        self,
        *,
        outbox: WebhookOutboxRepository | None = None,
        secret_loader: Callable[[Any, str], tuple[str, str | None] | None] | None = None,
        deliver_fn: Callable[..., Any] | None = None,
        audit_sink: AuditSink | None = None,
    ) -> None:
        self._outbox = outbox if outbox is not None else default_webhook_outbox()
        # Typed as Any-conn: unit tests inject loaders taking conn=None; the default (real) loader
        # requires a tenant-scoped Connection, which the Postgres drain path always supplies.
        self._secret_loader: Callable[[Any, str], tuple[str, str | None] | None] = (
            secret_loader or load_webhook_dispatch_target
        )
        self._deliver_fn = deliver_fn or deliver_webhook_sync
        self._audit_sink = audit_sink

    def _resolve_audit_sink(self) -> AuditSink:
        """Lazy default: durable Postgres sink when configured, else the JSONL default sink."""
        if self._audit_sink is None:
            from idis.persistence.db import is_postgres_configured

            if is_postgres_configured():
                from idis.audit.postgres_sink import PostgresAuditSink

                self._audit_sink = PostgresAuditSink()
            else:
                from idis.audit.sink import get_audit_sink

                self._audit_sink = get_audit_sink()
        return self._audit_sink

    def drain_once(
        self,
        *,
        tenant_id: str,
        now: datetime | None = None,
        limit: int = 10,
        conn: Connection | None = None,
    ) -> dict[str, int]:
        """Claim and dispatch this tenant's due pending deliveries once; return outcome counts."""
        stamp = now or datetime.now(UTC)
        if conn is None:
            from idis.persistence.db import begin_app_conn, is_postgres_configured, set_tenant_local

            if is_postgres_configured():
                with begin_app_conn() as own_conn:
                    set_tenant_local(own_conn, tenant_id)
                    return self._drain_with_conn(tenant_id, stamp, limit, own_conn)
        return self._drain_with_conn(tenant_id, stamp, limit, conn)

    def _drain_with_conn(
        self, tenant_id: str, now: datetime, limit: int, conn: Connection | None
    ) -> dict[str, int]:
        summary = {"claimed": 0, "succeeded": 0, "failed": 0, "exhausted": 0}
        rows = self._outbox.claim_due(tenant_id=tenant_id, now=now, limit=limit, conn=conn)
        summary["claimed"] = len(rows)
        for row in rows:
            self._dispatch_row(row, tenant_id, now, conn, summary)
        return summary

    def _dispatch_row(
        self,
        row: Any,
        tenant_id: str,
        now: datetime,
        conn: Connection | None,
        summary: dict[str, int],
    ) -> None:
        attempts_made = row.attempt_count + 1  # this attempt included
        target = self._secret_loader(conn, row.webhook_id)
        if target is None:
            # Missing (deleted) or inactive webhook: no delivery destination -> terminal.
            self._outbox.mark_exhausted(
                tenant_id=tenant_id,
                attempt_id=row.attempt_id,
                last_error="WEBHOOK_UNAVAILABLE",
                now=now,
                conn=conn,
            )
            summary["exhausted"] += 1
            self._record_outcome(row, tenant_id, now, "exhausted", None, attempts_made)
            return

        url, secret = target
        headers: dict[str, str] = {}
        if secret:
            # Sign the EXACT bytes the delivery layer sends (json.dumps(payload)).
            body = json.dumps(row.payload).encode("utf-8")
            signature = sign_webhook_payload(secret, int(now.timestamp()), body)
            headers = dict(signature.headers)

        try:
            result = self._deliver_fn(
                url=url,
                payload=row.payload,
                headers=headers,
                webhook_id=row.webhook_id,
                attempt_id=row.attempt_id,
            )
            success = bool(result.success)
            error = result.error
            status_code = result.status_code
        except Exception as exc:  # a broken delivery fn must not kill the drain loop
            success = False
            error = f"Delivery error: {type(exc).__name__}"
            status_code = None

        if success:
            self._outbox.mark_succeeded(
                tenant_id=tenant_id, attempt_id=row.attempt_id, now=now, conn=conn
            )
            summary["succeeded"] += 1
            self._record_outcome(row, tenant_id, now, "succeeded", status_code, attempts_made)
            return

        retry_at = next_attempt_at(now, attempts_made)
        last_error = (error or "delivery_failed")[:500]
        if retry_at is None:
            self._outbox.mark_exhausted(
                tenant_id=tenant_id,
                attempt_id=row.attempt_id,
                last_error=last_error,
                now=now,
                conn=conn,
            )
            summary["exhausted"] += 1
            self._record_outcome(row, tenant_id, now, "exhausted", status_code, attempts_made)
        else:
            self._outbox.mark_failed(
                tenant_id=tenant_id,
                attempt_id=row.attempt_id,
                next_attempt_at=retry_at,
                last_error=last_error,
                now=now,
                conn=conn,
            )
            summary["failed"] += 1
            self._record_outcome(row, tenant_id, now, "failed", status_code, attempts_made)

    def _record_outcome(
        self,
        row: Any,
        tenant_id: str,
        now: datetime,
        outcome: str,
        status_code: int | None,
        attempts_made: int,
    ) -> None:
        """Best-effort delivery audit + metrics (Task 6): safe metadata only, never breaks dispatch.

        The audit payload carries ONLY webhook_id / event_id / event_type / attempt_count /
        status_code / outcome — never the url, secret, body, headers, or any request/response
        content. Validation failures or sink errors are logged and swallowed; Task 5 dispatch
        semantics are unchanged.
        """
        try:
            labels = {"tenant_id": tenant_id}
            increment_counter(WEBHOOK_DELIVERY_ATTEMPTS_TOTAL, labels=labels)
            if outcome == "succeeded":
                increment_counter(WEBHOOK_DELIVERY_SUCCESS_TOTAL, labels=labels)

            safe: dict[str, Any] = {
                "webhook_id": row.webhook_id,
                "event_id": row.event_id,
                "event_type": row.event_type,
                "attempt_count": attempts_made,
                "outcome": outcome,
            }
            if status_code is not None:
                safe["status_code"] = int(status_code)
            audit_event_type = (
                WEBHOOK_DELIVERY_SUCCEEDED if outcome == "succeeded" else WEBHOOK_DELIVERY_FAILED
            )
            event: dict[str, Any] = {
                "event_id": str(uuid.uuid4()),
                "occurred_at": now.isoformat().replace("+00:00", "Z"),
                "tenant_id": tenant_id,
                "actor": {"actor_type": "SERVICE", "actor_id": "webhook-dispatcher"},
                "request": {
                    "request_id": row.attempt_id,
                    "method": "POST",
                    "path": f"/v1/webhooks/{row.webhook_id}",
                    "status_code": 200 if outcome == "succeeded" else 502,
                },
                "resource": {"resource_type": "webhook", "resource_id": row.webhook_id},
                "event_type": audit_event_type,
                "severity": "LOW" if outcome == "succeeded" else "MEDIUM",
                "summary": f"webhook delivery {outcome} for webhook {row.webhook_id}",
                "payload": {"safe": safe, "hashes": [], "refs": []},
            }
            validation = validate_audit_event(event)
            if not validation.passed:
                logger.warning(
                    "webhook delivery audit event failed validation, skipping emit: %s",
                    [error.code for error in validation.errors],
                )
                return
            self._resolve_audit_sink().emit(event)
        except Exception:  # best-effort: audit/metrics must never break dispatch
            logger.warning("webhook delivery audit emit failed", exc_info=True)


class WebhookDispatcherWorker:
    """Asyncio polling drainer mirroring the pipeline worker (errors swallowed, tenant-scoped)."""

    def __init__(
        self, *, poll_interval: int = 5, dispatcher: WebhookDispatcher | None = None
    ) -> None:
        self._poll_interval = poll_interval
        self._dispatcher = dispatcher or WebhookDispatcher()
        self._task: asyncio.Task[None] | None = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("Webhook dispatcher worker started (poll_interval=%ss)", self._poll_interval)

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        logger.info("Webhook dispatcher worker stopped")

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                # Off-loop, like the pipeline worker: the drain does blocking DB I/O and outbound
                # HTTP deliveries (up to 30s per attempt) — running it on the event loop would
                # freeze the whole API whenever a subscriber endpoint is slow.
                await asyncio.to_thread(self._drain_all_tenants)
            except Exception:  # the poll loop must survive any drain failure
                logger.exception("Webhook dispatcher poll iteration failed")
            await asyncio.sleep(self._poll_interval)

    def _drain_all_tenants(self) -> None:
        # Fail-safe scoping (mirrors the pipeline worker): no tenants configured -> no global scan.
        for tenant_id in get_worker_tenant_ids():
            try:
                self._dispatcher.drain_once(tenant_id=tenant_id)
            except Exception:  # one tenant's failure must not starve the others
                logger.warning("Webhook drain failed for a tenant", exc_info=True)


_dispatcher_worker: WebhookDispatcherWorker | None = None


async def start_webhook_dispatcher_worker() -> None:
    """Start the process-wide webhook dispatcher worker (app startup, Postgres-configured)."""
    global _dispatcher_worker
    if _dispatcher_worker is None:
        _dispatcher_worker = WebhookDispatcherWorker(poll_interval=5)
    await _dispatcher_worker.start()


async def stop_webhook_dispatcher_worker() -> None:
    """Stop the process-wide webhook dispatcher worker (app shutdown)."""
    global _dispatcher_worker
    if _dispatcher_worker is not None:
        await _dispatcher_worker.stop()
        _dispatcher_worker = None
