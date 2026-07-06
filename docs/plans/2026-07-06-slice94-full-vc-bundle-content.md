# Slice94 — Full VC Bundle Content Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task (RED → verify RED → minimal GREEN → verify). STOP for approval after each task.

**Goal:** Deliver the whole investor-readable VC package as a durable export bundle whose every material assertion links to safe evidence *and* provenance IDs, and whose financial tables/assumptions are reproducible — closing the one genuine gap (run-level provenance is not in the bundle) and proving the acceptance end-to-end.

**Architecture:** The `DeliverablesGenerator` already produces the 5 deliverable documents (screening / IC memo / truth dashboard / QA brief / decline), and `ProductBundleExporter` already emits the investor-package artifacts (screening, IC memo, truth dashboard, QA brief, executive summary, commercial/financial diligence, risk register, evidence index, run summary) **and the *evidence* side of the source/provenance appendix** — all durable, sanitized, and SHA256-addressed. The one genuine missing piece is the **run-level *provenance* appendix** (how the outputs were produced). Slice94 is therefore mostly a verify-and-complete slice, **not a broad rebuild**: add that safe provenance appendix, then prove both acceptance criteria over the whole exported bundle.

**Tech Stack:** Python 3.11, Pydantic v2, object storage, Anthropic live provider, pytest, ruff, mypy (Postgres exists in the wider project but is **not** touched this slice — Slice94 adds no migrations/durable tables). Injected fakes in tests — no real LLM; filesystem object store; no database this slice.

**Base:** worktree `C:\Projects\IDIS\IDIS-slice94` (branch `slice94-full-vc-bundle-content`) off `origin/main @ 97b9870`.

---

## 1. As-built map (verified — reuse-before-create)

**The exported bundle already emits the investor-package artifacts and the *evidence* side of the source/provenance appendix; the *run-level provenance* side is the real missing piece** (`src/idis/deliverables/product_bundle.py`, hand-verified). Item #10 below is only half-met — the evidence side ships, the provenance side (G1) does not:

