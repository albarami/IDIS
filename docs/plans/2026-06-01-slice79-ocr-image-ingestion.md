# Slice79 — OCR/Image Ingestion — Implementation Plan

> **For Claude / agentic workers:** REQUIRED SUB-SKILL: use `superpowers:executing-plans` (or `superpowers:subagent-driven-development`) to implement task-by-task. Per task: `superpowers:test-driven-development` (RED → verify red → minimal GREEN → verify → refactor), `superpowers:verification-before-completion` before any status claim, `superpowers:using-git-worktrees` already done, `superpowers:finishing-a-development-branch` before commit/PR. **Reuse before create.** **STOP for approval after each task.**
>
> **AS-BUILT (Tasks 1–6 delivered):** R-A = **NARROW** and R-D = **include-confidence** are locked. **§0** below is the authoritative as-built reconciliation and **governs** wherever §1–§10 (planning-time record) differ.

**Goal:** Enable scanned PDFs and images to produce **durable OCR spans**, with **strict health checks for OCR binaries/config** and honest, reason-coded blockers when OCR is unavailable — so no `real_example` OCR-required file is silently deferred.

**Architecture:** The OCR subsystem already exists and is wired into the canonical parse path (Tesseract adapter, `parse_pdf` scanned-detection → OCR, `parse_image` → OCR, `parse_bytes` passes `ocr_config`, env-driven `build_default_ocr_config`). Slice79 (a) adds a **strict OCR health-check module** (`ocr_health.py`) following the existing `rag/*_health.py` pattern and wires it into strict-readiness + provisioning-truth so enabling OCR is *verified*, not assumed; (b) enriches OCR **diagnostics/confidence/locators**; (c) proves the acceptance with **generated scanned-PDF/image fixtures → spans** and a **PARSE_SUPPORTED safe-aggregate** showing OCR-required counts go to zero (OCR on) or are explicitly blocked with an accepted `ocr_required` reason (OCR off/unhealthy) — note `INVENTORY_ONLY` is inventory-only and does **not** classify OCR (corrected as-built; see **§0**). **No** new DB/migration/RLS/OpenAPI work (spans persist via existing `document_spans`; metadata via existing JSONB). **No** media/STT (Slice80), **no** conversion engine.

**Tech Stack:** Python 3.11+, Pydantic v2, FastAPI, pytest, ruff (CI-parity ≥0.15), mypy. OCR deps (already integrated, skip-guarded in tests): `pytesseract`, `pdf2image`, `Pillow`; external binaries `tesseract`, `poppler` (`pdfinfo`).

**Base:** branch `slice79-ocr-image-ingestion` @ `b82243cd1a543ad79d4b8600bd8b9e11e4498401` (= `origin/main`, Slice78 merged), worktree `C:/Projects/IDIS/IDIS-slice79` (clean; baseline ruff + mypy green inherited from CI-green main).

---

## 0. AS-BUILT reconciliation — NARROW, Tasks 1–6 delivered (authoritative)

§1–§10 are the **planning-time** record (proposed decisions, open questions, task plan). This
section records what was **actually built** under the locked **R-A = NARROW** / **R-D =
include-confidence** decisions, and **governs where §1–§10 differ**. Task 1 characterization +
Task 6 acceptance proved most of the slice was already correct; production changes were limited
to the items below.

**Task 1 characterization answers (Q1–Q3):** Q1 image OCR spans **are** durably persisted
(persistence is gated on parse success, not triage); Q2 the canonical ingestion path
(`build_default_ingestion_service`, used by `api/main.py`) wires OCR from env when
`IDIS_OCR_ENABLED=1`; Q3 the PDF chunker correctly groups `source:"ocr"` `PAGE_TEXT` spans.

**Delivered (production):**
- **`src/idis/services/ocr_health.py` (new, Task 2):** strict, **env-honoring**, **fail-closed**
  health check — verifies `tesseract` + `pdfinfo` (poppler) binaries, Python deps
  (`pytesseract`/`pdf2image`/`pillow`), tessdata/language (honors the supplied `env`'s
  `TESSDATA_PREFIX`), and an optional bounded runtime version probe via an **injectable
  `command_runner`**. Safe Pydantic result (`extra="forbid"`): `status`
  (HEALTHY/DISABLED/MISSING_DEPENDENCIES/FAILED), `enabled`, `missing_dependencies` (fixed safe
  identifiers), sanitized+truncated `error` (only the exception class name reaches it). OCR is
  off-by-default → `DISABLED` is an expected non-error. No raw paths/env values/OCR text/command
  output are surfaced.
