# Slice86 — Enrichment Execution And Provenance — Implementation Plan

> **For Claude / agentic workers:** REQUIRED SUB-SKILL: `superpowers:executing-plans` (or `subagent-driven-development`) task-by-task. Per task: `test-driven-development` (RED → verify red → minimal GREEN → verify), `verification-before-completion` before any status claim, `using-git-worktrees` already done, `finishing-a-development-branch` before commit/PR. **Reuse before create. STOP for approval after each task.** The §9 decisions are locked (D-A full G1–G7 · D-B descriptor flag · D-C all-15-mandatory default · D-D from_cache field · D-E grade rule as specified · D-F run_summary+evidence_index · D-G narrow identifier-mismatch · D-H redaction-only after Finnhub primary-doc verification failed · D-I Layer-1 debate out). **Status: Tasks 1–7 complete; Task 8 (docs/gate/review) in progress; Task 9 = PR only.** §0 records the as-built result; the sections below are preserved as the original discovery/planning record.

**Goal:** Make FULL enrichment execution **observable and policy-driven**: a per-provider hit/miss/error/cache/blocked ledger in the step summary; provider errors fatal in strict mode **unless policy says optional**; provider provenance with **source-grade mapping** fed forward so it is **visible in the VC package** (product bundle); plus the two Slice85 follow-ups (FRED/Finnhub/FMP URL-key hardening, httpx request-log redaction).

**Architecture:** Discovery shows the enrichment EXECUTION engine is **already built and wired** (Slice57/85 + earlier): 15 connectors, rights gate, BYOL credentials, cache, audit events, a canonical FULL ENRICHMENT step with a strict fail-closed backstop, and `enrichment_refs` already feeding **analysis** (NFF-validated `AnalysisContext.enrichment_refs`), **scoring** (engine-validated dimension refs) and **Layer 2 IC challenge**. What's genuinely missing is the **observability + policy layer** the master plan names: no per-provider ledger (MISS/ERROR/cache silently dropped from the summary), no optional-vs-fatal policy (uniform strict raise), no source-grade mapping (RightsClass ⊥ SourceGrade), nothing surfacing enrichment in the deliverables/VC package (the strict matrix self-documents `provenance_ref_on_hit_not_final_output_visible`), and no data-level conflict checks. Slice86 adds these **additively** in the proven Slice83/84 style (additive blocks in the open `result_summary`, sanitized, leak-tested) and threads enrichment into the product bundle. **No real provider call in CI; no DB migration; no Slice87.**

**Tech Stack:** Python 3.11+, Pydantic v2, FastAPI, pytest, ruff (CI-parity), mypy, httpx (connectors use injectable `httpx.Client`/MockTransport in tests). Postgres only via existing repos; no new tables.

