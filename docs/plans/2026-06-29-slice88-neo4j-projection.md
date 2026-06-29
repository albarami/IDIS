# Slice88 — Neo4j Projection — Implementation Plan

> **For Claude / agentic workers:** REQUIRED SUB-SKILL: `superpowers:executing-plans` (or `subagent-driven-development`) task-by-task. Per task: `test-driven-development` (RED → verify red → minimal GREEN → verify), `verification-before-completion` before any status claim, `using-git-worktrees` already done (`C:\Projects\IDIS\IDIS-slice88`, branch `slice88-neo4j-projection`, base `origin/main` @ `5b8a03f`), `finishing-a-development-branch` before commit/PR. **Reuse before create. STOP for approval after each task.** Confirm the §9 decisions (especially **D-A scope / the deliverables schema conflict** and **D-B saga wiring**) BEFORE Task 1. **STATUS (as-built): in-scope gaps (G1, G2, G5, G6) complete; G3 (deliverables) and G4 (saga) deferred by locked decisions — see §11 Closeout.** (Original intent: discovery + planning; now executed.)

**Goal:** Make a FULL strict run durably project the full entity set the master plan names — claims, evidence, Sanads, defects, calculations, and deliverables — into Neo4j (or block safely), reusing the already-built `GraphProjectionService` / `GraphRepository` / driver / saga / tenant-isolation infrastructure and feeding it the entities it is currently starved of.

**Architecture (headline discovery):** Almost all the Neo4j machinery already exists and is tested — one `GraphProjectionService` (`project_deal`, `project_claim_sanad`), a `GraphRepository` of idempotent-MERGE Cypher, a fail-closed `neo4j_driver` (health = HEALTHY/MISSING_CREDENTIALS/FAILED; partial-config refuses to connect), driver-level tenant isolation (every node carries `tenant_id`; `execute_read/write` raise without it), a complete `DualWriteSagaExecutor` with reverse-order compensation, a 6-query §4.4 Cypher contract, and a **locked 12-node / 11-edge graph schema** (§4.1/§4.2, pinned by a contract test). The FULL graph step (`_run_full_graph_evidence`) has been wired since Slice61, runs at FULL step 21 **after** the durable EXTRACT/GRADE/CALC writes, and is strict fail-closed (`GRAPH_HEALTH_BLOCKED` / `GRAPH_PROJECTION_BLOCKED` / `GRAPH_RETRIEVAL_BLOCKED`). Slice87 added the calc feed. **The substance of Slice88 is feeding the step the entities it currently drops** (Sanad transmission chain, defects), deciding the saga/atomicity stance, reconciling docs — and resolving a real **scope conflict: the master plan names "deliverables," but the canonical graph schema has no Deliverable node and is contract-locked.** No real Neo4j run; no Slice89 (retrieval).

**Tech Stack:** Python 3.11, Pydantic v2, Neo4j (driver + Cypher MERGE), Postgres (durable source), pytest, ruff, mypy. Tests use injected fake graph repos / health checkers (no real Neo4j); real Neo4j only in CI integration jobs if configured.

---

## 1. Master Plan text (verbatim, `docs/IDIS_FULL_LIVE_MASTER_PLAN_V2.md:295-305`)

> #### Slice 88: Neo4j Projection
>
> **Goal:** Project claims, evidence, Sanads, defects, calculations, and deliverables into Neo4j.
>
> **Scope:**
> - GraphProjectionService after durable Postgres writes.
> - Tenant isolation and consistency saga.
> - Fail-closed graph writes in strict mode when configured.
>
> **Acceptance:**
> - FULL strict run writes graph projections or blocks safely.

## 2. ALREADY BUILT — reuse, do not rebuild (verified at 5b8a03f)

