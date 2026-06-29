# Slice87 — Calculation Path Unification And Persistence — Implementation Plan

> **STATUS (as-built): COMPLETE.** Tasks 1–7 implemented and merged to `main` individually (PRs #100–#104); a Task 8 independent review then found a metadata-unification gap in G1 (metadata-bearing calc types persisted a parallel duplicate), now **fixed** — `CalcRunner` shares the methodology `metadata_for_calc(...)` source so both paths hash identically (see §11). All six true gaps (G1–G6) are closed (IRR deferred). Both master-plan acceptance bullets are proven. See **§11 Closeout** for the as-built reconciliation. This document is retained as the planning + as-built record.

> **For Claude / agentic workers:** REQUIRED SUB-SKILL: `superpowers:executing-plans` (or `subagent-driven-development`) task-by-task. Per task: `test-driven-development` (RED → verify red → minimal GREEN → verify), `verification-before-completion` before any status claim, `using-git-worktrees` already done, `finishing-a-development-branch` before commit/PR. **Reuse before create. STOP for approval after each task.** Confirm the §9 decisions (especially **D-B unification direction**, **D-C formula scope**, **D-D financial-table shape**, **D-E graph/RAG feeding**) BEFORE Task 1. (Original intent: discovery + planning; now executed — see §11.)

**Goal:** Unify FULL's two calculation executions onto one authoritative, durably persisted CalcEngine/CalcSanad path; complete the formula registry to the degree decided; render **financial tables for deliverables** from persisted calc outputs; and feed calc outputs into the remaining acceptance consumers (graph, RAG) — so financial claims reliably produce calc IDs + CalcSanads and calc outputs feed analysis, debate, graph, RAG, and the VC package.

**Architecture (headline discovery):** There are **no stub calculators** — one production `CalcEngine` core already exists (extraction-gate enforced, Decimal-only, reproducibility-hashed, CalcSanad-deriving) with durable Postgres persistence (migration 0005) and rich downstream consumption (analysis `calc_registry` + NFF, scoring `supported_calc_ids`, debate `calc_refs`, VC bundle `calculation_package` with hashes/versions/grades). The real "parallel paths" are **two FULL-run executions of the same engine**: the `METHODOLOGY_DETERMINISTIC_CALCULATION` step (order ~10; methodology-materialized claims; deterministic UUID5 ids; **run-scoped in-memory records that are never durably persisted**) and the `CALC` step (order ~19; EXTRACT-step claims; random UUIDs; durable persistence; the ids every downstream consumer sees). Slice87's substance is unifying those two (D-B), implementing the missing formulas (D-C), building the missing financial-table rendering (D-D), feeding graph/RAG (D-E), and proving acceptance. **No new DB migration expected** (0005 tables suffice — Task 1 pins this). No real FULL run; no Slice88.

**Tech Stack:** Python 3.11+, Pydantic v2, FastAPI, pytest, ruff (CI-parity), mypy, Decimal-only arithmetic in formulas. Postgres via existing repos (RLS, JSONB).

**Base:** branch `slice87-calculation-path-unification-persistence` @ `31d91d27b07f8e71823351f76e063cd7a7d18628` (= `origin/main`, Slice86 merged via PR #99), worktree `C:/Projects/IDIS/IDIS-slice87`. Baseline green: import proof pinned to this worktree's `src` · `ruff format --check .` 766 ok · `ruff check .` ok · clean-cache `mypy src/idis` 396 files ok · smoke (calc runner/sanad/reproducibility/repository/loop-guardrail + slice59 bundle + strict readiness) = **69 passed**.

---

## 1. Master Plan text (verbatim, `docs/IDIS_FULL_LIVE_MASTER_PLAN_V2.md:279-291`)
> #### Slice 87: Calculation Path Unification And Persistence
>
> **Goal:** Replace stubs/parallel calc paths with one production CalcEngine/CalcSanad path.
>
> **Scope:**
> - CalcEngine execution in FULL.
> - Persist deterministic calculations and CalcSanads.
> - Reproducibility hashes and formula versions.
> - Financial tables for deliverables.
>
> **Acceptance:**
> - Financial claims produce calc IDs and CalcSanads.
> - Calc outputs feed analysis, debate, graph, RAG, and VC package.

---

## 2. ALREADY BUILT — reuse, do not rebuild (verified; exact refs at 31d91d2)

### 2.1 The single CalcEngine core (no stubs anywhere)
- **`CalcEngine`** (`src/idis/calc/engine.py:117-243`): fail-closed extraction gate on every input (`:299-341` — blocks `extraction_confidence < 0.95` / `dhabt_score < 0.90` unless human-verified, `ExtractionGateBlockedError`); Decimal-only formula execution; `calc_id` + `DeterministicCalculation` + `CalcSanad` outputs; `verify_reproducibility()` exists (`:245`) but is **never invoked** anywhere.
- **Reproducibility + versions:** `_compute_reproducibility_hash` (`:342-376`, SHA256 over canonical JSON of tenant/deal/calc_type/**formula_hash**/**code_version**/inputs/output); `FormulaSpec.formula_hash` (`calc/formulas/registry.py:40-53`, SHA256 of {calc_type, **formula_version**, expression_id}). Hash stability pinned by `tests/test_calc_reproducibility.py:43-109`.
- **CalcSanad derivation** (`engine.py:378-462`, model `models/calc_sanad.py:86-129`): `input_min_sanad_grade` = min over ALL inputs; `calc_grade` = min over MATERIAL inputs; `GradeExplanationEntry` audit list.
- **Formulas registered: 4 of 10 CalcTypes** (`calc/formulas/core.py:117-172` — RUNWAY, GROSS_MARGIN, BURN_RATE, LTV_CAC_RATIO). `CalcType` enum (`models/deterministic_calculation.py:15-29`) also names IRR, MOIC, NRR, CAC_PAYBACK, VALUATION_MULTIPLE, LTV — **no formula implementations** (the gap is absence, not stubs).
- **Loop guardrail** (`tests/test_calc_loop_guardrail.py:43-92`): derived claims (`source_calc_id` set) never re-trigger calculations.

### 2.2 Persistence — durable tables already exist
- **Migration 0005** (`persistence/migrations/versions/0005_deterministic_calculations_and_calc_sanads.py:25-123`): `deterministic_calculations` (calc_id PK, tenant/deal FKs, calc_type, inputs JSONB, formula_hash, code_version, output JSONB, reproducibility_hash) + `calc_sanads` (UNIQUE calc_id FK) with RLS. **No new migration expected.**
- **`PostgresCalculationsRepository.create`** (`persistence/repositories/calculations.py:18-75`) persists both records transactionally; in-memory fallback + `get_calculations_repository` factory (`:115-171`).

### 2.3 The two FULL-run executions (the real "parallel paths" — hand-verified)
- **`CALC` step** (FULL_STEPS order ~19, `models/run_step.py:107-137`; SNAPSHOT too): `calc_fn=partial(_run_snapshot_calc, db_conn=db_conn)` is **unconditional** (`services/runs/steps.py:152`); `_run_snapshot_calc` (`api/routes/runs.py:2620-2682`) → `CalcRunner` (`services/calc/runner.py:59-141` — claims→candidates with grade/Sanad metadata→engine→**durable persist**) → summary `{calc_ids, reproducibility_hashes, persisted_count, blocked_candidates}`; orchestrator accumulates `calc_ids` and feeds them to enrichment/graph/rag/debate/analysis/scoring/layer2 (`orchestrator.py:1884-2042`).
- **`METHODOLOGY_DETERMINISTIC_CALCULATION` step** (FULL_STEPS order ~10): `InMemoryRunMethodologyDeterministicCalculationService` (`services/runs/methodology_deterministic_calculation.py:52-398`) wraps the SAME engine over methodology-materialized claims, derives **deterministic UUID5 calc ids** (`models/calc_materialization.py:342-366`), dedups by (calc_type, input_claim_ids, methodology_question_id, extraction_task_id), stores **run-scoped in-memory records** (`ctx.methodology_calculations`/`methodology_calc_sanads`) consumed by the Truth Dashboard — **never written to Postgres** and never the source of the accumulated `calc_ids`.
- Divergence axes: claim source (methodology materialization vs EXTRACT), id derivation (deterministic vs random), persistence (none vs durable), dedup (yes vs no).

### 2.4 Downstream consumers — analysis/debate/scoring/VC already wired; graph/RAG not
- **Analysis:** `AnalysisContext.calc_ids` + `calc_registry: dict[calc_id → AnalysisCalcReference]` (incl. output, assumptions, formula_hash, code_version, reproducibility_hash, grades) built by `_build_analysis_calc_registry` (`api/routes/runs.py:1642-1681`); NFF validates calc refs (`analysis/no_free_facts.py:95-110`).
- **Scoring:** `DimensionScore.supported_calc_ids` validated (`analysis/scoring/models.py:64-89`, `engine.py`). **Debate:** advocate carries/propagates `calc_refs` into muhasabah (`debate/roles/advocate.py:145,197-202`).
- **VC package:** `_calc_package` (`deliverables/product_bundle.py:427-452`) emits per-calc `{calc_id, calc_sanad_id, calc_type, input_claim_ids, assumptions, output, formula_hash, code_version, reproducibility_hash, calc_grade, input_min_sanad_grade}`; `run_summary` carries calc counts/ids/hashes; `evidence_index.calc_entries` links calc→sanad→claims. (Two explorer claims to the contrary were **refuted by hand** — hashes/versions ARE bundle-visible.)
- **Graph:** no calc projection (graph retrieval queries claims only). **RAG:** no calc indexing/retrieval. (Acceptance-2 names both.)
- **Strict census:** `deterministic_calculations` + `calc_sanad` components (`services/runs/strict_full_live.py:735-783`) are `CODE_EXISTS_BUT_NOT_WIRED`/blocking **unless `product_export_ready`** (Postgres + object store configured) — environment-conditioned, not a code gap.

### 2.5 Deliverables financial surfaces — builders exist, nothing fills them
`MemoBuilder.add_financials_fact` (`deliverables/memo.py:177-193`) + a rendered "financials" section (`:435-438`) and `add_scenario_fact` (`:272`) exist, but **no code maps calc outputs into them**; no financial-table model/builder exists anywhere.

### 2.6 Tests to reuse
`test_calc_runner/calc_sanad/calc_reproducibility/calculations_repository/calc_loop_guardrail/calc_value_types_integration/run_calc_materialization_models/run_route_calc_truthfulness` + slice59/64 bundle calc assertions + Slice70 rehearsal (CALC step) + slice75a/b parity. Baseline all green (69 in smoke).

## 3. TRUE GAPS ONLY (each verified absent; explorer over-claims corrected)

| # | Gap | Evidence | Maps to |
|---|---|---|---|
| G1 | **FULL's two calc executions are un-unified**: methodology records are in-memory only (never persisted; never the accumulated `calc_ids`), can diverge from the CALC step's durable set; deterministic-vs-random id schemes | §2.3 | Goal + "Persist…" bullet |
| G2 | **6 of 10 CalcTypes have no formulas** (IRR, MOIC, NRR, CAC_PAYBACK, VALUATION_MULTIPLE, LTV) | `core.py:154-171` vs enum | "CalcEngine execution in FULL" breadth |
| G3 | **Financial tables for deliverables** — no model/builder/renderer from calc outputs; memo financial/scenario builders never fed | §2.5 | Scope bullet 4 |
| G4 | **Graph + RAG do not consume calc outputs** (no projection, no indexing/retrieval) | §2.4 | Acceptance 2 |
| G5 | `verify_reproducibility()` never invoked; per-calc `formula_version` string not explicitly surfaced (formula_hash/code_version are) | `engine.py:245`; `_calc_package` | "Reproducibility hashes and formula versions" polish |
| G6 | No Slice87-style characterization/acceptance/leak suites for the calc path | test inventory | Test discipline |

Out of scope (exists or other slices): engine/gate/sanad logic, persistence tables/repos, analysis/debate/scoring/VC consumption, strict census components (env-conditioned), durable-cache/graph-RAG infrastructure builds (Phase F slices own the infra; G4 here is *feeding only*).

## 4. Design sketch per gap
- **G1 (unification, D-B):** make the methodology step's records **durable and authoritative-compatible**: persist them via the existing repo (deterministic UUID5 ids are persistence-safe and idempotent for retry/resume), and reconcile the CALC step — preferred shape: CALC step **skips** calc types/input-sets already produced by the methodology step (dedup against persisted run-scoped records) so FULL yields ONE coherent persisted set feeding `calc_ids`. Alternative shapes in D-B.
- **G2 (formulas, D-C):** implement the decided subset as `FormulaSpec`s with required inputs from claim predicates/aliases (e.g. MOIC = distributions/invested; VALUATION_MULTIPLE = valuation/revenue; NRR/CAC_PAYBACK/LTV from their standard inputs). IRR needs a cash-flow series — likely defer (D-C).
- **G3 (financial tables, D-D):** pure builder `build_financial_tables(calc_records) -> list[FinancialTable]` (typed rows: calc_type, value, unit/currency, period, grade, calc_id) feeding `MemoBuilder.add_financials_fact` + an additive `financial_tables` block in the bundle (sanitized, mirroring Slice86's package style).
- **G4 (graph/RAG feeding, D-E):** minimal additive feeding inside the EXISTING FULL steps: graph step projects calc nodes/edges (calc→input claims) where the projection service allows; RAG step indexes calc output summaries as spans. If the Phase-F infra makes this premature, explicit deviation note instead (D-E).
- **G5:** invoke `verify_reproducibility` in the acceptance suite (and optionally at persist-read time — D-F); add `formula_version` to `AnalysisCalcReference`/`_calc_package` entries (one additive field).
- **G6:** characterization first (pin §2/§3 truths incl. the dual-path divergence), RED-first per gap, acceptance composing both bullets, leak/marker discipline from Slices 83–86.

## 5. No-real-call / safety boundary
No real FULL run; no network; no DB migration (0005 suffices — Task 1 pins it; if any decided shape needs schema change → STOP and report); in-memory + fake-conn repos in tests (real Postgres only in CI integration jobs); no private data; Decimal-only in new formulas; no Slice88 (graph infra) / Slice90+ (RAG infra) builds.

## 6. Verification gate (CI parity — worktree root, `PYTHONPATH=src`)
`python -c "import idis; print(idis.__file__)"` · `ruff format --check .` · `ruff check .` · clean-cache `mypy src/idis` · `python scripts/forbidden_scan.py --repo-root .` · `git diff --check` · targeted `pytest` (calc suites + run-orchestrator suites + slice59/64 bundle + Slice70 rehearsal + slice75a/b + strict readiness + any touched methodology suites). Contract/OpenAPI = N/A (no gate script).

## 7. Risks
- **Blast radius of unification** — the methodology step feeds Truth Dashboard/validated-evidence chains; the CALC step feeds every downstream consumer. Characterization must pin both contracts before any change; retry/resume idempotency (orchestrator rehydration of methodology calcs) must keep working.
- **Id-scheme change fallout** — if CALC adopts deterministic ids (or skips), tests pinning UUID randomness/counts may drift (controlled).
- **Formula correctness** — new formulas need golden-value tests + Decimal-quantization consistency with existing specs.
- **Bundle/manifest sanitizers** — new `financial_tables` block must pass slice64 manifest rules (Slice86 precedent).
- **Graph/RAG feeding scope creep** — keep additive within existing steps or defer explicitly.

## 8. Task breakdown (TDD; STOP after each)
- **Task 1 — Characterization (no prod change):** pin the dual-path truth (methodology records in-memory + deterministic ids + dedup; CALC durable + random ids; accumulated `calc_ids` sourced only from CALC), persistence schemas (no migration needed), formula registry contents (4 of 10), `_calc_package`/run_summary calc fields, memo financial builders unfed, graph/RAG blindness, `verify_reproducibility` un-invoked, strict census condition. GREEN-on-arrival expected; RED = STOP and report.
- **Task 2 — Unification + FULL persistence (G1, D-B):** persist methodology calc records; reconcile/dedup the CALC step per D-B; accumulated `calc_ids` reflect the unified set; retry/resume idempotent. RED-first.
- **Task 3 — Formula completion (G2, D-C):** decided subset with golden-value + gate + hash-stability tests.
- **Task 4 — Financial tables (G3, D-D):** builder + memo financials/scenario feeding + additive sanitized bundle block; slice59/64 regression green.
- **Task 5 — Graph/RAG calc feeding (G4, D-E)** — or explicit deviation note per decision.
- **Task 6 — Repro/version polish (G5, D-F):** `formula_version` surfacing + `verify_reproducibility` invocation point.
- **Task 7 — Acceptance proof:** both bullets end-to-end (financial claims → calc ids + CalcSanads persisted; outputs visible in analysis/debate/graph/RAG/VC surfaces per decided scope), hermetic, leak-swept.
- **Task 8 — Docs/config reconciliation + full gate + independent review.**
- **Task 9 — Finish branch: open PR only.**

## 9. Decisions — confirm BEFORE Task 1
- **D-A — SCOPE (key).** Full G1–G6 vs trimming G4 (graph/RAG feeding deferred to Phase F with deviation note) and/or G2 partial (formula subset). Tasks are independent enough to trim.
- **D-B — Unification direction (key).** (i) **Methodology-authoritative (recommended):** persist methodology records (deterministic ids), CALC step dedups/skips what methodology already produced, unified `calc_ids` accumulate both; (ii) CALC-authoritative: methodology step consumes/links to CALC persistence; (iii) merge both into one new step (largest blast radius — not recommended).
- **D-C — Formula scope (key).** Recommend: MOIC, VALUATION_MULTIPLE, NRR, CAC_PAYBACK, LTV (single-period, claim-derivable); **defer IRR** (needs cash-flow series modeling). Confirm subset.
- **D-D — Financial-table shape (key).** Typed `FinancialTable` rows from persisted calcs → memo `financials` facts + additive `financial_tables` bundle block (recommended) vs memo-only vs bundle-only.
- **D-E — Graph/RAG feeding.** Minimal additive feeding inside existing FULL steps (recommended if Phase-F infra suffices today — Task 1 verifies projection/indexing seams) vs explicit deviation note.
- **D-F — verify_reproducibility invocation.** Acceptance-suite-only (recommended) vs also at bundle-export read time (runtime cost, fail-closed semantics to define).
- **D-G — Id scheme.** Keep deterministic UUID5 for methodology-produced calcs and random UUID for CALC-only extras (recommended; both already persistence-compatible) vs unify all on deterministic ids.

## 10. Open questions for you
1. **D-A:** full scope or trim G4/G2?
2. **D-B:** methodology-authoritative unification (recommended)?
3. **D-C:** confirm the 5-formula subset, IRR deferred?
4. **D-D:** financial tables in both memo and bundle?
5. **D-E:** graph/RAG feeding now vs deviation note?
6. **D-F/D-G:** confirm verification point + id-scheme stance.

## 11. Closeout — As-Built reconciliation (Tasks 1–7 merged)

**Status: complete.** Tasks 1–7 merged to `main` individually. **Decisions locked at execution:** D-A full G1–G6 (graph/RAG minimal + additive) · D-B methodology-authoritative unification · D-C MOIC/VALUATION_MULTIPLE/NRR/CAC_PAYBACK/LTV (IRR deferred) · D-D typed `FinancialTable` → memo financials + sanitized VC bundle block · D-E minimal graph/RAG feed via existing FULL-step seams · D-F `formula_version` surfaced additively + `verify_reproducibility` exercised in acceptance · D-G persistence-compatible id scheme (deterministic UUID5 methodology ids reused; random ids for CALC-only extras).

| Gap | Status | As-built | PR |
| --- | --- | --- | --- |
| G1 — un-unified dual calc executions | **Closed** | `_run_full_methodology_deterministic_calculation` wrapper persists the (still-pure) methodology service's records via the existing durable repo, idempotent by UUID5 id; `CalcRunner` dedups/merges against persisted records by `reproducibility_hash` (also makes the CALC step resume-idempotent). Task 8 fix: `CalcRunner` now stamps the same output metadata via the shared `metadata_for_calc(...)`, so metadata-bearing types (`GROSS_MARGIN`, `LTV_CAC_RATIO`, currency/time-window calcs) hash identically across paths and dedup — not just metadata-free types. | [#100](https://github.com/albarami/IDIS/pull/100) |
| G2 — 6 of 10 CalcTypes had no formulas | **Closed (5 of 6; IRR deferred)** | Added MOIC, VALUATION_MULTIPLE, NRR, CAC_PAYBACK, LTV → registry 9/10. IRR deferred: no cash-flow-series input model exists and one was not invented (engine `fn` takes a flat `dict[str, Decimal]`). | [#100](https://github.com/albarami/IDIS/pull/100) |
| G3 — financial tables for deliverables | **Closed** | Typed `FinancialTable`/`FinancialTableRow` + `build_financial_table`; additive memo financials feed (No-Free-Facts via the calc's input claims; LLM `financial_agent` bridge preserved) + sanitized `financial_table` block in the VC `financial_diligence` artifact, beside the preserved `calculation_package`. | [#101](https://github.com/albarami/IDIS/pull/101) |
| G4 — graph/RAG don't consume calc | **Closed (additive, no new infra)** | Graph projection feeds persisted calcs through the existing `project_claim_sanad(calculations=…)` seam → `projected_calculation_count`. RAG step surfaces a sanitized `rag_calc_evidence` block (evidence reflection; pgvector calc-embedding deferred as new infra). | [#102](https://github.com/albarami/IDIS/pull/102) |
| G5 — `verify_reproducibility` uninvoked + `formula_version` not surfaced | **Closed** | `formula_version` surfaced via registry lookup with a `formula_hash` guard on `AnalysisCalcReference` + `_calc_package` (no model field, no migration); `verify_reproducibility` exercised through the acceptance proof. | [#103](https://github.com/albarami/IDIS/pull/103) |
| G6 — no Slice87 calc-path test suites | **Closed** | characterization + unification + formulas + financial-tables + graph/RAG + reproducibility/version + acceptance suites. | #100–#104 |

**Acceptance (both master-plan bullets) proven** — `tests/test_slice87_acceptance.py` ([#104](https://github.com/albarami/IDIS/pull/104)): financial claims produce calc IDs + CalcSanads; calc outputs feed analysis, debate, graph, RAG, and the VC package.

**Boundaries honored:** no new DB migration (0005 sufficed); no real FULL run; Decimal-only formulas; no Slice88 / Phase-F infra builds. **Deferred (noted, not gaps):** IRR (needs a cash-flow-series input model); pgvector calc-embedding for RAG (new infra); `rag_calc_evidence` surfacing in the bundle `_rag_package` (kept minimal).

### Task 8 review finding — G1 metadata-unification gap (found, fixed)

The Task 8 independent review (and an empirical repro) found that the CALC-step dedup keys on `reproducibility_hash`, which by design includes the output `metadata` (unit/currency/time_window). The methodology path stamped non-empty metadata via `metadata_for_calc(...)` (e.g. `{"unit":"percent"}` for `GROSS_MARGIN`) while `CalcRunner` passed none — so for **metadata-bearing** calc types the two paths hashed differently and the CALC step persisted a **parallel duplicate** rather than deduping. Metadata-free types (`RUNWAY`, `BURN_RATE`, …) were unaffected; the Task 2 unification test used `RUNWAY`, which masked it.

**Fix (Task 8):** `CalcRunner._build_candidate` now stamps output metadata via the **same** `metadata_for_calc(calc_type, claims)` source as the methodology path (`common_attr` generalized to read currency/time_window from repository dict claims as well as run-scoped claim objects), and passes it into `CalcEngine.run`. Output metadata (e.g. `unit=percent`) is preserved and the metadata stays in the reproducibility hash. A cross-path, metadata-bearing dedup test (`test_calc_step_dedups_metadata_bearing_methodology_calc`) was added RED-first; RUNWAY/metadata-free dedup is preserved. With this, the "one unified calc path" goal holds for all calc types.
