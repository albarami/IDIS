# Slice81 — Private Data-Room Parse Readiness Gate — Implementation Plan

> **For Claude / agentic workers:** REQUIRED SUB-SKILL: `superpowers:executing-plans` (or `subagent-driven-development`) task-by-task. Per task: `test-driven-development` (RED → verify red → minimal GREEN → verify), `verification-before-completion` before any status claim, `using-git-worktrees` already done, `finishing-a-development-branch` before commit/PR. **Reuse before create. STOP for approval after each task.** Confirm the §4 decisions (especially **D1 acceptance surface** and **D2 unintended-set**) BEFORE Task 1. **Status: implemented across Tasks 1–6 — see §0 As-built status.** §§1–12 are retained as the original planning record.

**Goal:** Run a local private gate over `real_example` for **upload/triage/parse readiness only** and prove **zero unintended deferrals** before any downstream live work is accepted — emitting a **safe aggregate** with counts by parser status, **evidence class**, and blocker reason, backed by the existing **resume ledger + per-file timeout/memory** controls. **No FULL run, no extraction/claims/live, no provider/network.**

**Architecture:** The private gate already exists end-to-end (`real_example_gate.py` + `real_example_gate_runtime.py` + `real_example_gate_ledger.py`) with two modes (`INVENTORY_ONLY`, `PARSE_SUPPORTED`), a hash-keyed resume ledger, per-file timeout / total-runtime / memory budgets, and a redacted `_safe_summary` (counts-only). Slice81 adds a **thin, safe parse-readiness projection** over `PARSE_SUPPORTED` that (a) classifies each non-success `reason_code` as **intended blocker** vs **unintended deferral** (reusing the gate's existing `RETRYABLE_REASON_CODES`), (b) aggregates **`counts_by_evidence_class`** (derived from existing extension sets), and (c) emits a `parse_ready` verdict (= zero unintended deferrals). It does **not** mutate `_safe_summary` (a separate projection avoids blast radius), does **not** parse with real binaries in CI (synthetic corpus + injected `parse_attempt_fn`), and does **not** touch FULL run / extraction / upload route / handoff.

**Tech Stack:** Python 3.11+, Pydantic v2, FastAPI, pytest, ruff (CI-parity ≥0.15), mypy. No new deps.

**Base:** branch `slice81-private-parse-readiness-gate` @ `9fd8a86b42f7e38a5172b6dc8c0e741ae4eafa18` (= `origin/main`, Slice80 merged via PR #92), worktree `C:/Projects/IDIS/IDIS-slice81` (baseline ruff + mypy green; smoke `test_real_example_gate.py` 38 passed; `idis.__file__` pinned to this worktree's `src`).

---

## 0. As-built status (Slice81 implemented — Tasks 1–6, pending commit/PR)

This section reconciles the plan to what was built; §§1–12 below are the original planning record.

- **D1 — LOCKED & implemented:** PARSE_SUPPORTED projection; **no new `GateMode`**. The projection wraps `run_real_example_gate(mode=PARSE_SUPPORTED)`.
- **D2 — LOCKED & implemented (disposition finalized in Task 3):**
  - **Unintended** = `RETRYABLE_REASON_CODES` (`internal_error`, `max_memory_exceeded`, `max_runtime_exceeded`, `parse_timeout`, `parser_failed`) **+ `unknown_format` + `media_transcription_failed` + `media_transcription_timeout` + any empty/None/unrecognized future reason** (fail-safe: never silently accept an unknown blocker).
  - **Intended** = `parsed`, `conversion_required`, `ocr_required`, `ocr_no_text_extracted`, `media_transcription_unavailable`, `media_no_text_extracted`, `media_duration_exceeded`, `unsupported_format`, `unsupported_in_slice_29`, `file_too_large`, `encrypted_pdf`, `no_text_extracted`, `corrupted_file`, `inventory_only`.
- **D4 — LOCKED & implemented:** evidence classes `PDF, SPREADSHEET, DOCUMENT, PRESENTATION, WEB_TEXT, IMAGE, MEDIA, OTHER`, derived from **extension only** (`.csv → OTHER`; `.xlsx/.xlsm → SPREADSHEET`). A filename is never split for an embedded extension.
- **D5 — implemented:** classifiers + `build_real_example_parse_readiness_summary` projection **co-located in `real_example_gate.py`**; thin **`--parse-readiness` CLI flag** added (mutually-exclusive mode; emits the projection; always safe — does **not** require `--safe-summary`).
- **`_safe_summary` unchanged** — Slice81 uses the **separate** `build_real_example_parse_readiness_summary`, which adds `counts_by_evidence_class`, `counts_by_deferral_class`, `unintended_deferral_reason_codes`, and `parse_ready` (True ⇔ zero unintended deferrals) on top of the gate's safe counts. The Slice80 10-key `_safe_summary` leak test still passes. **`parse_ready=True` means zero *unintended* deferrals — not zero blockers** (intended blockers may still be present).
- **Resume ledger + timeout/memory confirmed through the projection** (Task 5): terminal intended entries resume; retryable/unintended reasons are **re-attempted, not terminal**; `parse_timeout` (injected `timed_out()`) and `max_memory_exceeded` flow through as unintended → `parse_ready=False`.
- **Media ledger privacy preserved:** media files use a content-free `media-no-read:<digest>` ledger key (bytes never read); the projection counts media as `MEDIA` without reading content.
- **Windows/local memory caveat (characterized):** `_current_memory_mb()` returns `0.0` on this Windows dev env, so a positive `max_memory_mb` may not fire locally; tests force the probe via a monkeypatch/helper route, and CI/POSIX (`resource.getrusage` `ru_maxrss`) covers the real memory path.
- **No** FULL run, extraction/claims, provider/network, DB migration, OpenAPI/schema, or Slice82 work.
- **Tests (TDD):** `test_slice81_parse_readiness_{characterization,classifiers,projection,acceptance,resume_controls}.py`.
- **CLI usage (safe, local/CI-private):** `python scripts/run_real_example_gate.py --root real_example --parse-readiness` — emits the **safe aggregate projection only** (counts + `parse_ready`); no per-file records, paths, filenames, or content. No dedicated CLI runbook exists; the argparse `--help` epilog documents the command shapes.

---

## 1. Master Plan text (verbatim)
> #### Slice 81: Private Data-Room Parse Readiness Gate
>
> **Goal:** Run a local private gate over `real_example` for upload/triage/parse readiness only.
>
> **Scope:**
> - No FULL run yet.
> - Safe aggregate counts by parser status, evidence class, and blocker reason.
> - Resume ledger and per-file timeout/memory controls.
>
> **Acceptance:**
> - `real_example` parse readiness has zero unintended deferrals before downstream live work is accepted.

---

## 2. Discovery — the gate is largely built (verified, exact refs)

### 2.1 Already built and working (REUSE as-is)
- **Gate + modes:** `src/idis/evaluation/real_example_gate.py` — `run_real_example_gate(*, root, ledger_path, mode, safe_summary, per_file_timeout_seconds, max_runtime_seconds, max_memory_mb, parse_attempt_fn, ocr_*, media_*)` (:83-250); `GateMode` (:64-68) = `INVENTORY_ONLY` / `PARSE_SUPPORTED`; `_attempt_for_file` (:395-541) classifies each file; `_capability_reason_code` (:752-766); `main` CLI (:285+) with `--inventory-only|--parse-supported`, `--per-file-timeout-seconds`, `--max-runtime-seconds`, `--max-memory-mb`, `--ocr-*`, `--media-*`.
- **Safe aggregate:** `_safe_summary` (:803-820) returns **exactly 10 keys** — `gate, safe_summary, mode, total_files, processed_files, ledger_entry_count, counts_by_extension, counts_by_status, counts_by_parser_outcome, counts_by_reason_code`; `_count` (:823-824) is `Counter`→sorted dict. **Counts-only; no per-file records, paths, or filenames.**
- **Parse outcomes:** `real_example_gate_runtime.py` — `ParseAttempt` (:35-86): `parsed` (status=parsed/outcome=success), `failed(reason_code)` (failed/error), `timed_out` (timed_out/timeout/parse_timeout), `deferred(reason_code)` (deferred/not_attempted), `media_required(reason_code)` (deferred/media_required), `ocr_required` / `ocr_no_text_extracted`, `unsupported(reason_code)` (unsupported/not_attempted). `run_injected_parse_with_timeout` (:91-103, deadline) and `run_parse_subprocess` (:106-163, process.join timeout + bounded queue) — output-suppressed.
- **Resume ledger:** `real_example_gate_ledger.py` — hash-keyed (`sha256` → `by_extension` → `{extension,size_bytes,status,parser_outcome,reason_code,+policy_keys}`); `load_ledger`/`save_ledger` (:87-111) fail closed and **never expose local paths**; `terminal_ledger_entry` (:114-154) re-attempts retryable; `record_ledger_entry` (:157-193); `ledger_entry_count` (:196-204).
- **Per-file timeout / memory:** `per_file_timeout_seconds` (subprocess join + injected deadline), `max_runtime_seconds` (`_runtime_exceeded` :775), `max_memory_mb` (`memory_exceeded` / `_current_memory_mb`, gate_runtime :166-170, :333). All present and tested.
- **INVENTORY_ONLY projection precedent:** `build_data_room_package_inventory_summary` (:253-282) — a thin Slice77 wrapper that runs INVENTORY_ONLY and re-projects the safe aggregate. **This is the exact shape to mirror for a PARSE_SUPPORTED parse-readiness projection.**

### 2.2 The "unintended deferral" concept already exists (the key Slice81 lever)
`RETRYABLE_REASON_CODES` (`real_example_gate_ledger.py:15-23`) = `{internal_error, max_memory_exceeded, max_runtime_exceeded, parse_timeout, parser_failed}` — these are the **transient/error** outcomes the ledger refuses to treat as terminal (it re-attempts them). They map 1:1 to **"unintended deferrals."** Everything else terminal is an **intended blocker**: `conversion_required`, `ocr_required`, `ocr_no_text_extracted`, `media_transcription_unavailable` (+ other `MEDIA_POLICY_SENSITIVE_REASON_CODES`), `unsupported_format`, `unsupported_in_slice_29`, `file_too_large`, `encrypted_pdf`, `no_text_extracted`, `corrupted_file`, `inventory_only`. (`unknown_format` is ambiguous — see D2.)

### 2.3 The gaps Slice81 fills (small, net-new)
1. **No intended-vs-unintended classification in any summary.** The gate emits `counts_by_reason_code` but does not flag which are unintended → no `parse_ready` verdict. (grep: no `unintended`/`intended` in `evaluation/`.)
2. **No `counts_by_evidence_class`.** `evidence_class` exists **nowhere** in `src` (grep empty). Slice81's "counts by evidence class" is net-new; it can derive from existing extension sets in `parser_capabilities.py` (`_EXTENSION_TO_FORMAT`, `_CONVERSION_REQUIRED_EXTENSIONS`, `_OCR_REQUIRED_EXTENSIONS`, `_UNSUPPORTED_EXTENSIONS`, `MEDIA_EXTENSIONS`).
3. **No parse-readiness entrypoint/verdict.** There is a Slice77 INVENTORY_ONLY projection but no PARSE_SUPPORTED "parse readiness" projection emitting the verdict.

### 2.4 Blast radius (verified)
Six test files consume the gate: `tests/test_real_example_gate.py` (primary), `tests/test_slice77_data_room_package.py` (INVENTORY_ONLY projection), `tests/test_slice79_ocr_acceptance.py`, `tests/test_slice80_media_characterization.py`, `tests/test_slice80_media_acceptance.py`, `tests/test_tesseract_ocr_adapter.py`. **CRITICAL:** `test_slice80_media_acceptance.py::test_safe_aggregate_has_no_filename_path_model_or_secret_leak` asserts `set(summary.keys()) == {the 10 _safe_summary keys}` — **so Slice81 MUST NOT add keys to `_safe_summary`; it must use a separate projection** (or that test breaks). This locks D5.

---

## 3. Reuse map (exact files)
**Reuse unchanged (verify only):** `run_real_example_gate` + `GateMode.PARSE_SUPPORTED`, `_attempt_for_file`, `ParseAttempt`, `run_injected_parse_with_timeout` / `run_parse_subprocess`, the full `real_example_gate_ledger.py` (resume + safety), per-file timeout / `max_runtime_seconds` / `max_memory_mb`, `_safe_summary` (do not change its keys), `parser_capabilities` extension sets.
**Touch (production) — NARROW:**
- **new** classification + projection in `src/idis/evaluation/real_example_gate.py` (or a new sibling module `real_example_parse_readiness.py` — see D5): `INTENDED`/`UNINTENDED` reason-code split reusing `RETRYABLE_REASON_CODES`; `_evidence_class_for_extension`; `build_real_example_parse_readiness_summary(...)` returning a **separate** safe dict: `{safe_summary, mode:"parse_supported", total_files, processed_files, ledger_entry_count, counts_by_extension, counts_by_status, counts_by_reason_code, counts_by_evidence_class, counts_by_deferral_class, unintended_deferral_reason_codes (counts), parse_ready (bool)}`.
- optional thin CLI flag (`--parse-readiness`) or a `scripts/` hook mirroring the INVENTORY_ONLY path (decide in D5).
- **Out of scope:** `_safe_summary` key changes, FULL run, extraction/claims, upload route, handoff, harness.

---

## 4. Decisions — confirm BEFORE Task 1

### D1 — Acceptance surface (LOCKED proposal)
**PARSE_SUPPORTED** is the parse-readiness surface (INVENTORY_ONLY does not parse, so it cannot prove readiness; it remains a guard only — consistent with Slice80 R-E). Add a **separate projection** over `PARSE_SUPPORTED`; do **not** add a new `GateMode`.

### D2 — "Unintended deferral" set (LOCKED proposal + 1 open)
**Unintended = `RETRYABLE_REASON_CODES`** = `{internal_error, max_memory_exceeded, max_runtime_exceeded, parse_timeout, parser_failed}` (reuse the gate's own canonical transient set; single source of truth). All other terminal reasons are **intended blockers**. **Open:** classify `unknown_format` as **unintended** (a triage/classification gap worth surfacing) — recommended — vs intended. Recommend **unintended**. (Confirm.)

### D3 — CI determinism vs real run (LOCKED proposal)
CI acceptance uses a **synthetic/generated corpus + injected `parse_attempt_fn`** (deterministic; no private `real_example` data; no real parsers/OCR/media binaries) — mirrors Slice80. The **real private `real_example` PARSE_SUPPORTED run** stays a local/CI-private operation (already supported by the gate + CLI); it is documented, not CI-gated.

### D4 — Evidence-class taxonomy (LOCKED proposal, naming open)
Derive `evidence_class` from extension using existing sets: `PDF` (.pdf), `SPREADSHEET` (.xlsx/.xlsm), `DOCUMENT` (.docx), `PRESENTATION` (.pptx), `WEB_TEXT` (.html/.htm/.txt), `IMAGE` (.png/.jpg/.jpeg/.tif/.tiff/.bmp), `MEDIA` (.mp4), `OTHER` (everything else). **Open:** exact class names. Evidence class is derived from **extension only** (no filename/content) → safe.

### D5 — Where the projection lives (LOCKED proposal)
A **separate projection** (function in `real_example_gate.py` next to `build_data_room_package_inventory_summary`, or a small new `real_example_parse_readiness.py`) that **wraps** `run_real_example_gate(mode=PARSE_SUPPORTED)` and adds the new safe fields — leaving `_safe_summary`'s 10 keys untouched (protects the Slice80 leak test). Recommend co-locating in `real_example_gate.py` for discoverability.

### D6 — No FULL/extraction/upload (LOCKED proposal)
No FULL run, no extraction/claims/live LLM, no provider/network, no upload-route admission change, no run-scoped inventory, no handoff, no harness change, no DB/OpenAPI/schema migration.

---

## 5. Scope boundary
- **No FULL run** — readiness/aggregate only; never executes a strict FULL or live run.
- **No downstream extraction / claims / live work** — the gate stops at parse readiness.
- **No provider/network** — no Anthropic/OpenAI/enrichment/graph calls; no model downloads (CI blocks).
- **Safe aggregate only** — counts + booleans + fixed identifiers; no per-file records.

## 6. Acceptance mapping
- **"zero unintended deferrals before downstream live work is accepted"** → `build_real_example_parse_readiness_summary(...)` returns `parse_ready=True` iff `sum(counts of unintended reason codes) == 0`; tests prove (a) intended blockers keep `parse_ready=True`, (b) an injected `parser_failed` (or other `RETRYABLE_REASON_CODES`) flips `parse_ready=False` and increments `counts_by_deferral_class["unintended"]`.
- **"safe aggregate counts by parser status, evidence class, blocker reason"** → `counts_by_status`, `counts_by_evidence_class`, `counts_by_reason_code` present and safe.
- **"resume ledger + per-file timeout/memory controls"** → reuse + confirm: ledger resume skips terminal / re-attempts retryable; `per_file_timeout_seconds` / `max_runtime_seconds` / `max_memory_mb` produce the corresponding (unintended) reason codes.

## 7. Task breakdown (TDD; STOP after each)
- **Task 1 — Characterization (no prod change):** over a synthetic corpus + injected `parse_attempt_fn`, pin current truth: PARSE_SUPPORTED reason codes per evidence class (pdf/image/media/unsupported/text); injected `parser_failed`→`parser_failed`; ledger records terminal & skips retryable; timeout/memory → `parse_timeout`/`max_*_exceeded`; and that `_safe_summary` has **no** `evidence_class`/`parse_ready` (justifies the new projection). GREEN-on-arrival = confirmation.
- **Task 2 — Evidence-class + deferral-class classifiers (pure functions):** `_evidence_class_for_extension` and `intended/unintended` split (reuse `RETRYABLE_REASON_CODES`); unit tests incl. `unknown_format` per D2.
- **Task 3 — `build_real_example_parse_readiness_summary` projection:** wraps PARSE_SUPPORTED; adds `counts_by_evidence_class`, `counts_by_deferral_class`, unintended reason-code counts, `parse_ready`; **does not change `_safe_summary` keys**; safe-aggregate leak guards.
- **Task 4 — Acceptance proof:** synthetic corpus → `parse_ready=True` with only intended blockers; injected unintended → `parse_ready=False`; evidence-class counts; INVENTORY_ONLY guard (not the readiness path); leak guards (no path/filename/content/URI/model/env/secret/root).
- **Task 5 — Resume + timeout/memory confirmation:** confirm ledger resume + per-file timeout/memory reason codes flow into the readiness projection (mostly reuse; verification-style).
- **Task 6 — Config/docs + full gate + independent review.**
(Optional CLI flag folded into Task 3 if cheap; otherwise its own task.)

## 8. Not doing yet (explicit)
FULL run; extraction/claims/live LLM; provider/network; model downloads; upload-route admission; run-scoped inventory; handoff; full-run harness changes; OpenAPI/schema/DB migration; mutating `_safe_summary` keys. **None unless discovery during a task proves it strictly required — then STOP and report before editing.**

## 9. Safety rules
No raw path, filename, OCR/media transcript text, object key / storage URI, model path, env value, command output, or secret in any public/safe summary or ledger. Reuse the gate's counts-only `_safe_summary` discipline + the hash-keyed ledger; every new field is counts/booleans/fixed identifiers derived from **extension or reason_code only**. Each acceptance/projection test includes an explicit leak guard (root path + injected confidential markers absent from the serialized summary).

## 10. Verification gate (CI parity — from worktree root, `PYTHONPATH=src`)
`python -c "import idis; print(idis.__file__)"` (must resolve to this worktree) · `ruff format --check .` · `ruff check .` · clear `.mypy_cache` then `mypy src/idis` · `python scripts/forbidden_scan.py --repo-root .` · `git diff --check` · targeted `pytest` (synthetic corpus + injected `parse_attempt_fn`; real parsers/OCR/media skip-guarded). DB-backed `*_postgres.py` only in CI.

## 11. Risks
- **Blast radius on `_safe_summary`:** the Slice80 leak test pins its exact 10-key set — Slice81 must use a **separate projection** (D5), not extend `_safe_summary`.
- **Intended/unintended misclassification:** anchor on the gate's own `RETRYABLE_REASON_CODES` (single source of truth); decide `unknown_format` explicitly (D2). A wrong split would make the acceptance meaningless.
- **Determinism/CI:** never require the private `real_example` tree or real parsers/OCR/media in CI — synthetic corpus + injected `parse_attempt_fn`; skip-guard real-binary paths.
- **Leakage:** evidence_class must derive from extension only; never from filename/content.

## 12. Open questions for you — RESOLVED
1. **D2:** `unknown_format` → **unintended** (approved; plus `media_transcription_failed`/`media_transcription_timeout` unintended, and `ocr_no_text_extracted`/`media_no_text_extracted`/`media_duration_exceeded`/`unsupported_in_slice_29` promoted to intended in Task 3).
2. **D4:** evidence-class names **approved** (PDF/SPREADSHEET/DOCUMENT/PRESENTATION/WEB_TEXT/IMAGE/MEDIA/OTHER), extension-derived.
3. **D5:** **co-located in `real_example_gate.py`** + **`--parse-readiness` CLI flag added** (approved & implemented).
