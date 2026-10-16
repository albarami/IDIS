# Verification, acceptance, and operational runbooks

**Status:** execution specification, not a record of completed product verification. Documentation publication is authorized; product implementation, service changes, migrations, activation, tests, and provider calls require their separate authorizations. Start with [START_HERE.md](START_HERE.md) and [BASELINE_MANIFEST.md](BASELINE_MANIFEST.md). Task scope and estimates are in [GO_LIVE_MASTER_PLAN.md](GO_LIVE_MASTER_PLAN.md) and [IMPLEMENTATION_BACKLOG.md](IMPLEMENTATION_BACKLOG.md); authority and integration modes are in [AGENT_EXECUTION.md](AGENT_EXECUTION.md). Record actual results and next action in [RUN_STATE.md](RUN_STATE.md), with publication checks in [DOCUMENTATION_REVIEW.md](DOCUMENTATION_REVIEW.md).

This document preserves the existing IDIS architecture and Slice100 obligations. Source references below were checked against the local development tree, `C:\Projects\IDIS\IDIS-slice100`, including uncommitted work. They are not a claim that this documentation branch contains that implementation. Do not infer readiness from the existence of a test, a historical report, a passing documentation check, or an AI review.

## 1. Evidence vocabulary and record requirements

Every result must distinguish five things: documented intent, implemented source, stored data, demonstrated execution, and measured analytical quality. Record an explicit status for each; `UNKNOWN`, `BLOCKED`, `NOT_EXECUTED`, and `NOT_APPLICABLE_WITH_EVIDENCE` are legitimate outcomes, not PASS.

Each acceptance record must identify:

- Task/requirement IDs and child obligations; approved scope and decision references.
- Exact Git commit, additional local patch identity if any, pipeline contract/version/hash, methodology version/hash, prompt versions, model IDs/settings, dependency/runtime versions, and environment class.
- Whether source material is synthetic, real, or unknown; whether each model/provider/stage is live, hermetic, replayed, simulated, unavailable, or incomplete. Label mixed runs stage by stage.
- Test/evaluation command or named procedure, start/end time, outcome, failures/skips, artifact hashes, and independent-review disposition. Skipped required checks are not green.
- Safe evidence references and access instructions, not confidential evidence contents. Keep private artifacts in approved restricted storage; a public GitHub record contains hashes, aggregate results, and authorized-access references only.
- Active owner time, analyst contributions, elapsed runtime, waiting time, provider usage, retries/unknown outcomes, and incremental cash cost. Cost is not inferred from token limits or subscription price.
- Next action, owner, blocker, and approval needed. Accepted checkpoints must be recoverable from GitHub, not merely described as present on this PC.

A citation ID existing is not proof that its source supports a statement. A valid Sanad/Calc-Sanad, model review, or structured schema likewise does not independently guarantee factual or analytical correctness.

## 2. Documentation verification versus product verification

### Documentation publication checks — authorized for this package

1. Confirm the dedicated branch starts from the intended freshly verified GitHub base; inspect its complete outgoing commit range and complete diff. Include only the documentation package. Preserve unrelated local work and unpublished implementation commits.
2. Check every package Markdown file for readable structure, working package links, consistent task IDs/dependencies/status, and explicit unknowns. Verify implementation references against the recorded local baseline; clearly identify references unavailable in the documentation-only checkout.
3. Review for credentials, environment-variable values, private company/document names, confidential workbook text, source excerpts, data-room filenames, and embedded private attachments. Publish only safe requirement coordinates, aggregates, and references.
4. Independently review completeness against the user-approved architecture, solo execution/cost model, authority boundaries, and execution modes. Correct findings before publication.
5. Inspect staged diff and `git diff --check`; commit only the reviewed package. Push the documentation branch, open a PR, and verify remote file contents at that exact commit.
6. Inspect required GitHub checks against that SHA and record their actual states. CI triggered by the PR is distinct from locally running product tests. A queued, skipped, unavailable, or failing required check is not PASS. Follow current repository merge rules; publication does not itself authorize merging.

Do not run `make check` as a read-only documentation check: the current `Makefile` invokes `ruff format .`, which edits files, followed by lint, type checking, tests, and the forbidden scan. Do not run provisioning, migrations, backup/restore drills, gates, or health probes merely because they appear below.

### Future product checks — only after relevant authorization

The following entry points exist in the assessed development tree. They are commands to be selected by an authorized implementation packet, **not commands executed to prepare this package**. Use a disposable, scoped test environment where required; do not point tests at private production stores.

