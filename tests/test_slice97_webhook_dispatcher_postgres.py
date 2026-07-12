"""Slice97 Task 5 (durable) — Postgres dispatcher: secret load under RLS, no double-delivery.

Env-gated: skips locally without IDIS_DATABASE_URL / IDIS_DATABASE_ADMIN_URL; with
IDIS_REQUIRE_POSTGRES=1 (CI) it fails instead of skipping. Proves what the unit twin cannot: the
dedicated dispatch-target loader reads (url, secret) under RLS only; a real drain marks the DB row
succeeded with a verifiable signature; and two overlapping drainers never deliver the same row
(``FOR UPDATE SKIP LOCKED`` held across claim -> deliver -> mark). PYTHONPATH pinned to src.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import text

from idis.persistence.repositories.webhook_outbox import PostgresWebhookOutboxRepository
from idis.services.webhooks.dispatcher import WebhookDispatcher, load_webhook_dispatch_target
from idis.services.webhooks.signing import verify_webhook_signature

ADMIN_URL_ENV = "IDIS_DATABASE_ADMIN_URL"
APP_URL_ENV = "IDIS_DATABASE_URL"
REQUIRE_POSTGRES_ENV = "IDIS_REQUIRE_POSTGRES"

_T0 = datetime(2026, 7, 10, 0, 0, 0, tzinfo=UTC)
_URL = "https://example.test/hook"
_SECRET = "s97-pg-dispatch-secret"


def _skip_or_fail_if_no_postgres() -> None:
    admin_url = os.environ.get(ADMIN_URL_ENV)
    app_url = os.environ.get(APP_URL_ENV)
    require = os.environ.get(REQUIRE_POSTGRES_ENV, "0") == "1"
    if not admin_url or not app_url:
        msg = f"Postgres webhook-dispatcher integration requires {ADMIN_URL_ENV} and {APP_URL_ENV}"
        if require:
            pytest.fail(f"REQUIRED: {msg} (IDIS_REQUIRE_POSTGRES=1)")
        pytest.skip(msg)


@pytest.fixture(scope="module")
def _pg_schema() -> Generator[None, None, None]:
    """Migrate to head (idempotent)."""
    _skip_or_fail_if_no_postgres()

    from alembic import command
    from alembic.config import Config

    import idis.persistence.migrations as migrations_pkg
    from idis.persistence.db import get_admin_engine, reset_engines

    config = Config()
    config.set_main_option("script_location", os.path.dirname(migrations_pkg.__file__))
    with get_admin_engine().begin() as conn:
        config.attributes["connection"] = conn
        command.upgrade(config, "head")
    yield
    reset_engines()


@pytest.fixture
def dispatch_tenant(_pg_schema: None) -> Generator[str, None, None]:
    """A unique tenant per test; truncate webhook tables (admin bypasses RLS) for isolation."""
    from idis.persistence.db import get_admin_engine

    def _truncate() -> None:
        with get_admin_engine().begin() as conn:
            conn.execute(text("TRUNCATE webhook_delivery_attempts, webhooks CASCADE"))

    _truncate()
    yield str(uuid.uuid4())
    _truncate()


def _create_webhook(tenant_id: str, *, secret: str | None = _SECRET, active: bool = True) -> str:
    from idis.persistence.db import begin_app_conn, set_tenant_local

    webhook_id = str(uuid.uuid4())
    with begin_app_conn() as conn:
        set_tenant_local(conn, tenant_id)
        conn.execute(
            text(
                "INSERT INTO webhooks (webhook_id, tenant_id, url, events, secret, active, "
                "created_at, updated_at) VALUES (CAST(:w AS uuid), CAST(:t AS uuid), :u, "
                "ARRAY['run.completed'], :s, :a, now(), now())"
            ),
            {"w": webhook_id, "t": tenant_id, "u": _URL, "s": secret, "a": active},
        )
    return webhook_id


def _enqueue(repo: PostgresWebhookOutboxRepository, *, tenant: str, webhook: str) -> str:
    event_id = str(uuid.uuid4())
    repo.enqueue(
        webhook_id=webhook,
        tenant_id=tenant,
        event_id=event_id,
        event_type="run.completed",
        payload={"event_type": "run.completed", "data": {"status": "COMPLETED"}},
        now=_T0,
    )
    return event_id


class _FakeDelivery:
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
        self.calls.append({"url": url, "payload": payload, "headers": dict(headers)})
        return self._result


def test_dispatch_target_loader_is_rls_scoped_and_skips_inactive(dispatch_tenant: str) -> None:
    from idis.persistence.db import begin_app_conn, set_tenant_local

    active_webhook = _create_webhook(dispatch_tenant)
    inactive_webhook = _create_webhook(dispatch_tenant, active=False)

    with begin_app_conn() as conn:
        set_tenant_local(conn, dispatch_tenant)
        target = load_webhook_dispatch_target(conn, active_webhook)
        assert target == (_URL, _SECRET)  # url + secret, dispatch-time only
        assert load_webhook_dispatch_target(conn, inactive_webhook) is None  # inactive -> skipped

    other_tenant = str(uuid.uuid4())
    with begin_app_conn() as conn:
        set_tenant_local(conn, other_tenant)
        # RLS: another tenant cannot load this webhook's dispatch target (no secret leak).
        assert load_webhook_dispatch_target(conn, active_webhook) is None


def test_drain_once_signs_delivers_and_marks_succeeded_in_db(dispatch_tenant: str) -> None:
    repo = PostgresWebhookOutboxRepository()
    webhook = _create_webhook(dispatch_tenant)
    _enqueue(repo, tenant=dispatch_tenant, webhook=webhook)
    delivery = _FakeDelivery(success=True)
    dispatcher = WebhookDispatcher(outbox=repo, deliver_fn=delivery)  # REAL secret loader

    summary = dispatcher.drain_once(tenant_id=dispatch_tenant, now=datetime.now(UTC), limit=10)

    assert summary["claimed"] == 1 and summary["succeeded"] == 1
    (call,) = delivery.calls
    body = json.dumps(call["payload"]).encode("utf-8")
    timestamp = int(call["headers"]["X-IDIS-Webhook-Timestamp"])
    assert verify_webhook_signature(
        _SECRET, timestamp, body, call["headers"]["X-IDIS-Webhook-Signature"]
    )
    from idis.persistence.db import begin_app_conn, set_tenant_local

    with begin_app_conn() as conn:
        set_tenant_local(conn, dispatch_tenant)
        row = conn.execute(
            text("SELECT status, attempt_count FROM webhook_delivery_attempts")
        ).fetchone()
    assert row is not None and row.status == "succeeded" and row.attempt_count == 1


def test_concurrent_drainers_do_not_double_deliver(dispatch_tenant: str) -> None:
    from idis.persistence.db import begin_app_conn, set_tenant_local

    repo = PostgresWebhookOutboxRepository()
    webhook = _create_webhook(dispatch_tenant)
    for _ in range(2):
        _enqueue(repo, tenant=dispatch_tenant, webhook=webhook)

    delivery_a = _FakeDelivery(success=True)
    delivery_b = _FakeDelivery(success=True)
    dispatcher_a = WebhookDispatcher(outbox=repo, deliver_fn=delivery_a)
    dispatcher_b = WebhookDispatcher(outbox=repo, deliver_fn=delivery_b)

    now = datetime.now(UTC)
    # Two overlapping transactions: the claim lock is held across claim -> deliver -> mark, so the
    # second drainer skips the locked row (FOR UPDATE SKIP LOCKED) and no row is delivered twice.
    with begin_app_conn() as conn_a:
        set_tenant_local(conn_a, dispatch_tenant)
        summary_a = dispatcher_a.drain_once(
            tenant_id=dispatch_tenant, now=now, limit=1, conn=conn_a
        )
        with begin_app_conn() as conn_b:
            set_tenant_local(conn_b, dispatch_tenant)
            summary_b = dispatcher_b.drain_once(
                tenant_id=dispatch_tenant, now=now, limit=1, conn=conn_b
            )
    assert summary_a["claimed"] == 1 and summary_b["claimed"] == 1
    delivered_a = {json.dumps(c["payload"], sort_keys=True) for c in delivery_a.calls}
    delivered_b = {json.dumps(c["payload"], sort_keys=True) for c in delivery_b.calls}
    assert len(delivery_a.calls) == 1 and len(delivery_b.calls) == 1
    # each drainer delivered a DIFFERENT outbox row (attempt ids disjoint via the claim lock)
    with begin_app_conn() as conn:
        set_tenant_local(conn, dispatch_tenant)
        statuses = [
            r.status
            for r in conn.execute(text("SELECT status FROM webhook_delivery_attempts")).fetchall()
        ]
    assert statuses == ["succeeded", "succeeded"]
    assert delivered_a and delivered_b  # both actually delivered something
