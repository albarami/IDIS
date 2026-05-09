# Phase 3.0 Slice 8 Sanad Creation Linking Grading Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Convert Slice 6 run-scoped materialized claims plus Slice 7 EvidenceItems/source provenance into deterministic run-scoped Sanad, claim-link, grade, and defect outputs.

**Architecture:** Add a FULL-only run step after `METHODOLOGY_EVIDENCE_ITEM_MATERIALIZATION` and before legacy `EXTRACT`. Reuse existing Sanad, TransmissionNode, Defect, and grading concepts, but add a deterministic run-scoped adapter because existing creation paths mint random IDs/timestamps, persist through durable repositories, or are explicitly synthetic Phase 2.x boundaries.

**Tech Stack:** Python 3.13, Pydantic v2, existing IDIS run orchestrator, existing Sanad/Defect/TransmissionNode models, existing deterministic Sanad grader, pytest, ruff, mypy.

---

## Starting Point

- Worktree: `C:\Users\baram\.config\superpowers\worktrees\IDIS\phase-3-0h-sanad-creation-linking-grading`
- Branch: `phase-3-0h-sanad-creation-linking-grading`
- Base: `origin/main` at Slice 7 merge commit `e1be1bb62cf65fcf6edf586fdfa353539b68af21`
- Status: planning only. Do not implement Slice 8 until reviewed. Do not start Slice 9.

## Inventory And Reuse Decisions

### Reuse Directly

- `src/idis/models/sanad.py`
  - Reuse `Sanad`, `SanadGrade`, `CorroborationStatus`, `to_canonical_dict()`, and `stable_hash()`.
  - `Sanad` accepts string `claim_id`, so it can wrap `claim_mth_*` in memory.

- `src/idis/models/transmission_node.py`
  - Reuse `TransmissionNode`, `NodeType`, `ActorType`, `VerificationMethod`, and stable hashing.
  - Slice 8 must supply deterministic `node_id` and deterministic timestamps.
  - Internal `input_refs` / `output_refs` must be safe ID-only metadata.

- `src/idis/models/defect.py`
  - Reuse `Defect`, `DefectType`, `DefectSeverity`, `CureProtocol`, and `DefectStatus`.
  - Internal defects may contain short deterministic descriptions because the model requires `description`.
  - Persisted run-step summaries must exclude defect descriptions.

- `src/idis/services/sanad/grader.py`
  - Reuse `grade_sanad_v2()` / `calculate_sanad_grade()` as the single in-memory grading source.
  - Do not use `SanadService._compute_grade()`.

- Slice 6/7 inputs:
  - `RunScopedMaterializedClaim` / `RunScopedMaterializedClaimShell`
  - `RunScopedEvidenceItemRecord` / `RunScopedEvidenceItemShell`
  - `RunScopedEvidenceProvenanceRef`
  - `MaterializedClaimSourceRef` safety validation already applied upstream.

### Reuse As Reference, Not Core Execution

- `src/idis/services/sanad/service.py`
  - Useful API and validation reference.
  - Do not call `SanadService.create()` in Slice 8 core because it generates non-deterministic IDs/timestamps and persists through repositories.
  - Do not use `SanadService._compute_grade()` for Slice 8 grading.

- `src/idis/services/sanad/chain_builder.py`
  - Reuse INGEST -> EXTRACT -> optional NORMALIZE chain shape and fail-closed behavior.
  - Do not call directly because it uses random UUIDs and current timestamps.

- `src/idis/services/sanad/auto_grade.py`
  - Useful snapshot-run reference.
  - Do not call in Slice 8 FULL-run core because it reads repositories, persists Sanads/defects, and updates claims.

- `src/idis/models/sanad_coverage_boundary.py` and `src/idis/services/methodology/sanad_coverage_boundary.py`
  - Reuse readiness and scope concepts where helpful.
  - Do not mutate coverage in Slice 8.