| Purpose | Existing entry point / command | Required evidence and limitation |
|---|---|---|
| Non-mutating formatting/lint/type checks | `ruff format --check .`; `ruff check .`; `mypy src/idis --ignore-missing-imports` | Full output and exact SHA; do not hide the policy-v2/legacy expected-answer mismatch with a cast. |
| Question/import/planning compatibility | `pytest tests/test_methodology_models.py tests/test_extraction_task_planner.py tests/test_fdd_methodology_importer.py tests/test_commercial_methodology_template.py -v` | Add packet-specific positive/negative cases, frozen-surface ownership proof, version-bound admission, and byte-identical legacy expectations. Existing tests alone are insufficient. |
| Extraction protocol | `pytest tests/test_run_methodology_extraction_task_execution_service.py tests/test_methodology_extraction_execution_models.py -v` | Demonstrate admission before dispatch and terminal persistence before finalization; hermetic transport is not a live extraction receipt. |
| Calculation conservation | `pytest tests/test_slice87_calc_unification.py tests/test_slice87_calc_formulas.py tests/test_slice87_reproducibility_version.py -v` | Extend to multiple entity/period/currency/unit/adjustment coordinates and both methodology and later calculation results; a single duplicate example does not prove complete conservation. |
| Trust and VEP components | `pytest tests/test_run_methodology_evidence_trust_court_service.py tests/test_run_methodology_validated_evidence_package_service.py -v` | Real substantive inputs, namespace exclusions, rejection of shells, dissent/defects, and downstream content consumption. |
| Muḥāsabah and outputs | `pytest tests/test_muhasabah_validator.py tests/test_analysis_muhasabah_enforced.py tests/test_deliverable_no_free_facts.py tests/test_deliverables_generator.py -v` | Add statement-support and no-backfilling cases, material-assumption/Nafs/insight-classification behavior, and correction/regeneration evidence. Schema success is not truth. |
| Repository contract and prompt governance | `python scripts/contract_lock.py verify --repo-root .`; `python -m idis prompts validate --prompts-root prompts --repo-root . --out prompt_governance_report.json` | Preserve exact hashes; the latter writes a report. Registry validation is not live prompt qualification or promotion. |
| PostgreSQL integration | `scripts/run_postgres_integration_local.py`, `scripts/pg_bootstrap_ci.py`, and scoped test suites | Inspect prerequisites before invocation. Provisioning/migration/data-writing behavior requires specific authority; a disconnected/skipped test is not evidence of durable integration. |
| Recovery | `scripts/db_backup_restore.py`; `tests/test_slice99_backup_restore_postgres.py` | Destructive drill only in an explicitly disposable target. Verify tenant isolation, audit contents, application-role permissions, and restoration, not just a backup file. |
| Real-case gate | `scripts/run_real_example_gate.py` and the controlling Slice100 runbook | No generic permission to run. Enforce Gate A/B, exact profiles/contracts, rights, budgets, source immutability, authentication, and current approved CLI. |

Current `.github/workflows/ci.yml` defines `ui-check`, `check`, `postgres-integration`, `evaluation-harness`, `container-build`, `k8s-validate`, `terraform-validate`, `backup-restore-drill`, and aggregate `release-gate`. It runs on PRs to `main` and pushes to `main`. These names are workflow inventory, not a claim that branch protection requires exactly this set. Read current remote rules/check requirements at each integration. PR merge-reference CI must be recorded with both the tested merge SHA and PR head SHA; after the head/base changes, stale results do not establish acceptance of the new combination. The release gate's current manifest and backup checks do not prove the unimplemented Slice100 paths or real-case quality.

## 3. Preserved strict-execution and authority gates

The controlling detailed source is `docs/plans/2026-07-15-slice100-strict-real-example-live-dry-run.md`, with its later amendments taking precedence over stale earlier prose. The assessed runtime registry, `src/idis/pipeline/contract_registry.py`, registers `full-strict-v21` as **INACTIVE**. The Task8 v27 materialization is a successor candidate, not a runtime activation. Neither documentation publication, candidate freeze, census success, nor a component pilot changes that state.

The inherited strict sequence has 28 canonical steps:

```text
 1 DATA_ROOM_INVENTORY_PACKAGE
 2 DATA_ROOM_INGESTION_HANDOFF
 3 INGEST_CHECK
 4 DOCUMENT_PREFLIGHT
 5 METHODOLOGY_COVERAGE_INIT
 6 METHODOLOGY_EXTRACTION_TASK_PLANNING
 7 METHODOLOGY_EXTRACTION_TASK_EXECUTION
 8 METHODOLOGY_CLAIM_MATERIALIZATION
 9 METHODOLOGY_EVIDENCE_ITEM_MATERIALIZATION
10 METHODOLOGY_SANAD_CREATION_LINKING_GRADING
11 METHODOLOGY_DETERMINISTIC_CALCULATION
12 METHODOLOGY_TRUTH_DASHBOARD
13 EXTRACT
14 GRADE
15 CALC
16 METHODOLOGY_COMPANY_IDENTITY_PACKAGE
17 ENRICHMENT
18 GRAPH_EVIDENCE
19 RAG_EVIDENCE
20 METHODOLOGY_EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN
21 METHODOLOGY_EVIDENCE_TRUST_COURT
22 METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE
23 DEBATE
24 ANALYSIS
25 SCORING
26 METHODOLOGY_LAYER2_READINESS_PACKAGE
27 LAYER2_IC_CHALLENGE
28 DELIVERABLES
```

