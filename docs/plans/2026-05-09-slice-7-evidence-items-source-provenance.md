# Phase 3.0 Slice 7 EvidenceItems and Source Provenance Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Convert Slice 6 run-scoped materialized claims into governed in-memory `EvidenceItem` and source-provenance records, attach them to the run context, and persist only safe run-step summaries.

**Architecture:** Slice 7 adds a FULL-only run step after `METHODOLOGY_CLAIM_MATERIALIZATION` and before legacy `EXTRACT`. It reuses the existing `EvidenceItem`, evidence repository concepts, and Slice 6 safe source-reference models, but it does not create Sanads, Truth Dashboard artifacts, CALC outputs, enrichment checks, Layer 1/Layer 2 debate outputs, recommendations, deliverables, or real E2E. Durable Postgres evidence persistence remains deferred until durable Claim Registry persistence or a schema-compatible run-scoped evidence table exists, because the current `evidence_items` migration expects UUID `claim_id` and UUID `source_span_id` while Slice 6 claims are run-scoped `claim_mth_*` IDs.

**Tech Stack:** Python 3.13, Pydantic v2, existing IDIS run orchestrator, existing `EvidenceItem` model, existing evidence repository interfaces, pytest, ruff, mypy.

---

## Starting Point

Worktree:

`C:\Users\baram\.config\superpowers\worktrees\IDIS\phase-3-0g-evidence-items-source-provenance`

Branch:

`phase-3-0g-evidence-items-source-provenance`

Base:

`origin/main` at Slice 6 merge commit `a26addbf73ee8a5bde5460f883f4361ed4a7f2c6`.

Plan status:

- Approved for TDD implementation after the clarification decisions below are incorporated.
- Do not start Slice 8.

## Mandatory Inventory and Reuse Decisions

### Existing Code Inventory

Read and reuse where semantically safe:

- `src/idis/models/evidence_item.py`
  - Existing `EvidenceItem`, `VerificationStatus`, `SourceGrade`, and `SourceSubgrade`.
  - Has deterministic helpers `to_canonical_dict()` and `stable_hash()`.
  - Accepts optional `source_span_id`, `source_system`, `upstream_origin_id`, `rationale`, and timestamps.

- `schemas/evidence_item.schema.json`
  - Existing schema expects `evidence_id`, `tenant_id`, `deal_id`, `source_grade`, and `verification_status`.
  - Describes `evidence_id` and `source_span_id` as UUID format.

- `src/idis/persistence/repositories/evidence.py`
  - Existing `EvidenceRepo` protocol.
  - Existing `PostgresEvidenceRepository`.
  - Existing `get_evidence_repository()` factory.

- `src/idis/persistence/repositories/claims.py`
  - Existing `InMemoryEvidenceRepository`.
  - Existing evidence seed/store helpers.

- `src/idis/persistence/migrations/versions/0010_run_steps_evidence_items.py`
  - Existing `evidence_items` table.
  - Table has `evidence_id UUID`, `claim_id UUID`, `source_span_id UUID`, `source_grade`, `created_at`, tenant RLS, and indexes.

- Existing tests:
  - `tests/test_evidence_item_model.py`
  - `tests/test_full_system_wiring_baseline.py`
  - Postgres evidence/RLS coverage appears through broader Postgres tests that exercise `evidence_items`.
  - Sanad tests use evidence-shaped dicts, but Slice 7 must not invoke Sanad creation or grading.

- Existing consumers that must remain untouched in Slice 7:
  - `src/idis/services/sanad/auto_grade.py`
  - `src/idis/services/sanad/chain_builder.py`
  - `src/idis/services/methodology/sanad_creation_boundary_support.py`
  - `src/idis/deliverables/truth_dashboard.py`

### Reuse Decisions

- Reuse `EvidenceItem` as the canonical evidence item payload model.
- Reuse `VerificationStatus.UNVERIFIED` for newly materialized run evidence.
- Reuse `SourceGrade.D` as the conservative default source grade. This is not evidence grading; Slice 7 does not grade evidence.
- Reuse `EvidenceItem.to_canonical_dict()` and `stable_hash()` in tests and deterministic summaries where useful.
- Reuse Slice 6 `MaterializedClaimSourceRef` validation. Do not create a second source-ref validator.
- Reuse Slice 6 `RunScopedMaterializedClaim` and `RunScopedMaterializedClaimShell` as inputs.
- Reuse existing run-step patterns from `METHODOLOGY_CLAIM_MATERIALIZATION`.
- Reuse existing run-step repository persistence for safe summaries.