### 2.1 GraphProjectionService + GraphRepository
- `src/idis/persistence/graph_consistency.py:128` — `GraphProjectionService` with `project_deal(tenant_id, deal_id, documents, spans, entities=None)` (:152) and `project_claim_sanad(tenant_id, claim, evidence_items, transmission_nodes, defects=None, calculations=None)` (:242). Fail-closed: SKIPPED when Neo4j not configured, FAILED/AUDIT_FAILURE when configured and projection/audit fails (audit emit is fatal, :101).
- `src/idis/persistence/graph_repo.py` — `upsert_deal_graph_projection` (:47; Deal/Document/Span/Entity + HAS_DOCUMENT/HAS_SPAN/MENTIONED_IN) and `upsert_claim_sanad_projection` (:156; Claim/EvidenceItem/TransmissionNode/Defect/Calculation + SUPPORTED_BY/HAS_SANAD_STEP/INPUT/OUTPUT/HAS_DEFECT/DERIVED_FROM). All idempotent `MERGE`.
- `ProjectionResult`/`ProjectionStatus` (graph_consistency.py:33,51): SUCCESS/SKIPPED/FAILED/AUDIT_FAILURE; result always carries `tenant_id`.

### 2.2 Driver, health, fail-closed, tenant isolation
- `src/idis/persistence/neo4j_driver.py` — `check_neo4j_health` (:175) → HEALTHY/MISSING_CREDENTIALS/FAILED; `_validate_config` (:143) **fails closed on partial config**; `is_neo4j_configured` (:131); `execute_read/execute_write` **raise without `tenant_id`** (driver-level tenant isolation; :274,:325). `NodeLabel` (12, :94) and `EdgeType` (11, :111) — **locked schema** (no Tenant node, no Deliverable node).

### 2.3 Consistency saga (built, tested — NOT wired into the FULL step)
- `src/idis/persistence/saga.py` — `DualWriteSagaExecutor` (:195) with `add_postgres_step`/`add_graph_step` + reverse-order compensation; `SagaResult`/`SagaStatus`; `DualWriteConsistencyError`. Helper `create_claim_projection_saga` (graph_consistency.py:338).

### 2.4 FULL graph step (wired since Slice61; calc feed from Slice87)
- `src/idis/api/routes/runs.py` — `_run_full_graph_evidence` (:1933): health-gate → `_project_graph_evidence` (:2249) → retrieval. `steps.py` binds `graph_fn=partial(_run_full_graph_evidence, db_conn=db_conn) if is_full else None`; orchestrator `_execute_graph_evidence` passes `created_claim_ids` + `calc_ids`. `GRAPH_EVIDENCE` = FULL step **21**, after EXTRACT(18)/GRADE(19)/CALC(20) durable writes. **Projects today:** documents, spans, claims, evidence (from Postgres `EvidenceRepository`), calculations (Slice87). Strict raises `GRAPH_HEALTH_BLOCKED`/`GRAPH_PROJECTION_BLOCKED`/`GRAPH_RETRIEVAL_BLOCKED`.

### 2.5 Cypher §4.4 contract + tests to reuse
- `src/idis/persistence/cypher/q_4_4_1..6_*.py` — 6 tenant-scoped, deterministic-ordered query builders.
- Tests: `test_graph_projection_tenant_isolation.py`, `test_graph_postgres_consistency_saga.py`, `test_graph_repo_cypher_contract.py`, `test_neo4j_driver_fail_closed.py`, `test_slice61_graph_visibility.py`, `test_slice87_graph_rag_calc_feeding.py`. Data model: `docs/02_IDIS_Data_Model_Schema_v6_3.md` §4.1/§4.2/§4.4 (canonical graph schema).

## 3. TRUE GAPS ONLY (each verified; explorer claims hand-checked)

