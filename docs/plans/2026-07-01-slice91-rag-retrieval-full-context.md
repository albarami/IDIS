# Slice 91 — RAG Retrieval In FULL Context — Implementation Plan

> **For Claude / agentic workers:** REQUIRED SUB-SKILL: `superpowers:executing-plans` (or `subagent-driven-development`) task-by-task. Per task: `test-driven-development` (RED → verify red → minimal GREEN → verify), `verification-before-completion` before any status claim, `using-git-worktrees` already done (`C:\Projects\IDIS\IDIS-slice91`, branch `slice91-rag-retrieval-full-context`, base `origin/main` @ `22a6ff2`), `finishing-a-development-branch` before commit/PR. **Reuse before create. STOP for approval after each task.** The §9 decisions were LOCKED before implementation. **As-built (2026-07-02): Tasks 1–8 are complete; Task 9 is this closeout — see §0.**

**Goal (master plan):** Feed retrieved evidence chunks into extraction/debate/analysis/scoring and exports.

**Scope (master plan):** Retriever API/service; prompt context integration; source IDs, scores, and retrieval provenance.

**Acceptance (master plan):** Final package lists retrieved evidence with IDs/scores; RAG runtime proof is separate from pgvector connectivity.

**Architecture:** Slice90 landed the indexing side and a **probe-mode** retrieval that returns safe `{source_type, source_id, score}` matches, strict-gated and already surfaced in the VC package. **Acceptance bullet 1 (final package lists retrieved evidence with IDs/scores) is therefore already met.** The remaining substance is (a) feeding those retrieved matches into the debate/analysis/scoring prompt contexts (the "feed into …" scope; mirrors the Slice89 graph-conclusion consumer feeds), and (b) formalizing a **RAG runtime proof** (retrieval actually ran and produced matches) as a signal distinct from pgvector connectivity (acceptance bullet 2). Extraction runs before RAG, so it is an ordering decision, not a simple wiring.

**Tech Stack:** Python 3.11, Pydantic v2, Postgres + pgvector, OpenAI embeddings, pytest, ruff, mypy.

---

## 0. As-built outcome (Task 9 closeout, 2026-07-02)

Both master-plan acceptance bullets are met and pinned end-to-end in
`tests/test_slice91_acceptance.py`: (1) the final package lists retrieved evidence with
IDs/scores (`evidence_index.rag_evidence.retrieval.matches` + `run_summary` counts — already
built pre-slice, now acceptance-pinned); (2) `rag_runtime_proof` (`retrieval_ran` /
`retrieval_proved` / `match_count`, derived solely from the retrieval outcome) is separate from
pgvector connectivity in both directions. What landed per task:

- **T1** `test_slice91_rag_retrieval_characterization.py` — 9 pins of the pre-slice truth
  (GREEN-on-arrival); pins flipped only as tasks genuinely changed them.
- **T2** `rag_runtime_proof` in `product_bundle.py` (`_rag_runtime_proof`, wired into
  `_rag_package`/`_empty_rag_package`/run_summary). Summary signal only — no new strict gate.
- **T3** Analysis feed: `AnalysisRagMatch`/`AnalysisRagEvidence` (+ whitelist
  `from_retrieval_summary` with a skip-malformed/non-finite **score guard**) in
  `analysis/models.py`; `AnalysisContext.rag_evidence`; deterministic sorted payload section in
  `llm_specialist_agent`; `_execute_analysis` threads `accumulated["rag_retrieval"]` (null-safe);
  `_run_full_analysis` attaches it to the context.
- **T4** Scoring feed: same section in `llm_scorecard_runner._build_context_payload`, read from
  the already-threaded `_analysis_context` — no new orchestrator threading.
- **T5** Debate feed: `DebateContext.rag_evidence` (plain-dict section, default `{}`),
  RAG markdown block in `_serialize_context`, `_execute_debate` threading,
  `_run_full_debate` wiring. Shared shape extracted to
  `AnalysisRagEvidence.to_payload_section()` (analysis/scoring refactored to it — zero behavior
  change, pinned by parity tests).