- **Strict readiness + provisioning consume `ocr_health` (Task 3):** via an **injectable
  `ocr_health_checker`**. `_ocr_runtime_ready` now means `status is HEALTHY` (enabled OCR requires
  the **full** health check, replacing the binary-only check). `_ocr()` is fail-closed:
  OCR-required + not-healthy → `MISSING_INFRASTRUCTURE` with safe `missing_dependencies`/`status`
  (never `error` content); DISABLED → `IDIS_OCR_ENABLED=1` blocker; not-required →
  `CODE_EXISTS_BUT_NOT_WIRED`/may-proceed. Provisioning adds an opt-in OCR local probe (DISABLED →
  not-attempted/`ocr_disabled`; else pass/fail); the base report uses a static OCR health (no live
  probe). Tests inject fakes — **no real binary dependency in CI**.
- **Confidence diagnostics (Task 4): additive, safe metadata only.** `OcrPageText` gained
  `confidence: float|None = None` (backward compatible). `normalize_ocr_confidence` (Tesseract
  0–100 → mean 0–1; `-1`/invalid ignored; None if none) + `overall_mean_confidence`.
  `ParseResult.metadata` adds `ocr_mean_confidence` (+ `ocr_confidence_by_page` for PDF) — numbers
  only, text-free. **`image_to_string` still owns text extraction**; `image_to_data` is used
  **only** inside the process-isolated worker, best-effort, for confidence — failures degrade to
  `None` and never change text/`content_hash`. `SpanDraft` schema and locators are unchanged
  (`source:"ocr"` / `"ocr_image"`). (No engine/dpi/lang/elapsed diagnostics were added — not
  available at the parser layer; the R-D proposal is narrowed accordingly.)
- **Durable spans confirmed (Task 5) — no repair needed.** Ingestion builds + persists OCR
  `PAGE_TEXT` spans for scanned-PDF and image via `_persist_spans` → `repo.create_document_span`
  (gated on parse success, **not** triage); persisted `Document.metadata` carries the safe OCR
  fields (via the `{**parse_result.metadata}` spread) and is text-free. Actual Postgres
  `document_spans` **row** proof is a **CI/postgres-integration follow-up**.

**Acceptance (Task 6) — corrected proof path.** `INVENTORY_ONLY` is **truly inventory-only**
(`parser_outcome="not_attempted"`, `reason_code="inventory_only"`) and does **not** parse or
classify OCR-required reasons — so any §1/§5/§7 mention of proving acceptance via INVENTORY_ONLY
counts is **superseded**. Acceptance is proven on the **`PARSE_SUPPORTED`** safe aggregate:
OCR-disabled → OCR-required files explicitly **blocked** with the accepted `ocr_required` reason
(status `deferred`); OCR-enabled+healthy → `ocr_required` count **goes to zero** (deterministic
injected `parse_attempt_fn`, no real tesseract). Generated scanned-PDF/image fixtures → OCR
`PAGE_TEXT` spans with page/line locators. Safe aggregates expose counts only.

**Carried-forward follow-ups (NOT blockers):**
- **Image triage labeling through ingestion:** an OCR'd image is persisted with
  `parser_support_status=UNKNOWN` because ingestion calls `triage_document(parse_result=…)`
  **without a filename**, so the `.png` → `SCANNED_OR_IMAGE_ONLY` mapping isn't re-derived. The
  image is still correctly **non-eligible / downstream-blocked** and its spans are still durable —
  consistent with NARROW. Wiring OCR'd images through extraction/claims and tightening this triage
  label is **BROAD/future** (deferred).
- **Postgres `document_spans` row proof** for OCR spans (CI/postgres-integration).

**Confirmed out of scope (unchanged):** no DB migration / RLS / JSON-schema / OpenAPI; no
provider/media/STT (Slice80); no conversion engine; no IMAGE chunker; no image-to-claims; no
readiness/VC-ready claim; OCR is not forced on in production.

**As-built file set:** **new** `src/idis/services/ocr_health.py`; **modified**
`src/idis/parsers/ocr.py`, `parsers/pdf.py`, `parsers/image.py`,
`services/runs/strict_full_live.py`, `services/runs/strict_provisioning_truth.py`; **one updated
test** `tests/test_slice58_ocr_media_ingestion.py` (OCR readiness now uses the injectable healthy
checker, reflecting the full-health requirement); **new test files** for Tasks 1–6
(`test_slice79_ocr_characterization.py`, `test_ocr_health.py`, `test_slice79_ocr_strict_wiring.py`,
`test_slice79_ocr_confidence.py`, `test_slice79_ocr_span_persistence.py`,
`test_slice79_ocr_acceptance.py`).

