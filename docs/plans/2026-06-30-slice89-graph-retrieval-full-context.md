# Slice 89 — Graph Retrieval In FULL Context — Implementation Plan

> **For Claude / agentic workers:** REQUIRED SUB-SKILL: `superpowers:executing-plans` (or `subagent-driven-development`) task-by-task. Per task: `test-driven-development` (RED → verify red → minimal GREEN → verify), `verification-before-completion` before any status claim, `using-git-worktrees` already done (`C:\Projects\IDIS\IDIS-slice89`, branch `slice89-graph-retrieval-full-context`, base `origin/main` @ `b7c89c4`), `finishing-a-development-branch` before commit/PR. **Reuse before create. STOP for approval after each task.** Confirm the §9 decisions (especially **DEC-1 conclusions shape/safety**, **DEC-5 scope breadth**) BEFORE Task 2. **STATUS (as-built, acceptance-first): foundation + VC-package conclusions complete (T1–T5); analysis/debate/scoring/Layer 2 consumer feeds deferred (DEC-5 option B) — see §11 Closeout.** (Original intent: discovery + planning; now executed.)

**Goal (master plan):** Use graph retrieval in analysis, debate, scoring, Layer 2, and deliverables.

**Scope (master plan):** Chain, weakest-link, independence-cluster, contradiction/co-occurrence, defect-impact queries. Persist/cite graph retrieval outputs.

**Acceptance (master plan):** VC package contains graph-derived conclusions and provenance.

**Architecture:** The Neo4j projection (Slice88) and the read-side Cypher queries + `GraphRetrievalService` already exist, and FULL already **invokes** retrieval and **strict-blocks** on failure. The gap is that retrieval currently surfaces only **counts** — the actual graph-derived **conclusions** never reach analysis/debate/scoring/Layer 2/deliverables. Slice89 surfaces a deterministic, tenant-safe **graph-derived conclusions** structure (derived only from safe fields: grades, statuses, counts, ids — no raw spans/text), wires the unused defect-impact query, and feeds those conclusions — each carrying claim/calc **provenance** — into the consumers and the VC package, satisfying No-Free-Facts.

**Tech Stack:** Python 3.11, Pydantic v2, FastAPI, pytest, ruff, mypy, Neo4j (read via `execute_read`), Postgres.

---

## 1. Baseline (this worktree, verified)

- Worktree `C:\Projects\IDIS\IDIS-slice89`, branch `slice89-graph-retrieval-full-context`, base `origin/main` @ `b7c89c4aed0594fc4ae89afd42b9d7464604913f`. Imports resolve to this worktree (`idis.__file__` under `IDIS-slice89\src`).
- Baseline gates green: `ruff format --check` 778 clean · `ruff check` clean · clean-cache `mypy src/idis` **Success, 397** · smoke **273 passed / 5 skipped** across graph/RAG/analysis/debate/scoring/Layer2/deliverables.
- Zero Slice89 artifacts (no branch/worktree/plan/test before this doc).

## 2. Already built (hand-verified — reuse, do not recreate)