- **T6** Extraction deferral pinned (`test_slice91_extraction_rag_deferral.py`): EXTRACT precedes
  RAG_EVIDENCE; `_execute_extract` passes exactly run scope + documents; prompt builder and
  production extraction fn carry no RAG. No production change.
- **T7** Acceptance proof (`test_slice91_acceptance.py`): one orchestrated FULL run feeds the
  same safe matches to analysis/scoring/debate + deliverables; package-level proofs for both
  acceptance bullets.
- **T8** Readiness doc reconciled: post-Slice91 update banner + RAG row consumption sentence
  (frozen Slice-53 census preserved per convention); pinned by
  `test_readiness_doc_reconciled_slice91_rag_consumption`.

Contract drifts (flipped consciously): 10 explicit orchestrator-test stubs (5 analysis, 5 debate)
gained optional `rag_retrieval=None`; three T1 pins flipped (analysis/scoring/debate consumption)
plus the T2 runtime-proof pin. Strict `RAG_*_BLOCKED` gates unchanged (pinned); no query-driven
retriever, no text chunks, no DB migration, no real OpenAI anywhere in tests.

---

## 1. Baseline (this worktree, verified)

- Worktree `C:\Projects\IDIS\IDIS-slice91`, branch `slice91-rag-retrieval-full-context`, base `origin/main` @ `22a6ff248dc002441dc1cd0188bbbcaa152521b8`. Imports resolve to this worktree.
- Baseline gates green: `ruff check`/`format --check` clean (789) · clean-cache `mypy src/idis` **Success, 397** · RAG/consumer smoke **78 passed**.
- Zero Slice91 artifacts before this doc; the dirty baseline checkout (`C:/Projects/IDIS/IDIS`) is untouched.

## 2. Already built (hand-verified — reuse, do not recreate)

- **Probe retrieval** (`services/rag/retrieval.py:13-82`): `retrieve_rag_probe_evidence(deal_id, probe_embeddings, repository, limit)` runs indexed embeddings through `similarity_search`; returns `{status ∈ skipped|failed|probed, retrieval_mode:"probe", probe_count, match_count, matches:[{source_type, source_id, score}]}`. Deliberately not semantic RAG (no query text / span text / vectors exposed).
- **`similarity_search`** (`persistence/repositories/vector_embeddings.py`): returns `{source_type, source_id, score}` only; tenant+deal scoped (RLS). The `vector_embeddings` table has **no text column** — a match→text chunk requires a JOIN back to the source (span `text_excerpt`, calc output, or graph_conclusions in the run context).
- **FULL RAG step** (`api/routes/runs.py:_run_full_rag_evidence`): indexes `document_span`/`calc_output`/`graph_summary` (Slice90) then probe-retrieves; strict fail-closed (`RAG_CONFIG_BLOCKED`/`RAG_HEALTH_BLOCKED`/`RAG_DATABASE_BLOCKED`/`RAG_INDEXING_BLOCKED`/`RAG_PROBE_RETRIEVAL_BLOCKED`); returns `rag_retrieval` with the matches.
- **Export already lists matches** (`deliverables/product_bundle.py:_rag_package`/`_safe_rag_retrieval` 618, 772-804): `evidence_index.rag_evidence.retrieval.matches` = `[{source_type, source_id, score}]`, plus `run_summary` `rag_retrieval_status`/`rag_match_count`/`rag_probe_count`. **Acceptance bullet 1 is met** (pinned by `test_product_bundle_includes_safe_rag_visibility`). RAG stays an evidence-index listing, not deliverable facts (No-Free-Facts — matches lack claim/calc backing).
- **Threading**: `_execute_layer2_ic_challenge` (orchestrator.py:2009) and `_execute_deliverables` (2115) already thread `accumulated["rag_retrieval"]`. `_execute_debate`/`_execute_analysis`/`_execute_scoring` do **not**.
- **Runtime signal exists**: `rag_retrieval.status` is `probed` (ran + ≥1 match) vs `failed` (ran, 0 matches) vs `skipped` (no probes) — a per-run runtime signal distinct from `pgvector_health` (extension reachable).

## 3. True gaps (hand-verified, with evidence)