### Non-Reuse Decisions and Rationale

- Do not use `PostgresEvidenceRepository.create()` in Slice 7 execution.
  - Current Postgres schema expects UUID `claim_id` and UUID `source_span_id`.
  - Slice 6 claim IDs are deterministic run-scoped strings like `claim_mth_*`, not durable Claim Registry UUIDs.
  - Slice 6 safe source refs may be non-UUID safe identifiers.
  - Forcing these through Postgres would either fail or encourage premature durable Claim Registry persistence.

- Do not use `InMemoryEvidenceRepository.create()` as the service's deterministic core.
  - It stamps `created_at` with current time.
  - It stores only `claim_id`, `source_span_id`, source grade, status, and timestamps.
  - It does not preserve `document_id`, locator-safe provenance, methodology linkage, or extraction output linkage.
  - It can be used later as an adapter only after deterministic semantics are defined.

- Do not reuse `evidence_items_from_references()` from `sanad_creation_boundary_support.py`.
  - It is tied to synthetic Sanad creation boundary references and would pull Slice 7 toward Slice 8 concerns.
  - Slice 7 consumes Slice 6 materialized claims, not Sanad creation decisions.

- Do not reuse `auto_grade_claims_for_run()` or `build_sanad_chain()`.
  - Both are Sanad/grade flows and are strict non-scope.

## Slice 7 Scope

Slice 7 must:

- Consume `RunContext.methodology_materialized_claims`.
- Accept both full `RunScopedMaterializedClaim` objects and safe `RunScopedMaterializedClaimShell` objects.
- Create governed in-memory `EvidenceItem` records for every safe `source_ref` on each materialized claim.
- Create deterministic source-provenance mappings linking:
  - `tenant_id`
  - `deal_id`
  - `run_id`
  - `claim_id`
  - `evidence_id`
  - `document_id`
  - `source_span_id`
  - `methodology_question_id`
  - `coverage_record_id`
  - `extraction_task_id`
  - `extraction_output_id`
- Attach evidence items and provenance shells to `RunContext`.
- Produce safe run-step summaries only.
- Support resume by rehydrating safe shells from run-step summaries.
- Generate deterministic, idempotent evidence IDs.

Slice 7 must not:

- Create, link, or grade Sanads.
- Build Truth Dashboard artifacts.
- Run deterministic CALC.
- Run enrichment or API conflict checks.
- Run Layer 1 Evidence Trust Court.
- Run Layer 2 IC Debate.
- Produce GO, CONDITIONAL, or NO-GO.
- Produce deliverables.
- Access `real_example/`.
- Run real data-room E2E.
- Start Slice 8.

## Proposed Step Placement

Add a new FULL-only step:

`METHODOLOGY_EVIDENCE_ITEM_MATERIALIZATION`

Order:

1. `METHODOLOGY_EXTRACTION_TASK_PLANNING`
2. `METHODOLOGY_EXTRACTION_TASK_EXECUTION`
3. `METHODOLOGY_CLAIM_MATERIALIZATION`
4. `METHODOLOGY_EVIDENCE_ITEM_MATERIALIZATION`
5. legacy `EXTRACT`

Implementation files:

- Modify `src/idis/models/run_step.py`
  - Add `StepName.METHODOLOGY_EVIDENCE_ITEM_MATERIALIZATION`.
  - Insert it after `METHODOLOGY_CLAIM_MATERIALIZATION`.
  - Shift later FULL step orders by one.
  - Add it to `FULL_STEPS`, `FULL_ONLY_STEPS`, and `IMPLEMENTED_STEPS`.
  - Keep it out of `SNAPSHOT_STEPS`.

- Modify `src/idis/services/runs/orchestrator.py`
  - Add `RunContext.methodology_evidence_items`.
  - Add `RunContext.methodology_evidence_source_provenance`.
  - Add optional injection callable `methodology_evidence_item_materialization_fn`.
  - Dispatch the new step.
  - Fail closed if materialized claims are missing.
  - Rehydrate evidence/provenance shells on resume.

