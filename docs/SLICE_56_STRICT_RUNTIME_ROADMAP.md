# Slice 56 Strict Runtime Roadmap

Slice 56 does not make IDIS VC-ready. Slice 56 makes false success claims impossible by establishing strict runtime inventory, secret-safe local configuration loading, and fail-before-upload/run gates.

Slice 56 does not wire downstream components. BYOL enrichment, OCR/media ingestion, durable export, calculation package visibility, Neo4j, RAG, Layer 2 IC challenge, UI/API package access, and the final live `real_example` run remain assigned to Slices 57-65.

Final VC output cannot be claimed until Slice 65 strict run passes with `IDIS_REQUIRE_FULL_LIVE=1`, strict readiness `may_proceed=true`, and a persisted product package available through product API/UI paths.

No later slice is complete unless its component is visible in all applicable surfaces:

- Strict readiness inventory.
- Run provenance and step summaries.
- Durable storage or durable database state.
- Final investor-facing output where the component contributes to the package.

## Slice 56: Canonical Strict Runtime, Env, And Preflight Foundation

Goal: establish the exact canonical FULL path from `origin/main`, secret-safe strict env loading, full component inventory, and fail-before-upload/run blocking.

Acceptance criteria:

- `IDIS_REQUIRE_FULL_LIVE=1` blocks before API run creation if any required component is fallback, disabled, unconfigured, noncanonical, not health-checkable, or not output-visible.
- Private `real_example` harness strict mode blocks before upload when readiness fails.
- Strict output includes machine-readable component inventory for every required component.
- Dotenv values are explicit to strict mode only, process env overrides dotenv, and reports expose only source labels: `process`, `dotenv`, or `missing`.
- Non-strict API and harness behavior remains unchanged.
- The strict report contains no secret values, private paths, filenames, raw text, media content, or private data-room content.

## Slice 57: BYOL Enrichment Credential Wiring

Goal: wire BYOL provider credentials into tenant-scoped credential storage without reading provider env keys directly inside connectors.

Acceptance criteria:

- Companies House, GitHub, FRED, Finnhub, and FMP credentials are loaded through tenant credential repository only.
- Strict readiness distinguishes public providers from BYOL providers.
- BYOL provider errors are fail-closed in strict mode.
- Enrichment provenance is recorded in run provenance and durable state.
- Enrichment evidence is output-visible in the final package sections that rely on it.

## Slice 58: OCR And Media Ingestion Wiring

Goal: make OCR and MP4/STT ingestion production-wired rather than merely present in parser code.

Acceptance criteria:

- OCR-required PDFs/images are processed through configured OCR runtime or strict readiness blocks.
- MP4 media is transcribed through configured ffmpeg/ffprobe/model runtime or strict readiness blocks.
- OCR text and media timecode spans are persisted durably.
- Run provenance records parser mode, runtime, and source evidence IDs.
- Final package can cite OCR spans and media transcript timecodes.

## Slice 59: Durable Product Export Bundle

Goal: persist product-grade diligence exports through product paths.

Acceptance criteria:

- FULL run writes export metadata to Postgres and files to configured object storage.
- Exported bundle includes executive summary, commercial diligence, financial diligence, risk register, IC memo, truth dashboard, QA brief, evidence index, and run summary JSON.
- API can retrieve bundle metadata and download URIs.
- Strict readiness blocks when product export storage or export path is not configured.
- Final package output does not depend on local-only report directories or hidden private scripts.

## Slice 60: Calculation Persistence And CalcSanad

Goal: make financial calculations and CalcSanad reproducibility first-class product evidence.

Acceptance criteria:

- CALC step persists deterministic calculations and CalcSanads for eligible financial claims.
- Reproducibility hashes are stored and verifiable.
- Strict readiness reports calculation and CalcSanad visibility.
- Run provenance records calculation IDs and blocked calculation candidates.
- Final financial diligence output includes calculation tables, assumptions, source claims, and reproducibility hashes.

## Slice 61: Neo4j Projection And Retrieval

Goal: wire graph projection and retrieval into FULL run evidence flow.

Acceptance criteria:

- Claims, Sanads, calculations, and relevant evidence are projected to Neo4j when configured.
- Neo4j health is checked in strict readiness.
- Graph retrieval is consumed by analysis/debate/scoring where relevant.
- Graph provenance is recorded in run provenance.
- Final package includes graph-derived findings with source-backed provenance.

## Slice 62: RAG And Vector Indexing/Retrieval

Goal: implement real vector indexing and retrieval for evidence-backed reasoning.

Acceptance criteria:

- Parsed spans, OCR text, media transcripts, enrichment records, calculation outputs, and graph summaries are indexed.
- Embedding provider health is strict-checked; no fake embeddings in strict mode.
- Retrieval results include evidence IDs, scores, and source references.
- Analysis/debate/scoring use retrieved context with provenance.
- Final package includes retrieved evidence references and rationale.

## Slice 63: Layer 2 IC Challenge

Goal: implement a distinct live IC challenge layer, not just a readiness package.

Acceptance criteria:

- Layer 2 uses live role runners in strict mode.
- Layer 2 consumes Layer 1 debate, calculations, enrichment, graph, and RAG context.
- Dissent, challenge responses, arbiter outcomes, and unresolved risks are persisted.
- Strict readiness distinguishes Layer 2 readiness package from real Layer 2 IC challenge execution.
- Final package includes Layer 2 findings and open questions.

## Slice 64: UI/API Final Package Access

Goal: expose final package state, strict readiness, provenance, and downloads through product UI/API.

Acceptance criteria:

- API exposes strict readiness report, component inventory, run provenance, step blockers, and export URIs.
- UI displays readiness blockers, component modes, package files, and download actions.
- UI/API responses remain secret-safe and private-data-safe.
- Strict readiness blocks if final package access is claimed but not exposed.
- Final package can be reviewed without inspecting local private report files.

## Slice 65: Strict Real Example Live VC Package Run

Goal: run the full strict live `real_example` diligence workflow and inspect the VC package quality.

Acceptance criteria:

- `IDIS_REQUIRE_FULL_LIVE=1` and strict readiness `may_proceed=true` before upload/run.
- No component is skipped, mocked, deterministic, or silently deferred.
- All required external APIs, models, OCR/media runtime, graph, RAG, storage, and database paths are live.
- Final package is persisted through product paths and downloadable through API/UI.
- Manual inspection confirms the package is investor-readable, evidence-backed, financially useful, debate-visible, and honest about residual gaps.