---

## 1. Master Plan text (verbatim)
> **Slice 79: OCR/Image Ingestion**
> **Goal:** Enable scanned PDFs and images to produce durable OCR spans.
> **Scope:** Tesseract/poppler/image parser config · OCR diagnostics, confidence, page/image locators · Strict health checks for OCR binaries and config.
> **Acceptance:** Generated scanned PDF/image fixtures produce OCR spans · Private `real_example` OCR-required file counts go to zero or are explicitly blocked with accepted reason.

---

## 2. Discovery — the OCR subsystem is largely built (verified)

Four parallel read-only Explore agents + direct code verification. **Citations are file:line in this worktree.**

### 2.1 Already built and working (REUSE as-is)
- **OCR adapter:** `src/idis/parsers/ocr.py` — `OcrConfig(enabled, adapter, max_pages, timeout_seconds)` (:58-66), `OcrAdapter` protocol (:37-55), `TesseractOcrAdapter` (process-isolated via multiprocessing; `pdf2image.convert_from_bytes` + `pytesseract`; timeout + `MAX_OCR_IMAGE_PIXELS=20_000_000` bounds) (:72-139). Errors: `OcrError`/`OcrTimeoutError`/`OcrUnavailableError`.
- **PDF OCR:** `src/idis/parsers/pdf.py` — scanned detection (zero native text → OCR when `ocr_config.enabled`) (:224-230); `_parse_pdf_with_ocr` (:289-327); spans `{"page":p,"line":n,"source":"ocr"}`, `private_diagnostics["pdf_diagnostic_reason"]="parsed_ocr"`.
- **Image OCR:** `src/idis/parsers/image.py` — `parse_image(data, limits, ocr_config)`; on success returns `doc_type="IMAGE"`, spans `{"page":1,"line":n,"source":"ocr_image"}`, metadata `{ocr_performed:True, ocr_image_count:1}` (:90-100). Returns `OCR_UNAVAILABLE` when not enabled (:33-34).
- **Registry dispatch:** `src/idis/parsers/registry.py` — `parse_bytes(..., ocr_config=...)` routes PDF (:154) and image sources (:166) with `ocr_config` passed through.
- **Env wiring:** `src/idis/services/ingestion/defaults.py` `build_default_ocr_config()` (:62-75) reads `IDIS_OCR_ENABLED`/`IDIS_OCR_ADAPTER`/`IDIS_OCR_MAX_PAGES`/`IDIS_OCR_TIMEOUT_SECONDS`/`IDIS_OCR_DPI`; returns `None` when disabled (so **OCR is OFF by default**). `IngestionService` stores `_ocr_config` and passes it to `parse_bytes` (service.py:708, 1094-1101).
- **Triage reason codes:** `src/idis/services/documents/parser_capabilities.py` maps OCR errors → `ocr_failed`/`ocr_timeout`/`ocr_unavailable`/`ocr_no_text_extracted` (:104-109); scanned-PDF (`NO_TEXT_EXTRACTED`+PDF) → `SCANNED_OR_IMAGE_ONLY`/`OCR_REQUIRED`/`ocr_required` (:221-231); image extensions → same via `_OCR_REQUIRED_EXTENSIONS` (:155-163).
- **Strict readiness (basic):** `src/idis/services/runs/strict_full_live.py` `_ocr_runtime_ready` (:2563-2573, checks `IDIS_OCR_ENABLED` + adapter==tesseract + `tesseract` binary present), `_ocr()` component (:547-590), inventory (:1520-1542). `strict_provisioning_truth.py` maps `"OCR":"ocr"` (:137) but has **no local probe**.
- **Gate/harness:** `real_example_gate_runtime.py` parses images/scanned-PDFs via OCR when `ocr_enabled=True` (:209-256); `real_example_gate.py` `--ocr-enabled` CLI (:311-314), `OCR_IMAGE_EXTENSIONS` (:59), `build_data_room_package_inventory_summary` → `counts_by_reason_code` (:253-282); ledger retry of prior `ocr_required` when policy changes (`real_example_gate_ledger.py`:133-147).
- **Tests + fixtures:** `tests/test_tesseract_ocr_adapter.py` (478 lines — real OCR when deps present + mocked workers; fixtures `_create_image_text_pdf`/`_create_image_text_png` via PIL+reportlab, skip-guarded), `tests/test_pdf_ocr_adapter.py` (376 lines — `RecordingOcrAdapter` mock, `_create_image_only_pdf`), `tests/test_slice58_ocr_media_ingestion.py` (env wiring). Determinism: real tests use DPI=220 and assert stable `content_hash`.

