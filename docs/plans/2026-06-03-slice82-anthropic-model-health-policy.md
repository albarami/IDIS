# Slice82 — Anthropic Model Health And Policy — Implementation Plan

> **For Claude / agentic workers:** REQUIRED SUB-SKILL: `superpowers:executing-plans` (or `subagent-driven-development`) task-by-task. Per task: `test-driven-development` (RED → verify red → minimal GREEN → verify), `verification-before-completion` before any status claim, `using-git-worktrees` already done, `finishing-a-development-branch` before commit/PR. **Reuse before create. STOP for approval after each task.** The §9 decisions are **RESOLVED as-built** (see §0). **Tasks 1–5 are complete and verified; Task 6 = docs reconciliation + full gate + independent review.**

**Goal:** Prove **configured live (Anthropic) model health without running FULL** — add a **strict model-health check** (config/credential/model validity; safe; no private data; no real provider call by default) and wire it into strict-readiness + provisioning-truth so the report **distinguishes `configured` vs `health-checked` vs `runtime-call-proven` vs `not-yet-FULL-used`** for the Anthropic components, with safe provider request-id/model metadata and prompt/model-registry-policy linkage.

**Architecture:** The strict-readiness stack (Slices 56–80) already inventories four Anthropic-backed components — **extraction, debate, analysis, scoring** — but their health is **config-presence only** (`LIVE_WIRED_AND_USED` vs `MISSING_CREDENTIALS`) and inventory health is fixed `"not_implemented"` ("Slice 56 has no real live model health check", `strict_full_live.py:2048`). The provisioning-truth report already has the four-state fields (`configured`, `health_checked`, `runtime_call_proven` [fixed False], `full_run_used` [fixed False]) but no Anthropic health checker. Slice82 adds a **standalone `llm_model_health.py`** — mirroring `embedding_health.py` (the OpenAI-provider health analog: injectable `client_factory`, safe constant probe, no real call in CI) — and wires an injectable `model_health_checker` into `build_strict_full_live_readiness_report` + `build_strict_provisioning_truth_report` (mirroring the Slice79/80 OCR/media health wiring), making the four states explicit for the Anthropic components. **No FULL run, no real provider call in CI, no extraction/claims, no DB/OpenAPI/schema.**

**Tech Stack:** Python 3.11+, Pydantic v2, FastAPI, pytest, ruff (CI-parity ≥0.15), mypy. `anthropic` SDK already a dependency (used by `anthropic_client.py`); no new deps. Tests use an injectable mock client factory — **no real Anthropic call**.

