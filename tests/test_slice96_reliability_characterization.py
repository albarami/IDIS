"""Slice96 Task 1 — characterization: pin the as-built reliability surface + the true gaps.

GREEN-on-arrival. Pins (A) what already works — the Postgres/in-memory run queue + claim, atomic
retry/resume/cancel transitions, idempotency replay, rate-limit tiers/headers + the current
in-memory store, and worker tenant scoping — and (B) the current GAPS that later Slice96 tasks
flip: unguarded duplicate-run creation, absent provider budgets, absent idempotency TTL, an unused
Redis dependency, and the not-yet-implemented cooperative mid-run cancellation.

Any RED here is a real as-built surprise -> STOP and investigate. The exhaustive lifecycle /
idempotency / rate-limit contracts are already locked by test_slice75a/75b, test_api_idempotency_
middleware, and test_api_rate_limit_middleware; these are focused Slice96 snapshots, not re-tests.
Pinned: PYTHONPATH=C:/Projects/IDIS/IDIS-slice96/src for every run.
"""

from __future__ import annotations

import inspect
from collections.abc import Iterator
from pathlib import Path

import pytest

import idis
from idis.api.middleware.idempotency import IDEMPOTENT_METHODS
from idis.idempotency.store import IdempotencyRecord, ScopeKey, SqliteIdempotencyStore
from idis.persistence.repositories import runs as runs_repo_module
from idis.persistence.repositories.runs import InMemoryRunsRepository, PostgresRunsRepository
from idis.pipeline.worker import PipelineWorker
from idis.rate_limit.limiter import (
    DEFAULT_BURST_MULTIPLIER,
    DEFAULT_INTEGRATION_RPM,
    DEFAULT_USER_RPM,
    RateLimitConfig,
    RateLimitTier,
    TenantRateLimiter,
    load_rate_limit_config,
)

_TENANT_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_TENANT_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
_DEAL = "dddddddd-dddd-dddd-dddd-dddddddddddd"
_IDIS_SRC = Path(idis.__file__).parent


@pytest.fixture(autouse=True)
def _clear_in_memory_runs() -> Iterator[None]:
    # The in-memory runs store is a module-level global; isolate each pin from cross-test state.
    runs_repo_module._in_memory_runs_store.clear()
    yield
    runs_repo_module._in_memory_runs_store.clear()


def _src_files() -> list[Path]:
    return list(_IDIS_SRC.rglob("*.py"))


def _imports_redis(text: str) -> bool:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("import redis", "from redis")):
            return True
    return False


# --- (A) Queue + claim (EXISTS) ---


def test_postgres_claim_uses_for_update_skip_locked() -> None:
    src = inspect.getsource(PostgresRunsRepository.claim_queued_runs)
    assert "FOR UPDATE" in src and "SKIP LOCKED" in src


def test_in_memory_claim_is_tenant_scoped_and_queued_only() -> None:
    repo_a = InMemoryRunsRepository(_TENANT_A)
    repo_b = InMemoryRunsRepository(_TENANT_B)
    repo_a.create(run_id="r-a", deal_id=_DEAL, mode="FULL")
    repo_b.create(run_id="r-b", deal_id=_DEAL, mode="FULL")
    assert {r["run_id"] for r in repo_a.claim_queued_runs()} == {"r-a"}  # never claims tenant B
    repo_a.try_mark_running("r-a")
    assert repo_a.claim_queued_runs() == []  # a RUNNING run is not claimable


def test_try_mark_running_is_single_winner() -> None:
    repo = InMemoryRunsRepository(_TENANT_A)
    repo.create(run_id="r1", deal_id=_DEAL, mode="FULL")
    assert repo.try_mark_running("r1") is True  # QUEUED -> RUNNING (the claim winner)
    assert repo.try_mark_running("r1") is False  # already RUNNING -> RUN_ALREADY_CLAIMED at the API


# --- (B) Retry / resume / cancel (EXISTS) ---


