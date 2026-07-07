# Strict Full-Live Readiness

Base SHA: `c6df5e67ea6ded42e235746d5a979945a0e2d9d1`

Slice: 53

Verdict: **Not full-live / not VC-presentable today.**

The system can complete the current local/deterministic FULL pipeline, but it cannot truthfully claim a strict full-live enterprise run. Strict full-live means no mocks, no deterministic model fallback where live components are required, no silent provider fallback, no disabled OCR/media paths when the data room needs them, and no "code exists" claims unless the component is actually wired into FULL and used.

No additional long `real_example` fallback run should be used as evidence for full-live readiness. The prior run is only a baseline showing that the fallback/local pipeline can complete.

> **Update (2026-06-06, post-Slice84):** The "strict-mode rejection of deterministic clients" item (see *Missing Implementations* and *Required Code Changes* #3) is now implemented as **execution-time enforcement**, in addition to the preflight readiness gate: extraction (Slice83 — `STRICT_LIVE_EXTRACTION_REQUIRED` / `STRICT_LIVE_EXTRACTION_PROVIDER_FAILED`) and debate Layer 1 / analysis / scoring (Slice84 — `STRICT_LIVE_{DEBATE,ANALYSIS,SCORING}_REQUIRED` / `..._PROVIDER_FAILED`, gated by `IDIS_DEBATE_BACKEND=anthropic`). Layer 2 IC challenge retains its own separate strict pattern. The Slice-53 census below is otherwise preserved as the original gap analysis.

> **Update (2026-06-10, post-Slice85):** The BYOL tenant-credential items (see the *External enrichment APIs* row, the *Missing Credentials And Env Vars* "Required enrichment credential work" item "Define and wire tenant credential provisioning for BYOL providers", and *Required Code Changes* #5 "Add tenant credential loading for BYOL providers") are now implemented: durable tenant-scoped encrypted credential storage (`enrichment_credentials` table, migration 0011, RLS; **AES-256-GCM** at rest as of Slice85, versioned `v2:` ciphertext, keyed by `IDIS_ENRICHMENT_ENCRYPTION_KEY`); whitelist-only env→store bootstrap (`COMPANIES_HOUSE_API_KEY`, `GITHUB_API_TOKEN`, `FRED_API_KEY`, `FINNHUB_API_KEY`, `FMP_API_KEY`) at both preflight and strict execution; connectors read only tenant credentials via the repository (never ambient env); and strict runs block on missing/non-durable/unhealthy BYOL credentials at preflight (`external_enrichment_apis`) with an execution-time fail-closed backstop. Credential lifecycle APIs/rotation remain future work.

> **Update (2026-06-11, post-Slice86):** The strict enrichment-execution items (see the *External enrichment APIs* row's "strict mode must fail on provider errors instead of swallowing them" and *Missing Implementations* "Strict provider error handling that fails instead of silently continuing") are now implemented: provider errors/blocked outcomes are **fatal in strict FULL by default**, with a per-provider `optional_in_strict` registration policy (all 15 default providers mandatory) whose failures are recorded-and-continued instead. The ENRICHMENT step additionally records a per-provider hit/miss/error/cache/blocked **ledger** (with rights class, `SourceGrade` mapping GREEN→B / YELLOW→C / RED+BYOL→C / RED-without-BYOL→D, and narrow identifier-mismatch conflict flags), and enrichment provenance is now **output-visible in the VC product bundle** (`run_summary` enrichment counts + `evidence_index.enrichment_evidence`); the strict provider matrix's `provenance_output_status` accordingly reports `enrichment_package_output_visible` for registered providers. FRED/Finnhub/FMP URL-key exposure is hardened by FetchError + httpx-log redaction (Finnhub header auth was not verifiable from primary docs, so query-param auth is retained). The Slice-53 census below is otherwise preserved as the original gap analysis.

> **Update (2026-07-01, post-Slice90):** The RAG items (see *Missing Infrastructure* "RAG embedding/index/query infrastructure" and *Missing Implementations* "RAG embeddings, vector index, retrieval API, and FULL integration") are now implemented and FULL-wired: a live OpenAI embedding provider (approved-live-only; the `deterministic` backend is rejected), pgvector storage (vector(1536), HNSW, RLS), indexing, and probe retrieval, strict-gated via `RAG_CONFIG_BLOCKED`/`RAG_HEALTH_BLOCKED`. `document_span` indexes parsed spans, OCR text, and transcripts; `calc_output` and `graph_summary` are indexed; enrichment-record indexing is deferred (no durable UUID source id). The Slice-53 census below is otherwise preserved as the original gap analysis.

> **Update (2026-07-02, post-Slice91):** RAG retrieval consumption is now wired (see *Required Code Changes* item 6 "Wire RAG retrieval results into analysis/debate"): the safe probe matches (source IDs/scores only — never span text, vectors, or query text) feed the analysis, scoring, and debate prompt contexts, and the final VC package lists the retrieved evidence with IDs/scores plus a `rag_runtime_proof` summary signal (retrieval ran and returned matches) distinct from pgvector connectivity. Extraction is deliberately deferred by step ordering (EXTRACT precedes RAG_EVIDENCE). Query-driven retrieval and text-chunk recovery remain out of scope, and the strict `RAG_*_BLOCKED` gates are unchanged. The Slice-53 census below is otherwise preserved as the original gap analysis.

> **Update (2026-07-04, post-Slice92):** The Layer 1 Evidence Trust Court output is now **durable and referenced by Layer 2**. Migration 0021 adds `validated_evidence_packages`, `evidence_trust_findings`, and `muhasabah_records` (tenant RLS, deterministic idempotent upserts; production-shaped `claim_mth_`/`finding-` string ids stored as text, UUID ids as UUID). The existing court/VEP steps (12/13) persist their safe rows — court findings, court-scoped Muhasabah records with structured uncertainty triples, and the VEP candidate — through Postgres/InMemory twin repositories, failing closed with `METHODOLOGY_LAYER1_PERSISTENCE_FAILED` on a write failure (the in-memory twin keeps non-database runs green). The `LAYER2_IC_CHALLENGE` result records the durable reference as `vep_ref_ids` (threaded from the VEP step's persisted summary, resume-safe), and the VC package lists a safe `vep_evidence` block (UUID-validated IDs, derived counts, status only — no claim text, transcripts, or debate content). Existing strict gates are unchanged. The Slice-53 census below is otherwise preserved as the original gap analysis.

> **Update (2026-07-06, post-Slice93):** The Layer 2 IC challenge is now a **distinct, durable, live-provider-proven, and deliverable-visible** second review layer (the `not-implemented` census row below is retained as the original Slice-53 gap analysis). *Durable:* migration 0022 adds `layer2_ic_challenges` and `layer2_ic_findings` (tenant RLS with the canonical NULLIF policy, deterministic idempotent upserts; `challenge_id` is a bare UUID5 stored as UUID, `finding_id` is a prefixed / LLM-supplied string stored as text and keyed compositely on `(tenant_id, run_id, finding_id)`; the challenge `safe_summary` carries `by_finding_type` / `by_severity` / `by_category` histograms plus a `stage_emphasis` block, and each finding row carries its bounded `category`). The `LAYER2_IC_CHALLENGE` step persists the safe challenge + finding rows through Postgres/InMemory twin repositories, failing closed with `LAYER2_PERSISTENCE_FAILED` (a static, ledger-safe reason; the in-memory twin keeps non-database runs green). *Visible:* a safe `layer2_challenge` block (challenge/finding IDs, counts, and category/severity histograms only — never claim text, transcripts, or model output) surfaces in the IC memo and QA brief. *Live-provider-proven:* a `_build_layer2_provenance` artifact (provider / model / prompt IDs, sanitized provider request IDs, and per-runner executed booleans) surfaces in the step result, and strict `debate_layer_2_ic_challenge` now clears only when **both** the challenger (default) and arbiter debate models are `runtime_call_proven` — a strictly stronger bar than debate layer 1's single-model check, so labels-only config never clears the gate. *Distinct:* findings carry a bounded `Layer2ChallengeCategory` enum (mapped 1:1 to the eight scorecard dimensions plus a `GENERAL` catch-all), and a scorecard-safe **stage-weighted** category emphasis reuses the scoring `stage_packs` weights read-only and never mutates the scorecard. Deferred to a Slice93 follow-on (honest scope): the `ic_advocate` role (challenger→arbiter only this slice), Layer-2 dissent, and deep VEP consumption (`vep_ref_ids` stays recorded-not-consumed). Existing strict gates are otherwise unchanged. The Slice-53 census below is otherwise preserved as the original gap analysis.

> **Update (2026-07-06, post-Slice94):** The Full VC Bundle content is now **complete and acceptance-proven**. The `ProductBundleExporter` emits the whole investor package — screening snapshot, IC memo, truth dashboard, QA brief, executive summary, commercial / financial diligence, risk register — plus the `evidence_index`, `run_summary`, and a new safe **`provenance_appendix`** artifact that consolidates the five run-level LLM provenance blocks (extraction / debate / analysis / scoring / layer2): provider / model / prompt IDs, sanitized provider request IDs, and executed booleans only, with **typed value filtering** (boolean fields keep booleans, `prompt_ids` keeps bounded strings, every other field a bounded string scalar; nested / mistyped values are dropped, never stringified or coerced) so no prompt body, model output, API key, path, or exception text can ride in under an allowed key. The provenance blocks are threaded from the per-step result summaries through the export path and cross-referenced from `run_summary` (`provenance_status` / `provenance_blocks`) and `evidence_index` (`provenance_appendix`). Acceptance is proven end-to-end over an exported bundle: every material assertion (each `is_factual` deliverable fact) links to safe claim / calc evidence IDs — No-Free-Facts is enforced at generation — and every ref used anywhere resolves through the `evidence_index`, while the run-level provenance IDs resolve through the `provenance_appendix`. Financial diligence carries **reproducible** calc lineage — a SHA256 `reproducibility_hash`, `formula_hash`, `formula_version`, `code_version`, input-claim lineage, and **frozen `assumptions`** — surfaced in the `calculation_package` and byte-identical across independent re-runs. Nothing is deferred beyond the acceptance spine this slice; tests use **injected fakes only** (no real Anthropic; filesystem object store; **no database** — no Postgres path is exercised this slice, which adds no new durable tables). Existing strict gates are unchanged. The Slice-53 census below is otherwise preserved as the original gap analysis.

> **Update (2026-07-07, post-Slice95):** The strict run and its investor package are now **reviewable through the product API/UI without exposing any private report**, and the UI↔backend review contracts are **locked by tests** (Master plan §392; closes Phase H). Two safe backend read-models close the gaps: **`GET /v1/strict-readiness`** projects `build_strict_full_live_readiness_report` down to component modes, blocking-component names, and required env-var **names** / service labels only — the internal report's evidence file:line refs, `env_sources`, free-text `blocker_message`, `component_inventory`, and provider matrices are dropped, and requirement tokens like `IDIS_EXTRACT_BACKEND=anthropic` are normalized to bare names (deduped, stable first-occurrence order) so **no required value leaks**. This reviewer GET is **config-only inspection, not a live-connectivity proof**: it reports wiring / credential / infrastructure modes through config-only health checkers with the object-store probe disabled, so it never opens a live Neo4j / Postgres connection, calls the embedding provider, or writes to the object store, and it is **exempt from the request DB transaction** (`DBTransactionMiddleware`) so it stays available even when the database is down (missing-env detection is preserved; strict **run admission** still performs the real live checks). The second read-model, **`GET /v1/deals/{deal_id}/runs`**, lists safe run summaries (run / deal IDs, status, mode, timestamps) behind a stable **`(created_at, run_id)`** composite cursor that drops no row when `created_at` ties. The debate `rounds` passthrough is hardened into a typed safe-shape **`DebateRoundSummary`** (round number / role / claim + calc **ref IDs only**; `extra="forbid"`), and the OpenAPI YAML is reconciled to reference it. The Next.js review UI adds a strict-readiness screen, a deal-scoped run list plus a run-monitor step-ledger / blocker-**code** detail, a data-room upload form, and human approve / reject / correct + override-justification action screens — reusing the existing truth dashboard, claim / Sanad explorer, debate transcript, and deliverables / manifest surfaces. **Contracts locked by tests:** a backend contract test compares the static OpenAPI YAML against the generated FastAPI schema (`required` / `properties` / `additionalProperties`) for the Slice95 review read-models (acknowledged caveat: it does not compare full property schemas or enum values), and UI client / component contract tests pin the consumed shapes and the safe-shape rendering boundaries (error **codes** not messages, env-var **names** not values, `RunStatus` includes `CANCELLED`). Boundaries this slice: **injected fakes only — no real Anthropic, no migration** (no new durable tables; the run / readiness / debate reads reuse existing storage), and **no private report or raw evidence text** surfaces in any reviewer component. Existing strict gates are unchanged. The Slice-53 census below is otherwise preserved as the original gap analysis.

## Strict Classification

Allowed strict classifications:

- `live-wired-and-used`: component is implemented, configured, called by FULL, and observable in outputs/evidence.
- `code-exists-but-not-wired`: implementation exists, but FULL does not call it or does not feed its results downstream.
- `configured-but-failed-health-check`: configuration exists, but a live readiness check failed.
- `missing-credentials`: live path requires credentials or model provider keys that are absent.
- `missing-infrastructure`: live path requires runtime services, binaries, models, or databases that are absent.
- `not-implemented`: no production implementation exists for the required component.

## Strict Matrix

| Component | Strict status | Evidence | Exact blocker |
| --- | --- | --- | --- |
| Supported parsers | `live-wired-and-used` | Default ingestion routes PDF/XLSX/DOCX/PPTX through the parser registry and persisted document flow. | Strict coverage is limited to supported document formats. |
| Extraction LLM | `missing-credentials` | Extraction client selection defaults to deterministic unless `IDIS_EXTRACT_BACKEND=anthropic`. | `ANTHROPIC_API_KEY` is absent; strict mode must reject deterministic extraction. |
| OCR | `code-exists-but-not-wired` | `TesseractOcrAdapter` and OCR parser configuration exist; default ingestion does not enable OCR. | FULL must enable OCR for OCR-required files or fail before execution. |
| MP4/STT | `missing-infrastructure` | Faster-whisper adapter exists; current data room includes MP4s; harness/public upload path defers media. | `ffmpeg`, `ffprobe`, and a provisioned faster-whisper model are missing; FULL media ingestion is not wired. |
| Deterministic calculations | `live-wired-and-used` | `CalcRunner` and deterministic calculation steps are wired into SNAPSHOT/FULL paths. Slice87 persists the methodology records durably and dedups the CALC step against them by reproducibility hash (both paths share the `metadata_for_calc` source so metadata-bearing types unify too), and surfaced calc outputs into financial tables, graph projection, RAG evidence, and the VC `calculation_package` (with `formula_version`). | None for execution itself. |
| External enrichment APIs | `missing-credentials` | Default registry includes public and BYOL connectors; FULL enrichment iterates providers. | BYOL credentials are not loaded into tenant credential storage; strict mode must fail on provider errors instead of swallowing them. |
| Live LLM/model clients | `missing-credentials` | Anthropic client exists and fails closed when selected without key; defaults remain deterministic. | `ANTHROPIC_API_KEY` is absent; strict mode must require live backend selection for extraction, debate, analysis, and scoring. |
| Agent analysis | `missing-credentials` | Eight specialist agents are wired through FULL analysis. | Analysis uses `IDIS_DEBATE_BACKEND`; without Anthropic configuration it uses deterministic analysis output. |
| Debate layer 1 | `missing-credentials` | `DebateOrchestrator` is called by FULL debate. | Default `RoleRunners` are deterministic; strict live debate requires `IDIS_DEBATE_BACKEND=anthropic` and live model credentials. |
| Debate layer 2 / IC challenge | `not-implemented` | Audit found no distinct second challenge/review debate orchestrator. | A production Layer 2 debate design and implementation are required. |
| Muhasabah / NFF gates | `live-wired-and-used` | Debate, analysis, scoring, and deliverables validate No-Free-Facts / muhasabah outputs. | Validation is not enough to make deterministic or unwired components full-live. |
| Scoring LLM | `missing-credentials` | Scoring engine is wired, but client selection follows `IDIS_DEBATE_BACKEND`. | Without Anthropic configuration, scoring uses deterministic scorecard output. |
| RAG/evidence retrieval | `missing-credentials` | Live OpenAI embedding (approved-live-only — the `deterministic` backend is rejected), pgvector storage (vector(1536), HNSW, RLS), span indexing, and probe retrieval exist and are FULL-wired; strict blocks safely via `RAG_CONFIG_BLOCKED`/`RAG_HEALTH_BLOCKED`. Indexes `document_span` (parsed spans, OCR text, and transcripts), `calc_output`, and `graph_summary`. Post-Slice91 the probe matches (safe IDs/scores only) feed the analysis, scoring, and debate prompt contexts, and the VC package lists them with a `rag_runtime_proof` summary signal distinct from pgvector connectivity; extraction is deferred by step ordering. | `IDIS_ENABLE_VECTOR_SEARCH`/`OPENAI_API_KEY`/pgvector env required; strict blocks until configured. Enrichment-record indexing is deferred (no durable UUID source id — needs a durable enrichment table first). Query-driven retrieval and text-chunk recovery remain out of scope. |
| Graph/evidence layer | `missing-credentials` | FULL calls `GraphProjectionService` after durable Postgres writes, projecting deal/document/span/claim/evidence, the Sanad transmission chain, defects, and calculations into Neo4j (idempotent MERGE, tenant-scoped). Deliverables projection is deferred (no Deliverable node in the locked 12-node schema). | `NEO4J_*` env is absent; strict blocks safely via `GRAPH_HEALTH_BLOCKED` until configured. Graph retrieval is wired into FULL and feeds graph-derived conclusions (per-claim lineage + defect-impact, with claim/calc provenance) into the VC package; analysis/debate/scoring/Layer 2 consumer feeds are a later follow-on. |
| Deliverable generation | `live-wired-and-used` | `DeliverablesGenerator.generate()` is called by FULL deliverables. | Generated bundle is in-memory and only as good as upstream evidence; it is not strict-live if upstream components are fallback/missing. |
| Export bundle | `code-exists-but-not-wired` | Product has deliverable export primitives; Slice 53 private exporter was experiment tooling and is not part of product wiring. | Product-wired export from strict-live run outputs is still required. |

## Live-Wired-And-Used Today

- Supported PDF/XLSX/DOCX/PPTX parsing through ingestion and selected-document FULL runs.
- Deterministic calculation execution through `CalcRunner`.
- Muḥāsabah and No-Free-Facts validation gates where the current debate, analysis, scoring, and deliverable paths invoke them.
- In-memory deliverable generation through `DeliverablesGenerator`.

These are not sufficient for full-live VC-grade readiness because strict live requires the model, OCR/media, enrichment, RAG, graph, Layer 2 debate, and export paths to be live and observable too.

## Code Exists But Is Not Wired

- OCR execution exists, but default ingestion omits `OcrConfig`.
- Media/STT parsing exists, but FULL/public upload defers MP4 files rather than transcribing them.
- Neo4j graph projection is wired into FULL (it calls `GraphProjectionService` after durable Postgres writes, feeding claims/evidence/Sanad chain/defects/calculations); it requires `NEO4J_*` env to write and blocks safely without it. Graph retrieval is also wired into FULL and feeds graph-derived conclusions (with provenance) into the VC package; strict blocks safely via `GRAPH_RETRIEVAL_BLOCKED`. Analysis/debate/scoring/Layer 2 consumer feeds are a later follow-on.
- Product export primitives exist, but strict VC bundle export is not product-wired.
- Several methodology boundaries are in-memory/run-scoped and explicitly defer persistence, promotion, provider execution, Layer 2 decisioning, and delivery surfaces. (Exception: the methodology deterministic-calculation boundary now persists durably and is unified with the CALC step per Slice87; the others remain in-memory.)

## Missing Credentials And Env Vars

No secret values should be documented. Strict-live preflight must check presence only.

Required model env:

- `IDIS_EXTRACT_BACKEND=anthropic`
- `IDIS_DEBATE_BACKEND=anthropic`
- `ANTHROPIC_API_KEY`
- `IDIS_ANTHROPIC_MODEL_EXTRACT`
- `IDIS_ANTHROPIC_MODEL_DEBATE_DEFAULT`
- `IDIS_ANTHROPIC_MODEL_DEBATE_ARBITER`

Required durable runtime env:

- `IDIS_DATABASE_URL`
- `IDIS_API_KEYS`
- `IDIS_OBJECT_STORE_BACKEND`

Required graph env:

- `NEO4J_URI`
- `NEO4J_USERNAME`
- `NEO4J_PASSWORD`

Required media/STT env:

- `IDIS_MEDIA_STT_MODEL_PATH` or `IDIS_MEDIA_STT_MODEL_NAME`
- `IDIS_MEDIA_STT_ALLOW_DOWNLOAD` only if explicit model download is approved

Required enrichment credential work:

- Define and wire tenant credential provisioning for BYOL providers.
- Current default enrichment setup uses an empty in-memory credential repository.
- BYOL providers requiring credentials include `companies_house`, `github`, `fred`, `finnhub`, and `fmp`.

## Missing Infrastructure

- Postgres/pgvector service configured through `IDIS_DATABASE_URL`.
- Neo4j service reachable through `NEO4J_URI`.
- `ffmpeg` and `ffprobe` on the runtime path.
- A validated local faster-whisper model directory or an explicitly approved download path.
- RAG embedding/index/query infrastructure.
- Product export storage/persistence path for strict-live deliverables.

## Missing Implementations

- `IDIS_REQUIRE_FULL_LIVE=1` or `--require-full-live` preflight gate.
- Strict mode rejection of deterministic extraction, debate, analysis, and scoring clients.
- Strict OCR-required document detection before run execution.
- Strict MP4/STT detection before run execution.
- Strict enrichment credential/provider health checks before run execution.
- Strict provider error handling that fails instead of silently continuing.
- RAG embeddings, vector index, retrieval API, and FULL integration.
- Neo4j projection/retrieval call sites in FULL.
- Distinct Layer 2 / IC challenge debate.
- Product-wired strict export bundle generated from live run outputs.

## Required Code Changes

1. Add strict full-live configuration:
   - `IDIS_REQUIRE_FULL_LIVE=1` for API/runtime.
   - `--require-full-live` for private/evaluation harnesses.

2. Add a pre-run strict readiness gate:
   - Run before upload-to-run execution or before `RunExecutionService.execute`.
   - Return structured blockers instead of starting a fallback run.
   - Classify blockers using the strict status vocabulary in this document.

3. Forbid fallback clients in strict mode:
   - Extraction must reject `DeterministicLLMClient`.
   - Debate must reject deterministic `RoleRunners`.
   - Analysis must reject `DeterministicAnalysisLLMClient`.
   - Scoring must reject `DeterministicScoringLLMClient`.

4. Wire OCR and media readiness checks:
   - Detect OCR-required documents from persisted preflight/parser metadata.
   - Detect MP4/media files before run execution.
   - Require OCR adapter readiness, `ffmpeg`, `ffprobe`, and STT model readiness where applicable.

5. Make enrichment strict:
   - Add tenant credential loading for BYOL providers.
   - Health-check public providers.
   - Fail strict runs if intended providers are missing, blocked, or failed.
   - Persist and expose provenance references in evidence and deliverables.

6. Implement retrieval layers:
   - Add durable UUID-keyed enrichment-record persistence, then index enrichment records — the only scoped evidence type not yet indexed (parsed spans, OCR, and transcripts index via `document_span`; `calc_output` and `graph_summary` are indexed).
   - Wire RAG retrieval results into analysis/debate (retrieval is already wired into FULL and surfaced in the VC package).
   - Wire graph-retrieval conclusions into analysis/debate/scoring/Layer 2 (retrieval is already wired into FULL and feeds the VC package).

7. Implement Layer 2 debate:
   - Add distinct IC challenge/review debate orchestration.
   - Preserve muhasabah and No-Free-Facts gates.
   - Record rounds and model/client used.

8. Product-wire export:
   - Export bundle must come from strict-live run outputs.
   - Export must include evidence/provider/model/graph/RAG usage metadata.
   - Do not label generated artifacts VC-presentable unless strict preflight and strict run both pass.

## Slice 54 Boundary

Slice 54 should build the strict full-live foundation only. It should not attempt to wire every enterprise component.

The Slice 54 objective is to add the strict gate so the system fails before execution if any intended enterprise component is fallback, disabled, missing credentials, missing infrastructure, or not wired. Component wiring should follow in later slices.