- Modify `src/idis/services/runs/steps.py`
  - Add the optional callable to `build_run_context()`.

## Data Model Plan

Use existing `EvidenceItem` as the evidence payload. Add only Slice 7 boundary/mapping models that existing code does not provide.

Suggested new file:

`src/idis/models/evidence_item_materialization.py`

New models:

- `RunScopedEvidenceProvenanceRef`
  - Safe source provenance for one evidence item.
  - Fields: `document_id`, `source_span_id`, `locator`.
  - Must reuse or wrap `MaterializedClaimSourceRef`; do not create a duplicate source-ref safety validator.

- `RunScopedEvidenceItemRecord`
  - In-memory governed boundary around existing `EvidenceItem`.
  - Fields: `tenant_id`, `deal_id`, `run_id`, `claim_id`, `evidence_item`, `source_ref`, `methodology_question_id`, `coverage_record_id`, `extraction_task_id`, `extraction_output_id`, `status`.
  - This is not a durable Postgres `evidence_items` record.

- `RunScopedEvidenceItemShell`
  - Safe resume shell.
  - Fields: `tenant_id`, `deal_id`, `run_id`, `claim_id`, `evidence_id`, `document_id`, `source_span_id`, `methodology_question_id`, `coverage_record_id`, `extraction_task_id`, `extraction_output_id`, `status`.
  - No raw text.
  - No locator unless proven summary-safe; prefer no locator in summary.

- `MethodologyEvidenceItemMapping`
  - Summary-safe mapping from claim/source ref to evidence ID.
  - Fields: `claim_id`, `evidence_id`, `methodology_question_id`, `coverage_record_id`, `extraction_task_id`, `extraction_output_id`, `document_id`, `source_span_id`.

- `MethodologyEvidenceItemRejection`
  - Stable reason-coded rejection.

- `MethodologyEvidenceItemMaterializationSummary`
  - Counts only: total claims, total source refs, created evidence count, rejected source ref count, by status, by reason.

- `MethodologyEvidenceItemMaterializationRunResult`
  - Run-step-safe result with `to_run_step_summary()`.

Reason codes must be lowercase snake_case, matching Slice 6 style:

- `missing_materialized_claims`
- `malformed_materialized_claim`
- `missing_claim_id`
- `missing_source_refs`
- `unsafe_source_ref`
- `duplicate_claim_source_ref`
- `tenant_or_run_mismatch`

Deterministic evidence ID:

- Use a stable project-local UUID namespace plus UUID v5 to satisfy existing evidence schema expectations for `evidence_id`.
- Seed UUID v5 with canonical JSON containing:
  - `tenant_id`
  - `deal_id`
  - `run_id`
  - `claim_id`
  - `extraction_output_id`
  - `extraction_task_id`
  - `methodology_question_id`
  - `coverage_record_id`
  - `document_id`
  - `source_span_id`
- Return string UUID.
- Do not use random UUIDs.
- Do not invent UUIDs for source spans.

EvidenceItem payload defaults:

- `evidence_id`: deterministic UUID v5.
- `tenant_id`: from claim/run context.
- `deal_id`: from claim/run context.
- `source_span_id`: if the Slice 6 safe `source_span_id` is a UUID, copy it into `EvidenceItem.source_span_id`; if it is non-UUID but safe, set `EvidenceItem.source_span_id = None`.
- `source_system`: `methodology_claim_materialization`
- `upstream_origin_id`: safe source span ID.
- `verification_status`: `VerificationStatus.UNVERIFIED`
- `source_grade`: `SourceGrade.D` as a conservative ungraded default, not actual evidence grading.
- `rationale`: safe structured metadata only:
  - `claim_id`
  - `run_id`
  - `methodology_question_id`
  - `coverage_record_id`
  - `extraction_task_id`
  - `extraction_output_id`
  - `document_id`
  - `source_span_id`
  - `source`: `slice_7_methodology_source_provenance`
- `created_at` and `updated_at`: leave unset for deterministic in-memory records.
- Always preserve the safe original `source_span_id` in the run-scoped provenance wrapper and summary mapping.
- Do not reject safe non-UUID source refs just because Postgres cannot persist them yet.