def test_retry_requeues_only_failed_runs() -> None:
    repo = InMemoryRunsRepository(_TENANT_A)
    repo.create(run_id="r1", deal_id=_DEAL, mode="FULL")
    assert repo.try_requeue_failed("r1") is False  # QUEUED is not retryable
    repo.update_status("r1", status="FAILED", finished_at="2026-01-01T00:00:00Z")
    assert repo.try_requeue_failed("r1") is True  # FAILED -> QUEUED
    requeued = repo.get("r1")
    assert requeued is not None and requeued["status"] == "QUEUED"


def test_cancel_is_terminal_and_guards_completion() -> None:
    repo = InMemoryRunsRepository(_TENANT_A)
    repo.create(run_id="r1", deal_id=_DEAL, mode="FULL")
    assert repo.try_cancel_active("r1") is True  # QUEUED -> CANCELLED
    run = repo.get("r1")
    assert (
        run is not None and run["status"] == "CANCELLED" and run["cancel_requested_at"] is not None
    )
    assert repo.try_cancel_active("r1") is False  # already terminal
    # Completion cannot overwrite a cancellation (RUNNING-only guard).
    assert repo.try_complete_running("r1", status="SUCCEEDED", finished_at=None) is False


# --- (C) Idempotency replay (EXISTS) ---


def test_idempotency_store_replays_by_scope_key() -> None:
    store = SqliteIdempotencyStore(in_memory=True)
    key = ScopeKey(_TENANT_A, "actor-1", "POST", "startRun", "idem-key-1")
    record = IdempotencyRecord(
        payload_sha256="sha256:abc",
        status_code=202,
        media_type="application/json",
        body_bytes=b'{"run_id":"r1"}',
        created_at="2026-01-01T00:00:00Z",
    )
    assert store.get(key) is None
    store.put(key, record)
    got = store.get(key)
    assert got is not None
    assert got.status_code == 202 and got.body_bytes == b'{"run_id":"r1"}'
    assert got.payload_sha256 == "sha256:abc"  # payload digest is what enables 409 conflict


def test_idempotency_applies_to_post_and_patch_only() -> None:
    assert {"POST", "PATCH"} == IDEMPOTENT_METHODS


# --- (D) Rate limits + current store (EXISTS) ---


def test_rate_limit_defaults_and_tiers() -> None:
    config = load_rate_limit_config()
    assert (config.user_rpm, config.integration_rpm, config.burst_multiplier) == (600, 1200, 2)
    assert (DEFAULT_USER_RPM, DEFAULT_INTEGRATION_RPM, DEFAULT_BURST_MULTIPLIER) == (600, 1200, 2)
    assert set(RateLimitTier) == {RateLimitTier.USER, RateLimitTier.INTEGRATION}


def test_rate_limit_denies_beyond_capacity_with_retry_after() -> None:
    # capacity = rpm * burst = 1 * 2 = 2 tokens; refill (1/60 tok/s) is negligible across 3 checks.
    limiter = TenantRateLimiter(RateLimitConfig(user_rpm=1, integration_rpm=1, burst_multiplier=2))
    assert limiter.check(_TENANT_A, RateLimitTier.USER).allowed is True
    assert limiter.check(_TENANT_A, RateLimitTier.USER).allowed is True
    denied = limiter.check(_TENANT_A, RateLimitTier.USER)
    assert denied.allowed is False and denied.retry_after_seconds is not None


def test_rate_limit_default_store_is_in_memory_per_process() -> None:
    # DEC-A (Task 3): the token-bucket state now lives behind a RateLimitStore seam; the default is
    # the per-process in-memory store (a Redis-backed store is injectable for cross-replica limits).
    from idis.rate_limit.limiter import InMemoryRateLimitStore

    limiter = TenantRateLimiter(load_rate_limit_config())
    assert isinstance(limiter._store, InMemoryRateLimitStore)
    limiter.check(_TENANT_A, RateLimitTier.USER)
    assert isinstance(limiter._store._buckets, dict) and limiter._store._buckets


