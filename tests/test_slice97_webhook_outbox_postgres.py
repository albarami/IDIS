"""Slice97 Task 2 (durable) — Postgres webhook outbox: unique-index idempotency, SKIP LOCKED, RLS.

Env-gated: skips locally without IDIS_DATABASE_URL / IDIS_DATABASE_ADMIN_URL; with
IDIS_REQUIRE_POSTGRES=1 (CI) it fails instead of skipping. Proves what the in-memory twin cannot:
idempotent enqueue enforced by the migration-0025 unique index (race-safe ``ON CONFLICT DO
NOTHING``), concurrent drainers that do not double-claim a row (``FOR UPDATE SKIP LOCKED``), and
tenant isolation via RLS (cross-tenant invisible; no-tenant write blocked by ``WITH CHECK``).
PYTHONPATH pinned to src.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from idis.persistence.repositories.webhook_outbox import PostgresWebhookOutboxRepository

ADMIN_URL_ENV = "IDIS_DATABASE_ADMIN_URL"
APP_URL_ENV = "IDIS_DATABASE_URL"
REQUIRE_POSTGRES_ENV = "IDIS_REQUIRE_POSTGRES"

_T0 = datetime(2026, 7, 10, 0, 0, 0, tzinfo=UTC)


def _skip_or_fail_if_no_postgres() -> None:
    admin_url = os.environ.get(ADMIN_URL_ENV)
    app_url = os.environ.get(APP_URL_ENV)
    require = os.environ.get(REQUIRE_POSTGRES_ENV, "0") == "1"
    if not admin_url or not app_url:
        msg = f"Postgres webhook-outbox integration requires {ADMIN_URL_ENV} and {APP_URL_ENV}"
        if require:
            pytest.fail(f"REQUIRED: {msg} (IDIS_REQUIRE_POSTGRES=1)")
        pytest.skip(msg)


@pytest.fixture(scope="module")
def _pg_schema() -> Generator[None, None, None]:
    """Migrate to head (idempotent) so migration 0025's unique index exists."""
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
def outbox_tenant(_pg_schema: None) -> Generator[str, None, None]:
    """A unique tenant per test; truncate the webhook tables (admin bypasses RLS) for isolation."""
    from idis.persistence.db import get_admin_engine

    def _truncate() -> None:
        with get_admin_engine().begin() as conn:
            conn.execute(text("TRUNCATE webhook_delivery_attempts, webhooks CASCADE"))

    _truncate()
    yield str(uuid.uuid4())
    _truncate()


def _create_webhook(tenant_id: str) -> str:
    """Insert a webhook subscription (FK target for outbox rows) under the tenant's RLS context."""
    from idis.persistence.db import begin_app_conn, set_tenant_local

    webhook_id = str(uuid.uuid4())
    with begin_app_conn() as conn:
        set_tenant_local(conn, tenant_id)
        conn.execute(
            text(
                "INSERT INTO webhooks (webhook_id, tenant_id, url, events, active, "
                "created_at, updated_at) VALUES (CAST(:w AS uuid), CAST(:t AS uuid), :u, "
                "ARRAY['run.completed'], true, now(), now())"
            ),
            {"w": webhook_id, "t": tenant_id, "u": "https://example.test/hook"},
        )
    return webhook_id


def _enqueue(
    repo: PostgresWebhookOutboxRepository, *, tenant: str, webhook: str, event_id: str
) -> bool:
    return repo.enqueue(
        webhook_id=webhook,
        tenant_id=tenant,
        event_id=event_id,
        event_type="run.completed",
        payload={"status": "COMPLETED", "artifact_count": 1},
        now=_T0,
    )