- **Read-side Cypher (all 6):** `src/idis/persistence/cypher/q_4_4_1_full_chain.py` … `q_4_4_6_entity_cooccurrence.py` — chain, deal-claims-grades, independence-clusters, weakest-link, defect-impact, entity-co-occurrence. Each filters `tenant_id` as a first-match constraint.
- **GraphRepository read methods:** `src/idis/persistence/graph_repo.py` — `get_claim_sanad_chain` (357), `get_deal_claims_with_grades` (375), `get_independence_clusters` (393), `get_weakest_link` (411), **`get_defect_impact` (429)**, `get_entity_cooccurrence` (447). All via `execute_read`.
- **Driver-level tenant guard:** `src/idis/persistence/neo4j_driver.py:execute_read` raises `ValueError` if `tenant_id` absent (274–302).
- **`GraphRetrievalService`:** `src/idis/services/graph/retrieval.py` — `retrieve_deal_graph_summary` (56–125) runs 5 queries and returns a **counts-only** safe summary.
- **FULL invokes retrieval + strict-gates it:** `src/idis/api/routes/runs.py` — `_retrieve_graph_evidence` (2502–2519) calls `retrieve_deal_graph_summary`; the graph step returns `{graph_status, graph_projection, graph_retrieval}` and raises `RunStepBlockedError("GRAPH_RETRIEVAL_BLOCKED")` in strict mode when status != `retrieved` (2135–2155).
- **Partial downstream use of the summary:** `src/idis/services/runs/layer2_ic_challenge.py:_graph_ref_ids` (442–459) extracts `claim_id`s from `query_summaries`; `src/idis/deliverables/product_bundle.py` surfaces `graph_retrieval_count` in the manifest. Both consume **counts/ref-ids only**, never conclusions.
- **Deliverables provenance + No-Free-Facts:** `src/idis/models/deliverables.py` (`DeliverableFact.claim_refs`/`calc_refs`, `AuditAppendix`); `src/idis/validators/deliverable.py:81-115` rejects any `is_factual=True` fact lacking refs (`NO_FREE_FACTS_UNREFERENCED_FACT`). The additive **calc-derived-facts** pattern in `src/idis/deliverables/generator.py` (~503-543) is the model to copy.
- **Retrieval tests:** `tests/test_slice61_graph_visibility.py` (`test_graph_retrieval_service_returns_safe_tenant_scoped_summary` ~340; product-bundle graph visibility ~548); `FakeGraphRepository` test fake (60–91).

## 3. True gaps (hand-verified, with evidence)

- **G1 — Retrieval surfaces COUNTS, not conclusions.** `retrieve_deal_graph_summary` computes each query's records then keeps only `len(...)` (retrieval.py:64–124). The graph-derived content (weakest-link grade, corroboration status, chain depth, co-occurring entities, defect impact) never leaves the service.
- **G2 — Defect-impact query is unwired into retrieval.** `get_defect_impact` exists (graph_repo.py:429–445) but `retrieve_deal_graph_summary` never calls it, and `GraphRepositoryProtocol` (retrieval.py:10–46) omits it. Master scope explicitly lists "defect-impact queries."
- **G3 — Analysis consumes no graph conclusions.** Seam: `src/idis/analysis/agents/llm_specialist_agent.py:_build_context_payload` payload dict (95–107), serialized at 108.
- **G4 — Debate consumes no graph conclusions.** Seam: `src/idis/api/routes/runs.py` `DebateContext(conflicts=[])` empty stub (1194), the debate-claim dicts (~1131-1141), and `src/idis/debate/roles/llm_role_runner.py:_serialize_context` conflicts section (~285-290).
- **G5 — Scoring consumes no graph conclusions.** Seam: `src/idis/analysis/scoring/llm_scorecard_runner.py:_build_context_payload` payload dict (~94-109).
- **G6 — Layer 2 consumes ref-ids only, not conclusions.** `layer2_ic_challenge.py:_graph_ref_ids` (442–459) extracts claim-ids; `src/idis/services/runs/methodology_layer2_readiness_package.py` consumes no graph signal.
- **G7 — VC package has no graph-derived conclusions.** `product_bundle.export_bundle(graph_evidence=...)` exists but only counts reach the manifest; the generators add no graph-derived facts. Master **acceptance** requires graph-derived conclusions + provenance in the VC package.
- **G8 — Stale readiness doc + no Slice89 tests.** `docs/architecture/strict_full_live_readiness.md` still says "Graph retrieval into analysis/debate is a later slice." and "Wire Neo4j graph retrieval into FULL (graph projection is already wired)." No characterization/acceptance tests for Slice89.

## 4. Design / approach

