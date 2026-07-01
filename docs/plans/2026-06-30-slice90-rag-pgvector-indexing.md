# Slice 90 — RAG / pgvector Indexing — Implementation Plan

> **For Claude / agentic workers:** REQUIRED SUB-SKILL: `superpowers:executing-plans` (or `subagent-driven-development`) task-by-task. Per task: `test-driven-development` (RED → verify red → minimal GREEN → verify), `verification-before-completion` before any status claim, `using-git-worktrees` already done (`C:\Projects\IDIS\IDIS-slice90`, branch `slice90-rag-pgvector-indexing`, base `origin/main` @ `2fd115b`), `finishing-a-development-branch` before commit/PR. **Reuse before create. STOP for approval after each task.** Confirm the §9 decisions (especially **DEC-A scope breadth** and **DEC-C graph-summary text**) BEFORE any indexing task. **STATUS (as-built): calc_output + graph_summary indexed; OCR + transcripts reuse `document_span`; enrichment deferred (no durable UUID source); no migration; strict gate unchanged — see §11 Closeout.** (Original intent: discovery + planning; now executed.)

**Goal (master plan):** Persist embeddings and searchable retrieval records for tenant-scoped evidence.

**Scope (master plan):** pgvector migrations; live embedding provider abstraction; index parsed spans, OCR text, transcripts, enrichment records, calc outputs, graph summaries.

**Acceptance (master plan):** Strict run persists embeddings using approved live provider only; no fake/deterministic embeddings in strict mode.

**Architecture:** The pgvector storage (table + RLS + HNSW), the live OpenAI embedding provider, the strict fail-closed gates (deterministic rejected; `RAG_*_BLOCKED`), and the document-span indexing + probe-retrieval pipeline already exist (Slice62/63) and run in FULL. The master **acceptance** (approved-live-only, no-fake in strict) is therefore already enforced. The remaining substance is to **extend indexing to the other five scoped evidence types** (OCR text, transcripts, enrichment records, calc outputs, graph summaries), all of which fit the existing generic schema — plus prove the acceptance and reconcile the stale readiness doc.

**Tech Stack:** Python 3.11, Pydantic v2, Postgres + pgvector (vector(1536), HNSW, RLS), OpenAI embeddings, pytest, ruff, mypy.

---

## 1. Baseline (this worktree, verified)

- Worktree `C:\Projects\IDIS\IDIS-slice90`, branch `slice90-rag-pgvector-indexing`, base `origin/main` @ `2fd115bbc46258ff137a3c27a1c6febe5fba4cd2`. Imports resolve to this worktree.
- Baseline gates green: `ruff check`/`format --check` clean (783) · clean-cache `mypy src/idis` **Success, 397** · RAG/pgvector smoke **66 passed / 10 skipped**.
- Zero Slice90 artifacts before this doc.

## 2. Already built (hand-verified — reuse, do not recreate)

- **pgvector storage (Slice62):** `migrations/versions/0017_vector_embeddings.py` — `vector_embeddings` table (`embedding_id, tenant_id, deal_id, run_id, source_type TEXT, source_id UUID, embedding_model, embedding_dimensions, content_hash, embedding vector(1536)`), unique `(tenant_id, source_type, source_id, content_hash)`, HNSW `vector_cosine_ops` index, RLS `idis.tenant_id` policy. **No text column** (only the vector + ref). `VECTOR_EMBEDDING_DIMENSIONS = 1536` pinned in `services/rag/constants.py`.
- **Repository:** `persistence/repositories/vector_embeddings.py` `PostgresVectorEmbeddingsRepository` — `upsert_embedding(deal_id, source_type, source_id, content_hash, embedding, embedding_model, embedding_dimensions, run_id)` (ON CONFLICT upsert) + `similarity_search(deal_id, query_embedding, limit)` (cosine; returns `{source_type, source_id, score}` only). Tenant-scoped via `set_tenant_local`. **No InMemory variant.**
- **Live embedding provider:** `services/rag/indexing.py` `create_openai_embed_batch` (OpenAI `text-embedding-3-small`, 1536) + `EmbeddingClientFactory` protocol (`embedding_health.py`). `ALLOWED_EMBEDDING_BACKENDS = frozenset({"openai"})`.
- **Strict fail-closed gate (acceptance is already enforced):** `embedding_health.check_embedding_health` rejects `deterministic` (`embedding_health.py:164-167`) and live-probes OpenAI; `runs.py:_run_full_rag_evidence` raises `RAG_CONFIG_BLOCKED` (2246), `RAG_HEALTH_BLOCKED` (2277), `RAG_DATABASE_BLOCKED` (2286), `RAG_INDEXING_BLOCKED` (2339), `RAG_PROBE_RETRIEVAL_BLOCKED` (2369) in strict; preflight `strict_full_live._rag_foundation_layer` blocks unless embedding+pgvector are HEALTHY.
- **Indexing + retrieval pipeline (Slice63):** `index_document_spans_for_deal` (indexes `document_span` only — `indexing.py:20,110`) + `retrieve_rag_probe_evidence` (probe-mode; safe `{source_type,source_id,score}` only). FULL-wired (runs.py:2311) and consumed as safe counts/status by Layer 2 IC challenge + the deliverables `_rag_package` (visibility only, like Slice89 graph evidence).
- **Slice89 graph summaries (an indexing input):** `services/graph/retrieval.py:retrieve_deal_graph_summary` returns safe-fields-only `graph_conclusions` (per-claim lineage + defect-impact + co-occurrence count) — available in the FULL run's accumulated state when RAG runs.