- `src/idis/models/sanad_creation_boundary.py` and `src/idis/services/methodology/sanad_creation_boundary.py`
  - Reuse mapping/rejection vocabulary where semantically correct.
  - Do not call the service because it is synthetic-only and delegates to `SanadService.create()`.

- `src/idis/models/claim_sanad_link_boundary.py` and `src/idis/services/methodology/claim_sanad_link_boundary.py`
  - Reuse non-IC promotion constraints.
  - Do not call `apply_claim_sanad_links()` because it updates through `ClaimService`.

### Persistence Decision

Slice 8 remains in memory and persists only safe run-step summaries.

Do not use Postgres `sanads`, `defects`, `claims`, or `evidence_items` tables in this slice:

- `0007_claims_sanad_defects_tables.py` defines `claims.claim_id UUID` and `sanads.claim_id UUID REFERENCES claims(claim_id)`.
- `0010_run_steps_evidence_items.py` defines `evidence_items.claim_id UUID` and `source_span_id UUID`.
- Slice 6 claims are deterministic `claim_mth_*` strings and Slice 7 source spans may be safe non-UUID identifiers.

## Slice 8 Scope

Slice 8 must:

- Consume `RunContext.methodology_materialized_claims`.
- Consume `RunContext.methodology_evidence_items`.
- Consume `RunContext.methodology_evidence_source_provenance`.
- Accept full records and safe resume shells from Slice 6/7.
- Create deterministic run-scoped `Sanad` records for claims with matching evidence.
- Create deterministic run-scoped claim-to-Sanad link records.
- Grade each run-scoped Sanad and preserve structured defects.
- Attach Sanad/link/grade/defect outputs to `RunContext`.
- Persist only safe run-step summaries.
- Keep all IDs deterministic and idempotent.
- Fail closed for missing input, scope mismatch, duplicate conflicts, malformed chains, and grading failures.

Slice 8 must not:

- Build Truth Dashboard artifacts.
- Run deterministic FDD CALC.
- Run enrichment/API conflict checks.
- Run Layer 1 Evidence Trust Court.
- Create a Validated Evidence Package.
- Run Layer 2 IC Debate.
- Produce GO/CONDITIONAL/NO-GO.
- Produce deliverables.
- Access `real_example/`.
- Run real data-room E2E.
- Mutate methodology coverage.
- Promote claims to IC-ready status.
- Start Slice 9.

## Step Placement

Add FULL-only step:

`METHODOLOGY_SANAD_CREATION_LINKING_GRADING`

Order:

1. `METHODOLOGY_EXTRACTION_TASK_PLANNING`
2. `METHODOLOGY_EXTRACTION_TASK_EXECUTION`
3. `METHODOLOGY_CLAIM_MATERIALIZATION`
4. `METHODOLOGY_EVIDENCE_ITEM_MATERIALIZATION`
5. `METHODOLOGY_SANAD_CREATION_LINKING_GRADING`
6. legacy `EXTRACT`

Implementation files:

- Modify `src/idis/models/run_step.py`.
- Modify `src/idis/services/runs/orchestrator.py`.
- Modify `src/idis/services/runs/steps.py`.

`RunContext` should gain:

- `methodology_sanads`
- `methodology_sanad_links`
- `methodology_sanad_grades`
- `methodology_sanad_defects`
- optional `methodology_sanad_creation_linking_grading_fn`

## Data Model Plan

Create `src/idis/models/sanad_materialization.py`.

New models should wrap existing payloads instead of duplicating them:

- `RunScopedSanadRecord`: wraps existing `Sanad` with tenant/deal/run/claim/task linkage.
- `RunScopedSanadShell`: safe resume shell with IDs, grade, counts, node types, and defect IDs only.
- `RunScopedSanadLinkRecord`: run-scoped claim-to-Sanad link with non-IC promotion status.
- `RunScopedSanadGradeRecord`: grade letter, reason codes, defect IDs, defect severity counts.
- `RunScopedSanadDefectRecord`: wraps existing `Defect`.
- `RunScopedSanadDefectShell`: defect ID/type/severity/cure/status only.
- `MethodologySanadMapping`: summary-safe claim/evidence/Sanad mapping.
- `MethodologySanadRejection`: stable reason-coded rejection.
- `MethodologySanadMaterializationSummary`: counts by status/reason/grade/severity.
- `MethodologySanadMaterializationRunResult`: run-step-safe result with `to_run_step_summary()`.

Reason-code values should be lowercase snake_case:

- `missing_materialized_claims`
- `missing_evidence_items`
- `missing_source_provenance`
- `tenant_or_run_mismatch`
- `missing_claim_id`
- `missing_claim_evidence`
- `malformed_evidence_item`
- `duplicate_claim_sanad_input`
- `chain_build_failed`
- `sanad_validation_failed`
- `grading_failed`
- `defect_materialization_failed`
- `claim_link_failed`

Deterministic IDs:

- Use stable project-local UUID namespaces plus UUID v5.
- `sanad_id` seed: tenant/deal/run, claim ID, sorted evidence IDs, sorted source span IDs, extraction output/task/question/coverage IDs.
- `node_id` seed: sanad ID, node type, ordinal, input refs, output refs.
- `defect_id` seed: sanad ID, claim ID, defect code/type, severity, cure protocol, safe evidence IDs.
- Do not use random UUIDs.
- Do not use current time for deterministic chain nodes; use a fixed synthetic timestamp sequence such as epoch plus ordinal seconds.
- All IDs and timestamps for reused `Sanad`, `TransmissionNode`, and `Defect` payloads must be supplied by the Slice 8 adapter.

## Service Plan

Create `src/idis/services/runs/methodology_sanad_creation_linking_grading.py`.

Service:

`InMemoryRunMethodologySanadCreationLinkingGradingService`

Signature:

```python
def run(
    self,
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    materialized_claims: list[RunScopedMaterializedClaim | RunScopedMaterializedClaimShell],
    evidence_items: list[RunScopedEvidenceItemRecord | RunScopedEvidenceItemShell],
    source_provenance: list[RunScopedEvidenceProvenanceRef],
) -> tuple[
    MethodologySanadMaterializationRunResult,
    list[RunScopedSanadRecord],
    list[RunScopedSanadLinkRecord],
    list[RunScopedSanadGradeRecord],
    list[RunScopedSanadDefectRecord],
]:
    ...
```

Behavior:

- Sort claims by `claim_id`, `extraction_output_id`, `extraction_task_id`, `methodology_question_id`.
- Validate claim, evidence, and provenance scope.
- Group evidence by `claim_id`.
- Reject claims without `claim_id` or matching evidence.
- Deduplicate by `(claim_id, sorted evidence_ids, extraction_output_id)`.
- Build deterministic `TransmissionNode` chain: INGEST -> EXTRACT, plus NORMALIZE only when explicit upstream metadata says deduped.
- Keep `TransmissionNode.input_refs` and `output_refs` to safe ID-only metadata: no raw text, claim text, value structs, locators, document names, paths, URIs, or full evidence rationale.
- Build existing `Sanad` payload with deterministic `sanad_id`, run-scoped `claim_id`, evidence IDs, grade, defects, and deterministic chain.
- Use `grade_sanad_v2()` / `calculate_sanad_grade()` as the only grading source.
- `grade_explanation` may exist on the in-memory `Sanad`; run-step summaries must include only grade letter, reason codes, defect IDs, and counts.
- Map grader defect summaries carefully to existing `DefectType` values. If a grader defect code cannot be safely mapped, reject with `defect_materialization_failed` rather than inventing a new defect type.
- Create run-scoped claim link only; do not mutate a claim service.

Must not call:

- `SanadService.create()`
- `DefectService`
- `ClaimService`
- Postgres repositories
- Truth Dashboard
- CALC
- enrichment/API services
- debate services
- Validated Evidence Package services
- deliverable services

