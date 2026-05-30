# Slice76 — Strict Audit And Observability Baseline — Implementation Plan

> **For execution:** Use superpowers:executing-plans (or subagent-driven-development) to implement task-by-task. TDD per task: RED → verify red → minimal GREEN → verify → refactor → commit. Use superpowers:verification-before-completion before any status claim.

**Goal:** Make strict-run provenance and operator diagnostics durable and safe — authenticated actor identity in audit events, per-step strict provenance (component mode, env-source class, health/probe status, runtime-use status, output-visibility status), an enforced strict-mode audit-sink policy, and operator-safe failure summaries — with no placeholder actor identity and no secret/path/payload leakage.

**Architecture:** Reuse the existing `RunStep.result_summary` JSON surface for a structured `provenance` block sourced from the existing strict readiness models (`StrictComponentReadiness` / `StrictComponentInventory`); attribute audit actor identity from an authenticated/originating actor instead of the `"unknown"` placeholder; enforce a durable audit-sink policy in strict mode; and keep all new fields redaction-safe.

**Tech Stack:** Python 3.11+, Pydantic v2, FastAPI, SQLAlchemy, Alembic, pytest, ruff, mypy.

**Base:** branch `slice76-strict-audit-observability-baseline` @ `620051210ba0ab022c7f42d68defca363b943453` (main), worktree `C:/Projects/IDIS/IDIS-slice76`.

---

## 1. Skills / plugins / functions used
- **superpowers:using-git-worktrees** — created/validated the isolated worktree (done; HEAD `6200512`, clean).
- **superpowers:writing-plans** — this document.
- **superpowers:test-driven-development** — RED test design only (no implementation in this step).
- **superpowers:verification-before-completion** — gates all completion claims during execution.
- **GitHub plugin (`gh`)** — only if PR/CI status is needed later (not in planning).
- Repo tooling: `pytest`, `ruff format --check` / `ruff check`, `mypy --no-incremental`, `scripts/forbidden_scan.py`.

## 2. Verified facts from code (read at `6200512`)
- **Audit actor placeholder exists.** `src/idis/api/middleware/audit.py:107-116` sets `tenant_id="unknown"`, `actor_id="unknown"`, `actor_type="SERVICE"` as fallback when `request.state.tenant_context` is absent, then emits them in the event `actor` block (`:159-165`). Authenticated `/v1` mutations have a tenant context (RBAC requires it), but the placeholder is reachable for any emitter without one.
- **Audit idempotency-key already hashed.** `audit.py:185-188` emits `request.idempotency_key_sha256` only (Slice75B). Schema `schemas/audit_event.schema.json` enforces `^[0-9a-f]{64}$` + `additionalProperties:false` on `request`.
- **RunStep has no dedicated provenance fields.** `src/idis/models/run_step.py:212-242` — fields are `step_id, run_id, tenant_id, step_name, step_order, status, started_at, finished_at, error_code, error_message, retry_count, result_summary: dict[str, Any]`. `result_summary` is the natural carrier for a `provenance` block.
- **Strict readiness models already carry the five provenance dimensions.** `src/idis/services/runs/strict_full_live.py`:
  - `StrictComponentReadiness` (`:202-213`): `component_name, status (StrictComponentStatus), blocker_message, required_env_vars, required_services, evidence, may_proceed`.
  - `StrictComponentInventory` (`:216-229`): `component_name, exists_in_code, full_wired, config_present, health_check_status, output_visible, blocker, implementation_slice, evidence_files`.
  - `StrictComponentStatus` (`:191-199`): `live-wired-and-used | code-exists-but-not-wired | configured-but-failed-health-check | missing-credentials | missing-infrastructure | not-implemented` → maps to **component mode** + **runtime-use status**.
  - `StrictFullLiveReadinessReport` (`:232-245`): `env_sources: dict[str,str]` → **env-source class**; `component_inventory[].health_check_status` → **health/probe status**; `component_inventory[].output_visible` → **output-visibility status**.
