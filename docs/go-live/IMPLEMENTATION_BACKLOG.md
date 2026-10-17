# IDIS implementation backlog

**Status:** executable planning specification, not implementation authorization or evidence of acceptance. The permission record is [RUN_STATE.md](RUN_STATE.md); architecture, product scope, cost allowances and decisions are authoritative in [GO_LIVE_MASTER_PLAN.md](GO_LIVE_MASTER_PLAN.md). Start with [START_HERE.md](START_HERE.md).

**Goal:** complete the existing IDIS product so a fund can obtain reviewed financial, market and commercial diligence in its own methodology and delivery package.

**Architecture:** retain ingestion, extraction, persistence, provenance, Sanad, deterministic calculations/Calc-Sanad, Court/VEP, the separate FULL DEBATE, specialists, scoring, Layer 2 IC challenge, Muḥāsabah/Muḥāsibī and the existing stores. Complete their substantive connections; do not create a competing orchestration framework.

**Execution:** AI implementation and fresh AI review, with the solo owner controlling material decisions, spending, integration authority and fund acceptance. Agent review does not substitute for substantive financial/commercial validation. Use the execution and GitHub checkpoint protocol in [AGENT_EXECUTION.md](AGENT_EXECUTION.md).

## 1. Baseline and use of this backlog

The original documentation package was published from GitHub main `4594ecabc4d7b598b42a67bf999b894d510f0ec9`; its source assessment used the original local Slice100 checkout. PUB-IMPL-01 subsequently preserved/published the cleared implementation through PR #119 and four separately controlled private artifacts. Current engineering starts from `go-live/implementation-batch-2026-10-16` at the reconciled head in RUN_STATE, not the older documentation PR/main. Historical identities remain in [BASELINE_MANIFEST](BASELINE_MANIFEST.md#pub-impl-01--proposed-implementation-recoverypublication-packet). AUTH-CONTINUE-20261016 authorizes the bounded CI-baseline follow-up and GL-P02/03 onward; earlier source-preservation and CIR evidence remains scoped to its recorded bytes.

All code paths below are repository-relative. Paths were inspected in the implementation tree during documentation preparation, not assumed present at the publication commit. New paths are explicitly marked **proposed**. Function names describe inspected seams; implementations must recheck their signatures against the authorized revision. The actual task planner is `src/idis/services/extraction/task_planner.py`, not `src/idis/methodology/task_planner.py`.

The controlling technical plan is `docs/plans/2026-07-15-slice100-strict-real-example-live-dry-run.md` and its accepted amendments. Tasks 1–7 are not reopened merely because this backlog reorganizes delivery. Their accepted scope does not establish complete diligence quality. Tasks 8–15, frozen snapshots, migration reviews, inactive execution contracts, prompt promotion and Gate A/Gate B remain binding.

### IDs and dependency rules

- `GL-01` through `GL-24` retain the previous master backlog's work-package identities. They are not new Slice100 task numbers.
- `GL-P01`, `GL-P02`, `GL-P03` are the first three bounded implementation packets. They complete explicitly named subsets of the parent work packages, not the parent packages in full.
- A work package may be decomposed into stable child IDs before authorization; its acceptance conditions cannot be dropped. Record child ownership and file locks in RUN_STATE.
- A prerequisite marked **design-only** permits interface review, not dependent runtime integration. Accepted evidence must identify the exact producer and consumer revisions.
- Conditional `GL-04` is not on the mandatory critical path. Choose it only by a separate decision; replay does not require it.
- No implementation packet is currently authorized merely by being listed here. Documentation publication authorization does not authorize implementation commits, tests, migrations, services or provider calls.

### Current CI-baseline follow-up

These are bounded children of CIR-04's still-open whole-system acceptance, authorized by AUTH-CONTINUE-20261016 and refined by AUTH-SEQUENCE-20261017. Diagnose/review each actual cause before correction and continue eligible substeps, but never advance to another product task while required checks are red, pending or skipped. [Runbook section10](VERIFICATION_AND_RUNBOOKS.md#10-continuation-ci-baseline-diagnosis-and-recovery) records historical evidence; RUN_STATE owns current exact identities. Each child needs its own role briefs, owned paths and affected verification before implementation.

ID reconciliation: the earlier table used E for container admission and F for historical-candidate recovery. Current Codex receipts use E for platform typing and F for truthful health reporting. Preserve those historical labels as aliases only: their unfinished obligations are now H (strict container admission) and I (private/historical recovery and CI admission). No obligation is removed or treated as accepted by this bookkeeping correction.

| Child | Scope / acceptance boundary | Current state |
|---|---|---|
| CIR-04A | Task8 lint and negative-fixture scan: trace immutable source/census hashes; use legitimate ownership/successor review, never casually reformat/repin frozen evidence or weaken scanning | Corrected candidate independently accepted for scope and published at7dbebdc;303 targeted tests pass. Actual CI format/lint pass and expose9 pre-existing Linux typing errors; whole CI remains failed |
| CIR-04B | Disposable PostgreSQL preparation: provision0038's two erasure roles and existing guard-function owner with exact posture/zero memberships; preserve frozen cleanliness checks, app-role isolation and migration ceiling; verify bootstrap/recovery | Scoped fix complete at `17f55dc789a5a22f1ce1367d7af7739791e78d8b`: independent PASS,69 local role/ceiling tests and5 recovery tests pass; exact CI run38951041704 confirms both fresh role steps, bootstrap/verify and backup job pass. Broader fixture/CI failures remain |
| CIR-04C | Older database fixtures: reconcile explicit document source-binding and run contract fields, real UUID/data shapes and remaining behavior failures; preserve schema invariants | C1 storage/RLS fixture subset reviewed/published:47 original cases pass; exact CI at05a1f44 and7dbebdc shows142 passes/20 failures/1 skip. Remaining20 cases require actual-cause correction; no fake native parents or defaults **2026-10-17 13:20 UTC (Fable):** the byte-identical 21-module invocation on the composed C2+C3 candidate tree against a fresh disposable 0038 database is 5 failed / 159 passed (hosted base at 6c5dcc7: 20 / 142 / 1 skipped); the candidate corrects 15 of the 20. The five remaining are legacy selected-FULL callers started from plain uploads (durable-evidence, step-name, sanad-auto-grade, multidocument acceptance, observability block-reason); under AUTH-FROZEN-PREVIEWS-20261017 they are requalified on the frozen path with every obligation preserved (C3 batch 4 brief prepared; not yet dispatched). Hosted proof still owed **2026-10-17 16:22 UTC:** C3 batch 3b (manifest-04) reviewed PASS for reviewed WIP publication and staged for the combined checkpoint with coordinator correction CC-01 (two synthetic encrypted-PDF password literals made runtime-generated for the public forbidden scan; disclosed with before/after hashes); required failures by test id stay visible (two A4 positives, five slice75a seams, restart_cut[1], four legacy FULL positives); batch 4 (the legacy FULL callers, the genuine signed-exclusion positives, the slice75a seams) is in flight on the C3 writer. **2026-10-17 18:36 UTC:** batches 4, 4b and 4c reviewed; 4c PASS for reviewed WIP publication (manifest-07 58ea2af4…): the four legacy FULL positives and the five slice75a seams are closed in the bytes, the two A4 positives are closed in the bytes but hosted-only RED until a qualified Linux governed Poppler closure exists (CIR-04H H2a) and `postgres-integration` installs Poppler/Tesseract (J-04); `restart_cut[1]` stays RED under the amendment; publication with the J removal revision after P4 V3. |
| CIR-04C2/C3 follow-up | Reuse genuine production package freeze/admission for all producers, repair exact-run claim/audit and observability lease handling, and requalify historical callers without erasing their obligations | AUTH-FROZEN-PREVIEWS-20261017 resolves the legacy/SNAPSHOT choice: freeze required, no UNBOUND-preview exception. Current expanded evidence170pass/11fail/0skip; two historical0037 nodes are RED at base and candidate. Preserve original evidence; no blind migration bump, fabricated frozen inputs, empty-answer success or weakened assertions. Exact owned implementation paths and dependency order require the focused plan/review. **2026-10-17 owner clarification and mapping (RUN_STATE, 10:36 UTC entry):** the freeze ruling removes no documented input support; the combined C2/C3/J candidate (actual Opus review CHANGES_REQUIRED) must additionally close these current obligations before CIR-04 acceptance, serialized in the C3 writer scope after batch 1: (a) scanned-PDF/image/media members: reviewed Tesseract/tessdata runtime identity, OCR/media receipt references at ingestion, and a first runtime parse attempt (including FAILED attempts for signed exclusions) so such packages freeze as the Task 7 design intends; (b) older and manifest-less uploads: a reviewed runtime backfill route into manifest-backed packages reusing the legacy-document-metadata-v1 recipe and content-integrity proof, plus a public upload-to-package path for ordinary filenames; (c) SNAPSHOT runs selecting a subset of a frozen package per RunSource.document_ids; (d) the DOCUMENT_PREFLIGHT NO_USABLE_DOCUMENTS ledger reachable through a frozen package of signed exclusions; (e) distinct safe 409 refusal codes as diagnostics only. Batch 1 (in progress) covers the refusal codes, the broken unchanged callers and two minor findings **2026-10-17 16:35 UTC:** batches 1–3b are reviewed (3b PASS for reviewed WIP publication, `c3-corrections-manifest-04.json`); batch 4 is in progress on the C3 writer; see the CIR-04C row. |
| CIR-04D | Cold import: remove the eager initialization dependency cycle while preserving exported interfaces and normal/cold import behavior | Reviewed/verified repair published in integration `93a2dcde6fd7073c78e93832064d241808e36008`:142 tests and528-file mypy pass; whole-system CI remains failed |
| CIR-04E | Windows/POSIX platform typing and filesystem-security behavior; no casts/ignores masking defects | Reviewed sourceffd7af4 integrated/publishedfcb3b79. Both528-file mypy runs pass; Windows235/Linux228 targeted passes with complementary platform skips. Actual Opus scoped PASS; whole CI remains unaccepted |
| CIR-04F | Truthful container-health summary and unchanged mandatory gate | Three-path tree4397991f: Windows/Linux44 passes plus33 subtests each; actual Opus PASS with evidence finding closed; sourced502c65 integrated/published5b5965a. Does not provide positive runtime health or green whole CI |
| CIR-04G | Strict FULL worker version/hash/RESUMABLE and admission-graph checks before provider composition | Reviewed source494deafa integrated/published1bb6046;164 focused cases and528-file mypy pass per OS, coordinator fresh integration164pass; actual Opus source/evidence/integration PASS. Exact-head CI38995685872 remains required, not inferred green. No claim-SQL or lifecycle-policy change |
| CIR-04H (historical E) | Container-health composition with actual services/launch audit; separate image construction, synthetic health and canonical acceptance | AUTH-CI-HEALTH-20261017 permits an explicitly selected isolated synthetic test profile only: fictional79-row workbook, unsigned deny-only declarations, zero-call cap and authority-free disposable public key. All validators, seven real checks, literal13routes/default command remain; executable zero-call/no-spend/no-egress controls and canonical-signature negatives required. Composition isolation is not a loader mode; never pass the profile to gate/Task4/FULL proof, record disposable-key SHA256 and negatively verify no gate invocation. Linux native renderer/STT qualification remains. Success is only "container health with synthetic fixtures", not Task4/FULL/financial/production acceptance. No general bypass, fake DLL or historical repin |
| CIR-04I (historical F) | Exact private/historical evidence recovery and trusted exact-head CI admission | AUTH-RECOVERY-20261017 six additions privately published at1a094b8 after actual Opus privacy/recovery PASS; original four conserved. Exact ten-artifact fresh remote restoration and fresh-source historical signature/closure validation pass; no new census/signature. Trusted exact-head CI admission remains required, not configured by preservation **2026-10-17 13:20 UTC (Fable):** the hosted `check` job requires the GitHub environment `cir04-private-inputs` (required reviewer `albarami`) and reads the environment secret `IDIS_RECOVERY_READ_KEY` (SSH read key for the private recovery repository, consumed by `scripts/ci_private_inputs.py fetch`). GitHub currently lists zero environment and zero repository secrets, so the private fetch step exits 1 before any test runs (run 39014917801), and runs 39017690283/39020576851 wait on the environment approval. Owner prerequisites: store the read key as that environment secret and approve each pending deployment; Fable neither creates secrets nor approves deployments **2026-10-17 16:22 UTC:** P4 probe wiring P4b reviewed PASS for scoped WIP (manifest-05 3d930be9…; custom-shell `{0}` route for part B; JP4-01..09 closed); both superseded pending environment reviews rejected and read back (runs 39017690283, 39020576851); the probe revision is being published as a reviewed WIP checkpoint whose `check` job fails by design at `canary-capture` (qualification evidence, not green CI); P4 V1→V3 then run under their own tuples and approvals before any real recovery key. **Superseded in part (16:35 UTC):** the 13:20 UTC sentence "Fable neither creates secrets nor approves deployments" described the pre-ruling state; under the owner's P4 rulings the coordinator registers only the synthetic canary seed (owner confirmation requested) and submits the exact-run approval, and under the 15:59 UTC grant it rejected the two superseded reviews (RUN_STATE decision-note item 3, superseded note). |
| CIR-04D0039 | Development-only migration 0039 and its isolated disposable-database CI path for the two restart positives (AUTH-CIR04-DEV0039-20261017, owner decisions 2 and 3 of the reviewed amendment v6) | Authorized 15:23 UTC against packet `R/restart-amendment-preparation-v6/` (proposal SHA256 6555898c…; Opus PASS/fit-to-present 91ff0384…). Scope: dev revision `scripts/dev_only/cir04_restart_reference/versions/0039_task7_restart_reference_authority.py` outside wheel/image/sdist with exclusion REDs; driver `scripts/run_cir04_restart_reference_dev_migration.py::upgrade_disposable_to_dev_0039` with per-context engine validation and connection-bound 0038→0039 upgrades plus the maintenance-database refusal RED; J-owned sibling steps/dev config builder/zero-skip steps/pins in `postgres-integration`; the `(request, tmp_path, completed_prefix)` signature with `dev_0039_restart_engine` only through `request.getfixturevalue`; the historical test adds the driver call after `migrate(…, "0038")`; the sibling rollback test and the 0038 `app_engine` assertion unchanged; node id `…restart_cut[1]` and both positive obligations preserved. Not authorized: anything depending on decisions 1 (hermetic-proof authority; historical positive stays BLOCKED) or 4 (staged request); 0039 on any non-disposable database. Plan: J-04 after P4b; C3 batch 5 after batch 4 (historical test under a new reservation); RED-first, Fable verification on fresh disposable databases, Opus review, reviewed publication, exact-head CI |