1. **Graph-derived conclusions structure (the foundation).** Add a deterministic, tenant-safe per-deal conclusions object derived from the existing query records — e.g. per-claim `{claim_id, chain_depth, weakest_grade, corroboration_status, independent_source_count}`, deal-level `co_occurring_entities` (name/type/doc_count), and `defect_impacts` (`{defect_type, severity, affected_claim_ids, affected_calc_ids}`). **Only safe fields** (grades, statuses, counts, ids, entity names already surfaced in deliverables) — never raw spans/claim_text. Deterministic: sorted, no `datetime.now`/`uuid`/`random`.
2. **Surface without breaking Slice61.** Preserve the existing counts-only `query_summaries`; ADD the conclusions under a new key (e.g. `graph_conclusions`) on the retrieval summary, or via a sibling method. Wire `get_defect_impact` here (G2).
3. **Provenance binding.** Every graph-derived deliverable fact cites the claim(s)/calc(s) it is derived from via existing `claim_refs`/`calc_refs` — satisfying No-Free-Facts with **no new `RefType`** and no new audit-appendix entries.
4. **Additive consumer feeds.** Inject the conclusions into each consumer's existing payload/seam additively (mirroring the Slice87 calc-feed and Slice88 sanad/defect feeds); preserve determinism and existing behavior when graph is absent.
5. **Strict behavior unchanged.** Conclusions ride the existing retrieval seam (`retrieved` → `available`); `GRAPH_RETRIEVAL_BLOCKED` stays the strict gate. No new strict gates.

## 5. Safety / strict boundaries

- No raw private content leaves the graph layer — conclusions use only safe derived fields. Preserve the Slice61 "no raw records" posture (its safety test must stay green).
- Tenant scoping unchanged (driver-level `execute_read` guard + per-query `tenant_id`).
- Fail-closed unchanged: strict FULL blocks at `GRAPH_RETRIEVAL_BLOCKED`; non-strict consumers degrade to empty conclusions.
- No Neo4j schema change (read-only slice; the locked 12/11 schema is untouched). No real Neo4j run in tests — inject `FakeGraphRepository`.

## 6. Verification gate (every task)

Import proof · `pytest` (task tests + relevant regression) · `ruff format --check .` · `ruff check .` · clean-cache `mypy src/idis` · `scripts/forbidden_scan.py --repo-root .` · `git diff --check`. Closeout: full `pytest` + independent review.

## 7. Risks

- **Determinism drift** in conclusions breaking the analysis/scoring deterministic payloads or No-Free-Facts exact-dict pins → sort everything, derive purely from records.
- **Over-broad scope** (5 consumers) → see DEC-5; land the acceptance core first.
- **No-Free-Facts rejection** if a graph fact lacks refs → every conclusion must cite a claim/calc.
- **Slice61 safety regression** if conclusions leak raw text → restrict to safe fields; keep the safety test green.

## 8. Tasks (bite-sized, TDD)

> Acceptance-critical: **T1–T3, T8–T9**. Additive consumer feeds: **T4–T7** (subject to DEC-5).

### Task 1 — Characterization (pin current truth)
- **Test:** `tests/test_slice89_graph_retrieval_characterization.py` (new).
- Pin: retrieval summary has `query_summaries` with `record_count` and **no** `graph_conclusions` key; `get_defect_impact` not referenced in `retrieval.py`; analysis/scoring payloads + `DebateContext.conflicts` carry no graph data; readiness doc has the "later slice" wording; FULL strict still raises `GRAPH_RETRIEVAL_BLOCKED`. GREEN-on-arrival. Any RED → STOP + report.

### Task 2 — Graph-derived conclusions surface + defect-impact wiring (foundation; G1, G2)
- **Files:** Modify `src/idis/services/graph/retrieval.py` (+ `GraphRepositoryProtocol` to add `get_defect_impact`); Test `tests/test_slice89_graph_conclusions.py` (new).
- **RED:** seed `FakeGraphRepository` with chain/weakest/independence/cooccurrence/defect records; assert `retrieve_deal_graph_summary` (or a new `retrieve_deal_graph_conclusions`) returns deterministic `graph_conclusions` (per-claim grades/status/counts + co-occurrence + defect-impacts) **and** preserves the counts `query_summaries`; assert defect-impact present. Verify RED.
- **GREEN:** derive conclusions from records (safe fields only); call `get_defect_impact` for seeded defects. Preserve Slice61 safety test.