- **Worker audit sink is durable + fail-closed.** `src/idis/pipeline/worker.py:347-354` `_default_worker_audit_sink()` returns `PostgresAuditSink()` and raises `WorkerAuditConfigurationError` on failure. The worker run context receives `audit_sink=service.audit_sink` (`:199`).
- **Strict admission already runs in API and worker FULL paths** (`runs.py:227`, `worker.py:168-189`) and produces the readiness report used above (Slice75A).
- **Leakage-safe test pattern exists** (Slice75B): `tests/test_slice75b_run_retry_resume_cancel_parity.py` asserts no DSN/`postgresql://`/`raw_text`/`object_key`/raw idempotency key in encoded steps — reusable shape for provenance leakage tests.
- **Postgres suite is skipped locally** (no `IDIS_DATABASE_ADMIN_URL`/`IDIS_DATABASE_URL`); runtime DB proof runs in CI `postgres-integration`.

## 3. Assumptions / design decisions (to confirm in Task 0 before RED)
- **A1 (provenance carrier):** Put strict provenance in `RunStep.result_summary["provenance"]` (JSON), NOT new model columns → **no DB migration** (run_steps already persists `result_summary` as JSON). Decision: minimal, additive, leakage-controlled. (Alternative — dedicated `provenance` column — deferred unless Task 0 shows result_summary is not durably persisted as JSON.)
- **A2 (actor attribution):** Audit `actor.actor_id` must never be `"unknown"`/placeholder on strict paths. For HTTP strict mutations, use the authenticated `TenantContext`. For worker/queued strict runs (no HTTP actor), attribute a **defined identity**: either the run's originating actor persisted at run creation, or a well-defined service principal (e.g., `SERVICE`/worker id) — **never `"unknown"`**. Task 0 must confirm whether `runs` persists an originating `actor_id`; if not, the service-principal route is the fallback (and a follow-up may add originating-actor persistence).
- **A3 (strict sink policy):** In strict mode the audit sink must be the durable Postgres sink and must fail closed; JSONL-only emission is not acceptable for strict runs. Task 0 to confirm exact current selection logic in `audit.py` / `create_app`.
- **A4 (provenance shape):** A typed Pydantic `StepProvenance` model with fields `component_mode`, `env_source_class`, `health_status`, `runtime_use_status`, `output_visibility_status` (all safe enums/strings), serialized into `result_summary["provenance"]`. Redaction-safe by construction (no raw env values, paths, or payloads — only classes/statuses).
- **A5 (operator-safe failure summaries):** Every strict blocker step carries a stable `error_code` + human-readable, secret-free `error_message` + provenance; reuse existing safe-summary helpers.

## 4. RED tests to add first (design only — not implemented in this step)
Grouped by acceptance criterion. Each gets a failing test before any production change.

**T1 — No placeholder actor identity in strict audit (Acceptance: "No placeholder actor identity in strict paths")**
- `tests/test_slice76_strict_audit_observability.py::test_strict_audit_event_has_no_placeholder_actor`
  - Build/emit a strict-path audit event with no HTTP tenant context (worker/queued strict run path).
  - Assert `event["actor"]["actor_id"] != "unknown"` and is a defined principal; `actor_type` is a valid enum; not the placeholder.
  - RED today: `audit.py` fallback yields `"unknown"`.
- `tests/test_api_audit_middleware.py::test_unknown_actor_not_emitted_for_strict_mutation` (companion) — strict mutation without resolvable actor must fail closed (no event with placeholder actor), mirroring the existing resource_id fail-closed.

**T2 — Step provenance present and complete (Acceptance: strict provenance durable)**
- `tests/test_slice76_strict_audit_observability.py::test_strict_step_provenance_contains_all_five_dimensions`
  - Drive a strict run/step (in-memory) and read the persisted `RunStep.result_summary["provenance"]`.
  - Assert keys: `component_mode`, `env_source_class`, `health_status`, `runtime_use_status`, `output_visibility_status` all present with safe values.
  - RED today: no provenance block written.