Do not dispatch GL-P02/03, GL-02/05 preparation or another product task while the current CIR-04 task lacks required acceptance, final-candidate actual Opus PASS, reviewed publication and exact-current-head green CI. Earlier permission to skip ahead is superseded. Preserve the existing later backlog and every full-product obligation.

Astra in Codex coordinates Sol6.1 implementation and actual Claude CLI Opus5.5 review. E/F/G are scoped published repairs, not whole-task acceptance. Continue C2/C3's review findings and genuine frozen-source path under the owner's resolved ruling, plus legitimate H/I prerequisites from RUN_STATE; do not restart the inventory or planning exercise. All main-merge,0039, activation/deployment, live-provider, new-paid-service and fund-communication holds remain.

### Common completion protocol — applies to every packet below

1. Confirm authorization, baseline, clean ownership of the allowed file set and required private inputs. Preserve existing work; do not reset, stash or cherry-pick unrelated changes without explicit authority.
2. Record producer/consumer signatures, schema versions, internal/external boundary, concrete acceptance examples and failing cases before editing. New semantic contracts and schema changes require the applicable ruling.
3. Implement bounded changes with regression tests; record RED evidence, then GREEN evidence. Never make expected values from the implementation under test the sole oracle.
4. Run only the authorized verification set. Record command, exit code, source commit and worktree diff identity, configuration identity, fixture/real-input status, model/prompt identity and unavailable checks. Do not equate skipped checks with passing checks.
5. Obtain fresh review of the actual diff and evidence. Correct material findings and rerun affected checks. Label self-review if no separate reviewer is available; it cannot close a gate requiring independent review.
6. Under the batch's explicit commit/push authority: inspect the **complete outgoing diff and commit range**, commit only the packet, push, open/update the PR and check required CI against that exact SHA. Follow repository protections; merge only under the owner-approved merge policy. No force push to shared branches.
7. Record implemented → verified → reviewed → accepted separately. An accepted checkpoint must be recoverable from GitHub, with sanitized evidence references, exact SHA, open limitations and next action. Private evidence remains in controlled storage with a safe receipt/reference, not public Git.
8. Recovery is a scoped corrective/revert commit after inspection, not destructive worktree reset. Data migrations need their own forward-recovery/restore plan; code rollback cannot erase evidence or restore spent budget. Keep prior answer/artifact versions accessible.

Unless a packet says otherwise: no new service, provider, migration, environment flag, subscription or live run is implied; UI/API/schema changes not named are out of scope. The review roles are AI implementer, fresh AI technical reviewer, and the owner for material product/contract decisions. Fund feedback validates fund requirements and usefulness; it is not an assumed hired review service. Cost and authority are resolved by RUN_STATE, not by estimates in this backlog.

### Verification command catalogue

These commands are **future implementation instructions; none was executed to prepare this package**. Existing test paths and command forms were inspected in `Makefile`, `pyproject.toml` and `ui/package.json`. Passing behavior and environment suitability remain unverified. Test collection itself can import application code: review fixture/service/provider behavior before execution.

| ID | Command / scope | Admission and expected evidence |
|---|---|---|
| V-01 | `ruff check <explicit changed Python paths>` | Existing project tool; select paths before execution. No lint errors; unchanged pre-existing issues distinguished. |
| V-02 | `ruff format --check <explicit changed Python paths>` | Read-only formatting check; proposed command variant. No format changes required. |
| V-03 | `mypy src/idis --ignore-missing-imports` | Existing Makefile typecheck form. Full result required; no casts/ignores hiding contract failures. |
| V-04 | `pytest <explicit test paths> -q` | Existing project runner; each path must exist and its fixtures be approved. Positive, negative and conservation cases recorded. |
| V-05 | `python scripts/forbidden_scan.py` | Existing Makefile scan. No forbidden patterns; inspect current script before running. |
| V-06 | In `ui`: `npm run lint`, `npm run typecheck`, `npm run test`, `npm run build` | Existing scripts; dependencies must already be available or separately authorized. Run as distinct commands. |
| V-07 | `git diff --check`; `git diff --stat`; `git log <approved-base>..HEAD --oneline`; `git diff <approved-base>...HEAD -- <packet paths>` plus complete unfiltered diff | Check current changes and **all** outgoing paths/commits before publication. Exact approved base required. |
| V-08 | Authorized configured-service census/restart/recovery runbook | No generic command supplied: operator identity, namespace, sealed wrapper, current hashes and authority must be resolved first in VERIFICATION_AND_RUNBOOKS. |

Do not substitute `make check`: it runs a mutating formatter and the full test suite. `make ui-check` installs dependencies. Neither is implicitly authorized by a targeted packet. New test commands below are labelled proposed until the new file exists. Broad regression, configured-store and live acceptance remain later gates even when a targeted subset passes.

## 2. First implementation cycle

Dependency chain: `GL-01 baseline/authority → GL-P01 → GL-P02 → GL-P03`. Only after the current CIR-04 green transition gate, independent GL-02 requirement decomposition and GL-05 adapter design may proceed in parallel under their authorization if they do not write the same files. Do not parallelize writers to the shared methodology/analysis schemas or `src/idis/api/routes/runs.py`.

The first cycle uses production-relevant pure components with honestly labelled offline/replay inputs. It does not need the entire Tasks 8–15 backlog. It does not obtain live extraction admission by bypassing the strict contract. A later two-question pilot must cover the additional paths in GL-16; these three packets alone are not that full pilot, canonical FULL success, comprehensive scoring, or Slice100 acceptance.

### GL-P01 — close the version-bound financial expected-answer schema boundary

**Maps to:** GL-03; Slice100 Task 8 financial policy-v2 compatibility and its preserved Task 5/6/7 planning/extraction boundaries.

**Objective / user result:** a frozen policy-v2 financial question can be planned and serialized into an extractor request without being coerced into the legacy calculation schema. Legacy methodology versions keep their exact accepted bytes and behavior.

