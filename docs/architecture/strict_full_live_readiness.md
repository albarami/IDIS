# Strict Full-Live Readiness

Base SHA: `c6df5e67ea6ded42e235746d5a979945a0e2d9d1`

Slice: 53

Verdict: **Not full-live / not VC-presentable today.**

The system can complete the current local/deterministic FULL pipeline, but it cannot truthfully claim a strict full-live enterprise run. Strict full-live means no mocks, no deterministic model fallback where live components are required, no silent provider fallback, no disabled OCR/media paths when the data room needs them, and no "code exists" claims unless the component is actually wired into FULL and used.

No additional long `real_example` fallback run should be used as evidence for full-live readiness. The prior run is only a baseline showing that the fallback/local pipeline can complete.

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
| Deterministic calculations | `live-wired-and-used` | `CalcRunner` and deterministic calculation steps are wired into SNAPSHOT/FULL paths. | None for deterministic calculation execution itself. |
| External enrichment APIs | `missing-credentials` | Default registry includes public and BYOL connectors; FULL enrichment iterates providers. | BYOL credentials are not loaded into tenant credential storage; strict mode must fail on provider errors instead of swallowing them. |
| Live LLM/model clients | `missing-credentials` | Anthropic client exists and fails closed when selected without key; defaults remain deterministic. | `ANTHROPIC_API_KEY` is absent; strict mode must require live backend selection for extraction, debate, analysis, and scoring. |
| Agent analysis | `missing-credentials` | Eight specialist agents are wired through FULL analysis. | Analysis uses `IDIS_DEBATE_BACKEND`; without Anthropic configuration it uses deterministic analysis output. |
| Debate layer 1 | `missing-credentials` | `DebateOrchestrator` is called by FULL debate. | Default `RoleRunners` are deterministic; strict live debate requires `IDIS_DEBATE_BACKEND=anthropic` and live model credentials. |
| Debate layer 2 / IC challenge | `not-implemented` | Audit found no distinct second challenge/review debate orchestrator. | A production Layer 2 debate design and implementation are required. |
| Muhasabah / NFF gates | `live-wired-and-used` | Debate, analysis, scoring, and deliverables validate No-Free-Facts / muhasabah outputs. | Validation is not enough to make deterministic or unwired components full-live. |
| Scoring LLM | `missing-credentials` | Scoring engine is wired, but client selection follows `IDIS_DEBATE_BACKEND`. | Without Anthropic configuration, scoring uses deterministic scorecard output. |
| RAG/evidence retrieval | `not-implemented` | pgvector is provisioned in database setup only; no app embedding/index/query path exists. Debate retrieval node only marks retrieval complete. | Need production embedding, index, query, and FULL wiring into debate/analysis/evidence. |
| Graph/evidence layer | `code-exists-but-not-wired` | Neo4j driver, graph repository, and projection service exist. | FULL does not call `GraphProjectionService`; `NEO4J_*` env is absent. |
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
- Neo4j graph projection exists, but FULL does not call the graph projection service.
- Product export primitives exist, but strict VC bundle export is not product-wired.
- Several methodology boundaries are in-memory/run-scoped and explicitly defer persistence, promotion, provider execution, Layer 2 decisioning, and delivery surfaces.

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
   - Add embedding generation.
   - Add pgvector index/query path.
   - Wire RAG results into debate, analysis, and evidence references.
   - Wire Neo4j graph projection and graph retrieval into FULL.

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
