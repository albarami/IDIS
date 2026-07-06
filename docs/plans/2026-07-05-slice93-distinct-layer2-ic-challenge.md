# Slice 93 — Distinct Layer 2 IC Challenge — Implementation Plan

> **For Claude / agentic workers:** REQUIRED SUB-SKILL: `superpowers:executing-plans` (or `subagent-driven-development`) task-by-task. Per task: `test-driven-development` (RED → verify red → minimal GREEN → verify), `verification-before-completion` before any status claim, `using-git-worktrees` already done (`C:\Projects\IDIS\IDIS-slice93`, branch `slice93-distinct-layer2-ic-challenge`, base `origin/main` @ `bcdd5d8`), `finishing-a-development-branch` before commit/PR. **Reuse before create — a live Layer-2 challenge already exists; EXTEND it, do not rewrite. STOP for approval after each task.** Confirm the §9 decisions — especially **DEC-A scope breadth/phasing**, **DEC-B durability shape**, **DEC-C IC-memo/QA-brief mechanics** — BEFORE any implementation task. This is **discovery + planning only** — no implementation yet.

**Goal (master plan §357):** Implement a real second IC challenge layer, not readiness metadata.

**Scope (master plan):** IC advocate/challenger/arbiter roles; stage-specific weighting; live agents, rounds, challenge categories, dissent, NFF/Muḥāsabah validation; consume VEP plus enrichment/graph/RAG/calc context.

**Acceptance (master plan):** Layer 2 outputs are distinct, durable, live-provider-proven, and visible in IC memo/QA brief.

**Architecture:** A live Layer-2 IC challenge **already exists and is FULL-wired** (Slice65, commit `cb4c55c`): `LAYER2_IC_CHALLENGE` (FULL step 24) runs a genuine strict-live challenger→arbiter pass over the deal's claims/calcs + graph/RAG/enrichment refs, with NFF/Muḥāsabah validation, private-leak rejection, deterministic non-strict fallback, and safe product-bundle visibility. So "not readiness metadata" is **already true** for what exists — but it is **narrow**. Slice93's substance is closing the acceptance gaps against the existing service: (1) **durability** (no Postgres table today — mirror the Slice92 Layer-1 template), (2) **IC-memo/QA-brief visibility** (Layer-2 findings surface nowhere in memo/QA today), (3) **live-provider proof** (no Layer-2 provenance builder → strict still blocks on "runtime proof"), plus the scope enrichments — advocate role, challenge categories, stage weighting, dissent, and actually *consuming* the VEP (currently recorded as `vep_ref_ids` but not fed into the challenge). This is a **broad slice**; DEC-A phases it.

**Tech Stack:** Python 3.11, Pydantic v2, Postgres (+RLS, versioned migrations), Anthropic live provider, pytest, ruff, mypy. Injected fakes in tests — no real LLM.

---

## 1. Baseline (this worktree, verified)

- Worktree `C:\Projects\IDIS\IDIS-slice93`, branch `slice93-distinct-layer2-ic-challenge`, base `origin/main` @ `bcdd5d8123c25ba1dd127eadeb15f5d951517f6b` (post-Slice92). Imports resolve here.
- Baseline gates green: `ruff format --check`/`check` clean (805) · clean-cache `mypy src/idis` **Success, 400** · Layer-2/NFF/Muḥāsabah smoke **47 passed**.
- Zero Slice93 artifacts before this doc; dirty baseline untouched.

## 2. Already built (hand-verified — reuse/EXTEND, do not recreate)