**Prerequisites:** GL-01 baseline recorded and future implementation authorization; local Task 8 source available; exact allowed methodology-version IDs and policy bindings verified; ownership of `ExpectedAnswerSchema` checked against the 68 frozen snapshots and recorded. If that surface is frozen, obtain an explicit successor/ruling before changing it. Do not infer ownership from the requirement-model precedent.

**Producer → consumer / data:** `MethodologyQuestion.required_calculations` → `build_expected_answer_schema()` → `ExtractionTask.expected_answer_schema` → persisted/reconstituted extraction task → methodology extractor request. Preserve exact question text, requirement shape, frozen `methodology_version_id`, source/row trace, validation requirements and report mapping. Internal fund methodology only; this change admits no external facts or provider access.

**Existing files / seams:**

- `src/idis/methodology/models.py`: `MethodologyQuestion`, legacy `RequiredCalculation`, policy-v2 requirement shape and version admission.
- `src/idis/models/extraction_task.py`: `ExpectedAnswerSchema`, `ExtractionTask`, deterministic serialization.
- `src/idis/services/extraction/task_planner.py`: `build_expected_answer_schema()`, `InMemoryExtractionTaskPlanner.plan_tasks()` and `_plan_for_question()`.
- `src/idis/models/methodology_extraction.py`: extraction input/output models; only extend if the version-disjoint boundary must be carried there.
- `src/idis/services/extraction/methodology_task_extractor.py`: inspect `prepare()` and request-schema construction for preservation, not live dispatch changes.
- `src/idis/persistence/repositories/methodology_extraction.py`: inspect task rehydration; change only the serialized schema decoder if necessary and explicitly reviewed.
- `contracts/fdd_formula_policy_v2.json`: read-only policy authority, not a fixture to regenerate casually.
- Existing tests: `tests/test_extraction_task_planner.py`, `tests/test_methodology_models.py`, `tests/test_methodology_extraction_execution_models.py`, `tests/slice100_task8/test_fdd_formula_requirement_model_preslice.py`.
- Proposed regression file: `tests/test_go_live_policy_v2_expected_answer_schema.py`.

**Schema/API/UI/config implications:** introduce a disjoint expected-answer shape or version-bound decoder only if the ownership review permits it. Route by the frozen methodology version, not by guessing from fields or accepting an unrestricted union. Keep the legacy JSON schema unchanged for legacy callers. No HTTP/UI/config change expected; if a version field must cross a public interface, stop for that explicit contract decision. No casts, widening to `Any`, dropping fields, back-conversion, silent defaults or legacy-policy coercion.

**Bounded steps:**

1. Record current field declarations and enumerate all expected-answer constructors, readers and serializers. Map each to the owning frozen snapshot or explicitly outside it.
2. Capture independent golden bytes for representative accepted legacy FDD/CDD tasks from the existing authoritative fixtures. Record hashes before any edit; preserve original source text bytes.
3. Add RED cases: an allowed policy-v2 question reaches planning/request serialization; legacy shape under policy-v2 is rejected; policy-v2 shape under legacy/unknown version is rejected; mixed shapes and extra fields are rejected; persisted round-trip retains the exact admitted shape.
4. Implement the smallest disjoint, version-bound schema and route the already frozen version identity through the necessary constructors/readers. Do not infer version from the model class alone.
5. Add positive, malformed, wrong-version and legacy-conservation cases at each touched boundary; run the targeted verification and typecheck once authorized.
6. Produce a before/after compatibility table and reviewer report. If the existing mypy failure moves to another genuine consumer, record and resolve that consumer within the authorized boundary or report the dependency; do not conceal it.

**Out of scope:** formula changes; migration 0039; modifying the 68 frozen artifacts; contract activation; census; prompts/models; live extraction; fund data import; full Task 8 acceptance.

**Verification:** V-01/V-02 on changed Python files; V-03; V-04 with the four existing test files above plus `tests/test_go_live_policy_v2_expected_answer_schema.py` (**new/proposed**); V-05; V-07. A frozen-conservation command must be selected from the authoritative current guard set after ownership review, not invented here. Required evidence: RED/GREEN logs, schema/serialization diff, exact legacy hashes, rejected-shape matrix, no unauthorized frozen-file change, independent review.

**Acceptance / review:** all admitted policy-v2 examples retain all four policy fields; all disallowed combinations fail closed; legacy serialized outputs remain byte-identical; typecheck passes without suppression of the mismatch; reviewer confirms frozen-surface ownership and routing authority. Any unrelated baseline failure stays visible and prevents a broad “all green” claim.

**Recovery / authority:** code-only revert/corrective commit under future authorization; no persisted schema migration in this packet. If incompatible existing records are discovered, pause and design versioned rehydration before writing data. No paid calls; implementation and verification authority still required.

**Next after acceptance:** GL-P02, the remaining GL-07 formula implementation, and explicit production/replay extraction integration. Acceptance does not activate strict execution.

### GL-P02 — one governed EBITDA calculation and statement-support path

**Maps to:** GL-07 thin slice plus GL-11 assertion-support thin slice; preserves Slice100 Task 8 calculations and Task 10 substantive consumer intent. This is not completion of all ten policy formulas.

**Objective / user result:** an inspectable answer fragment distinguishes the calculated EBITDA amount/margin from the separate judgment about whether projected long-term margin is realistic. It shows the operands, exact source locations, adjustments, calculation lineage, missing information and the limits of the conclusion.

**Prerequisites:** GL-P01; approved policy rule for the selected calculation; GL-02's two-question obligation subset agreed; independent reference calculation for a bounded non-confidential fixture and, when authorized, a controlled real-case operand set. No historical model answer is accepted as a gold answer. Any new assertion-envelope semantics require their explicit owner approval before implementation.

**Producer → consumer / data:** admitted/replayed extraction observations and materialized claim/evidence/Sanad records → methodology calculation service / `CalcEngine.run()` → deterministic calculation and Calc-Sanad → `AnalysisContext` → `FinancialAgent.run()` / specialist response → statement-specific supported answer. Carry entity, period, currency, units, accounting basis, actual/forecast status, adjustment identity, source locators, input hashes, formula version, result, competing observations and unresolved reconciliation. No last-write-wins operand selection.

**Boundary:** internal financial statements/forecasts remain internal evidence. External benchmarks may inform later Layer 2 realism analysis, but are not required for this offline numeric slice and must not be relabelled as internal Court/VEP evidence. If forecast realism lacks market/cost/operational support, return a qualified or unanswered judgment, not a confident answer inferred from arithmetic.

**Existing files / seams:**

- `src/idis/services/runs/methodology_deterministic_calculation.py`: `InMemoryRunMethodologyDeterministicCalculationService.run()`, `_build_inputs()`; this is a reusable computational component, not permission to invent native persistence parents.
- `src/idis/services/runs/methodology_deterministic_calculation_helpers.py`: requested calculations, scoped claims, metadata and input mapping.
- `src/idis/calc/engine.py`: `CalcEngine.run()`, reproducibility and Calc-Sanad; `src/idis/calc/formulas/core.py`, `src/idis/calc/formulas/registry.py` for approved versioned formula registration.
- `src/idis/models/calc_materialization.py`, `src/idis/models/deterministic_calculation.py`, `src/idis/models/calc_sanad.py` for typed result/lineage changes only if required by the approved policy.
- `src/idis/services/calc/runner.py`: `CalcRunner.run()`, `_build_candidate()`, `_claims_by_input_key()`; characterize overlap rather than replace the calculation subsystem.
- `src/idis/analysis/models.py`: `AnalysisContext`, `AnalysisCalcReference`, `AgentReport`; `src/idis/analysis/agents/financial_agent.py`: `FinancialAgent.run()`; `src/idis/analysis/agents/llm_specialist_agent.py`: `_build_context_payload()`, `run_specialist_agent()` and `_build_risks()`.
- `src/idis/validators/muhasabah.py`: reuse its existing gates; existence of a reference is not a new support oracle.
- Existing tests: `tests/test_run_methodology_deterministic_calculation_service.py`, `tests/test_calc_reproducibility.py`, `tests/test_calc_sanad.py`, `tests/test_slice87_calc_unification.py`, `tests/test_analysis_prompt_evidence_rule.py`.
- Proposed tests: `tests/test_go_live_ebitda_support_path.py` and `tests/test_go_live_statement_support.py`.

**Schema/API/UI/config implications:** approved typed assertion fields must include assertion ID, question/obligation ID, text or numeric value, support links to exact evidence/calculation/operand references, status (`supported`, `inferred`, `disputed`, `missing` or an approved equivalent), qualifications and missing prerequisites. Keep model output validation separate from the underlying stored evidence. Extend existing models; do not invent a parallel answer database. No UI, live transport, contract activation or production DB schema is required for the first offline slice.

**Bounded steps:**

1. Freeze the selected formula, sign/precision policy, coordinates and independent expected result. Explicitly record whether EBITDA margin is a separate approved derived ratio; do not silently add a formula not owned by the policy.
2. Add RED fixtures for correct matched coordinates, different entities/periods/currencies/units, duplicate competing operands, explicit adjustments, missing input, zero denominator where applicable and unsupported forecast judgment. Do not fill missing inputs with zero or convert currencies silently.
3. Implement policy-v2 dispatch into the existing calculation engine and preserve operand/source lineage. Reject ambiguous input selection; expose competing observations for reconciliation.
4. Pass actual operands, results, source excerpts/locators and caveats into the specialist context. Use a deterministic response fixture for offline integration; label it simulated, not a live model capability result.
5. Validate statement-to-support membership and numerical entailment separately. For example, an EBITDA value must match its declared calculation and coordinates; a source cited for a different period must fail. General financial interpretation still requires expert review and is not guaranteed by model review.
6. Remove support assignment by “first available references” on this path. An unsupported assertion stays unsupported or is excluded from the accepted answer; do not manufacture citations.
7. Record an inspectable answer artifact and limitations. Preserve the original source and all earlier observations.

**Out of scope:** all remaining financial formulas; external research; full extraction admission; full five-role debate or IC execution; a complete fund answer; a new autonomous investigation loop; automatic FX conversion; broad score/recommendation.

**Verification:** V-01–V-05 as authorized; V-04 with the five existing test files and the two **proposed** files above; V-07. Required evidence includes independent manual/reference calculation, RED/GREEN coordinate and support cases, deterministic hashes, Calc-Sanad linkage, exact prompt/context sample with synthetic data, and an owner financial-review disposition. Real evidence may only be stored privately with a safe receipt.

