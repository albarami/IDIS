# Slice 97: Webhooks And Lifecycle Events — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: use `superpowers:executing-plans` to implement this plan **task-by-task**. Follow TDD (`superpowers:test-driven-development`): RED → verify RED → minimal GREEN → verify. **STOP for explicit approval after every task.** Never commit/push/PR/merge/cleanup or start the next task unless explicitly told.

**Goal:** Emit durable, safe, signed lifecycle-event webhooks for runs, deliverables, human gates, packages, and failures — without ever breaking audit.

**Master plan:** §426 (Slice 97, Phase I — second slice of Phase I). **Base:** `origin/main @ 3e56ecc`. **Worktree:** `C:/Projects/IDIS/IDIS-slice97`, branch `slice97-webhooks-lifecycle-events`.

**Status (post-Slice97):** As-built; acceptance met. Tasks 1–7 landed: safe payload builder (A2),
durable outbox + migration 0025 (guarded RLS), best-effort emitter, lifecycle wiring with the A1
proof, dispatcher (sign + deliver + retry, SKIP LOCKED), delivery audit + metrics, and this
docs/CI reconciliation with the acceptance capstone (`tests/test_slice97_acceptance_capstone.py`).

**Acceptance (verbatim from the master plan):**
- **A1** — No webhook creation can break audit after a successful mutation.
- **A2** — Events contain no raw private content or secrets.