## 3. True gaps (hand-verified, with evidence)

- **G1 — only 1 of 6 scoped evidence types is indexed.** `index_document_spans_for_deal` persists embeddings ONLY for `source_type="document_span"` (parsed spans). **OCR text, transcripts, enrichment records, calc outputs, graph summaries are NOT embedded** — no `source_type` constants, no indexing pipelines for them. (Calc outputs are *reflected* read-only via `rag_calc_evidence` — Slice87 — but never embedded.)
- **G2 — stale readiness doc.** `strict_full_live_readiness.md:46` still says `RAG/evidence retrieval | not-implemented | … no app embedding/index/query path exists`, which is false: the path exists, is strict-gated, and runs in FULL. (Line ~121 also lists "Add embedding generation / pgvector index/query path" as pending.)
- **G3 — no Slice90 tests/characterization** for the acceptance (approved-live-only/no-fake) or the new indexing types.

## 4. Design / approach

1. **No schema change needed.** The `vector_embeddings` table is generic (`source_type TEXT`, `source_id UUID`). Indexing a new type = add a `source_type` constant + an indexing pipeline that reads the durable entity, embeds its text via the existing OpenAI provider, and upserts. Unique `(tenant_id, source_type, source_id, content_hash)` gives dedup for free. (Confirm no new migration — see DEC-B.)
2. **Reuse the existing pipeline shape.** Each new type mirrors `index_document_spans_for_deal`: gather `(source_id, content_hash, text)` tuples → `embed_batch(texts)` → `upsert_embedding(source_type=…, source_id=…)`. Strict gate, health, and provider are unchanged.
3. **Safe content only.** For graph summaries (no raw text — Slice89 safe-fields-only), CONSTRUCT a deterministic text string from `graph_conclusions` (e.g. `"Claim X: chain depth N, weakest grade G, corroboration S, K independent sources"`); only the vector + `source_id` (claim/defect id) are stored — no raw content (DEC-C). For OCR/transcripts/enrichment/calc, embed the already-persisted text/summary; verify no private leakage in retrieval (it returns ids/scores only).
4. **Acceptance is already enforced.** Slice90 PROVES it (strict + deterministic → blocks; strict + approved-live → persists) and extends coverage; it does not rebuild the gate.

## 5. Safety / strict boundaries

- Strict approved-live-only / no-fake stays exactly as built (`RAG_HEALTH_BLOCKED`, deterministic rejected). No new strict infra.
- Retrieval continues to expose only `{source_type, source_id, score}` — no vectors/text/queries (Slice63 privacy test must stay green).
- Tenant isolation unchanged (RLS + `set_tenant_local`).
- No real OpenAI call in tests — inject an embedding client factory / `embed_batch` fake (deterministic vectors for test only, never in strict). Real pgvector exercised only in the Postgres-integration CI job (the existing Slice62 pattern).

## 6. Verification gate (every task)