**Acceptance / review:** exact approved numerical result and precision; all operands retain coordinates and supporting locators; wrong-scope and disputed operands block or remain explicit; no unsupported realism conclusion; each material assertion has its own support or visible non-supported status; calculation IDs survive the handoff. AI technical review and owner/fund substantive validation are recorded separately. If the latter is unavailable, quality validation remains open.

**Recovery / authority:** retain immutable calculation/answer versions; a corrected operand creates a new result and invalidates dependent conclusions rather than overwriting history. No migration/live/paid permission implied. The approved verification budget is referenced from RUN_STATE.

**Next after acceptance:** GL-P03 and remaining GL-07/GL-10/GL-11 work. This packet provides measured calibration for later packets; do not extrapolate a universal AI productivity multiplier.

### GL-P03 — answer-to-deliverable propagation and basic correction/regeneration

**Maps to:** GL-15 thin slice plus the basic correction subset of GL-14/GL-19; Slice100 Task 14 artifact/output obligations remain larger than this slice.

**Objective / user result:** the reviewed answer from GL-P02 appears at its correct question/section destination with qualifications and accessible evidence/calculation links. A controlled correction visibly changes the affected answer and regenerated outputs, while retaining the prior version and explaining the change.

**Prerequisites:** GL-P02; proposed four-part delivery format from the master plan; GL-02 subset mapping; owner approval of correction semantics and artifact-version identity. Final fund template confirmation is not required to build this inspectable slice, but remains a release dependency. Source workbook stays untouched.

**Producer → consumer / data:** reviewed `AgentReport`/assertion envelope + methodology `report_mapping` + obligation identity + correction record → `DeliverablesGenerator.generate()` → `ProductBundleExporter.export_bundle()` → report/annex/workbook response projection and artifact manifest. Preserve support status, unresolved contradictions, calculation inputs/results, source locators and review status. External evidence is labelled and access-controlled; unauthorized source content is not exported.

**Existing files / seams:**

- `src/idis/deliverables/generator.py`: `generate()`, `_extract_facts_from_report()`, `_build_ic_memo()`, `_validate_nff()`; remove any generic citation backfill on the new supported-answer path.
- `src/idis/deliverables/product_bundle.py`: `export_bundle()`, `_artifact_drafts()`, `_calc_package()`, `_evidence_index()`, manifest creation.
- `src/idis/deliverables/financial_table.py`, `src/idis/deliverables/memo.py`, `src/idis/deliverables/export.py`, `src/idis/models/deliverables.py` for projections and exports.
- `src/idis/api/routes/human_gates.py`: existing gate/action interfaces; `src/idis/api/routes/overrides.py`: existing correction/override audit semantics. Inspect before choosing the minimal extension; do not treat the existing test-only gate creator as a production gate producer.
- `src/idis/persistence/repositories/deliverables.py` and `src/idis/storage/object_store.py`: existing artifact persistence boundaries, used only when separately admitted; offline evaluation can exercise serializers without pretending it persisted a native run.
- Proposed module: `src/idis/deliverables/fund_response_workbook.py`, only if there is no suitable existing exporter at the implementation baseline.
- Existing tests: `tests/test_deliverables_generator.py`, `tests/test_deliverable_no_free_facts.py`, `tests/test_slice87_financial_tables.py`, `tests/test_slice93_layer2_deliverable_visibility.py`.
- Proposed test: `tests/test_go_live_answer_correction_regeneration.py`.

**Schema/API/UI/config implications:** define an immutable correction/review version using existing review/audit records where possible; changed inputs invalidate only affected findings and artifacts. A production endpoint or database change is not silently included: first demonstrate service-level offline correction, then authorize the corresponding GL-14 API/UI wiring. Workbook response projection must preserve original sheet/row/child-obligation identity and shared-instruction reference; never write into the authoritative original file. Two-question artifacts display “partial evaluation”; no comprehensive investment score or final IC decision.

**Bounded steps:**

1. Add RED cases for question-to-destination mapping, qualifications, evidence links, unsupported statements, missing data and private-source access denial.
2. Extend existing generator/export projections to consume the typed supported answer without inferring support from a global reference list.
3. Produce the minimal response-workbook view, narrative finding and evidence/calculation annex from the same answer version. Keep the IC memo projection visibly partial and do not generate a comprehensive verdict.
4. Apply one explicit operand correction and one evidence/status correction through the proposed service-level review boundary. Record actor, reason, prior/new versions and actual analyst contribution; do not count user-supplied reasoning as autonomous analysis.
5. Recompute affected calculation/answer projections and regenerate affected outputs; show prior/new hashes and invalidation. Unaffected reviewed work remains reusable only with matching inputs, methodology/prompt/model and rights validity.
6. Verify the output can be read without the chat: requirement identity, answer, evidence, formula, qualification and open follow-up must all be navigable. Record final-format confirmation as open if still pending.

**Out of scope:** bulk 100-question completion, a full analyst UI, production correction persistence without its schema/API approval, adaptive routing, learning, automatic repeated investigations, full Task 14 catalog acceptance or fund sign-off.

**Verification:** V-01–V-05 as authorized, V-04 with the four existing tests and the **proposed** test above; V-07. Inspect the generated XLSX/DOCX/PDF artifacts during authorized implementation using the applicable artifact verification workflow, not only JSON tests. Record original workbook hash unchanged, readable outputs, stable row/obligation links, failed unauthorized access, before/after correction diff and no stale supported answer.

**Acceptance / review:** one reviewed answer reaches every in-scope projection consistently; corrections are auditable and change only the correct dependencies; support/uncertainty is not lost; previous versions remain accessible; artifacts are visibly partial and source-safe. Owner evaluates usability and records active review time; fresh technical review covers invalidation and privacy.

**Recovery / authority:** disable/revert the new projection without deleting stored historical artifacts. Never overwrite the source workbook. A schema/API decision is a stop point if needed beyond the offline service boundary; no implicit migrations or provider spending.

**Next after acceptance:** reassess estimates using measured results; complete the additional connections for GL-16; decide whether the canonical route is sufficiently near before even authorizing GL-04 design/build work.

## 3. Complete dependency-linked work packages

The following packages retain the complete agreed product. They are not all prerequisites for GL-P01–03. Each inherits the common completion protocol, verification catalogue and authorization rules above. A proposed new public schema/API/prompt semantic must be reviewed before implementation, even where its intention is described here.

### GL-01 — implementation authority and recoverable baseline

- **Result / mapping:** original backlog 01; preserve accepted Tasks 1–7 and all later holds. Establish exactly which implementation source is being changed and recoverable.
- **Inputs → outputs:** local Git history, dirty source, frozen contracts and safe evidence inventory → reviewed baseline manifest and branch/PR integration plan consumed by every packet. No private source data in Git.
- **Files / change:** `docs/go-live/RUN_STATE.md`, relevant original Slice100 plan and handoff references; inspect `.github/workflows/`, `.github/`, `contracts/`, `schemas/` and source diff. No product code change.
- **Dependencies / parallelism:** first; read-only mapping work may run beside it. Publishing local implementation requires separate authority from this documentation PR.
- **Steps / interfaces:** inspect actual status; identify remote-recoverable vs local-only source; isolate required unpublished commits/diffs; preserve exact hashes; decide integration base; record authorized batch/files/limits and handoff. No env/secret dump.
- **Verification / acceptance:** V-07 and safe manifest review. No unrelated commit or private file in the outgoing range; required implementation baseline is reconstructible or prominently blocked. Fresh reviewer can identify the next authorized action.
- **Recovery / next:** documentation corrective commit only; preserve all worktrees. Enables GL-P01, GL-02 and separately authorized design work for GL-05/09/23.

### GL-02 — complete fund requirements and output mapping

- **Result / mapping:** original 02; 79 financial items and 21 commercial questions, with every child obligation and shared instruction addressed rather than merely imported.
- **Inputs → outputs:** authoritative private workbook and commercial template → versioned requirement/obligation matrix with required evidence, calculations, analytical judgment, completeness criteria, review and output destinations. Original wording/rows remain private unless the fund authorizes publication.
- **Files / seams:** `src/idis/methodology/importers/fdd_excel.py`, `src/idis/methodology/importers/fdd_formula_policy_v2.py`, `src/idis/methodology/models.py`, `src/idis/methodology/templates/commercial_dd_v1.json`, `src/idis/services/extraction/task_planner.py`; proposed safe obligation-manifest schema under `schemas/` only after contract review.
- **Dependencies / parallelism:** GL-01; parallel with GL-P01 on a non-overlapping mapping artifact. Schema writes serialize with GL-P01. Only the two-question subset is prerequisite for GL-P02.
- **Steps / interfaces:** preserve raw rows/merged ancestry and normalized text separately; decompose obligations; map all 21 commercial IDs and specialist aliases to actual registered roles; define evidence standards and destinations; log workbook ambiguities and decision owners. Child obligations do not silently mutate the frozen 79/21 counts.
- **Verification / acceptance:** V-04 `tests/test_fdd_methodology_importer.py`, `tests/test_commercial_methodology_template.py`, `tests/test_extraction_task_planner.py`; add proposed obligation-conservation tests. All source items and child obligations accounted for; no wording/instruction loss; unresolved guidance remains visible and blocks affected definitive answers. Numerical answer keys are not invented from the workbook, which has no executable formulas.
- **Recovery / next:** version mapping artifacts and retain previous imports; no original-file changes. Enables full GL-07/08/11/15/18/22. Fund format and substantive ambiguity decisions remain separate acceptance dependencies.

### GL-03 — financial expected-answer compatibility

- **Result / mapping:** original 03; GL-P01 is its detailed first packet; Slice100 Task 8.
- **Scope / files / verification:** use GL-P01 verbatim; downstream constructors/rehydration found during the ownership trace must be included or explicitly blocked. No broad union or coercion as a shortcut.
- **Prerequisites / next:** GL-01 → GL-P01 → remaining policy-v2 execution consumers in GL-07 and GL-06. Legacy conservation and all frozen-review conditions remain the acceptance boundary.

### GL-04 — conditional live component-evaluation admission