- **Live Layer-2 IC challenge service** (`services/runs/layer2_ic_challenge.py`): `RunLayer2ICChallengeService.run(tenant_id, deal_id, run_id, debate_summary, created_claim_ids, calc_ids, graph_evidence, rag_evidence, enrichment_refs)` — **no `vep` param** (verified). Strict path: `_challenger_runner.run({"role":"ic_challenger",...})` → `_arbiter_runner.run({"role":"ic_arbiter",...})` (2 roles, **no advocate**). Non-strict = deterministic single-finding fallback. Fail-closed codes: `LAYER1_DEBATE_MISSING`, `LAYER2_NO_REFERENCED_EVIDENCE`, `LAYER2_MISSING_LIVE_MODEL_CONFIG`, `LAYER2_LIVE_RUNNER_MISSING`, `LAYER2_INVALID_JSON`, `LAYER2_PRIVATE_DATA_LEAK`. `build_live_layer2_ic_runners(challenger_client, arbiter_client, prompts)`; `Layer2ICLLMRunner`; `_missing_layer2_model_env` (IDIS_DEBATE_BACKEND=anthropic + ANTHROPIC_API_KEY + DEBATE_DEFAULT/ARBITER models).
- **Models** (`models/layer2_ic_challenge.py`): `Layer2ICChallengeFinding` (`finding_type: str`, `severity: str` — **free strings, no enum**; claim/calc/graph/rag/enrichment ref ids; ≥1 claim-or-calc required), `Layer2ICChallengeRecord` (+ `unresolved_question_count`, `muhasabah_passed`), `Layer2ICChallengeShell`, `to_run_step_summary()` (safe: ids/counts + `by_finding_type`/`by_severity` histograms), namespace UUID + `deterministic_layer2_ic_challenge_id`.
- **Wiring**: `runs.py:_run_full_layer2_ic_challenge` (kw incl. Slice92 `vep_package_ids`; builds live Anthropic runners; **appends `vep_ref_ids` post-run** — records, does not consume); `orchestrator.py:_execute_layer2_ic_challenge` (step 24, threads debate_summary + claim/calc ids + graph/rag/enrichment + `layer1_persistence.package_ids`). FULL_ONLY + IMPLEMENTED.
- **Strict readiness component EXISTS** (`strict_full_live.py:834 _debate_layer_2_ic_challenge`, in `REQUIRED_STRICT_COMPONENTS` line 99, called line 502): MISSING_CREDENTIALS → CODE_EXISTS_BUT_NOT_WIRED → and, even when wired, blocks on **"strict readiness requires runtime proof that challenger and arbiter model calls executed successfully"** (line 867). **No Layer-2 provenance builder exists** (cf. `_build_debate_provenance`/`_build_analysis_provenance`/`_build_scoring_provenance`).
- **Product-bundle visibility**: `_layer2_package` + `evidence_index.layer2_evidence` + run_summary `layer2_status`/`layer2_challenge_ids`/`layer2_finding_count`/`layer2_unresolved_question_count`.
- **Reusable machinery** (proven): debate roles (`debate/roles/*` — advocate/arbiter/sanad_breaker/contradiction_finder/risk_officer), `debate/orchestrator.py`, `deterministic_id`/`deterministic_timestamp` (`debate/roles/base.py`), `MuhasabahGate` (`debate/muhasabah_gate.py`), NFF (`validators/no_free_facts.py` + `analysis/no_free_facts.py`), `validators/muhasabah.py`, **stage weighting** (`analysis/scoring/models.py` Stage enum + `analysis/scoring/stage_packs.py` per-stage weights summing to 1.0), `services/llm_model_health.py` (LlmModelRole/_ROLE_SPECS), **Slice92 durability template** (`persistence/migrations/versions/0021_layer1_evidence_durability.py` + `models/layer1_durability.py` + `repositories/layer1_evidence.py` + orchestrator `_persist_layer1_*` fail-closed helpers + `steps.py` selector binding). Latest migration **0021** (next: 0022).

## 3. True gaps (hand-verified, with evidence)