def test_app_role_is_non_superuser_and_nobypassrls(_pg_schema: None) -> None:
    # Invariant: every RLS proof below is only meaningful under a real non-superuser app role. A
    # superuser / BYPASSRLS role (e.g. ``postgres``) ignores RLS, so USING/WITH CHECK become vacuous
    # and the no-tenant write test would wrongly pass. Fail fast with a clear message if
    # IDIS_DATABASE_URL points at such a role.
    from idis.persistence.db import begin_app_conn

    with begin_app_conn() as conn:
        row = conn.execute(
            text(
                "SELECT current_user AS name, rolsuper, rolbypassrls "
                "FROM pg_roles WHERE rolname = current_user"
            )
        ).fetchone()
    assert row is not None
    assert not row.rolsuper, (
        f"IDIS_DATABASE_URL must be a non-superuser role; current_user={row.name!r} is SUPERUSER"
    )
    assert not row.rolbypassrls, (
        f"IDIS_DATABASE_URL must be a NOBYPASSRLS app role; current_user={row.name!r} has BYPASSRLS"
    )


def test_enqueue_is_idempotent_via_unique_index(outbox_tenant: str) -> None:
    repo = PostgresWebhookOutboxRepository()
    webhook = _create_webhook(outbox_tenant)
    event_id = str(uuid.uuid4())
    assert _enqueue(repo, tenant=outbox_tenant, webhook=webhook, event_id=event_id) is True
    # a concurrent/duplicate enqueue of the same (webhook, event) must not create a second row
    assert _enqueue(repo, tenant=outbox_tenant, webhook=webhook, event_id=event_id) is False
    assert len(repo.claim_due(tenant_id=outbox_tenant, now=_T0, limit=10)) == 1


def test_concurrent_drainers_do_not_double_claim(outbox_tenant: str) -> None:
    from idis.persistence.db import begin_app_conn, set_tenant_local

    repo = PostgresWebhookOutboxRepository()
    webhook = _create_webhook(outbox_tenant)
    for _ in range(2):
        _enqueue(repo, tenant=outbox_tenant, webhook=webhook, event_id=str(uuid.uuid4()))

    # Two overlapping transactions each claim with FOR UPDATE SKIP LOCKED -> disjoint rows.
    with begin_app_conn() as conn_a:
        set_tenant_local(conn_a, outbox_tenant)
        claimed_a = repo.claim_due(tenant_id=outbox_tenant, now=_T0, limit=1, conn=conn_a)
        with begin_app_conn() as conn_b:
            set_tenant_local(conn_b, outbox_tenant)
            claimed_b = repo.claim_due(tenant_id=outbox_tenant, now=_T0, limit=1, conn=conn_b)
            ids_a = {r.attempt_id for r in claimed_a}
            ids_b = {r.attempt_id for r in claimed_b}
    assert len(ids_a) == 1 and len(ids_b) == 1
    assert ids_a.isdisjoint(ids_b)  # the second drainer skipped the row locked by the first


def test_cross_tenant_rows_are_invisible(outbox_tenant: str) -> None:
    repo = PostgresWebhookOutboxRepository()
    webhook = _create_webhook(outbox_tenant)
    _enqueue(repo, tenant=outbox_tenant, webhook=webhook, event_id=str(uuid.uuid4()))
    assert len(repo.claim_due(tenant_id=outbox_tenant, now=_T0, limit=10)) == 1
    other_tenant = str(uuid.uuid4())
    assert repo.claim_due(tenant_id=other_tenant, now=_T0, limit=10) == []  # RLS: not visible


def test_no_tenant_context_write_is_blocked(outbox_tenant: str) -> None:
    from idis.persistence.db import begin_app_conn

    webhook = _create_webhook(outbox_tenant)
    # A write with an EMPTY tenant context must be rejected by the guarded WITH CHECK,
    # deterministically: SET LOCAL is transaction-scoped, so we clear it here rather than relying on
    # connection/pool state.
    with pytest.raises(SQLAlchemyError), begin_app_conn() as conn:
        conn.execute(text("SET LOCAL idis.tenant_id = ''"))
        conn.execute(
            text(
                "INSERT INTO webhook_delivery_attempts (attempt_id, webhook_id, tenant_id, "
                "event_id, event_type, payload, status, created_at, updated_at) VALUES "
                "(gen_random_uuid(), CAST(:w AS uuid), CAST(:t AS uuid), gen_random_uuid(), "
                "'run.completed', '{}'::jsonb, 'pending', now(), now())"
            ),
            {"w": webhook, "t": outbox_tenant},
        )