### Task 3 — VC package graph-derived facts (ACCEPTANCE; G7)
- **Files:** Modify `src/idis/deliverables/generator.py` (+ thread `graph_evidence`/conclusions from `product_bundle.export_bundle`); Test `tests/test_slice89_deliverables_graph_conclusions.py` (new).
- **RED:** with seeded graph conclusions, assert a deliverable (e.g. IC Memo risks/financials) contains a graph-derived fact whose `claim_refs` (and/or `calc_refs`) are non-empty and that it passes `validate_deliverable_no_free_facts`. Verify RED.
- **GREEN:** add graph-derived facts via existing builder methods, each citing the conclusion's claim/calc ids. No new `RefType`.

### Task 4 — Analysis feed (additive; G3)
- **Files:** Modify `src/idis/analysis/agents/llm_specialist_agent.py:_build_context_payload`; Test `tests/test_slice89_analysis_graph_feed.py` (new).
- RED→GREEN: add a `graph_summary` block to the payload before `json.dumps` (108); empty/deterministic when absent.

### Task 5 — Debate feed (additive; G4)
- **Files:** Modify `src/idis/api/routes/runs.py` (populate `conflicts` from contradiction/co-occurrence + independence; enrich debate-claim dicts) and `src/idis/debate/roles/llm_role_runner.py:_serialize_context`; Test `tests/test_slice89_debate_graph_feed.py` (new).
- RED→GREEN: `DebateContext.conflicts` non-empty from graph conclusions; serialized for the role prompt; empty when absent.

### Task 6 — Scoring feed (additive; G5)
- **Files:** Modify `src/idis/analysis/scoring/llm_scorecard_runner.py:_build_context_payload`; Test `tests/test_slice89_scoring_graph_feed.py` (new).
- RED→GREEN: add `graph_summary` to the scoring payload; deterministic.

### Task 7 — Layer 2 feed (additive; G6)
- **Files:** Modify `src/idis/services/runs/methodology_layer2_readiness_package.py` (and/or `layer2_ic_challenge.py` to consume conclusions, not just ref-ids); Test `tests/test_slice89_layer2_graph_feed.py` (new).
- RED→GREEN: graph-derived conclusion influences a readiness reason/blocker with provenance; deterministic.

### Task 8 — Readiness doc reconciliation + characterization flip (G8)
- **Files:** Modify `docs/architecture/strict_full_live_readiness.md`; flip the Task 1 pin.
- Update the "later slice" / "Wire Neo4j graph retrieval into FULL" wording to the wired reality (retrieval conclusions feed consumers + VC package; strict blocks safely).

### Task 9 — Acceptance proof
- **Test:** `tests/test_slice89_acceptance.py` (new).
- Prove: a FULL strict run with healthy graph + seeded conclusions yields a VC package containing graph-derived conclusions with provenance (claim/calc refs, No-Free-Facts satisfied); and blocks safely (`GRAPH_RETRIEVAL_BLOCKED`) when retrieval fails. Injected fakes; no real Neo4j.

### Task 10 — Closeout
- Reconcile this plan to as-built; full local gate (incl. full `pytest`); independent review of the cumulative diff; then closeout PR (only when approved).

## 9. Decisions to confirm before Task 2

- **DEC-1 — Conclusions shape & safety.** Surface a deterministic `graph_conclusions` structure derived from safe fields only (grades/statuses/counts/ids/entity-names), preserving the Slice61 counts summary and the "no raw records" safety posture. (Recommended.)
- **DEC-2 — Pure (non-LLM) derivation.** Conclusions derived deterministically from query records — no LLM, no `datetime`/`uuid`/`random` — to fit deterministic payloads + No-Free-Facts.
- **DEC-3 — Provenance via existing refs.** Graph-derived deliverable facts cite existing `claim_refs`/`calc_refs`; no new `RefType`, no new audit-appendix entry kind.
- **DEC-4 — Strict unchanged.** Keep `GRAPH_RETRIEVAL_BLOCKED`; conclusions ride the existing `retrieved`→`available` seam; no new strict gates; non-strict consumers degrade to empty.
- **DEC-5 — Scope breadth.** The master scope names 5 consumers, but the **acceptance** is the VC package only. Options: **(A)** full breadth this slice (T1–T9); **(B, recommended)** acceptance-first — foundation + deliverables (T1–T3, T8–T9) now, analysis/debate/scoring/Layer 2 feeds (T4–T7) as additive follow-on. Choose A or B.

