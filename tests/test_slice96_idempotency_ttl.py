"""Slice96 Task 5 — idempotency record TTL + tenant-safe cleanup (DEC-E): SQLite path (hermetic).

RED-first. A config-driven TTL (``IDIS_IDEMPOTENCY_TTL_DAYS``, default ~30 days) plus a
tenant-scoped ``delete_expired`` that removes only records created strictly before a cutoff, and
only for the given tenant. Replay/conflict semantics are untouched -- expired rows simply become
eligible for removal. The Postgres path is proven separately (real, env-gated) in
``test_slice96_idempotency_ttl_postgres.py``. PYTHONPATH pinned to this worktree's src.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from idis.idempotency.store import (
    IdempotencyRecord,
    ScopeKey,
    SqliteIdempotencyStore,
    load_idempotency_ttl_days,
)

_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _scope(tenant: str, key: str) -> ScopeKey:
    return ScopeKey(tenant, "actor-1", "POST", "startRun", key)


def _put(store: SqliteIdempotencyStore, *, tenant: str, key: str, created_at: datetime) -> None:
    store.put(
        _scope(tenant, key),
        IdempotencyRecord(
            payload_sha256="sha256:abc",
            status_code=202,
            media_type="application/json",
            body_bytes=b"{}",
            created_at=_iso(created_at),
        ),
    )


def test_ttl_default_is_about_30_days_and_env_overrides() -> None:
    assert load_idempotency_ttl_days(env={}) == 30
    assert load_idempotency_ttl_days(env={"IDIS_IDEMPOTENCY_TTL_DAYS": "7"}) == 7
    for bad in ("0", "-5", "x", "", "  "):
        assert load_idempotency_ttl_days(env={"IDIS_IDEMPOTENCY_TTL_DAYS": bad}) == 30  # fail-safe


def test_sqlite_cleanup_removes_expired_keeps_unexpired() -> None:
    store = SqliteIdempotencyStore(in_memory=True)
    cutoff = _NOW - timedelta(days=30)
    _put(store, tenant="t-a", key="old", created_at=_NOW - timedelta(days=60))  # expired
    _put(store, tenant="t-a", key="new", created_at=_NOW)  # fresh
    removed = store.delete_expired(tenant_id="t-a", older_than=cutoff)
    assert removed == 1
    assert store.get(_scope("t-a", "old")) is None  # expired removed
    assert store.get(_scope("t-a", "new")) is not None  # unexpired remains


def test_sqlite_cleanup_is_tenant_safe() -> None:
    store = SqliteIdempotencyStore(in_memory=True)
    cutoff = _NOW - timedelta(days=30)
    _put(store, tenant="t-a", key="old", created_at=_NOW - timedelta(days=60))  # A expired
    _put(store, tenant="t-b", key="old", created_at=_NOW - timedelta(days=60))  # B expired
    _put(store, tenant="t-b", key="new", created_at=_NOW)  # B fresh
    removed = store.delete_expired(tenant_id="t-a", older_than=cutoff)
    assert removed == 1  # only tenant A's one expired row
    assert store.get(_scope("t-a", "old")) is None  # A's expired gone
    assert store.get(_scope("t-b", "old")) is not None  # B's expired UNTOUCHED (no cross-tenant)
    assert store.get(_scope("t-b", "new")) is not None  # B's fresh untouched