- **G1 — Not durable.** Zero `layer2`/`ic_challenge` references under `src/idis/persistence/`; latest migration 0021. Layer-2 output lives only in `run_steps.result_summary` JSONB + the product-bundle export — un-queryable, not first-class. **Acceptance: "durable."**
- **G2 — Not visible in IC memo / QA brief.** `memo.py`/`qa_brief.py` contain zero `layer2`/`ic_challenge` refs (verified). IC-memo `DissentSection` is fed from Layer-1 debate only; QA-brief items are agent-`questions_for_founder`-driven only. **Acceptance: "visible in IC memo/QA brief."**
- **G3 — No live-provider proof surface.** Live runners execute, but there is no `_build_layer2_provenance`-style artifact, so strict `_debate_layer_2_ic_challenge` stays blocked on "runtime proof." **Acceptance: "live-provider-proven."**
- **G4 — No advocate role.** Only `ic_challenger` + `ic_arbiter`. **Scope: "advocate/challenger/arbiter roles."**
- **G5 — No challenge categories.** `finding_type`/`severity` are free strings; no taxonomy/enum, no per-category routing. **Scope: "challenge categories."**
- **G6 — No stage-specific weighting.** No `stage` input to Layer-2; `stage_packs` exists to mirror but is unused here. **Scope: "stage-specific weighting."**
- **G7 — No dissent computation.** No dissent field/logic in Layer-2 (Layer-1's `PositionSnapshot`/`dissent_preserved`/`stable_dissent_rounds` are not paralleled). **Scope: "dissent."**
- **G8 — VEP recorded, not consumed.** `service.run` has no `vep` param; `vep_ref_ids` are appended after the run. **Scope: "consume VEP."**
- **G9 — Stale readiness census.** `strict_full_live_readiness.md:49` still says Layer-2 "not-implemented" (frozen Slice-53 row); needs a post-Slice93 banner.

## 4. Design / approach

1. **Extend the existing service — never rewrite.** Every gap attaches to `RunLayer2ICChallengeService`/its models/prompts and the existing step wiring. Keep all current fail-closed codes and the safe-refs-only posture.
2. **Durability = the Slice92 template exactly** (DEC-B): migration 0022 (`layer2_ic_challenges` + `layer2_ic_findings`, tenant RLS NULLIF, **composite PK incl. tenant+run** per the Slice92 finding-key lesson, deterministic idempotent upserts) + safe-shape rows (`models/layer2_durability.py`) + Postgres/InMemory twin repos + an orchestrator `_persist_layer2_*` helper that runs after the step's status check and fails closed (`LAYER2_PERSISTENCE_FAILED`), InMemory twin keeping non-DB runs green. **Discovery gate:** verify the actual shapes of `layer2_challenge_id`/`finding_id` (bare-UUID5 vs prefixed like Slice92's `finding-<hex>`) before choosing column types — Slice92 shipped a schema that rejected production ids until fixed.
3. **IC-memo/QA-brief visibility = safe-fields-only feed** (DEC-C): thread the safe `layer2_evidence` (ids/counts/categories/unresolved questions — never claim text/transcripts) into the memo builder (a Layer-2 challenge/dissent section) and the QA-brief builder (Layer-2 unresolved questions as grounded QA items), mirroring how enrichment/graph/RAG already surface. No-Free-Facts stays enforced on any deliverable text.
4. **Live-provider proof = a Layer-2 provenance builder** (DEC-F) modelled on `_build_debate_provenance`: safe model/prompt identifiers + a runtime signal that challenger+arbiter live calls executed; wired so strict `_debate_layer_2_ic_challenge` can clear. Existing `STRICT_LIVE_*`-style codes stay unchanged unless DEC-F adds a Layer-2 one.
5. **Scope enrichments** (advocate role, challenge categories, stage weighting, dissent, VEP consumption) reuse the debate machinery + `stage_packs` + `PositionSnapshot`; each is additive and independently testable. DEC-A decides which land this slice vs a follow-on.
6. **Safety/strict unchanged:** all new persistence/provenance/deliverable feeds carry safe fields only (ids/enums/counts); strict fail-closed posture preserved; deterministic ids/timestamps (no wall-clock/random); no real LLM in tests (inject fakes; env-gated Postgres per convention).

## 5. Safety / strict boundaries

- Safe shapes only in durable rows, provenance, and memo/QA feeds — no claim text, transcripts, prompt text, or vectors (the Layer-2 service already rejects private leakage; keep that invariant on every new surface).
- RLS NULLIF + tenant/run-scoped composite keys on new tables; fail-closed on empty tenant.
- Deterministic ids/timestamps; determinism in any sorted output.
- Existing Layer-2 fail-closed codes unchanged; new persistence blocker fail-closed with a **static, ledger-safe message** (the Slice92 lesson — no raw exception text into `step.error_message`).
- No real Anthropic calls in tests; Postgres tests env-gated (CI `postgres-integration`).

## 6. Verification gate (every task)

Import proof · `pytest` (task tests + relevant regression) · `ruff format --check .` · `ruff check .` · clean-cache `mypy src/idis` · `scripts/forbidden_scan.py --repo-root .` · `git diff --check`. Closeout: full `python -m pytest -q` + independent review.

## 7. Risks