| Scope item | Status | Where (file:line) |
| --- | --- | --- |
| Executive summary | ✅ artifact `executive_summary.json` | `product_bundle.py:195-198` (← `ic_memo.executive_summary`) |
| Commercial diligence | ✅ artifact (company/market/team) | `product_bundle.py:199-206` |
| Financial diligence | ✅ artifact (financials + scenario + calc package + financial table) | `product_bundle.py:207-218` |
| Risk register | ✅ artifact `risk_register.json` | `product_bundle.py:219-222` (← `ic_memo.risks_and_mitigations`) |
| IC memo | ✅ PDF+DOCX | `product_bundle.py:185-186`; model `models/deliverables.py:250-309` |
| Truth dashboard | ✅ `truth_dashboard.json` | `product_bundle.py:187-190`; `truth_dashboard.py` |
| QA brief | ✅ `qa_brief.json` | `product_bundle.py:191-194`; `qa_brief.py` |
| Evidence index | ✅ `evidence_index.json` (claim/calc audit entries + calc/graph/rag/layer2/enrichment/vep) | `product_bundle.py:224-235`, `481-524` |
| Run summary JSON | ✅ `run_summary.json` (per-subsystem counts/status/ids) | `product_bundle.py:236-295` |
| Source/**provenance** appendix | ⚠️ **evidence side only** — provenance side MISSING | `evidence_index` has claim/calc + evidence packages; **no LLM provenance** |

**Acceptance surfaces already in place:**
- **NFF hard gate** (every factual assertion must reference a claim/calc id or be subjective): `src/idis/validators/no_free_facts.py:188-525`; enforced on all deliverables at generation (`DeliverablesGenerator._validate_nff`). The 4 diligence artifacts `model_dump` IC-memo sections whose `DeliverableFact`s carry `claim_refs`/`calc_refs` (`models/deliverables.py:27-70`) — so exported assertions are ref-linked and NFF-gated.
- **Per-deliverable audit appendix** (`ref_id`/`ref_type`/`sanad_grade`/`source_summary`/`reproducibility_hash`): `models/deliverables.py:97-155`; enriched `generator.py:680-724`; consolidated `product_bundle.py:481-524`.
- **Financial reproducibility**: `deliverables/financial_table.py:49-78` rows carry `calc_sanad_id`, `formula_hash`, `code_version`, `reproducibility_hash` (SHA256), `input_claim_ids`; assumptions frozen at calc time (`api/routes/runs.py:1788-1801`); exported in `financial_diligence.calculation_package` + `financial_table` (`product_bundle.py:215-216`).
- **Safe run-level provenance builders EXIST** (safe fields only — provider/model/prompt-ids/versions/sanitized request-ids; layer2 adds executed booleans): `api/routes/runs.py:3537-3735`, attached to step result_summaries — `extraction` (`:3334`), `debate` (`:1227`), `analysis` (`:1658`), `scoring` (`:1860`), `layer2` (`:1555`). `_build_debate_observability` (`:3738-3760`) also exists.
- **Sanitization + durability**: whitelist key/value filters + path/text/embedding stripping (`product_bundle.py:33-40, 339-362`); SHA256 object URIs + manifest (`product_bundle.py:405-434`); `ARTIFACT_CATALOG` (`artifact_catalog.py:26-63`).

## 2. True gaps ("what's not")

- **G1 — Run-level provenance is not in the bundle (the one real build gap).** Hand-verified: `product_bundle.py` contains **zero** `provenance` references, and `export_bundle` (`:80-95`) accepts graph/rag/layer2/enrichment/vep evidence but **not** the 5 provenance blocks. So *how the outputs were produced* (models, prompts, backends, sanitized request-ids, layer2 challenger/arbiter executed proof) never reaches the investor package — scope item #10 "Source/**provenance** appendix" is only half-met, and the acceptance's "provenance IDs" are absent from the bundle.
- **G2 — No end-to-end bundle acceptance proof (the core deliverable).** There is no single test asserting the two Slice94 acceptance criteria over the *whole exported bundle*: (a) every material assertion in every exported artifact links to safe evidence/provenance IDs, (b) financial tables + assumptions are reproducible. Given most content exists, this proof IS the slice.
- **G3 — Reproducibility is exported but not acceptance-asserted.** `reproducibility_hash`/`formula_hash`/`code_version`/`input_claim_ids` are present, but nothing pins that a deterministic re-run yields identical hashes and that assumptions are frozen/complete. Minor — an assertion, not a build.
- **Non-gaps (already met — do NOT rebuild):** the 10 artifacts, NFF on exported assertions, per-deliverable audit appendix, financial reproducibility export, sanitization, durability/manifest. Explorer suggestions to "re-validate agent reports for NFF" and "surface debate dissent" are **out of scope**: exported assertions are already NFF-gated, and dissent/advocate are deferred (Slice93 DEC-H).

## 3. Approach

1. **Provenance appendix (G1)** — add a consolidated, safe `provenance_appendix` bundle artifact that surfaces the 5 existing provenance blocks (+ optional debate observability: counts/booleans only). Mirror the existing `_*_package` safe-builder pattern and the Slice93 `_build_layer2_provenance` safety contract: **safe fields only** — provider/backend/model/prompt-ids/versions/sanitized request-ids/executed booleans; never API keys, prompt bodies, responses, raw payloads, exception text, or paths. Thread the blocks into `export_bundle` (new optional param, mirroring the evidence params), register in `ARTIFACT_CATALOG` + manifest, and cross-reference from `run_summary`/`evidence_index` so provenance IDs are linkable.
2. **Bundle acceptance proof (G2)** — one end-to-end path over an exported bundle (injected fakes) asserting: every exported deliverable artifact's factual assertions carry claim/calc refs and pass NFF; every ref resolves in `evidence_index`; the `provenance_appendix` carries safe run-level provenance with no leakage; `financial_diligence` carries reproducible calc lineage.
3. **Reproducibility assertion (G3)** — assert a re-run yields identical `reproducibility_hash`es and that assumptions are frozen/serialized — no new production code expected.
4. **Reconciliation + closeout** — reconcile the readiness doc / plan (post-Slice94 banner, frozen census preserved), then acceptance + independent review + closeout PR (only when approved).
5. **Safety/strict unchanged** — no new leak surface; deterministic ids/timestamps; no real LLM in tests; filesystem object store; no database this slice.

## 4. Safety / strict boundaries

- The provenance appendix carries **safe shapes only** (ids/enums/counts/booleans/sanitized request-ids) — reuse `_sanitize_request_id` and the `product_bundle` whitelist/path filters; never claim text, transcripts, prompt bodies, model output, keys, or paths.
- No scorecard mutation; deterministic ordering and ids; fail-closed posture preserved.
- Filesystem object-store roundtrips in tests; injected fakes elsewhere; no database exercised this slice.

## 5. Risks

- **Leak via provenance** — the provenance blocks already exist as safe structures, but the appendix builder must re-assert the whitelist (belt-and-suspenders) and be adversarially tested.
- **Plumbing source** — the provenance blocks live in per-step result_summaries; the export route/orchestrator step must thread them from the accumulated step results without inventing new data.
- **Over-reach** — resist adding dissent/advocate/agent-NFF (deferred / already met). Keep the slice to G1 + G2 + G3.
- **NFF completeness** — confirm the 4 diligence artifacts' refs are actually resolvable in `evidence_index` (they should be, via the memo audit appendix) — pin it, don't assume.

## 6. Tasks (bite-sized, TDD)

> Acceptance-critical spine: **T1 (characterize already-built), T2 (provenance appendix), T3 (bundle acceptance proof), T5 (reconcile), T6 (closeout)**. T4 is a small reproducibility assertion.

> **Status (post-Slice94, 2026-07-06):** Landed & gate-green — **T1** (characterization: the bundle already emits the investor-package artifacts + the *evidence* side of the source/provenance appendix; NFF + reproducibility fields present), **T2** (the run-level `provenance_appendix` — safe whitelist builder with **typed value filtering**, catalog + manifest registration, threaded from the per-step provenance blocks through the export path, cross-referenced from `run_summary`/`evidence_index`; the G1 pin flipped), **T3** (end-to-end acceptance proof: every material assertion links to safe claim/calc IDs that resolve through `evidence_index`; provenance IDs resolve through `provenance_appendix`; financial diligence reproducible; deterministic re-export — no production gap), **T4** (frozen-`assumptions` reproducibility pinned precisely on the `calculation_package`; genuine RED→GREEN, no production change), **T5** (this readiness-doc + plan reconciliation). **Remaining:** **T6** (independent review + closeout PR, on explicit approval). No production gap surfaced across T3–T5; the slice was substantially already-built, with T2 the one genuine addition. Test boundary this slice: injected fakes only; filesystem object store; **no database** (no Postgres path is exercised — Slice94 adds no durable tables).

### Task 1 — Characterization (pin the already-built truth; GREEN-on-arrival)
`tests/test_slice94_bundle_characterization.py`: pin that the exported bundle already emits **the investor-package artifacts and the *evidence* side of the source/provenance appendix** (executive_summary/commercial_diligence/financial_diligence/risk_register/ic_memo/truth_dashboard/qa_brief/evidence_index/run_summary + manifest), that exported deliverable assertions carry claim/calc refs and pass NFF, that financial reproducibility fields are present, and that **no run-level provenance is in the bundle yet** (the pin that flips in T2). Any RED → STOP + report.

### Task 2 — Consolidated provenance appendix (G1)
RED-first: `_provenance_appendix(...)` safe builder + `provenance_appendix` artifact; thread the 5 provenance blocks (+ safe debate observability) into `export_bundle`; register in `ARTIFACT_CATALOG` + manifest; cross-reference from `run_summary`/`evidence_index`. Adversarial test proves no key/prompt/response/path leaks. Flip the T1 "no provenance in bundle" pin.

### Task 3 — End-to-end bundle acceptance proof (G2)
RED-first (green-on-arrival for existing content, drives out any residual gap): one path exports a full bundle (injected fakes) and asserts both acceptance criteria across every exported artifact — assertions ↔ safe evidence/provenance IDs (NFF + evidence_index + provenance_appendix), and financial tables/assumptions reproducible.

### Task 4 — Reproducibility assertion (G3)
RED-first: assert a deterministic re-run yields identical `reproducibility_hash`es and frozen assumptions across the financial table / calc package. Expect no production change; if a real gap surfaces, close it minimally.

### Task 5 — Readiness doc + plan reconciliation
RED-first doc-pin: post-Slice94 banner reconciling the complete investor bundle + provenance appendix; preserve the frozen Slice-53 census and prior banners verbatim; reconcile this plan's task status.

### Task 6 — Acceptance + independent review + closeout
Full `python -m pytest -q` + clean-cache mypy + ruff + forbidden scan + `git diff --check`; independent multi-agent review (leak-boundary / provenance / acceptance); fix any Critical/Important; closeout PR only when explicitly approved.

## 7. Decisions (LOCKED, 2026-07-06)

- **DEC-A — Scope (LOCKED).** Slice94 = **acceptance proof + the run-level provenance appendix**, NOT a broad rebuild. Agent-report NFF re-validation and dissent/advocate surfacing stay **out** (already met / deferred per Slice93 DEC-H).
- **DEC-B — Provenance appendix shape (LOCKED).** A **first-class `provenance_appendix.json`** bundle artifact consolidating the 5 safe provenance blocks (+ safe debate observability), registered in `ARTIFACT_CATALOG` + manifest and cross-referenced from `run_summary`/`evidence_index`.
- **DEC-C — Provenance source/plumbing (LOCKED).** **Thread the provenance blocks from the accumulated per-step result_summaries** (`debate/analysis/scoring/extraction/layer2_provenance`) through the product-bundle export path (mirroring how the evidence dicts are already assembled) — not by re-reading persisted step rows.
- **DEC-D — Reproducibility acceptance (LOCKED).** **Test-first assertion** (re-run → identical `reproducibility_hash` + frozen assumptions); add production code only if a real gap appears.
- **DEC-E — Test boundary (LOCKED; reconciled to as-built).** **Injected fakes only; no real Anthropic; filesystem object store; no database** — Slice94 adds no durable tables, so **no Postgres path is exercised** this slice.