Import proof · `pytest` (task tests + relevant regression) · `ruff format --check .` · `ruff check .` · clean-cache `mypy src/idis` · `scripts/forbidden_scan.py --repo-root .` · `git diff --check`. Closeout: full `pytest` + independent review.

## 7. Risks

- **Test boundary:** new indexing needs DB-backed tests (real pgvector) for persistence + injected `embed_batch` for the embedding. Follow the Slice62/63 split (unit with fakes; DB in the postgres-integration job).
- **source_id type:** column is `UUID NOT NULL` — confirm every new source ref (span/ocr/transcript/calc/claim/defect/enrichment) is a UUID; deal-level co-occurrence would need a UUID source (e.g. deal_id) or be excluded.
- **OCR/transcript overlap:** OCR/STT may already persist as `document_spans` — confirm whether they are a distinct `source_type` or already flow through span indexing (avoid double-indexing); this is per-task discovery.
- **Scope creep:** five new pipelines is large — see DEC-A.

## 8. Tasks (bite-sized, TDD)

> Acceptance-critical: **T1–T3**. Indexing breadth: **T4–T8** (subject to DEC-A).

### Task 1 — Characterization (pin current truth)
- **Test:** `tests/test_slice90_rag_indexing_characterization.py` (new).
- Pin: only `document_span` is indexed (`SOURCE_TYPE_*` inventory); `ALLOWED_EMBEDDING_BACKENDS == {"openai"}`; `check_embedding_health` rejects `deterministic`; FULL strict raises `RAG_HEALTH_BLOCKED`/`RAG_CONFIG_BLOCKED`; readiness doc has the stale "not-implemented" wording. GREEN-on-arrival. Any RED → STOP + report.

### Task 2 — Acceptance proof (approved-live-only / no-fake)
- **Test:** `tests/test_slice90_acceptance.py` (new).
- Prove: strict FULL with a deterministic/disallowed backend → `RAG_HEALTH_BLOCKED` (no embeddings persisted); strict FULL with the approved live provider (injected fake client standing in for OpenAI) → embeddings persisted via `upsert_embedding`; no fake/deterministic vector is persisted in strict. Injected fakes; no real OpenAI.

### Task 3 — Readiness doc reconciliation (G2)
- **Files:** `docs/architecture/strict_full_live_readiness.md`; flip the Task 1 stale-wording pin.
- Update line 46 (and the line ~121 pending item) to the as-built reality: embedding/index/query path exists, is strict-gated (approved-live-only, no-fake), indexes document spans today; the remaining evidence types are this slice's scope/follow-on.

### Tasks 4–8 — Indexing pipelines (one per evidence type; subject to DEC-A)
Each task: add a `SOURCE_TYPE_*` constant + an indexing function mirroring `index_document_spans_for_deal`, wire it into the FULL RAG step, RED→GREEN with an injected `embed_batch` + a DB-backed persistence assertion, preserving the safe-retrieval + strict gate.
- **T4 — calc outputs** (`source_type="calc_output"`, `source_id=calc_id`; embed the calc output summary). Highest-value: turns the existing read-only `rag_calc_evidence` into real embeddings.
- **T5 — graph summaries** (`source_type="graph_summary"`, `source_id=claim_id`/`defect_id`; CONSTRUCT text from Slice89 `graph_conclusions` — DEC-C). Natural Slice89 follow-on.
- **T6 — enrichment records** (`source_type="enrichment"`; embed the enrichment text/summary).
- **T7 — OCR text** (confirm distinct from `document_span`; `source_type="ocr_text"` if separate).
- **T8 — transcripts** (`source_type="transcript"`; media STT segments).

### Task 9 — Closeout
- Reconcile this plan to as-built; full local gate (incl. full `pytest`); independent review of the cumulative diff; then closeout PR (only when approved).

## 9. Decisions to confirm before implementation