**Base:** branch `slice82-anthropic-model-health-policy` @ `3a8de3e3914450237b5af971bbe11803d876dffc` (= `origin/main`, Slice81 merged via PR #93), worktree `C:/Projects/IDIS/IDIS-slice82` (baseline ruff + mypy green; smoke `test_slice56_strict_runtime_foundation.py` 14 passed; `idis.__file__` pinned to this worktree's `src`).

---

## 0. As-Built Status (Tasks 1–5 complete; Task 6 = docs/gate/review)

**Status:** Tasks 1–5 **complete and verified** (TDD; mocked; **no real provider call; no FULL run**). Task 6 (this) = docs/config reconciliation + full local gate + independent review. **D-A = NARROW** (confirmed); all §9 decisions resolved (see the note in §9).

**As-built task map** (the §11 seven-task plan executed as six tasks — readiness + provisioning wiring landed together in Task 3):
- **Task 1 — Characterization** (`tests/test_slice82_model_health_characterization.py`): pinned the pre-Slice82 truth; updated to as-built truth after Task 3 (controlled drift).
- **Task 2 — `src/idis/services/llm_model_health.py`** (standalone): `LlmModelRole`, `LlmModelHealthStatus`, `LlmModelHealthCheck` (`extra="forbid"`), `check_llm_model_health(...)`. Tests: `tests/test_llm_model_health.py`.
- **Task 3 — Wire into strict readiness + provisioning** (`strict_full_live.py`, `strict_provisioning_truth.py`): injectable `model_health_checker((env, role) -> LlmModelHealthCheck)`; the 4 LLM components consume it fail-closed; inventory health is health-status-driven (`healthy`/`configured_failed`/`missing_config`, replacing `not_implemented`); provisioning forwards the checker and sets `runtime_call_proven` (opt-in only). Tests: `tests/test_slice82_model_health_strict_wiring.py`.
- **Task 4 — Provider metadata + thin registry linkage** (`llm_model_health.py`): safe `provider_request_id`/`provider`/`models` capture on the opt-in probe; `PromptRegistryModelLinkage` + `summarize_prompt_registry_model_linkage(...)`. Tests: `tests/test_slice82_model_health_metadata_registry.py`.
- **Task 5 — Acceptance proof** (`tests/test_slice82_model_health_acceptance.py`): all four states proven across readiness + provisioning.

**As-built behavior (NARROW):**
- **No-network default health.** `check_llm_model_health` validates backend selection + `ANTHROPIC_API_KEY` presence + model-name presence only; it never constructs a client on the default path. States: `HEALTHY` (configured) / `MISSING_CREDENTIALS` / `DISABLED` (backend ≠ anthropic) / `FAILED` (unsupported backend or probe failure).
- **Opt-in runtime-call-proven only.** `runtime_call_proven=True` requires the explicit opt-in probe (`run_probe=True` + injectable `client_factory`; provisioning additionally requires `allow_local_strict_health_probes=True`). Default + CI = `False`; tests always inject a fake — **no real Anthropic call**.
- **Four-state distinction across readiness + provisioning.** configured (`config_present`/`configured`) · health-checked (readiness inventory `health_check_status`: `healthy`/`configured_failed`/`missing_config`) · runtime-call-proven (provisioning `runtime_call_proven`, opt-in) · not-yet-FULL-used (`full_run_used=False`, always pre-FULL).
- **Provisioning `health_checked=False` nuance.** The provisioning `health_checked` field keeps its Slice72 meaning ("an opt-in local PROBE was attempted") and stays `False` for the statically-not-probed Anthropic components — deliberately (Slice72 anti-overclaiming, `test_strict_provisioning_truth_marks_static_runtime_checks_not_run`). The no-network "health-checked" signal is surfaced at the **readiness inventory** layer instead (documented + tested: `test_provisioning_four_state_truth_is_explicit_and_split_by_design`).
- **Safe provider metadata/request-id capture.** The health result carries `provider_request_id` (sanitized), `provider`, `models` (config-derived names), `role`; failures carry only the exception **class name**. Strict readiness/provisioning never surface the request id or raw metadata. No API key/prompt/response/path/payload leak (leak-guard tests).
- **Thin registry mismatch diagnostic.** `summarize_prompt_registry_model_linkage()` reads `prompts/registry.yaml` **read-only** and reports `provider_mismatch=True` (registry model classes → OpenAI names vs runtime → Anthropic), label-only (prompt IDs / model classes / model-name strings already in the registry). **No registry mutation; no prompt body / env-value leak.**
- **Boundaries honored.** No FULL run; no real provider/network call by default; no `anthropic_client.py` call-path change; no DB/OpenAPI/schema; no prompt-registry mutation; no Slice83.

**Config/docs reconciliation (Task 6):** `.env.example` already documents the six model/provider env vars with safe placeholders and `deterministic` (off/no-network) defaults — **accurate, no edit**. `docs/architecture/strict_full_live_readiness.md` is a Slice-53-pinned foundational audit whose classification vocabulary (incl. `configured-but-failed-health-check`) and model-env list remain accurate at its abstraction level — **no edit**. No CLI/runbook surface describes model-health internals — **N/A**.

**Remaining follow-ups (out of Slice82 scope):**
- Capture `response.id`/model safely in the production `anthropic_client.py` extraction path (Slice83+).
- Deeper `(prompt_id, version) → model_name` binding + reconcile `registry.yaml` OpenAI names vs Anthropic runtime (BROAD).
- Real opt-in provider health probe execution in an approved non-CI environment.
- (Optional) also surface the no-network model health in the provisioning `health_checked` field for Anthropic components without breaking the Slice72 anti-overclaiming contract — deferred; the current readiness/provisioning split is intentional.

---

## 1. Master Plan text (verbatim)
> ### Phase C: Live LLM Runtime
>
> #### Slice 82: Anthropic Model Health And Policy
>
> **Goal:** Prove configured live model health without running FULL.
>
> **Scope:**
> - Extract/default debate/arbiter/scoring model envs.
> - Minimal safe health checks with no private data.
> - Request IDs/provider metadata captured safely.
> - Prompt/model registry linkage.
>
> **Acceptance:**
> - Strict readiness distinguishes configured, health-checked, runtime-call-proven, and not-yet-FULL-used.

---

## 2. Discovery — what already exists (verified; exact refs)

### 2.1 Strict readiness — Anthropic components (config-only today)
`src/idis/services/runs/strict_full_live.py`:
- 4 LLM readiness components, **status = config-presence only**: `_live_llm_model_clients` (:765-799, extract), `_analysis` (:801-820), `_debate_layer_1` (:822-844), `_scoring` (:892-911) → `LIVE_WIRED_AND_USED` if env present else `MISSING_CREDENTIALS` (`_missing_model_env` :2649-2661).
- `StrictComponentStatus` (:203-212): `LIVE_WIRED_AND_USED`, `CODE_EXISTS_BUT_NOT_WIRED`, `CONFIGURED_BUT_FAILED_HEALTH_CHECK`, `MISSING_CREDENTIALS`, `MISSING_INFRASTRUCTURE`, `NOT_IMPLEMENTED`. (Note: a `CONFIGURED_BUT_FAILED_HEALTH_CHECK` status already exists — useful for Slice82.)
- `_inventory_llm_item` (:2027-2055) builds the 4 "Anthropic extraction/debate/analysis/scoring" inventory rows; **health fixed `"not_implemented"`** when configured (:2048 comment: "Slice 56 has no real live model health check").
- `STRICT_MODEL_ENV_VARS` (:144): `IDIS_EXTRACT_BACKEND`, `IDIS_DEBATE_BACKEND`, `ANTHROPIC_API_KEY`, `IDIS_ANTHROPIC_MODEL_EXTRACT`, `IDIS_ANTHROPIC_MODEL_DEBATE_DEFAULT`, `IDIS_ANTHROPIC_MODEL_DEBATE_ARBITER`.
- **Health-checker injection pattern (the exact shape to mirror):** `build_strict_full_live_readiness_report` (:408-426) accepts 5 injectable `*_health_checker` params (neo4j/embedding/pgvector/ocr/media); each is threaded via a `_X_health(env, checker, …)` wrapper (e.g. `_media_health` :2205-2212) and consumed (:454-458). **No `model_health_checker` / `anthropic_health_checker` yet.**

### 2.2 Provisioning truth — four-state fields exist, Anthropic not probed
`src/idis/services/runs/strict_provisioning_truth.py`:
- `_strict_provisioning_component` (:172-215) emits **`configured`** (`config_present`, :197), **`health_checked`** (probe attempted + status, :198-199), **`runtime_call_proven`** (**fixed `False`**, :200), **`full_run_used`** (**fixed `False`**, :201).
- `_STATIC_NOT_PROBED_COMPONENTS` (:39-56) includes `"Anthropic extraction"`, `"Anthropic debate"`, `"Anthropic analysis"`, `"Anthropic scoring"` — currently static-only, never locally probed.
- Static-health + opt-in local-probe pattern: 5 `_static_*_health` (:494-533) + 5 `_*_local_probe_status` (:331-434) + `_build_local_probe_statuses`. No Anthropic static/probe.

### 2.3 Anthropic client (anthropic_client.py)
`src/idis/services/extraction/extractors/anthropic_client.py`: `AnthropicLLMClient` — real `anthropic.Anthropic(api_key, timeout=120)` (:64); the **only** network call is `messages.create(...)` (:94, retry/backoff). **Captures NO request-id/provider metadata** — only `response.content[0].text` (:101-104). Backend/model selected in `api/routes/runs.py` (:2559-2620, 2236-2283).

### 2.4 Health-check canonical pattern (mirror target)
`ocr_health.py` / `media_health.py` / `rag/embedding_health.py` / `rag/pgvector_health.py` / `persistence/neo4j_driver.py` share: a `StrEnum` status (HEALTHY/DISABLED/MISSING_*/FAILED), a `BaseModel` result with `ConfigDict(extra="forbid")` (status, config_present|enabled, missing_env_vars|missing_dependencies, sanitized `error`, + safe metadata), `.healthy()/.missing()/.failed()` factories, injectable probes (Protocol), and a secret/path sanitizer (regex + 240-char truncate; exceptions → class name only). **`embedding_health.py` is the closest analog** (OpenAI provider health): injectable `EmbeddingClientFactory` (:32-37) + a **safe constant probe** `EMBEDDING_HEALTH_PROBE_INPUT = "idis-embedding-health-check"` (:26); tests inject a mock factory (no real call). Slice73 wires these as opt-in local probes (`allow_local_strict_health_probes`).

### 2.5 Request-id / provider metadata (safe-capture machinery)
`api/middleware/request_id.py` (uuid4 request id), `observability/tracing.py` `set_span_attributes` (:298-316, safe attrs only — no secrets/bodies). Anthropic responses expose safe `.id` / `.model` / `.usage` (not currently captured).

### 2.6 Prompt/model registry
`services/prompts/registry.py`: `PromptArtifact` has `model_requirements` (model_class, context window, json/tool support) + `fallback_policy` (model-name list) but **no explicit prompt→model-name binding**; `RegistryPointer` maps prompt_id→version per env. `prompts/registry.yaml` defines `model_classes` (fast/reasoning/verifier) with **OpenAI-named defaults (`gpt-4o*`)** — a mismatch vs the Anthropic runtime models (env). `docs/IDIS_Prompt_Registry_and_Model_Policy_v6_3.md` §7 defines the model-policy contract. **No runtime structure maps (prompt_id, version)→(model_class, model_name).**

### 2.7 FULL-run boundary (what Slice82 must NOT trigger)
`IDIS_REQUIRE_FULL_LIVE` → `is_strict_full_live_required` (`strict_full_live.py:302-313`); gate enforced at `api/routes/runs.py:227-240` (HTTP 409 `STRICT_FULL_LIVE_BLOCKED`) + worker + `real_example_run_harness`. **Slice82 must only call `build_strict_full_live_readiness_report` / `build_strict_provisioning_truth_report` (readiness/inventory, no execution); never `run_real_example_full_run_harness`, never set `IDIS_REQUIRE_FULL_LIVE=1`, never make a real provider call.**

### 2.8 Net-new gaps (Slice82 fills, NARROW)
1. **No `llm_model_health` / `anthropic_model_health` module** (grep: none).
2. **No `model_health_checker` injectable** on the strict builders; Anthropic inventory health fixed `"not_implemented"`.
3. **No four-state distinction wired for Anthropic** (config-only today; `runtime_call_proven`/`full_run_used` fixed False generically).
4. **No safe provider request-id/model capture**.
5. **No registry linkage check**.

---

## 3. Anthropic / model env vars + defaults (verified)
| Env var | Reader | Default | Drives |
|---|---|---|---|
| `IDIS_EXTRACT_BACKEND` | `runs.py:2570` | `deterministic` | extract LLM (`anthropic`→live) |
| `IDIS_DEBATE_BACKEND` | `runs.py:2248,2273,2602` | `deterministic` | debate + scoring + analysis LLM |
| `ANTHROPIC_API_KEY` | `anthropic_client.py:51` | (none; fail-closed) | all Anthropic-backed calls |
| `IDIS_ANTHROPIC_MODEL_EXTRACT` | `anthropic_client.py:60` | `claude-sonnet-4-20250514` | extract model |
| `IDIS_ANTHROPIC_MODEL_DEBATE_DEFAULT` | `runs.py:2253,2278,2612` | `claude-sonnet-4-20250514` | debate roles + scoring + analysis |
| `IDIS_ANTHROPIC_MODEL_DEBATE_ARBITER` | `runs.py:2614` | `claude-opus-4-20250514` | debate arbiter |

No separate scoring backend/model env (scoring reuses DEBATE backend + DEBATE_DEFAULT model). `.env.example` documents all six (off by default: backends `deterministic`).

---

## 4. The four acceptance states — proposed definitions
For each Anthropic component (extract / debate-default / debate-arbiter / scoring / analysis):
- **configured** — `IDIS_*_BACKEND=anthropic` + `ANTHROPIC_API_KEY` present + the relevant `IDIS_ANTHROPIC_MODEL_*` present (already = `config_present` / not `MISSING_CREDENTIALS`).
- **health-checked** — the new `check_llm_model_health` ran and validated backend selection, credential presence, and model-name validity (safe, no network by default) → `HEALTHY` / `CONFIGURED_BUT_FAILED_HEALTH_CHECK`. (Net-new; replaces inventory `"not_implemented"`.)
- **runtime-call-proven** — a **real** minimal provider call succeeded (opt-in local probe, approval-gated; or a FULL run). In NARROW Slice82 this stays **`False`** unless the opt-in real probe is explicitly enabled+approved; the default health check is config/validity only (so a component can be `health-checked` but **not** `runtime-call-proven`).
- **not-yet-FULL-used** — `full_run_used` = `False` (no FULL run in Slice82; stays False until Slice83+).

This makes the report distinguish all four (acceptance), with NARROW proving the first two for the Anthropic components and explicitly leaving the latter two `False` (honest "not yet").

---

## 5. Minimal safe health check (design — mirror `embedding_health.py`)
**New `src/idis/services/llm_model_health.py`** (name TBD — D-G):
- Status `StrEnum`: `HEALTHY` / `DISABLED` (backend ≠ anthropic) / `MISSING_CREDENTIALS` / `FAILED`.
- Result `BaseModel` (`extra="forbid"`): `status`, `configured: bool`, `backend: str` (safe), `missing_env_vars: list[str]` (safe names only), `models: dict[str,str]` (role→safe model name), `runtime_call_proven: bool`, `provider_request_id: str | None` (safe), `error: str | None` (sanitized+truncated). Factories `.disabled()/.healthy()/.missing()/.failed()`.
- **Default path = NO network:** validate backend == anthropic, `ANTHROPIC_API_KEY` present, model names present/non-empty → `HEALTHY` (configured, `runtime_call_proven=False`). Missing → `MISSING_CREDENTIALS`. Backend not anthropic → `DISABLED`.
- **Opt-in real probe (gated):** injectable `client_factory` (mirror `EmbeddingClientFactory`); when an opt-in flag is set AND approved, send a **minimal safe message** (constant, e.g. `"health"`, no private data, `max_tokens` tiny) → on success `runtime_call_proven=True` + capture `response.id`/`response.model` safely. **CI/tests always inject a mock factory — no real call.**
- Reuse the secret/path sanitizer; exceptions → class name only.

---

## 6. Request-id / provider metadata — safe capture
- The health result records `provider_request_id` (from `response.id` when the opt-in probe runs) + safe model names — never the API key, prompt, or response body.
- Optionally set safe span attributes (`tracing.set_span_attributes({"anthropic.model": …})`). **NARROW does not modify the production `anthropic_client.py` call path** (capturing `response.id` for the FULL extraction path is Slice83+); the safe capture lives in the health module.

---

## 7. Prompt/model registry linkage — verification (thin)
NARROW verifies, safely: (a) the configured Anthropic model envs are present + internally consistent; (b) the prompt registry / model-policy exists and its model-class policy is reflected (presence check). **Full `(prompt_id,version)→model_name` binding is BROAD/out of scope.** **Flag the known mismatch:** `prompts/registry.yaml` `model_classes` use OpenAI names (`gpt-4o*`) while the runtime uses Anthropic models — Slice82 surfaces this as a readiness note, not a silent pass. (Confirm depth in D-E.)

---

## 8. Reuse map (exact files)
**Reuse unchanged (verify only):** `embedding_health.py` (pattern), `ocr_health.py`/`media_health.py` (sanitizer + factory shape), the `*_health_checker` injection wiring in `strict_full_live.py` + `strict_provisioning_truth.py`, `STRICT_MODEL_ENV_VARS`, the four-state provisioning fields, `request_id`/`tracing` safe-capture, prompt `registry.py`.
**Touch (production) — NARROW:** new `llm_model_health.py`; injectable `model_health_checker` on both strict builders + the 4 LLM components consume it (inventory health `healthy`/`configured_failed` instead of `not_implemented`); provisioning static-health + opt-in local probe for the Anthropic components; map the four states. **Out of scope:** real provider call by default, FULL run, extraction/claims, `anthropic_client.py` call-path change, DB/OpenAPI/schema, full prompt→model binding.

---

## 9. Decisions — confirm BEFORE Task 1
> **RESOLVED as-built:** D-A **NARROW**; D-B/D-D no-network default + opt-in approval-gated real probe + provider metadata in the health result only (no `anthropic_client.py` change); D-C four-state mapping as §4; D-E **thin** registry mismatch diagnostic; D-G module **`llm_model_health.py`**.

- **D-A — SCOPE (key).** **NARROW (recommended):** strict model-health module + wire fail-closed into strict readiness/provisioning + make the four states explicit for the Anthropic components + acceptance proof, with **no real provider call in CI** (injectable mock) and `runtime_call_proven`/`full_run_used` honestly `False` by default. **BROAD:** also add the opt-in real provider health call + capture in the live extraction path + full prompt→model binding (larger; not required by acceptance).
- **D-B — Real-call policy (LOCKED proposal).** Health check is **config/credential/model-validity only by default (NO network)**; a real minimal "health" call is **opt-in + approval-gated** (mirrors Slice73 `allow_local_strict_health_probes`), never in CI. CI/tests inject a mock `client_factory`.
- **D-C — Four-state mapping (LOCKED proposal).** As §4: configured (env), health-checked (new check), runtime-call-proven (opt-in real probe only → default False), not-yet-FULL-used (full_run_used False).
- **D-D — Provider metadata capture (LOCKED proposal).** Capture `provider_request_id`/model in the **health result** only (safe); do **not** modify `anthropic_client.py` in NARROW.
- **D-E — Registry linkage depth (open).** Thin presence/consistency check + surface the `registry.yaml` OpenAI/Anthropic model-name mismatch — vs deeper `(prompt,version)→model` binding (BROAD). Recommend thin.
- **D-F — Determinism/CI (LOCKED proposal).** Mock `client_factory` + injectable `model_health_checker`; no real Anthropic/network; no FULL run; no `IDIS_REQUIRE_FULL_LIVE=1`.
- **D-G — Module name (open).** `llm_model_health.py` (provider-neutral, recommended) vs `anthropic_model_health.py`. Status enum value for configured-but-not-network-proven: reuse `CONFIGURED_BUT_FAILED_HEALTH_CHECK` only for failures; healthy-config = `HEALTHY` with `runtime_call_proven=False`.

---

## 10. Scope boundary / not doing yet
No FULL run; **no real provider/network call (CI always mocks; real probe opt-in + approval-gated)**; no extraction/claims/live LLM execution; no `anthropic_client.py` call-path change; no full prompt→model binding; no DB/OpenAPI/schema migration; no Slice83. None unless a task's discovery proves it strictly required — then STOP and report.

## 11. Task breakdown (TDD; STOP after each)
- **Task 1 — Characterization (no prod change):** pin current truth (the 4 LLM components are config-only; inventory health `"not_implemented"`; provisioning Anthropic in `_STATIC_NOT_PROBED_COMPONENTS` with `runtime_call_proven`/`full_run_used` False; no `model_health_checker` param; no `llm_model_health` module; anthropic client captures no request-id; the model env vars/defaults). GREEN-on-arrival = confirmation; justifies Tasks 2–3.
- **Task 2 — `llm_model_health.py` (standalone):** mirror `embedding_health.py`; status/result/factories/sanitizer + injectable `client_factory`; default no-network config/validity health; opt-in mock real probe. Unit tests via injected fakes (no real call).
- **Task 3 — Wire into strict readiness:** injectable `model_health_checker`; the 4 LLM components + inventory consume it (health `healthy`/`configured_failed`, fail-closed); the four-state distinction surfaced; no `_safe_summary`-style leak; no-drift on strict consumers (Slices 56–81).
- **Task 4 — Wire into provisioning truth:** static model health + opt-in local probe for the Anthropic components; `runtime_call_proven` set True only by an opt-in (mocked) real probe; `full_run_used` stays False.
- **Task 5 — Request-id/provider metadata + registry linkage (thin):** safe capture in the health result; registry/model-policy presence + mismatch surfacing.
- **Task 6 — Acceptance proof:** strict readiness distinguishes configured / health-checked / runtime-call-proven / not-yet-FULL-used for the Anthropic components (deterministic, mocked). Leak guards (no API key/prompt/response/secret).
- **Task 7 — Config/docs + full gate + independent review.**

## 12. Verification gate (CI parity — from worktree root, `PYTHONPATH=src`)
`python -c "import idis; print(idis.__file__)"` · `ruff format --check .` · `ruff check .` · clear `.mypy_cache` then `mypy src/idis` · `python scripts/forbidden_scan.py --repo-root .` · `git diff --check` · targeted `pytest` (mocked client factory; **no real Anthropic/network**). DB-backed `*_postgres.py` only in CI.

## 13. Risks
- **Accidental real provider call / FULL run** — health check must default to NO network; real probe opt-in + approval-gated; never call the FULL harness or set `IDIS_REQUIRE_FULL_LIVE`. (Highest risk.)
- **Leakage** — API key / prompt / response body / model paths must never reach the safe result; reuse the sanitizer + safe-fields-only discipline; capture only `request_id`/model.
- **Blast radius on strict consumers** — adding a `model_health_checker` + changing Anthropic inventory health from `"not_implemented"` may drift Slice56/72/73 tests that assert the current values; treat as controlled drift (inject `LLMModelHealthCheck.healthy()`), like Slice79/80.
- **Registry mismatch** — `registry.yaml` OpenAI names vs Anthropic runtime: surface, don't "fix" silently (BROAD).

## 14. Open questions for you
1. **D-A:** NARROW (recommended) or BROAD?
2. **D-B/D-D:** confirm health is **config/validity only by default, no network**; real probe opt-in + approval-gated; provider metadata captured in the health result only (no `anthropic_client.py` change).
3. **D-E:** registry linkage depth — thin presence/consistency + surface the OpenAI/Anthropic mismatch (recommended) vs deeper binding?
4. **D-G:** module name `llm_model_health.py` (recommended) vs `anthropic_model_health.py`.

### Proposed Task 1 (characterization, no production change)
Add `tests/test_slice82_model_health_characterization.py` pinning: (a) `_live_llm_model_clients`/`_analysis`/`_debate_layer_1`/`_scoring` are config-presence only (`LIVE_WIRED_AND_USED` when `STRICT_MODEL_ENV_VARS` present, `MISSING_CREDENTIALS` when absent) — via `build_strict_full_live_readiness_report`; (b) the Anthropic inventory items have `health_check_status == "not_implemented"` when configured; (c) `build_strict_full_live_readiness_report` / `build_strict_provisioning_truth_report` have **no** `model_health_checker`/`anthropic_health_checker` param (inspect.signature) — justifying Task 3; (d) `importlib.util.find_spec("idis.services.llm_model_health") is None` — justifying Task 2; (e) provisioning report: Anthropic components present with `runtime_call_proven`/`full_run_used` False; (f) document the model env vars/defaults. GREEN-on-arrival = current truth confirmed (no repair). Mocked/synthetic; **no real Anthropic call, no FULL run.**
