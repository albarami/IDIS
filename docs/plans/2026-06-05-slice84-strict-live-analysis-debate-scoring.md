# Slice84 — Strict Live Analysis, Debate Layer 1, And Scoring — Implementation Plan

> **For Claude / agentic workers:** REQUIRED SUB-SKILL: `superpowers:executing-plans` (or `subagent-driven-development`) task-by-task. Per task: `test-driven-development` (RED → verify red → minimal GREEN → verify), `verification-before-completion` before any status claim, `using-git-worktrees` already done, `finishing-a-development-branch` before commit/PR. **Reuse before create. STOP for approval after each task.** The §10 decisions are locked (see §0). **Status: Tasks 1–5 complete; Task 6 (docs/gate/review) in progress; Task 7 = PR only.** §0 records the as-built result; §1–16 below are preserved as the original discovery/planning record.

**Goal:** Make **analysis, Layer 1 debate (advocate/sanad-breaker/contradiction-finder/risk-officer/arbiter), and scoring** use only **live, approved Anthropic backends** in strict FULL — no deterministic role/scoring path — and make their outputs **observable** (round counts, dissent, arbiter result, scoring provenance + safe model/prompt provenance and source references). Proven by a **synthetic selected FULL** under the opt-in strict profile **with injected fake live role runners/clients — no real provider call**.

**Architecture:** Slice83 landed the template for EXTRACTION: an injectable client factory seam + execution-time strict enforcement (`strict_live_extraction_required` threaded from the strict FULL path → fail-closed when not anthropic) + additive safe provenance in the step `result_summary`. Slice84 applies the **same pattern three more times** — for `_build_analysis_llm_client` / `_build_scoring_llm_client` (both gated by `IDIS_DEBATE_BACKEND`) and `_build_debate_role_runners` (Layer 1 role runners) — and surfaces the already-computed debate observability (round count, dissent, arbiter decisions, challenges validated) safely in the debate step summary. Readiness-level gating already exists (Slice82: `agent_analysis`, `debate_layer_1`, `scoring` model-health components); the gap is **execution-time enforcement + provenance/observability**. **No real provider/network call (CI injects fakes), no real-data FULL run, no `anthropic_client.py` call-path rewrite, no DB/OpenAPI/schema migration, no Slice85. Layer 2 IC challenge is OUT of scope** (it is `debate_layer_2`, already has its own strict pattern + readiness component).

**Tech Stack:** Python 3.11+, Pydantic v2, FastAPI, pytest, ruff (CI-parity), mypy. `anthropic` SDK already present. Tests use injected fake role runners / LLM clients + deterministic/synthetic corpus — **no real Anthropic call**.