- **Result / mapping:** original 04, now **conditional and not committed**. Determine whether a separate live admission path yields material earlier evidence than completing the production path.
- **Producer → consumer:** real authorized extraction request → committed call/budget admission → provider response → committed terminal persistence → typed downstream receipt. Replay is not legitimate live extraction admission.
- **Existing seams:** `src/idis/services/runs/methodology_extraction_task_execution.py` (`DurableRunMethodologyExtractionTaskExecutionService.run()`); `src/idis/services/extraction/methodology_task_extractor.py` (`prepare`, `dispatch`, `validate_dispatched`, `finalize`); `src/idis/services/runs/methodology_postgres_runtime.py`; `src/idis/persistence/repositories/methodology_extraction.py`; `src/idis/persistence/repositories/strict_preflight_proofs.py`; `src/idis/pipeline/contract_registry.py`; `src/idis/services/runs/methodology_postgres_materialization_runtime.py`.
- **Dependencies / decision:** first measure GL-P01–03 and inventory remaining canonical prerequisites. Prefer canonical completion plus replay. A new evaluation contract requires a separate owner/ruling decision, reviewed native admission/persistence design, prompt/model/price approval, configured services, exact budget and data authority. Do not wait for unrelated modernization before making the comparison.
- **Bounded steps / interfaces:** compare complete effort and maintenance of both routes; identify extraction and downstream FK/receipt requirements; if authorized, reuse phased extractor/computational services behind truthful typed evaluation persistence. No fake native run/step roots, nullable legacy escape, bypassed guards or in-memory success labelled production persistence.
- **Verification / acceptance:** review `tests/test_run_methodology_extraction_task_execution_service.py` and existing durable extraction guards; new evaluation contract tests/commands are **unverified and require the approved design**. Test crash/unknown outcome, committed-before-dispatch and terminal-before-finalize, scope isolation and no external leakage. Canonical and component-evaluation results remain differently labelled.
- **Recovery / next:** cancellation leaves no extra infrastructure requirement; admitted paid calls remain accountable after rollback. If adopted, its typed boundaries must carry into production without a second orchestration framework. Enables only the separately admitted live portion of GL-16, not canonical FULL acceptance.

### GL-05 — role-specific model, transport, price and prompt modernization

- **Result / mapping:** original 05; Slice100 Tasks 6/12 and runtime model/pricing obligations. Replace shared-client limitations with explicit role configurations, not a wholesale model upgrade.
- **Producer → consumer:** admitted role/model/prompt configuration → transport adapter → structured typed response and usage → governed budget/receipt. Follow the exact model candidate/qualification matrix in the master plan; candidate is not qualified.
- **Files / seams:** `src/idis/services/extraction/extractors/llm_client.py`; `src/idis/analysis/agents/llm_specialist_agent.py`; `src/idis/debate/roles/llm_role_runner.py`; `src/idis/analysis/scoring/llm_scorecard_runner.py`; `src/idis/services/runs/layer2_ic_challenge.py`; `src/idis/api/routes/runs.py`; `src/idis/services/prompts/registry.py`, `promotion_policy.py`, `versioning.py`; `prompts/`; existing budget/pricing modules located by current callers.
- **Dependencies / parallelism:** GL-01; adapter/prompt design can run beside the first cycle. Integration in `runs.py` serialized with GL-09/10/12. Live qualification requires explicit spending and data permissions.
- **Steps / interfaces:** introduce role-to-config resolution; separate clients per role; implement supported reasoning/output/tool transport; strict parsing of block types/refusal/truncation; price admission/cache accounting; fail-closed fallback; review all 19 prompt families and missing IC advocate. Pin model/prompt/adapter/price versions in receipts.
- **Verification / acceptance:** V-01–05; existing `tests/test_slice99_prompt_model_linkage.py`, `tests/test_slice99_prompt_governance.py`, `tests/test_prompt_registry.py`, `tests/test_slice96_provider_budget.py`, role prompt-contract tests. Add transport contract fixtures and later same-evidence live evaluations. No role silently shares an unsuitable model, unpriced call, unsupported forced-tool request or ambiguous retry. Draft prompts stay draft until promotion evidence exists.
- **Recovery / next:** retain prior qualified configuration; do not automatic-fallback to unqualified models. Enables modern live paths for GL-09/12/16/21; later adaptive routing remains GL-19.

### GL-06 — production intake, extraction, materialization and Sanad closure

- **Result / mapping:** original 06; preserve Tasks 1–7 and finish their required production consumption, not reimplement ingestion.
- **Producer → consumer:** authorized uploads/classification/spans → frozen question tasks → admitted extraction → claims/evidence → source provenance/independence/contradictions/defects → Sanad grades and calculation inputs.
- **Files / seams:** `src/idis/services/ingestion/`; `src/idis/services/extraction/task_planner.py`; `src/idis/services/extraction/methodology_task_extractor.py`; `src/idis/services/runs/methodology_extraction_task_execution.py`; `src/idis/services/runs/methodology_postgres_materialization_runtime.py`; `src/idis/services/runs/methodology_sanad_creation_linking_grading.py`; `src/idis/services/sanad/`; `src/idis/persistence/repositories/methodology_extraction.py`.
- **Dependencies / parallelism:** GL-01/03, agreed requirements subset and existing configured prerequisites. Pure materialization fixtures can run offline; native writes require canonical authority or the separately approved GL-04 boundary.
- **Steps / interfaces:** preserve original passages/tables/locators and parser limitations; complete source type/time/COI/competing-observation metadata; stop shells from becoming trusted evidence; retain grade/calculation admission semantics; expose blocked/missing/unsupported files to the analyst. Grade/confidence calibration requires evidence, not silent threshold relaxation.
- **Verification / acceptance:** V-04 with existing extraction execution, claim materialization and Sanad service/model tests; V-08 only when authorized. Source→claim→evidence→Sanad lineage is complete, tenant/run bounded and independent of fixture self-assertion. OCR/media/deferred files remain explicit; real parsing is not proof of substantive extraction quality.
- **Recovery / next:** immutable source/receipt preservation and idempotent replay of committed work, no duplicate live dispatch. Enables full GL-07/10 and canonical GL-21.

### GL-07 — Task 8 governed financial formulas and reconciliation

- **Result / mapping:** original 07; complete Task 8, not just GL-P02. All ten approved policy formula anchors and 107 bindings have truthful execution and conservation evidence.
- **Producer → consumer:** policy-v2 requirements + coordinate-correct observations → deterministic formulas/Calc-Sanad → dashboard, Court/VEP and permitted analysis/deliverable consumers. Preserve entity, period, currency, units, adjustments and source lineage.
- **Files / seams:** GL-P02 files; `src/idis/methodology/importers/fdd_formula_policy_v2.py`; `contracts/fdd_formula_policy_v2.json`; `src/idis/services/runs/methodology_truth_dashboard.py`; current Task 8 materializers/wrappers in `scripts/`; Task 8 tests under `tests/slice100_task8/`.
- **Dependencies / parallelism:** GL-03 and GL-02 formula decisions; GL-P02 provides only one slice. Formula families can have separate non-overlapping test work; shared schemas/contract writes and census are serialized.
- **Steps / interfaces:** implement approved formula dispatch and reconciliation; explicit missing/completeness inputs; all-coordinate calculation identity; exact rounding/sign rules; review SQL classifier and public error boundary/import closure guards; complete required dual exact-byte reviews and current wrapper guard set; only then approved freeze/census/INACTIVE publication under the standing technical conditions.
- **Verification / acceptance:** V-04 targeted formula tests and the current approved Task 8 guard set; V-08 requires application role, fresh sealed namespace, complete FK closure, no placeholder bytes and signed receipt retaining ledger and pre-GRANT receipts. Ledger represents every write transaction; real SQL lexing rejects hidden multi-statements/leading empty statements. POSIX descriptor quarantine remains fail-closed. No admin-run or self-verifying fixture substitutes for the census. Conservation diff shows only approved semantics.
- **Recovery / authority / next:** migration 0039, activation, commits and paid/live work retain their separate holds; a successor INACTIVE artifact is not activation. Restore/recovery follows approved runbook, never destructive pathname cleanup. Enables Task 8 checkpoint and GL-21 only when all obligations, not just calculation outputs, pass.

### GL-08 — remaining financial/commercial calculations

- **Result / mapping:** original 08; complete fund obligations beyond the ten policy anchors, without silently expanding the frozen Task 8 catalog.
- **Producer → consumer:** approved question/cohort rules and evidence → versioned formulas/schedules/sensitivities → supported answers/annex. Examples: retained earnings, DSO/DPO, FCF conversion, debt/lease/working-capital schedules, cohort retention/CAC/LTV and forecast sensitivities where applicable.
- **Files / seams:** `src/idis/calc/formulas/core.py`, `registry.py`, `src/idis/calc/engine.py`, `src/idis/services/calc/runner.py`, `src/idis/methodology/models.py`, `src/idis/methodology/templates/commercial_dd_v1.json`, `src/idis/deliverables/financial_table.py`.
- **Dependencies / parallelism:** GL-02 clarified definitions and GL-07 reusable calculation boundary; can prepare independent numerical oracles earlier. Public/private-company applicability is explicit; no SaaS ratio forced onto an unsuitable case.
- **Steps / interfaces:** resolve ambiguous denominators/signs/tax jurisdictions; design new approved formula versions; add per-coordinate/cohort candidate selection; retain actual/forecast and independent assumptions; create calculation and unsupported-input tests. New schema/API effects require their own review.
- **Verification / acceptance:** V-04 existing formula/reproducibility/Calc-Sanad tests plus proposed obligation-specific tests; every requested result matches an independent reference and remains reproducible; rejected or unavailable inputs do not appear as zero. Financial reviewer disposition recorded.
- **Recovery / next:** prior formula versions remain reproducible; no rewrite of past results. Enables full GL-22 financial/commercial coverage, not a prerequisite for the first two-question pilot unless its selected obligations require a particular rule.

### GL-09 — external-fact persistence, market intelligence and provider migration