- **Scope size** — the master-plan scope lists 6+ enrichments plus 4 acceptance properties; without DEC-A phasing this balloons. Anchor on the four acceptance properties first.
- **ID-shape schema mismatch** (Slice92 repeat) — verify `challenge_id`/`finding_id` shapes before the migration.
- **Deliverable NFF** — Layer-2 text in memo/QA must pass the existing No-Free-Facts deliverable validator; feed ids/refs, not prose.
- **Strict provenance** — getting `_debate_layer_2_ic_challenge` to clear must not weaken the "runtime proof" bar; prove real challenger+arbiter execution, don't stub the gate green.
- **Determinism drift** in histograms/category weighting → sort + fixed rounding.
- **Over-reach into scoring** — Layer-2 must not silently alter the composite score; stage-weighting here is for challenge emphasis, not the scorecard (confirm boundary in DEC-E).

## 8. Tasks (bite-sized, TDD)

> Acceptance-critical spine: **T1, T2–T3 (durable), T4 (visibility), T5 (live-proof), T8–T9**. Enrichments (DEC-A gated): **T6 (categories/stage/advocate), T7 (dissent + VEP consumption)**.

> **Status (post-Slice93, 2026-07-06):** Landed & gate-green — **T1** (characterization), **T2** (migration 0022 + twin repos), **T3** (route-level persistence, fail-closed `LAYER2_PERSISTENCE_FAILED`; strict free-text `finding_id`/`finding_type`/`severity` hardened to safe values), **T4** (IC-memo/QA-brief `layer2_challenge` visibility, safe ids/counts/categories; UUID-/token-shaped id + identifier-shaped histogram-key guards), **T5** (`_build_layer2_provenance` + strict `debate_layer_2_ic_challenge` clears only when **both** challenger and arbiter models are `runtime_call_proven`), **T6** (bounded `Layer2ChallengeCategory` + scorecard-safe stage-weighted emphasis; category persisted durably in `layer2_ic_findings` + challenge `safe_summary`), **T8** (this readiness-doc + plan reconciliation). **Deferred** per DEC‑G/H to a Slice93 follow-on: **T7** — `ic_advocate` role (challenger→arbiter only), Layer-2 dissent, and deep VEP consumption (`vep_ref_ids` stays recorded-not-consumed). Remaining: **T9** (end-to-end acceptance + independent review + closeout).

### Task 1 — Characterization (pin current truth)
`tests/test_slice93_layer2_characterization.py`: Layer-2 is live-wired (challenger→arbiter, strict fail-closed) but (a) not durable (no persistence-layer refs; latest migration 0021), (b) invisible in memo/QA (no refs), (c) `finding_type`/`severity` free strings, (d) no advocate role, (e) VEP recorded-not-consumed (`service.run` has no vep param), (f) strict component blocks on runtime proof, (g) readiness census says "not-implemented". GREEN-on-arrival. Any RED → STOP + report.

### Task 2 — Migration 0022 + durable models + twin repos (DEC-B)
RED-first repo/table tests (env-gated Postgres + InMemory twin). Verify id shapes first; tables `layer2_ic_challenges` + `layer2_ic_findings` (RLS, composite tenant/run PK, deterministic upserts); safe rows in `models/layer2_durability.py`. No step wiring yet.

### Task 3 — Persist from the Layer-2 step path
RED-first: the LAYER2 step persists its record + findings via the repo, idempotent on retry/resume, fail-closed `LAYER2_PERSISTENCE_FAILED` (static message), InMemory twin default green; step summary gains only ids/counts.

### Task 4 — IC-memo + QA-brief visibility (DEC-C, acceptance)
RED-first: safe `layer2_evidence` threads into the memo builder (Layer-2 challenge/dissent section) and QA-brief builder (Layer-2 unresolved questions as grounded items); NFF deliverable validator stays green; empty-safe when Layer-2 absent.

### Task 5 — Live-provider provenance (DEC-F, acceptance)
RED-first: a safe Layer-2 provenance artifact (model/prompt ids + runtime-executed signal) surfaced in the result/summary; strict `_debate_layer_2_ic_challenge` clears when challenger+arbiter live calls are proven, still blocks otherwise. No weakening of the runtime-proof bar.

