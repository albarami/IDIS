# Slice83 — Strict Live Extraction — Implementation Plan

> **For Claude / agentic workers:** REQUIRED SUB-SKILL: `superpowers:executing-plans` (or `subagent-driven-development`) task-by-task. Per task: `test-driven-development` (RED → verify red → minimal GREEN → verify), `verification-before-completion` before any status claim, `using-git-worktrees` already done, `finishing-a-development-branch` before commit/PR. **Reuse before create. STOP for approval after each task.** The §10 decisions are **RESOLVED as-built** (see §0). **Tasks 1–5 are complete and verified; Task 6 = docs reconciliation + full gate + independent review.**

**Goal:** Make strict FULL extraction use only a **live, approved Anthropic extractor backend** — no deterministic extractor in strict mode — and record **safe provider provenance + prompt/model version** in the extraction step summary, proven by a **synthetic selected FULL** under an opt-in strict profile **with no real provider call** (injected fake live client).

**Architecture:** Today the strict gate (`IDIS_REQUIRE_FULL_LIVE`) blocks a FULL run at **admission/preflight** (HTTP 409 `STRICT_FULL_LIVE_BLOCKED`) when the four Anthropic readiness components are not configured/health-checked (Slice82). But the extraction client is chosen **fresh at execution time** by `_build_extraction_llm_client()` (env-driven, silent fall-through to `DeterministicLLMClient`), with **no execution-time enforcement** that the live extractor is actually used and **no provider/prompt provenance** recorded in the step summary. Slice83 adds: (1) an **injectable extractor-client factory** seam (mirror Slice82's `client_factory`) so the live path is testable without a network call; (2) **execution-time strict enforcement** that forbids the deterministic extractor when strict-live FULL is required (fail-closed, safe reason); (3) **safe provenance** (provider + safe model name + prompt id/version) additively in the extraction step `result_summary` (JSONB dict — no schema migration); (4) a **synthetic selected FULL** acceptance under the opt-in strict profile with an injected fake live client. **No real provider/network call (CI injects a fake), no real-data FULL run, no `anthropic_client.py` call-path rewrite required, no DB/OpenAPI/schema migration, no Slice84.**

**Tech Stack:** Python 3.11+, Pydantic v2, FastAPI, pytest, ruff (CI-parity), mypy. `anthropic` SDK already present. Tests use injected fake extractor clients + deterministic/synthetic corpus — **no real Anthropic call**.

**Base:** branch `slice83-strict-live-extraction` @ `e6dac5dd6016ff6727d6a7067b80c0cbaa43bdac` (= `origin/main`, Slice82 merged via PR #94), worktree `C:/Projects/IDIS/IDIS-slice83`. Baseline green: `ruff format --check .` 743 ok · `ruff check .` ok · `mypy src/idis` 393 files ok · smoke `test_llm_backend_selection.py + test_strict_full_live_readiness.py + test_slice82_model_health_strict_wiring.py` = 29 passed · `idis.__file__` pinned to this worktree's `src`.

---

## 0. As-Built Status (Tasks 1–5 complete; Task 6 = docs/gate/review)

**Status:** Tasks 1–5 **complete and verified** (TDD; **no real provider call; no real FULL run**). Task 6 (this) = docs reconciliation + full local gate + independent review. **D-A = NARROW** (confirmed); all §10 decisions resolved (see the note in §10).

**As-built task map:**
- **Task 1 — Characterization** (`tests/test_slice83_strict_live_extraction_characterization.py`): pinned the gap; drift-updated after Tasks 2/3/4.
- **Task 2 — Injectable extractor-client seam** (`runs.py`): `ExtractorClientSelection` + `ExtractorClientFactory`; `_build_extraction_llm_client(*, extractor_client_factory=None)` + `_run_snapshot_extraction(..., extractor_client_factory=None)`; default behavior unchanged. Tests: `tests/test_slice83_extractor_client_factory_seam.py`.
- **Task 3 — Execution-time strict enforcement** (`runs.py`, `steps.py`, `worker.py`): `strict_live_extraction_required` flag threaded from the strict FULL execution path (API `start_run` + worker `_default_run_context_factory` → `build_run_context` → `extract_fn` → `_run_snapshot_extraction` → builder). Tests: `tests/test_slice83_strict_live_extraction_enforcement.py`.
- **Task 4 — Safe provenance** (`runs.py`): additive `extraction_provenance` in the EXTRACT step summary. Tests: `tests/test_slice83_extraction_provenance_summary.py`.
- **Task 5 — Acceptance proof** (`tests/test_slice83_strict_live_extraction_acceptance.py`): both master-plan acceptance bullets proven with an injected fake (no real call).

**As-built behavior (NARROW):**
- **Injectable extractor-client seam.** `_build_extraction_llm_client` accepts `extractor_client_factory` (default `None` → unchanged); when supplied, the factory receives a safe `ExtractorClientSelection` (backend/model/max_tokens — **no API key**) and returns the client. Lets the live path be exercised with a fake — **no network**.
- **Execution-time strict enforcement.** When `strict_live_extraction_required=True` (threaded only from the strict FULL execution path; **never inferred from env in low-level code**): a non-anthropic backend **fails closed before any deterministic client is built** → `STRICT_LIVE_EXTRACTION_REQUIRED`; an anthropic provider construction/call failure → `STRICT_LIVE_EXTRACTION_PROVIDER_FAILED`. Both codes are safe/fixed (no key/prompt/response/payload/raw-exception/path).
- **Strict FULL forbids deterministic fallback;** **SNAPSHOT remains non-strict** (the admission gate + flag are FULL-only); **non-strict FULL unchanged** (deterministic still allowed).
- **API + worker parity.** Both compute `mode=="FULL" and is_strict_full_live_required(...)` and thread it through the shared `build_run_context` → `extract_fn` funnel; no bypass/race introduced.
- **Safe additive provenance.** The EXTRACT step `result_summary` gains `extraction_provenance` = `{provider, backend, model, prompt_id (EXTRACT_CLAIMS_V1), prompt_version (from registry.yaml), strict_live_extraction_required, provider_request_id}` — the request id is sanitized (Slice82 `_sanitize_request_id`) and present **only if the client safely exposes one**. Additive in the open `result_summary` dict → **no schema/OpenAPI migration**; existing summary fields unchanged.
- **Boundaries honored.** No real provider/network call (CI injects a fake); no real-data FULL run; no `anthropic_client.py` call-path rewrite; no DB/OpenAPI/schema; no prompt-registry mutation; no Slice84.

**Config/docs reconciliation (Task 6):** `.env.example` already documents `IDIS_EXTRACT_BACKEND` (default `deterministic`, with the `anthropic` option in the comment), `ANTHROPIC_API_KEY` (safe non-real value), and `IDIS_ANTHROPIC_MODEL_EXTRACT` — **accurate, no edit**; Slice83 introduces **no new env var** (enforcement uses the existing `IDIS_REQUIRE_FULL_LIVE` + `IDIS_EXTRACT_BACKEND`; the strict flag is internal). `docs/architecture/strict_full_live_readiness.md` (Slice-53 audit) lists "Forbid fallback clients in strict mode: Extraction must reject DeterministicLLMClient" as a required change — Slice83 now satisfies it for extraction; the doc remains accurate as a roadmap (**no edit**). No CLI/operator runbook describes strict live extraction — **N/A**.

**Remaining follow-ups (out of Slice83 scope):**
- Capture `response.id`/model safely in the production `anthropic_client.py` call path so `provider_request_id` is populated on real runs (Slice82 follow-up).
- Apply the same execution-time live enforcement + provenance to analysis / Layer 1 debate / scoring (**Slice 84**).
- (Optional) thread the `extractor_client_factory` through `build_run_context` for an end-to-end strict-FULL acceptance with a fake (currently injected at the `_run_snapshot_extraction` seam in tests).

---

## 1. Master Plan text (verbatim)
> #### Slice 83: Strict Live Extraction
>
> **Goal:** Make strict extraction use only live approved extractor backends.
>
> **Scope:**
> - `IDIS_EXTRACT_BACKEND=anthropic`.
> - No deterministic extractor in strict mode.
> - Provider provenance and prompt/model version in step summary.
>
> **Acceptance:**
> - Synthetic selected FULL uses live extraction under opt-in strict profile.
> - Missing/failed provider blocks before or during strict run with safe reason.

---

## 2. Discovery — what already exists (verified; exact refs at e6dac5dd)

### 2.1 Extraction backend selection (the chokepoint)
`src/idis/api/routes/runs.py`:
- `_build_extraction_llm_client()` (~:2556-2580): `backend = os.environ.get("IDIS_EXTRACT_BACKEND", "deterministic")`; `== "anthropic"` → `AnthropicLLMClient(model=IDIS_ANTHROPIC_MODEL_EXTRACT default claude-sonnet-4-20250514, max_tokens=4096)`; **else → `DeterministicLLMClient()`** (silent fall-through). Called at extract-step execution (`_run_snapshot_extraction` ~:2461 → `LLMClaimExtractor`).
- `src/idis/services/extraction/extractors/anthropic_client.py`: `AnthropicLLMClient` — fail-closed `ValueError` if `ANTHROPIC_API_KEY` absent (:51-57); only network call is `messages.create(...)`; returns `response.content[0].text` (:101-104) — **captures NO request-id/provider metadata**.
- `src/idis/services/extraction/extractors/llm_client.py:33` `DeterministicLLMClient` — fixed-value JSON claims; **no network, no env validation**.
- Selection tests: `tests/test_llm_backend_selection.py` (default→deterministic; anthropic+key→Anthropic; anthropic-no-key→`ValueError`); `tests/test_anthropic_client_max_tokens.py` (fake `messages.create` via MagicMock; max_tokens wiring).

### 2.2 Strict gate + FULL admission
`src/idis/services/runs/strict_full_live.py`:
- `is_strict_full_live_required(env)` (~:312-323) reads `IDIS_REQUIRE_FULL_LIVE` (truthy `1/true/yes/on`).
- `build_strict_full_live_readiness_report(...)` / admission report → consumed at **API** `src/idis/api/routes/runs.py:227-240` (FULL only): `may_proceed=False` → `IdisHttpError(409, STRICT_FULL_LIVE_BLOCKED, details=build_strict_block_operator_safe_details(...))`; re-applied on retry/resume (~:444-456); **worker** preflight `src/idis/pipeline/worker.py:138-190` persists a `STRICT_FULL_LIVE_BLOCKED` FAILED step.
- Extraction readiness components: `_supported_parsers_extraction(env)` (~:555-579, MISSING_CREDENTIALS vs LIVE_WIRED_AND_USED via `_missing_model_env`); `_live_llm_model_clients(env, model_health)` (~:786-798, Slice82 health-driven via `check_llm_model_health(role=EXTRACTION)` → HEALTHY/CONFIGURED_BUT_FAILED_HEALTH_CHECK/MISSING_CREDENTIALS, fail-closed `_model_component` ~:2197-2231).
- Safe blocker surface: `build_strict_block_operator_safe_details(...)` (~:1299-1322) — exposes only `may_proceed`, `blocker_count`, `blocking_components`, safe `StepProvenance`; never raw env/secret/path. Error code `STRICT_FULL_LIVE_BLOCKED`.

### 2.3 Provisioning truth — "Anthropic extraction" four-state (Slice82)
`src/idis/services/runs/strict_provisioning_truth.py`: `_inventory_llm_item("Anthropic extraction", model_health=extract_model_health, …)` → four-state `configured` (=`model_health.configured`) / `health_checked` (**False**: in `_STATIC_NOT_PROBED_COMPONENTS`, intentional Slice72 anti-overclaiming) / `runtime_call_proven` (False unless opt-in `allow_local_strict_health_probes` + proven checker) / `full_run_used` (**False**, never set in provisioning).

### 2.4 Run-step summary + provenance machinery
- `src/idis/models/run_step.py:212-242` `RunStep` → `result_summary: dict[str, Any]` (persisted JSONB: `run_steps.py` Postgres `create`/`update` cast to JSONB; InMemory store). **Open dict — additive keys need no schema/OpenAPI migration** (confirm in D-G).
- Extraction step summary today: `src/idis/models/extraction_execution.py:410-425` `to_run_step_summary()` → `{status, task_results[], summary{counts/by_status/by_reason}}` — **no provider/model/prompt-version provenance**.
- Safe-summary discipline to reuse: `src/idis/models/step_provenance.py:58-89` safe token pattern `^[a-z][a-z0-9_]*$`; `orchestrator.py` `SAFE_STEP_ERROR_MESSAGE` + `SENSITIVE_AUDIT_RESULT_KEY_PARTS` redaction.
- **Slice82 reuse:** `src/idis/services/llm_model_health.py` — `LlmModelHealthCheck.{provider, models, provider_request_id (sanitized), runtime_call_proven}`, `_sanitize_error`/`_sanitize_request_id` (secret/path redaction + 240 truncate), `PromptRegistryModelLinkage` + `summarize_prompt_registry_model_linkage()` (read-only registry linkage), `LlmModelRole.EXTRACTION` spec. `src/idis/services/prompts/registry.py` `PromptRegistry.get_version(prompt_id)` + `PromptArtifact.version` (SemVer) + `model_requirements.model_class`; `prompts/registry.yaml` `EXTRACT_CLAIMS_V1` (model_class `fast`).

### 2.5 Synthetic selected FULL harness (acceptance backbone)
- `tests/test_slice70_synthetic_full_execution_rehearsal.py` + `src/idis/evaluation/synthetic_strict_runtime_rehearsal.py` `build_bounded_synthetic_full_execution_rehearsal(...)`: one synthetic GDBS case through all FULL steps, in-memory app + `TestClient`, **clears live-provider env** (`SYNTHETIC_FULL_EXECUTION_LIVE_PROVIDER_ENV_KEYS` incl. `ANTHROPIC_API_KEY`, `IDIS_EXTRACT_BACKEND`) so it runs **deterministic** today; restores env after. Selected-run = `RunSource(type="deal_documents", document_ids=[...])` filters corpus pre-preflight (`runs.py` `_apply_run_source_to_preflight_corpus`).
- No-real-call patterns proven: inject fake client/factory; `DeterministicLLMClient`; `monkeypatch`/`patch.dict(os.environ, clear=True)`; `TestClient(raise_server_exceptions=False)` + in-memory stores; Slice82 `model_health_checker=` callback injection.

---

## 3. Where deterministic extraction is STILL used today
1. **`_build_extraction_llm_client()` silent fall-through** (`runs.py` ~:2578-2580) — returns `DeterministicLLMClient()` whenever `IDIS_EXTRACT_BACKEND != "anthropic"`, **even at execution time after the strict admission gate passed** (env re-read fresh; no runtime re-check). **This is the core gap.**
2. **SNAPSHOT mode** bypasses the strict gate entirely (`runs.py:227` only fires for `mode == "FULL"`).
3. Synthetic rehearsals deliberately clear live env → deterministic (by design, for safety).
4. Direct deterministic use in `scripts/llm_demo_one_deal.py` + extraction unit/e2e tests (`test_claim_extraction_snapshot_run_e2e.py`, `test_llm_claim_extractor.py`, `test_extraction_service.py` `DeterministicStubExtractor`).
> Separate (out of Slice83 extraction scope): `DeterministicAnalysisLLMClient`/`DeterministicScoringLLMClient` (analysis/debate/scoring) belong to **Slice 84**.

## 4. Exact strict-mode boundary for `IDIS_EXTRACT_BACKEND=anthropic`
Strict-live extraction MUST hold when **`is_strict_full_live_required()` is true AND `mode == FULL`** (the opt-in strict profile). Within that boundary:
- `IDIS_EXTRACT_BACKEND` MUST be `anthropic`; the extractor actually constructed/used MUST be the **live Anthropic client** (or an explicitly injected approved live-extractor for tests); the **deterministic extractor is forbidden**.
- Outside the boundary (SNAPSHOT, non-strict FULL, synthetic rehearsal with cleared env) deterministic remains allowed (unchanged).

## 5. How strict mode should block deterministic extractors (design)
- **Single chokepoint enforcement (recommended D-B):** at the extractor-client construction seam (`_build_extraction_llm_client()` refactored to take an injectable factory + a strict flag), if strict-live FULL is required and the resolved backend/client is not the approved live extractor → **fail closed** with a safe `STRICT_FULL_LIVE_BLOCKED`-class block (sanitized reason, e.g. `strict_live_extraction_requires_anthropic`), raised **before or during** the extract step — never silently falling back to deterministic. Keep the existing preflight admission gate (defense in depth).
- **Injectable seam (D-C):** introduce an `extractor_client_factory` (mirror Slice82 `client_factory((api_key)->client)` / `model_health_checker`) threaded through the run/extraction context, defaulting to the real `_build_extraction_llm_client` selection. Tests inject a fake live client → exercise the live branch with **no network**.

## 6. How missing/failed providers block safely
- **Missing** (no key/model, or backend≠anthropic) → the existing readiness/admission gate blocks at preflight (HTTP 409, safe details) — already works (Slice82); Slice83 adds the execution-time backstop (no silent deterministic).
- **Failed during a strict run** (live client raises) → the extract step records `status=FAILED` with a **safe error code** (new `STRICT_LIVE_EXTRACTION_PROVIDER_FAILED` or reuse `STRICT_FULL_LIVE_BLOCKED`, D-F) and a sanitized reason via the existing `_sanitize_error`/`SAFE_STEP_ERROR_MESSAGE` discipline — **never** the provider message/prompt/response/key. The run does not silently continue on deterministic output.

## 7. Provider provenance + prompt/model version in step summary (design)
Additively extend the extraction step `result_summary` (JSONB open dict — no schema/OpenAPI migration, confirm D-G) with a **safe** block, e.g.:
```
"extraction_provenance": {
  "provider": "anthropic",                 # closed constant / "deterministic"
  "models": {"extract_model": "<safe model name from env/health>"},
  "provider_request_id": "<sanitized>" | null,   # only if the (injected) live client exposes response.id; else null
  "runtime_call_proven": true|false,        # true only when a live call actually ran (injected fake counts as a real-path call, NOT a real network call)
  "prompt_id": "EXTRACT_CLAIMS_V1",
  "prompt_version": "1.0.0",                # PromptRegistry.get_version / PromptArtifact.version
  "prompt_model_class": "fast"
}
```
- **Reuse** Slice82 sanitizers + safe-token discipline + `PromptRegistry`/`PromptArtifact.version`. **Never** record API key, prompt body, response body, raw payload, or paths.
- **NARROW note:** capturing `provider_request_id` requires the live client to surface `response.id`. The production `AnthropicLLMClient` does not today (Slice82 follow-up). NARROW records `provider`+safe `models`+`prompt_id/version` unconditionally and sets `provider_request_id` only when the (possibly injected) client exposes a safe id — without rewriting the production `anthropic_client.py` call path (D-D).

## 8. Proving the acceptance with synthetic selected FULL, NO real calls
- Reuse the Slice70 synthetic FULL rehearsal backbone, but add an **opt-in strict-extraction acceptance path**: run a synthetic **selected** FULL with `IDIS_REQUIRE_FULL_LIVE=1` + `IDIS_EXTRACT_BACKEND=anthropic` and an **injected fake live extractor client** (returns valid claim JSON + a safe fake `response.id`; no network). Prove:
  1. the **live** extractor branch is selected and the **deterministic extractor is rejected** in strict FULL;
  2. the extract step `result_summary` carries the **safe provenance** (provider/model/prompt-version) with no leaks;
  3. **missing/failed provider blocks** with a safe reason (two sub-cases: missing config → preflight 409; injected failing client → safe FAILED step).
- Determinism/safety: synthetic GDBS corpus only (no private data), injected fake (no real Anthropic/network), no `IDIS_REQUIRE_FULL_LIVE` real run against live providers, env isolated/restored (Slice70 pattern).

## 9. Reuse map (exact files)
**Reuse unchanged (verify only):** Slice82 `llm_model_health.py` (sanitizers, `LlmModelHealthCheck`, `PromptRegistryModelLinkage`, `LlmModelRole.EXTRACTION`); `prompts/registry.py` (`get_version`, `PromptArtifact.version`, `model_class`); `step_provenance.py` safe-token pattern; strict admission gate + `build_strict_block_operator_safe_details` + `STRICT_FULL_LIVE_BLOCKED`; Slice70 synthetic FULL rehearsal + `TestClient`/in-memory stores; `_missing_model_env`, four-state provisioning fields.
**Touch (production) — NARROW:** injectable `extractor_client_factory` seam + execution-time strict enforcement (forbid deterministic in strict FULL, fail-closed safe reason) in `runs.py` extraction wiring; additive `extraction_provenance` in the extraction step `result_summary` (reuse sanitizers). **Out of scope:** real provider/network call by default, real-data FULL, `anthropic_client.py` call-path rewrite for request_id (Slice82 follow-up), analysis/debate/scoring live wiring (Slice84), DB/OpenAPI/schema migration, full `(prompt,version)→model` binding.

## 10. Decisions — confirm BEFORE Task 1
> **RESOLVED as-built:** D-A **NARROW**; D-B enforce at the extractor-construction seam + keep the preflight gate; D-C injectable `extractor_client_factory`; D-D safe provenance (provider/model/prompt-version) + `provider_request_id` only if safely available + no `anthropic_client.py` rewrite; D-E **SNAPSHOT non-strict**; D-F distinct safe code **`STRICT_LIVE_EXTRACTION_PROVIDER_FAILED`** (+ `STRICT_LIVE_EXTRACTION_REQUIRED`); D-G `result_summary` confirmed open `dict` (no schema change); D-H prompt **`EXTRACT_CLAIMS_V1`**.

- **D-A — SCOPE (key).** **NARROW (recommended):** execution-time strict enforcement (no deterministic in strict FULL) + injectable live-extractor seam + safe provenance in step summary + synthetic acceptance with an injected fake — **no real provider call in CI**, `runtime_call_proven` honest. **BROAD:** also capture `response.id` in the production `anthropic_client.py` path + a real opt-in provider probe (larger; not required by acceptance).
- **D-B — Enforcement point.** Centralized at the extractor-client construction seam (`_build_extraction_llm_client`, refactored) **+ keep the preflight admission gate** (recommended) vs per-step guard vs both.
- **D-C — Injectable seam shape.** `extractor_client_factory` param (mirror Slice82 `client_factory`) threaded via the run/extraction context, default = current real selection; tests inject a fake live client. Confirm the exact thread path (route → `build_run_context`/`_run_snapshot_extraction` → `LLMClaimExtractor`).
- **D-D — Provider metadata depth.** Record `provider`+safe `models`+`prompt_id/version` always; set `provider_request_id` only when the (injected) client exposes a safe id; **do NOT** modify `anthropic_client.py` call path in NARROW.
- **D-E — SNAPSHOT mode.** Leave SNAPSHOT bypassing strict (out of scope; acceptance is FULL) vs also enforce. Recommend leave.
- **D-F — Provider-failure error code.** New `STRICT_LIVE_EXTRACTION_PROVIDER_FAILED` vs reuse `STRICT_FULL_LIVE_BLOCKED`. Recommend a distinct, documented safe code (clarity), still sanitized.
- **D-G — Step-summary additive fields & contract.** Confirm `result_summary` is an open `dict[str,Any]` in the run-step API/OpenAPI response (so additive keys need **no** schema/OpenAPI change). If the response pins a closed schema, STOP and report (scope conflict).
- **D-H — Prompt binding.** Which prompt id drives extraction provenance (`EXTRACT_CLAIMS_V1`) and how to resolve its version safely (registry `get_version` vs metadata) without loading prompt bodies.

## 11. Scope boundary / not doing yet
No real provider/network call (CI injects a fake; any real opt-in probe is approval-gated, never CI); no real-data FULL run; no `IDIS_REQUIRE_FULL_LIVE=1` against live providers; no `anthropic_client.py` call-path rewrite; no analysis/debate/scoring live wiring (Slice84); no DB/OpenAPI/schema migration; no prompt-registry mutation; no Slice84. None unless a task's discovery proves it strictly required — then STOP and report.

## 12. Task breakdown (TDD; STOP after each)
- **Task 1 — Characterization (no prod change):** pin current truth (see §13). GREEN-on-arrival confirms the gap; justifies Tasks 2–5.
- **Task 2 — Injectable extractor-client seam:** refactor `_build_extraction_llm_client` to accept an injectable factory + thread it through the extraction/run context; default unchanged. No behavior change for existing paths (characterization stays green). Tests inject a fake live client.
- **Task 3 — Execution-time strict enforcement:** when strict-live FULL is required, forbid the deterministic extractor; fail closed with a safe reason (D-F); never silent fallback. RED tests first (strict→deterministic blocked; non-strict unchanged; missing→safe; failed→safe FAILED step, no leak).
- **Task 4 — Safe provenance in step summary:** additively record `extraction_provenance` (provider/models/prompt_id/version, sanitized request_id when available) via reused sanitizers. RED tests: fields present + no secret/prompt/response leak; deterministic path labels `provider="deterministic"`.
- **Task 5 — Synthetic selected FULL acceptance (opt-in strict, injected fake):** prove the two master-plan acceptance bullets with no real call (§8).
- **Task 6 — Docs/config reconciliation + full gate + independent review.**
- **Task 7 — Finish branch: open PR only.**

## 13. Proposed Task 1 (characterization, no production change)
Add `tests/test_slice83_strict_live_extraction_characterization.py` pinning (mocked/synthetic; **no real Anthropic call, no FULL run**):
1. `_build_extraction_llm_client()` selection: unset/`deterministic`→`DeterministicLLMClient`; `anthropic`+`ANTHROPIC_API_KEY`→`AnthropicLLMClient`; `anthropic` no key→`ValueError` (re-pin existing `test_llm_backend_selection` truth in the Slice83 file).
2. **No execution-time strict enforcement yet:** with `IDIS_REQUIRE_FULL_LIVE=1` but `IDIS_EXTRACT_BACKEND` unset, `_build_extraction_llm_client()` still returns `DeterministicLLMClient` (documents the gap; no exception today).
3. **No injectable extractor factory yet:** `inspect.signature(_build_extraction_llm_client)` has no `extractor_client_factory` param (justifies Task 2).
4. **No extraction provenance yet:** the extraction step `result_summary` keys (via `MethodologyExtractionExecutionRunResult.to_run_step_summary()` or the extract-step summary) contain **no** `extraction_provenance`/`provider`/`prompt_version` (justifies Task 4).
5. **Reuse available:** `LlmModelRole.EXTRACTION`, `LlmModelHealthCheck`, `_sanitize_request_id`, `PromptRegistry.get_version`/`PromptArtifact.version` exist and are importable (justifies reuse in Tasks 3–4).
6. **`result_summary` is an open `dict[str, Any]`** (additive-safe; D-G evidence).
GREEN-on-arrival = current truth confirmed (no repair). Mocked/synthetic only.

## 14. Verification gate (CI parity — from worktree root, `PYTHONPATH=src`)
`python -c "import idis; print(idis.__file__)"` · `ruff format --check .` · `ruff check .` · clear `.mypy_cache` then `mypy src/idis` · `python scripts/forbidden_scan.py --repo-root .` · `git diff --check` · targeted `pytest` (injected fake extractor; **no real Anthropic/network; no real FULL run**). DB-backed `*_postgres.py` only in CI. Contract/OpenAPI parse = N/A (no `scripts/*contract*`/`*openapi*` gate script).

## 15. Risks
- **Accidental real provider call / real FULL** — the live branch must be exercised only via an injected fake in CI; never set `IDIS_REQUIRE_FULL_LIVE=1` against live providers; never call the real-data FULL harness. (Highest risk.)
- **Leakage** — provenance must record only safe provider/model/prompt-id/version (+ sanitized request id); reuse the Slice82 sanitizer + safe-token discipline; never API key/prompt/response/path.
- **Blast radius** — refactoring `_build_extraction_llm_client` + threading a factory touches the run/extraction wiring; keep the default path byte-equivalent (characterization green) and treat any drift in extraction/FULL/strict tests as controlled (like Slice79/80/82).
- **Contract drift** — if `result_summary` is a closed API/OpenAPI schema (not open dict), additive provenance would need a schema change (out of scope) — STOP and report (D-G).
- **SNAPSHOT bypass** — leaving SNAPSHOT non-strict is intentional; document so it is a conscious boundary, not an oversight.

## 16. Open questions for you
1. **D-A:** NARROW (recommended) or BROAD?
2. **D-B:** centralized seam enforcement + keep preflight gate (recommended) — confirm.
3. **D-C:** injectable `extractor_client_factory` thread path — confirm the seam.
4. **D-D:** record provider/model/prompt-version always; request_id only if the injected client exposes it; no `anthropic_client.py` change — confirm.
5. **D-E:** leave SNAPSHOT non-strict — confirm.
6. **D-F:** distinct safe `STRICT_LIVE_EXTRACTION_PROVIDER_FAILED` code vs reuse `STRICT_FULL_LIVE_BLOCKED`.
7. **D-G:** confirm `result_summary` is open `dict` in the API/OpenAPI contract (no schema change).
8. **D-H:** prompt id/version source for extraction provenance (`EXTRACT_CLAIMS_V1`).
