# Slice78 — Parser Capability And Conversion Workflow — Implementation Plan

> **For Claude / agentic workers:** REQUIRED SUB-SKILL: use `superpowers:executing-plans` (or `superpowers:subagent-driven-development`) to implement task-by-task. Per task: `superpowers:test-driven-development` (RED → verify red → minimal GREEN → verify → refactor), `superpowers:verification-before-completion` before any status claim, `superpowers:using-git-worktrees` already done, `superpowers:finishing-a-development-branch` before commit/PR. **Reuse before create.** Per **Option A** (see §0), this slice makes HTML/TXT **genuinely supported end-to-end** (parse → capability → ingestion metadata → Slice77 ledger → upload-route admission → extraction chunking) — but with **no** conversion/OCR/media execution, **no** providers, **no** readiness/VC-ready changes, and **no** migration/OpenAPI/schema changes.

**Goal:** Close non-OCR/media parser gaps so the canonical capability matrix + production ingestion treat text-like classes consistently with what the system can actually parse: make **HTML/TXT supported** (integrate the existing `parse_html_text` parser), keep **DOCX/PPTX/XLSX/PDF** capability triage deterministic, and give **conversion-required** classes clear remediation reason codes — without any silent deferral and with user-visible blockers for genuinely-unsupported classes.

**Architecture:** Register the already-existing `parse_html_text` parser into the main `parse_bytes` registry (filename/MIME dispatch, like image/media), and update `capability_for_document` so `.html/.htm/.txt` classify as `SUPPORTED`/`READY` with a positive reason code. Triage then flows unchanged through `triage_document` → ingestion `Document.metadata` → the Slice77 durable package ledger, so HTML/TXT become `file_status: supported` end-to-end. Conversion-required (`.mp4`, OneNote) stays **reason-coded only** (no conversion engine). Acceptance is proven by parser/capability/ingestion tests with generated fixtures plus the Slice77 `INVENTORY_ONLY` safe-aggregate hook.

**Tech Stack:** Python 3.11+, Pydantic v2, FastAPI, pytest, ruff (CI-parity ≥0.15), mypy. **No DB / migration / RLS work** (triage persists via existing JSONB `Document.metadata` + Slice77 ledger `reason_codes` JSONB; no schema change) — so no Supabase/Postgres guidance applies.

**Base:** branch `slice78-parser-capability-conversion-workflow` @ `8ffbf4283f1eb7b1756846120f388c8e3740e2b7` (= `origin/main`, Slice77 merged), worktree `C:/Projects/IDIS/IDIS-slice78` (clean; baseline inherited from CI-green main).

---

## 0. Option A reconciliation — AS-BUILT scope (updated after Task 5)

§1–§10 below captured the **initial, narrower** intent (canonical capability flip + `INVENTORY_ONLY` proof). Task 5's review proved that genuinely supporting HTML/TXT **end-to-end** required more than the canonical flip; the user approved **Option A** ("make supported truthful end-to-end"). This section records the **actual** scope as built — **where it conflicts with §1–§10, this section governs.**