| # | Gap | Evidence | Master-plan tie |
| --- | --- | --- | --- |
| G1 | **Sanads not projected in FULL** — `_project_graph_evidence` passes `transmission_nodes=[]` (runs.py:2325); the Sanad/transmission chain is never loaded from Postgres and fed, so no `TransmissionNode`/`HAS_SANAD_STEP`/`INPUT`/`OUTPUT` is written in a FULL run. | runs.py:2321-2326 | Goal "Sanads" |
| G2 | **Defects not projected in FULL** — `project_claim_sanad` is called without `defects=` (defaults None), so no `Defect`/`HAS_DEFECT` is written in a FULL run. | runs.py:2321-2326 | Goal "defects" |
| G3 | **Deliverables — SCOPE CONFLICT.** No `Deliverable` node label exists; the canonical schema is **locked at 12 nodes / 11 edges** and pinned by a contract test (`test_neo4j_driver_fail_closed.py` `len(NodeLabel)==12`, "no invented labels"). Projecting deliverables requires extending §4.1 (new node + edge), the `NodeLabel`/`EdgeType` enums, the contract test, and the data-model doc — beyond "wire existing seams." | neo4j_driver.py:94; test_neo4j_driver_fail_closed.py:160 | Goal "deliverables" |
| G4 | **Consistency saga not wired into the FULL projection** — `_project_graph_evidence` calls `project_deal`/`project_claim_sanad` directly (0 saga refs); the built `DualWriteSagaExecutor`/`create_claim_projection_saga` are unused in the FULL path. (Note: the FULL step is a read-then-project from already-durable Postgres, so atomicity semantics differ from synchronous dual-write — see D-B.) | runs.py (no saga import) | Scope "consistency saga" |
| G5 | **Strict readiness census stale** — `strict_full_live_readiness.md:47` says graph is `code-exists-but-not-wired` / "FULL does not call GraphProjectionService", but FULL **does** call it (Slice61). Needs reconciliation to "wired + fail-closed; gated on `NEO4J_*` env". | strict_full_live_readiness.md:47 | Scope "fail-closed … when configured" |
| G6 | **No Slice88 characterization/acceptance suite** — the acceptance ("FULL strict run writes graph projections or blocks safely") is unproven for the full entity set (sanads/defects), and there is no characterization pinning current projection coverage. | test inventory | Acceptance + Test discipline |

Out of scope (exists or other slices): the driver/health/fail-closed, tenant isolation, the saga executor itself, the Cypher §4.4 contract, graph **retrieval** in FULL context (Slice89), RAG (Slice90/91), real Neo4j runtime provisioning (env/infra). G4 here is *wiring/decision only*, not a new saga build.

## 4. Design sketch per gap
- **G1 (Sanads, the core projection gap):** in `_project_graph_evidence`, load the persisted Sanad transmission chain for each claim (mirror `_graph_evidence_by_claim`/`_graph_calculations_by_claim`: a `_graph_transmission_nodes_by_claim(tenant_id, deal_id, created_claim_ids, db_conn)` reading the durable Sanad/transmission store) and pass it as `transmission_nodes=…` instead of `[]`. Report a `projected_sanad_step_count` mirroring `projected_calculation_count`. Only project **real persisted** transmission nodes (no invention), consistent with the calc-feed contract.
- **G2 (Defects):** similarly load persisted defects per claim (`_graph_defects_by_claim`) and pass `defects=…`. Report `projected_defect_count`. Only real persisted defects.
- **G3 (Deliverables — DECISION FIRST, D-A):** **do not implement before approval.** Options: (a) extend the canonical graph schema — add `Deliverable` node + a `DELIVERABLE_OF`/`HAS_DELIVERABLE` edge to §4.1/§4.2, the `NodeLabel`/`EdgeType` enums, the contract test (→ 13/12), a `project_deliverable` method, and a FULL-step feed (deliverables are produced at FULL step 28, *after* graph at step 21 — so a deliverables projection would need a later projection pass or a step reorder); (b) defer "deliverables" with an explicit deviation note (canonical schema is the source of truth and omits it); (c) represent deliverables via existing nodes. **This is the key open decision (§9 D-A) — likely STOP-and-report before widening.**
- **G4 (Saga wiring, D-B):** decide whether the per-claim FULL projection should run inside `create_claim_projection_saga` / `DualWriteSagaExecutor` (compensation on partial graph failure) or whether read-then-project-from-durable-Postgres + the existing fail-closed `GRAPH_PROJECTION_BLOCKED` is the correct atomicity model (recommended default, since Postgres is already durable and the graph write is one-way/idempotent MERGE). If the saga is wanted, wire it minimally around the existing project calls.
- **G5 (Docs):** reconcile `strict_full_live_readiness.md:47` to reflect the wired + fail-closed reality (and the new sanad/defect coverage once G1/G2 land).
- **G6 (Tests):** characterization first (pin current coverage: docs/spans/claims/evidence/calcs projected, sanads/defects/deliverables not), then RED-first per gap, then an acceptance test composing "FULL strict run projects the full set OR blocks safely" using injected fake graph repo + health checker (no real Neo4j).