def test_outbox_rls_policy_has_is_not_null_guard(outbox_tenant: str) -> None:
    # Deterministic (connection-state-independent) proof that migration 0025 hardened the outbox RLS
    # to the 0024 guarded form: USING (qual) and WITH CHECK both carry the IS NOT NULL guard.
    from idis.persistence.db import get_admin_engine

    with get_admin_engine().begin() as conn:
        row = conn.execute(
            text(
                "SELECT qual, with_check FROM pg_policies WHERE "
                "tablename = 'webhook_delivery_attempts' AND "
                "policyname = 'webhook_delivery_attempts_tenant_isolation'"
            )
        ).fetchone()
    assert row is not None, "webhook_delivery_attempts_tenant_isolation policy must exist"
    assert "IS NOT NULL" in row.qual  # USING guarded (fail-closed reads)
    assert row.with_check is not None and "IS NOT NULL" in row.with_check  # WITH CHECK guarded


# --- transactional lifecycle-emit semantics on a caller (request-like) connection ---
# F2/F3 remediation: webhook work on a caller conn must be SAVEPOINT-isolated (a SQL failure must
# not poison the caller's transaction) and the enqueue must be transactional WITH the caller
# (commit together, roll back together — no self-committed ghost events).


def _create_subscription(tenant_id: str, events: list[str]) -> str:
    from idis.persistence.db import begin_app_conn, set_tenant_local

    webhook_id = str(uuid.uuid4())
    with begin_app_conn() as conn:
        set_tenant_local(conn, tenant_id)
        conn.execute(
            text(
                "INSERT INTO webhooks (webhook_id, tenant_id, url, events, active, "
                "created_at, updated_at) VALUES (CAST(:w AS uuid), CAST(:t AS uuid), :u, "
                ":e, true, now(), now())"
            ),
            {
                "w": webhook_id,
                "t": tenant_id,
                "u": "https://example.test/hook",
                "e": events,
            },
        )
    return webhook_id


def _count_outbox_rows(tenant_id: str) -> int:
    from idis.persistence.db import begin_app_conn, set_tenant_local

    with begin_app_conn() as conn:
        set_tenant_local(conn, tenant_id)
        row = conn.execute(text("SELECT count(*) AS n FROM webhook_delivery_attempts")).fetchone()
    return int(row.n) if row is not None else 0