**Why the scope expanded (blast radius of the canonical `SUPPORTED` flip):**
- **Upload-route admission was required.** `POST /v1/deals/{dealId}/documents/upload` rejected HTML/TXT via `_reject_unsupported_upload_format` (`src/idis/api/routes/documents.py`), which only admitted magic-byte / image / media. Fix: admit `is_text_source(...)` too. (Originally assumed out of scope under R-D's "no FULL run".)
- **Extraction chunking was required.** Once HTML/TXT are `SUPPORTED` they flow into the run pipeline (`document_preflight` → `methodology_extraction_task_planning` → extraction); `ChunkingService` had no HTML/TEXT chunker and raised `UnsupportedDocumentTypeError` → silent `CHUNKING_FAILED` drop + deep failure. Fix: new pure `TextChunker` (`src/idis/services/extraction/chunking/text_chunker.py`) registered for `HTML`/`TEXT`. **No provider/LLM/OCR/media/network.**
- **real_example proof expanded beyond INVENTORY_ONLY to full-run/harness parity.** In addition to the `INVENTORY_ONLY` safe-aggregate hook, the **full-run harness** (`src/idis/evaluation/real_example_run_harness.py` `PUBLIC_UPLOAD_EXTENSIONS`) was updated so HTML/TXT upload/attempt like PDF/DOCX/PPTX/XLSX — verified against the **real** admission rule via a **direct route-admission test** (not the fake client). **This supersedes R-D's "INVENTORY_ONLY only / no FULL run".**
- **run-scoped inventory, ingestion handoff, and data_room_harness were INTENTIONAL blast-radius surfaces.** All consume the canonical capability matrix, so HTML flips `deferred→supported` there too: `RunDataRoomInventoryPackageService` (reason `supported_parser_available`), `RunDataRoomIngestionHandoffService` (supported/reused/fallback counts), and the FULL-run `data_room_harness`. Their tests were updated to the as-built behavior; the harness "blocked-steps" test now uses a genuinely-unsupported `.csv` so it still exercises `NO_INGESTED_DOCUMENTS` honestly (not weakened).

**Still NOT in scope (unchanged from §9):** **no** migration / DB / RLS / JSON-schema change; **no OpenAPI change** (verified 42 paths — reason codes flow through existing free-form arrays); **no** readiness / VC-ready work; **no** provider / OCR / media execution or network calls; **no** conversion/transcode engine (conversion stays reason-coded).

**As-built production surface (5 changed + 1 new):** `parsers/registry.py` (filename/MIME text dispatch + `is_text_source`/`_is_html_text_source`), `services/documents/parser_capabilities.py` (HTML/TEXT → `SUPPORTED`/`READY`/`text_parser_available`), `api/routes/documents.py` (route admission), `services/extraction/chunking/service.py` (register HTML/TEXT), **new** `services/extraction/chunking/text_chunker.py`, `evaluation/real_example_run_harness.py` (`PUBLIC_UPLOAD_EXTENSIONS`). Reason-code note: the capability/ingestion/Slice77-ledger path uses `text_parser_available`; the run-scoped inventory keeps its own `supported_parser_available`.

---

## 1. Master Plan text (verbatim)
> **Slice 78: Parser Capability And Conversion Workflow**
> **Goal:** Close non-OCR/media parser gaps before private full ingestion.
> **Scope:** HTML/TXT support or explicit supported conversion · DOCX/PPTX/XLSX/PDF capability triage · Conversion-required policy and remediation reasons.
> **Acceptance:** No `real_example` supported text-like class is silently deferred · Unsupported classes are user-visible blockers with reason codes.

## 2. Key discovery finding (the gap to close)
There are **two divergent parser paths today**:
- **Canonical / production path:** `parse_bytes` registry (`src/idis/parsers/registry.py:102-180`) routes PDF/XLSX/DOCX/PPTX/IMAGE/MEDIA and returns `UNSUPPORTED_FORMAT` for everything else. `capability_for_document` (`src/idis/services/documents/parser_capabilities.py:105-166`) maps `.html/.htm/.txt` (and `.csv/.zip/.rar/.7z/.msg/.eml`) to `DocumentSupportStatus.UNSUPPORTED` / `DocumentTriageStatus.UNSUPPORTED_SOURCE` / reason `unsupported_format` (`_UNSUPPORTED_EXTENSIONS`, `parser_capabilities.py:~53-76`).
- **real_example gate path:** `TEXT_PARSE_EXTENSIONS = {".html",".htm",".txt"}` (`real_example_gate.py:57-61`) ARE actively parsed via `parse_html_text(...)` (`real_example_gate_runtime.py:234-235`) → `status=parsed`. A real parser exists at `src/idis/parsers/html_text.py` (`parse_html_text(data, is_html, limits)`), proven by `tests/test_html_text_parser.py:8-31`.

**Consequence:** HTML/TXT — which the system **can** parse — are marked UNSUPPORTED by the canonical matrix, so in ingestion → `Document.metadata` → the Slice77 durable package ledger they roll up to **`blocked`** (`_rollup_file_status`, `package_service.py:196-224`). That is exactly a "supported text-like class" being treated as a blocker. Slice78 reconciles the canonical path with reality.

> Note: in the **gate itself**, HTML/TXT are NOT silently deferred today (Agent B confirmed). The gap is in the **canonical capability/ingestion path** that feeds production + the Slice77 ledger. Slice78 fixes the canonical path and keeps the gate consistent.

## 3. Current capability matrix (verified, `parser_capabilities.py` + tests)
| Class | support_status | triage_status | parser? | reason_code | Slice77 rollup today |
|---|---|---|---|---|---|
| `.pdf` | PARTIALLY_SUPPORTED | PARTIAL | pdf ✓ | `pdf_text_only_no_ocr` | deferred |
| `.xlsx`/`.xlsm` | PARTIALLY_SUPPORTED | PARTIAL | xlsx ✓ | `xlsx_partial_table_semantics` | deferred |
| `.docx` | SUPPORTED | READY | docx ✓ | `docx_text_parser_available` | supported |
| `.pptx` | PARTIALLY_SUPPORTED | PARTIAL | pptx ✓ | `pptx_partial_slide_text` | deferred |
| **`.html`/`.htm`/`.txt`** | **UNSUPPORTED** | **UNSUPPORTED_SOURCE** | parser exists but **unregistered** | `unsupported_format` | **blocked** ← gap |
| `.csv`/`.zip`/`.rar`/`.7z`/`.msg`/`.eml` | UNSUPPORTED | UNSUPPORTED_SOURCE | none | `unsupported_format` | blocked (correct) |
| `.mp4`, `.one`/`.onetoc2` | CONVERSION_REQUIRED | CONVERSION_REQUIRED | none | `conversion_required` (`requires_conversion=True`) | deferred |
| `.png`/`.jpg`/… | SCANNED_OR_IMAGE_ONLY | OCR_REQUIRED | OCR adapter | `ocr_required` | deferred |

Enums (no new values needed): `DocumentSupportStatus` (`document_classification.py:49-60`) already has `SUPPORTED`; `DocumentTriageStatus` (`:63-73`) already has `READY`/`CONVERSION_REQUIRED`. `ParseStatus` (`document.py:35-43`) = PENDING/PARSED/FAILED.

## 4. Reuse map (exact files / functions)
**Touch (production):**
- `src/idis/parsers/registry.py` — `parse_bytes(...)` (`:102-180`): add HTML/TXT dispatch (by filename/MIME, since they have no ZIP/PDF magic) → call `parse_html_text`.
- `src/idis/parsers/html_text.py` — `parse_html_text(data, is_html, limits)`: REUSE as-is (already returns `doc_type` HTML/TEXT + spans). Only add a `limits` default / MIME helper if needed.
- `src/idis/services/documents/parser_capabilities.py` — extension→capability map (`:19-83`), `_UNSUPPORTED_EXTENSIONS` (`:~53-76`), reason vocab (`:84-102`), `capability_for_document` (`:105-166`), `triage_document` (`:169-282`): move `.html/.htm/.txt` out of unsupported into a SUPPORTED text class with a positive reason code.

**Reuse unchanged (verify only — should flow automatically):**
- `src/idis/services/ingestion/service.py` — `_parse_metadata_for_persistence` (`:1116-1148`): persists `parser_support_status`/`parser_triage_status`/`parser_reason_codes`/`parser_requires_ocr`/`parser_requires_conversion`/`parse_error_codes` (`:1140-1144`). No change; HTML/TXT supported flows through.
- `src/idis/services/data_room/package_service.py` — `_build_file` (`:145-181`), `_rollup_file_status` (`:196-224`), `_safe_codes` (`:265-268`): consumes the metadata; HTML/TXT will roll up to `supported` automatically.
- `src/idis/evaluation/real_example_gate*.py` — gate already parses HTML/TXT; `build_data_room_package_inventory_summary` (`real_example_gate.py:266-272`) for the safe-aggregate proof.

**Tests / fixtures to reuse:** `create_test_docx` (`test_docx_parser.py:20-51`), `create_test_pptx` (`test_pptx_parser.py:21-66`), `create_test_xlsx` (`test_xlsx_parser.py:30-62`, openpyxl skip-guard), `create_test_pdf` (`test_pdf_parser.py:31-56`, reportlab skip-guard), raw HTML/TXT bytes (`test_html_text_parser.py`), `_write_fixture_tree` (`test_run_data_room_inventory_package_service.py`). Capability assertions live in `test_parser_capabilities.py` (esp. `test_html_txt_csv_are_unsupported_without_parser` — will be SPLIT, see RED). Registry routing: `test_parser_registry.py`. Ingestion persistence: `test_ingestion_service.py:902-931`, `test_document_triage.py:33-103`. Gate: `test_real_example_gate.py:172-277`.

## 5. Decisions

### LOCKED (proposed — confirm before Task 1; only R-A changes production behavior)
- **R-B (conversion = reason-coded only):** Slice78 ships **remediation reason codes** for conversion-required classes; it does **NOT** build/execute any conversion/transcode engine. `.mp4`/OneNote keep `CONVERSION_REQUIRED` + `requires_conversion=True` + a clear reason code. Actual conversion is deferred to a later slice.
- **R-C (no migration / no DB work):** triage persists via existing JSONB `Document.metadata` + Slice77 ledger `reason_codes` (JSONB). New reason-code strings need no schema change. **No migration, no RLS work.**
- **R-D (real_example proof) — EXPANDED under Option A (see §0):** the `INVENTORY_ONLY` safe-aggregate hook remains, **and** full-run/harness parity was added (HTML/TXT upload/attempt like PDF/DOCX/PPTX/XLSX, verified via a direct upload-route admission test). Still **no provider/OCR/media/network execution, no readiness change, no VC-ready claim, no raw content** — the full-run harness uses the existing fake API client + pure parsing/chunking only.
- **R-E (no OpenAPI version bump):** reason codes flow through existing free-form `reason_codes`/`counts_by_reason_code` arrays; `DocumentSupportStatus`/`DocumentTriageStatus` enum values are unchanged. Expect **no OpenAPI/schema change** (confirm during impl; if any, extend in place, no bump).
- **R-F (gate parity):** keep the real_example gate behavior consistent with the canonical matrix after the change (HTML/TXT supported in both).

### KEY OPEN DECISION (needs explicit user lock before Task 1)
- **R-A — HTML/TXT: integrate parser (recommended) vs explicit blocker/conversion.** The Master Plan allows "HTML/TXT support **or** explicit supported conversion." **Recommendation: integrate** — `parse_html_text` already exists, is tested, and the gate already uses it; integrating closes the gap with the least risk and directly satisfies "HTML/TXT support." This **changes production behavior**: HTML/TXT documents become ingestable/`supported` (and Slice77 ledger `supported` instead of `blocked`). If instead you want HTML/TXT to remain explicit blockers (or "conversion-required"), Task 1 changes accordingly. **Do not start Task 1 until R-A is locked.**

### NON-LOCKED / to settle during tasks
- Exact new reason code for supported text (proposed `html_text_parser_available` for HTML, `text_parser_available` for TXT, or a shared `text_parser_available`) — pick during Task 1; must match `^[a-z][a-z0-9_]*$` (Slice77 `_safe_codes`).
- Whether `.csv` stays UNSUPPORTED (recommended yes — no CSV parser; user-visible blocker) or is considered text-like (out of scope unless requested).
- Whether to fold in the Slice77 follow-up of adding `mypy`/`make check` to the local gate (recommended yes — see §7).

## 6. RED test list (design only — not implemented here)
**HTML/TXT support (drives R-A):**
- **T1 — capability:** `capability_for_document(filename=".html"/".htm"/".txt")` → `support_status=SUPPORTED`, `triage_status=READY`, `parser_name` set, positive reason code; `requires_conversion=False`, `requires_ocr=False`. (Currently UNSUPPORTED → RED.) Split `test_html_txt_csv_are_unsupported_without_parser` so CSV stays unsupported.
- **T2 — registry:** `parse_bytes(data, filename="x.html")` / `"x.txt"` routes to `parse_html_text`, returns `success=True`, `doc_type` HTML/TEXT. (Currently `UNSUPPORTED_FORMAT` → RED.)
- **T3 — triage:** `triage_document(parse_result=<successful html/txt parse>)` → SUPPORTED/READY (no deferral).
- **T4 — ingestion persistence:** ingesting an HTML/TXT doc persists `parser_support_status="supported"`, `parser_triage_status="ready"`, `parser_reason_codes=[<text reason>]` into `Document.metadata`.
- **T5 — Slice77 ledger (acceptance traceability):** a packaged HTML/TXT document → `file_status == "supported"` (was `blocked`); `counts_by_status` includes supported.

**Triage determinism + blockers + conversion reasons:**
- **T6 — DOCX/PPTX/XLSX/PDF:** deterministic capability triage retained (supported/partially_supported with the documented reason codes); nothing silently deferred. (Likely green-on-arrival guard.)
- **T7 — conversion-required:** `.mp4` (and `.one`) → `CONVERSION_REQUIRED` + `requires_conversion=True` + remediation reason code; assert **no** media/provider/transcode call is invoked. (Guard.)
- **T9 — genuine blockers:** `.csv`/`.zip`/`.rar`/`.7z`/`.msg`/`.eml` remain `UNSUPPORTED` + `unsupported_format` (user-visible blocker with reason code). (Acceptance.)

**Aggregate acceptance proof (safe):**
- **T8 — real_example proof (INVENTORY_ONLY + full-run/harness parity under Option A — see §0):** (a) an `INVENTORY_ONLY` synthetic tree (html/txt/docx/pptx/xlsx/pdf/png/mp4) via the Slice77 `build_data_room_package_inventory_summary` emits safe aggregate counts only; **and** (b) the full-run harness uploads/attempts HTML/TXT like PDF/DOCX/PPTX/XLSX (verified via a direct upload-route admission test). **Both** proofs: seeded path/name/content markers absent; **no provider/OCR/media execution; no readiness change**. (Reuses the Slice77 hook + leak-safety pattern.)

**Contract guard:**
- **T10 (if applicable):** OpenAPI/JSON-schema unchanged (no version bump); reason-code additions flow through free-form arrays. Likely a parse/no-diff assertion only.

## 7. Task sequence (bite-sized, TDD, STOP after each for approval)
- **Task 0 (this doc):** discovery/verification — confirm the HTML/TXT canonical-vs-gate inconsistency, that `parse_html_text` is registry-ready, that no migration is needed, and **get R-A locked**. → STOP.
- **Task 1:** HTML/TXT into `parse_bytes` registry + `capability_for_document` SUPPORTED mapping — RED **T1/T2/T3** (incl. splitting the unsupported test) → minimal GREEN → STOP.
- **Task 2:** ingestion + Slice77 ledger propagation — RED **T4/T5** → GREEN (expected: zero production change beyond Task 1; mostly coverage) → STOP.
- **Task 3:** DOCX/PPTX/XLSX/PDF determinism + genuine blockers + conversion-required remediation reasons — RED **T6/T7/T9** → GREEN → STOP.
- **Task 4:** real_example acceptance proof — `INVENTORY_ONLY` safe-aggregate **plus full-run/harness parity** (Option A, §0): HTML/TXT uploaded/attempted, verified via a direct upload-route admission test — RED **T8** → GREEN → STOP.
- **Task 5:** full verification gate (below) + `code-reviewer` review → STOP before any commit/PR.

## 8. Verification gate (execution time; PYTHONPATH pinned to this worktree's `src`, CI-parity ruff)
```
$env:PYTHONPATH=(Resolve-Path 'src').Path
python -c "import idis; print(idis.__file__)"   # must be C:\Projects\IDIS\IDIS-slice78\src\idis\__init__.py
pytest tests/test_parser_capabilities.py tests/test_html_text_parser.py tests/test_parser_registry.py \
  tests/test_ingestion_service.py tests/test_document_triage.py tests/test_pdf_parser.py \
  tests/test_docx_parser.py tests/test_pptx_parser.py tests/test_xlsx_parser.py \
  tests/test_real_example_gate.py tests/test_real_example_run_harness.py \
  tests/test_slice77_data_room_package.py tests/test_run_data_room_inventory_package_service.py -q
mypy src/idis --ignore-missing-imports        # ADD to gate (Slice77 follow-up — caught the only Slice77 CI red)
ruff format --check src tests scripts ; ruff check src tests scripts
python scripts/forbidden_scan.py --repo-root . ; git diff --check
# Contracts (parse only): json.loads(schemas/audit_event.schema.json) + yaml.safe_load(openapi/IDIS_OpenAPI_v6_3.yaml)
```
Run `mypy`/`make check` **before** committing (Slice77's only CI failure was a mypy gap absent from the local gate). Watch for known flaky timing tests in `test_real_example_run_harness.py` (deadline/timeout) — re-run/isolate to distinguish flakiness from regression.

## 9. Boundaries / out-of-scope
- **No** file conversion/transcoding engine — conversion is **reason-coded remediation only** (R-B).
- **No** OCR/media execution, **no** provider/network/LLM calls, **no** FULL run, **no** readiness clearing, **no** VC-ready claim.
- **No** migration / DB / RLS change (R-C); **no** OpenAPI version bump (R-E).
- **No** `real_example` raw content in **either** the `INVENTORY_ONLY` safe-aggregate proof **or** the full-run/harness proof — safe aggregates / safe summaries only (R-D, §0).
- **No** changes to OCR/media adapters or image/media parsers; **don't regress** DOCX/PPTX/XLSX/PDF.
- **Not** the platform ABAC `deal_id` extraction gap (separate Slice77 follow-up).
- **CSV and archives/mail** (`.csv/.zip/.rar/.7z/.msg/.eml`) stay UNSUPPORTED blockers unless explicitly added.

## 10. Risks / open questions (mapped to discovery area 5)
- **R-A (PRIMARY, needs user lock):** implement HTML/TXT support now (integrate existing `parse_html_text`) vs explicit conversion/blocker policy. Recommendation: integrate. Changes production behavior.
- **R-B:** conversion workflow persisted vs reason-coded only → **reason-coded only** (proposed lock).
- **R-C:** migration needed? → **No** (proposed lock).
- **R-D (EXPANDED under Option A — see §0):** real_example proof = `INVENTORY_ONLY` safe-aggregate **plus full-run/harness parity**; no raw content in either proof. (Original "inventory-only" intent superseded.)
- **R-G:** consistency risk — changing HTML/TXT to SUPPORTED flips existing assertions in `test_parser_capabilities.py` and the Slice77 ledger rollup for HTML/TXT (blocked→supported). This is intended; tests are updated by RED, and `test_real_example_gate.py` HTML/TXT-parsed assertions should already align.

---

**Status: implemented as Option A (see §0) and reconciled to as-built before commit.** R-A was locked (integrate the existing `parse_html_text` parser). Beyond the canonical flip, Option A added upload-route admission + a pure HTML/TEXT extraction chunker + full-run/harness parity, and updated the run-scoped inventory / ingestion-handoff / data_room_harness blast-radius tests. **No** migration / DB / RLS / JSON-schema change, **no OpenAPI change** (42 paths), **no** readiness/VC-ready/provider/OCR/media/network work, and **no** `real_example` private data changed.