## 5. No-real-call / safety boundary
No real Neo4j connection or run; injected fake graph repos / health checkers / `RecordingProjectionService` in tests (real Neo4j only in CI integration jobs if configured); no DB migration unless a decided shape needs one (G3 schema extension would — STOP and report); no private data; **no Slice89 (graph retrieval) / Slice90+ (RAG) work.** Honor the locked 12/11 schema unless D-A explicitly approves extending it.

## 6. Verification gate (CI parity — worktree root, `PYTHONPATH=src`)
Import proof · `pytest` (targeted Slice88 suites + graph/neo4j/saga/tenant/cypher/strict regression; full suite before the closeout PR) · `ruff format --check .` · `ruff check .` · clean-cache `mypy src/idis` · `scripts/forbidden_scan.py --repo-root .` · `git diff --check`.

## 7. Risks
- **Schema-lock collision (G3):** any new node/edge breaks `test_neo4j_driver_fail_closed.py` (12/11) — must be a deliberate, approved schema extension, not a silent add.
- **Ordering (G3 deliverables):** deliverables are produced at FULL step 28, after graph at step 21 — projecting them needs a second projection pass or step reorder (design risk).
- **Loading sanad/defect data:** the persisted transmission/defect stores and their dict shape must be confirmed (mirror the evidence/calc loaders) before feeding `project_claim_sanad`.
- **Saga semantics (G4):** wrong atomicity model could either over-engineer (saga where not needed) or under-protect (partial graph writes).

## 8. Task breakdown (TDD; STOP after each)
- **Task 1 — Characterization (no prod change):** pin current FULL projection coverage (docs/spans/claims/evidence/calcs projected; `transmission_nodes=[]` → sanads not projected; defects not passed; no Deliverable node; saga not wired; strict block codes present; readiness row stale). GREEN-on-arrival; RED = STOP + report.
- **Task 2 — Sanad projection in FULL (G1):** load + feed the transmission chain; `projected_sanad_step_count`. RED-first.
- **Task 3 — Defect projection in FULL (G2):** load + feed defects; `projected_defect_count`. RED-first.
- **Task 4 — Saga wiring decision (G4, D-B):** implement the decided atomicity model (or an explicit deviation note).
- **Task 5 — Deliverables (G3, D-A):** ONLY if D-A approves the schema extension — schema + enums + contract test + `project_deliverable` + FULL feed/ordering; else a documented deviation. **Likely STOP-and-report first.**
- **Task 6 — Strict acceptance proof:** "FULL strict run writes the decided projection set OR blocks safely" (fake graph repo + health checker; fail-closed verified).
- **Task 7 — Docs/readiness reconciliation + full gate + independent review.**
- **Task 8 — Finish branch: closeout PR only.**