### 2.2 The verified gap: image OCR is NOT honored end-to-end (asymmetry)
After a **successful** OCR (no parse errors), `triage_document` (parser_capabilities.py:200-296) hits no error branch and falls through (:288) to `capability_for_document(detected_format=doc_type)`:
- **Scanned PDF** → `capability_for_document("PDF")` → `PDF` ∈ `_SUPPORTED_CAPABILITIES` → `PARTIALLY_SUPPORTED`/`PARTIAL` → **eligible** (preflight + task_planner accept PARTIAL) → PDF chunker exists → **flows end-to-end** (conditioned on OCR being enabled+healthy in the run path).
- **Image** → `capability_for_document("foo.png")` re-derives from the **extension** (`.png` ∈ `_OCR_REQUIRED_EXTENSIONS`, :155) → **`SCANNED_OR_IMAGE_ONLY`/`OCR_REQUIRED` again** — the successful OCR is *ignored*. Then:
  - preflight `_is_eligible` excludes it (document_preflight.py:221-232) → reason `OCR_REQUIRED` (:242-243),
  - task_planner blocks it (`_SUPPORT_BLOCKERS`/`_TRIAGE_BLOCKERS`, :39/:48),
  - and there is **no IMAGE chunker** in `ChunkingService` ({PDF, XLSX, DOCX, PPTX, HTML, TEXT}) → would raise `UnsupportedDocumentTypeError` (the Slice78 failure mode).

**So:** images produce OCR spans at the parser, but in the **run/extraction pipeline** they are honestly **blocked with reason `ocr_required`** (NOT silently dropped). Wiring images to flow through extraction (claims) is the Slice78-"Option A"-style choice in **R-A** below.

### 2.3 Open characterization questions (to LOCK empirically in Task 1, not assumed)
- **Q1 — Durability:** does `IngestionService` persist `parse_result.spans` to `document_spans` for an OCR'd **image** (triaged `SCANNED_OR_IMAGE_ONLY`), or only for extraction-eligible docs? ("Durable OCR spans" for images depends on this.)
- **Q2 — Run-path OCR:** is `build_default_ocr_config()` actually invoked by the canonical **run/worker** path (not just a hand-built `IngestionService`), i.e., does enabling `IDIS_OCR_ENABLED` make production runs OCR?
- **Q3 — PDF chunker + OCR spans:** does the PDF chunker correctly group `source:"ocr"` page spans (locator has `page`+`line`+`source`)?

---

## 3. Current-state matrix (verified)
| Class | parser (OCR on) | spans? | triage after success | run-pipeline today | acceptance-relevant |
|---|---|---|---|---|---|
| scanned `.pdf` | `parse_pdf`→OCR | ✅ `{page,line,source:ocr}` | `PARTIALLY_SUPPORTED`/`PARTIAL` | **flows** (PDF chunker) | spans ✅; counts→0 when OCR on |
| `.png/.jpg/.jpeg/.tif/.tiff/.bmp` | `parse_image`→OCR | ✅ `{page:1,line,source:ocr_image}` | **re-derived `SCANNED_OR_IMAGE_ONLY`** | **blocked** `ocr_required` (no IMAGE chunker) | spans ✅ (parser); run-claims ❌ |
| OCR off / binary missing | n/a | ❌ | `SCANNED_OR_IMAGE_ONLY`/`ocr_required` | visible blocker `OCR_REQUIRED` | "blocked with accepted reason" ✅ |

Enums already present (`models/document_classification.py`): `DocumentSupportStatus.SCANNED_OR_IMAGE_ONLY`, `DocumentTriageStatus.OCR_REQUIRED`. A new "OCR succeeded" status/reason would only be needed for **R-A = BROAD**.

---

