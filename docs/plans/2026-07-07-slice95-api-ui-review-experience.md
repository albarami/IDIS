# Slice95 — API/UI Review Experience Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL — use `superpowers:executing-plans` to implement this plan task-by-task (TDD, per-task gate, STOP for approval). Decisions in §7 must be LOCKED before implementation.

**Goal:** Make the strict run and its investor package reviewable by a fund reviewer through the product API/UI — without exposing any private report content — and lock the UI↔backend contracts with tests. (Master plan §392; closes Phase H.)

**Architecture:** IDIS already ships a Next.js 15 review UI (`ui/`) over a tenant-scoped FastAPI backend whose review endpoints are safe-shape-enforced. Discovery (3 parallel Explore agents + hand-verification) shows the backend already serves ~all review needs and the UI already renders truth dashboard, claim/Sanad explorer, deliverables + manifest, run status, and audit. Slice95 is therefore a **complete-the-review-experience + lock-the-contracts** slice, **not** a rebuild: fill the genuine UI gaps (strict readiness, data-room upload, approval/override actions, run-monitor detail), close two small backend read-model gaps (reviewer readiness endpoint + run listing) and the debate safe-shape contract, and — the crux of the acceptance — add contract tests that lock the UI↔backend surface on both stacks.

**Tech Stack:** Backend — Python 3.11, FastAPI, Pydantic v2, pytest, ruff, mypy; safe-shape read models. Frontend — Next.js 15 (App Router) / React 18 / TypeScript strict / Tailwind / Vitest + React Testing Library; hand-written typed client (`ui/src/lib/idis.ts`) over a server proxy; OpenAPI spec `openapi/IDIS_OpenAPI_v6_3.yaml`. Injected fakes / stubs in tests — no real LLM. This slice adds no migration unless a locked decision requires durable state (default: none — see §7).

---

## 1. As-built map (verified — reuse-before-create)

Discovery via 3 Explore agents (backend API surface, `ui/` frontend, domain producers + privacy), with load-bearing claims hand-verified against the code.

### Backend API (all tenant-scoped via `RequireTenantContext`, safe-shape, audited; 16 routers registered in `main.py`)
- **Runs** (`routes/runs.py`): `POST /v1/deals/{deal_id}/runs` (start SNAPSHOT/FULL), `GET /v1/runs/{run_id}` → `RunStatus` (status, mode, per-step ledger `steps[]` with status/error_code/safe error_message/retry_count, `block_reason`), `POST /runs/{run_id}/{retry,resume,cancel}`. Step summaries sanitized (~908-968). **No run-LIST endpoint.**
- **Truth dashboard / claims** (`routes/claims.py`): `GET /v1/deals/{deal_id}/truth-dashboard` (summary by_grade / by_verdict / fatal_defects + paginated claims), `GET /v1/claims/{claim_id}`, `GET /v1/claims/{claim_id}/sanad` (transmission chain + computed grade), `GET /v1/deals/{deal_id}/claims`, PATCH/POST claim.
- **Evidence chain** (`routes/sanad.py`): `GET /v1/sanads/{sanad_id}`, `GET /v1/deals/{deal_id}/sanads`.
- **Defects** (`routes/defects.py`): list / get / create / `waive` / `cure`.
- **Documents / data room** (`routes/documents.py`, `routes/data_room_packages.py`): `POST /v1/deals/{deal_id}/documents/upload` (raw bytes, auto-ingest), safe document summaries + spans, `data-room-packages` CRUD (safe file ledger).
- **Deliverables / package** (`routes/deliverables.py`): `GET /v1/deals/{deal_id}/deliverables`, `GET /v1/deliverables/{id}/content` (bytes, BYOK), `GET /v1/deals/{deal_id}/runs/{run_id}/product-bundle/manifest` → `ProductBundleManifestReview` (sanitized artifact refs).
- **Human gates / overrides** (`routes/human_gates.py`, `routes/overrides.py`): `GET/POST /v1/deals/{deal_id}/human-gates` (APPROVE/REJECT/CORRECT + notes, actor tracked, audited), `POST /v1/deals/{deal_id}/overrides` (create; **no GET**).
- **Debate** (`routes/debate.py`): `POST /v1/deals/{deal_id}/debate`, `GET /v1/debate/{debate_id}` → `DebateSession{rounds: list[dict[str, Any]]}` — **see verified nuance below.**
- Enrichment, audit, health, tenancy routers also registered.