This is an order specification, not a claim that all producers/consumers are complete. Layer1 Court/VEP and the separate FULL DEBATE remain distinct. Internal data-room evidence, calculations, and internal conflict findings may enter them; external provider facts/findings must not. External facts join permitted specialists, scoring, external conflict checks, Layer2 readiness/IC, and deliverables under provenance and rights controls. Graph/RAG must enforce internal/external namespaces, including retrieval and consumption receipts, not merely labels.

| Gate / obligation | Required completion evidence | Authority and stop condition |
|---|---|---|
| Task8 financial contract/formulas | Version-disjoint expected-answer closure; approved formula semantics; all 79 row dispositions; calculation/Calc-Sanad lineage; complete signed census package | Migration0039, implementation beyond separately authorized scope, activation, and live/priced work keep their existing holds. A new documentation commit does not authorize a product commit. |
| Task8 SQL-authority census | Application role; fresh sealed namespace; complete FK closure; no placeholder bytes; every write transaction represented, including repository engines, internal freeze operations, direct cursors, and rollback probes; signed receipt retaining ledger and pre-GRANT authority receipts | No admin-run or instrumented-subset census. Dual exact-byte review and repinned wrapper guards must pass. Preserve permanent REDs for multi-statement and leading-empty-statement smuggling; use real SQL lexing. Unsupported POSIX-quarantine platforms fail before reservation/profile/database access; do not replace owned-descriptor truncation with pathname unlink. |
| Task9 providers | Verified identity/applicability before rights/credentials/budget/cache/HTTP; all registry-derived 18 providers have admissible outcome/probe or evidenced zero-call N/A; normalized facts persisted and consumed | Unavailable applicable provider is a blocker, not false N/A. Rights-restricted facts remain excluded from prohibited indexes and retain permitted non-index consumers. Perplexity prose is not primary evidence. |
| Task10 consumption | Substantive Court/VEP/graph/RAG/fact content in the permitted consumers; concrete deep-consumption receipts | IDs, status booleans, empty packages, and planned-but-unexecuted checks do not close edges. |
| Task11 agents and restart | Eight specialists, five FULL roles, three IC roles; reports/scorecard/findings/dissent delivered; all 27 restart cut points; all 48 rules satisfy `derived = persisted expectations = receipts` | Task7 proved available producers/schema/guards, not the complete later-producer proof. Preserve stale-worker fencing and native output rehydration. |
| Task12 prompts | Runtime-derived complete 19-family inventory, evaluated model/prompt hashes, thresholds, required human approvals, atomic PROD pointer, durable promotion audit | Existing draft prompts are not qualified; a different/newer model is not an approved substitution. |
| Tasks13–14 real-case gate/package | Safe external gate ledger; exact corpus reconciliation; authenticated verification of all 15 catalog rows/14 manifest entries; hashes, isolation, restart, no source modification or leakage | No strict fallback client; no fabricated native roots/receipts or success through nullability escapes. |
| Task15A | Authorized offline capstone, disposable-store verification, independent reviews; scoped historical ingestion exception only as explicitly authorized | No configured semantic/provider acceptance services before Gate A. Required skipped test blocks acceptance. |
| Gate A / Task15B | Explicit owner approval; reviewed migrations/profile; configured store/model/provider/media probes; rights/budget imports; bounded non-private live evaluation and promotion; final-profile conformance | Gate A does not authorize private FULL analysis. Stop on failure; revalidate exact input and authority bindings before reuse. |
| Gate B | Separate owner approval; fresh two-case `may_proceed=true` proofs bound to corpus/tenant/deal/profile/contract/methodology/prompts/provider receipts and expiry; visible remaining budgets | Only then queue the authorized private priced FULL runs. Stop on first failure and preserve diagnostic evidence. |
| Product release | Full journey, full requirements, real-case quality, recovery, security/rights, fund acceptance, and exact-SHA release evidence | A two-question component result cannot be relabeled full Slice100 acceptance or product go-live. |

## 4. Two-question acceptance demonstration

### Scope and honest baseline

Use the already selected existing case and these questions: (1) whether the projected long-term EBITDA margin is realistic; (2) addressable market size and its support. Keep confidential case identity and source documents outside GitHub. The retained May22 outputs are historical execution evidence, not a reference answer or evidence of current-source behavior. Independently checked numerical and analytical reference answers are still needed; neither case is a pristine unseen holdout.

Prepare a restricted reference pack with document locators and versions, financial operands/reconciliations, management forecast assumptions, externally corroborated market sources with dates/rights, material contradictions, acceptable interpretations, and explicitly unanswerable obligations. The owner/fund must validate material financial/commercial judgments. Another model cannot substitute for that validation. If the reference cannot be validated, record which quality claims remain unmeasured.

### Preferred first route: production-relevant components plus truthful replay