- **DEC-A — Scope breadth (the big one).** The acceptance (approved-live-only/no-fake) is ALREADY enforced, so the substance of Slice90 is indexing the five missing types. Options: **(A)** all five (T4–T8); **(B, recommended)** high-value first — calc outputs + graph summaries (T4–T5, the Slice87/89 follow-ons) now, defer OCR/transcripts/enrichment; **(C)** acceptance-only (T1–T3) — thin, adds little new functionality. Acceptance proof + doc + characterization (T1–T3) are needed regardless.
- **DEC-B — No schema change.** The generic `vector_embeddings` table covers all types; confirm no new migration this slice (add only `source_type` constants + pipelines).
- **DEC-C — Graph-summary text.** Index graph summaries by constructing deterministic text from the safe `graph_conclusions` (no raw content); `source_id` = claim/defect id. Confirm the constructed-text approach + which granularity (per-claim, per-defect).
- **DEC-D — Strict gate unchanged.** Keep the existing `RAG_*_BLOCKED` gates + approved-live-only/no-fake; no new strict infra.
- **DEC-E — Test boundary.** Unit tests inject `embed_batch`/client factory (deterministic test vectors, never in strict); real pgvector persistence asserted only in the postgres-integration CI job (Slice62 pattern). No real OpenAI in tests.

## 10. Open questions for you
1. DEC-A: which breadth — all five (A), high-value calc+graph first (B), or acceptance-only (C)?
2. DEC-C: graph-summary granularity (per-claim + per-defect records?) and the exact constructed-text shape?
3. DEC-B: confirm no new migration (generic schema), or do you want a dedicated `source_type` enum/check constraint added?
4. Is the existing probe-mode retrieval sufficient for "searchable retrieval records," or does this slice need real query-driven retrieval (semantic search on an external query)?
5. OCR/transcripts: should they be a distinct `source_type`, or is indexing them as `document_span` (if they already persist as spans) acceptable?

## 11. Closeout — As-Built reconciliation (Tasks 1–8)

**Status: acceptance met; indexing breadth complete except the deferred enrichment blocker.** Decisions locked at execution: **DEC-A** all five types with reuse-before-create; **DEC-B** no migration; **DEC-C** graph-summary constructed safe text per claim/defect; **DEC-D** strict gate unchanged; **DEC-E** inject fake embeds, no real OpenAI.

| Scoped evidence type | Status | As-built |
| --- | --- | --- |
| Parsed spans | **Indexed (pre-Slice90)** | `index_document_spans_for_deal` → `source_type="document_span"` (Slice63). |
| OCR text | **Reuse `document_span` (T7)** | OCR persists as `PAGE_TEXT` `DocumentSpan`s (Slice79) with UUID `span_id`; the span indexer ignores span_type/locator, so OCR is already indexed. No `SOURCE_TYPE_OCR_TEXT`. |
| Transcripts | **Reuse `document_span` (T8)** | Media/STT transcripts persist as `TIMECODE` `DocumentSpan`s (Slice80) with UUID `span_id`; already indexed. No `SOURCE_TYPE_TRANSCRIPT`. |
| Calc outputs | **Indexed (T4)** | `SOURCE_TYPE_CALC_OUTPUT` + `index_calc_outputs_for_deal`; `source_id`=calc_id, `content_hash`=reproducibility_hash; safe text (type + primary value + unit). Wired additively after spans. |
| Graph summaries | **Indexed (T5)** | `SOURCE_TYPE_GRAPH_SUMMARY` + `index_graph_summaries_for_deal`; per claim/defect UUID `source_id` (non-UUID skipped+counted), `content_hash`=sha256(safe text). `_execute_rag_evidence` threads `graph_conclusions`. |
| Enrichment records | **Deferred blocker (T6)** | No durable enrichment-record table and no stable UUID (`ref_id`=`"enrich-{provider}-{run_prefix}"`, provenance `source_id` is a legacy string). `vector_embeddings.source_id` is `UUID NOT NULL`. Not indexed; **no invented ids, no schema change, no migration**. Follow-up: add a durable UUID-keyed enrichment table, then index. |

**Acceptance proven** — `tests/test_slice90_acceptance.py`: strict runs persist embeddings via the approved live provider only; a deterministic/disallowed backend blocks (`RAG_HEALTH_BLOCKED`) and persists nothing; no real OpenAI (injected fakes).

**Boundaries honored:** existing generic vector schema (no migration); strict gate unchanged (calc/graph indexing is additive after span indexing, no new block codes, rides the already-healthy provider); safe-fields-only embed text (only vector + source_id + content_hash stored — no raw text); UUID source ids only. **Deferred:** enrichment indexing (durable-persistence follow-up).

**Production footprint:** `services/rag/indexing.py`, `api/routes/runs.py`, `services/runs/orchestrator.py`. Docs: this plan + the readiness reconciliation.
