"""Slice98 Task 7 (durable) - retention janitor against real Postgres.

Env-gated: skips locally without IDIS_DATABASE_URL / IDIS_DATABASE_ADMIN_URL; with
IDIS_REQUIRE_POSTGRES=1 (CI) it fails instead of skipping. Proves what the hermetic fakes cannot:
the destructive sweep deletes ONLY the scoped tenant's expired idempotency records and terminal
webhook-outbox attempts - durably, verified through a separate admin connection - while another
tenant's rows and non-qualifying rows (fresh, pending) survive; dry-run deletes nothing; and a
REAL legal hold (0029 ``legal_holds`` via the Postgres registry seam) blocks the hold-aware
deletion through the real ComplianceEnforcedStore until lifted. No migration belongs to Task 7:
the janitor reads and cleans EXISTING tables only. PYTHONPATH pinned to this worktree's src.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from idis.audit.sink import InMemoryAuditSink
from idis.compliance.retention import (
    HoldTarget,
    PostgresLegalHoldRegistry,
    RetentionClass,
    RetentionPolicy,
    apply_hold,
    lift_hold,
    reset_legal_hold_registry,
)

ADMIN_URL_ENV = "IDIS_DATABASE_ADMIN_URL"
APP_URL_ENV = "IDIS_DATABASE_URL"
REQUIRE_POSTGRES_ENV = "IDIS_REQUIRE_POSTGRES"

_TENANT_A = "11111111-1111-1111-1111-111111111111"
_TENANT_B = "22222222-2222-2222-2222-222222222222"
_NOW = datetime.now(UTC)
_TABLES = ("webhook_delivery_attempts", "webhooks", "idempotency_records", "legal_holds")


def _skip_or_fail_if_no_postgres() -> None:
    admin_url = os.environ.get(ADMIN_URL_ENV)
    app_url = os.environ.get(APP_URL_ENV)
    require = os.environ.get(REQUIRE_POSTGRES_ENV, "0") == "1"
    if not admin_url or not app_url:
        msg = f"Postgres janitor integration requires {ADMIN_URL_ENV} and {APP_URL_ENV}"
        if require:
            pytest.fail(f"REQUIRED: {msg} (IDIS_REQUIRE_POSTGRES=1)")
        pytest.skip(msg)


@pytest.fixture(scope="module")
def _pg_schema() -> Generator[None, None, None]:
    """Migrate to head (idempotent); Task 7 adds no migration - existing tables only."""
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
def pg_clean(_pg_schema: None) -> Generator[None, None, None]:
    from idis.persistence.db import get_admin_engine

    def _truncate() -> None:
        with get_admin_engine().begin() as conn:
            conn.execute(text(f"TRUNCATE {', '.join(_TABLES)} CASCADE"))

    reset_legal_hold_registry()
    _truncate()
    yield
    _truncate()
    reset_legal_hold_registry()


def _seed_idempotency_row(tenant_id: str, key: str, created_at: datetime) -> None:
    from idis.persistence.db import get_admin_engine

    with get_admin_engine().begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO idempotency_records (
                    tenant_id, actor_id, method, operation_id, idempotency_key,
                    payload_sha256, status_code, media_type, body_bytes, created_at
                ) VALUES (
                    CAST(:tenant_id AS uuid), 'actor-janitor', 'POST', 'createDeal', :key,
                    :sha, 201, 'application/json', ''::bytea, :created_at
                )
                """
            ),
            {"tenant_id": tenant_id, "key": key, "sha": "0" * 64, "created_at": created_at},
        )