Complete the shared schema, financial/support, and answer-to-deliverable packets first. Do not build a separate live admission framework by default. Replay or hermetic transport may evaluate serialization, calculations, provenance handling, context, verification, and outputs, but cannot mint live extraction proof or demonstrate canonical FULL success.

The existing production extractor is phased: `DurableRunMethodologyExtractionTaskExecutionService.run()` in `src/idis/services/runs/methodology_extraction_task_execution.py` prepares the request, commits admission/budget, dispatches, validates, commits terminal results/budget audit, then finalizes. `src/idis/services/runs/methodology_postgres_runtime.py` supplies `admit_call()` and `terminalize_call()`. These depend on real native run/step authority and fenced leases. Completing production admission is the default live route; it remains unavailable while its required activation and separate gates are unmet.

After measured first-packet work, compare the remaining production path with the **entire** cost/risk of a separately versioned evaluation authority, native-compatible persistence, security, recovery, and maintenance. A separate live route is conditional on an owner-approved material learning benefit that replay cannot provide; it must not impersonate strict steps, bypass the inactive contract, weaken FK/lease/budget guards, or become a parallel product architecture. No such route is authorized or implemented by this document.

### Demonstration sequence and observations

| Stage | Required observable result |
|---|---|
| Methodology and extraction | Frozen question/child-obligation/source-row mapping; expected-answer contract admits only its version; relevant passages/tables with locators and extraction outcomes. |
| Sanad and calculation | Preserve entity, period, currency, units, source, adjustments, competing observations, defects/independence. Calculate EBITDA/margin from approved operands and formula versions with reproducible results and Calc-Sanad; do not confuse arithmetic correctness with forecast realism. |
| Market intelligence | Distinguish management estimates from independent evidence, actual from forecast, geography/segment/time period, and TAM/SAM/SOM where required. Use explicit assumptions and versioned calculations; record unavailable or stale corroboration. |
| Court/VEP and FULL DEBATE | Internal substantive evidence only; full findings/qualifications/calculations available, not IDs alone. Persist dissent and unresolved contradictions. In replay, label every non-live role and any unavailable stage. |
| Specialists, scoring, Layer2 IC | Relevant internal evidence plus authorized external facts; specialist reports and scorecard reach IC advocate/challenger/arbiter. Stage-specific scoring replaces hard-coded Seed only after approved semantics. Every selected role/model/prompt is identifiable. |
| System-driven investigation | System identifies material uncertainty, explains decision impact, proposes a specific evidence request/research/calculation with expected result and bounded scope/cost. Owner approves the pilot action; system then incorporates admissible results and reassesses. Record approval versus owner-supplied reasoning/evidence/answer separately. |
| Verification and review | Statement-specific source/calculation support, numerical recomputation, source applicability, independence, contradiction handling, and the exact GL-11 disciplines: Nafs default interpretation; single most consequential assumption challenge/Mujahada; `conventional`, `deal-specific`, `contradictory` insight types. Support classification is separate. Preserve uncertainty and analyst decisions; do not backfill unrelated citations. |
| Regeneration and outputs | A reviewed correction invalidates affected outputs and triggers bounded recomputation/reassessment; versioned workbook answer, narrative, evidence/calculation annex, and IC memo update consistently without deleting prior evidence or qualifications. |

Compare the improved path against the existing approach on the same authorized evidence and expert rubric, holding model/settings constant where practical. For Anthropic financial-services adaptations, compare unchanged-model variants and record content version/license/dependencies; skill-generated calculations must use the governed calculation/verification path. Repeated demonstrations on these familiar cases are engineering evaluations, not generalization estimates.

The improvement fails if answers are less correct/complete, material support or dissent is lost, an owner has to supply the missing reasoning, review effort increases without justified quality gain, or added cost/latency does not deliver a useful measured improvement. Report tradeoffs and inconclusive samples; do not cherry-pick one successful run.

## 5. Quality, effort, latency, and cost acceptance

These are **proposed acceptance thresholds**, not achieved results. Owner approval is required before evaluation; fund-dependent judgments/targets remain provisional. Existing contractual guards are not relaxed to hit a target.

