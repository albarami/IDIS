# Slice96: Queue, Retry, Idempotency, Rate Limits, And Redis Decision — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task, TDD (RED → verify RED → minimal GREEN → verify), reporting + STOP-for-approval after each task. Every Python gate MUST pin `PYTHONPATH=C:/Projects/IDIS/IDIS-slice96/src` (pytest) / `MYPYPATH=C:/Projects/IDIS/IDIS-slice96/src` (mypy), and reports must state the pin (a stale editable `.pth` otherwise wins).

**Goal:** Make long expensive runs **reliable and controllable** — and record the **Redis / cache / worker-role decision** (Master plan §412; **opens Phase I: Production Runtime Reliability**).

**Acceptance (master plan):** API and worker paths are **consistent, tenant-scoped, retry-safe, and observable.**

**Architecture:** IDIS already runs on a Postgres-backed run queue (`runs` table, status `QUEUED`) drained by a polling `PipelineWorker`, with a canonical shared `RunExecutionService.execute` on both the API and worker paths, atomic retry/resume/cancel lifecycle transitions, an idempotency middleware over 15 mutation endpoints, and a per-tenant token-bucket rate limiter. Slice96 is therefore **mostly a decide-and-close-the-real-gaps slice, not a rebuild**: the queue/retry/resume/cancel/idempotency/rate-limit *mechanisms* exist and are heavily tested (Slice75/75b), so the genuine new work is (a) the **Redis / cache / worker-role decision** the slice is named for, (b) three real correctness gaps — **duplicate-run safety on creation**, **multi-replica rate-limit correctness**, and **provider budgets** — and (c) **observability** for queue/retry/cancel/limit events. **Tech stack:** Python 3.11, Pydantic v2, FastAPI, SQLAlchemy/psycopg2 (Postgres + RLS), pytest, ruff, mypy.

---

## 1. As-built map (verified — reuse-before-create)

Verified by 3 parallel discovery agents + hand-verification of load-bearing claims. File:line refs are on `IDIS-slice96` @ `570c2558`.

**Queue + worker (EXISTS, tested).**
- Queue = Postgres `runs` table, `status ∈ {QUEUED, RUNNING, SUCCEEDED, FAILED, CANCELLED}` (migration `0009_runs_...`; `CANCELLED` added by `0018`). In-memory fallback `_in_memory_runs_store` for non-Postgres mode.
- `PipelineWorker` (`src/idis/pipeline/worker.py`) polls every 5s, claims `QUEUED` rows via `claim_queued_runs()` with `FOR UPDATE SKIP LOCKED` (`repositories/runs.py`), scoped to `IDIS_WORKER_TENANT_IDS`; started/stopped in `api/main.py:148-159` (only when Postgres configured).
- API path (`routes/runs.py:197-348` `start_run`) creates the run `QUEUED` then **executes synchronously in-request** via `await asyncio.to_thread(execution_service.execute, ctx)` — so a run is durable-queued but the API caller also drives execution. Worker + API share the same `RunExecutionService.execute`.

**Retry / resume / cancel (EXISTS, comprehensively tested — Slice75b, 2051 lines).**
- `RunLifecycleService` (`services/runs/lifecycle.py`): `request_retry`/`request_cancel`. Retry/resume = `try_requeue_failed` (`FAILED→QUEUED`, atomic `WHERE status='FAILED'`, clears `cancel_requested_at`/`finished_at`; re-queues only — does **not** execute). Cancel = `try_cancel_active` (`QUEUED|RUNNING→CANCELLED`, atomic `WHERE status IN (...)`, sets `cancel_requested_at`). Completion guard `try_complete_running` (`WHERE status='RUNNING'`) refuses to overwrite `CANCELLED`. All RLS-scoped.
- API↔worker parity + duplicate-*execution* safety (Slice75a): `try_mark_running` race loss → **`RUN_ALREADY_CLAIMED` (409)** deterministically; leakage-safe strict-block ledger; legacy `PipelineExecutor` quarantined.

**Idempotency (EXISTS, tested — 930 lines).**
- `IdempotencyMiddleware` (`api/middleware/idempotency.py`): POST/PATCH on `/v1/*`, `Idempotency-Key` header, SHA-256 payload digest, scope `(tenant, actor, method, operation_id, key)`. Replay stored 2xx (+ side-effecting 409s) with `X-IDIS-Idempotency-Replay`; same-key different-payload → **`IDEMPOTENCY_KEY_CONFLICT` (409)**; fail-closed. Stores: `idempotency/store.py` (SQLite fallback) + `idempotency/postgres_store.py` (in-tx, RLS); selected by `db_conn` presence. **15 endpoints** declare `IdempotencyKey` in the OpenAPI (incl. `startRun`, `cancelRun`).