- **G1 — retriever is probe-only.** No query-driven retriever exists (no function that embeds an external query → `similarity_search` → ranked evidence). The FULL run retrieves the deal's own indexed evidence via probe vectors. Scope names "Retriever API/service" — see DEC-A.
- **G2 — debate/analysis/scoring do not consume RAG evidence in their prompt contexts.** Seams (all currently RAG-free): `debate/roles/llm_role_runner.py:_serialize_context` + `DebateContext` (add a `rag_evidence` field); `analysis/agents/llm_specialist_agent.py:_build_context_payload` (payload dict + `AnalysisContext.rag_evidence`); `analysis/scoring/llm_scorecard_runner.py:_build_context_payload` (via `AnalysisContext`). Orchestrator threading to these three is absent.
- **G3 — extraction runs before RAG.** Order is `EXTRACT → … → RAG_EVIDENCE`, so `rag_retrieval` is not in `accumulated` at extraction time. Feeding RAG into extraction needs reordering, a separate pre-indexed retrieval, or descope — see DEC-B.
- **G4 — no distinct "RAG runtime proof separate from pgvector connectivity".** The `rag_retrieval.status` signal exists, but there is no formalized proof/summary field asserting "retrieval ran and produced matches" as a first-class artifact separable from `pgvector_health`. Acceptance bullet 2.
- **G5 — matches carry no text.** "Feed retrieved evidence chunks" vs the safe ids/scores-only posture — see DEC-C.
- **G6 — stale readiness doc mentions of retrieval consumption + no Slice91 tests.**

## 4. Design / approach

1. **Reuse the probe matches as the "retrieved evidence."** The safe `{source_type, source_id, score}` matches (already produced + exported) are the retrieval evidence fed to consumers — deterministic, safe, and consistent with the export. A full query-driven retriever is likely out of scope (no external query in a FULL run) — DEC-A.
2. **Additive consumer feeds** (mirror Slice89 graph-conclusion feeds): thread `rag_retrieval.matches` from `accumulated` into debate/analysis/scoring via their existing payload/context seams + `AnalysisContext`; deterministic and empty when RAG is absent.
3. **Runtime proof:** surface a small `rag_runtime_proof` (e.g. `{retrieval_ran: bool, match_count: int}` derived from `rag_retrieval.status == "probed"`) in the run summary / readiness, explicitly separate from `pgvector_health`. No new strict gate unless DEC-D says so.
4. **Safety:** feed only safe fields (source_type/source_id/score) — no text/vectors/queries; the export sanitizers stay authoritative. If DEC-C wants text chunks, recover from the run context (span `text_excerpt` etc.) with the same no-private-leak posture and a claim/calc-ref check before any deliverable fact.
5. **Strict unchanged:** the existing `RAG_*_BLOCKED` gates stay; consumer feeds are additive and non-fatal.

## 5. Safety / strict boundaries

- Feed safe fields only (ids/scores) — the Slice63/90 no-text/no-vector retrieval invariant must stay green.
- Determinism in the analysis/scoring payloads (sorted; no datetime/uuid/random).
- Strict fail-closed unchanged (`RAG_*_BLOCKED`); consumers degrade to empty when RAG absent/skipped.
- No DB schema/migration change (retrieval is read-only). No real OpenAI in tests — inject fakes.

## 6. Verification gate (every task)

Import proof · `pytest` (task tests + relevant regression) · `ruff format --check .` · `ruff check .` · clean-cache `mypy src/idis` · `scripts/forbidden_scan.py --repo-root .` · `git diff --check`. Closeout: full `python -m pytest -q` + independent review.

## 7. Risks

- **Determinism drift** in analysis/scoring payloads → sort match lists.
- **Extraction ordering** (DEC-B) — do not silently wire extraction to an absent `rag_retrieval`.
- **Over-scope** — a query-driven retriever/text-recovery may exceed the acceptance; keep to the safe-matches feed unless DEC-A/DEC-C expand it.
- **No-Free-Facts** — RAG evidence stays an evidence-index listing (no claim/calc backing); do not emit RAG as deliverable facts.

## 8. Tasks (bite-sized, TDD)

> **Status: Tasks 1–8 ✅ complete as specified (as-built details in §0); Task 9 = this closeout.**
> Acceptance-critical: **T1, T2, T7–T8**. Consumer feeds: **T3–T5**. Extraction: **T6** (subject to DEC-B).