### Task 6 — Challenge categories + stage weighting + advocate role (DEC-A/DEC-D/DEC-E; if in-scope)
Category taxonomy (enum) on findings; stage-weighted emphasis via `stage_packs` pattern (no scorecard mutation); add `ic_advocate` role + prompt mirroring the debate advocate. Each additive + independently pinned.

### Task 7 — Dissent + real VEP consumption (DEC-A/DEC-G; if in-scope)
Layer-2 dissent (parallel `PositionSnapshot`/stable-dissent); feed the durable VEP into the challenge inputs (not just record `vep_ref_ids`).

### Task 8 — Readiness doc + plan reconciliation
Post-Slice93 banner (frozen census preserved); flip drifted pins.

### Task 9 — Acceptance proof + closeout
End-to-end: one orchestrated FULL run → distinct + durable + provenance + memo/QA visibility; full `python -m pytest -q` + independent review; closeout PR only when approved.

## 9. Decisions to confirm before implementation

- **DEC-A — Scope breadth / phasing (the big one).** The acceptance's four properties (distinct/durable/live-provider-proven/visible in IC memo/QA brief) are the binding contract; the scope also lists advocate role, challenge categories, stage weighting, dissent, and VEP consumption. Options: **(i, recommended)** acceptance-first — land durability (T2–3), memo/QA visibility (T4), and live-proof provenance (T5) as the spine this slice, plus the *lightest* enrichment that makes outputs "distinct" (challenge categories), and defer advocate role / stage weighting / dissent / deep VEP consumption to a Slice93-follow-on with an honest pin; **(ii)** full scope in one slice (large, higher risk); **(iii)** you specify the exact subset. Choose.
- **DEC-B — Durability shape.** Migration 0022 + Postgres/InMemory twin repos with safe-shape rows and composite tenant/run PKs, mirroring Slice92 (recommended). Confirm — and confirm the id-shape discovery gate (verify `challenge_id`/`finding_id` real shapes before column types).
- **DEC-C — IC-memo/QA-brief mechanics.** Feed safe `layer2_evidence` (ids/categories/counts/unresolved questions) into a memo Layer-2 challenge/dissent section + QA-brief grounded items (recommended — mirrors enrichment/graph/RAG deliverable feeds, NFF-safe). Or a narrower surface (e.g. run_summary only)? Choose.
- **DEC-D — Challenge categories taxonomy.** If in scope: define the enum (e.g. `team_risk`/`market_risk`/`product_risk`/`traction_risk`/`capital_efficiency_risk`/`legal_risk`/`execution_risk` — aligned to the 8 scorecard dimensions?) or a smaller set. Choose the taxonomy or defer.
- **DEC-E — Stage weighting boundary.** Stage weighting emphasizes challenge categories only (must **not** mutate the Layer-2/analysis scorecard) — confirm the boundary, or defer.
- **DEC-F — Live-provider proof / strict.** Add a Layer-2 provenance builder + let strict `_debate_layer_2_ic_challenge` clear on proven live execution; keep existing gate codes unchanged (add a Layer-2 `STRICT_LIVE_*` code only if you want execution-time parity with debate/analysis/scoring). Confirm.
- **DEC-G — VEP consumption depth.** Actually feed the durable VEP into the challenge inputs (recommended if T7 in scope), vs keep the current record-only `vep_ref_ids`. Choose.
- **DEC-H — Advocate role.** Add `ic_advocate` (3-role challenger/advocate/arbiter) this slice, or keep challenger→arbiter and pin the advocate deferral? Choose.
- **DEC-I — Test boundary.** Injected fakes / deterministic path; Postgres env-gated; no real Anthropic. Confirm.

## 10. Open questions for you
1. DEC-A: acceptance-first spine + categories, deferring advocate/stage/dissent/VEP-consumption (i)? Or full scope (ii)? Or a specific subset (iii)?
2. DEC-B: migration 0022 + twin repos mirroring Slice92, with the id-shape discovery gate — confirmed?
3. DEC-C: memo Layer-2 section + QA-brief grounded items as the visibility mechanic?
4. DEC-D/DEC-E: challenge-category taxonomy + stage-weighting-emphasis-only boundary — in scope, and which taxonomy?
5. DEC-F: Layer-2 provenance builder to clear strict "runtime proof" — and do you want a new execution-time `STRICT_LIVE_LAYER2_*` code or keep the existing gate pattern?
6. DEC-H: advocate role this slice or deferred with a pin?