## 4. Reuse map (exact files)
**Reuse unchanged (verify only):** `parsers/ocr.py`, `parsers/pdf.py` OCR path, `parsers/registry.py` dispatch, `ingestion/defaults.py` env wiring, gate/runtime/ledger OCR handling, existing OCR tests/fixtures.
**Touch (production) — depends on locked scope:**
- `src/idis/services/` **new** `ocr_health.py` (R-C) — mirror `rag/pgvector_health.py` (:1-118) / `rag/embedding_health.py` (:1-209): status enum, Pydantic result (`extra="forbid"`), `config_present`, `missing_env_vars`, sanitized `error`, injectable probe.
- `src/idis/services/runs/strict_full_live.py` — replace/extend `_ocr_runtime_ready` (:2563-2573) to call `ocr_health`; `strict_provisioning_truth.py` (:137) — add OCR local probe.
- `src/idis/parsers/ocr.py` + `image.py` + `pdf.py` — OCR confidence/diagnostics (R-D) via `pytesseract.image_to_data` (per-page mean confidence; safe diagnostics; locators already present).
- `.env.example` + strict-readiness tracked vars — add `TESSDATA_PREFIX`, `IDIS_OCR_DPI`, `IDIS_OCR_LANGUAGE` (R-C).
- **R-A = BROAD only:** `parser_capabilities.py` (honor OCR-succeeded image → supported), `models/document_classification.py` (optional new status), `document_preflight.py` `_is_eligible`, `task_planner.py` blockers, **new** image/OCR chunker in `services/extraction/chunking/`, + blast-radius tests.

---

## 5. Decisions — confirm BEFORE Task 1 (only R-A changes behavior materially)