- `tests/test_slice76_strict_audit_observability.py::test_step_provenance_model_roundtrip` — `StepProvenance` Pydantic model validates/serializes (RED: model doesn't exist).

**T3 — Strict blocker has safe operator evidence (Acceptance: "Every strict blocker has safe operator evidence")**
- `tests/test_slice76_strict_audit_observability.py::test_strict_blocked_step_has_operator_safe_evidence`
  - For a strict-blocked run (reuse Slice75B `_BlockingStrictReport` pattern), assert the blocked step has stable `error_code`, non-empty safe `error_message`, and provenance — and contains no secrets/paths/payloads.

**T4 — Leakage safety on new fields (Acceptance: "No raw content, secrets, paths, object keys, or provider payloads")**
- `tests/test_slice76_strict_audit_observability.py::test_provenance_and_audit_are_leakage_safe`
  - Set `DATABASE_URL=SECRET_DSN`, seed provider-ish env; assert encoded step + emitted audit event contain none of: the DSN, `postgresql://`, raw env values, filesystem paths, object keys, provider payloads. Only classes/statuses appear.
- `tests/test_audit_event_validator.py::test_validator_rejects_raw_provider_payload_in_provenance` (schema/validator coverage) — extend REDACTION checks for provenance keys.

**T5 — Strict sink policy (Acceptance: durable audit in strict mode)**
- `tests/test_slice76_strict_audit_observability.py::test_strict_mode_requires_durable_audit_sink`
  - In strict mode with an unavailable/Jsonl-only sink, assert fail-closed (no silent JSONL fallback); with Postgres sink available, emission succeeds.
- Postgres SQL-shape/static coverage in `tests/test_slice75b_postgres_lifecycle_integration.py`-style file (`tests/test_slice76_postgres_audit_provenance_integration.py`), gated/skipped without DB env, asserting the persisted audit/provenance shape.

## 5. Exact files expected to change (subject to Task 0 confirmation)
- **Create:**
  - `tests/test_slice76_strict_audit_observability.py` (primary RED suite).
  - `tests/test_slice76_postgres_audit_provenance_integration.py` (Postgres static/skip coverage).
  - Possibly `src/idis/models/step_provenance.py` (or a `StepProvenance` model added to `run_step.py`) for A4.
- **Modify:**
  - `src/idis/api/middleware/audit.py` — remove/replace placeholder actor on strict paths; fail-closed when no defined actor.
  - `src/idis/services/runs/orchestrator.py` and/or `src/idis/services/runs/steps.py` — write `result_summary["provenance"]` from the strict readiness report per step.
  - `src/idis/services/runs/strict_full_live.py` — expose a safe per-component → provenance mapping helper (if not already derivable).
  - `src/idis/pipeline/worker.py` — attribute defined actor identity for worker/queued strict runs; align failure logging (folds in Slice75B deferred "misleading log").
  - `src/idis/validators/audit_event_validator.py` + `schemas/audit_event.schema.json` — allow/validate provenance + extend redaction (only if provenance lands in audit events; otherwise validator-only for step provenance).
  - Possibly `src/idis/audit/postgres_sink.py` (+ comment/rename for the `idempotency_key` column that stores the hash — Slice75B deferred item) and `src/idis/api/main.py` (strict sink selection) for A3.
- **Likely NOT changed:** retry/resume/cancel routes, ABAC/RBAC, migration `0018`, OpenAPI (unless a new audit field requires a contract bump — TBD in Task 0).

## 6. Migration / schema needs
- **Preferred (A1):** none — provenance rides in existing `result_summary` JSON. Confirm in Task 0 that `run_steps.result_summary` is persisted as JSON/JSONB durably (Postgres) and round-trips.
- **If a dedicated provenance column is chosen instead:** a new additive Alembic migration `00xx_run_steps_provenance.py` (nullable JSONB column, named-constraint-safe, safe downgrade) — deferred unless Task 0 justifies it.
- **Audit schema:** only if provenance is embedded in audit-event payloads (then a schema + validator update with redaction); step-level provenance alone needs no audit-schema change.

## 7. Local verification commands
```
$env:PYTHONPATH="C:\Projects\IDIS\IDIS-slice76\src"
# Focused RED/GREEN loop
python -m pytest tests/test_slice76_strict_audit_observability.py -q
# Affected existing suites
python -m pytest tests/test_api_audit_middleware.py tests/test_audit_event_validator.py tests/test_run_orchestrator_steps.py tests/test_run_execution_service.py tests/test_pipeline_worker.py tests/test_slice75b_run_retry_resume_cancel_parity.py tests/test_slice75a_canonical_api_worker_full_path_parity.py -q
# Contracts (parse only; do not Ruff-format JSON/YAML)
@'
import json,yaml
from pathlib import Path
json.loads(Path("schemas/audit_event.schema.json").read_text(encoding="utf-8"))
yaml.safe_load(Path("openapi/IDIS_OpenAPI_v6_3.yaml").read_text(encoding="utf-8"))
print("JSON/YAML parse: OK")
'@ | python -
# Lint / format (Python only) + types + forbidden scan
python -m ruff format --check src tests scripts
python -m ruff check src tests scripts
python -m mypy --no-incremental <touched source files>
python scripts/forbidden_scan.py --repo-root .
git diff --check
```
Postgres runtime proof (provenance/audit persistence, strict sink) is deferred to CI `postgres-integration` (skipped locally without DB env vars).

## 8. Explicit non-goals / deferred items
- **No provider/network/LLM calls; no real strict FULL execution; no `real_example`; no readiness/gate-state mutation; no VC-ready claim** (same discipline as Slice75x).
- **No retry/resume/cancel/ABAC/idempotency behavior changes** beyond what audit-actor/provenance require.
- **Originating-actor persistence on `runs`** (if not already present) may be a follow-up rather than in-scope, depending on Task 0 (A2).
- **Dedicated provenance DB column** deferred unless Task 0 disproves A1.
- Slice75B Minors NOT folded here: inline `import os` in `abac.py`, redundant exception-path write in `execution.py`, orchestrator private-global cancellation fallback (general follow-ups, not audit/observability scope).

## 9. Task sequence (bite-sized, TDD)
- **Task 0 (discovery/verify, no code):** confirm A1 (result_summary JSON persistence), A2 (runs originating actor?), A3 (current sink selection), and exact worker/orchestrator/lifecycle audit-actor handling. Adjust §5/§6 accordingly.
- **Task 1:** `StepProvenance` model — RED `test_step_provenance_model_roundtrip` → minimal model → green → commit.
- **Task 2:** Strict readiness → provenance mapping helper — RED → green → commit.
- **Task 3:** Write provenance into step `result_summary` on strict paths — RED `test_strict_step_provenance_contains_all_five_dimensions` → green → commit.
- **Task 4:** Strict blocker operator-safe evidence — RED T3 → green → commit.
- **Task 5:** Audit actor non-placeholder + fail-closed — RED T1 → green → commit.
- **Task 6:** Leakage-safety + validator/schema (if needed) — RED T4 → green → commit.
- **Task 7:** Strict sink policy — RED T5 → green → commit.
- **Task 8:** Full local verification gate + Postgres static/skip coverage; fold Slice75B deferred audit items (misleading log, audit column naming).

---

**Awaiting approval before implementation.** Per writing-plans, execution options after approval: (1) subagent-driven in this session, or (2) a separate session via executing-plans. No code edited, no commit, no PR.

---

## 10. Task 0 discovery results (verified at `6200512`)

### Verified facts
- **Runs persist NO originating actor.** `runs` table (`0009_...py:32-44`) columns: `run_id, tenant_id, deal_id, mode, status, started_at, finished_at, idempotency_key, created_at` (+ `source` JSONB via `0013`, `cancel_requested_at` via `0018`). `PostgresRunsRepository.create` INSERT (`runs.py:69-71`) has no actor. → originating authenticated actor is **not** recoverable from the run row.
- **Run-step audit emitter has NO actor block and skips validation.** `RunOrchestrator._emit_audit_event` (`orchestrator.py:2274-2297`) emits `{event_id, event_type, tenant_id, timestamp, details}` directly to `self._audit.emit()` — no `actor`/`request`/`resource`, not passed through `validate_audit_event`. This is the API+worker run-step ledger audit path. (Bigger gap than "unknown placeholder".)
- **HTTP audit actor placeholder confirmed.** `audit.py:107-116` fallback `actor_id="unknown"`; reachable only without `tenant_context` (authenticated `/v1` mutations have it).
- **Provenance carrier confirmed durable (NO migration).** `run_steps.result_summary` is `JSONB NOT NULL DEFAULT '{}'` (`0010_...py:39`); Postgres `create`/`update` use `CAST(:result_summary AS JSONB)` + `json.dumps` (`run_steps.py:189,204,290,302`); `_row_to_model` `json.loads` round-trips (`:319-333`); in-memory stores the model directly. → `result_summary["provenance"]` is sufficient.
- **Audit sink selection.** HTTP middleware: Postgres in-tx when `db_conn` present, else `JsonlFileAuditSink` (`audit.py:321-327`); no strict-mode gate. Worker: `_default_worker_audit_sink` → `PostgresAuditSink`, fail-closed (`worker.py:347-354`). `create_app` wires `AuditMiddleware(sink=..., postgres_sink=...)` (`main.py:139`).

### Updated decisions
- **D1 actor identity → migration REQUIRED.** Add nullable `created_by_actor_id` (+ `created_by_actor_type`) to `runs` (additive migration); capture `tenant_ctx.actor_id/actor_type` at run creation; preserve across retry/resume requeue; worker + orchestrator strict audit events read it. Genuinely system-originated runs use a **defined** service principal (e.g., `actor_type=SERVICE`, `actor_id="idis-worker"`), never `"unknown"`. Rationale: user-originated FULL runs must carry the real actor (acceptance: "authenticated actor identity"; "no placeholder actor identity in strict paths"); a service principal for user runs would erase accountability — so origin-actor persistence is in-scope, not deferred.
- **D2 provenance carrier → `result_summary["provenance"]`, NO migration** (A1 confirmed). Typed `StepProvenance` model serialized into the JSON block.
- **D3 strict sink policy.** Rule to test: when `is_strict_full_live_required()` is true, audit emission MUST use the durable Postgres sink and MUST fail closed (AUDIT_EMIT_FAILED / WorkerAuditConfigurationError) if it is unavailable; JSONL-only is rejected for strict runs. Non-strict behavior unchanged.

### Strict-path emitters to change
1. `src/idis/api/middleware/audit.py` — `_build_audit_event` (no-placeholder + strict fail-closed) and dispatch (strict durable-sink gate).
2. `src/idis/services/runs/orchestrator.py` — `_emit_audit_event` (add actor from run origin/service principal + provenance; conform shape).
3. `src/idis/services/runs/lifecycle.py` — `persist_failed_block` / `_persist_lifecycle_evidence` (provenance + operator-safe code/message on blocker steps).
4. `src/idis/api/routes/runs.py` (start-run actor capture; retry block path) and `src/idis/pipeline/worker.py` (`_persist_worker_preflight_block`, `_default_worker_audit_sink`, run-context actor).
5. `src/idis/persistence/repositories/runs.py` (+ new migration) — capture/read originating actor.
6. `src/idis/services/runs/strict_full_live.py` — safe per-component → `StepProvenance` mapping helper.

### Updated RED test list (design only)
- `test_runs_persist_originating_actor` (create captures actor; round-trips) + Postgres SQL-shape (`created_by_actor_id` column/insert).
- `test_retry_requeue_preserves_originating_actor`.
- `test_orchestrator_run_step_audit_event_has_authenticated_actor` (no missing/`unknown` actor).
- `test_worker_strict_audit_uses_origin_actor_or_defined_service_principal`.
- `test_strict_step_provenance_contains_all_five_dimensions` + `test_step_provenance_model_roundtrip`.
- `test_strict_blocked_step_has_operator_safe_evidence`.
- `test_strict_mode_requires_durable_audit_sink` (fail-closed; no JSONL-only).
- `test_provenance_and_audit_are_leakage_safe` + validator/schema redaction coverage.

### Migration need
- **YES** — one additive migration for `runs.created_by_actor_id` (+ optional `created_by_actor_type`), nullable, safe downgrade. Provenance needs **no** migration.

### Risks / open questions
- **R1 (scope):** orchestrator run-step events are non-v6.3-shaped and unvalidated; making them carry actor/provenance + validate may be larger than a "baseline" — confirm whether full v6.3 conformance of run.step.* events is in Slice76 or a follow-up.
- **R2:** retry/resume requeue must carry the original actor forward (not the retrying actor?) — product decision (recommend: record the actor who triggered each lifecycle action, and keep the run's original `created_by_actor_id`).
- **R3:** defined service-principal identity value/format needs sign-off.
- **R4:** Postgres-applied behavior (actor column, strict sink, provenance persistence) remains CI-only (`postgres-integration`); local proof is static + in-memory.