- **Result / mapping:** original 09; complete Task 9's disjoint provider-periphery work and separately authorized runtime wiring. Market/commercial answers consume substantive external facts, not merely provider execution IDs.
- **Producer → consumer:** verified company/market identity → applicability/rights/budget/cache checks → provider facts and citations → persisted normalized facts/conflicts → permitted specialists/scoring/IC/deliverables and rights-permitted RAG/graph consumers.
- **Files / seams:** `src/idis/services/enrichment/service.py`, `registry.py`, `connectors/perplexity.py`; `src/idis/services/enrichment/task9/provider_catalog.py`, `provider_admission.py`, `normalized_facts.py` (`materialize_provider_intents()`), `rights_index_policy.py`, `probe_runner.py`; `src/idis/api/routes/runs.py`; persistence consumers identified by the approved intents.
- **Dependencies / parallelism:** GL-01/02 identities and standards; GL-05 transport/pricing for affected live models; typed offline provider work independent of GL-P01. Runtime wiring requires its own authority beyond prior periphery authorization.
- **Steps / interfaces:** wire normalized facts rather than discarding them; persist source/date/retrieval/rights/content identity and consumer edges; migrate unsupported Perplexity transport; distinguish original-source corroboration from synthesized search answers; define fact-age separately from cache TTL; enforce no-store/no-index where required. Select per-case providers by applicability, not by assuming every provider must be called for every question.
- **Boundary:** external facts do not enter internal-only Court/VEP/FULL DEBATE. A provider fact or derived calculation does not acquire internal trust through storage. Provider unavailability is explicit, not an invented N/A exemption.
- **Verification / acceptance:** V-04 `tests/test_enrichment_service_orchestration.py`, `tests/test_enrichment_rights_gate.py`, relevant existing `tests/test_enrichment_connector_*.py` files selected explicitly, and new intent-to-consumer tests. V-08/live probe only with exact authority. All required provider inventory statuses and permitted consumer edges are evidenced; stale/denied/inapplicable/missing outcomes are visible; no unauthorized raw cache/index/export.
- **Recovery / next:** expiry/invalidation preserves receipts without retaining prohibited content; unknown paid outcomes are not retried blindly. Enables market question in GL-16 and full GL-21/22.

### GL-10 — substantive Court/VEP, graph and RAG consumption

- **Result / mapping:** original 10; complete Task 10 substantive evidence handoffs without replacing either debate layer.
- **Producer → consumer:** source-backed claims, calculations, trust findings and authorized retrieval → Court → VEP → FULL DEBATE; permitted graph/RAG and external fact views → specialists/scoring/Layer 2. A list of IDs is not a substitute for relevant content and locators.
- **Files / seams:** `src/idis/services/runs/methodology_evidence_trust_court.py`, `methodology_validated_evidence_package.py`; `src/idis/services/graph/retrieval.py`; `src/idis/services/rag/retrieval.py`, `indexing.py`; `src/idis/analysis/models.py`; `src/idis/debate/roles/llm_role_runner.py`; `src/idis/api/routes/runs.py` context builders.
- **Dependencies / parallelism:** GL-06/07 typed evidence; GL-09 for external Layer 2 views only. Schema and shared context writes serialize with GL-11/12. Offline fixtures can demonstrate boundaries before services are available.
- **Steps / interfaces:** include passages/tables and locators, calculation inputs/results, competing observations, provenance/independence/defects, and material unresolved issues; add bounded authorized retrieval with query/action receipts and source limits; prove what each model sees. Preserve graph findings as substantive data and hydrate RAG matches from authorized sources.
- **Verification / acceptance:** V-04 Court/VEP service tests, `tests/test_slice87_graph_rag_calc_feeding.py`, `tests/test_slice91_scoring_rag_feed.py`, `tests/test_slice92_layer2_vep_reference.py`; proposed content-consumption and cross-boundary denial tests. Identical unsupported IDs cannot pass as substantive evidence; restricted content cannot leak to forbidden layers.
- **Recovery / next:** version contexts; retain old evidence references for audit; derived index rebuild does not rewrite source truth. Enables GL-11/12/13 and the relevant GL-16 stages.

### GL-11 — statement-specific support and enforced Muḥāsibī disciplines

- **Result / mapping:** original 11; GL-P02 provides a thin slice. Remove unsupported citation backfilling and make the three disciplines in `docs/IDIS_Muhasabi_Prompt_Enhancement_v1.md` observable without changing their meaning: Nafs default-interpretation awareness, Mujahada's most consequential assumption challenge, and the documented insight types.
- **Producer → consumer:** substantive context + role output → typed assertions and support checks → reviewed findings, debate/IC inputs and deliverables. Separate reference existence, numerical entailment, semantic support, uncertainty and expert judgment.
- **Files / seams:** `src/idis/analysis/models.py`; `src/idis/analysis/agents/llm_specialist_agent.py` (`_build_risks()`); `src/idis/validators/muhasabah.py`; `src/idis/debate/muhasabah_gate.py`; `src/idis/deliverables/generator.py` (`_extract_facts_from_report()`); `prompts/`; `docs/IDIS_Muhasabi_Prompt_Enhancement_v1.md`.
- **Dependencies / parallelism:** GL-02 standards, GL-P02 contract subset, GL-10 content. Prompt edits depend on GL-05 governance; no same-file parallel writers.
- **Steps / interfaces:** add assertion/assumption IDs and supported/disputed/inferred/missing status; validate cited scope and calculation match; remove generic/first-N reference fallback. Nafs must identify and label the agent's default/conventional interpretation before analysis in `analysis_sections.nafs_check`, exposing stage/sector pattern-matching rather than substituting a generic bias or self-interest checklist. Mujahada must identify the **single assumption that, if wrong, would most materially change the verdict**, record its challenge as an evidence-linked `risks[]` entry, and make its impact and evidence need inspectable. Each analysis-section entry keeps `insight_type` equal to `conventional`, `deal-specific`, or `contradictory`. Observation/inference/hypothesis may be a separately named support classification; it must not replace or alias these insight types. The source enhancement is prompt-level and already fits existing report fields; any additional schema/validation enforcement here is a separately reviewed GL-11 implementation proposal, not a claim that the source document mandates a new architecture. Preserve existing Muḥāsabah gates rather than replacing them with a model critic.
- **Verification / acceptance:** V-04 `tests/test_analysis_muhasabah_enforced.py`, `tests/test_muhasabah_validator.py`, `tests/test_debate_muhasabah_integration.py`, `tests/test_deliverable_no_free_facts.py` and proposed unsupported-assertion regressions. Check a concrete labelled default interpretation; one verdict-changing assumption with evidence-linked challenge rather than a generic list; all three exact insight types; and independence of any support classification. Missing/wrong discipline outputs must not pass merely because citations exist. Negative citation-present-but-irrelevant cases fail; missing facts remain visible. A model review is evidence of a review, never a correctness guarantee.
- **Recovery / next:** version output schema/prompts and retain old reports labelled with their original support standard. Enables dependable GL-12/13/15/22.

### GL-12 — full debate, specialist/scorecard delivery and IC challenge

- **Result / mapping:** original 12; retain separate five-role FULL DEBATE and implement the planned three-role Layer 2 IC advocate/challenger/arbiter. Correct hard-coded Seed scoring and calculation-ID conservation.
- **Producer → consumer:** substantive VEP/internal debate → specialist reports + stage/methodology-specific scorecard + permitted external facts → Layer 2 challenge → qualified final findings/output. No full investment decision from a two-question pilot.
- **Files / seams:** `src/idis/services/runs/orchestrator.py` and its existing `accumulated.update(result)` seam; `src/idis/api/routes/runs.py`; `src/idis/services/runs/layer2_ic_challenge.py` (`run_with_record`, `_run_strict_live`); `src/idis/models/layer2_ic_challenge.py`; `src/idis/analysis/scoring/stage_packs.py`; `src/idis/analysis/scoring/llm_scorecard_runner.py`; `src/idis/analysis/agents/__init__.py`; `src/idis/debate/`.
- **Dependencies / parallelism:** GL-05/10/11; GL-09 for permitted external content; GL-02 for fund/stage rules. Interface design can precede data readiness; shared orchestrator integration serialized.
- **Steps / interfaces:** carry report content and scorecard rationale, not only IDs; add IC advocate under promoted prompt contract; select scoring pack from admitted deal/fund methodology with explicit unknown behavior; replace destructive result-list replacement with reviewed identity-preserving accumulation where demonstrated necessary; cover multi-period/entity calculation candidates. Existing persisted calculations are not assumed lost solely because an accumulated list may be replaced.
- **Verification / acceptance:** V-04 `tests/test_slice87_calc_unification.py`, `tests/test_stage_packs_weights.py`, `tests/test_slice65_layer2_ic_challenge.py`, `tests/test_slice93_layer2_acceptance.py`, `tests/test_slice93_layer2_provenance.py`; proposed multi-coordinate conservation/non-Seed/context-content tests. All intended roles executed with receipts, disputed findings reach IC, scoring uses the selected pack, no calculation lineage is hidden/replaced.
- **Recovery / next:** retain prior report versions; incompatible role/schema changes remain inactive until reviewed. Enables GL-13/16/21. No claim that additional roles alone improve quality; GL-22 measures that.

### GL-13 — system-driven bounded investigation and reassessment

- **Result / mapping:** original 13; new behavioral improvement using existing planning/execution controls, not a parallel autonomous framework.
- **Producer → consumer:** material question uncertainty/challenge → specific proposed evidence request/research/calculation with reason and expected resolution → approved bounded action → new evidence/result → affected trust/analysis/IC reassessment → revised deliverable.
- **Files / seams:** `src/idis/services/extraction/task_planner.py`; `src/idis/debate/`; `src/idis/services/runs/layer2_ic_challenge.py`; `src/idis/api/routes/human_gates.py`; `src/idis/api/routes/overrides.py`; existing runs/budget/provider services; `src/idis/analysis/models.py`. Proposed typed investigation action schema belongs with these existing models after explicit contract review.
- **Dependencies / parallelism:** GL-10/11/12 and action-specific GL-06/07/09; may design action schema earlier. State-machine and shared orchestration edits serialized.
- **Steps / interfaces:** system explains materiality and a falsifiable evidence need; records proposed vs approved vs executed vs failed actions; checks scope/rights/budget/loop count; pilot uses analyst approval; execute only approved action, reassess dependencies and stop on unresolved material issues. Record whether the analyst supplied missing reasoning/answer. Eventual permitted autonomy is subsequent capability, not the pilot claim.
- **Verification / acceptance:** proposed tests/commands require approved action contract; V-01–05 and existing human-gate/calc-loop/debate tests selected by actual paths. At least one challenge produces a useful bounded action and a supported changed or explicitly unchanged conclusion; denial/exhaustion/duplicate/unknown outcome do not dispatch. No retrieval-completed flag masquerades as resolved uncertainty.
- **Recovery / next:** immutable action log; cancel pending unexecuted actions; reconcile unknown outcomes before retry. Enables useful reassessment in GL-16; later repeated autonomy under GL-19 remains separately bounded.