## Safe Summary Rules

`to_run_step_summary()` may include:

- `status`
- `sanad_ids`
- `claim_ids`
- `evidence_ids`
- `source_span_ids`
- `sanad_mappings` with safe IDs only
- `claim_sanad_links` with safe IDs and non-IC promotion state
- `grade_records` with grade letters, defect IDs, defect counts, and reason codes
- `defect_shells` with defect ID/type/severity/cure protocol/status only
- rejections with reason codes
- counts by status, reason, grade, and defect severity

`to_run_step_summary()` must not include:

- raw span text
- claim text
- answer/value payloads
- `value_struct`
- source locators
- document names
- filesystem paths
- URIs
- full evidence rationale payloads
- full transmission-chain refs
- transmission-chain raw payloads
- defect descriptions
- verbose grade explanations
- Truth Dashboard, CALC, enrichment/API, Layer 1, Layer 2, Validated Evidence Package, GO/CONDITIONAL/NO-GO, or deliverable data

## Resume Behavior

When `METHODOLOGY_SANAD_CREATION_LINKING_GRADING` is already completed:

- Do not rerun the service.
- Rehydrate `RunScopedSanadShell`, link shells, grade shells, and defect shells from persisted summary.
- Do not rehydrate raw text, claim payloads, value structs, full chain refs, defect descriptions, or verbose grade explanations.

Missing-input behavior:

- Service may return completed diagnostic no-op for explicit empty claim/evidence lists.
- Orchestrator must block/fail if prior claim materialization context is missing.
- Orchestrator must block/fail if prior evidence materialization context is missing.

## Audit Wording Plan

Update `scripts/audit_full_system_wiring.py` and `tests/test_full_system_wiring_baseline.py`.

Audit should say:

- Slice 8 has an in-memory governed Sanad creation/linking/grading boundary.
- Run-scoped Sanads are created from Slice 6 materialized claims and Slice 7 EvidenceItems/source provenance.
- Durable Postgres Sanad/Defect/Claim persistence remains deferred because durable Claim Registry persistence remains deferred and current tables expect UUID claim IDs.
- Claim-to-Sanad links are run-scoped only and do not promote claims to IC-ready status.
- Truth Dashboard, CALC, enrichment/API checks, Layer 1, Validated Evidence Package, Layer 2, GO/CONDITIONAL/NO-GO, deliverables, and real E2E remain deferred.

## TDD Task Plan

### Task 1: Model Tests

Files:

- Create: `tests/test_run_sanad_materialization_models.py`
- Modify later: `src/idis/models/sanad_materialization.py`

Tests:

- Existing `Sanad`, `TransmissionNode`, `Defect`, `SanadGrade`, and `DefectSeverity` are reused.
- Deterministic `sanad_id`, `node_id`, and `defect_id` use UUID v5 and are stable.
- IDs change when evidence/provenance changes.
- Shells exclude full chain refs, defect descriptions, claim text, and value structs.
- Reason-code values are lowercase snake_case.

Run:

`py -3.13 -m pytest -q tests/test_run_sanad_materialization_models.py`

### Task 2: Service Tests

Files:

- Create: `tests/test_run_sanad_creation_linking_grading_service.py`
- Modify later: `src/idis/services/runs/methodology_sanad_creation_linking_grading.py`

Tests:

- One claim plus one evidence item creates one Sanad, one link, and one grade.
- Multiple evidence items create deterministic primary/corroborating evidence IDs.
- Duplicate inputs do not create duplicate Sanads.
- Slice 6/7 shell inputs work.
- Missing evidence, missing claim context, malformed evidence, and scope mismatch fail closed.
- Chain node IDs/timestamps are stable across reruns.
- Defects use existing `Defect` model and deterministic IDs.
- Safe summary excludes forbidden payloads.
- Service does not call forbidden persistence/downstream services.

Run:

`py -3.13 -m pytest -q tests/test_run_sanad_creation_linking_grading_service.py`

### Task 3: Orchestrator Tests

Files:

- Create: `tests/test_run_orchestrator_sanad_creation_linking_grading.py`
- Modify later: `src/idis/models/run_step.py`, `src/idis/services/runs/orchestrator.py`, `src/idis/services/runs/steps.py`
- Update affected FULL step-count tests from 15 to 16.

Tests:

- Step order is after evidence materialization and before `EXTRACT`.
- Step is FULL-only and not SNAPSHOT.
- Successful FULL run attaches Sanad/link/grade/defect outputs.
- Missing claims or evidence blocks cleanly.
- Resume rehydrates safe shells and does not rerun.
- No Truth Dashboard, CALC, enrichment, Layer 1, Validated Evidence Package, Layer 2, recommendations, deliverables, or real E2E outputs are produced.

Run:

`py -3.13 -m pytest -q tests/test_run_orchestrator_sanad_creation_linking_grading.py tests/test_run_orchestrator_evidence_item_materialization.py tests/test_run_orchestrator_new_steps.py tests/test_run_orchestrator_debate_step.py tests/test_run_orchestrator_steps.py`

### Task 4: Reuse-Limit Tests

Files:

- Extend: `tests/test_run_sanad_creation_linking_grading_service.py`
- Optionally extend: `tests/test_sanad_model.py`, `tests/test_sanad_methodology_v2_unit.py`

Tests:

- Document that `SanadService.create()`, `build_sanad_chain()`, `auto_grade_claims_for_run()`, synthetic Sanad creation service, and claim-link apply service are not called by Slice 8 core.

Run:

`py -3.13 -m pytest -q tests/test_run_sanad_creation_linking_grading_service.py tests/test_sanad_model.py tests/test_sanad_methodology_v2_unit.py`

### Task 5: Audit Tests

Files:

- Modify: `scripts/audit_full_system_wiring.py`
- Modify: `tests/test_full_system_wiring_baseline.py`

Tests:

- Sanad creation/linking/grading boundary is `PARTIAL`.
- Boundary is in-memory and run-scoped.
- Durable Sanad/Defect/Claim persistence remains deferred.
- Claim links do not promote IC readiness.
- Truth Dashboard, CALC, enrichment/API checks, Layer 1, Validated Evidence Package, Layer 2, GO/CONDITIONAL/NO-GO, deliverables, and real E2E remain deferred.

Run:

`py -3.13 -m pytest -q tests/test_full_system_wiring_baseline.py`

## Full Validation Commands

```powershell
python scripts/forbidden_scan.py
py -3.13 -m ruff format --check .
py -3.13 -m ruff check .
py -3.13 -m mypy src/idis --ignore-missing-imports
py -3.13 -m pytest -q tests/test_run_sanad_materialization_models.py tests/test_run_sanad_creation_linking_grading_service.py tests/test_run_orchestrator_sanad_creation_linking_grading.py tests/test_run_orchestrator_evidence_item_materialization.py tests/test_full_system_wiring_baseline.py
pytest -q
python scripts/run_postgres_integration_local.py
```

## Review Decisions Incorporated

1. Use `grade_sanad_v2()` / `calculate_sanad_grade()` as the single in-memory grading source. Do not use `SanadService._compute_grade()`.
2. Reused Sanad/TransmissionNode/Defect payloads must receive deterministic IDs and timestamps from the Slice 8 adapter.
3. Internal chain refs must be safe ID-only metadata, and summaries must exclude raw text, claim text, value structs, locators, document names, paths, URIs, rationale payloads, defect descriptions, and verbose grade explanations.
4. Synthetic timestamps are approved as a fixed deterministic sequence.
5. Explicit empty service inputs may no-op, while the orchestrator blocks when prior claim/evidence context is missing.

## Stop Condition

Stop after implementation and validation. Do not start PR prep unless explicitly asked.