### R-A — SCOPE (the key decision; mirrors Slice78 "Option A")
- **NARROW (recommended):** OCR-runnable-and-honest. Deliver **strict health checks** (so enabling OCR is verified), **durable OCR spans** at parser/ingestion for scanned-PDF + image (confirm/repair persistence per Q1), **diagnostics/confidence**, and **acceptance proof** (fixtures→spans; INVENTORY_ONLY counts→zero with OCR on / blocked-with-reason when off/unhealthy). Scanned-PDFs flow end-to-end via OCR; **raw images remain honestly blocked with `ocr_required`** in the extraction pipeline (sanctioned by acceptance #2 "or explicitly blocked with accepted reason"). **No new enum / chunker / preflight change.**
- **BROAD (Option-A analog):** NARROW **plus** wire OCR-succeeded **images** end-to-end (capability honors successful image OCR → supported; preflight + task_planner accept; **new IMAGE/OCR chunker**; optional `OCR_*COMPLETED` status; full blast-radius tests). Larger surface; **not required by the acceptance**.
- *Recommendation:* **NARROW** — it fully satisfies both acceptance criteria with the smallest truthful surface; escalate to BROAD only if you want image→claims parity now.

### R-B — OCR execution policy (LOCKED proposal)
OCR stays **config-gated** (`IDIS_OCR_ENABLED`, off by default) and **fail-closed**: when evidence requires OCR but health check fails, the run surfaces a **visible blocker** (no silent run, no silent skip). Slice79 does **not** force OCR on in production.

### R-C — Strict health checks (LOCKED proposal)
New `ocr_health.py` verifies: `tesseract` binary (and `--version` responds), `poppler` (`pdfinfo`), Python deps (`pytesseract`/`pdf2image`/`PIL`) importable, and `TESSDATA`/language data resolvable. Safe result model (no path/secret leakage; sanitized error, truncated). Wire into `strict_full_live._ocr()` + `strict_provisioning_truth`. Add `TESSDATA_PREFIX`/`IDIS_OCR_DPI`/`IDIS_OCR_LANGUAGE` to `.env.example` + tracked vars.

### R-D — Diagnostics/confidence (LOCKED proposal)
Add per-page **mean OCR confidence** + safe OCR diagnostics via `pytesseract.image_to_data`; keep existing page/line locators; **no content/path leakage**. *Down-scope option:* locators + diagnostics only (no confidence) if `image_to_data` is deemed too heavy. **AS-BUILT (§0 governs):** delivered `ocr_mean_confidence` (+ `ocr_confidence_by_page` for PDF) only; engine/dpi/lang/elapsed were **not** added (not available at the parser layer). `image_to_string` still owns text extraction; `image_to_data` is best-effort, worker-confined, confidence-only.

### R-E — Acceptance-proof surface (LOCKED proposal)
(1) **Parser fixture tests:** generated scanned-PDF + image fixtures → assert OCR spans (deterministic, mocked adapter for CI; real-binary variant skip-guarded). (2) **PARSE_SUPPORTED safe-aggregate** test (**AS-BUILT — §0 governs**): `counts_by_reason_code["ocr_required"]==0` with OCR enabled+healthy (deterministic injected parse), else explicitly present as a `deferred`/`ocr_required` blocker (OCR off). `INVENTORY_ONLY` is inventory-only and does **not** compute OCR-required counts. No fake client; no path/content leakage.

### R-F — Test determinism (LOCKED proposal)
Deterministic CI uses the **mocked `OcrAdapter`/`RecordingOcrAdapter`** (existing convention) for span/health/flow tests; **real tesseract/poppler** tests use `pytest.importorskip` + binary skip-guards (existing pattern). Health-check unit tests use the **injectable probe** (no real binaries needed).

### R-G — No DB/OpenAPI (LOCKED proposal)
No migration/RLS/JSON-schema/OpenAPI change. OCR spans persist via existing `document_spans`; OCR metadata/diagnostics via existing JSONB `Document.metadata`. (Verify in Task 1.)

---

## 6. Open questions for you
**RESOLVED (see §0):** R-A = **NARROW**; R-D = **include confidence**; acceptance proven on the
**PARSE_SUPPORTED** safe aggregate (INVENTORY_ONLY is inventory-only and does not compute
OCR-required counts).
1. ~~**R-A: NARROW or BROAD?**~~ → NARROW.
2. ~~**R-D:** include confidence or locators+diagnostics only?~~ → include confidence.
3. ~~Acceptance-proof surface (INVENTORY_ONLY vs full-run)?~~ → PARSE_SUPPORTED safe aggregate.

---

## 7. Task breakdown (for R-A = NARROW; BROAD adds Tasks N1–N4)
Each task = TDD (RED → verify red → minimal GREEN → verify), then **STOP for approval**.

- **Task 1 — Characterization (RED-as-discovery, no prod change):** tests pinning current truth — scanned-PDF OCR → spans + eligible; image OCR → spans at parser but blocked `ocr_required` downstream; **Q1** span persistence for image; **Q2** run-path OCR enablement; **Q3** PDF chunker handles `source:ocr` spans. Lock the baseline; convert any surprise into a defect note.
- **Task 2 — `ocr_health.py` (R-C):** new strict health module (mirror `pgvector_health.py`); unit tests via injectable probe (healthy / missing-binary / missing-deps / missing-tessdata / sanitized-error).
- **Task 3 — Wire health into strict readiness + provisioning-truth:** `_ocr_runtime_ready`/`_ocr()` use `ocr_health`; provisioning-truth OCR probe; fail-closed visible blocker when required-but-unhealthy. Tests.
- **Task 4 — OCR diagnostics/confidence (R-D):** extend adapter/spans/metadata; safe (no leakage) tests; determinism preserved.
- **Task 5 — Durable-spans confirm/repair (Q1):** ensure scanned-PDF + image OCR spans persist durably at ingestion; test. (If already durable: verification-only.)
- **Task 6 — Acceptance proof (R-E):** generated scanned-PDF/image fixtures → spans; INVENTORY_ONLY counts→zero/blocked-with-reason; leakage guards.
- **Task 7 — Config/docs:** `.env.example` + tracked vars (`TESSDATA_PREFIX`/`IDIS_OCR_DPI`/`IDIS_OCR_LANGUAGE`); plan reconciliation.
- **Task 8 — Verification gate + review:** full CI-parity gate + code review.
- **(BROAD only) N1–N4:** capability honors OCR-succeeded image; preflight/task_planner accept; new IMAGE/OCR chunker; blast-radius tests.

---

## 8. Verification gate (CI parity — run from worktree root, `PYTHONPATH=src`)
`ruff format --check .` · `ruff check .` · `mypy src/idis` · `python scripts/forbidden_scan.py` · `git diff --check` · targeted `pytest` for touched modules (mocked OCR; skip-guarded real-binary). Note: DB-backed `*_postgres.py` only run in CI `postgres-integration`; main suite local-only with pinned PYTHONPATH.

## 9. Out of scope
Media/STT (Slice80); conversion/transcode engine; bounding-box locators (unless trivial); VC-ready/readiness; DB/migration/RLS/OpenAPI; forcing OCR on in production; new providers/network.

## 10. Risks
- **Hidden assumption (Slice78 lesson):** verify Q1–Q3 empirically in Task 1 before designing — do not assume "scanned PDFs already flow" / "image spans are durable".
- **Determinism/flakiness:** real OCR varies by binary/version — keep CI on mocked adapter; skip-guard real-binary tests.
- **Leakage:** OCR text is document content — all health/diagnostic/aggregate outputs must stay path/content-free (reuse `_safe_summary`/sanitizer patterns).