## 9. Decisions — confirm BEFORE Task 1
- **D-A — Deliverables scope (key).** The master plan names "deliverables," but the canonical §4.1 graph schema has no Deliverable node and is contract-locked (12/11), and deliverables are produced *after* the graph step. Choose: (a) extend the schema (new node+edge, enums, contract test, data-model doc, `project_deliverable`, and a projection pass after deliverables) — a real widening; (b) **defer deliverables with an explicit deviation note** (recommended unless you want the schema extended now); (c) other. This likely warrants STOP-and-report before any deliverables code.
- **D-B — Saga / atomicity model (G4).** Wrap the FULL per-claim projection in `create_claim_projection_saga`/`DualWriteSagaExecutor` (compensation), **or** keep read-then-project-from-durable-Postgres + the existing fail-closed `GRAPH_PROJECTION_BLOCKED` (recommended: Postgres already durable, graph write is idempotent MERGE)?
- **D-C — Sanad representation.** Confirm "project Sanads" = feeding the existing `TransmissionNode`/`HAS_SANAD_STEP` chain (canonical, no separate Sanad node), not adding a dedicated `Sanad` node (which would also hit the schema lock).
- **D-D — Full G1–G6 vs trim.** Full scope, or land G1/G2/G5/G6 (the in-schema, no-conflict gaps) now and defer G3 (deliverables) + decide G4?

## 10. Open questions for you
1. **D-A:** extend the schema for deliverables now, or defer with a deviation note?
2. **D-B:** saga-wrap the FULL projection, or rely on durable-Postgres + fail-closed?
3. **D-C:** Sanad = TransmissionNode chain (confirm, no dedicated node)?
4. **D-D:** full G1–G6, or trim G3 out of this slice?
5. Any objection to the no-real-Neo4j test boundary (injected fakes; real Neo4j only in CI integration)?

## 11. Closeout — As-Built reconciliation (Tasks 1–5)

**Status: in-scope work complete.** Decisions locked at execution: **D-A** defer deliverables (no schema extension); **D-B** do not wire the saga (rely on durable Postgres + the existing fail-closed projection); **D-C** Sanads = the existing `TransmissionNode`/`HAS_SANAD_STEP` chain (no dedicated node); **D-D** in-schema gaps only (G1, G2, G5, G6).

| Gap | Status | As-built |
| --- | --- | --- |
| G1 — Sanads not projected | **Closed** | `_graph_transmission_nodes_by_claim` loads each claim's persisted `transmission_chain` and feeds it into `project_claim_sanad(transmission_nodes=…)`; reports `projected_sanad_step_count`. Existing schema only. |
| G2 — defects not projected | **Closed** | `_graph_defects_by_claim` loads persisted defects (`DefectsRepository.list_by_claim`) and feeds `project_claim_sanad(defects=…)`; reports `projected_defect_count`. Uses the existing `Defect` node. |
| G3 — deliverables | **Deferred (D-A)** | No `Deliverable` node in the locked 12-node schema; not extended this slice. Acceptance proves deliverables are never projected (no seam, no node). |
| G4 — saga not wired | **Deferred (D-B)** | The FULL projection reads already-durable Postgres and writes idempotent MERGE; kept fail-closed (`GRAPH_PROJECTION_BLOCKED`) rather than wrapping the dual-write saga. |
| G5 — stale readiness doc | **Closed** | `strict_full_live_readiness.md` graph row reconciled: FULL calls `GraphProjectionService`; Sanad-chain + defect feeds noted; deliverables deferred; strict blocks safely on `NEO4J_*` absence. |
| G6 — no Slice88 tests | **Closed** | Characterization + sanad-feed + defect-feed + acceptance suites. |

**Acceptance proven** — `tests/test_slice88_acceptance.py`: a FULL strict run writes the in-schema set (claims, evidence, Sanad chain, defects, calculations) when Neo4j is healthy, and blocks safely (`GRAPH_HEALTH_BLOCKED` / `GRAPH_PROJECTION_BLOCKED`) when missing/unhealthy/failed; deliverables are never projected.

**Boundaries honored:** existing 12-node/11-edge schema unchanged; no real Neo4j run (injected fakes); no saga wiring; no DB migration. **Deferred (decisions, not gaps):** deliverables projection (G3 — needs a schema extension; a follow-up slice) and saga wiring (G4). Graph **retrieval** into analysis/debate is Slice89.

**Production footprint:** `src/idis/api/routes/runs.py` only (the two loaders + the feed). Docs: this plan + the readiness reconciliation.
