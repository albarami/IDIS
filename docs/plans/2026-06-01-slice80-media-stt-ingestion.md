# Slice80 — Media/STT Ingestion — Implementation Plan

> **For Claude / agentic workers:** REQUIRED SUB-SKILL: `superpowers:executing-plans` (or `subagent-driven-development`) task-by-task. Per task: `test-driven-development` (RED → verify red → minimal GREEN → verify), `verification-before-completion` before any status claim, `using-git-worktrees` already done, `finishing-a-development-branch` before commit/PR. **Reuse before create. STOP for approval after each task.** Confirm the §5 decisions (especially **R-A scope**) BEFORE Task 1. **Status: implemented NARROW across Tasks 1–6 — see §0 As-Built status.** §§1–10 are retained as the original planning record.

**Goal:** Enable MP4/media evidence to produce **durable transcript/timecode spans**, with **strict health checks for ffmpeg/ffprobe + faster-whisper model provisioning**, and honest, reason-coded **safe blockers** when media transcription is unavailable — so no `real_example` media file is silently deferred.

**Architecture:** The media/STT subsystem already exists end-to-end (slices 37–41) and is wired into the canonical parse path: `parse_media` + process-isolated `FasterWhisperMediaAdapter` (ffmpeg/ffprobe + faster-whisper, duration/segment/size bounds, timeout) → `MediaSegmentText` → `TIMECODE` spans `{start_ms,end_ms,source:"media_transcript"}`; `parse_bytes` routes `.mp4`/media via `is_media_source`; env-driven `build_default_media_config` (off by default) wired into `build_default_ingestion_service`; ingestion persists `TIMECODE` spans; no-network-by-default model provisioning (`media_model_bootstrap`, `probe_faster_whisper_model`). Slice80 (a) adds a **strict media health module** (`media_health.py`) mirroring `ocr_health.py` and wires it into strict-readiness + provisioning-truth so enabling media is *verified*, not assumed; (b) **proves acceptance** with a generated MP4 fixture → transcript/timecode spans (deterministic, mocked adapter) and a **PARSE_SUPPORTED** safe-aggregate showing media transcribed (enabled) or explicitly blocked with safe reasons (disabled/unhealthy); (c) **confirms durable** media span persistence. **No** DB migration (parser `MEDIA`→`DocumentType.VIDEO`, already allowed), **no** OpenAPI/schema, **no** media-to-claims/extraction chunker (BROAD/future), **no** new provider/network.

**Tech Stack:** Python 3.11+, Pydantic v2, FastAPI, pytest, ruff (CI-parity ≥0.15), mypy. Media deps (already integrated, skip-guarded in tests): `faster-whisper` (1.2.1, lazy in worker); external binaries `ffmpeg`/`ffprobe`.

