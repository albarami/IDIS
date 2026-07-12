# Slice97 — Webhooks And Lifecycle Events (durable outbox, best-effort emit, signed dispatch)

Records the as-built Slice97 design (master plan §426, Phase I). Outbound lifecycle webhooks are
emitted for run claimed/completed/failed/cancelled, deliverable produced/failed, human-gate action
submitted, and data-room package created — durably queued, signed at dispatch time, retried with
the existing policy, and audited — without ever being able to break the mutation/audit they ride
alongside.

## Durable outbox (reuses migration 0003; hardened in 0025)

The delivery queue is the pre-existing `webhook_delivery_attempts` table (migration 0003) with its
`next_attempt_at` drain index. Slice97 wired it for the first time via
`persistence/repositories/webhook_outbox.py` (in-memory + Postgres twins). Migration 0025 adds the
`(webhook_id, event_id)` UNIQUE index — enqueue is `ON CONFLICT DO NOTHING`, so re-emission is
idempotent even under a race — and replaces the 0003 RLS policy with the canonical 0024 guarded
form (`FORCE ROW LEVEL SECURITY`; explicit `IS NOT NULL` guard on both `USING` and `WITH CHECK`),
so reads AND writes fail closed without a tenant context. Status lifecycle:
`pending` (retryable) → `succeeded` / `exhausted` (terminal); `delete_terminal` prunes tenant-wise.

## Safe payloads (acceptance A2)

`services/webhooks/events.py::build_webhook_event` is the redaction boundary. It composes the
shared value-level sanitizer (`services/webhooks/safe_payload.py`, the run-summary sanitizer lifted
out of `routes/runs.py` — one implementation, re-exported) with a fail-closed key-level check
against the audit validator's `REDACTION_BLOCKLIST`. Paths, URIs, base64 blobs, excerpts,
transcripts, exception text, and secret-like keys never reach the outbox row or the delivered body.

## Best-effort emit at every lifecycle point (acceptance A1)

`services/webhooks/lifecycle.py::notify_webhook_lifecycle` is the call-site wrapper wired into:
the shared `RunExecutionService.execute` path (claimed/completed/failed/cancelled — API and worker
identically), `_run_full_deliverables` (produced/failed), the human-gate action route, and the
data-room package route. It is best-effort by contract: the entire body swallows all exceptions
(mirroring `emit_run_signal`), because the durable mutation audit commits atomically with the
mutation — a throwing webhook enqueue inside that transaction would otherwise abort the mutation or
roll back the just-written audit row. A1 is proven by tests that force the webhook machinery to
raise and assert the mutation and its audit signal still commit.

**Delivery semantics (exact):** when the lifecycle emit runs on a caller connection (the route and
deliverables call sites, which pass `request.state.db_conn` / the run's `db_conn`), ALL webhook
work — the subscription lookup and the outbox enqueue — runs inside a **SAVEPOINT** on that
connection. Two consequences: (1) a SQL-level failure in the webhook path rolls back to the
savepoint, un-aborting the caller's transaction before the exception is swallowed, so the mutation
and its in-transaction audit still commit (A1 holds against database errors, not just Python
errors); (2) the enqueued outbox row commits and rolls back **with** the caller — a rolled-back
mutation can never leave a committed, dispatchable webhook behind (no ghost or premature events).
Standalone emits with no caller connection (the run-lifecycle path, which opens its own
tenant-scoped connection) are independent best-effort at-least-once: they commit in their own small
transaction. The dispatcher itself delivers at-least-once; consumers should treat deliveries as
retryable notifications, not exactly-once facts.

## Dispatcher: claim -> sign -> deliver -> retry (no double-delivery)

`services/webhooks/dispatcher.py::WebhookDispatcher.drain_once` claims due pending rows and holds
the claim lock across claim -> deliver -> mark on one tenant-scoped connection, so concurrent
drainers skip each other's rows (`FOR UPDATE SKIP LOCKED`) and never double-deliver. The webhook
secret is read ONLY at dispatch time via `load_webhook_dispatch_target` (RLS-scoped; never logged,
persisted, or delivered); the exact delivered bytes (`json.dumps(payload)`) are signed with the
existing HMAC-SHA256 `sign_webhook_payload`. Outcomes apply the existing `retry.py` policy
(10 attempts, exponential backoff capped at 4h inside a 24h window): 2xx → `succeeded`; failure →
rescheduled via `next_attempt_at`; exhaustion (or a missing/inactive webhook) → `exhausted`.
`WebhookDispatcherWorker` mirrors the pipeline worker (poll loop, `IDIS_WORKER_TENANT_IDS`
fail-safe scoping, errors swallowed) and starts with the app when Postgres is configured.

## Delivery audit + metrics

Every dispatched attempt emits a `webhook.delivery.succeeded` / `webhook.delivery.failed` audit
event (v6.3 shape, `validate_audit_event`-validated, actor `SERVICE/webhook-dispatcher`) whose
payload carries safe metadata only: `webhook_id`, `event_id`, `event_type`, `attempt_count`,
`status_code`, `outcome` — never the url, secret, headers, or body. Counters
`webhook_delivery_success_total` / `webhook_delivery_attempts_total` (labeled by `tenant_id`) match
the names the SLO dashboard queries; they live in the dependency-free in-process registry
`observability/metrics.py` with a Prometheus exposition renderer. A `/metrics` scrape endpoint is
not yet wired — exposing the registry is a documented follow-up, not part of this slice.