### GL-14 — launch, review and correction journey

- **Result / mapping:** original 14; explicit methodology selection, traceable analyst review and basic correction/reuse, not hidden manual patching.
- **Producer → consumer:** onboarding/frozen methodology choice + upload → valid FULL request → gate/findings review → attributable correction → invalidated/recomputed outputs. Fund and user permissions stay authoritative.
- **Files / seams:** `ui/src/app/deals/[dealId]/truth-dashboard/page.tsx`; `ui/src/lib/idis.ts`; `src/idis/api/routes/runs.py`; `src/idis/api/routes/human_gates.py`; `src/idis/api/routes/overrides.py`; `openapi/IDIS_OpenAPI_v6_3.yaml`; `ui/src/lib/openapi.ts` generated only through approved workflow.
- **Dependencies / parallelism:** GL-02/03 API identity, GL-P03 correction semantics. Accept the offline/interface slice first using explicit contract fixtures; this slice is an input to GL-21. Canonical runtime journey validation occurs within or after GL-21 admission, not as a prerequisite to starting GL-21. Mocked UI evidence must not imply live functionality.
- **Steps / interfaces:** add frozen methodology selection to launch request; enforce version/tenant/deal scope server-side; show partial/missing/disputed status; produce legitimate review gates; persist correction identity/reason and expose regeneration status. Capture active review time separately from waiting time.
- **Verification / acceptance:** V-06 and targeted route/OpenAPI tests after inspecting actual fixtures; proposed UI journey tests. Wrong/stale/no methodology cannot silently launch; authorized correction is audited and invalidates all affected outputs; unauthorized correction/export fails. A UI demo with mocked API remains labelled mocked.
- **Recovery / next:** retain old artifacts/audit; do not replay live runs merely to repair UI state. Enables GL-15/18/24 and full journey acceptance.

### GL-15 — complete four-part fund delivery package

- **Result / mapping:** original 15; GL-P03 thin slice, Slice100 Task 14 full catalog/manifest obligations retained.
- **Producer → consumer:** reviewed obligation answers, calculations, evidence, contradictions and IC findings → response workbook, narrative diligence report, evidence/calculation annex and IC memo from one reviewed finding version.
- **Files / seams:** GL-P03 files; `src/idis/deliverables/artifact_catalog.py`, `artifact_resolver.py`, `manifest_review.py`; `src/idis/api/routes/deliverables.py`; `src/idis/storage/`.
- **Dependencies / parallelism:** GL-02/11/14 and fund template decision for final acceptance; exporter work can precede full GL-22 coverage. No full product acceptance from two rows or a JSON artifact alone.
- **Steps / interfaces:** preserve original workbook; map every row/child obligation/shared instruction; render narrative/annex/memo consistently; verify authorized accessible evidence links; retain limitations and follow-ups; complete all 15 catalog rows/14 manifest entries under Task 14 and authenticated hash-bound downloads.
- **Verification / acceptance:** V-04 deliverable/API/artifact tests selected from current paths plus render-and-inspect outputs; V-06 where relevant. Every obligation has a visible disposition; all material assertions retain support/status; artifact hashes and downloads match; no confidential content in public receipts. Fund confirms final format before final release.
- **Recovery / next:** immutable versioned artifacts and safe revocation of unauthorized access, no source mutation. Enables GL-22/24 acceptance package.

### GL-16 — bounded two-question end-to-end evaluation

- **Result / mapping:** original 16; evaluate projected long-term EBITDA margin and addressable market size on one existing case. This is a partial evaluation, not comprehensive investment diligence or Slice100 acceptance.
- **Inputs → outputs:** controlled case evidence, independent numerical reference and expert answer rubric → stage-by-stage evidence, two qualified answers, review/correction record and measured effort/time/cost. Management TAM is distinguished from independently corroborated TAM under the fund's standard.
- **Files / seams:** existing `src/idis/evaluation/` framework, GL-06–15 components, `src/idis/evaluation/synthetic_strict_runtime_rehearsal.py` as a labelled offline reference only; proposed pilot manifest/harness location must be chosen within the existing evaluation package, not a new framework.
- **Dependencies / parallelism:** minimum relevant slices of GL-02/03/06/07/09/10/11/12/13/15, not completion of unrelated full-system work. Offline replay can proceed incrementally; live evaluation requires canonical admission or separately approved GL-04, qualified prompts/models, services, data rights and budget. Any unavailable stage is explicitly simulated/incomplete.
- **Steps / interfaces:** bind exact contract/version and order; follow the master plan's canonical stage sequence: extraction/Sanad/calculations and dashboard, identity/enrichment, graph/RAG/conflict planning, internal-only Court/VEP, separate FULL DEBATE, specialists/scoring/readiness, Layer 2 IC and delivery/review. External research can execute before Court but its content remains excluded from Court/VEP/FULL DEBATE and enters only permitted Layer 2 consumers. A replay/subset states omitted stages and does not claim canonical execution. Include one system-proposed bounded investigation and one analyst correction; compare before/after answers and unchanged portions. No inference about May22 execution from newer code.
- **Verification / acceptance:** follow VERIFICATION_AND_RUNBOOKS pilot rubric: exact numeric reference, statement support, completeness, contradictions, useful reassessment and visible unknowns. Log all role calls/retries/failed spend, user active time, waiting time and wall time. No benefit is established if quality/completeness does not improve or analyst effort/cost increases without a justified gain.
- **Recovery / next:** preserve replay/live labels and evidence versions. Result calibrates GL-04 decision and subsequent packet estimates; successful component evaluation does not unlock canonical FULL automatically.

### GL-17 — evaluate and adapt Anthropic financial-services content

- **Result / mapping:** original 17; explicit evaluated reuse, not installation of an independent agent runtime. Ordinary Claude API calls are not financial-skill execution.
- **Inputs → outputs:** pinned Apache-2.0 official skill/plugin/template content + same case/model/expert rubric → adopt/adapt/retain decision and reviewed prompt/checklist patterns. Data rights remain independent of repository license.
- **Files / seams:** `src/idis/analysis/agents/financial_agent.py`, `llm_specialist_agent.py`; `src/idis/services/prompts/`; `prompts/`; `src/idis/calc/`; `src/idis/deliverables/`; methodology mapping from GL-02. Official source/pin and candidate matrix are authoritative in the master plan.
- **Dependencies / parallelism:** GL-02/05/11; same-evidence comparisons can accompany GL-16. Content inspection is separable from runtime adaptation; no automatic plugin/tool permissions.
- **Steps / interfaces:** prioritize spreadsheet audit, three-statement modeling, diligence checklist, unit economics, competitive research and IC memo; remove PE/public-market-only defaults unsuitable for VC; retain fund requirements and Sanad/Calc-Sanad; record license/notice modifications; route generated formulas through governed calculations. Do not import reference orchestrators or broad MCP tool access.
- **Verification / acceptance:** proposed paired evaluation harness under existing evaluation package; same model/evidence where practical, blinded expert rubric, failure cases and measured adaptation effort. No claimed savings before measurement; reject candidates that worsen support, numerical correctness, fund fit or effort/cost.
- **Recovery / next:** revert candidate prompts independently; retain the existing qualified path. Successful adaptations feed GL-05/08/11/15; no adoption required merely because a skill exists.

### GL-18 — generalized fund onboarding and methodology preservation

- **Result / mapping:** original 18; different funds can define evidence standards and deliverable destinations without hard-coded assumptions or modifying historic frozen versions.
- **Producer → consumer:** fund configuration, selected methodology/template and scope → frozen version/child obligations/role routing → tasks/scoring/deliverables and analyst review.
- **Files / seams:** `src/idis/methodology/models.py`, `src/idis/methodology/importers/`, `src/idis/services/extraction/task_planner.py`, `src/idis/analysis/scoring/stage_packs.py`, methodology API/UI consumers discovered from current routes; GL-14 launch and GL-15 output modules.
- **Dependencies / parallelism:** GL-02/14/15 contracts; may prototype independent import fixtures earlier. Full multi-fund generalization is not a pilot prerequisite.
- **Steps / interfaces:** configure fact/calculation/judgment/completeness/evidence criteria separately; validate unsupported requirements instead of silently ignoring them; freeze explicit mappings and role aliases; preserve historic behavior; bind report template and scoring stage. New onboarding API/schema requires explicit review.
- **Verification / acceptance:** V-04 methodology/importer/planner tests plus proposed second-fund synthetic fixture; V-06 affected UI. Two differing methodologies retain their own standards/output placement, tenant isolation and historic reproducibility; no Seed/default template leakage.
- **Recovery / next:** publish new frozen versions rather than edit old versions. Enables broader GL-22/24 readiness; fund-specific unresolved rules stay visible.

### GL-19 — valid work reuse, later adaptive routing and bounded autonomy

- **Result / mapping:** original 19. Basic release reuse/correction invalidation is required; adaptive model routing and repeated autonomous investigation are later capabilities, not claims of the first pilot.
- **Producer → consumer:** prior reviewed artifacts + dependency/rights/freshness identities → reuse-or-recompute decision → governed execution and updated answers.
- **Files / seams:** existing `src/idis/services/runs/`, `src/idis/services/calc/runner.py`, `src/idis/services/enrichment/cache_policy.py`, prompt/model admission services, correction/export modules from GL-14/15 and investigation schema from GL-13.
- **Dependencies / parallelism:** basic reuse follows GL-P03/14; adaptive routing/repetition requires GL-05/13/16 measurements and explicit new policy. Do not block a useful first pilot on later optimization.
- **Steps / interfaces:** prove dependency identity and invalidation across source/methodology/formula/model/prompt/rights/freshness changes; reuse only valid work; later qualify task-appropriate routing against same rubric; bounded loops escalate genuine exceptions rather than ask a human to manage every retrieval. No automatic budget increase.
- **Verification / acceptance:** existing reproducibility/cache tests plus proposed dependency-invalidation/routing budget tests. Changed evidence cannot leave stale supported conclusions; valid unchanged work avoids redundant work; adaptive policies improve measured quality/effort/cost without weakening gates.
- **Recovery / next:** disable optimization while preserving deterministic baseline; every action remains receipted. Enables sustainable operation and GL-20 learning inputs; not required to call the initial pilot successful.