| Dimension | Proposed criterion | Measurement |
|---|---|---|
| Numerical correctness | 100% of reference calculations correct at approved precision; zero material unexplained numeric error | Independently recompute from exact source coordinates and approved formula/rounding; check denominators, currency/units, adjustments, scope, and missing-input handling. |
| Material support | 100% of material assertions have specific valid support or are explicitly classified inferred/disputed/missing; zero unsupported material final conclusion | Statement-level rubric with source passages/calculation lineage, applicability and counterevidence; no generic citation-count proxy. |
| Overall supported factual accuracy | At least 98% expert-supported factual statements on the declared evaluation set | Publish numerator/denominator, severity, reviewer disagreements, and sampling limits; two questions do not establish a population rate. |
| Completeness | 100% obligation disposition; at least 95% of answerable obligations adequately addressed; all remaining material omissions visible | Separate answerable, not applicable with rationale, and missing-evidence obligations; missing evidence cannot make the denominator conveniently disappear. |
| Contradictions | All seeded material contradictions detected; known material real-case contradictions preserved or explicitly resolved | Use separate labelled test perturbations; retain both sources and reconciliation rationale. Do not silently mutate originals. |
| Pilot analyst effort | No increase in active analyst effort at no lower quality; later 20% median reduction is a provisional goal, not a claim | Time comparable collection/calculation/reconciliation/checking/rewriting; measure corrections by severity and whether the system or human supplied reasoning. Use enough comparable cases before claiming the later median. |
| Runtime latency | Two prepared-input questions ≤30 minutes; reassessment ≤10 minutes; full case ≤8 hours within an agreed corpus envelope | Record p50/p95 when sample size permits; report wall time plus human/provider waiting separately. No evidence omission to meet a ceiling. |
| Runtime cost | Proposed admission ceilings: $50 per two-question variant; $250 per full-case evaluation | Actual provider/model/tool usage, retries, failures, unknown-outcome charges, and storage/compute; ceilings are not expected spend or spending authorization. Detailed solo cash allowances belong to the master plan. |
| Security and governance | Zero tenant leakage, unauthorized calls, unreceipted accepted output, or boundary violations | Negative permissions/rights/budget/namespace tests and traceable real execution records. |

Record owner time separately from cash: product/methodology decisions, technical review, financial/commercial verification, fund coordination, and operations. Log active minutes, elapsed waiting, reason for intervention, correction type, and output changed. AI agents' implementation/review time is a separate operational measure, not paid human effort. Do not count a subscription's already-paid baseline twice or translate human person-days into AI calendar duration.

## 6. Complete analyst-journey evidence checklist

The two-question demonstration samples this journey; release must cover every selected financial/commercial obligation and the complete production path.

| Journey / component | Acceptance evidence | Existing implementation anchors to extend or verify |
|---|---|---|
| Onboard fund and access | Tenant isolation, role permissions, residency/rights/retention settings, immutable source methodology, agreed delivery/review authority | `src/idis/api/main.py`; access/compliance repositories and routes; Task9 rights records |
| Select methodology | Correct frozen fund/version and applicability; FULL launch carries methodology selection; stage scoring is deliberate | `src/idis/methodology/models.py`; `src/idis/services/extraction/task_planner.py`; `src/idis/api/routes/runs.py`; `ui/src/app/deals/[dealId]/truth-dashboard/page.tsx`; `ui/src/lib/idis.ts` |
| Upload / ingest | Read-only originals; object hashes; exact inventory; parser/OCR/media/spans; failures visible; restart-safe access | Task4 ingestion receipts and Task13 gate; `scripts/run_real_example_gate.py`; document/artifact repositories |
| Plan and extract | Every child obligation mapped; shared/merged source instructions retained; version-bound expected-answer schema; durable admitted/terminal attempts | `src/idis/models/extraction_task.py`; `src/idis/models/methodology_extraction.py`; extraction service/runtime above |
| Trust and calculate | Sanad defects/independence/contradictions, typed operands, all selected calculations conserved, Calc-Sanad and exact lineage | `src/idis/services/runs/methodology_sanad_creation_linking_grading.py`; `src/idis/calc/engine.py`; `src/idis/services/calc/runner.py`; `src/idis/services/runs/methodology_deterministic_calculation.py` |
| Research and retrieve | Verified identity, rights/freshness/applicability, durable external facts; substantive tenant-scoped graph/RAG results and consumer coverage | `src/idis/services/enrichment/task9/provider_catalog.py`; graph/RAG services; Task9/10 native records and receipts |
| Court/VEP / FULL DEBATE | Internal-only content, findings, substantive consumption, five roles, dissent, correct gates | `src/idis/services/runs/methodology_evidence_trust_court.py`; `src/idis/services/runs/methodology_validated_evidence_package.py`; `src/idis/debate/` |
| Specialist / scoring / IC | All intended specialists and role-specific prompts; fund/stage scoring; specialist reports/scorecard consumed by all three IC roles | `src/idis/analysis/`; `src/idis/analysis/scoring/`; `src/idis/services/runs/layer2_ic_challenge.py`; orchestration in `src/idis/api/routes/runs.py` |
| Review and investigate | Accessible statement-specific evidence, meaningful uncertainty, Nafs/Mujahada/insight categories, proposed bounded investigations, approvals and human contribution | `src/idis/validators/muhasabah.py`; `src/idis/api/routes/human_gates.py`; existing review/dashboard UI; new typed investigation state within existing orchestration |
| Correct and regenerate | Auditable correction, authorization, affected-dependency invalidation, bounded reassessment, preserved historical versions, consistent downstream artifacts | Human gates/overrides and existing run/deliverable services; integration is an explicit remaining task, not presumed implemented |
| Export | Response workbook traces original sheet/row/wording/shared instructions and child obligations; narrative, annex, IC memo share reviewed findings; access-controlled references resolve | `src/idis/deliverables/generator.py`; `product_bundle.py`; `financial_table.py`; `memo.py`; `export.py`; `artifact_catalog.py`; `src/idis/api/routes/deliverables.py` |
| Recover and hand over | Exact-SHA deployment/recovery record, restored evidence and permissions, budget/lease-safe resume, operator instructions, fund signoff and limitations | Release manifest/CI; `scripts/db_backup_restore.py`; run repositories; Tasks11/14/15 |