def test_sql_failure_in_webhook_listing_does_not_poison_caller_tx(
    outbox_tenant: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # F2: a REAL SQL error raised by the webhook listing on the caller's connection must be
    # rolled back to a SAVEPOINT — the swallow alone cannot un-abort a poisoned transaction, so
    # without the savepoint the caller's own mutation is silently lost on "commit".
    from idis.persistence.db import begin_app_conn, set_tenant_local
    from idis.services.webhooks import lifecycle as webhook_lifecycle

    class _BadSQLService:
        def list_webhooks(self, conn: Any, active_only: bool = False) -> list[Any]:
            conn.execute(text("SELECT 1 FROM nonexistent_table_slice97_f2"))  # real SQL error
            return []

    monkeypatch.setattr(webhook_lifecycle, "get_webhook_service", lambda: _BadSQLService())

    mutation_id = str(uuid.uuid4())
    with begin_app_conn() as conn:
        set_tenant_local(conn, outbox_tenant)
        # the request's own mutation (audit-like durable write), BEFORE the webhook emit:
        conn.execute(
            text(
                "INSERT INTO webhooks (webhook_id, tenant_id, url, events, active, "
                "created_at, updated_at) VALUES (CAST(:w AS uuid), CAST(:t AS uuid), :u, "
                "ARRAY['x'], true, now(), now())"
            ),
            {"w": mutation_id, "t": outbox_tenant, "u": "https://example.test/mutation"},
        )
        webhook_lifecycle.notify_webhook_lifecycle(
            tenant_id=outbox_tenant,
            event_type="run.completed",
            resource_type="run",
            resource_id=str(uuid.uuid4()),
            data={"status": "COMPLETED"},
            conn=conn,
        )  # swallowed; the savepoint must have cleared the aborted state
        conn.execute(text("SELECT 1"))  # tx still usable
    # begin_app_conn committed on exit -> the mutation survived the webhook SQL failure
    from idis.persistence.db import begin_app_conn as _bac

    with _bac() as conn:
        set_tenant_local(conn, outbox_tenant)
        row = conn.execute(
            text("SELECT count(*) AS n FROM webhooks WHERE webhook_id = CAST(:w AS uuid)"),
            {"w": mutation_id},
        ).fetchone()
    assert row is not None and int(row.n) == 1  # A1: the mutation committed despite the failure


def test_conn_backed_enqueue_rolls_back_and_commits_with_caller_tx(outbox_tenant: str) -> None:
    # F3: with a caller transaction and an active subscription, the outbox enqueue must be
    # transactional WITH the caller: outer rollback -> no ghost row; outer commit -> row present.
    from idis.persistence.db import begin_app_conn, set_tenant_local
    from idis.services.webhooks import lifecycle as webhook_lifecycle

    _create_subscription(outbox_tenant, ["run.completed"])

    class _Abort(Exception):
        pass

    # roll the request-like transaction back after the emit
    with pytest.raises(_Abort), begin_app_conn() as conn:
        set_tenant_local(conn, outbox_tenant)
        webhook_lifecycle.notify_webhook_lifecycle(
            tenant_id=outbox_tenant,
            event_type="run.completed",
            resource_type="run",
            resource_id=str(uuid.uuid4()),
            data={"status": "COMPLETED"},
            conn=conn,
        )
        raise _Abort()
    assert _count_outbox_rows(outbox_tenant) == 0  # no ghost event survived the rollback

    with begin_app_conn() as conn:  # same emit, but the caller commits
        set_tenant_local(conn, outbox_tenant)
        webhook_lifecycle.notify_webhook_lifecycle(
            tenant_id=outbox_tenant,
            event_type="run.completed",
            resource_type="run",
            resource_id=str(uuid.uuid4()),
            data={"status": "COMPLETED"},
            conn=conn,
        )
    assert _count_outbox_rows(outbox_tenant) == 1  # enqueue committed WITH the caller


def test_delete_terminal_is_tenant_safe(outbox_tenant: str) -> None:
    repo = PostgresWebhookOutboxRepository()
    webhook = _create_webhook(outbox_tenant)
    done, pending = str(uuid.uuid4()), str(uuid.uuid4())
    _enqueue(repo, tenant=outbox_tenant, webhook=webhook, event_id=done)
    _enqueue(repo, tenant=outbox_tenant, webhook=webhook, event_id=pending)
    done_attempt = next(
        r.attempt_id
        for r in repo.claim_due(tenant_id=outbox_tenant, now=_T0, limit=10)
        if r.event_id == done
    )
    repo.mark_succeeded(tenant_id=outbox_tenant, attempt_id=done_attempt, now=_T0)

    removed = repo.delete_terminal(tenant_id=outbox_tenant, older_than=datetime.now(UTC))
    assert removed == 1  # only the succeeded (terminal) row
    survivors = repo.claim_due(tenant_id=outbox_tenant, now=datetime.now(UTC), limit=10)
    assert [r.event_id for r in survivors] == [pending]