**Base:** branch `slice84-strict-live-analysis-debate-scoring` @ `885cac778a4b53e19737ceb948fc27667a518ebd` (= `origin/main`, Slice83 merged via PR #95), worktree `C:/Projects/IDIS/IDIS-slice84`. Baseline green: `ruff format --check .` 748 ok · `ruff check .` ok · `mypy src/idis` 393 files ok · smoke `test_llm_backend_selection + test_strict_full_live_readiness + test_analysis_deterministic_llm_client + test_scoring_deterministic_llm_client` = 43 passed · `idis.__file__` pinned to this worktree's `src`.

---

## 0. AS-BUILT (Tasks 1–5 complete)

Decisions locked: **D-A NARROW** (analysis + Debate Layer 1 + scoring only; **Layer 2 IC challenge OUT** — separate `debate_layer_2`) · **D-B** enforce at each builder construction seam + keep the preflight gate · **D-C** injectable seams `analysis_client_factory` / `scoring_client_factory` / `debate_role_runners_factory` · **D-D** one shared flag `strict_live_debate_backend_required` · **D-E** distinct safe codes via one shared `StrictLiveRoleError` · **D-F/D-G** additive provenance + debate observability with a **fixed** arbiter rationale summary (never raw) · **D-H** registry/on-disk prompt versions · **D-I** `result_summary` is an open `dict[str, Any]` (additive-safe, no schema change) · **D-J** output-visible = step-summary provenance + source refs.

**Delivered (all in `src/idis/api/routes/runs.py` unless noted; default/non-strict paths byte-equivalent):**
- **Task 1 — Characterization** (`tests/test_slice84_strict_live_roles_characterization.py`): pinned the pre-change truth; items 2 & 4 drift-flipped to the as-built truth after Tasks 3 & 4.
- **Task 2 — Injectable seams**: frozen `AnalysisClientSelection` / `ScoringClientSelection` / `DebateRoleRunnerSelection` (safe context, **no API key**) + factory type aliases + `_resolve_*_selection`; the 3 builders accept `*_factory=None`; the 3 FULL steps + `build_run_context` (`services/runs/steps.py`) thread the factories. Default behavior unchanged.
- **Task 3 — Execution-time strict enforcement**: shared flag `strict_live_debate_backend_required` threaded from the strict FULL path (`start_run` + worker `_default_run_context_factory` → `build_run_context` → the 3 FULL partials → the 3 builders). Strict + non-anthropic backend → role-specific `STRICT_LIVE_{ANALYSIS,DEBATE,SCORING}_REQUIRED`; strict + anthropic provider/factory failure → `..._PROVIDER_FAILED` — both via one shared `StrictLiveRoleError` (fixed safe message). No env inference inside builders; SNAPSHOT/non-strict unchanged.
- **Task 4 — Provenance + debate observability**: additive `analysis_provenance` / `scoring_provenance` / `debate_provenance` (provider/backend, model(s), prompt id/version, strict flag, **sanitized** `provider_request_id` via Slice82 `_sanitize_request_id`) + `debate_observability` (round/stop/agent counts, `dissent_preserved`, `challenges_validated_count`, **fixed** `arbiter_rationale_summary` = `arbiter_decision_recorded`/`no_arbiter_decision`, `source_reference_ids`). Prompt ids: debate = 5 `DEBATE_*_V1` registry ids + version; scoring = `scoring_agent`/`1.0.0`; analysis = null (8 embedded specialist prompts, no single registry id). `_extraction_prompt_version` (Slice83) DRY-delegates to the new `_prompt_registry_version`.
- **Task 5 — Acceptance** (`tests/test_slice84_strict_live_roles_acceptance.py`): master-plan acceptance proven GREEN-on-arrival (no production change) — no deterministic role/scoring path in strict; injected live fakes allowed with no real call; safe provider-failed blocks; non-strict/SNAPSHOT unchanged; API/worker strict-flag threading; safe provenance + debate observability; additive in open `result_summary`.

**Guarantees:** no real provider/network call (CI injects fakes); no real-data FULL run; no DB/OpenAPI/schema migration; no prompt-registry mutation; no `anthropic_client.py` rewrite; Layer 2 IC challenge untouched.

**Remaining follow-ups (out of this slice):** analysis/scoring per-report/dimension source-reference ids are available in the underlying `_analysis_bundle` reports / scorecard (`supported_claim_ids`/`supported_calc_ids`) but are **not** flattened into the provenance blocks (debate surfaces `source_reference_ids`); optional `response.id` capture in production `anthropic_client.py` (BROAD); deeper prompt→model binding. None required by the master-plan acceptance.

---

## 1. Master Plan text (verbatim)
> #### Slice 84: Strict Live Analysis, Debate Layer 1, And Scoring
>
> **Goal:** Make analysis, Layer 1 debate, and scoring live-provider backed and output-visible.
>
> **Scope:**
> - `IDIS_DEBATE_BACKEND=anthropic`.
> - Live role runners only in strict mode.
> - Round counts, dissent, arbiter result, scoring provenance.
>
> **Acceptance:**
> - No deterministic LLM role/scoring path is used in strict mode.
> - Outputs include safe model/prompt provenance and source references.

---

## 2. Discovery — what already exists (verified; exact refs at 885cac77)

### 2.1 The three client/runner builders (the chokepoints) — `src/idis/api/routes/runs.py`
- **`_build_analysis_llm_client()` (~:2267-2289):** `backend = os.environ.get("IDIS_DEBATE_BACKEND", "deterministic")`; `=="anthropic"` → `AnthropicLLMClient(model=IDIS_ANTHROPIC_MODEL_DEBATE_DEFAULT default claude-sonnet-4-20250514, max_tokens=8192)`; **else → `DeterministicAnalysisLLMClient()`** (silent). No factory, no strict, no provenance.
- **`_build_scoring_llm_client()` (~:2242-2264):** same env; anthropic → `AnthropicLLMClient(DEBATE_DEFAULT, max_tokens=16384)`; **else → `DeterministicScoringLLMClient()`**.
- **`_build_debate_role_runners(context=None)` (~:2751-2820):** `backend != "anthropic"` → `RoleRunners()` (**deterministic, silent**); `=="anthropic"` → `RoleRunners` of `LLMRoleRunner` for advocate/sanad_breaker/contradiction_finder/risk_officer (model `DEBATE_DEFAULT`) + arbiter (model `IDIS_ANTHROPIC_MODEL_DEBATE_ARBITER` default claude-opus-4-20250514). Two `AnthropicLLMClient` (default + arbiter).
- Deterministic clients: `src/idis/services/extraction/extractors/llm_client.py` — `DeterministicAnalysisLLMClient` (:113-778), `DeterministicScoringLLMClient` (:792-975) (fixed-value JSON; no network). Live: `AnthropicLLMClient` (`anthropic_client.py`; **captures no request-id**; fail-closed `ValueError` on missing key).

### 2.2 The three FULL steps + their step summaries — `src/idis/api/routes/runs.py`
- **`_run_full_analysis()` (~:1379-1464):** `_build_analysis_llm_client()` → `build_default_specialist_agents(llm_client)` (8 agents, `src/idis/analysis/agents/__init__.py`) → `AnalysisEngine.run` (`src/idis/analysis/runner.py`) → returns `{agent_count, report_ids, bundle_id, _analysis_bundle, _analysis_context}`. **No provenance.**
- **`_run_full_debate()` (~:1144-1215):** `_build_debate_role_runners(context)` → `DebateOrchestrator(...).run(state)` (`src/idis/debate/orchestrator.py`) → returns `{debate_id, stop_reason, round_number, muhasabah_passed, agent_output_count}`. `final_state` ALSO has `arbiter_decisions` (with `dissent_preserved`, `challenges_validated`, `utility_adjustments`, `rationale`) + `agent_outputs` — **NOT surfaced** in the summary. **No provenance.**
- **`_run_full_scoring()` (~:1609-1651):** `_build_scoring_llm_client()` → `LLMScorecardRunner` (`src/idis/analysis/scoring/llm_scorecard_runner.py`) → `ScoringEngine.score` (`src/idis/analysis/scoring/engine.py`) → returns `{composite_score, band, routing, _scorecard}`. **No provenance.**
- All `result_summary` persisted via `run_steps.py` Postgres `create`/`update` as JSONB; `RunStep.result_summary: dict[str, Any]` open dict → additive keys need **no schema/OpenAPI migration**.

### 2.3 Strict gate + threading (Slice83 template) — `runs.py` / `steps.py` / `worker.py`
- `start_run` (~:229) computes `strict_live_extraction_required = request_body.mode == "FULL" and is_strict_full_live_required(...)` → passes to `build_run_context(..., strict_live_extraction_required=...)` (~:285). Worker `_default_run_context_factory` (~:429-449) computes + passes the same.
- `build_run_context` (`steps.py:72-167`): `extract_fn=partial(_run_snapshot_extraction, db_conn, strict_live_extraction_required=...)` (:142-146) — has the flag. **`analysis_fn` (:154-158), `debate_fn` (:152), `scoring_fn` (:159) do NOT carry a strict flag.** `layer2_ic_challenge_fn` (:153) reads env itself (separate pattern).
- The extraction enforcement template to mirror: `StrictLiveExtractionError` + codes `STRICT_LIVE_EXTRACTION_REQUIRED`/`_PROVIDER_FAILED` (~:2577-2591); `ExtractorClientSelection`/`ExtractorClientFactory` (~:2595-2607); `_resolve_extraction_selection` (~:2613); `_build_strict_anthropic_extractor` (~:2626-2647); `_build_extraction_llm_client(strict_live_extraction_required)` (~:2650-2702); `_safe_client_request_id`/`_extraction_prompt_version`/`_build_extraction_provenance` (~:2705-2748).

### 2.4 Readiness gating already exists (Slice82) — `src/idis/services/runs/strict_full_live.py`
- `_analysis` (~:801-813) `agent_analysis`; `_debate_layer_1` (~:816-831) `debate_layer_1`; `_scoring` (~:880-890) `scoring` — all model-health-driven via `check_llm_model_health(role=ANALYSIS/DEBATE/SCORING)` + `_missing_model_env(IDIS_DEBATE_BACKEND, …)`; HEALTHY→LIVE, else fail-closed (`may_proceed=False`). `_debate_layer_2_ic_challenge` (~:834-877) is **separate** (runtime-proof-required) — **out of Slice84 scope**.

### 2.5 Reuse (Slice82) + envs
- `src/idis/services/llm_model_health.py`: `LlmModelRole.{ANALYSIS,DEBATE,SCORING}` + `_ROLE_SPECS` (:65-85) — ANALYSIS→(IDIS_DEBATE_BACKEND, DEBATE_DEFAULT), DEBATE→(IDIS_DEBATE_BACKEND, DEBATE_DEFAULT + ARBITER), SCORING→(IDIS_DEBATE_BACKEND, DEBATE_DEFAULT); `_sanitize_request_id` (:173). **No new env var** — analysis/debate/scoring all gate on `IDIS_DEBATE_BACKEND`. `.env.example:98-105` documents `IDIS_DEBATE_BACKEND` (default `deterministic`) + the 3 models.
- Prompt registry (`prompts/registry.yaml`): `DEBATE_ADVOCATE_V1`, `DEBATE_SANAD_BREAKER_V1`, `DEBATE_CONTRADICTION_FINDER_V1`, `DEBATE_RISK_OFFICER_V1`, `DEBATE_ARBITER_V1`, validator/scoring prompts; analysis agents load `src/idis/analysis/agents/<agent>/prompt.md`, scoring loads `prompts/scoring_agent/1.0.0/prompt.md`. (Prompt-id → version source for provenance — see D-H.)

### 2.6 Tests to reuse
`tests/test_llm_backend_selection.py` (debate cases: deterministic default / anthropic+key→LLMRoleRunner / anthropic-no-key→ValueError); `tests/test_analysis_deterministic_llm_client.py`; `tests/test_scoring_deterministic_llm_client.py`; `tests/test_debate_role_determinism.py`; `tests/test_slice70_synthetic_full_execution_rehearsal.py` (synthetic FULL, isolates live env); `tests/test_slice75a/b` parity. No-real-call: inject fake clients/runners, deterministic clients, `patch.dict(os.environ, clear=True)`.

---

## 3. Where deterministic analysis/debate/scoring paths remain
1. **`_build_analysis_llm_client` / `_build_scoring_llm_client` / `_build_debate_role_runners`** silently return the deterministic client/`RoleRunners()` whenever `IDIS_DEBATE_BACKEND != "anthropic"` — **even at execution time after the strict admission gate passed** (env re-read fresh; no runtime re-check, no strict flag). **The core gap (×3).**
2. SNAPSHOT bypasses the FULL-only strict gate (unchanged, intentional).
3. Synthetic rehearsals clear live env → deterministic (by design).
4. Direct deterministic use in unit tests + `scripts/`.
> Layer 2 IC challenge (`_run_full_layer2_ic_challenge`) ALREADY enforces strict (raises `LAYER2_MISSING_LIVE_MODEL_CONFIG`) — separate `debate_layer_2` concern, **out of scope**.

## 4. Exact strict-mode boundary for `IDIS_DEBATE_BACKEND=anthropic`
Strict-live analysis/debate-L1/scoring MUST hold when **`is_strict_full_live_required()` is true AND `mode == FULL`** (the opt-in strict profile). Within that boundary, for each of the three roles: `IDIS_DEBATE_BACKEND` MUST be `anthropic`; the constructed analysis client / debate role runners / scoring client MUST be the **live (Anthropic / `LLMRoleRunner`)** variant (or an explicitly injected approved live runner for tests); the **deterministic variant is forbidden**; missing/failed provider blocks with a safe reason. Outside the boundary (SNAPSHOT, non-strict FULL) deterministic remains allowed (unchanged).

## 5. How strict mode should block deterministic role/scoring paths (design)
Mirror Slice83 for each builder: add an injectable factory + a `strict_live_..._required` flag; when strict and the resolved backend is not `anthropic` (or the live runner cannot be built) → **fail closed before any deterministic client/runner is constructed** with a safe code (D-F). Thread the flag from the strict FULL path (`start_run` + worker → `build_run_context` → `analysis_fn`/`debate_fn`/`scoring_fn` → the builders). Keep the existing preflight admission gate (defense in depth).

## 6. How missing/failed providers should block safely
- **Missing** (backend≠anthropic, no key/model) → existing readiness gate blocks at preflight (HTTP 409 `STRICT_FULL_LIVE_BLOCKED`); Slice84 adds the execution-time backstop (no silent deterministic).
- **Failed** (live client/runner construction or call raises) → wrap as a safe `STRICT_LIVE_{ANALYSIS,DEBATE,SCORING}_PROVIDER_FAILED` (fixed message, no raw exception/key/prompt/response/payload); the step fails closed (the orchestrator records a sanitized FAILED step). Reuse the Slice83 sanitizer discipline.

## 7. How round counts / dissent / arbiter result / scoring provenance are recorded
Additively in each step's `result_summary` (open dict — no schema migration):
- **Debate (`_run_full_debate`):** surface `round_number` (exists) + `stop_reason` (exists) + a safe **arbiter/observability** block from `final_state.arbiter_decisions`: per-round `dissent_preserved` (bool), `challenges_validated` **count** (and safe claim-id list), final arbiter `rationale` (a fixed/short safe string — confirm in D-G), `agent_output_count` (exists) + `debate_provenance` (provider/backend/model[default+arbiter]/prompt ids+versions/strict flag/request_id). Source references = `supported_claim_ids`/`supported_calc_ids` (safe ids already in outputs).
- **Analysis (`_run_full_analysis`):** `analysis_provenance` (provider/backend/model/prompt id(s)+version/strict flag/request_id) + report source references (`supported_claim_ids`/`supported_calc_ids` per report — safe ids).
- **Scoring (`_run_full_scoring`):** `scoring_provenance` (provider/backend/model/prompt id+version/strict flag/request_id); dimension source refs (`supported_claim_ids`/`supported_calc_ids`, `enrichment_refs`) already in the scorecard.
- **Safe always:** reuse Slice82 `_sanitize_request_id` + prompt-registry version; never API key, prompt body, response text, raw provider payload, exception message, or path.

## 8. Proving acceptance with synthetic selected FULL, injected fakes, NO real calls
Mirror Slice83's seam-level acceptance: exercise each FULL step with the strict flag True + an **injected fake live runner/client** (returns valid AgentReport / DebateState outputs / Scorecard JSON; no network). Prove: (1) the **live** variant is used and the **deterministic variant is rejected** under strict (the three acceptance "no deterministic path" cases); (2) each step summary carries safe provenance + source references + (debate) round/dissent/arbiter observability; (3) missing config → safe block; failed injected runner → safe `..._PROVIDER_FAILED`; (4) `build_run_context` threads the strict flag(s) into `analysis_fn`/`debate_fn`/`scoring_fn` (parity: API + worker funnel). Synthetic GDBS corpus only; no real Anthropic/network; no real FULL.

## 9. Reuse map (exact files)
**Reuse unchanged (verify only):** Slice83 extraction template (`StrictLive*Error`/codes/`*ClientSelection`/`*ClientFactory`/`_resolve_*`/`_build_strict_anthropic_*`/`_build_*_provenance`/`_extraction_prompt_version`/`_safe_client_request_id`), `strict_live_*_required` threading (`steps.py`/`worker.py`); Slice82 `llm_model_health` (`LlmModelRole`, `_sanitize_request_id`, `_ROLE_SPECS`, readiness components); prompt `registry.py`; Slice70 synthetic FULL + `TestClient`/in-memory stores; the debate `final_state` (round/arbiter/dissent already computed).
**Touch (production) — NARROW (×3 roles):** injectable factory + execution-time strict enforcement (forbid deterministic, fail-closed safe code) for analysis/scoring clients + debate role runners in `runs.py`; thread `strict_live_..._required` from the strict FULL path (`start_run`/`worker` → `build_run_context` → the 3 step fns); additive provenance + debate observability in the 3 step summaries (reuse sanitizers). **Out of scope:** real provider call by default, real-data FULL, `anthropic_client.py` rewrite, Layer 2 IC challenge, DB/OpenAPI/schema migration, deeper prompt→model binding.

## 10. Decisions — confirm BEFORE Task 1
- **D-A — SCOPE (key).** **NARROW (recommended):** execution-time strict enforcement + injectable seams + safe provenance/observability for **analysis, debate Layer 1, scoring** + synthetic acceptance with injected fakes — no real call. **Layer 2 IC challenge OUT** (separate `debate_layer_2`). **BROAD:** also capture `response.id` in production `anthropic_client.py` + deeper prompt→model binding (not required by acceptance).
- **D-B — Enforcement point.** Each builder's construction seam (`_build_analysis_llm_client` / `_build_scoring_llm_client` / `_build_debate_role_runners`, refactored) **+ keep the preflight admission gate** (recommended).
- **D-C — Injectable seams.** `analysis_client_factory` / `scoring_client_factory` / `debate_role_runners_factory` (mirror `ExtractorClientFactory`), default = current selection; tests inject fakes.
- **D-D — Strict flag shape (key).** **One shared flag** `strict_live_debate_backend_required` (all three gate on `IDIS_DEBATE_BACKEND`; recommended, fewer moving parts) **vs** three flags `strict_live_{analysis,debate,scoring}_required`. Recommend the shared flag; note the alternative.
- **D-E — Provider-failure + required codes.** Distinct safe codes per role: `STRICT_LIVE_ANALYSIS_REQUIRED`/`_PROVIDER_FAILED`, `STRICT_LIVE_DEBATE_REQUIRED`/`_PROVIDER_FAILED`, `STRICT_LIVE_SCORING_REQUIRED`/`_PROVIDER_FAILED` (mirror Slice83 clarity) — vs a shared `StrictLiveLlmRoleError`. Recommend distinct codes via one shared exception type.
- **D-F — Provenance shape.** Additive `{analysis,debate,scoring}_provenance` per step: `{provider, backend, model (debate: default + arbiter), prompt_id(s), prompt_version(s), strict_live_*_required, provider_request_id}`. Confirm whether to record a representative prompt id vs a list (analysis = 8 agents, debate = 5 roles).
- **D-G — Debate observability shape (key).** Surface `round_number` + `stop_reason` + per-round `dissent_preserved` + `challenges_validated` count + safe arbiter `rationale` + `agent_output_count`. Confirm `rationale` is safe to surface (it is a derived short string, not a model body) or replace with a fixed safe summary.
- **D-H — Prompt id/version source.** Analysis agents + debate roles + scoring each have prompts. Confirm the prompt-id→version source for provenance (per-role registry entries vs the on-disk prompt dir version like Slice83's `EXTRACT_CLAIMS_V1` → `1.0.0`).
- **D-I — result_summary open dict.** Task 1 must confirm `result_summary` is an open `dict[str,Any]` (additive-safe, no schema change). If a closed API/OpenAPI schema pins it, STOP and report.
- **D-J — Output visibility scope.** "Output-visible" = provenance + source references in the **step summaries** (not deliverables rendering). Confirm.

## 11. Scope boundary / not doing yet
No real provider/network call (CI injects fakes; any real probe approval-gated, never CI); no real-data FULL run; no `IDIS_REQUIRE_FULL_LIVE=1` against live providers; no `anthropic_client.py` call-path rewrite; **no Layer 2 IC challenge changes**; no DB/OpenAPI/schema migration; no prompt-registry mutation; no Slice85. None unless a task's discovery proves it strictly required — then STOP and report.

## 12. Task breakdown (TDD; STOP after each)
- **Task 1 — Characterization (no prod change):** pin current truth (see §13). GREEN-on-arrival confirms the gaps; justifies Tasks 2–6.
- **Task 2 — Injectable seams:** add injectable factories to the 3 builders (default unchanged); thread through the 3 step fns. No behavior change.
- **Task 3 — Execution-time strict enforcement (×3):** thread `strict_live_debate_backend_required` (D-D) from the strict FULL path; forbid deterministic for analysis/debate/scoring; fail closed with safe codes (D-E). RED-first.
- **Task 4 — Provenance + debate observability:** additive `{analysis,debate,scoring}_provenance` + debate round/dissent/arbiter block + source references; sanitized. RED-first.
- **Task 5 — Synthetic acceptance (opt-in strict, injected fakes):** prove the master-plan acceptance (no deterministic path in strict; outputs include safe provenance + source refs) with no real call.
- **Task 6 — Docs/config reconciliation + full gate + independent review.**
- **Task 7 — Finish branch: open PR only.**

## 13. Proposed Task 1 (characterization, no production change)
Add `tests/test_slice84_strict_live_roles_characterization.py` pinning (mocked/synthetic; **no real Anthropic call, no FULL run**):
1. **Selection truth** for `_build_analysis_llm_client` / `_build_scoring_llm_client` / `_build_debate_role_runners`: unset/`deterministic` → deterministic client / `RoleRunners()`; `anthropic`+`ANTHROPIC_API_KEY` → `AnthropicLLMClient` / `LLMRoleRunner`; `anthropic` no key → `ValueError`.
2. **No execution-time strict enforcement yet:** with `IDIS_REQUIRE_FULL_LIVE=1` + `IDIS_DEBATE_BACKEND` unset, each builder still returns the deterministic variant (no exception).
3. **No injectable factory yet:** the 3 builders' signatures have no `*_factory` param.
4. **No provenance yet:** `_run_full_analysis` / `_run_full_debate` / `_run_full_scoring` summaries contain no `*_provenance`; the debate summary has `round_number`/`stop_reason`/`muhasabah_passed`/`agent_output_count` but **no** arbiter/dissent/challenge detail.
5. **Slice83/82 reuse importable:** `StrictLiveExtractionError`, `_sanitize_request_id`, `LlmModelRole.{ANALYSIS,DEBATE,SCORING}`, `_safe_client_request_id`/`_extraction_prompt_version` (template), prompt `registry`.
6. **`result_summary` is an open `dict[str,Any]`** (D-I evidence).
7. **Layer 2 IC challenge is already strict (out of scope):** `_run_full_layer2_ic_challenge` raises `LAYER2_MISSING_LIVE_MODEL_CONFIG` when strict + missing config (pin that it is a separate pattern Slice84 does not touch).
GREEN-on-arrival = current truth confirmed (no repair). Mocked/synthetic only.

## 14. Verification gate (CI parity — from worktree root, `PYTHONPATH=src`)
`python -c "import idis; print(idis.__file__)"` · `ruff format --check .` · `ruff check .` · clear `.mypy_cache` then `mypy src/idis` · `python scripts/forbidden_scan.py --repo-root .` · `git diff --check` · targeted `pytest` (injected fakes; **no real Anthropic/network; no real FULL run**). DB-backed `*_postgres.py` only in CI. Contract/OpenAPI parse = N/A (no `scripts/*contract*`/`*openapi*` gate script).

## 15. Risks
- **Accidental real provider call / real FULL** — the live variants must be exercised only via injected fakes in CI; never set `IDIS_REQUIRE_FULL_LIVE=1` against live providers; never call the real-data FULL harness. (Highest.)
- **Leakage** — provenance/observability must record only safe provider/model/prompt-id/version + sanitized request id + safe claim/calc ids; never key/prompt/response/payload/path. Debate `rationale`/arbiter content must be a derived safe string, not a model body (D-G).
- **Blast radius (×3)** — three builders + threading + three summaries touch shared run wiring; keep default paths byte-equivalent (characterization green); treat strict-consumer test drift as controlled (like Slice79/80/82/83).
- **Debate complexity** — Layer 1 has 5 roles + 2 models + an orchestrator graph; the injectable seam must cover `RoleRunners` cleanly; arbiter/dissent already computed in `final_state` (surface, don't recompute).
- **Contract drift** — if `result_summary` is a closed schema, additive provenance needs a migration (out of scope) — STOP and report (D-I).

## 16. Open questions for you
1. **D-A:** NARROW (recommended; Layer 2 IC out) or BROAD?
2. **D-D:** one shared `strict_live_debate_backend_required` flag (recommended) or three role flags?
3. **D-E:** distinct per-role safe codes (recommended) or one shared code?
4. **D-F/D-G:** confirm the provenance shape + the debate observability fields (round/dissent/challenges/arbiter rationale safety).
5. **D-H:** prompt-id/version source for analysis/debate/scoring provenance.
6. **D-I:** confirm `result_summary` open dict (no schema change).
7. **D-J:** "output-visible" = step-summary provenance + source refs (not deliverables) — confirm.