For each checklist row record evidence URI/hash, exact commit, reviewer, date, PASS/BLOCKED/NOT_EXECUTED, open defects, and next action. This package is the checklist specification; empty cells must not be filled with assumed PASS.

## 7. Deployment and recovery runbooks

### Topology and prerequisites

Retain PostgreSQL as durable transactional/audit authority, pgvector for governed vector indexes, Neo4j for graph projections/retrieval, Redis for existing rate-limit/coordination uses, and persistent document/artifact storage for immutable source objects and deliverables. Deploy authenticated UI/API plus separately controlled workers; pin dependency, prompt, model, formula, migration, and contract versions. Current Compose/Kubernetes scaffolding is not evidence of a complete qualified worker/UI production deployment.

Unavailable services, stale/missing indexes, inactive contracts, missing entitled providers, unqualified models, and draft prompts are prerequisites to resolve, not permission to simulate a successful production path. Before go-live, prove configured store connectivity, migration head, RLS/application-role behavior, index/model dimensions and freshness, backups, object hash retrieval, provider rights and budget state, and downstream consumer coverage. Never print secrets to a safe report.

Provisional owner/fund service objectives: PostgreSQL RPO ≤15 minutes and RTO ≤4 hours; source/artifact versioning and tested backups; rebuild derived graph/vector indexes within 24 hours while affected functions remain blocked or explicitly degraded; 99.5% availability in the agreed fund service window. These need a tested topology and costed feasibility decision. The owner is the operator; no 24/7 staffing or on-call coverage is assumed. Define service hours, escalation contacts, and what happens while the owner is unavailable before accepting a fund SLA.

### Authorized deployment checklist

1. Verify owner release authorization, approved exact commit, required exact-SHA CI, release manifest, schema compatibility, prompt/model qualification, rights and budget admissions, and closed material defects. Confirm private configuration is delivered through the approved secret mechanism, not Git.
2. Preserve verified backups and an immutable prior release image/manifest. Record whether rollback is code-only compatible; do not assume migration downgrade is safe.
3. Deploy into an isolated authorized environment, validate effective application role and service health, then execute the approved bounded smoke/acceptance set. A health endpoint alone does not demonstrate diligence correctness.
4. Confirm source/evidence/answer links, fund access, review/correction/regeneration, export, budgets, and alert delivery. Promote only through approved gates; record the release SHA and operating limits.

### Failure, resume, and rollback

- On an unexpected material defect, permission leak, unsupported assertion, provider-budget anomaly, or corrupted evidence binding: stop new affected work, preserve receipts/logs and immutable outputs, notify the owner, and record impact. Do not edit success states to hide failure.
- For interrupted calls, preserve `OUTCOME_UNKNOWN` and reconcile durable provider/attempt records before retry. Do not automatically reissue a potentially billed operation or refund without proof. Retry/fallback is a separately admitted attempt with its own budget and provenance.
- Resume only from native durable output roots with matching tenant/deal/run/version/hash, valid leases/fencing, complete incoming receipts and derivable expectations. Never reconstruct success from a cached summary or package ID.
- Restore only through an approved target-specific procedure. Verify counts/hashes, audit immutability, RLS/permissions, artifacts, and rehydration before reopening. Rebuild derived indexes from governed records and verify namespace/freshness/census; Redis must not become the sole evidence source.
- Roll back application code only to a compatible qualified release. For incompatible schema changes, use the reviewed forward repair or approved restoration procedure; no unreviewed destructive downgrade. Preserve a record of irreversible changes and recovery decisions.
- If budget or rights expire, block new affected calls and show pending limitations. Do not swap models/providers, weaken rights, or drop required evidence silently to continue overnight.

### Handover and continuing operation

The owner receives a compact runbook: start/stop authority, deployment SHA, approved environment/model/provider inventory, service windows, health and budget views, backup/restore instructions, escalation/incident record, private evidence-access instructions, known limitations, next action, and fund feedback status. Optional outside assistance is separately chosen and funded, never assumed.

Every authorized implementation checkpoint follows: **implement → verify → independent review → correct → commit → push → check required CI against the exact commit → update task/evidence state**. Controlled parallel agents use separate bounded branches/worktrees, reviewed ownership, and serial integration; no overlapping writers or hidden local-only accepted state. Daytime, supervised, and overnight modes share the same authorization, review, CI, privacy, and merge rules. Overnight work stops at unavailable authority, secrets/rights, material design choices, spend gates, or unverifiable outcomes; it does not auto-activate, auto-merge, or bypass a blocked check.

