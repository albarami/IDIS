"""Slice96 observability WIRING — signals emit through the REAL create_app() default sink.

RED-first. Independent review found that ``app.state.audit_sink`` is ``None`` in the deployed app:
production invokes ``create_app()`` with no arguments (Dockerfile ``--factory`` and
``idis/app.py``), and ``create_app`` assigned that ``None`` straight to ``app.state.audit_sink``
with no default. So the ``RATE_LIMIT_DENIED`` and ``IDEMPOTENCY_CLEANUP`` observability signals were
silent no-ops in production -- their Task 7 tests passed only by injecting a sink directly into the
middleware/helper.

These tests drive the REAL ``create_app()`` path with **no injected audit sink** and assert the
signals are actually emitted through the production default sink (``get_audit_sink()`` ->
``JsonlFileAuditSink``, pointed at a temp file via ``IDIS_AUDIT_LOG_PATH``). The sink is obtained
the way production obtains it; the test only redirects the sink's own configurable file path so the
emitted events can be read back. PYTHONPATH pinned to this worktree's src.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from idis.api.auth import IDIS_API_KEYS_ENV
from idis.api.main import create_app
from idis.api.routes.deals import clear_deals_store
from idis.idempotency.store import IdempotencyRecord, ScopeKey, SqliteIdempotencyStore
from idis.observability.runtime_signals import IDEMPOTENCY_CLEANUP, RATE_LIMIT_DENIED
from idis.rate_limit.limiter import RateLimitConfig, TenantRateLimiter

_REGION = "me-south-1"


def _api_keys(tenant: str, api_key: str, actor: str) -> str:
    return json.dumps(
        {
            api_key: {
                "tenant_id": tenant,
                "actor_id": actor,
                "name": "Tenant",
                "timezone": "Asia/Qatar",
                "data_region": _REGION,
                "roles": ["ANALYST"],
            }
        }
    )


def _events_from_log(path: Path) -> list[dict[str, Any]]:
    """Read events the production default (JSONL) sink actually wrote to disk."""
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped:
            events.append(json.loads(stripped))
    return events


def test_rate_limit_denial_signal_emits_through_real_default_sink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = str(uuid.uuid4())
    api_key = f"key-{uuid.uuid4().hex[:16]}"
    actor = f"actor-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv(IDIS_API_KEYS_ENV, _api_keys(tenant, api_key, actor))
    audit_log = tmp_path / "audit_events.jsonl"
    monkeypatch.setenv("IDIS_AUDIT_LOG_PATH", str(audit_log))
    clear_deals_store()

    # Deterministic denial: capacity = rpm * burst = 1, so a follow-up request is denied.
    limiter = TenantRateLimiter(RateLimitConfig(user_rpm=1, integration_rpm=1, burst_multiplier=1))
    # NO audit_sink injected -> exercises the production default-sink path (get_audit_sink()).
    app = create_app(rate_limiter=limiter, service_region=_REGION)
    client = TestClient(app, raise_server_exceptions=False)
    headers = {"X-IDIS-API-Key": api_key}

    statuses = [client.get("/v1/deals", headers=headers).status_code for _ in range(3)]
    assert 429 in statuses  # precondition: a real rate-limit denial occurred

    denied = [e for e in _events_from_log(audit_log) if e.get("event_type") == RATE_LIMIT_DENIED]
    assert denied, "RATE_LIMIT_DENIED must emit through the real create_app() default sink"
    assert denied[0]["tenant_id"] == tenant  # tenant-scoped, safe-shape
    assert denied[0]["payload"]["safe"]["code"] == "RATE_LIMIT_EXCEEDED"


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


def test_idempotency_cleanup_signal_emits_through_real_default_sink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = str(uuid.uuid4())
    api_key = f"key-{uuid.uuid4().hex[:16]}"
    actor = f"actor-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv(IDIS_API_KEYS_ENV, _api_keys(tenant, api_key, actor))
    audit_log = tmp_path / "audit_events.jsonl"
    monkeypatch.setenv("IDIS_AUDIT_LOG_PATH", str(audit_log))
    clear_deals_store()

    store = SqliteIdempotencyStore(in_memory=True)
    now = datetime.now(UTC)  # middleware cutoff is real-now - TTL; seed relative to now
    _seed(store, tenant=tenant, key="old", created_at=now - timedelta(days=60))  # expired (>30d)

    # NO audit_sink injected -> production default-sink path. Store injection is not the sink.
    app = create_app(idempotency_store=store, service_region=_REGION)
    client = TestClient(app, raise_server_exceptions=False)
    headers = {
        "X-IDIS-API-Key": api_key,
        "Content-Type": "application/json",
        "Idempotency-Key": "wiring-key-1",
    }
    resp = client.post("/v1/deals", headers=headers, json={"name": "D", "company_name": "Acme"})
    assert resp.status_code in (200, 201)  # precondition: real request succeeded
    assert store.get(_scope(tenant, "old")) is None  # precondition: expired record reclaimed

    cleanup = [e for e in _events_from_log(audit_log) if e.get("event_type") == IDEMPOTENCY_CLEANUP]
    assert cleanup, "IDEMPOTENCY_CLEANUP must emit through the real create_app() default sink"
    assert cleanup[0]["tenant_id"] == tenant  # tenant-scoped, safe-shape
    assert cleanup[0]["payload"]["safe"]["deleted_count"] >= 1
