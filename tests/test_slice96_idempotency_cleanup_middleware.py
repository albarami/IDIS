"""Slice96 Task 5 (cleanup wiring) — opportunistic idempotency TTL cleanup in the middleware.

RED-first. ``IdempotencyMiddleware`` opportunistically runs a throttled, tenant-scoped cleanup using
the configured TTL, so expired records are actually reclaimed during the REAL request flow (not only
by calling ``delete_expired`` directly). Cleanup is best-effort and MUST NOT change replay/conflict
semantics. This file proves the SQLite path end-to-end through the real app + TestClient; the
Postgres path is proven real + env-gated in ``test_slice96_idempotency_ttl_postgres.py``.
PYTHONPATH pinned to this worktree's src.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from idis.api.auth import IDIS_API_KEYS_ENV
from idis.api.main import create_app
from idis.api.middleware.idempotency import IdempotencyMiddleware
from idis.api.routes.deals import clear_deals_store
from idis.audit.sink import InMemoryAuditSink
from idis.idempotency.store import IdempotencyRecord, ScopeKey, SqliteIdempotencyStore


def _scope(tenant: str, key: str) -> ScopeKey:
    return ScopeKey(tenant, "seed-actor", "POST", "seedOp", key)


def _seed(store: SqliteIdempotencyStore, *, tenant: str, key: str, created_at: datetime) -> None:
    store.put(
        _scope(tenant, key),
        IdempotencyRecord(
            payload_sha256="sha256:abc",
            status_code=201,
            media_type="application/json",
            body_bytes=b"{}",
            created_at=created_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        ),
    )


def test_middleware_cleans_expired_during_real_request_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_a = str(uuid.uuid4())
    tenant_b = str(uuid.uuid4())
    api_key_a = f"key-a-{uuid.uuid4().hex[:16]}"
    actor_a = f"actor-a-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv(
        IDIS_API_KEYS_ENV,
        json.dumps(
            {
                api_key_a: {
                    "tenant_id": tenant_a,
                    "actor_id": actor_a,
                    "name": "Tenant A",
                    "timezone": "Asia/Qatar",
                    "data_region": "me-south-1",
                    "roles": ["ANALYST"],
                }
            }
        ),
    )
    clear_deals_store()

    store = SqliteIdempotencyStore(in_memory=True)
    now = datetime.now(UTC)  # cutoff in the middleware is real-now - TTL, so seed relative to now
    _seed(store, tenant=tenant_a, key="a-old", created_at=now - timedelta(days=60))  # expired
    _seed(store, tenant=tenant_a, key="a-new", created_at=now)  # fresh
    _seed(store, tenant=tenant_b, key="b-old", created_at=now - timedelta(days=60))  # other tenant

    app = create_app(
        audit_sink=InMemoryAuditSink(), idempotency_store=store, service_region="me-south-1"
    )
    client = TestClient(app)
    headers = {
        "X-IDIS-API-Key": api_key_a,
        "Content-Type": "application/json",
        "Idempotency-Key": "flow-key-1",
    }
    body = {"name": "Deal Flow", "company_name": "Acme"}

    resp = client.post("/v1/deals", headers=headers, json=body)
    assert resp.status_code in (200, 201)  # the real request succeeded

    # cleanup fired opportunistically for tenant A during the request:
    assert store.get(_scope(tenant_a, "a-old")) is None  # expired removed via the REAL flow
    assert store.get(_scope(tenant_a, "a-new")) is not None  # unexpired remains
    assert store.get(_scope(tenant_b, "b-old")) is not None  # other tenant UNTOUCHED (tenant-safe)

    # replay/conflict semantics unchanged: same key + same payload replays the stored response.
    replay = client.post("/v1/deals", headers=headers, json=body)
    assert replay.status_code == resp.status_code
    assert replay.headers.get("X-IDIS-Idempotency-Replay") == "true"


class _CountingStore:
    """Records each cleanup call so the throttle can be asserted (no real store needed)."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def delete_expired(self, *, tenant_id: str, older_than: datetime) -> int:
        self.calls.append(tenant_id)
        return 0


async def _noop_app(scope: object, receive: object, send: object) -> None:  # pragma: no cover
    return None


def test_cleanup_is_throttled_not_run_on_every_request() -> None:
    store = _CountingStore()
    mw = IdempotencyMiddleware(_noop_app, cleanup_interval_seconds=3600.0)
    mw._maybe_cleanup("tenant-1", store)  # first for tenant-1 -> fires
    mw._maybe_cleanup("tenant-1", store)  # within the interval -> throttled (skipped)
    mw._maybe_cleanup("tenant-2", store)  # different tenant -> fires
    assert store.calls == ["tenant-1", "tenant-2"]  # not run on every call