**Base:** branch `slice86-enrichment-execution-provenance` @ `8d0f3ed023393622f63ec784bba45d51045ec3ab` (= `origin/main`, Slice85 merged via PR #98), worktree `C:/Projects/IDIS/IDIS-slice86`. Baseline green: import proof pinned to this worktree's `src` · `ruff format --check .` 756 ok · `ruff check .` ok · clean-cache `mypy src/idis` 393 files ok · smoke (enrichment orchestration/rights/cache/connectors-registry + Slice57 + Slice85 acceptance + strict readiness) = **82 passed**.

---

## 0. AS-BUILT (Tasks 1–7 complete)

- **Task 1 — Characterization** (`tests/test_slice86_enrichment_execution_provenance_characterization.py`, 14 tests): pinned the pre-change truth; items 1/3/4/5/6/8/9 drift-flipped honestly as Tasks 2–6 landed.
- **Task 2 — Optional-provider strict policy (G1):** `ProviderDescriptor.optional_in_strict: bool = False` + `register(..., optional_in_strict=...)`; **all 15 default providers mandatory** (D-C); `_run_full_enrichment` gates its three strict-fatal branches on `strict_fatal = strict and not optional` (optional failures take the non-strict recorded-and-continued handling); `list_providers()` exposes the flag; strict matrix surfaces `optional_in_strict` + `strict_optional_continue_on_error`. Audit payloads deliberately unchanged (service is policy-agnostic; the ledger records outcomes).
- **Task 3 — Ledger + cache visibility (G2/D-D):** additive `EnrichmentResult.from_cache` (set only on cache-served copies via `model_copy`; stored entries unmarked) + additive `enrichment_ledger` in the step summary: per-provider safe rows `{provider_id, status, from_cache, rights_class, optional_in_strict, ref_id, source_grade, conflicts}` + counts `{hit, miss, error, blocked_rights, blocked_missing_byol, cache_hits}`. Legacy summary fields/semantics untouched.
- **Task 4 — Source grade + VC visibility (G3/G4):** `src/idis/services/enrichment/source_grade.py` — GREEN→B, YELLOW→C, RED+BYOL→C, RED w/o BYOL→D, **never A**, computed at summary time (`has_byol = requires_byol and status != BLOCKED_MISSING_BYOL`), never persisted into `EnrichmentProvenance`; enrichment threaded orchestrator → `_run_full_deliverables(enrichment_evidence=…)` → `ProductBundleExporter`: `run_summary` gains 7 enrichment fields (a status token + 6 counts), `evidence_index` gains the whitelist-sanitized `enrichment_evidence` package (no new artifacts; count stays 14); matrix `provenance_output_status` flipped to `enrichment_package_output_visible` for registered providers.
- **Task 5 — Narrow conflict checks (G5/D-G):** `src/idis/services/enrichment/conflicts.py` — per HIT, shared-field comparison of request query identifiers vs `provenance.identifiers_used` (case/whitespace-insensitive); mismatch → `{"code": "identifier_mismatch", "field": <name>}` on the ledger row — never fatal, never values; package propagates via `_safe_conflict_flags`.
- **Task 6 — URL-key + httpx hardening (G6/D-H):** **Finnhub header auth NOT verifiable from primary docs** (docs JS-rendered; Finnhub's own official Python client uses the `token` query param) → **redaction-only for all three**: `src/idis/services/enrichment/redaction.py` (`redact_secret_params` + idempotent `SecretParamRedactionFilter` on the `httpx` logger); FRED/Finnhub/FMP `*FetchError` messages scrub api_key/apikey/token values; behavior/parsing unchanged.
- **Task 7 — Acceptance** (`tests/test_slice86_enrichment_execution_provenance_acceptance.py`, 8 tests, no production change): both master-plan bullets proven end-to-end — mandatory fatality ×3 vs optional recorded-and-continued; full-outcome ledger incl. cache; real step summary → real exporter → run_summary counts + graded/conflict-flagged `enrichment_evidence` rows; no normalized payload copying; URL-key + planted-marker leak sweep over summaries, results, bundle JSON, and captured httpx logs.

**Guarantees:** no real provider call in any test (MockTransport/fakes; the Finnhub doc check used WebFetch on public pages only); no DB migration; no Layer-1 debate changes; no Slice87. **Out-of-slice follow-ups:** durable cache backend; enrichment-fact materialization (`enrichment_fact_ids`, needs migration); per-tenant optionality configuration; broader value-level conflict checks; IC-memo/screening prose sections for enrichment.

---

## 1. Master Plan text (verbatim, `docs/IDIS_FULL_LIVE_MASTER_PLAN_V2.md:263-275`)
> #### Slice 86: Enrichment Execution And Provenance
>
> **Goal:** Execute approved enrichment providers in FULL and feed outputs forward.
>
> **Scope:**
> - Rights/BYOL policy.
> - Hit/miss/error/cache/blocked ledger.
> - Provider provenance, source-grade mapping, conflict checks.
> - Feed enrichment into analysis/debate/scoring/deliverables.
>
> **Acceptance:**
> - Provider errors are fatal in strict mode unless policy says optional.
> - Enrichment provenance is visible in VC package.

---

## 2. ALREADY BUILT — reuse, do not rebuild (verified; exact refs at 8d0f3ed)

### 2.1 Execution engine + canonical FULL step (Slice57/85 + earlier)
- **15 connectors** (`src/idis/services/enrichment/connectors/*.py`) with `provider_id`/`rights_class`/`cache_policy`/`fetch()`; registry `_build_default_registry` (`service.py:369-409`); `ProviderDescriptor` (`registry.py:42-57`: provider_id, rights_class, cache_policy, requires_byol, connector — **no optionality flag**).
- **`EnrichmentService.enrich()`** (`service.py:96-259`): rights check (:139-153) → cache lookup (:156-172, emits `enrichment.cache_hit`) → BYOL credential load (:174-205, `BLOCKED_MISSING_BYOL` fail-closed) → fetch (:220-235, broad-except → fixed `provider_fetch_failed`) → cache persist (HIT-only, `cache_policy.py:199-207`) → audit (`enrichment.started/completed/failed/blocked`; rights gate emits `enrichment.rights_denied`).
- **Rights gate** (`rights_gate.py:55-186`): GREEN always; YELLOW with attribution; RED blocked in PROD without BYOL; HIGH-severity audit on deny.
- **Canonical step:** `StepName.ENRICHMENT` order 22 in FULL (`models/run_step.py:43,85,130,153,189`); `_run_full_enrichment` (`api/routes/runs.py:1231-1333`) iterates providers, returns `{provider_count, result_count, blocked_count, enrichment_refs}`; **strict backstop** raises `RuntimeError` on Exception (:1297-1299), `BLOCKED_RIGHTS`/`BLOCKED_MISSING_BYOL` (:1322-1323), `ERROR` (:1325-1326) — uniformly, no policy.
- **Provenance on HIT:** `EnrichmentProvenance` (`models.py:115-133`): provider_id, source_id, retrieved_at, rights_class, `raw_ref_hash` (SHA256, never raw), identifiers_used.

### 2.2 Feed-forward — partially done
- Orchestrator accumulates the ENRICHMENT summary's `enrichment_refs` (`orchestrator.py:1861-1891`) → **analysis** (`:2011-2043`; `_run_full_analysis` builds frozen `EnrichmentRef` models into `AnalysisContext.enrichment_refs`, `analysis/models.py:28-43,106-109` — NFF fail-closed: `no_free_facts.py:114-133`) → **scoring** (`ScoringEngine._validate_enrichment_refs`, `scoring/engine.py:76,135-169`; refs serialized into the LLM scorecard prompt, `llm_scorecard_runner.py:84-106,157,235-248`) → **Layer 2 IC challenge** (`layer2_ic_challenge.py:81,98` receives `enrichment_refs`).
- **Layer-1 debate consumes nothing** (zero enrichment mentions in `src/idis/debate/`); **deliverables receive `analysis_context` but surface nothing** (§3 G3).

### 2.3 Strict readiness + self-documented visibility gap
- Preflight matrix `EnrichmentProviderMatrixEntry` (`byol_credentials.py:171-184`): `strict_behavior` ∈ {`strict_fail_closed_on_error` (HEALTH_PASSED), `strict_blocks_until_byol_ready`, `not_registered_not_wired`} (:506-509); `provenance_output_status` is **hardcoded** `provenance_ref_on_hit_not_final_output_visible` for ALL registered providers (:440,452,463) and `not_output_visible` for unregistered (:476) — the codebase self-documents acceptance (2)'s gap.
- `_external_enrichment_apis` gate + execution backstop unchanged from Slice85 (`strict_full_live.py:703-732`).

### 2.4 Patterns + tests to mirror/reuse
- **Additive provenance pattern:** Slice83 `_build_extraction_provenance` / Slice84 `_build_{analysis,debate,scoring}_provenance` + `_build_debate_observability` in `runs.py`; sanitizers `_sanitize_request_id`/`_sanitize_error` (`llm_model_health.py:164-176`); leak-marker test discipline (Slice83/84/85 suites); `StepProvenance` safe-token model (`models/step_provenance.py`).
- **Deliverables/VC package:** `src/idis/deliverables/` (memo, screening, truth_dashboard, qa_brief, decline_letter, product_bundle, export, manifest_review); `_run_full_deliverables` (`runs.py:1687-1779`) consumes analysis_bundle/scorecard/analysis_context + graph/rag/layer2 evidence — **no enrichment param**; `ProductBundleExporter.export_bundle` (`product_bundle.py:77-90`) builds `run_summary` (:220-266, has graph/rag/layer2 counts, **no enrichment**) and `evidence_index` (:439-478, has graph/rag/layer2 packages, **no enrichment_package**); manifest sanitization pattern (`test_slice64_final_package_download.py:127-150`).
- **Slice13 conflict-check PLAN** (`methodology_external_intelligence_conflict_check_plan.py:1-247`): registry-metadata-level plan only (BYOL → BLOCKED-deferred, public → DEFERRED missing-query-identifiers) — **no data-level conflict check**.
- Tests: `test_enrichment_{service_orchestration,rights_gate,cache_policy_determinism,connectors_registry,api_e2e}.py`, 15 connector suites (httpx.MockTransport + ctx credential injection), `test_slice57_byol_enrichment.py`, 3 Slice85 suites. Baseline all green.

### 2.5 Slice85 follow-ups (in-scope inputs to this slice)
- **URL-key risk (contained today):** FRED/Finnhub/FMP embed the key in the URL (`fred.py:110-114`, `finnhub.py:109`, `fmp.py:109`); their `*FetchError` messages interpolate `str(last_error)` (`fred.py:232`, `finnhub.py:227`, `fmp.py:233`) where `str(httpx.HTTPStatusError)` carries the full URL; every such error is caught in-connector and replaced by fixed strings (`fred.py:116-123` etc.) — verified CONTAINED in Slice85 Task 4 review.
- **httpx request logging (pre-existing, dormant):** httpx logs `HTTP Request: GET <full URL>` at INFO if an operator attaches a handler; connectors build per-instance `httpx.Client`s with no central hook point.

---

## 3. TRUE GAPS ONLY (Slice86 substance; each verified absent)

| # | Gap | Evidence of absence | Maps to |
|---|---|---|---|
| G1 | **Per-provider optional-vs-fatal strict policy** | `ProviderDescriptor` has no flag (`registry.py:42-57`); `_run_full_enrichment` raises uniformly (`runs.py:1297-1326`); Slice85 plan §D-F deferred it here | Acceptance (1) |
| G2 | **Per-provider hit/miss/error/cache/blocked ledger** | Summary = 3 aggregate counts + refs (`runs.py:1328-1333`); MISS/ERROR dropped (:1309-1327); cache hits audit-only (`service.py:164-172`), invisible in summary; no CACHED distinction (`cache_policy.py:137-174`) | Scope bullet 2 |
| G3 | **Enrichment provenance visible in VC package** | `_run_full_deliverables` has no enrichment param (`runs.py:1687-1779`); `product_bundle.py` run_summary (:220-266) + evidence_index (:439-478) have zero enrichment fields; matrix self-documents `..._not_final_output_visible` | Acceptance (2) |
| G4 | **Source-grade mapping** | `EnrichmentProvenance` has no grade field; zero RightsClass(GREEN/YELLOW/RED)→SourceGrade(A/B/C/D) mapping anywhere (grep-verified) | Scope bullet 3 |
| G5 | **Data-level conflict checks** | Only the Slice13 registry-metadata plan exists; zero enrichment-vs-claims/value conflict code | Scope bullet 3 |
| G6 | **URL-key hardening + httpx log redaction** | §2.5 — contained but fragile; no central redaction | Slice85 follow-ups |
| G7 | **Enrichment summary/ledger leak + FULL-integration provenance tests** | No enrichment analogue of the Slice83/84 provenance/leak suites | Test discipline |

Out of scope (already built or other slices): credential storage/bootstrap (Slice85), readiness gating (Slice57/85), extraction/debate/scoring provenance (83/84), durable cache backend, enrichment-fact DB table / `enrichment_fact_ids` materialization (Layer2 forward-reference — needs DB migration → **not this slice**), credential lifecycle APIs.

## 4. Design sketch per gap (additive, mirroring Slice83/84/85)
- **G1 (policy):** add `optional_in_strict: bool = False` to `ProviderDescriptor` + `registry.register(..., optional_in_strict=...)`; `_run_full_enrichment` strict branch: optional provider's ERROR/exception/blocked → recorded in ledger as non-fatal (continue), mandatory → raise (unchanged). Surface per-provider policy in the strict matrix (`strict_behavior` gains e.g. `strict_optional_continue_on_error`) + audit payload gains `optional_in_strict`. Default: **all 15 mandatory** (preserves today's behavior; D-C decides any defaults flipped).
- **G2 (ledger):** additive `enrichment_ledger` in the ENRICHMENT `result_summary`: per-provider rows `{provider_id, status, cached, rights_class, optional_in_strict, ref_id|null}` + aggregate counts `{hit, miss, error, blocked_rights, blocked_missing_byol, cache_hits}`. `cached` needs the service to surface cache-hit (smallest seam: `EnrichmentService.enrich` returns/marks cached — e.g. additive `from_cache` flag on the returned result or a `(result, cached)` accessor; decision D-D). Safe values only (ids/enums/bools/counts); leak-tested.
- **G3 (VC package):** thread the enrichment summary (ledger + refs) through `_run_full_deliverables` → `ProductBundleExporter.export_bundle`: `run_summary` gains enrichment counts; `evidence_index` gains `enrichment_package` (refs with provider_id/source_id/rights_class/source_grade). Then flip the matrix's `provenance_output_status` for registered providers to an output-visible value (one constant site, `byol_credentials.py:440/452/463`) — controlled characterization drift.
- **G4 (source grade):** pure mapping fn `rights_class → SourceGrade` (D-E decides rule; strawman GREEN→B, YELLOW→C, RED→C with BYOL else D — NOT A: A is reserved for primary/audited docs per evidence-grade semantics — confirm); carried in ledger rows + `enrichment_package`; **no change to EnrichmentProvenance model needed** (computed at summary/export time) unless D-E says persist.
- **G5 (conflict checks):** NARROW, well-defined check only: for HIT results, compare normalized fields against deal metadata already in context (e.g. company-name mismatch between request identifiers and provider-returned canonical name) → `conflicts` list in ledger rows (safe strings: field name + provider id, never values?? — D-G decides value-safety shape); recorded, never fatal. Alternative: defer with explicit master-plan-deviation note (D-G).
- **G6 (hardening):** (a) Finnhub documents header auth (`X-Finnhub-Token`) — **claim from provider API knowledge, NOT the codebase; Task 6 must re-verify against Finnhub's public docs before switching** (`finnhub.py:109` currently embeds `token={api_key}` in the URL; `_make_request` already sends headers, :160-165, so the seam exists); FRED/FMP are query-param-only APIs → keep param but add a shared `_redact_url_secrets()` applied at `*FetchError` message construction; (b) central httpx hygiene: attach a redacting `event_hook`/logging filter where connectors build clients (shared helper). All behavior-preserving; connector tests updated for Finnhub header.
- **G7 (tests):** characterization first (pin §2/§3 truths), then RED-first per gap; acceptance suite composes both master-plan bullets with injected fakes (no network; hermetic env discipline from Slice85).

## 5. No-real-call / safety boundary
No real provider HTTP anywhere (httpx.MockTransport / fake connectors / fake registries; hermetic env for real-funnel tests — Slice85 discipline). No DB migration (ledger/provenance live in the open `result_summary` dict + product-bundle artifacts). No private data. No prompt-registry mutation. No Layer 2 changes beyond its existing refs consumption. No Slice87 (calculations).

## 6. Verification gate (CI parity — worktree root, `PYTHONPATH=src`)
`python -c "import idis; print(idis.__file__)"` · `ruff format --check .` · `ruff check .` · clean-cache `mypy src/idis` · `python scripts/forbidden_scan.py --repo-root .` · `git diff --check` · targeted `pytest` (enrichment suites + Slice57/85 + connector suites + strict readiness + Slice70 rehearsal + Slice75a/b parity + deliverables/product-export suites when touched). Contract/OpenAPI = N/A (no gate script).

## 7. Risks
- **Blast radius in `_run_full_enrichment` + deliverables export** — both are FULL-path shared wiring; keep default behavior byte-equivalent except the approved additions (characterization pins it).
- **Leakage via ledger/`enrichment_package`** — values restricted to ids/enums/bools/counts; planted-marker tests over summary JSON, bundle artifacts, exceptions (Slice85 discipline); `normalized` payloads NEVER copied into summaries.
- **Strict-behavior regression** — optional policy must not weaken mandatory fatality; matrix/`strict_behavior` strings are consumed by readiness tests (controlled drift only).
- **Finnhub header-auth switch** — connector contract change; pin with its existing MockTransport suite (request headers asserted), no live call.
- **Conflict-check scope creep** — keep D-G narrow or defer explicitly.
- **Product-bundle manifest sanitization** — new `enrichment_package` must pass the existing manifest sanitizer rules (`manifest_review.py` / slice64 tests).

## 8. Task breakdown (TDD; STOP after each)
- **Task 1 — Characterization (no prod change):** pin §2/§3 truths: summary shape (3 counts + refs; MISS/ERROR dropped), uniform strict fatality (all three raise branches), no optionality flag, cache-hit audit-only, matrix `strict_behavior`/`provenance_output_status` exact strings, deliverables/product-bundle blind to enrichment, refs feed analysis/scoring/L2 only (debate L1 none), no RightsClass→SourceGrade mapping, URL-key containment + per-connector client construction (no hooks). GREEN-on-arrival expected; RED = STOP and report.
- **Task 2 — Optional-provider strict policy (G1, D-B/D-C):** descriptor flag + registry param + `_run_full_enrichment` policy branch + matrix/audit surfacing. RED-first.
- **Task 3 — Ledger + cache visibility (G2, D-D):** service cache-hit surfacing seam + additive `enrichment_ledger` (rows + counts) in the step summary; sanitized; leak tests. RED-first.
- **Task 4 — Source-grade mapping + VC-package visibility (G3+G4, D-E/D-F):** mapping fn; thread enrichment into `_run_full_deliverables` → `ProductBundleExporter` (`run_summary` counts + `evidence_index.enrichment_package`); flip `provenance_output_status` (controlled drift); manifest-sanitizer green. RED-first.
- **Task 5 — Conflict checks (G5, D-G):** narrow data-level check recorded in ledger (or explicit deferral per decision). RED-first.
- **Task 6 — URL-key + httpx hardening (G6, D-H):** Finnhub header auth; shared FetchError redaction; central httpx redaction hook. RED-first; behavior-preserving.
- **Task 7 — Acceptance proof:** compose both master-plan bullets end-to-end with injected fakes (optional vs mandatory fatality differential; enrichment provenance present in exported bundle artifacts), hermetic, leak-marker swept.
- **Task 8 — Docs/config reconciliation + full gate + independent review** (incl. readiness-doc matrix-status note; plan AS-BUILT).
- **Task 9 — Finish branch: open PR only.**

## 9. Decisions — confirm BEFORE Task 1
- **D-A — SCOPE (key).** ALL of G1–G7 (recommended: matches every master-plan bullet) vs trimming G5 (conflict checks deferred with explicit deviation note) and/or G6 (hardening to its own mini-slice). Tasks are independent enough to drop either.
- **D-B — Optional-policy shape.** `optional_in_strict: bool` on `ProviderDescriptor` set at registration (recommended: matches `requires_byol` precedent, no new config surface) vs env/config-driven list vs rights-class-derived.
- **D-C — Default optionality.** All 15 mandatory by default (recommended: preserves current strict semantics; flipping any provider to optional is then an explicit follow-up decision) vs marking low-stakes public feeds (e.g. hackernews, gdelt, google_news_rss, wayback) optional now.
- **D-D — Cache-hit surfacing seam.** Additive `from_cache: bool = False` field on `EnrichmentResult` set by the service on cache hits (recommended: additive, callers unaffected) vs `(result, cached)` tuple vs ledger-only audit-derived counting.
- **D-E — Source-grade rule (key).** Mapping table from `rights_class` (+BYOL?) → `SourceGrade`; strawman: GREEN→B, YELLOW→C, RED+BYOL→C, RED w/o BYOL→D; grade computed at summary/export time, NOT persisted into `EnrichmentProvenance` (no model change). Confirm the rule and whether any provider gets a per-provider override.
- **D-F — VC-package surfaces.** `run_summary` counts + `evidence_index.enrichment_package` (recommended) vs also an IC-memo/screening section (bigger; deliverable-content drift).
- **D-G — Conflict-check scope (key).** NARROW: company-identifier mismatch flags per HIT, recorded as safe strings in the ledger, never fatal (recommended) vs DEFER with explicit deviation note vs broader field-level value comparison (out: needs value exposure).
- **D-H — Hardening shape.** Finnhub→header auth + shared redaction helper + httpx event-hook/logging filter (recommended) vs redaction-only (no Finnhub change).
- **D-I — Debate L1 feed.** Leave Layer-1 debate without enrichment input and document that the debate-layer consumer is Layer 2 IC challenge (already receives refs) — recommended; feeding L1 would touch the debate context/prompt surface (bigger, riskier). Confirm.

## 10. Open questions for you
1. **D-A:** full G1–G7 scope, or trim G5/G6?
2. **D-B/D-C:** descriptor-flag policy + all-mandatory default?
3. **D-E:** confirm/adjust the source-grade mapping rule.
4. **D-G:** narrow conflict check vs explicit deferral.
5. **D-H:** Finnhub header auth + central redaction?
6. **D-I:** Layer-1 debate stays out (Layer 2 is the debate consumer)?