## 8. Release decision record

Before claiming product readiness, produce one owner-approved record linking: complete financial/commercial requirement coverage; validated numerical/analytical reference pack; exact-SHA code and CI; canonical execution/receipt and restart proofs; prompt/model/provider qualification; full analyst-journey outputs; financial/commercial review and corrections; real measured effort/time/cost; deployment/recovery evidence; rights/security checks; remaining limitations; and fund feedback/final-format confirmation.

Until those records exist, use the narrow truthful claim: documentation published, component evaluated, offline integration verified, live preflight passed, or canonical FULL demonstrated—as applicable. None is a synonym for full fund acceptance or go-live.

## 9. Completed documentation-head CI diagnosis — 52c830e

This is read-only diagnosis, not a repair or a fresh local test run. [CI run 38923212479](https://github.com/albarami/IDIS/actions/runs/38923212479) completed with **FAILURE** for exact source head `52c830ed823fff6ff1fb49f0166b8df07daf33bf`. Six jobs succeeded (`ui-check`, `evaluation-harness`, `container-build`, `k8s-validate`, `terraform-validate`, `backup-restore-drill`); `check` and `postgres-integration` failed; `release-gate` was skipped. No release/merge acceptance follows from the six successes.

### Seven typing diagnostics, two compatibility causes

Evidence: [check job 117048096703](https://github.com/albarami/IDIS/actions/runs/38923212479/job/117048096703). `make check` stopped at mypy after formatting/lint; its subsequent pytest and forbidden-scan steps did not execute. Line numbers here refer to this documentation head/base, not the newer local Slice100 tree.

| Diagnostic | Source location | Observed cause | Smallest proposed repair scope |
|---|---|---|---|
| TYP-01 | `src/idis/services/extraction/extractors/anthropic_client.py:94` | Unexpected `temperature` keyword in `Messages.create` overload | CIR-01 below |
| TYP-02 | Same call at line94 | No matching overload for the supplied argument types; same request mismatch as TYP-01, not a second independent defect | CIR-01 |
| TYP-03 | `src/idis/evaluation/synthetic_strict_runtime_rehearsal.py:924` | Deal response is `httpx2.Response`, helper requires `httpx.Response` | CIR-02 below |
| TYP-04 | Same file:957 | Upload response, same type mismatch | CIR-02 |
| TYP-05 | Same file:1087 | FULL-run status response, same type mismatch | CIR-02 |
| TYP-06 | Same file:1184 | Deliverable-list response, same type mismatch | CIR-02 |
| TYP-07 | Same file:1194 | Manifest response, same type mismatch | CIR-02 |

The job resolved `anthropic==1.15.0`, `mypy==2.4.0`, `fastapi==0.143.3`, `starlette==1.8.0`, `httpx==0.28.1`, `httpx2==2.13.1` and `typing-extensions==4.16.0`. Its logged SDK overload has no `temperature` argument; source supplies it at line97. The five response diagnostics directly establish the TestClient return-family mismatch. Broad dependency ranges in `pyproject.toml` allow these newly resolved interfaces; no resolved lock protects the older call shapes.

The response problem is also an error-sanitization compatibility issue: this module catches only `httpx.HTTPStatusError` at lines479,952,1233,1304. A new-family exception may bypass safe status-only reporting or download-failure classification. Changing only the type annotation is insufficient. Existing `tests/test_slice68_synthetic_api_upload_rehearsal.py` covers an old-family exception, not proof that both families work.

### PostgreSQL bootstrap and the artifact

Evidence: [postgres-integration job 117048096588](https://github.com/albarami/IDIS/actions/runs/38923212479/job/117048096588) and its [postgres-bootstrap-log artifact](https://github.com/albarami/IDIS/actions/runs/38923212479/artifacts/12055626950). The ZIP was read in memory, not executed; SHA-256 `87a098006fb9d4dd22399ffdeee78488f5fde02cdfaee341c53a5e290967cd14` matches GitHub's artifact digest. Its single log is 680 characters/16 split lines. Only a safe diagnostic summary is retained here; no connection values are copied.

The artifact reports `No module named 'psycopg'` after PostgreSQL readiness, role/database preparation and entry to migrations. `scripts/pg_bootstrap_ci.py:292` catches `ImportError` across the whole migration operation and labels it “Could not import alembic”; that label does not establish that Alembic itself is absent. The install log contains Alembic1.20.0, SQLAlchemy2.1.4 and psycopg2-binary2.9.13, but not psycopg3.

Source trace: bootstrap creates bare PostgreSQL URLs and exports them to the process; Alembic `src/idis/persistence/migrations/env.py` calls `get_admin_engine()`; `src/idis/persistence/db.py:_ensure_psycopg_driver()` only rewrites the `postgres://` alias to another bare URL despite its docstring claiming explicit psycopg2 selection. SQLAlchemy2.1 changed the default for bare PostgreSQL URLs to psycopg3. This explains the missing-driver import while psycopg2 is installed. The same CI's successful backup/restore job explicitly supplies `postgresql+psycopg2` URLs, a useful comparison—not proof that the failing integration suite passed. [Official SQLAlchemy2.1 migration note](https://docs.sqlalchemy.org/en/21/changelog/migration_21.html#default-postgresql-driver-changed-to-psycopg-psycopg-3).

No migration completion or PostgreSQL integration-test success was established: the bootstrap failure skipped both the application-role security/connectivity step and the integration tests.

### Base-revision attribution and limits

All offending source, dependency and CI definitions are byte-identical between base `4594ecabc4d7b598b42a67bf999b894d510f0ec9` and head `52c830ed823fff6ff1fb49f0166b8df07daf33bf`; the PR changes only Markdown. These are **base-existing compatibility conditions exposed by the current resolved dependencies**, not documentation-introduced product regressions.

| Base/head-identical file | Git blob SHA |
|---|---|
| Anthropic client | `c1ba6dec4c15548848f9d885011ecb0100d5a4a6` |
| Synthetic rehearsal | `f1e5ed22f9f14c25d7a56a6abb7dc6b407a78491` |
| `src/idis/persistence/db.py` | `38f224a789d2a61d405159ee311abf4dc167bfb0` |
| `scripts/pg_bootstrap_ci.py` | `3e7e0c9f9eb2a21795c12d62849604c0bbc93b67` |
| `pyproject.toml` | `60d08d981ce2d4484025db1f51f8647f5122b049` |
| `.github/workflows/ci.yml` | `c2765babbf4ea581292c70ab125bba36f7c2442c` |
| `Makefile` | `2cd7261ee3178b313114eeb6b81b25d2c46968b7` |

The exact base's [July14 run29370909359](https://github.com/albarami/IDIS/actions/runs/29370909359) succeeded, including its check and PostgreSQL jobs. Its old job logs now return HTTP410, so the old resolved versions cannot be recovered from those logs. Do **not** claim the historical base CI failed, or that a fresh base-only reproduction ran in this task. Under the observed October dependency set, the identical incompatible code remains present on base; that conclusion is from logs, source identity and the documented driver change, not a newly executed test.

### Proposed bounded repair packet — not authorized or performed

Use separate repair commits/PR scope, not this documentation PR or a disguised baseline-preservation commit. Reconcile the patch into the advanced Slice100 tree without replacing its newer changes; line numbers will differ there.

| ID | Bounded change | Required future evidence |
|---|---|---|
| CIR-01: SDK request compatibility | In `anthropic_client.py`, qualify the observed SDK interface and stop passing its unsupported `temperature` argument; correct the deterministic-output claim. Preserve model selection, prompt, output ceiling, retry/budget behavior. This is a request-contract change requiring review, not a cast. If an existing frozen request requires temperature, pause for a specifically audited compatible SDK constraint; do not invent a known-good version from expired logs | Offline SDK-signature/request-contract tests catch unsupported kwargs (not only unrestricted mocks); existing legacy requirements conserved or explicitly ruled on; same full typecheck passes on the recorded resolution; no live request needed |
| CIR-02: response and safe-error compatibility | In `synthetic_strict_runtime_rehearsal.py`, explicitly support both response families and both narrow status-error classes at the helper and four catch sites; declare/lock a direct `httpx2` dependency if imported. A narrow structural adapter is acceptable only if it preserves status/error semantics. No `Any`, cast, ignored error or broad swallowing | Both old/new families tested for create/upload/status/list/manifest/download failures; status-only sanitized errors retain no body/URL/credential leakage; actual current TestClient exercised offline; unchanged safety/coverage checks |
| CIR-03: explicit installed DB driver | In `src/idis/persistence/db.py:_ensure_psycopg_driver`, map bare PostgreSQL schemes to the installed `postgresql+psycopg2` dialect and preserve explicitly selected drivers. Keep raw psycopg2 URL conversion separate from SQLAlchemy engine configuration in `scripts/pg_bootstrap_ci.py:run_migrations`; correct the misleading import-error classification if touched. Do not blindly install psycopg3 or alter roles/RLS/migration semantics | URL tests for bare alias, bare PostgreSQL and explicit drivers; driver selection without connecting; bootstrap plus existing mandatory integration/security and recovery checks in an explicitly disposable authorized database; migration0039 remains excluded |
| CIR-04: reproducible resolution | Record the exact validated dependency/tool resolution used by these repairs in the existing packaging/CI mechanism; qualify compatibility rather than downgrade the checker or remove checks. Broad ecosystem modernization remains GL-05/23, not this repair packet | Same dependency set reproduced in a clean authorized environment; complete unchanged CI green at the exact repair head; failures/skips recorded and new incompatibilities separately scoped |

Acceptance requires more than seven diagnostics disappearing: the safe-error behavior, request contract and PostgreSQL bootstrap must work, required tests must actually run, and the release aggregate must pass. No product change, local mypy/pytest run, dependency installation, service operation or migration was performed for this diagnosis.