## Service Plan

Suggested new file:

`src/idis/services/runs/methodology_evidence_item_materialization.py`

Suggested service:

`InMemoryRunMethodologyEvidenceItemMaterializationService`

Signature:

```python
def run(
    self,
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    materialized_claims: list[RunScopedMaterializedClaim | RunScopedMaterializedClaimShell],
) -> tuple[
    MethodologyEvidenceItemMaterializationRunResult,
    list[RunScopedEvidenceItemRecord],
]:
    ...
```

Behavior:

- Sort claims by `claim_id`, `extraction_output_id`, `extraction_task_id`, `methodology_question_id`.
- Validate all claim shells match `tenant_id`, `deal_id`, and `run_id`.
- Reject claim records without `claim_id`.
- Reject claim records with no `source_refs`.
- For each source ref:
  - Reuse `MaterializedClaimSourceRef` validation.
  - Generate deterministic evidence ID.
  - Create `EvidenceItem`.
  - Create `RunScopedEvidenceItemRecord`.
  - Create summary-safe mapping.
- Deduplicate by `(claim_id, document_id, source_span_id, extraction_output_id)`.
- Duplicate source refs should not create duplicate evidence items.
- Rejections should be stable and reason-coded.
- Do not call Sanad services.
- Do not call `PostgresEvidenceRepository`.
- Do not call `InMemoryEvidenceRepository.create()`.
- Do not mutate coverage records.
- Do not create durable claims.
- Do not call Truth Dashboard, CALC, enrichment, debate, or deliverable services.

## Safe Summary Rules

`to_run_step_summary()` may include:

- `status`
- `evidence_ids`
- `claim_ids`
- `evidence_item_mappings` with safe `document_id`, `source_span_id`, `claim_id`, `evidence_id`, `extraction_task_id`, `extraction_output_id`, `methodology_question_id`, and `coverage_record_id`
- `rejected_source_refs` with reason codes
- `summary.total_claims`
- `summary.total_source_refs`
- `summary.created_evidence_count`
- `summary.rejected_source_ref_count`
- `summary.by_status`
- `summary.by_reason`

`to_run_step_summary()` must not include:

- raw span text
- claim text
- answer/value payloads
- `value_struct`
- raw locator fields
- `document_name`
- filesystem paths
- URIs
- `text`
- `raw_text`
- `text_excerpt`
- `path`
- `uri`
- Sanad data
- Truth Dashboard data
- Layer 1/Layer 2 data
- enrichment/API payloads
- CALC outputs
- deliverable content

If source metadata is unsafe, reject before summary creation.

## Resume Behavior

When `METHODOLOGY_EVIDENCE_ITEM_MATERIALIZATION` is already completed:

- Do not rerun the materialization service.
- Rehydrate `RunScopedEvidenceItemShell` records from the persisted run-step summary.
- Rehydrate enough provenance shell data for later Slice 8 planning to know evidence IDs and safe source refs.
- Do not rehydrate raw text or full answer/value data.

Empty materialized claims behavior:

- The service may return `COMPLETED` as a diagnostic no-op for an explicit empty materialized-claims list.
- The orchestrator must fail/block only when the prior claim materialization output/context is missing entirely.

## Audit Wording Plan

Update `scripts/audit_full_system_wiring.py` and `tests/test_full_system_wiring_baseline.py`.

Audit should say:

- Slice 7 has an in-memory governed EvidenceItem/source-provenance boundary.
- Durable Postgres evidence persistence remains deferred because durable Claim Registry persistence remains deferred and current evidence table expects UUID claim/source IDs.
- Sanad creation/linking/grading remains deferred to Slice 8.
- Truth Dashboard remains deferred.
- CALC remains deferred.
- enrichment/API checks remain deferred.
- Layer 1 Evidence Trust Court remains deferred.
- Layer 2 IC Debate remains deferred.
- GO/CONDITIONAL/NO-GO package remains deferred.
- deliverables remain deferred.
- real `real_example/` data-room E2E remains deferred.

## TDD Task Plan

### Task 1: Model Tests for EvidenceItem Reuse Boundary

**Files:**