## 10. Open questions for you
1. DEC-1: confirm the safe-fields-only conclusions shape (any field to include/exclude)?
2. DEC-5: full breadth (A) or acceptance-first (B)?
3. Any objection to surfacing conclusions on the existing retrieval summary (new `graph_conclusions` key) vs a separate method?
4. Confirm no Neo4j schema change and no real-Neo4j test boundary (inject `FakeGraphRepository`).
5. Persist graph retrieval outputs (master scope "persist/cite"): is citing in the VC package + run summary sufficient, or is a durable Postgres record also required this slice?

## 11. Closeout — As-Built reconciliation (Tasks 1–5)

**Status: acceptance-first core complete.** Decisions locked at execution: **DEC-1** safe-fields-only `graph_conclusions` (ids/grades/statuses/counts; co-occurrence as a count only — entity names + source-system excluded to preserve the Slice61 no-leak posture); **DEC-2** pure/deterministic derivation; **DEC-3** existing `claim_refs`/`calc_refs` provenance, no new `RefType`; **DEC-4** strict gate unchanged; **DEC-5 option B** acceptance-first — foundation + VC package now, consumer feeds deferred. **Q5** run-summary + VC-package visibility is sufficient; no new durable Postgres table.

| Gap | Status | As-built |
| --- | --- | --- |
| G1 — retrieval counts-only | **Closed (T2)** | `retrieve_deal_graph_summary` adds `graph_conclusions` (per-claim lineage + defect-impact + co-occurrence count) alongside the preserved counts `query_summaries`; safe fields only. |
| G2 — defect-impact unwired | **Closed (T2)** | `get_defect_impact` declared on the Protocol and called when `defect_ids` are supplied. |
| G7 — VC package no graph facts | **Closed (T3)** | `generate()`/`_build_ic_memo` render conclusions as IC-memo facts with existing claim/calc provenance (No-Free-Facts); no new `RefType`. |
| (T4) defect ids not in FULL retrieval | **Closed (T4)** | `_project_graph_evidence` surfaces `projected_defect_ids`; `_run_full_graph_evidence` threads them into `_retrieve_graph_evidence` → defect-impact conclusions flow end-to-end. |
| G8 — stale readiness doc | **Closed (T6)** | `strict_full_live_readiness.md` reconciled: retrieval wired into FULL + feeds the VC package; consumer feeds a later follow-on. |
| G6 — no Slice89 tests | **Closed** | characterization + conclusions + deliverables + full-defect-retrieval + acceptance suites. |

**Acceptance proven** — `tests/test_slice89_acceptance.py`: the VC package contains graph-derived conclusions (per-claim lineage + defect-impact) with provenance; strict FULL blocks safely (`GRAPH_RETRIEVAL_BLOCKED`); no raw/private values leak even with an adversarial graph repo.

**Deferred follow-ons (honest):** the master scope names five consumers; per **DEC-5 option B**, the **analysis (G3), debate (G4), scoring (G5), and Layer 2 (G6-readiness)** feeds are NOT wired this slice — only the deliverables/VC-package consumer is. Those payload-assembly seams (`llm_specialist_agent._build_context_payload`, `runs._run_full_debate` `conflicts=[]`, `llm_scorecard_runner._build_context_payload`, `methodology_layer2_readiness_package`) remain for a follow-on. Also noted: `layer2_ic_challenge._graph_ref_ids` has a dead `query_summaries` fallback (Task 1 finding) to address when Layer 2 is wired.

**Production footprint:** `services/graph/retrieval.py`, `deliverables/generator.py`, `api/routes/runs.py`. Test-fixture: `tests/test_slice61_graph_visibility.py` (`FakeGraphRepository` made Protocol-conformant).