### GL-20 — learning from validated analyst corrections

- **Result / mapping:** original 20; subsequent capability. Use validated corrections as curated evaluation/examples, not uncontrolled self-training or promotion of model agreement to truth.
- **Producer → consumer:** attributable reviewed corrections and resolution evidence → consented/redacted versioned evaluation/example set → controlled prompt/routing improvement evaluation.
- **Files / seams:** existing `src/idis/evaluation/`, review/audit APIs, `src/idis/services/prompts/`, `prompts/`; proposed curation schema only after privacy/ownership decision.
- **Dependencies / parallelism:** GL-14/19 and sufficient validated correction records; fund consent/data retention decision. Not a first-pilot or interim-release prerequisite.
- **Steps / interfaces:** classify correction cause; distinguish user-supplied reasoning from system capability; exclude disputed/unvalidated material; split evaluation from tuning examples; review cross-fund leakage; measure regressions before promotion.
- **Verification / acceptance:** proposed provenance/privacy/holdout tests and controlled evaluations. No correction becomes authoritative without validation; no unauthorized reuse across funds; measurable improvement and no material regression required for promotion.
- **Recovery / next:** revoke examples and revert prompt versions while preserving audit. Completion contributes to full modernization, not proof the system improves itself autonomously.

### GL-21 — canonical FULL integration, proof ledger and acceptance gates

- **Result / mapping:** original 21; complete remaining Slice100 Tasks 8–15 obligations and Gate A/Gate B. This is distinct from GL-16 component evaluation.
- **Producer → consumer:** admitted contract/version, schemas, prompts, rights, budgets, services and complete stage producers → canonical strict FULL order → durable evidence/receipts/artifacts → signed acceptance checkpoint.
- **Files / seams:** `src/idis/pipeline/contract_registry.py`; `src/idis/services/runs/strict_full_live.py`; canonical orchestrator and `src/idis/api/routes/runs.py`; `src/idis/evaluation/real_example_terminal_reconciliation.py`; current sealed runbooks/wrappers and `tests/slice100_task7/`, `tests/slice100_task8/`; exact later-task test files resolved from the controlling plan.
- **Dependencies / parallelism:** accepted offline/interface slices of GL-03/05–15 and configured services from GL-23A, plus available replay feedback from GL-16. GL-14 live journey acceptance and GL-16 canonical-live evaluation are verified within or after GL-21, not prerequisites to it. This staged dependency prevents a GL-14/16/21 cycle without waiving any final acceptance. Full later optimization GL-19/20 is not needed unless explicitly in the relevant release definition. Gate planning may proceed earlier; activation/migrations/live runs cannot.
- **Steps / interfaces:** prove every canonical edge and receipt; finish Task 11 all 27 restart cuts and all 48-rule three-way equality once their producers exist; Task 12 all 19 prompt families; Task 13 real gate/proof ledger; Task 14 full artifact catalog/manifest; Task 15A offline before Gate A and Task 15B configured-service checks after Gate A/before preparation proof. Follow current versioned plan for exact activation and private priced run sequence.
- **Verification / acceptance:** V-08 only with explicit authority and exact reviewed wrapper/version. No stale v21 hard-coded proof, fabricated native root, admin census, missing FK closure, placeholder bytes or simulated stage can be accepted as canonical execution. Required CI tied to exact source SHA plus private evidence receipts; all holds satisfied explicitly.
- **Recovery / next:** execute reviewed restore/recovery procedures; preserve complete failed-run budget/evidence. Enables canonical real-case GL-22 and final release gates, not automatic deployment permission.

### GL-22 — full real-case substantive quality and acceptance matrix

- **Result / mapping:** original 22; verify all 79 financial items, 21 commercial questions and child obligations on the real cases, not just the two pilot answers.
- **Producer → consumer:** authorized private cases + expert reference calculations/rubrics + canonical results → complete requirement→answer→evidence→calculation→limitations→fund-feedback package.
- **Files / seams:** existing `src/idis/evaluation/`, methodology coverage services, deliverable modules; proposed private result manifest through approved storage, sanitized summary in `docs/go-live/RUN_STATE.md`.
- **Dependencies / parallelism:** GL-02/08/09/11/15/21; independent reference preparation can happen earlier. Previously seen cases are not a pristine holdout; add unseen/synthetic adversarial cases only with honest labels.
- **Steps / interfaces:** assess numerical correctness, evidence support, material contradictions, question completeness and usefulness; measure corrections/active analyst time/waits/elapsed/cost; separate unanswerable source limitations from system failures; trace all fund feedback to resolution or accepted limitation. No percentage denominator hides deferred or missing obligations.
- **Verification / acceptance:** master-plan thresholds and VERIFICATION_AND_RUNBOOKS; zero unsupported material final conclusion; exact required calculations; all obligations have disposition and all material gaps visible. Model-only grading does not substitute for user/fund substantive validation. Thresholds requiring fund agreement remain provisional until accepted.
- **Recovery / next:** failures return to specific producer/consumer packets with regression cases; retain failed evidence. Enables GL-24 only after material release failures resolved or explicitly accepted under an interim scope.

### GL-23 — deployment, stores, recovery and operations

- **Result / mapping:** original 23; deploy and operate the existing PostgreSQL/Neo4j/pgvector/Redis/document-artifact architecture with truthful health and recovery, not infrastructure for its own sake.
- **Files / seams:** `docker-compose.yml`, `Dockerfile`, `deploy/`, `src/idis/storage/`, `src/idis/persistence/`, `src/idis/services/rag/pgvector_health.py`, existing health/audit/worker controls and UI build configuration.
- **GL-23A prerequisites:** after GL-01 and explicit service/infrastructure authorization, establish a reviewed execution environment, role separation, storage paths, index/migration compatibility, secrets/residency, backups and budget caps. This phase supplies GL-21; it does not depend on GL-21 acceptance.
- **GL-23B release hardening:** after GL-21 integration and GL-22 workload envelope, package strict worker/UI/prompt assets, dependency locking, monitoring, restore and operator support procedures. No new service starts/deployment are authorized by this document.
- **Producer → consumer / boundary:** source-of-truth PG/object storage → derived graph/vector/index/cache consumers; recovery restores authoritative evidence first and rebuilds derived state without fabricating fresh results. Redis cache/coordination loss must not authorize duplicate paid work.
- **Verification / acceptance:** V-08 and deployment/recovery checks from VERIFICATION_AND_RUNBOOKS; prove RPO/RTO and service-window targets under the agreed topology rather than just configuring backups. Missing required services fail closed or show an explicitly approved degraded mode; no false healthy readiness. Security/tenant/access/audit/artifact retrieval checks cover the full journey.
- **Recovery / next:** rehearsed rollback/restore, accountable operator and escalation; no assumption of hired or 24/7 support. Enables GL-24 deployment acceptance; infrastructure cost remains a planning allowance until quoted and authorized.

### GL-24 — fund UAT, final acceptance and operational handover

- **Result / mapping:** original 24; complete the agreed product, or explicitly label any earlier scoped release as interim with the remaining modernization backlog intact.
- **Producer → consumer:** GL-22 quality matrix + GL-23 operational evidence + four reviewed deliverables → fund review/feedback → owner release decision and operator runbook.
- **Files / seams:** `docs/go-live/RUN_STATE.md`, `VERIFICATION_AND_RUNBOOKS.md`, existing delivery/review UI and artifacts. Private feedback stays in controlled storage; publish only safe acceptance references.
- **Dependencies / parallelism:** GL-14/15/18/21/22/23B and fund format/quality/service-window decisions; GL-19/20 remain tracked if a clearly bounded interim release precedes full modernization.
- **Steps / interfaces:** demonstrate onboarding→methodology selection→upload→execution→review→correction→regeneration→export; reconcile every fund comment; record release scope/limitations; demonstrate recovery/monitoring; agree support ownership and fund contact; owner authorizes deployment/release separately.
- **Verification / acceptance:** component-to-evidence checklist in VERIFICATION_AND_RUNBOOKS complete with exact commit/config/artifact references; fund-dependent targets confirmed; no unresolved material failure concealed; next action and remaining obligations recoverable from GitHub. A documentation PR, successful demo or model review is not fund acceptance.
- **Recovery / next:** release rollback/communications follow the approved operator runbook; maintain immutable prior artifacts and audit. Full completion requires all agreed capabilities, including later modernization explicitly retained in GL-19/20, or an explicit owner/fund scope amendment—not silent backlog deletion.

## 4. Critical path, parallelism and stop points

**First cycle:** GL-01 → GL-P01 → GL-P02 → GL-P03 → measured recalibration. After the current CIR-04 green transition gate, GL-02 mapping and GL-05 design can proceed in parallel under non-overlapping ownership. Current guidance is at most two implementation writers plus a fresh reviewer within the eligible current task; this is a coordination limit, not a staffed team or permission to skip the transition gate.

**Minimum useful two-question pilot:** first cycle + relevant GL-06/09/10/11/12/13/15 slices → GL-16. Use replay to expose integration/quality failures early. A live pilot additionally needs legitimate admission, terminal persistence, qualified prompts/models, services, rights and budget. The choice is canonical production admission when available, or an explicitly approved GL-04—not a hidden bypass.

**Full product:** complete remaining requirement/calculation/provider coverage, canonical GL-21 and substantive GL-22, then operational GL-23B and fund GL-24. GL-23A is the earlier environment prerequisite, avoiding a circular dependency. Later optimization/learning remain tracked and are not relabelled as delivered by the initial pilot.

**Stop/continue rules:** stop the affected packet for missing authority, conflicting frozen semantics, unavailable required evidence, exhausted budget, destructive recovery, unauthorized data use, unresolved material acceptance failure or missing required independent review. Continue other authorized dependency-ready work with non-overlapping files. Do not increase budgets, activate contracts, weaken criteria or substitute synthetic proof silently.

**Calibration:** use measured packet completion, rework, independent-review defects, active owner time, waits and incremental cash. Do not convert old engineering person-days into calendar dates or invent an AI multiplier. Waiting for fund judgments is distinct from model/development effort. Update the rolling forecast and next packet in RUN_STATE after each accepted GitHub-recoverable checkpoint.