**Base:** branch `slice80-media-stt-ingestion` @ `49aecbcd94bb0d0f322012cb99df7714eda0262a` (= `origin/main`, Slice79 merged), worktree `C:/Projects/IDIS/IDIS-slice80` (clean; baseline ruff + mypy green; `idis.__file__` pinned to this worktree's `src`).

---

## 0. As-built status (Slice80 implemented NARROW — Tasks 1–6, pending commit/PR)

This section reconciles the plan to what was built. §§1–10 below are the original planning record.

- **R-A NARROW — LOCKED & implemented.** Strict media health + durable TIMECODE spans + acceptance proof + honest blockers. Media is **not** wired to extraction/claims (no `TimecodeChunker`, no media-to-claims, no preflight/capability change).
- **R-D NONE — LOCKED.** No media confidence/diagnostics added (master-plan Slice80 scope omits confidence); existing safe `media_segment_count` / `media_transcription_performed` metadata only.
- **`src/idis/services/media_health.py` — NEW standalone strict health** mirroring `ocr_health.py`: `MediaHealthStatus` (HEALTHY/DISABLED/MISSING_DEPENDENCIES/FAILED); `MediaHealthCheck` (`extra="forbid"`; `status/enabled/missing_dependencies/error`); injectable probes (binary resolver, module probe, model probe, runtime probe, command runner). Default runtime runs bounded `ffmpeg -version` + `ffprobe -version`; only safe fixed dep identifiers (`ffmpeg`/`ffprobe`/`faster_whisper`/`media_model`); sanitized+truncated error; never loads faster-whisper or a model. (Task 2 — `tests/test_media_health.py`.)
- **Strict readiness + provisioning consume `media_health`, fail-closed.** `build_strict_full_live_readiness_report` and `build_strict_provisioning_truth_report` accept an injectable `media_health_checker`; `_mp4_stt` + the MP4/STT inventory item are health-status driven (the binary-only `_media_runtime_ready`/`_media_model_probe_ready`/`_missing_media_env`/`_media_health_status` chain was removed); provisioning adds a `media_local_health` opt-in probe + `_static_media_health`. Media-required + not-HEALTHY → `MISSING_INFRASTRUCTURE` (fail-closed); `MediaHealthCheck.error` is never echoed (only `status.value` + safe identifiers); unrelated corpus never blocked. (Task 3 — `tests/test_slice80_media_strict_wiring.py`.)
- **Durable TIMECODE span persistence — confirmed.** Ingestion persists `TIMECODE` spans via `repo.create_document_span` (`{start_ms,end_ms,source:"media_transcript"}`), `doc_type=VIDEO`, `parser_mode="media_stt"`; transcript text lives only in span `text_excerpt` (never metadata/result/audit); multi-segment locators survive with deterministic ordering and stable `content_hash` provenance (`span_id` is `uuid4`). (Task 4 — `tests/test_slice80_media_span_persistence.py`.)
- **Acceptance — proven on PARSE_SUPPORTED safe aggregates.** Generated MP4 fixture → TIMECODE spans; PARSE_SUPPORTED: media-disabled → `conversion_required`, enabled+unavailable → `media_transcription_unavailable`, enabled+injected-transcription → media-blocked count → 0 (CI-only, ffmpeg/ffprobe skip-guarded). `INVENTORY_ONLY` is used **only** as a guard that it does not classify media — it is NOT the reason-count path. Safe-aggregate leak guards (no filename/path/model/secret/root). (Task 5 — `tests/test_slice80_media_acceptance.py`.)
- **NARROW non-eligibility (durable but not extraction-eligible) — confirmed & documented.** A successful `doc_type="MEDIA"` parse triages (no filename) via `capability_for_document(detected_format="MEDIA")` → not a supported format/extension → `UNKNOWN`/`unknown_format` (requires_ocr/conversion False). Media transcripts persist durably but are honestly non-eligible downstream — never silently dropped.
- **No** DB migration (`MEDIA`→`VIDEO`; `VIDEO` is already an allowed `doc_type` predating Slice80 — present in the `valid_document_doc_type` CHECK; Postgres-row proof remains a CI follow-up), **no** OpenAPI/schema, **no** `TimecodeChunker`/media-to-claims, **no** provider/network, **no** Slice81.
- **Config docs:** `.env.example` media/STT vars documented and **off by default** (`IDIS_MEDIA_ADAPTER` commented). Names match the code read by `build_default_media_config`: `IDIS_MEDIA_ADAPTER`, `IDIS_MEDIA_STT_MODEL_PATH`, `IDIS_MEDIA_STT_MODEL_NAME`, `IDIS_MEDIA_STT_ALLOW_DOWNLOAD`, `IDIS_MEDIA_TIMEOUT_SECONDS`, `IDIS_MEDIA_LANGUAGE`, `IDIS_MEDIA_COMPUTE_TYPE`, `IDIS_MEDIA_MAX_DURATION_SECONDS` (note: the runtime vars are `IDIS_MEDIA_*`, **not** `IDIS_MEDIA_STT_*`, except the four model vars).

---

## 1. Master Plan text (verbatim)
> **Slice 80: Media/STT Ingestion**
> **Goal:** Enable MP4/media evidence to produce transcript/timecode spans or strict blockers.
> **Scope:** ffmpeg/ffprobe checks · faster-whisper or approved STT model provisioning · Duration and resource limits · Timecode span persistence.
> **Acceptance:** Generated media fixture produces transcript/timecode spans · Private `real_example` media files are transcribed or explicitly blocked with safe reasons.

---

## 2. Discovery — the media/STT subsystem is largely built (verified)

Four parallel read-only Explore agents + direct verification. Citations are file:line in this worktree.

### 2.1 Already built and working (REUSE as-is)
- **Media parser:** `src/idis/parsers/media.py` — `MediaConfig(enabled,adapter,timeout_seconds)`, `FasterWhisperMediaConfig` (model_path/name, allow_download, language, compute_type, `max_duration_seconds=600`, ffmpeg/ffprobe binaries), `FasterWhisperMediaAdapter` (process-isolated worker; ffprobe duration probe; faster-whisper lazy-imported in worker; timeout + psutil kill), `MediaSegmentText(start_ms,end_ms,text)`, `parse_media` (:160-224), `_parse_media_segments` (:226-289) → `SpanDraft(span_type="TIMECODE", locator={"start_ms","end_ms","source":"media_transcript"})`. Bounds: `MAX_MEDIA_SEGMENTS=500`, `MAX_MEDIA_SEGMENT_TEXT_CHARS≈20KB`, size limit, duration limit. Error codes: `MEDIA_TRANSCRIPTION_UNAVAILABLE`/`_TIMEOUT`/`_FAILED`/`MEDIA_DURATION_EXCEEDED`/`MEDIA_NO_TEXT_EXTRACTED` (base.py:33-37).
- **Span type:** `TIMECODE` exists in `parsers/base.py` `SpanDraft` Literal and `models/document_span.py` `SpanType`; `span_generator._map_span_type` maps `"TIMECODE"→SpanType.TIMECODE`.
- **Registry dispatch:** `parsers/registry.py` — `MEDIA_EXTENSIONS={.mp4}`, `MEDIA_MIME_TYPES={video/mp4,application/mp4}`, `is_media_source` (:204-210); `parse_bytes` routes media → `parse_media(media_config=...)` (:168-169).
- **Provisioning:** `tools/media_model_bootstrap.py` + `scripts/bootstrap_faster_whisper_model.py` + `probe_faster_whisper_model` (media.py): **no-network by default**, CI blocks downloads (`_running_in_ci`), local-path preferred (`model.bin`+`config.json`), path-free results. Status enum `LOCAL_MODEL_READY`/`DOWNLOAD_ALLOWED`/`MODEL_UNAVAILABLE`.
- **Env wiring:** `services/ingestion/defaults.py` `build_default_media_config()` reads `IDIS_MEDIA_ADAPTER` (off unless set), `IDIS_MEDIA_STT_MODEL_PATH`/`_MODEL_NAME`/`_ALLOW_DOWNLOAD`/`_TIMEOUT_SECONDS`/`_LANGUAGE`/`_COMPUTE_TYPE`/`_MAX_DURATION_SECONDS`; wired into `build_default_ingestion_service` (:58). `IngestionService` threads `media_config` to `parse_bytes`.
- **Ingestion persistence:** spans built gated on `parse_result.success and parse_result.spans` (service.py:888), `_persist_spans`→`repo.create_document_span` (:1291-1315). `_map_doc_type("MEDIA")→DocumentType.VIDEO` (:1111). `parser_mode="media_stt"`, `parser_source_type="media_stt"` (:1150-1177); metadata `media_transcription_performed`, `media_segment_count`. Full roundtrip proven safe in `tests/test_slice58_ocr_media_ingestion.py:258-309` (TIMECODE span, `source:"media_transcript"`, no transcript/path leak in metadata).
- **Gate/harness:** `real_example_gate.py` `MEDIA_EXTENSIONS`, `_should_parse_media`, `_media_adapter_attemptable`, `media_policy_key`, media-no-read file key; `real_example_gate_runtime.py` media branch builds `FasterWhisperMediaAdapter` (:211-233) or uses injected `parse_attempt_fn`; `real_example_gate_ledger.py` `MEDIA_POLICY_SENSITIVE_REASON_CODES` + resume; `real_example_run_harness.py` `.mp4` NOT in `PUBLIC_UPLOAD_EXTENSIONS` → always deferred (`media_transcription_unavailable`/`media_public_upload_unsupported`).
- **Upload route:** `api/routes/documents.py` `_reject_unsupported_upload_format` admits `.mp4` via `is_media_source` (media uploads accepted).
- **Tests:** `test_media_parser.py` (injected adapters + mocked workers; MP4 magic-byte stub `b"\x00\x00\x00\x18ftypmp42"`), `test_media_runtime_provisioning.py` (bootstrap/CI-safety), `test_slice58_ocr_media_ingestion.py` (`RecordingMediaAdapter` roundtrip). Real ffmpeg/faster-whisper skip-guarded.

### 2.2 The gap: strict media health is BINARY-ONLY/inline (Slice80's primary target)
`strict_full_live.py` media readiness is **inline** and not a dedicated injectable module (unlike Slice79's `ocr_health.py`):
- `_mp4_stt()` component, `_media_runtime_ready()` (ffmpeg+ffprobe present + `_media_model_probe_ready`), `_media_model_probe_ready()` (calls `probe_faster_whisper_model` directly), `_missing_media_env()`.
- **No `media_health.py`**, **no injectable `media_health_checker`** param on `build_strict_full_live_readiness_report`.
- `strict_provisioning_truth.py`: **no media local probe** (OCR/pgvector/neo4j have one; media has none → static "not_run"/inventory health only).

### 2.3 NARROW asymmetry (same as Slice79 images) + verified non-defects
- **doc_type:** parser emits `"MEDIA"` → `_map_doc_type`→`DocumentType.VIDEO` (`VIDEO` is an allowed `doc_type` predating Slice80 — present in both the upgrade and downgrade of the `valid_document_doc_type` CHECK; migration `0016` added `IMAGE`). **Not a DB constraint violation** (verified). Actual Postgres-row proof = CI follow-up.
- **Successful-media triage:** ingestion calls `triage_document(parse_result=…)` **without a filename**; for a successful `doc_type="MEDIA"` parse this falls through to `capability_for_document(detected_format="MEDIA")` → not in `_SUPPORTED_CAPABILITIES`, no extension → **`UNKNOWN`** (non-eligible). So media transcripts are **durable but not extraction-eligible** — honestly blocked downstream, never silently dropped (NARROW). `.mp4` pre-parse capability is `CONVERSION_REQUIRED`/`conversion_required`.
- **No MEDIA chunker:** `ChunkingService` = {PDF,XLSX,DOCX,PPTX,HTML,TEXT}; media (TIMECODE) spans would hit `UnsupportedDocumentTypeError` IF routed to chunking — but under NARROW media is non-eligible, so it never reaches chunking. A `TimecodeChunker` + media-to-claims is **BROAD/future**.

### 2.4 Characterization questions to LOCK empirically in Task 1 (not assumed)
- **Q1:** generated MP4 fixture + (mocked) adapter → `parse_media` produces `TIMECODE` spans with `{start_ms,end_ms,source:"media_transcript"}`.
- **Q2:** media ingestion persists `TIMECODE` spans durably + `doc_type=VIDEO` + `parser_mode="media_stt"`, gated on parse success (recording-repo seam; Postgres-row proof = CI follow-up). Confirm no doc_type constraint violation path.
- **Q3:** media disabled → gate `deferred`/`conversion_required`; media enabled + adapter unavailable → `media_required`/`media_transcription_unavailable` (deterministic, no real faster-whisper).
- **Q4:** strict media readiness is currently binary-only (confirm before replacing).

---

## 3. Current-state matrix (verified)
| Class | parser (media on) | spans? | triage after success | run-pipeline | acceptance-relevant |
|---|---|---|---|---|---|
| `.mp4` (transcribed) | `parse_media`→faster-whisper | ✅ `TIMECODE {start_ms,end_ms,source:media_transcript}` | re-derived `UNKNOWN` (non-eligible) | durable spans; **blocked** downstream (no MEDIA chunker) | spans ✅; gate count→parsed when enabled |
| `.mp4` (media disabled) | not parsed | ❌ | `CONVERSION_REQUIRED`/`conversion_required` | visible blocker | "blocked with safe reason" ✅ |
| `.mp4` (enabled, adapter unavailable) | attempted, blocked pre-subprocess | ❌ | `CONVERSION_REQUIRED` + `media_transcription_unavailable` | visible blocker `media_required` | "blocked with safe reason" ✅ |

---

## 4. Reuse map (exact files)
**Reuse unchanged (verify only):** `parsers/media.py`, `parsers/registry.py` media dispatch, `ingestion/defaults.py` media env wiring, `ingestion/service.py` persistence + `_map_doc_type`, `span_generator.py` TIMECODE, gate/runtime/ledger/harness media handling, `media_model_bootstrap.py`, existing media tests.
**Touch (production) — NARROW:**
- **new** `src/idis/services/media_health.py` (R-C) — mirror `ocr_health.py`: status enum (HEALTHY/DISABLED/MISSING_DEPENDENCIES/FAILED), safe Pydantic result (`extra="forbid"`; `enabled`, `missing_dependencies` fixed identifiers, sanitized `error`), injectable probes (binary resolver ffmpeg/ffprobe; faster-whisper module probe; model probe via `probe_faster_whisper_model`; optional runtime probe). Env-honoring, fail-closed.
- `services/runs/strict_full_live.py` — replace inline `_media_runtime_ready`/`_media_model_probe_ready` with `media_health`; add injectable `media_health_checker`; `_mp4_stt` consumes it (fail-closed); media inventory item uses safe health status.
- `services/runs/strict_provisioning_truth.py` — add injectable `media_health_checker` + opt-in media local probe; static media health in base report.
- `.env.example` + strict-readiness tracked vars — ensure media env vars documented (Task 6).
- **BROAD only (out of scope):** `TimecodeChunker`, preflight/capability honoring transcribed media, `DataRoomInventoryReason.MEDIA_REQUIRED`, handoff, DB migration.

---

## 5. Decisions — confirm BEFORE Task 1

### R-A — SCOPE (key decision; mirrors Slice79)
- **NARROW (recommended):** media-runnable-and-honest. Deliver **strict media health** (verify enabling is safe), confirm **durable TIMECODE spans** (parser + ingestion), and **acceptance proof** (generated MP4 fixture → spans; PARSE_SUPPORTED safe aggregate: media-disabled → `conversion_required` blocker / enabled+unavailable → `media_transcription_unavailable` / enabled+available → transcribed). Media transcripts are durable but **remain honestly blocked downstream** (non-eligible — sanctioned by acceptance "or explicitly blocked with safe reasons"). **No** chunker/preflight/capability change.
- **BROAD:** NARROW **plus** wire transcribed media through extraction/claims (new `TimecodeChunker`; capability honors successful media; preflight/task_planner accept; `MEDIA_REQUIRED` inventory reason; handoff). Larger surface; **not required by the acceptance**.
- *Recommendation:* **NARROW** — fully satisfies both acceptance criteria with the smallest truthful surface.

### R-B — Media execution policy (LOCKED proposal)
Media stays **config-gated** (`IDIS_MEDIA_ADAPTER`, off by default), **no-network model download by default** (CI blocks downloads), **fail-closed**: media-required + unhealthy → visible blocker (no silent run/skip). Slice80 does not force media on in production.

### R-C — Strict media health (LOCKED proposal)
New `media_health.py` verifies: `ffmpeg` + `ffprobe` binaries; `faster_whisper` importable; STT model availability via `probe_faster_whisper_model` (local path or download-allowed). Safe result (no model paths/transcript/command-output/env leak; sanitized+truncated error). Wire into `strict_full_live._mp4_stt` (via injectable `media_health_checker`) + a provisioning-truth media local probe. Tests inject fakes — no real ffmpeg/faster-whisper in CI.

### R-D — Diagnostics/confidence (LOCKED proposal: NONE)
**Unlike Slice79 (OCR), the master-plan Slice80 scope does NOT include confidence.** Duration/resource limits and timecode span persistence already exist. So **no new confidence/diagnostics work**; existing safe `media_segment_count`/`media_transcription_performed` metadata suffices. (Confirm you agree.)

### R-E — Acceptance-proof surface (LOCKED proposal)
(1) **Parser fixture test:** generated minimal MP4 fixture + mocked `MediaAdapter`/worker → `parse_media` → assert `TIMECODE` spans with `{start_ms,end_ms,source:"media_transcript"}` (deterministic; real ffmpeg/faster-whisper skip-guarded). (2) **Durable-spans confirm:** recording-repo proves ingestion persists media TIMECODE spans + `doc_type=VIDEO`, text-free metadata (Postgres-row proof = CI follow-up). (3) **PARSE_SUPPORTED safe-aggregate:** media-disabled → `counts_by_reason_code["conversion_required"]≥1` (deferred); media-enabled+adapter-unavailable → `media_transcription_unavailable`; media-enabled + injected `parse_attempt_fn`→`parsed` → media transcribed (count off `media_required`). `INVENTORY_ONLY` is inventory-only — NOT used for media classification. No path/transcript/secret leakage.

### R-F — Test determinism (LOCKED proposal)
Mocked `MediaAdapter`/`RecordingMediaAdapter` + injected worker/`parse_attempt_fn` for span/flow/acceptance tests; real ffmpeg/faster-whisper tests `skip`-guarded. `media_health` unit tests use injectable probes (no real binaries/model).

### R-G — No DB/OpenAPI/chunker (LOCKED proposal)
No migration (media→`VIDEO` already allowed; verify in Task 1; Postgres-row proof = CI follow-up), no OpenAPI/schema change, **no `TimecodeChunker`** / media-to-claims (BROAD), no new provider/network.

---

## 6. Open questions for you — RESOLVED
1. **R-A: NARROW or BROAD?** → **NARROW** (approved; implemented).
2. **R-D:** no media confidence/diagnostics → **confirmed NONE** (approved).
3. Acceptance-proof surface → **PARSE_SUPPORTED** safe aggregate (+ recording-repo durability) → **approved & implemented** (Tasks 4–5).

---

## 7. Task breakdown (for R-A = NARROW; TDD, STOP after each)
- **Task 1 — Characterization (no prod change):** Q1 parser MP4→TIMECODE spans; Q2 ingestion durable media spans + `doc_type=VIDEO` + `parser_mode=media_stt` (recording-repo); Q3 gate media-disabled→`conversion_required` / enabled+unavailable→`media_transcription_unavailable`; Q4 strict media health is binary-only. Lock baseline; convert any surprise into a defect note.
- **Task 2 — `media_health.py` (R-C):** new strict health module (mirror `ocr_health.py`); unit tests via injectable probes (healthy / missing ffmpeg / missing ffprobe / missing faster_whisper / model-unavailable / disabled / sanitized-error).
- **Task 3 — Wire media_health into strict readiness + provisioning:** injectable `media_health_checker`; `_mp4_stt`/`_media_runtime_ready` use it; provisioning media local probe; fail-closed; tests + no-drift on strict consumers (slices 56–77).
- **Task 4 — Durable-spans confirm (Q2):** recording-repo proves media TIMECODE spans persist + safe metadata; no real DB. (If already durable: verification-only, like Slice79 Task 5.)
- **Task 5 — Acceptance proof (R-E):** generated MP4 fixture → TIMECODE spans; PARSE_SUPPORTED safe aggregate (disabled/unavailable/transcribed); leakage guards.
- **Task 6 — Config/docs + verification gate + review:** `.env.example` media vars; plan reconciliation; full CI-parity gate; independent code review.

---

## 8. Verification gate (CI parity — from worktree root, `PYTHONPATH=src`)
`python -c "import idis; print(idis.__file__)"` (must resolve to this worktree) · `ruff format --check .` · `ruff check .` · clear `.mypy_cache` then `mypy src/idis` · `python scripts/forbidden_scan.py --repo-root .` · `git diff --check` · targeted `pytest` (mocked media; skip-guarded real-binary). DB-backed `*_postgres.py` only run in CI `postgres-integration`.

## 9. Out of scope (NARROW)
`TimecodeChunker` / media-to-claims / extraction wiring; capability/preflight honoring transcribed media; `DataRoomInventoryReason.MEDIA_REQUIRED`; DB migration / RLS / OpenAPI / schema; readiness/VC-ready; media confidence diagnostics; real provider/network calls; forcing media on in production; Slice81+.

## 10. Risks
- **Hidden assumption (Slice78/79 lesson):** verify Q1–Q4 empirically in Task 1 before designing — especially the `MEDIA→VIDEO` doc_type persistence path and durable TIMECODE spans.
- **Leakage:** transcript text + model paths are sensitive — all health/diagnostic/aggregate/audit outputs must stay path/transcript-free (reuse `ocr_health` sanitizer + `_safe_summary` patterns).
- **Determinism/CI:** never require real ffmpeg/faster-whisper/model in CI — mocked adapters/workers + injectable health probes; skip-guard real-binary tests; no-network model policy.
