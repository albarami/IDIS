"""Slice96 Task 3 — RateLimitStore seam (DEC-A): in-memory default + injectable Redis store.

RED-first. The token-bucket state moves behind a RateLimitStore interface: the current in-memory
impl stays the default; a Redis-backed store enables correct cross-replica limits. Fake-Redis unit
tests plus an env-gated real-Redis integration test (runs in CI, skips locally without a URL).
PYTHONPATH pinned to this worktree's src.
"""

from __future__ import annotations

import inspect
import math
import os
import re
import tomllib
import uuid
from pathlib import Path

import pytest

from idis.rate_limit.limiter import (
    InMemoryRateLimitStore,
    RateLimitConfig,
    RateLimitStore,
    RateLimitTier,
    RedisTokenBucketStore,
    TenantRateLimiter,
)

_TENANT = "11111111-1111-1111-1111-111111111111"


def _small_config() -> RateLimitConfig:
    # capacity = rpm * burst = 1 * 2 = 2 tokens; refill (1/60 tok/s) negligible across rapid calls.
    return RateLimitConfig(user_rpm=1, integration_rpm=1, burst_multiplier=2)


class _FakeRedis:
    """In-process stand-in for a Redis client: implements the token-bucket eval the store uses,
    keeping per-key (tokens, ts_ms) state in a dict — no real Redis, shareable across limiters."""

    def __init__(self) -> None:
        self._state: dict[str, tuple[float, float]] = {}
        self.eval_calls = 0

    def eval(self, script: str, numkeys: int, *args: object) -> list[int]:
        self.eval_calls += 1
        key = str(args[0])
        capacity, refill, cost, now, _ttl = (float(a) for a in args[1:6])
        tokens, ts = self._state.get(key, (capacity, now))
        elapsed = max(0.0, now - ts)
        tokens = min(capacity, tokens + elapsed * refill / 1000.0)
        allowed = 1 if tokens >= cost else 0
        if allowed:
            tokens -= cost
        self._state[key] = (tokens, now)
        retry = 0
        if not allowed:
            retry = max(1, math.ceil((cost - tokens) / refill)) if refill > 0 else 60
        return [allowed, int(tokens), retry]


# --- the seam ---


def test_rate_limit_store_protocol_and_impls_exist() -> None:
    assert isinstance(InMemoryRateLimitStore(), RateLimitStore)  # runtime-checkable protocol
    assert isinstance(RedisTokenBucketStore(_FakeRedis()), RateLimitStore)


def test_default_limiter_uses_in_memory_store() -> None:
    limiter = TenantRateLimiter(_small_config())
    assert isinstance(limiter._store, InMemoryRateLimitStore)


def test_in_memory_store_denies_beyond_capacity() -> None:
    store = InMemoryRateLimitStore()
    allowed = [
        store.consume(key="t:user", capacity=2, refill_rate_per_sec=1 / 60)[0] for _ in range(3)
    ]
    assert allowed == [True, True, False]


# --- Redis store via a fake client (no real Redis) ---


def test_redis_store_enforces_token_bucket_via_client() -> None:
    fake = _FakeRedis()
    store = RedisTokenBucketStore(fake)
    results = [
        store.consume(key="t:user", capacity=2, refill_rate_per_sec=1 / 60) for _ in range(3)
    ]
    assert [r[0] for r in results] == [True, True, False]
    assert results[2][1] >= 1  # retry_after seconds on denial
    assert fake.eval_calls == 3  # goes through the injected client


def test_redis_store_shared_across_replicas_enforces_one_limit() -> None:
    # The DEC-A win: two limiters (replicas) sharing one Redis enforce a SINGLE cross-replica limit,
    # unlike per-process in-memory buckets which would allow the capacity on EACH replica.
    shared = _FakeRedis()
    replica_a = TenantRateLimiter(_small_config(), store=RedisTokenBucketStore(shared))
    replica_b = TenantRateLimiter(_small_config(), store=RedisTokenBucketStore(shared))
    assert replica_a.check(_TENANT, RateLimitTier.USER).allowed is True
    assert replica_b.check(_TENANT, RateLimitTier.USER).allowed is True
    assert replica_a.check(_TENANT, RateLimitTier.USER).allowed is False  # capacity 2 shared


def test_in_memory_stores_are_per_process_not_shared() -> None:
    # Contrast: separate in-memory stores (per replica) each allow their own capacity (the gap
    # DEC-A closes for multi-replica deployments).
    replica_a = TenantRateLimiter(_small_config())
    replica_b = TenantRateLimiter(_small_config())
    assert replica_a.check(_TENANT, RateLimitTier.USER).allowed is True
    assert replica_a.check(_TENANT, RateLimitTier.USER).allowed is True
    assert replica_a.check(_TENANT, RateLimitTier.USER).allowed is False  # A exhausted
    assert replica_b.check(_TENANT, RateLimitTier.USER).allowed is True  # B independent


def test_redis_store_from_url_lazily_imports_redis_source_pin() -> None:
    src = inspect.getsource(RedisTokenBucketStore.from_url)
    assert "import redis" in src  # imported lazily inside from_url, not at module load


# --- real Redis integration (env-gated; exercises the actual Lua atomic bucket) ---

_REDIS_URL = os.environ.get("IDIS_TEST_REDIS_URL") or os.environ.get("IDIS_REDIS_URL")


@pytest.mark.skipif(
    not _REDIS_URL,
    reason="requires a real Redis: set IDIS_TEST_REDIS_URL (or IDIS_REDIS_URL)",
)
def test_real_redis_enforces_one_shared_bucket_across_replicas() -> None:
    # Proves the REAL Redis/Lua path (not the fake): two limiter instances (replicas) sharing one
    # real Redis enforce a SINGLE cross-replica token bucket. Skipped unless a Redis URL is set.
    assert _REDIS_URL is not None
    config = RateLimitConfig(user_rpm=1, integration_rpm=1, burst_multiplier=2)  # capacity = 2
    replica_a = TenantRateLimiter(config, store=RedisTokenBucketStore.from_url(_REDIS_URL))
    replica_b = TenantRateLimiter(config, store=RedisTokenBucketStore.from_url(_REDIS_URL))
    tenant = f"itest-{uuid.uuid4()}"  # unique key -> isolated from other runs / leftover state
    store = replica_a._store
    try:
        assert replica_a.check(tenant, RateLimitTier.USER).allowed is True  # 1st (replica A)
        assert (
            replica_b.check(tenant, RateLimitTier.USER).allowed is True
        )  # 2nd (replica B, shared)
        third = replica_a.check(tenant, RateLimitTier.USER)
        assert third.allowed is False  # 3rd -> the shared bucket is exhausted across replicas
        assert third.retry_after_seconds is not None and third.retry_after_seconds >= 1
    finally:
        store._client.delete(store._prefix + f"{tenant}:{RateLimitTier.USER.value}")


# --- packaging + CI: the Redis client ships, and CI provisions Redis so the real path runs ---


def test_redis_is_a_packaged_runtime_dependency() -> None:
    data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    names = {
        re.split(r"[<>=!~\[; ]", dep, maxsplit=1)[0].strip().lower()
        for dep in data["project"]["dependencies"]
    }
    assert "redis" in names, (
        "redis must be a runtime dependency (the Redis rate-limit store needs it)"
    )


def test_ci_provisions_redis_for_the_real_rate_limit_test() -> None:
    ci = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "redis:7-alpine" in ci  # a Redis service is provisioned in CI
    assert "IDIS_TEST_REDIS_URL" in ci  # wired so test_real_redis_... runs (not skips) in CI