### Task 1 — Characterization (pin current truth)
`tests/test_slice91_rag_retrieval_characterization.py`: retrieval is probe-only (no query-driven retriever); debate/analysis/scoring payloads carry no `rag_evidence`; extraction runs before RAG; the export already lists matches with ids/scores; no `rag_runtime_proof` field yet; strict `RAG_*_BLOCKED` intact. GREEN-on-arrival. Any RED → STOP + report.

### Task 2 — RAG runtime proof separate from pgvector connectivity (G4)
Surface a deterministic `rag_runtime_proof` (retrieval ran + match_count, from `rag_retrieval.status`) in the run summary / readiness, explicitly distinct from `pgvector_health`. RED→GREEN. Prove the acceptance-2 separation.

### Task 3 — Analysis feed (G2)
`AnalysisContext.rag_evidence` + `llm_specialist_agent._build_context_payload` inject safe matches; orchestrator `_execute_analysis` threads `accumulated["rag_retrieval"]`. RED→GREEN; deterministic; empty when absent.

### Task 4 — Scoring feed (G2)
`llm_scorecard_runner._build_context_payload` surfaces the matches (via `AnalysisContext.rag_evidence`). RED→GREEN.

### Task 5 — Debate feed (G2)
`DebateContext.rag_evidence` + `_serialize_context` section; thread from `_execute_debate`/`_run_full_debate`. RED→GREEN.

### Task 6 — Extraction (DEC-B; prove-before-create)
Prove the ordering (EXTRACT before RAG). If feeding extraction is in scope, decide reorder vs a separate pre-indexed retrieval; else pin the deferral honestly. No invented wiring to an absent `rag_retrieval`.

### Task 7 — Acceptance proof
`tests/test_slice91_acceptance.py`: the final package lists retrieved evidence with ids/scores (already met — pin it end-to-end); the `rag_runtime_proof` is separate from pgvector connectivity (can differ). Injected fakes; no real OpenAI.

### Task 8 — Readiness doc reconciliation + characterization flip
Update `strict_full_live_readiness.md` for retrieval-consumption + the runtime-proof distinction; flip the drifted pins.

### Task 9 — Closeout
Reconcile plan to as-built; full `python -m pytest -q` + independent review; then closeout PR (only when approved).

## 9. Decisions (LOCKED before implementation, 2026-07-02: DEC-A → (a), DEC-B → (i), DEC-C → safe IDs/scores, DEC-D → summary signal only, DEC-E → confirmed)

- **DEC-A — Retriever scope.** (a) **Reuse the existing probe matches** as the "retrieved evidence" fed to consumers (recommended — safe, deterministic, matches the export, no external query in a FULL run); or (b) build a **query-driven retriever** (embed an external query → search). Choose.
- **DEC-B — Extraction ordering.** Extraction runs before RAG. Options: **(i, recommended)** descope extraction this slice (feed debate/analysis/scoring + export, which is the acceptance path) and pin the deferral; **(ii)** reorder / add a separate pre-indexed retrieval for extraction. Choose.
- **DEC-C — ids/scores vs text chunks.** Feed **safe matches (source_type/source_id/score + provenance)** — no text (recommended; mirrors the export + safety + No-Free-Facts); or recover + feed text chunks from the run context. Choose.
- **DEC-D — Runtime proof shape / gate.** Surface `rag_runtime_proof` as a summary/readiness field only (recommended), or also add a strict signal — but keep the existing `RAG_*_BLOCKED` gates unchanged. Confirm.
- **DEC-E — No schema change / test boundary.** Read-only retrieval, no migration; inject fakes, no real OpenAI. Confirm.

## 10. Open questions — ANSWERED (all locked before implementation)
1. DEC-A: **(a)** reuse probe matches — no query-driven retriever.
2. DEC-B: **(i)** descope extraction; the ordering deferral is pinned (Task 6).
3. DEC-C: **safe ids/scores only** — no text chunks.
4. Acceptance bullet 1: proving/pinning it end-to-end was confirmed sufficient (Task 7).
5. DEC-D: **run-summary/readiness field only** — no strict-readiness component, no new gate.