**Architecture:** Build the *connective tissue* over existing but currently-unused building blocks; do **not** rebuild them. Migration `0003_webhooks_foundation.py` already ships `webhooks` + `webhook_delivery_attempts` (a durable, RLS-enforced outbox) with the exact drain index a poller wants; `signing.py`, `retry.py`, and `delivery.py` are complete but wired to nothing. Slice97 adds, in order: (1) a **safe payload builder** composing the existing sanitizers, (2) a durable **outbox repository** mirroring the Slice96 idempotency store (durable + cross-replica), (3) a **best-effort lifecycle emitter** fanning events out to subscribed webhooks, (4) **wiring** into the real lifecycle points, (5) a Postgres-polling **dispatcher** that signs + delivers + retries (mirroring the pipeline worker's `FOR UPDATE SKIP LOCKED` drain), and (6) delivery **audit + metrics**. The emitter and any enqueue at a fail-closed site are **best-effort — they swallow all exceptions** (mirroring `emit_run_signal`), so a webhook can never abort a mutation or roll back the transaction carrying the audit row (**A1**).

**Tech stack:** Python 3.11 (runs on 3.12 locally), Pydantic v2, FastAPI, SQLAlchemy/psycopg2 (Postgres + RLS), httpx, pytest, ruff, mypy. Windows dev.

---

## Reuse entry points (discovery pass — recorded per master-plan §530)

**Reusable as-is (do not rebuild):**
- **Durable outbox table (schema-only, currently dead):** `webhook_delivery_attempts` in `src/idis/persistence/migrations/versions/0003_webhooks_foundation.py:67-108` — columns `attempt_id, webhook_id (FK ON DELETE CASCADE), tenant_id, event_id, event_type, payload JSONB, attempt_count, next_attempt_at, last_attempt_at, last_error, status CHECK(pending|succeeded|failed|exhausted), created_at, updated_at`; drain index `ix_webhook_delivery_attempts_next_retry (next_attempt_at) WHERE status='pending' AND next_attempt_at IS NOT NULL` (`:97-101`); `ENABLE`+`FORCE` RLS (`:113-130`). Nothing in `src` writes it — this slice is its writer + drainer.
- **Subscriptions table:** `webhooks` (`0003:38-63`) — `webhook_id, tenant_id, url, events TEXT[], secret (nullable), active`; partial index `ix_webhooks_active (tenant_id, active) WHERE active`.
- **Signing:** `src/idis/services/webhooks/signing.py:40-123` — `sign_webhook_payload` / `verify_webhook_signature`, HMAC-SHA256 over `"{timestamp}.{body}"`, headers `X-IDIS-Webhook-Timestamp` / `X-IDIS-Webhook-Signature: sha256=<hex>`, constant-time compare.
- **Retry policy:** `src/idis/services/webhooks/retry.py:36-199` — `MAX_ATTEMPTS=10`, schedule `[60,120,240,480,960,1920,3840,7680,14400,14400]`, `compute_backoff_seconds`, `next_attempt_at`, `is_retry_exhausted`, 24h-window guard. `RetryState` maps 1:1 to the outbox columns.
- **Delivery:** `src/idis/services/webhooks/delivery.py:102-298` — `deliver_webhook` (httpx POST, 30s, OTel span, URL userinfo/query/fragment stripped `:51`, `authorization`/`x-api-key` headers dropped `:151`). Single attempt, no persistence, does not sign — the dispatcher supplies signed headers and persistence.
- **Subscription CRUD:** `WebhookService.create/get/list` `src/idis/services/webhooks/service.py:67-254` (secret-safe: never returned/selected).
- **Durable-store template:** `src/idis/idempotency/store.py` + `postgres_store.py` (SQLite+Postgres twins, `ScopeKey`, `ON CONFLICT`, `delete_expired`, fail-closed) — the pattern for the outbox repository.
- **Drainer template:** `src/idis/pipeline/worker.py:102-240` (`_poll_loop` → per-tenant `set_tenant_local` → claim → execute → commit; errors swallowed) + claim primitive `src/idis/persistence/repositories/runs.py:400-415` (`... FOR UPDATE SKIP LOCKED`).
- **Migration form to follow:** `0024_provider_budget_usage.py:43-61` (canonical RLS: `FORCE` + `DROP POLICY IF EXISTS` + `CREATE POLICY` with both `USING` and `WITH CHECK` on `NULLIF(current_setting('idis.tenant_id', true),'') IS NOT NULL AND tenant_id = NULLIF(...)::uuid`) and `0023:23-28` (partial unique index as a race-safe backstop). Note: the `0003` webhook RLS uses the older form **without** the `IS NOT NULL` guard — the new Slice97 migration must use the `0024` form.
- **Safe-payload sanitizers (compose, caller-side):**
  - **Value-level (strongest):** `_safe_public_run_summary` + helpers `src/idis/api/routes/runs.py:982-1092` (recursive allowlist-first/denylist-second; drops paths, base64, URIs, excerpts, transcripts, exception text; `SENSITIVE_SUMMARY_KEY_PARTS :846`, `SAFE_PUBLIC_STEP_ERROR_MESSAGE :845`). **Lift into a shared module** so both `routes/runs.py` and the webhook builder import it.
  - **Key-level (fail-closed):** `_check_redaction` + `REDACTION_BLOCKLIST` `src/idis/validators/audit_event_validator.py:73-164` (blocklisted secret-like keys → validation error). `webhook.` prefix + `webhook` resource type already whitelisted (`:26-66`).
  - **Test invariant:** `_assert_safe_shape` `tests/test_slice96_observability.py:59-72` (forbidden substrings, scalar-only, ≤128 chars, no paths).
  - **Caveat (memory `idis-observability-signal-not-sanitizer`):** `emit_run_signal` is an emit helper, **not** a sanitizer — it copies `details` verbatim. The webhook builder is the redaction boundary, not the emit path.

**Audit fail-closed ordering (governs A1):** middleware order (outermost→innermost) `RequestId → DBTransaction → Audit → OpenAPIValidation → Residency → RateLimit → RBAC → Idempotency → route` (`src/idis/api/main.py:83-100,138-155`). The whole mutation runs inside `call_next`; `AuditMiddleware` emits **after** it, via `emit_in_tx` on `request.state.db_conn` — so the mutation row and the audit row commit **atomically** (`db_tx.py:158` commit <500, `:179-205` rollback on ≥500/exception). Therefore a webhook enqueue that **throws** would either (a) inside the route → 5xx → rollback → mutation undone + audit never runs, or (b) after the audit emit but before commit → rollback destroys the just-written audit row. **Mitigation (mandatory):** every webhook enqueue at or around a mutation swallows all exceptions (mirror `emit_run_signal:60-63`).

**Lifecycle emission points to hook (from discovery):**

| Event | Where it fires | Signal already there |
|---|---|---|
| `run.claimed` (QUEUED→RUNNING) | `execution.py:81-86` | `emit_run_signal(RUN_CLAIMED)` — best-effort, ideal hook |
| `run.completed`/`.succeeded` | `routes/runs.py:351` → `_emit_run_completed_audit:1095-1128` | fail-closed audit |
| `run.failed` | `orchestrator._fail_step:2410`, `execution.py:100-103`, `worker.py:242-268` | strict step audit / terminal |
| `run.cancelled` (mid-run) | `orchestrator._cancelled_result:600-608` | `emit_run_signal(RUN_CANCELLED)` |
| `run.cancelled` (requested) | `routes/runs.py:464-492`, `lifecycle.request_cancel:45-56` | AuditMiddleware `cancelRun` |
| `deliverable.*` produced | `deliverables/generator.py:240,329,346` | `_emit_audit:1043` (fail-closed) |
| `human_gate.action.submitted` | `routes/human_gates.py:307-394` | AuditMiddleware `submitHumanGateAction` |
| `data_room_package.created` | `routes/data_room_packages.py` | AuditMiddleware `createDataRoomPackage` |

---

## Capability check (recorded per master-plan §538)

- **Superpowers skills to use:** `using-git-worktrees` (done — worktree created), `writing-plans` (this doc), `test-driven-development` (every task), `verification-before-completion` (every gate), `systematic-debugging` (any failure), `requesting-code-review` + `dispatching-parallel-agents` (independent reviews before PR), `finishing-a-development-branch` (only on explicit merge approval).
- **Postgres/RLS:** all outbox/dispatcher durable work follows the `0024` RLS migration form and is proven with **env-gated Postgres integration tests run under `IDIS_REQUIRE_POSTGRES=1` against a real Postgres locally before PR** (memory `idis-closeout-run-env-gated-postgres-tests`) — the full suite skips `*_postgres.py`.
- **GitHub tooling:** `gh` for PR/CI/merge/branch metadata; at PR time confirm CI's `postgres-integration` job actually **runs** (not skips) the new webhook durable tests (analogous to memory `slice96-pr-ci-redis-watch`).
- **PYTHONPATH discipline (memory `idis-pythonpath-pin-discipline`):** every pytest run pins `PYTHONPATH=C:/Projects/IDIS/IDIS-slice97/src`; every mypy run pins `MYPYPATH=...`; reports state the pin (stale `IDIS-slice40` `.pth` otherwise wins).
- **Durable + cross-replica (memory `idis-go-live-durable-cross-replica-state`):** the outbox is Postgres-backed and drained with `FOR UPDATE SKIP LOCKED`; the in-memory outbox is a dev/test fallback only, never the go-live store.
- **Wire-and-prove (memory `idis-wire-and-prove-real-path`):** the emitter must be wired into the real lifecycle points and proven through the real run/request flow, not only called directly by tests (Task 4 + capstone).
- **Intentionally skipped:** Supabase MCP (this is self-hosted Postgres via SQLAlchemy, not Supabase); browser/UI tools (no UI surface this slice); inbound-webhook/receiver tooling (out of scope).

---

## Out of scope (YAGNI — deferred, stated explicitly)

- Webhook subscription **CRUD expansion** (list/get/delete/secret-rotation endpoints). `WebhookService` has `get`/`list` but they are unrouted; not required to emit lifecycle events. Defer to a later slice.
- **Inbound** webhooks / receivers.
- Per-event delivery **UI/dashboard** (the SLO dashboard already references the metrics we will emit).
- NOTIFY/LISTEN low-latency dispatch (polling is sufficient for acceptance; matches DEC-B from Slice96).

---

## Tasks

Each task is RED-first, minimal, and ends at a **STOP** for explicit approval. Per-task gate (run before reporting): import proof · focused tests · full `PYTHONPATH`-pinned `pytest -q` · `ruff format --check .` · `ruff check .` · clean-cache `mypy src/idis --no-incremental` (MYPYPATH pinned) · `scripts/forbidden_scan.py --repo-root .` · `git diff --check`.

### Task 1 — Safe webhook event payload schema + builder (satisfies A2 at the source)

**Files:**
- Create `src/idis/services/webhooks/safe_payload.py` — lift/extract the value-level sanitizer helpers (`_safe_public_run_summary` family) from `routes/runs.py` into a shared module (re-export from `routes/runs.py` to avoid behavior drift — keep one implementation).
- Create `src/idis/services/webhooks/events.py` — a versioned `WebhookEvent` envelope (`schema_version`, `event_id`, `event_type`, `occurred_at`, `tenant_id`, `resource_type`, `resource_id`, `data: dict`) + `build_webhook_event(...)` that composes: assemble from safe fields only → project `data` through `safe_payload` → assert no blocklisted keys via a `_check_redaction`-style pass (raise `WebhookPayloadError` if violated).
- Test: `tests/test_slice97_webhook_payload_safety.py`.

**RED test intent:** a payload carrying a sensitive key (`transcript`, `secret`, `api_key`), a raw path, a `://` URI, a base64 blob, or exception text is stripped or rejected; a safe event (IDs/enums/counts/sha256) passes and round-trips as deterministic JSON; a shared `_assert_safe_shape`-style invariant holds over the built envelope. Verify RED (module/builder absent), then minimal GREEN, then full gate. **STOP.**

### Task 2 — Durable outbox repository + migration (durable + cross-replica)

**Files:**
- Create `src/idis/persistence/repositories/webhook_outbox.py` — `WebhookOutboxRepository` protocol + `InMemoryWebhookOutboxRepository` (dev/test) + `PostgresWebhookOutboxRepository`: `enqueue(*, webhook_id, tenant_id, event_id, event_type, payload)` **idempotent** on `(webhook_id, event_id)`; `claim_due(*, limit, now)` → `... WHERE status='pending' AND next_attempt_at <= now ORDER BY next_attempt_at LIMIT :n FOR UPDATE SKIP LOCKED`; `mark_succeeded`, `mark_failed(*, next_attempt_at, last_error)`, `mark_exhausted`, `delete_terminal(*, tenant_id, older_than)`.
- Create migration `0025_webhook_outbox_enqueue_idempotency.py` (next after the current head `0024_provider_budget_usage.py`; `down_revision="0024"`) — partial unique index `ux_webhook_outbox_event ON webhook_delivery_attempts(webhook_id, event_id)`, in the **0024 RLS form**; no new table (0003 already has it).
- Tests: `tests/test_slice97_webhook_outbox.py` (in-memory + logic) and `tests/test_slice97_webhook_outbox_postgres.py` (env-gated, `IDIS_REQUIRE_POSTGRES=1`).

**RED test intent:** duplicate `enqueue` of the same `(webhook_id, event_id)` yields one row (idempotent, race-safe via the unique index); `claim_due` returns only due `pending` rows in `next_attempt_at` order and is concurrency-safe (`SKIP LOCKED`); cross-tenant rows are invisible under RLS and a no-tenant write is blocked (`WITH CHECK`); `delete_terminal` reclaims only terminal rows for the tenant. **STOP.**

### Task 3 — Best-effort lifecycle emitter / fan-out

**Files:**
- Create `src/idis/services/webhooks/emitter.py` — `emit_lifecycle_event(*, tenant_id, event_type, resource_type, resource_id, data, outbox, webhook_service)`: list active webhooks for `tenant_id` whose `events[]` contains `event_type`, build the safe envelope (Task 1) per subscription, `enqueue` (Task 2). **Entire body wrapped in `try/except Exception` → log + return** (never raises; mirror `emit_run_signal:60-63`).
- Test: `tests/test_slice97_webhook_emitter.py`.

**RED test intent:** a subscription matching the event type gets exactly one outbox row with a safe payload; a non-matching event enqueues nothing; matching is tenant-scoped; **a raising outbox/service makes the emitter swallow and return (asserts it does NOT raise)**; zero subscriptions → no-op. **STOP.**

### Task 4 — Wire the emitter into lifecycle points + prove audit-safety (satisfies A1)

**Files (modify):** wire best-effort `emit_lifecycle_event` at the discovery-identified points — `execution.py` (claimed, failed), `routes/runs.py:351` (completed), `orchestrator.py:_cancelled_result` (cancelled), `deliverables/generator.py` (deliverable produced/failed), `routes/human_gates.py` (gate action), `routes/data_room_packages.py` (package created). Construct/inject the outbox + emitter in `api/main.py` and the worker wiring (durable Postgres outbox when configured, in-memory otherwise).
- Test: `tests/test_slice97_lifecycle_webhook_wiring.py`.

**RED test intent:** each lifecycle point, driven through the **real** flow (`RunExecutionService.execute`, the real routes via `TestClient`, the worker), enqueues the correct event to the outbox. **The A1 test:** inject a webhook emitter that **raises**, perform a successful mutation (e.g. `POST /v1/deals` or a run completion) → assert the response is still 2xx **and** the audit row is present (mutation + audit committed, not rolled back). API/worker parity via the shared execution path. **STOP.**

### Task 5 — Dispatcher / drainer (sign + deliver + retry)

**Files:**
- Create `src/idis/services/webhooks/dispatcher.py` — `WebhookDispatcher.drain_once(...)`: `claim_due` → load the subscription **secret** (dispatch-time only, via a dedicated secret-load path; never logged/returned elsewhere) → `sign_webhook_payload` → `deliver_webhook` → on 2xx `mark_succeeded`; on failure compute `next_attempt_at`/`is_retry_exhausted` (retry.py) → `mark_failed`/`mark_exhausted`. Plus `WebhookDispatcherWorker` mirroring `PipelineWorker` (`_poll_loop`, per-tenant `set_tenant_local`, errors swallowed, tenant-scoped by `IDIS_WORKER_TENANT_IDS`), started with the app when Postgres is configured.
- Tests: `tests/test_slice97_webhook_dispatcher.py` (fake delivery) + `tests/test_slice97_webhook_dispatcher_postgres.py` (env-gated).

**RED test intent:** a due row is delivered with a **verifiable** HMAC signature (round-trip `verify_webhook_signature` with the stored secret) and marked `succeeded`; a failing delivery sets `next_attempt_at` per the schedule and increments `attempt_count`; the 10th failure marks `exhausted`; concurrent drainers do not double-deliver (`SKIP LOCKED`); the secret never appears in spans/logs/audit. **STOP.**

### Task 6 — Delivery audit metadata + metrics

**Files (modify `dispatcher.py`):** emit `webhook.delivery.succeeded` / `webhook.delivery.failed` audit events (already taxonomy-whitelisted) carrying only **safe metadata** — `webhook_id, event_id, event_type, attempt_count, status_code, outcome` (**no** url, secret, or body — build via the Task 1 safe path). Emit Prometheus `webhook_delivery_success_total` / `webhook_delivery_attempts_total` (already referenced by `monitoring/slo_dashboard.py:525-533`).
- Test: `tests/test_slice97_webhook_delivery_audit.py`.

**RED test intent:** a successful delivery emits `webhook.delivery.succeeded` that passes `validate_audit_event` **and** the `_assert_safe_shape` invariant; a failure emits `webhook.delivery.failed`; the metrics increment; the audit payload contains no url/secret/body/path. **STOP.**

### Task 7 — Docs reconciliation + acceptance capstone + CI wiring

**Files:**
- Create `docs/architecture/slice97_webhooks_lifecycle.md` — records the outbox/dispatcher/emitter design, the best-effort A1 discipline, and the safe-payload composition (A2).
- Reconcile `docs/11_IDIS_Traceability_Matrix_v6_3.md` WH-001 `⏳ Planned` → delivered (with the emitted `webhook.delivery.*` events); update this plan's status.
- `.github/workflows/ci.yml` — add `tests/test_slice97_webhook_outbox_postgres.py` and `tests/test_slice97_webhook_dispatcher_postgres.py` to the `postgres-integration` job list (so they **run** under `IDIS_REQUIRE_POSTGRES=1`, not skip).
- Test: `tests/test_slice97_acceptance_capstone.py` — end-to-end: subscribe → real lifecycle event → outbox enqueue (safe payload) → dispatch → signed delivery → `succeeded` audit; plus **A1** (throwing emitter never breaks audit) and **A2** (no private content anywhere in the outbox row or delivered body) composed on the real path.

**RED test intent:** the capstone proves A1 + A2 + durable delivery compose end-to-end; the docs pins lock the reconciliation; CI wiring pin asserts both new `*_postgres.py` files appear in the `postgres-integration` command. **STOP.**

---

## Verification / closeout (after Task 7, on explicit approval only)

1. **Fresh closeout gate:** full `PYTHONPATH`-pinned `pytest -q` with `IDIS_TEST_REDIS_URL` set (real Redis test runs, not skips); **bootstrap a disposable Postgres and run the `postgres-integration` set under `IDIS_REQUIRE_POSTGRES=1` locally** (webhook outbox + dispatcher durable tests) — do **not** trust the skipped-Postgres full suite (memory `idis-closeout-run-env-gated-postgres-tests`); `mypy --no-incremental` (MYPYPATH pinned); `ruff format --check`; `ruff check`; `forbidden_scan`; `git diff --check`; exact footprint census.
2. **Independent reviews** before PR: Reviewer A (runtime correctness / real-path: A1 audit-safety, outbox RLS + idempotent enqueue, dispatcher signing + retry + `SKIP LOCKED`, best-effort emitter); Reviewer B (docs/CI/footprint honesty: A2 no-private-content, traceability reconciliation, CI runs the new durable tests, no fake/stub overclaim). Any Important/Critical → stop, report, no PR.
3. **PR flow** (only if clean/minor-only): stage by explicit path → commit → push → PR against `main` → verify diff → watch CI to terminal, confirming the Redis test runs+passes and `postgres-integration` **runs** the new webhook durable tests under `IDIS_REQUIRE_POSTGRES=1`.

**Explicit stop points:** after each Task (1–7) STOP for approval; STOP before the closeout gate; STOP before independent reviews; STOP before commit/push/PR; STOP after the PR/CI report (no merge/cleanup without separate explicit approval).