def _seed_outbox_attempt(tenant_id: str, status: str, updated_at: datetime) -> str:
    from idis.persistence.db import get_admin_engine

    webhook_id = str(uuid.uuid4())
    attempt_id = str(uuid.uuid4())
    with get_admin_engine().begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO webhooks (
                    webhook_id, tenant_id, url, events, active, created_at, updated_at
                ) VALUES (
                    CAST(:webhook_id AS uuid), CAST(:tenant_id AS uuid),
                    'https://subscriber.example.com/hook', ARRAY['deal.created'],
                    true, :now, :now
                )
                """
            ),
            {"webhook_id": webhook_id, "tenant_id": tenant_id, "now": _NOW},
        )
        conn.execute(
            text(
                """
                INSERT INTO webhook_delivery_attempts (
                    attempt_id, webhook_id, tenant_id, event_id, event_type, payload,
                    attempt_count, status, created_at, updated_at
                ) VALUES (
                    CAST(:attempt_id AS uuid), CAST(:webhook_id AS uuid),
                    CAST(:tenant_id AS uuid), CAST(:event_id AS uuid), 'deal.created',
                    '{}'::jsonb, 1, :status, :updated_at, :updated_at
                )
                """
            ),
            {
                "attempt_id": attempt_id,
                "webhook_id": webhook_id,
                "tenant_id": tenant_id,
                "event_id": str(uuid.uuid4()),
                "status": status,
                "updated_at": updated_at,
            },
        )
    return attempt_id


def _count(table: str, tenant_id: str) -> int:
    """Row count via a separate ADMIN connection (RLS-bypassing durability check)."""
    from idis.persistence.db import get_admin_engine

    with get_admin_engine().begin() as conn:
        row = conn.execute(
            text(f"SELECT COUNT(*) AS n FROM {table} WHERE tenant_id = CAST(:tenant_id AS uuid)"),
            {"tenant_id": tenant_id},
        ).fetchone()
    assert row is not None
    return int(row.n)


def _noop_deleter(tenant_id: str, candidate: object) -> None:
    raise AssertionError("retention deleter must not be called in these sweeps")


def _seed_orphans() -> None:
    old = _NOW - timedelta(days=60)
    _seed_idempotency_row(_TENANT_A, "expired-a", created_at=old)
    _seed_idempotency_row(_TENANT_A, "fresh-a", created_at=_NOW)
    _seed_idempotency_row(_TENANT_B, "expired-b", created_at=old)
    _seed_outbox_attempt(_TENANT_A, "succeeded", updated_at=old)
    _seed_outbox_attempt(_TENANT_A, "pending", updated_at=old)  # not terminal: must survive
    _seed_outbox_attempt(_TENANT_B, "exhausted", updated_at=old)


def _run_sweep(*, destructive: bool) -> dict:
    from idis.idempotency.postgres_store import PostgresIdempotencyStore
    from idis.persistence.repositories.webhook_outbox import PostgresWebhookOutboxRepository
    from idis.services.compliance.janitor import sweep_tenant

    return sweep_tenant(
        _TENANT_A,
        sources=[],
        deleter=_noop_deleter,
        idempotency_store=PostgresIdempotencyStore(),
        outbox_repo=PostgresWebhookOutboxRepository(),
        audit_sink=InMemoryAuditSink(),
        now=_NOW,
        destructive=destructive,
    )


def test_destructive_sweep_cleans_only_scoped_tenant_durably(pg_clean: None) -> None:
    _seed_orphans()

    result = _run_sweep(destructive=True)
    assert result["idempotency_deleted"] == 1  # only tenant A's EXPIRED record
    assert result["outbox_deleted"] == 1  # only tenant A's TERMINAL old attempt

    # durability + scoping proven through a separate admin connection:
    assert _count("idempotency_records", _TENANT_A) == 1  # the fresh record survives
    assert _count("idempotency_records", _TENANT_B) == 1  # other tenant untouched
    assert _count("webhook_delivery_attempts", _TENANT_A) == 1  # the pending one survives
    assert _count("webhook_delivery_attempts", _TENANT_B) == 1  # other tenant untouched


def test_dry_run_deletes_nothing_in_postgres(pg_clean: None) -> None:
    _seed_orphans()

    result = _run_sweep(destructive=False)
    assert result["dry_run"] is True
    assert result["idempotency_deleted"] == 0
    assert result["outbox_deleted"] == 0
    assert _count("idempotency_records", _TENANT_A) == 2
    assert _count("idempotency_records", _TENANT_B) == 1
    assert _count("webhook_delivery_attempts", _TENANT_A) == 2
    assert _count("webhook_delivery_attempts", _TENANT_B) == 1


def test_real_hold_blocks_hold_aware_deletion_until_lifted(pg_clean: None, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A REAL legal hold (Postgres registry via the seam) governs the real deletion boundary."""
    from idis.api.auth import TenantContext
    from idis.services.compliance.janitor import (
        RetentionCandidate,
        sweep_tenant_retention,
    )
    from idis.storage.compliant_store import ComplianceEnforcedStore
    from idis.storage.filesystem_store import FilesystemObjectStore

    ctx = TenantContext(
        tenant_id=_TENANT_A,
        actor_id="compliance-janitor",
        name="Janitor",
        timezone="UTC",
        data_region=None,
        roles=frozenset({"ADMIN"}),
    )
    # No explicit hold registry: the store resolves holds through the seam -> Postgres.
    store = ComplianceEnforcedStore(inner_store=FilesystemObjectStore(base_dir=tmp_path))
    object_key = "artifacts/task7-hold-proof.bin"
    store.put(tenant_ctx=ctx, key=object_key, data=b"retention janitor hold proof")

    hold = apply_hold(
        ctx,
        HoldTarget.ARTIFACT,
        object_key,
        "Litigation hold pending case 2026-CV-1138 discovery.",
        InMemoryAuditSink(),
        PostgresLegalHoldRegistry(),
    )

    def _deleter(tenant_id: str, candidate: RetentionCandidate) -> None:
        store.delete(
            ctx,
            candidate.resource_id,
            resource_id=candidate.resource_id,
            hold_target_type=candidate.hold_target_type,
        )

    class _Source:
        name = "artifacts"

        def list_candidates(self, tenant_id: str) -> list[RetentionCandidate]:
            return [
                RetentionCandidate(
                    resource_id=object_key,
                    created_at=_NOW - timedelta(days=31),
                    retention_class=RetentionClass.DELIVERABLES,
                    hold_target_type=HoldTarget.ARTIFACT,
                )
            ]

    permissive = {
        RetentionClass.DELIVERABLES: RetentionPolicy(
            retention_class=RetentionClass.DELIVERABLES,
            retention_days=30,
            hard_delete_allowed=True,
            requires_admin_approval=False,
        )
    }

    held_counts = sweep_tenant_retention(
        _TENANT_A, [_Source()], _deleter, policies=permissive, now=_NOW, destructive=True
    )
    assert held_counts["held_skipped"] == 1
    assert held_counts["deleted"] == 0
    assert store.get(ctx, object_key).body  # the held object is still there

    lift_hold(ctx, hold.hold_id, InMemoryAuditSink(), PostgresLegalHoldRegistry())

    lifted_counts = sweep_tenant_retention(
        _TENANT_A, [_Source()], _deleter, policies=permissive, now=_NOW, destructive=True
    )
    assert lifted_counts["deleted"] == 1
    from idis.storage.errors import ObjectNotFoundError

    with pytest.raises(ObjectNotFoundError):
        store.get(ctx, object_key)  # deleted after the hold was lifted