### UI (`ui/`, Next.js 15 / TS strict / Vitest + RTL)
- Hand-written typed client `ui/src/lib/idis.ts` (deals, claims + sanad, deliverables + manifest, runs, debate, humanGates, overrides, audit) over a **server-side proxy** (`app/api/idis/[...path]/route.ts`, HttpOnly-cookie API key, fail-closed 401). OpenAPI types generated to `src/lib/openapi.ts` via `npm run openapi:gen`.
- **Existing screens:** `/login`, `/deals`, `/deals/[dealId]/truth-dashboard`, `/deals/[dealId]/deliverables` (+ manifest-review modal), `/claims/[claimId]` (+ Sanad chain), `/runs`, `/runs/[runId]` (status 5s-poll + `DebateTranscript`), `/audit/events`.
- **Tests:** Vitest unit tests for `idis` / `requestId` / `devSession` / `debateNormalizer` / proxy + a `login` component test. **No contract tests against OpenAPI; no integration/e2e.**

### Domain producers + privacy (safe vs private)
- **Readiness:** `build_strict_full_live_readiness_report` / `StrictFullLiveReadinessReport` (`services/runs/strict_full_live.py`) — 6-value `StrictComponentStatus` enum, per-component operator-safe blocker_message, required env-var **names** (never values), file refs, `may_proceed`, blocker counts. **SAFE.**
- **Run status:** `RunStatus` / `RunStep` — step names/status enums, timestamps, stable error codes, retry counts, sanitized `result_summary`. **SAFE.**
- **Truth dashboard:** `TruthDashboard` / `TruthRow` — assertion summary (not raw claim text), verdict enum, claim/calc refs, sanad grade; audit appendix (refs only). **SAFE.**
- **Claims / sanad:** `ClaimResponse` (claim_text is the vetted assertion — safe), `SanadResponse` (transmission chain: actor types, verification methods, refs — no raw sources). **SAFE.**
- **Package:** `ProductBundleManifestReview` — artifact refs / sha256 / size, provenance whitelist-filtered (`_safe_provenance_value`); paths/keys/prompt-bodies/model-output dropped. **SAFE.**
- **Human gates / overrides:** gate status + action + operator justification. **SAFE.**
- **PRIVATE (never surface raw):** debate `DebateMessage.content` (raw agent reasoning), prompt bodies, raw model output, document paths/bytes, embedding vectors, enrichment payloads, env values, credentials.

