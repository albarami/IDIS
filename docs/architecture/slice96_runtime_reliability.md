# Slice96 — Production Runtime Reliability (Redis / rate-limit + worker-role decision)

Records the architecture decisions taken in Slice96 (Master plan §412; opens Phase I). These make
long expensive runs reliable and controllable without a queue/worker rebuild. The API path and the
worker execute runs through the one shared `RunExecutionService` → `RunOrchestrator`, so lifecycle,
cancellation, budgets, and observability are identical on both.

## DEC-A — Rate-limit store (Redis behind an injectable seam)

The per-tenant token-bucket rate limiter was in-memory per-process, so with K8s `replicas: 3` each
pod held its own buckets (~3× the intended limit). Decision **A1**: extract a `RateLimitStore`
seam; keep the current in-memory token bucket as the **default**, and add a Redis-backed store
(atomic Lua) that enforces one shared per-tenant bucket across replicas when `IDIS_REDIS_URL` is
set. Redis holds only the ephemeral counters; all durable state stays in Postgres. Dev / tests /
single-process runs inject the in-memory (or a fake) store and need no Redis. CI provisions a real
`redis:7-alpine` service so the cross-replica integration test runs rather than silently skipping.

## DEC-B — Queue model (keep Postgres-polling)

The run queue stays the Postgres `runs` table drained by a polling worker with
`FOR UPDATE SKIP LOCKED`. It is durable, tenant-RLS, race-safe, and fully tested; a real message
queue is a larger operational lift not required for the acceptance. A NOTIFY/signal to reduce poll
latency is out of scope this slice.

## DEC-C — Provider budgets (durable, not in-memory)

The per-tenant/provider hard cap is durable and cross-replica: a `ProviderBudgetStore` seam with a
Postgres-backed default (`provider_budget_usage`, migration 0024, RLS) that atomically consumes
under the cap (`INSERT ... ON CONFLICT DO UPDATE ... WHERE used + amount <= cap`), raising
`PROVIDER_BUDGET_EXCEEDED` before any live provider request. The in-memory store is only a hermetic
dev/test fallback, never the production proof — an in-memory per-process cap has the same
multi-replica flaw as the old rate limiter. The unit is a live-call count (a coarse spend proxy),
deliberately not a billing system.

## DEC-F — Worker role (keep the single in-process worker)

The single in-process `PipelineWorker`, started with the API when Postgres is configured and
tenant-scoped by `IDIS_WORKER_TENANT_IDS`, is retained. A dedicated worker deployment is a
documented future scaling lever; no behavior change this slice.

## DEC-G — Migrations only where a locked decision needs one

New durable tables/indexes were added only where a locked decision required: the D1 active-run
partial unique index (migration 0023) and the C1 provider budget table (migration 0024).
Idempotency TTL (DEC-E) is computed from the existing `created_at` — no schema change.
