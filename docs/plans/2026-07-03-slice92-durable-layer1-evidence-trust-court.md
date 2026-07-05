# Slice 92 — Durable Layer 1 Evidence Trust Court — Implementation Plan

> **For Claude / agentic workers:** REQUIRED SUB-SKILL: `superpowers:executing-plans` (or `subagent-driven-development`) task-by-task. Per task: `test-driven-development` (RED → verify red → minimal GREEN → verify), `verification-before-completion` before any status claim, `using-git-worktrees` already done (`C:\Projects\IDIS\IDIS-slice92`, branch `slice92-layer1-evidence-trust-court`, base `origin/main` @ `31bc290`), `finishing-a-development-branch` before commit/PR. **Reuse before create. STOP for approval after each task.** The §9 decisions were LOCKED before implementation (DEC-A..DEC-G as recommended). **As-built (2026-07-04): Tasks 1–7 are complete; Task 8 is the closeout — see §0.**

**Goal (master plan §346):** Produce a durable Validated Evidence Package candidate from evidence, Sanad, defects, calculations, enrichment, graph, and RAG.

**Scope (master plan):** Layer 1 evidence integrity, contradictions, sanad strength, No-Free-Facts checks; persist VEP candidate, dissent, unresolved uncertainties, and Muḥāsabah records.

**Acceptance (master plan):** Layer 1 output is durable and referenced by Layer 2.