### Verified nuance — the debate API is a stub, not a leak
Hand-verified: **nothing writes content into `debate_sessions.rounds`.** `start_debate` initializes `rounds=[]`, the table is referenced only by its migration (0009), and the API store (`_IN_MEMORY_DEBATES` / `debate_sessions`) is **not wired** to the real DEBATE-step orchestrator (which produces `DebateMessage.content` internally and never persists it here). So `GET /debate/{id}` currently returns empty rounds and the UI `debateNormalizer` is defensive over that. The `rounds: list[dict[str, Any]]` passthrough is **untyped** → a **latent** leak risk (not a current leak): if any future writer put raw `DebateMessage.content` into rounds, it would surface. (This corrects the domain agent's "currently exposed" over-statement.)

## 2. True gaps ("what's not")

Against the acceptance — **(A1)** a fund reviewer can inspect the package + evidence through the product API/UI, and **(A2)** UI/backend contracts are locked by tests:

- **G1 — Strict Readiness UI + read-model.** No reviewer-facing readiness surface. `build_strict_full_live_readiness_report` is **internal-only** (used at run admission, `routes/runs.py` ~233 / ~481); reviewers see only `block_reason` on `RunStatus`. Gap = a safe read endpoint + a UI screen (component modes + blocker details).
- **G2 — Data-Room Upload UI.** Backend `POST /documents/upload` exists; **no UI** to upload data-room documents.
- **G3 — Human approval/override actions UI.** Backend + read-only gate display exist; **no action UI** (approve/reject/correct buttons, override justification form).
- **G4 — Run monitor detail + run listing.** `/runs/[runId]` shows status + 5s poll but not component-mode / blocker detail; and there is **no `GET /deals/{deal_id}/runs` list**, so a reviewer must already know the run_id.
- **G5 — Debate transcript safe-shape contract.** The surface is an untyped stub (above). Gap = a typed safe-shape debate summary (roles / round count / verdict / stop_reason / ref counts / dissent summary), UI rendering only safe fields, and a test that a raw-content field can never serialize.
- **G6 (crux) — Contract tests locking UI↔backend.** **None exist.** A2 requires the UI↔backend contract be locked by tests on both stacks.

## 3. Approach

Reuse-first, safe-shape-first, TDD on both stacks. Sequence: close the small **backend** read-model gaps (readiness read endpoint, run list, debate safe-shape) with backend TDD; then complete the **UI** review screens with Vitest component TDD; then add the **contract tests** that lock the surface (A2) and a thin **acceptance** proof (A1). Because this spans two test stacks, each task states which gate runs — the Python gate (`pytest -q`, ruff, clean-cache mypy, forbidden-scan, `git diff --check`) and/or the `ui` gate (`npm run lint && npm run typecheck && npm run test && npm run build`).

## 4. Safety / strict boundaries
- **Safe-shape only** everywhere a reviewer can see: IDs / counts / categories / enums / refs / grades / sanitized summaries. **Never** raw claim text beyond the vetted assertion, debate `content`, prompt text, model output, paths, bytes, vectors, env values, or credentials.
- **Debate:** surface a safe summary only; add a test asserting no raw-content key is ever emitted; keep the untyped passthrough from regressing.
- **Tenant isolation** preserved (all reads scoped to `tenant_ctx`; cross-tenant → 200-empty or 404 per existing pattern). No new auth bypass; UI keeps the HttpOnly-cookie + server-proxy model.
- **No real LLM / injected fakes** in tests. No new migration unless a locked decision requires durable state (default: reuse existing tables / read-models).
- Fail-closed with static ledger-safe messages; cause chained via `from exc`; no raw exception text reaches the reviewer.

## 5. Risks
- **Two-stack scope creep** — 8 master-plan UI areas, but 4 already exist; without a scope lock this balloons. Mitigated by §7 decisions.
- **Contract-test strategy ambiguity** — "contracts locked by tests" can mean OpenAPI-schema conformance, generated-client parity, or e2e. Needs a locked definition (DEC-B).
- **Debate stub** — deciding whether to wire a real safe summary vs. only harden the passthrough contract. Wiring the real debate is larger than Slice95. Default: safe-shape contract + hardening only.
- **Readiness read-model** — exposing the internal report needs a safe projection (env-var names only, no file paths beyond safe refs) and probably a new GET endpoint.
- **UI ↔ OpenAPI drift** — the client is hand-written; contract tests should pin it to `openapi/IDIS_OpenAPI_v6_3.yaml` (or bump the spec in the same task that adds an endpoint).

## 6. Tasks (bite-sized, TDD) — PROPOSED sequence

> Each task: RED → verify RED → minimal GREEN → verify; per-task gate (Python and/or `ui`); STOP for approval. Task 1 is characterization (GREEN-on-arrival).

- **Task 1 — Characterization (pin the as-built truth).** Backend + UI tests pinning what already works (truth dashboard, claims/sanad, deliverables + manifest, run-status shapes; existing UI screens) and pinning the current gaps (no readiness endpoint, no run list, debate rounds empty). GREEN-on-arrival; any RED = a real as-built surprise → STOP.
- **Task 2 — Debate safe-shape contract + hardening (G5).** Type the `rounds` passthrough as a safe-shape summary model; test that raw-content keys can never serialize. Python gate.
- **Task 3 — Reviewer readiness read-model + endpoint (G1 backend).** Safe GET projecting `StrictFullLiveReadinessReport` (component modes + blockers, env-var names only). Python gate + OpenAPI bump.
- **Task 4 — Run listing endpoint (G4 backend).** `GET /deals/{deal_id}/runs` (safe, paginated). Python gate + OpenAPI bump.
- **Task 5 — Strict Readiness UI (G1 UI).** Screen rendering component modes + blocker details from Task 3. Vitest component TDD + `ui` gate.
- **Task 6 — Data-Room Upload UI (G2).** Upload screen over `POST /documents/upload` with safe result display. `ui` gate.
- **Task 7 — Human approval/override actions UI (G3).** Approve/reject/correct actions + override justification form over existing endpoints. `ui` gate.
- **Task 8 — Run monitor detail + listing UI (G4 UI).** Component-mode + blocker-detail rendering + run list. `ui` gate.
- **Task 9 — Contract tests locking UI↔backend (G6 crux, A2).** Per DEC-B: backend read-models validated against the OpenAPI spec + UI client/component tests consuming exactly those shapes + a safe-shape / no-private-key assertion. Both gates.
- **Task 10 — Acceptance proof (A1) + readiness-doc/plan reconciliation.** One path proving a reviewer can inspect package + evidence end-to-end via API/UI; reconcile the readiness doc + this plan to the post-Slice95 state.
- **Task 11 — Independent review + closeout (on explicit approval only).**

## 7. Decisions (LOCKED, 2026-07-07)

All six locked as the recommended defaults (user-confirmed).

- **DEC-A (scope split) — LOCKED.** One Slice95 (not split): keep the full acceptance (A1 + A2), treat the 4 already-shipped screens as reuse (characterize, don't rebuild), focus new work on G1-G6.
- **DEC-B (contract-test definition for A2) — LOCKED.** "Contracts locked" = (i) backend response-models validated against `openapi/IDIS_OpenAPI_v6_3.yaml` for the review read-models, (ii) UI client/component tests asserting they consume exactly those shapes, (iii) a safe-shape / no-private-key assertion.
- **DEC-C (debate scope) — LOCKED.** Safe-shape summary contract + hardening only; do **not** wire the real debate orchestrator into the debate API this slice.
- **DEC-D (readiness endpoint) — LOCKED.** Add a safe reviewer GET for the readiness report (new read endpoint, no migration); do **not** overload `RunStatus`.
- **DEC-E (test boundary) — LOCKED.** Injected fakes / stubs only, no real LLM; backend tests may use in-memory stores; `ui` tests use Vitest with fetch stubbed; no new migration.
- **DEC-F (OpenAPI source of truth) — LOCKED.** `openapi/IDIS_OpenAPI_v6_3.yaml` is the source of truth; bump it in the same task that adds an endpoint (Task 3/4).

---

## Status

**As-built (post-Slice95, 2026-07-07).** Slice95 shipped in worktree `IDIS-slice95` off `origin/main` @ `85ffa51`; §7 decisions were **LOCKED** (2026-07-07) and held. Tasks 1-10 are complete — each RED-first where it added behavior, with corrections folded in for the reviewer-schema `additionalProperties` drift, the run-list equal-`created_at` pagination drop, the `PaginatedRunList.items` required-drift, the `required_env_vars` `NAME=value` leak, the upload transport gap, and the `RunStatus` `CANCELLED` enum drift.

**Acceptance met.** *A1 — a fund reviewer can inspect the package + evidence through the product API/UI:* the backend serves the readiness (`GET /v1/strict-readiness` — config-only inspection, no live provider calls) and run-list (`GET /v1/deals/{deal_id}/runs`) read-models alongside the existing truth-dashboard, claim / Sanad, deliverables + manifest, run-status, human-gate, override, upload, and debate surfaces (all registered — pinned by `test_slice95_review_surface_characterization`), and the UI renders the matching strict-readiness / run-list / run-monitor / upload / approval / override screens. *A2 — the UI↔backend contracts are locked by tests:* `test_slice95_contract_lock` (static-vs-generated `required` / `properties` / `additionalProperties`; caveat — not full property schemas or enum values) on the backend and `slice95_contract.test.tsx` (client surface + safe-shape boundaries) on the UI. The readiness doc carries a post-Slice95 banner and this plan is reconciled — both pinned by `test_slice95_acceptance_docs`.

**Boundaries (honest scope).** Per DEC-E, tests use **injected fakes** only — no real LLM / no real Anthropic — and there is **no migration** (no new durable tables; the run / readiness / debate reads reuse existing storage). No private report or raw evidence text is exposed by any reviewer surface: env-var **names** not values, error **codes** not messages, ref **IDs** not claim text. No PR / merge / Slice96 without explicit instruction.