# --- (E) Worker tenant scoping (EXISTS) ---


def test_worker_is_tenant_scoped_by_construction() -> None:
    assert "tenant_ids" in inspect.signature(PipelineWorker.__init__).parameters


# --- (F) The current gaps that later Slice96 tasks flip ---


def test_duplicate_run_creation_is_guarded() -> None:
    # G1 / DEC-D CLOSED by Task 2: at most one active (QUEUED/RUNNING) run per (tenant, deal);
    # a second create raises RunAlreadyActiveError (mirrors the Postgres unique index).
    from idis.persistence.repositories.runs import RunAlreadyActiveError

    repo = InMemoryRunsRepository(_TENANT_A)
    repo.create(run_id="r1", deal_id=_DEAL, mode="FULL")
    with pytest.raises(RunAlreadyActiveError):
        repo.create(run_id="r2", deal_id=_DEAL, mode="FULL")
    items, _ = repo.list_by_deal(deal_id=_DEAL, limit=10)
    assert len([r for r in items if r["status"] == "QUEUED"]) == 1  # one active run enforced


def test_redis_rate_limit_store_is_wired_and_scoped() -> None:
    # DEC-A CLOSED by Task 3: a Redis-backed RateLimitStore now exists; redis is imported only
    # in the rate-limit store wiring (imported lazily; redis is a declared runtime dependency).
    from idis.rate_limit.limiter import RateLimitStore, RedisTokenBucketStore  # noqa: F401

    offenders = [
        f.relative_to(_IDIS_SRC).as_posix()
        for f in _src_files()
        if _imports_redis(f.read_text(encoding="utf-8")) and "rate_limit" not in f.as_posix()
    ]
    assert offenders == []  # redis imported only under rate_limit/


def test_provider_budget_hard_cap_is_wired_and_scoped() -> None:
    # DEC-C CLOSED by Task 4: a minimal per-tenant/provider hard cap now exists. The safe
    # ProviderBudgetExceededError / BudgetedLLMClient / wrap seam is defined once, and the stable
    # PROVIDER_BUDGET_EXCEEDED code lives only in the providers budget module (not scattered).
    from idis.providers.budget import (  # noqa: F401
        BudgetedLLMClient,
        ProviderBudgetExceededError,
        wrap_with_provider_budget,
    )

    definers = [
        f.relative_to(_IDIS_SRC).as_posix()
        for f in _src_files()
        if "PROVIDER_BUDGET_EXCEEDED" in f.read_text(encoding="utf-8")
    ]
    assert definers == ["providers/budget.py"]  # the stable denial code is defined in one place


def test_idempotency_ttl_and_cleanup_exist() -> None:
    # DEC-E CLOSED by Task 5: a config-driven TTL (~30 day default) + tenant-safe cleanup on both
    # the SQLite and Postgres stores. Expiry is computed from created_at (no schema change), so
    # replay/conflict semantics are unchanged -- expired rows just become eligible for removal.
    from idis.idempotency.postgres_store import PostgresIdempotencyStore
    from idis.idempotency.store import load_idempotency_ttl_days

    assert hasattr(SqliteIdempotencyStore, "delete_expired")
    assert hasattr(PostgresIdempotencyStore, "delete_expired")
    assert load_idempotency_ttl_days(env={}) == 30  # config-driven, ~30 day default


def test_cooperative_mid_run_cancellation_consults_cancel_requested_at() -> None:
    # G7 CLOSED by Task 6: the orchestrator consults cancel_requested_at at step boundaries and
    # stops boundedly with a safe RUN_CANCELLED ledger (no private text).
    from idis.services.runs import orchestrator

    src = inspect.getsource(orchestrator)
    assert "cancel_requested_at" in src
    assert "RUN_CANCELLED" in src