- Create: `tests/test_run_evidence_item_materialization_models.py`
- Modify later: `src/idis/models/evidence_item_materialization.py`

**Step 1: Write failing tests**

Test cases:

- Inventory test proves `EvidenceItem`, `SourceGrade`, and `VerificationStatus` are reused.
- Deterministic evidence ID is stable for the same claim/source ref.
- Deterministic evidence ID changes when source ref changes.
- Evidence item defaults are conservative: `UNVERIFIED`, grade `D`, no timestamps.
- Unsafe source refs are rejected by reusing `MaterializedClaimSourceRef`.
- Summary shell does not accept raw text/path/URI fields.

**Step 2: Run tests to verify failure**

Run:

`py -3.13 -m pytest -q tests/test_run_evidence_item_materialization_models.py`

Expected:

Fails because Slice 7 models/functions do not exist.

**Step 3: Implement minimal models**

Implement `src/idis/models/evidence_item_materialization.py`.

**Step 4: Re-run**

Run:

`py -3.13 -m pytest -q tests/test_run_evidence_item_materialization_models.py`

Expected:

Pass.

### Task 2: Service Tests for Claim-to-Evidence Materialization

**Files:**

- Create: `tests/test_run_evidence_item_materialization_service.py`
- Modify later: `src/idis/services/runs/methodology_evidence_item_materialization.py`

**Step 1: Write failing tests**

Test cases:

- One `RunScopedMaterializedClaim` with one source ref creates one `EvidenceItem`.
- One claim with multiple source refs creates deterministic evidence per source ref.
- Duplicate claim/source refs do not create duplicate evidence items.
- `RunScopedMaterializedClaimShell` input works.
- Explicit empty materialized claims list returns a completed diagnostic no-op.
- Missing prior claim materialization context blocks/fails in the orchestrator.
- Missing `claim_id` rejects.
- Tenant/deal/run mismatch rejects.
- Unsafe source ref rejects and does not leak metadata.
- `to_run_step_summary()` excludes raw text, claim text, value structs, locators, paths, URIs, document names, Sanad fields, dashboard fields, CALC fields, enrichment fields, and deliverables.

**Step 2: Run tests to verify failure**

Run:

`py -3.13 -m pytest -q tests/test_run_evidence_item_materialization_service.py`

Expected:

Fails because service does not exist.

**Step 3: Implement service**

Implement deterministic in-memory materialization only.

**Step 4: Re-run**

Run:

`py -3.13 -m pytest -q tests/test_run_evidence_item_materialization_service.py`

Expected:

Pass.

### Task 3: Orchestrator Wiring Tests

**Files:**

- Create: `tests/test_run_orchestrator_evidence_item_materialization.py`
- Modify later:
  - `src/idis/models/run_step.py`
  - `src/idis/services/runs/orchestrator.py`
  - `src/idis/services/runs/steps.py`
  - existing orchestrator step count tests

**Step 1: Write failing tests**

Test cases:

- FULL step order places `METHODOLOGY_EVIDENCE_ITEM_MATERIALIZATION` after `METHODOLOGY_CLAIM_MATERIALIZATION` and before `EXTRACT`.
- Step is FULL-only, not SNAPSHOT.
- Successful FULL run attaches evidence items/provenance to `RunContext`.
- Run-step summary contains evidence IDs and safe counts only.
- Missing materialized claims fails/blocks the evidence step cleanly.
- Resume skips completed evidence materialization and rehydrates evidence shells.
- No Sanad, Truth Dashboard, CALC, enrichment, Layer 1, Layer 2, deliverable, or real E2E outputs are produced by this step.

**Step 2: Run tests to verify failure**

Run:

`py -3.13 -m pytest -q tests/test_run_orchestrator_evidence_item_materialization.py`

Expected:

Fails because the step is not wired.

**Step 3: Implement wiring**

Update run-step enum/order/context/dispatch/resume.

**Step 4: Update affected step-count regression tests**

Existing FULL run tests will need count updates from 14 to 15.

Likely files:

- `tests/test_run_orchestrator_debate_step.py`
- `tests/test_run_orchestrator_new_steps.py`
- `tests/test_run_orchestrator_steps.py`

**Step 5: Re-run focused orchestrator tests**

Run:

`py -3.13 -m pytest -q tests/test_run_orchestrator_evidence_item_materialization.py tests/test_run_orchestrator_methodology_claim_materialization.py tests/test_run_orchestrator_new_steps.py tests/test_run_orchestrator_debate_step.py tests/test_run_orchestrator_steps.py`

Expected:

Pass.

### Task 4: Audit Truthfulness Tests

**Files:**

- Modify: `scripts/audit_full_system_wiring.py`
- Modify: `tests/test_full_system_wiring_baseline.py`

**Step 1: Write failing audit test**

Test should assert:

- EvidenceItem/source-provenance boundary is `PARTIAL`.
- In-memory governed EvidenceItem/source-provenance boundary exists.
- Durable Postgres evidence persistence remains deferred.
- Sanads remain deferred.
- Truth Dashboard remains deferred.
- CALC remains deferred.
- enrichment/API checks remain deferred.
- Layer 1 remains deferred.
- Layer 2 remains deferred.
- deliverables remain deferred.
- real E2E remains deferred.

**Step 2: Run test to verify failure**

Run:

`py -3.13 -m pytest -q tests/test_full_system_wiring_baseline.py`

Expected:

Fails until audit script is updated.

**Step 3: Update audit script**

Add Slice 7 checks for:

- model file
- service file
- run-step enum/wiring
- safe summary method
- explicit deferrals

**Step 4: Re-run audit test**

Run:

`py -3.13 -m pytest -q tests/test_full_system_wiring_baseline.py`

Expected:

Pass.

### Task 5: Repository Compatibility Tests

**Files:**

- Create or extend: `tests/test_run_evidence_item_materialization_service.py`
- Possibly extend: `tests/test_evidence_item_model.py`

**Step 1: Add tests documenting repository reuse limits**

Test should document:

- `EvidenceItem` is reused directly.
- `PostgresEvidenceRepository` is not used by Slice 7 core because it expects UUID durable claim/source IDs.
- `InMemoryEvidenceRepository.create()` is not used by deterministic service core because it stamps current time and drops provenance.

**Step 2: Run tests**

Run:

`py -3.13 -m pytest -q tests/test_run_evidence_item_materialization_service.py tests/test_evidence_item_model.py`

Expected:

Pass after implementation.

## Full Validation Commands

After Slice 7 implementation:

```powershell
python scripts/forbidden_scan.py
py -3.13 -m ruff format --check .
py -3.13 -m ruff check .
py -3.13 -m mypy src/idis --ignore-missing-imports
py -3.13 -m pytest -q tests/test_run_evidence_item_materialization_models.py tests/test_run_evidence_item_materialization_service.py tests/test_run_orchestrator_evidence_item_materialization.py tests/test_run_orchestrator_methodology_claim_materialization.py tests/test_full_system_wiring_baseline.py
pytest -q
python scripts/run_postgres_integration_local.py
```

## PR Prep Checklist

Before opening Slice 7 PR:

- Confirm no `real_example/` files.
- Confirm no `financial Due Diligence.xlsx`.
- Confirm no `.local_reports/`.
- Confirm no `.quarantine_real_example_removed/`.
- Confirm no `.tmp_gdbs_*`.
- Confirm no generated reports.
- Confirm no Sanad creation/linking/grading.
- Confirm no Truth Dashboard.
- Confirm no CALC.
- Confirm no enrichment/API checks.
- Confirm no Layer 1/Layer 2 outputs.
- Confirm no GO/CONDITIONAL/NO-GO.
- Confirm no deliverables.
- Confirm no Slice 8 files or behavior.

## Review Decisions Incorporated

1. `EvidenceItem.source_span_id`: copy only UUID source-span IDs; set `EvidenceItem.source_span_id = None` for safe non-UUID refs; always keep the original safe source-span ID in provenance/mapping; never invent source-span UUIDs.
2. Safe summaries: include safe IDs, counts, statuses, and reason codes; exclude locator, raw text, claim text, value structs, document names, paths, URIs, and answer payloads.
3. Empty materialized claims: explicit empty input is a completed diagnostic no-op; missing prior claim materialization context blocks/fails in the orchestrator.

## Stop Condition

Stop after implementation and validation. Do not start PR prep unless explicitly asked.