**Architecture:** The Layer 1 Evidence Trust Court and the VEP **already exist on main and are FULL-wired** (phase-3-0k/0l, PRs #22/#23, merged 2026-05-09): `METHODOLOGY_EVIDENCE_TRUST_COURT` (step 12) runs a governed Layer-1 debate (Muḥāsabah gate + contradiction finder) over materialized claims/evidence/sanads/grades/defects/calcs/truth-dashboards and produces claim-level dispositions and findings; `METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE` (step 13) packages the court record; `METHODOLOGY_LAYER2_READINESS_PACKAGE` (step 16) already consumes VEP records **in-memory**. Everything is run-memory only (`InMemoryRun...Service`s) — the only durable trace is the `run_steps.result_summary` JSONB. Slice92's substance is therefore **durability + the Layer-2 reference**: a migration + repository pair persisting the safe VEP candidate (and the scoped dissent/uncertainty/Muḥāsabah records), idempotent persistence from the existing step path, and a durable VEP reference consumed by `LAYER2_IC_CHALLENGE` (step 24). Evidence-integrity/contradiction/sanad-strength/NFF checks need no new engine — they are the existing court.

**Tech Stack:** Python 3.11, Pydantic v2, Postgres (+RLS, alembic-style versioned migrations), pytest, ruff, mypy. No LLM calls required by this slice (the court's Layer-1 debate already runs deterministically/live per debate backend policy — unchanged).

---

## 0. As-built outcome (Task 7 reconciliation, 2026-07-04)

The master-plan acceptance — "Layer 1 output is durable and referenced by Layer 2" — is met
and pinned end-to-end in `tests/test_slice92_acceptance.py` (one orchestrated FULL run with a
real materialized claim: durable rows + the real Layer-2 fn's `vep_ref_ids` ledger reference +
export visibility, plus the no-claims degrade). What landed per task:

- **T1** `test_slice92_layer1_durability_characterization.py` — pins of the pre-slice truth
  (court/VEP FULL-wired but in-memory; ordering; no persistence/migration/export/L2 reference);
  pins flipped only as tasks genuinely changed them.
- **T2** Migration **0021** (`validated_evidence_packages`, `evidence_trust_findings`,
  `muhasabah_records`; RLS NULLIF + ENABLE/FORCE; deterministic idempotent keys) + safe-shape
  rows (`models/layer1_durability.py`, whitelist constructors incl. the debate-record Muḥāsabah
  converter) + Postgres/InMemory twin repos (`repositories/layer1_evidence.py`) + env-gated
  Postgres roundtrip covering all three tables. **Corrected mid-review:** production-shaped
  string ids (`claim_mth_<hex>` claims, `finding-<hex>` findings) stored as VARCHAR — the
  schema accepts the ids the codebase actually produces; sanad/court/package/dashboard ids
  verified bare UUID5 and kept UUID.
- **T3** The court/VEP steps persist through the repositories (additive `muhasabah_sink` on the
  court service surfaces the governed debate's Muḥāsabah records; orchestrator persistence
  helpers; steps.py binds the selector). Idempotent on retry/resume; fail-closed
  `METHODOLOGY_LAYER1_PERSISTENCE_FAILED` blocker with a **static, ledger-safe message**
  (corrected mid-review: raw exception text never reaches step.error_message; cause chained
  for logs). Step summaries gain only the `layer1_persistence` ids/counts block.
- **T4** Durable Layer-2 reference: `_execute_layer2_ic_challenge` threads
  `accumulated["layer1_persistence"].package_ids` (null-safe) and the real
  `_run_full_layer2_ic_challenge` surfaces sorted/deduped `vep_ref_ids` in its result/ledger
  row. Proven through the REAL resume skip path (`accumulated.update(existing.result_summary)`)
  with a genuine stash-RED.
- **T5** Export visibility (DEC-E): `_vep_package` whitelist + `vep_evidence` in
  evidence_index + `vep_status`/`vep_package_count`/`vep_package_ids` in run_summary; the
  deliverables step threads the persisted block. **Corrected mid-review:** package ids are
  UUID-validated (`_is_uuid_string`) so free text can never ride in as an "id"; counts derive
  from the sanitized ids.
- **T6** Acceptance proof (`test_slice92_acceptance.py`) as above.
- **T7** Readiness doc post-Slice92 banner (frozen Slice-53 census preserved) + this
  reconciliation; pinned by `test_readiness_doc_reconciled_slice92_layer1_durability`.

Contract drifts (flipped consciously): four explicit deliverables stubs + one layer2 stub
gained optional kwargs; T1 pins flipped for migration/repos/wiring/L2-reference/export/doc.
DEC-B honored: court ordering and inputs untouched — graph/RAG/enrichment join at Layer 2
(Slice 93's own scope). Strict gates unchanged; no real LLM anywhere in tests.

---

## 1. Baseline (this worktree, verified)

- Worktree `C:\Projects\IDIS\IDIS-slice92`, branch `slice92-layer1-evidence-trust-court`, base `origin/main` @ `31bc2908f1420f737340092531ff6de0c55463f5` (post-Slice91). Imports resolve to this worktree.
- Baseline gates green: `ruff format --check`/`ruff check` clean (796) · clean-cache `mypy src/idis` **Success, 397** · L1/L2 smoke (layer2 + NFF + muhasabah + slice91 acceptance) **57 passed**.
- Zero Slice92 artifacts before this doc; dirty baseline checkout untouched.

## 2. Already built (hand-verified — reuse, do not recreate)

- **Court step IMPLEMENTED + FULL-wired** (verified via `IMPLEMENTED_STEPS`): `METHODOLOGY_EVIDENCE_TRUST_COURT` = FULL_STEPS[12], dispatched at `orchestrator.py:668` → `_execute_methodology_evidence_trust_court` (:1219); optional injected `methodology_evidence_trust_court_fn` (`RunContext` :348) with in-memory service default; resume rehydration at :538. Service `InMemoryRunMethodologyEvidenceTrustCourtService` (`services/runs/methodology_evidence_trust_court.py`) consumes materialized claims, evidence items + provenance, sanads, **sanad grades (mandatory)**, sanad defects, calcs + calc-sanads, and the full Truth Dashboard; reason-coded fail-closed rejections (13 codes incl. `MUHASABAH_GATE_REJECTED`).
- **Court record shape** (`models/evidence_trust_court_materialization.py`): `RunScopedEvidenceTrustCourtRecord` = per-claim `RunScopedClaimTrustAssessment` (disposition ∈ TRUSTED/DISPUTED/REJECTED/UNVERIFIED + evidence/sanad/grade/calc/defect ids + reason codes) + findings (PROVENANCE/SANAD_DEFECT/CONTRADICTION/DASHBOARD_CONSISTENCY/MUHASABAH_GATE) + role summaries; deterministic UUID5 `court_id`; `to_shell()` safe-resume + `to_run_step_summary()` (IDs/counts only — no claim text/transcripts by design).
- **VEP step IMPLEMENTED + FULL-wired**: `METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE` = FULL_STEPS[13]; `InMemoryRunMethodologyValidatedEvidencePackageService` packages full court records into `RunScopedValidatedEvidencePackageRecord` (deterministic `package_id`; claim_ids_by_disposition, evidence/span/sanad/defect/calc/finding ids, by_disposition/by_grade/by_finding_type aggregates; safe-summary excludes text/recommendations/GO-NO-GO).
- **"Referenced by Layer 2" exists in-memory at step 16**: `methodology_layer2_readiness_package.py:20/35/53` consumes `validated_evidence_packages` (full records). The **operational Layer 2 step** (`LAYER2_IC_CHALLENGE` = 24, `_run_full_layer2_ic_challenge` @ runs.py:1449) consumes debate summary + claim/calc ids + graph/rag/enrichment context — **no VEP reference**.
- **Layer-1 checks already enforced**: NFF (`analysis/no_free_facts.py` + `validators/no_free_facts.py` + deliverable validator, fail-closed), Muḥāsabah gate at every debate/analysis output boundary (`debate/muhasabah_gate.py`), contradiction finder role, sanad grading (A–D w/ dabt/tawātur/defect adjustments).
- **Dissent/uncertainty surfaces**: `DebateState.dissent_preserved` + `PositionSnapshot` stable-dissent (2 rounds), `MuhasabahRecordCanonical.uncertainties` (mandatory >0.80 confidence), `DissentSection` in the IC memo, `layer2 unresolved_question_count` — all in-memory in the FULL path.
- **Durable substrate + canonical patterns**: migrations 0001–0020 (next: **0021**); claims/sanads/defects (0007+0015), evidence_items (0010), spans (0004/0012), calcs+calc_sanads (0005, Slice87), vector_embeddings (0017, UPSERT exemplar), run_steps ledger (0010, `UNIQUE(tenant_id, run_id, step_name)`, result_summary JSONB); RLS `NULLIF(current_setting('idis.tenant_id', true), '')::uuid` policy pattern (0002/0020); repo pattern = Postgres + InMemory twins with `set_tenant_local`; deterministic UUID5 row ids for idempotent upsert (deliverables exemplar).

## 3. True gaps (hand-verified, with evidence)

- **G1 — Court/VEP records are not durable.** Zero `trust_court|validated_evidence` references under `src/idis/persistence/` (verified grep = 0); latest migration 0020. Full records die with the run; only `run_steps.result_summary` JSONB (IDs/counts) survives — un-queryable, un-versioned, single point of loss. Acceptance requires a **durable VEP candidate**.
- **G2 — Dissent, unresolved uncertainties, and Muḥāsabah records are not persisted anywhere in the FULL path.** They exist in-memory (court role summaries / debate state / muhasabah records on agent outputs). The `debate_sessions` table (0009) is written **only** by the API debate route (`api/routes/debate.py`) — never by the FULL DEBATE step (verified: no writer in runs.py/services).
- **G3 — Input-breadth vs ordering.** The court (12) runs **before** GRAPH_EVIDENCE (20), RAG_EVIDENCE (21), ENRICHMENT (22). The master-plan wording "from … enrichment, graph, and RAG" cannot be satisfied inside the existing court without reordering or a second pass. Note: master-plan **Slice 93** explicitly has Layer 2 "consume VEP **plus** enrichment/graph/RAG/calc context" — i.e. those sources join at Layer 2, which supports keeping the court's inputs as-is (DEC-B).
- **G4 — Layer 2 (step 24) does not reference the VEP.** `_run_full_layer2_ic_challenge` takes no VEP input; the in-memory reference at step 16 is not the operational challenge step and is not durable. Acceptance: "Layer 1 output … referenced by Layer 2."
- **G5 — No export visibility.** `product_bundle.py` contains zero court/VEP references (verified); evidence_index/run_summary carry calc/graph/rag/layer2/enrichment packages only.
- **G6 — No readiness-doc row** for the court/VEP (post-Slice53 census; convention = update banner + live-table wording, as Slice90/91 did).

## 4. Design / approach

1. **Reuse the existing court + VEP wholesale** (DEC-B(i)): no new judging engine, no reordering. Slice92 adds a **persistence boundary** after the existing steps produce their records.
2. **Migration 0021 + twin repositories** (DEC-A): persist the **safe shapes** (IDs, dispositions, grades, finding types, reason codes, aggregates — never claim text/transcripts/recommendations, matching the established safe-summary rules). Deterministic UUID5 `court_id`/`package_id` already exist → natural idempotent UPSERT keys (`UNIQUE(tenant_id, run_id, package_id)`), resume/retry-safe.
3. **Persist the scoped record set** (DEC-C): VEP candidate rows + court findings (contradictions/defect findings = the dissent-relevant Layer-1 output) + Muḥāsabah records (safe fields: agent_id, output_id, confidence, is_subjective, uncertainty/mitigation pairs, supported ids) + unresolved uncertainties (from muhasabah uncertainties + court DISPUTED/UNVERIFIED dispositions). Sourced from the court's own Layer-1 debate outputs — the operational DEBATE step (23) stays untouched.
4. **Layer-2 reference** (DEC-D): thread the durable `vep_package_ids` (+ court ids) into `LAYER2_IC_CHALLENGE` via `accumulated` (the exact Slice91 threading pattern) and record them in the layer2 result (`vep_ref_ids`) → run_steps ledger → (optionally) layer2 export package. This makes "referenced by Layer 2" durable and provable.
5. **Strict semantics** (DEC-F): with a DB connection in strict FULL, a persistence write failure fails the step closed (reason-coded blocker, consistent with the court's existing rejection style); without a DB (non-strict/dev), the InMemory twin keeps runs green. No change to existing gates.
6. **Determinism & safety:** deterministic IDs everywhere (already UUID5); no datetime.now/random in new code paths (timestamps from run context per repo convention); safe-fields-only persistence (No-Free-Facts posture); RLS on every new table.

## 5. Safety / strict boundaries

- Persist **safe shapes only** — the same exclusions the court/VEP safe summaries already enforce (no claim text, no transcripts, no Muḥāsabah narrative beyond structured uncertainty fields, no recommendations).
- RLS NULLIF pattern + tenant-scoped unique keys on every new table; fail-closed on empty tenant context.
- Existing strict gates and court rejection codes unchanged; new persistence blocker codes only per DEC-F.
- No real LLM calls in tests (deterministic court path + injected fakes); Postgres-integration tests follow the env-gated skip pattern.

## 6. Verification gate (every task)

Import proof · `pytest` (task tests + relevant regression) · `ruff format --check .` · `ruff check .` · clean-cache `mypy src/idis` · `scripts/forbidden_scan.py --repo-root .` · `git diff --check`. Closeout: full `python -m pytest -q` + independent review. Migration verified via the postgres-integration pattern (skips locally without env, runs in CI).

## 7. Risks

- **Result-summary size/shape drift** when persisting from the step path — keep persistence additive; never change existing step summaries except to add ids.
- **Resume/idempotency**: persistence must be UPSERT-idempotent (deterministic ids) so retry/resume re-runs don't duplicate rows; pin with resume tests.
- **Scope creep toward Slice 93**: Layer 2 *consumes* the VEP reference only — no advocate/challenger/arbiter work, no stage weighting, no live-provider proof (all Slice 93).
- **Ordering temptation**: do not reorder steps or feed graph/RAG/enrichment into the court (DEC-B) — pin the deferral like Slice91's extraction pin.
- **Postgres-only surfaces**: keep InMemory twins first-class so the default test suite stays hermetic.

## 8. Tasks (bite-sized, TDD)

> **Status: Tasks 1–7 ✅ complete (as-built details in §0); Task 8 = closeout.**
> Acceptance-critical: **T2–T4, T6**. Characterization: **T1**. Docs: **T7**. Closeout: **T8**.

### Task 1 — Characterization (pin current truth)
`tests/test_slice92_layer1_durability_characterization.py`: court/VEP steps implemented + in-memory only (no persistence-layer references); FULL step order (court 12 / VEP 13 before GRAPH 20 / RAG 21 / ENRICHMENT 22; LAYER2 24 after); step-16 readiness package consumes VEP in-memory; `_run_full_layer2_ic_challenge` signature has no VEP param; product bundle has no VEP package; `debate_sessions` written only by the API route; latest migration 0020. GREEN-on-arrival. Any RED → STOP + report.

### Task 2 — Migration 0021 + models + twin repositories (DEC-A/DEC-C shape)
RED-first: repo/table tests (env-gated Postgres + InMemory twin). Tables (names to confirm): `validated_evidence_packages` (safe VEP candidate row, UPSERT by deterministic package_id), `evidence_trust_findings` (court findings incl. contradiction/dissent-relevant rows), `muhasabah_records` + uncertainties (safe structured fields). RLS + unique keys per §2 conventions. No behavior change to steps yet.

### Task 3 — Persist from the court/VEP step path
RED-first: the VEP step (and court step for findings/Muḥāsabah) writes durable rows when a repository is available; idempotent on retry/resume; strict fail-closed on write failure (DEC-F); InMemory twin default keeps non-DB runs green; step summaries gain only ids/counts.

### Task 4 — Durable Layer-2 reference (acceptance bullet)
RED-first: thread `vep_package_ids` from `accumulated` into `_run_full_layer2_ic_challenge` (Slice91 threading pattern, null-safe); layer2 result records `vep_ref_ids`; pin that the reference survives in the run_steps ledger. No other Layer-2 changes (Slice 93 boundary).

### Task 5 — Export visibility (DEC-E, if confirmed)
`vep_evidence` package (safe IDs/counts) in evidence_index + run_summary counts, mirroring the layer2/rag package pattern + sanitizers.

### Task 6 — Acceptance proof
`tests/test_slice92_acceptance.py`: one orchestrated FULL run (injected fakes/in-memory repos) → durable VEP rows exist (twin repo observable), Layer 2 result references them, dissent/uncertainty/Muḥāsabah rows persisted; plus Postgres-gated integration test for the real tables.

### Task 7 — Readiness doc + plan reconciliation
Post-Slice92 banner + court/VEP row wording (frozen census preserved); flip only genuinely drifted pins.

### Task 8 — Closeout
Full `python -m pytest -q`, clean-cache mypy, ruff, forbidden scan, diff check; independent review (safety: safe-fields-only persistence + RLS; wiring: idempotency/resume/strict semantics); then closeout PR only when approved.

## 9. Decisions (LOCKED before implementation, 2026-07-04: all as recommended — DEC-A(a), DEC-B(i), DEC-C court-scoped set, DEC-D threading + vep_ref_ids, DEC-E export included, DEC-F fail-closed, DEC-G confirmed)

- **DEC-A — Durability shape.** (a, recommended) Migration **0021** + Postgres/InMemory twin repos persisting the **safe record shapes** with deterministic-UUID UPSERT idempotency; or (b) persist full records including text (rejected by default — violates the established safe-summary rules); or (c) no new tables — treat `run_steps.result_summary` as "durable enough" (rejected — un-queryable, fails the acceptance's spirit). Choose.
- **DEC-B — Input breadth / ordering.** (i, recommended) Keep the court's inputs and position as-is (methodology-phase evidence); graph/RAG/enrichment join at **Layer 2** exactly as master-plan Slice 93 specifies ("consume VEP plus enrichment/graph/RAG/calc context"); pin the deferral. (ii) Reorder the court after ENRICHMENT or add a second court pass (high blast radius — 28-step order is pinned across many suites). Choose.
- **DEC-C — Persisted record set.** VEP candidate rows (must) + court findings (contradiction/defect/provenance = the Layer-1 dissent record) + Muḥāsabah records (safe structured fields) + unresolved uncertainties (muhasabah uncertainties + DISPUTED/UNVERIFIED dispositions). Confirm the set and whether Muḥāsabah persistence covers only the court's Layer-1 debate (recommended) or also the operational DEBATE step 23 (defer — Slice 93 territory).
- **DEC-D — Layer-2 reference mechanics.** (recommended) Thread durable `vep_package_ids` into `LAYER2_IC_CHALLENGE` + record `vep_ref_ids` in its result; step-16 in-memory consumption additionally pinned. Confirm.
- **DEC-E — Export visibility.** Add a safe `vep_evidence` package to evidence_index/run_summary this slice (recommended — matches every prior package convention), or defer export to a later slice. Choose.
- **DEC-F — Strict persistence semantics.** With db_conn in strict FULL: write failure = reason-coded step blocker (fail-closed, recommended); InMemory twin keeps non-DB runs green; no changes to existing gates. Confirm.
- **DEC-G — Test boundary.** Injected fakes / deterministic court only; Postgres tests env-gated (CI postgres-integration job); no real LLM providers. Confirm.

## 10. Open questions — ANSWERED (all locked before implementation)
1. DEC-A: **confirmed** — migration 0021 + twin repos with safe shapes.
2. DEC-B: **confirmed** — court ordering/inputs untouched; graph/RAG/enrichment join at Layer 2 (Slice 93 scope).
3. DEC-C: **confirmed** — VEP + findings + Muḥāsabah + uncertainties, court-scoped only (operational DEBATE step untouched).
4. DEC-D/E: **confirmed both** — threading + `vep_ref_ids`, and export visibility landed in this slice (Task 5).
5. DEC-F: **confirmed** — fail-closed `METHODOLOGY_LAYER1_PERSISTENCE_FAILED` with a static ledger-safe message.
6. Table naming: **the three-table family** (`validated_evidence_packages` / `evidence_trust_findings` / `muhasabah_records`).