**Rate limits (EXISTS, tested — 633 lines).**
- `TenantRateLimiter` (`rate_limit/limiter.py`) + `RateLimitMiddleware`: per-tenant, per-tier token bucket (USER 600 rpm / INTEGRATION 1200 rpm, burst ×2), monotonic time, `429 RATE_LIMIT_EXCEEDED` + `Retry-After`, `X-IDIS-RateLimit-*` headers. **Storage is an in-memory `self._buckets` dict guarded by a `threading.Lock` (`limiter.py:249`) — per-process only.**

**Run status/step model (EXISTS).** `RunStatus`/`RunStep` (`models/run_step.py`), 29 step names, per-step `retry_count`, `error_code`, sanitized `result_summary`; run-level `block_reason` = stable code.

---

## 2. True gaps ("what's not") — against the acceptance

The acceptance is "API and worker paths are consistent, tenant-scoped, retry-safe, and observable." Consistency + tenant-scoping + retry-safety are largely **already locked** (Slice75/75b). The genuine gaps:

- **G1 — Duplicate-run safety on *creation* (real).** `start_run` (`routes/runs.py:225,265-273`) reads `Idempotency-Key` as *optional* and passes a possibly-`NULL` key to `create()`; there is **no "one active run per deal" guard**. The idempotency middleware only dedups an *identical-key* replay — a POST with **no key** (or a different key) creates a brand-new run every time, so a deal can accumulate multiple concurrent `QUEUED`/`RUNNING` runs (wasteful + confusing, not corrupting). Gap = a decided, enforced duplicate-run policy.
- **G2 — Multi-replica rate-limit correctness (real).** The limiter is in-memory per-process, but `deploy/k8s/deployment.yaml:13` runs **`replicas: 3`** → each pod holds its own buckets, so the effective limit is ~3× and non-deterministic (which pod you hit). Per-tenant fairness/quotas are not actually enforced cluster-wide. Gap = a decided store for cross-replica enforcement (this is the crux the "Redis decision" turns on).
- **G3 — Provider budgets (net-new).** No per-tenant/provider spend, token, or cost cap exists (`anthropic_client.py` fixes max-tokens/retries but tracks no cumulative spend; no budget table, no enforcement). Gap = a decided budget model + minimal enforcement (or an explicitly-scoped subset with honest deferral).
- **G4 — Redis / cache / worker-role DECISION (the slice's named deliverable).** `IDIS_REDIS_URL` is configured in docker-compose but **used nowhere in `src/`** (verified: no redis imports); K8s/terraform declare no Redis. A recorded architectural decision is required: where (if anywhere) Redis lands (rate-limit store? enrichment cache `services/enrichment/cache_policy.py`? queue signalling?), whether the queue stays Postgres-polling, and the worker deployment/scaling role.
- **G5 — Observability (acceptance word "observable").** Queue depth, claim/poll, retry/resume/cancel transitions, rate-limit denials, and (if added) budget denials should be observably countable/traceable in a safe-shape way. Coverage is partial (audit/tracing exist; queue/limit metrics may not). Gap = decided, safe-shape observability signals + tests.
- **G6 — Idempotency record TTL/cleanup (minor, real).** Neither the SQLite nor Postgres idempotency store expires records (`idempotency/store.py`) → unbounded growth. Gap = a TTL/cleanup policy (or an accepted, documented no-expiry decision).
- **G7 — Mid-run cooperative cancellation (carried RED from Slice75b).** Cancel of a `RUNNING` run sets `cancel_requested_at`, but the orchestrator "MUST consult and stop between steps" is a Slice75b **RED/future** item — a long expensive run keeps executing until it finishes even after cancel. Gap = orchestrator consults `cancel_requested_at` at safe step boundaries (bounded stop).

---

## 3. Approach

**Decide first, then close real gaps, reuse-first, TDD, safe-shape, fail-closed.** Because much is already built + tested, most tasks *characterize* the as-built (pin it GREEN-on-arrival) and then flip a small number of real gaps RED-first. Sequence: (1) lock the **§7 decisions** with the user (esp. DEC-A Redis role — it gates G2/G4 scope); (2) characterization pins of the as-built queue/retry/idempotency/rate-limit surface; (3) close the decided gaps RED-first (duplicate-run policy, rate-limit store, budgets-if-in-scope, cooperative cancel, TTL, observability); (4) reconcile docs (strict-readiness banner + a short worker/queue/limits architecture note recording the decision) + an acceptance capstone. Each task states its gate — the pinned Python gate (`pytest -q`, `ruff format --check`, `ruff check`, clean-cache `mypy`, `forbidden_scan`, `git diff --check`) and, only if UI/docs are touched, the relevant doc check.

---

## 4. Safety / strict boundaries

- **Safe-shape only:** any new reviewer/observability surface exposes IDs / counts / categories / codes / refs — never claim text, transcripts, prompt text, raw model output, secrets, paths, or env values. Rate-limit/budget denials use stable codes + safe metadata (limit, tier, retry-after), never internal state.
- **Fail-closed** with static, ledger-safe reasons (matching existing `RATE_LIMITER_FAILED`, `IDEMPOTENCY_STORE_FAILED`, `STRICT_FULL_LIVE_BLOCKED`).
- **Tenant-scoped** everywhere (RLS on any new table; per-tenant keys on any new limiter/budget store).
- **Migration discipline:** prefer no new durable tables; if a decision (budgets, dedup index, rate-limit counters) requires one, it is an explicit, tenant-RLS, idempotent migration called out in that task.
- **Injected fakes only** — no real Anthropic, no real Redis in tests (inject a fake clock / fake store / fake Redis client); no real network.

---

## 5. Risks

- **Redis introduces new infra + failure modes** (availability, connection pooling, failover). Mitigate: keep durable state in Postgres; use Redis (if chosen) only for the *ephemeral* rate-limit counters, fail-open-or-closed by decision, behind an injectable interface with an in-memory default so tests + single-process dev never need Redis.
- **Provider budgets can balloon in scope** (accounting, model price tables, reset cadence, cross-run aggregation). Mitigate: DEC-C scopes it to a minimal, testable enforcement (or explicit deferral) — do not build a billing system.
- **Cooperative cancellation touches the orchestrator hot path.** Mitigate: consult a cheap cancel flag only at existing step boundaries; never mid-step; bounded and covered by the existing Slice75b RED test.
- **Characterization surprises:** any RED in a characterization pin = a real as-built surprise → STOP and investigate before flipping.

---

## 6. Tasks (bite-sized, TDD) — PROPOSED sequence

- **Task 0 — Lock §7 decisions** (esp. DEC-A Redis role, DEC-C budget scope, DEC-D duplicate-run policy). No code.
- **Task 1 — Characterization (pin the as-built truth).** GREEN-on-arrival pins: queue/claim (`FOR UPDATE SKIP LOCKED`, `RUN_ALREADY_CLAIMED`), retry/resume/cancel transitions, idempotency replay/conflict, rate-limit tiers/headers, worker tenant-scope. Any RED = real surprise → STOP.
- **Task 2 — Duplicate-run safety (G1), RED-first.** Per DEC-D: enforce the decided policy on `start_run` (e.g., reject/return-existing when an active `QUEUED`/`RUNNING` run exists for the deal, or require an idempotency key for runs) with a race-safe guard (atomic conditional insert / partial unique index on active runs); tenant-scoped; leakage-safe code. Both repo twins (Postgres + in-memory).
- **Task 3 — Rate-limit store behind an interface (G2), RED-first.** Extract a `RateLimitStore` seam with the current in-memory impl as default; add the decided cross-replica impl (DEC-A) behind it (injectable, fake in tests). Pin that per-tenant limits hold across *simulated* replicas. No real Redis in tests.
- **Task 4 — Provider budgets (G3), RED-first — scoped per DEC-C.** Minimal per-tenant/provider budget check + safe `PROVIDER_BUDGET_EXCEEDED` denial + accounting seam (injected), or an explicit, documented deferral pin if DEC-C defers.
- **Task 5 — Cooperative mid-run cancellation (G7), RED-first.** Flip the carried Slice75b RED: orchestrator consults `cancel_requested_at` at step boundaries and stops with a safe `RUN_CANCELLED` ledger; bounded; API↔worker parity.
- **Task 6 — Idempotency TTL/cleanup (G6), RED-first — per DEC-E.** A tenant-safe expiry/cleanup for idempotency records (or a pinned, documented no-expiry decision).
- **Task 7 — Observability (G5), RED-first.** Safe-shape counters/signals for queue depth, retry/resume/cancel, rate-limit + budget denials; pinned via tests (no private content).
- **Task 8 — Docs + acceptance capstone.** Reconcile: strict-readiness post-Slice96 banner + a short `docs/architecture/` note recording the Redis/worker-role decision; a capstone proving "API and worker paths are consistent, tenant-scoped, retry-safe, and observable" over the decided surface. Doc-pin RED-first for new wording.

---

## 7. Decisions (LOCKED, 2026-07-07)

- **DEC-A (Redis / rate-limit store) — LOCKED: A1.** Redis-backed rate-limit counters behind an injectable `RateLimitStore` interface, with the current in-memory token-bucket impl kept as the **default**. Redis is used only for the ephemeral cross-replica counters (correct cluster-wide limits); all durable state stays in Postgres; dev/tests/single-process inject the in-memory (or a fake) store and need no Redis. Redis fail-mode is decided per-implementation task (fail-open vs fail-closed) and pinned by test.
- **DEC-B (queue model) — LOCKED (as proposed): keep Postgres-polling.** Durable, tenant-RLS, race-safe, fully tested; a real MQ is a larger operational lift not required for the acceptance. A lightweight NOTIFY/signal to reduce poll latency is out of scope this slice.
- **DEC-C (provider budgets scope) — LOCKED: C1 (minimal hard cap).** One per-tenant/provider budget with a hard cap, a safe `PROVIDER_BUDGET_EXCEEDED` denial, and an injected accounting seam. Deliberately minimal — deliver the named scope item without building a billing system.
- **DEC-D (duplicate-run policy) — LOCKED: D1.** At most one active (`QUEUED`/`RUNNING`) run per `(tenant, deal)`; a second returns `RUN_ALREADY_ACTIVE (409)`, enforced with a **race-safe partial unique index** on active runs (both repo twins). Leakage-safe code.
- **DEC-E (idempotency TTL) — LOCKED: TTL + cleanup.** Tenant-safe, config-driven TTL (default ~30 days) + periodic cleanup so idempotency records don't grow unbounded.
- **DEC-F (worker role) — LOCKED (as proposed): keep the single in-process `PipelineWorker`** started with the API when Postgres is configured, tenant-scoped by env. A dedicated worker deployment is documented as a future scaling lever; no behavior change this slice.
- **DEC-G (no new migration unless required) — LOCKED (as proposed):** add a durable table/index only where a locked decision needs it — **D1 active-run partial unique index**, **C1 budget table**, **E idempotency-cleanup** — each an explicit, tenant-RLS, idempotent migration in its own task.

---

## Status

**As-built; acceptance met (post-Slice96, 2026-07-08).** Slice96 shipped on worktree `IDIS-slice96` off `origin/main` @ `570c2558` (Phase I opener). The §7 decisions were LOCKED (DEC-A Redis-store-behind-injectable-`RateLimitStore` with in-memory default; DEC-B keep Postgres-polling queue; DEC-C minimal per-tenant/provider hard cap; DEC-D one-active-run-per-deal → `RUN_ALREADY_ACTIVE (409)`; DEC-E idempotency TTL + cleanup; DEC-F keep the single in-process worker; DEC-G migration only where a locked decision needs it) and all eight tasks landed task-by-task with STOP-for-approval:

- **Task 1** — characterization pins (GREEN-on-arrival); all five gap pins later flipped.
- **Task 2** — duplicate-run safety (DEC-D): partial unique index `ux_runs_one_active_per_deal` (migration 0023) + race-safe Postgres create + mirrored in-memory twin, second attempt returns `RUN_ALREADY_ACTIVE`.
- **Task 3** — cross-replica rate limiting (DEC-A): injectable `RateLimitStore` (in-memory default) + Redis-backed store (atomic Lua), the real path exercised locally and in CI (`redis:7-alpine`, `IDIS_TEST_REDIS_URL`).
- **Task 4** — provider budget hard cap (DEC-C): `BudgetedLLMClient` on every live Anthropic seam raising `PROVIDER_BUDGET_EXCEEDED` before spend, backed by a durable race-safe Postgres store (`provider_budget_usage`, migration 0024, RLS FORCE + WITH CHECK); in-memory only as a hermetic fallback.
- **Task 5** — idempotency TTL (DEC-E): `IDIS_IDEMPOTENCY_TTL_DAYS` (~30) + tenant-safe cleanup on both stores, wired opportunistically (throttled, best-effort) into `IdempotencyMiddleware`; replay/conflict unchanged.
- **Task 6** — cooperative cancellation (G7): the orchestrator consults `cancel_requested_at` at step boundaries and stops boundedly with a safe `RUN_CANCELLED` ledger, shared by API + worker.
- **Task 7** — safe-shape observability (G5): best-effort audit-sink signals (IDs/counts/codes only) for queue depth, claim, cancel stop, rate-limit + provider-budget denials, and idempotency cleanup.
- **Task 8** — docs reconciliation + acceptance capstone: this reconciliation, the post-Slice96 readiness banner, the `docs/architecture/slice96_runtime_reliability.md` note, and `test_slice96_acceptance_capstone` proving the acceptance — **API and worker paths are consistent, tenant-scoped, retry-safe, and observable** — by composing all six controls.

Postgres durable tests are env-gated but CI-wired under `IDIS_REQUIRE_POSTGRES=1`; the real-Redis test runs (not skips) when a Redis URL is set. No PR / merge / Slice97 without explicit instruction.
