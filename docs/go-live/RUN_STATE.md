# IDIS run state

State date: 2026-10-16. This is the current authority/status entry point. Reconcile it with the actual repository before acting. Historical acceptance remains scoped to its recorded bytes and conditions.

## Current state

| Field | State |
|---|---|
| Active milestone | Authorized staged batch: PUB-IMPL-01 → CIR-01..04 → GL-P01; documentation package remains in open PR #118 |
| Product implementation | **Authorized only for this staged batch** under AUTH-IMPL-20261016 below; preservation/admission in progress |
| Next product packet | **GL-P01 — financial-schema compatibility**, after the authorization/baseline prerequisites below |
| Next eligible action now | Review full outgoing history and CI/migration admission, preserve the baseline in isolation, verify private artifact recovery, then execute dependency-ready CIR repairs and GL-P01. Keep all PRs unmerged |
| Documentation branch | `docs/go-live-execution-package-2026-10-16` |
| Baselines | Exact main/implementation SHAs and local file hashes: [BASELINE_MANIFEST](BASELINE_MANIFEST.md) |
| Implementation checkpoint | Preservation head `96293ab3fc850d41a8d86622681bbb77565c6bd7` in open [PR #119](https://github.com/albarami/IDIS/pull/119); fresh public-plus-private recovery matches all174 file states. [Recovery evidence](../evidence/2026-10-16-batch-recovery.md). No product acceptance claimed |
| Strict execution | `full-strict-v21` INACTIVE in assessed registry; v27 successor work not activation or full acceptance |
| Pilot route | Production-relevant components plus explicitly labelled replay first; GL-04 separate live admission remains conditional |
| Documentation review | Original package independent PASS at `7ed25424125dd4d90db3700095c27410e0591046`; focused correction review/remote receipt tracked in [DOCUMENTATION_REVIEW](DOCUMENTATION_REVIEW.md) and PR #118. Review identities are not interchangeable |
| Completed CI at `52c830e` | **FAILURE**: `check` (seven typing diagnostics) and `postgres-integration` (missing selected `psycopg` driver); six successes, `release-gate` skipped. [Exact run](https://github.com/albarami/IDIS/actions/runs/38923212479); diagnosis/repair proposal in [runbook section9](VERIFICATION_AND_RUNBOOKS.md#9-completed-documentation-head-ci-diagnosis--52c830e). Not green |

## Authorization ledger

### AUTH-IMPL-20261016 — active staged implementation batch

Source: the owner's explicit authorization in this conversation, using execution package `070fb57e4485e2ff6101cf7d7f26b60f38e9ecbe`. This supersedes earlier documentation-only restrictions **only within the following scope**. No routine reconfirmation is required.

| Boundary | Authorized scope / restriction |
|---|---|
| Packets and order | PUB-IMPL-01 baseline recovery/publication; CIR-01..04 compatibility repairs and reproducible verification; GL-P01 after recovery and compatibility verification. Stop this batch after GL-P01 acceptance; no GL-P02/03 |
| Baseline preservation | Preserve original 26 commits and all174 manifest states. Review complete outgoing history. Use a separate implementation PR; a sanitized publication branch is authorized if restricted history requires it, with originals preserved and explicit provenance |
| Private recovery | May create a private repository under the owner's GitHub account for the four specified fund-derived artifacts and recovery metadata. Verify private visibility before upload; preserve exact bytes; prove restoration in a fresh authorized checkout. No original workbook/case uploads or credentials |
| Repairs and checks | Install required development dependencies; bounded CIR repairs; necessary offline tests and unchanged CI checks; verify actual request, sanitized-error and PostgreSQL behavior, not merely typing |
| Disposable databases | Authorized only through the existing approved migration boundary. Migration0039 remains excluded; inspect automatic CI and every migration entry point before publication/execution |
| Git/integration | Separate preservation and repair commits; independent review and correction; commit/push exact reviewed checkpoints and record exact-revision CI. Reviewed integration of these repairs into implementation branch is delegated to the coordinating agent. Keep PRs unmerged |
| Tools/spending | Existing development tools and subscriptions only. No new paid services, IDIS live model/provider calls, deployment, activation or fund communications |
| Contracts | Frozen contracts and legacy behavior preserved. If no permitted GL-P01 route exists, report the precise ruling needed and continue other eligible work; do not invent authority |
| Private CI | Any necessary restoration restricted to trusted authorized runs; never expose artifacts or credentials to untrusted PR code |
| End condition | GL-P01 acceptance or a material boundary requiring owner decision after other eligible work is exhausted; final handoff includes functionality, recovery evidence, exact tested/published revisions, failures and next packet |

Coordinator: this Codex task. Read-only parallel roles initially cover publication privacy/history, CI/migration admission and GL-P01 frozen ownership. Original implementation worktree remains read-only; implementation writers receive isolated paths/worktrees before editing. Owner review/decision time and agent/runtime cost will be reported only where observed; no productivity multiplier or invented savings.

Batch checklist: [x] complete-history privacy review and sanitized-publication clearance; [x] isolated source preservation; [x] private visibility/upload/restoration; [x] public implementation PR and recovery receipt; [ ] CIR RED/GREEN, independent reviews and exact CI; [ ] GL-P01 ownership/golden bytes/RED/GREEN/reviews/exact CI; [ ] final recoverable handoff. Existing failures remain visible until verified repaired.

### AUTH-DOC-20261016 — effective for this documentation task

Source: explicit owner instruction to create/verify the Markdown execution package, commit/push it to `albarami/IDIS` on a dedicated documentation branch, and open a PR. This supersedes the earlier attachment's prohibition on **documentation publication only**.

| Action | Authorized scope |
|---|---|
| Read repository/source/history/configuration | Yes, relevant read-only inspection; do not expose secrets/private source contents |
| Edit | Markdown execution package under `docs/go-live/` only |
| Review/verify | Documentation consistency, links, paths/command inspection, privacy, hashes, outgoing commit/diff, remote file and CI-state inspection |
| Git | Fetch target state; isolated documentation branch/worktree; explicit-path documentation commit/push and PR creation |
| Automatic repository CI | Existing PR-triggered workflow may run as a consequence of authorized publication; no workflow change or manual product-test dispatch is authorized here |
| Merge / deployment | **No**; owner retains merge authority; no deployment or release |
| Product edits / tests | **No**; listed future checks are instructions for later authorization, not actions to run now |
| Services / databases / migrations | **No** starts, restarts, queries, data changes or migrations in this task |
| Live models / providers / spending | **No** inference/data-provider calls, new subscriptions or approved spending limit; proposed allowances are not authority |
| Confidential publication | **No** workbook, real-case files, source passages, private company/document names, credentials, environment values or private report payloads |
| Implementation history publication | **No** automatic push of local Slice100 commits or dirty changes; separate scope/review required |

Earlier Tier1 delegation for specific Task8 freeze/census/INACTIVE-publication work is preserved as historical authority with its guards, not generalized into this documentation task. Migration0039, activation, implementation commit/publication and live/priced execution retain their own holds/reviews. A documentation commit does not satisfy or waive them.

### AUTH-DOC-CORRECTION-20261016 — focused follow-up

The owner's follow-up authorizes GL-11 documentation correction, read-only CI/base/log/bootstrap-artifact diagnosis, a concrete implementation-publication proposal, and documentation-only commit/push/review on the existing PR #118. It does **not** authorize CIR product repairs, PUB-IMPL-01 execution/publication, merging or GL-P01 execution. No local product tests/installations, service/database operations or provider calls are added. The eight-file package is retained; no overall planning restart.

### Proposed implementation grant — NOT approved

The owner may authorize the combined staged scope below, then GL-P02/03 or a later bounded batch. Record the actual grant here before relying on it across sessions:

```text
Authorization ID / owner decision date / durable decision reference:
Approved packet IDs and exact implementation baseline:
Permitted code, contracts and schemas; explicit exclusions:
Offline test/review commands and artifact permissions:
Allowed private inputs and access scope (opaque references only):
Service/database/migration permissions, if any:
Live provider/model permissions and per-call/run/total spending ceilings, if any:
Branch/commit/push/PR/integration permissions:
Merge/activation/deployment permissions, if separately granted:
Reviewer requirements, stop conditions, expiration/time window:
Remaining holds and decision owner:
```

Within a valid grant, agents continue routine eligible actions without repeated permission requests. An overnight prompt, proposed dollar ceiling or backlog dependency is not a grant. No default budget exists for future live work.

## Product packet state

| Packet / scope | Implemented | Verified/reviewed | Accepted/synchronized | Blocker / next action |
|---|---|---|---|---|
| PUB-IMPL-01 preservation | Published sanitized source plus private recovery | Independent privacy review PASS; fresh restoration174/174 hash matches | Source recovery complete at `96293ab`; not product acceptance | Preserve originals; private original case inputs/environment remain separately required |
| CIR-01..04 compatibility | Isolated request/error and DB-driver repairs in progress | RED-first offline regression verification underway | No | Request/error/driver behavior and reproducible dependency resolution; unchanged checks |
| GL-P01 compatibility | Authorized, ownership inspection only | No new execution evidence | No | Baseline recovery and CIR verification; exact frozen ownership before change |
| GL-P02 calculation/support path | No | No | No | GL-P01 and approved formula/reference/evidence scope; may prepare reference rubric read-only under separate grant |
| GL-P03 answer/correction/export path | No | No | No | GL-P02 typed supported-answer seam; fund output assumptions explicit |
| GL-04 separate live admission | Not selected | Not evaluated as implemented | No | Compare full canonical-route work versus complete separate admission/persistence/recovery cost after M1 |
| GL-01..24 full scope | Planned, with existing foundations preserved | Historical scoped reports only | Not full-system accepted | Follow backlog dependencies and actual source, not blanket planned/done labels |

## Decisions and unresolved prerequisites

| Item | Proposed assumption / responsible decision-maker | Resolve by |
|---|---|---|
| Local-only implementation availability | Owner preserves current tree; separately reviewed publication is necessary for hosted agents and recoverable future checkpoints | Before GL-P01 remote/isolated execution |
| Four private fund-derived policy/map artifacts | Use owner-approved private immutable recovery, not public Git; frozen bytes must remain exact. See PUB-IMPL-01 in BASELINE_MANIFEST | Before implementation publication/recovery acceptance |
| Dependency compatibility CI failures | Base-existing source conditions under October resolution; July base CI passed. CIR-01..04 are proposed repairs, not performed; no weakening checks | Before any repaired implementation checkpoint claims exact-head CI acceptance |
| Exact expected-answer frozen ownership | Do not assume the requirement-model precedent settles it; inspect the actual 68-schema set and route disjointly if admitted | GL-P01 design gate |
| Policy-v2 source ambiguities | No coercion or invented financial rule; owner obtains fund clarification where material | Before affected GL-07/08 acceptance |
| Reference answers and independence | Use existing case evidence, but create/review reference calculations and judgment rubrics; no gold set claimed | Before substantive pilot evaluation |
| Final fund format | Four-part package proposed; actual template if supplied must be versioned/mapped | Before M4 fund acceptance |
| Required case providers / rights / freshness | Verified identity and methodology determine requirements; no blanket requirement to call all18 per question | Before live research admission |
| Models, prices, prompts, residency | Candidates only; account access/retention/residency must qualify | GL-05/09 and before live evaluation |
| Prompt approval roles | HIGH-risk needs gates1–4 plus genuine OWNER and SECURITY/COMPLIANCE approvals; MEDIUM gates1–2 plus OWNER. Owner identifies legitimate coverage; no silent role collapse or model placeholder | Before prompt promotion/strict admission |
| Services / database/index state | Prior inspection is historical; configured stores cannot be assumed healthy or populated | Authorized preflight, not this task |
| 0039 / activation / canonical gates | Existing separate authority and checks unchanged | Before any corresponding operation |
| Solo-owner availability | Reserve 6–10 active hours/week provisionally; measure actual decisions/review/operations separately | M1 planning and recalibration |
| Budget / operations targets | Cash allowances and fund-dependent quality/service targets provisional; owner/fund approves actual limits | Before spend/deployment or SLA commitment |
| Separate pilot admission | Default not built; replay/common production seams first | M1 route decision |

No unresolved item authorizes weakening a frozen contract, substituting a model review for financial verification, fabricating evidence or presenting partial outputs as complete diligence.

## Checkpoint and evidence register

This publication captures the plan, not new product acceptance. Historical implementation evidence locations and current local hashes are recorded in the baseline manifest; current documentation evidence is in DOCUMENTATION_REVIEW. Do not use May22 outputs as gold answers or infer historical behavior from newer code.

Documentation checkpoint: [reviewed original commit](https://github.com/albarami/IDIS/commit/7ed25424125dd4d90db3700095c27410e0591046) followed by state commit `52c830ed823fff6ff1fb49f0166b8df07daf33bf`, [PR #118](https://github.com/albarami/IDIS/pull/118). The latter's [CI run38923212479](https://github.com/albarami/IDIS/actions/runs/38923212479) is completed **FAILURE**, superseding the previous queued/running handoff. All seven typing diagnostics and the bootstrap failure are attributed in runbook section9; identical base source is established, not a fresh base test. The exact base's July CI passed; its old logs expired. Publication is not CI acceptance. This correction's exact final head, remote eight-file verification and current CI state belong in the PR receipt, avoiding a self-referential commit/hash update loop.

For future accepted checkpoints append the template in [AGENT_EXECUTION](AGENT_EXECUTION.md): tested code SHA, review identity, complete diff range, exact-head CI, durable safe evidence, private access owner, acceptance authority, actual costs/user time, and exact next action. Keep code candidate and later evidence-only commit identities separate. A checkpoint missing required GitHub recovery or evidence is **pending**, not accepted synchronized.

## Authorized batch decomposition — AUTH-IMPL-20261016

| Order | Concrete scope | Required boundary / completion evidence |
|---|---|---|
| 1. PUB-IMPL-01 / GL-01 | Preserve/review the26 local commits and174 overlay states; publish only privacy-cleared code/plans/contracts on a separate implementation PR, with private recovery for four fund-derived artifacts | Owner selects private destination; complete history/diff/privacy review, raw-byte identity and another-machine restoration proof. No repairs hidden in preservation commits; failed CI remains WIP |
| 2. CIR-01..04 | Repair SDK request, HTTP response/error-family and PostgreSQL driver compatibility; record reproducible dependency resolution in existing mechanisms | Explicit code/test/install and disposable-CI-database permissions as needed; RED/GREEN/review and complete exact-head CI. No migration0039, activation, provider calls or check weakening. Apply to both release/main and advanced implementation as reviewed, without overwriting local work |
| 3. GL-P01 | Establish frozen expected-answer ownership and implement the smallest approved version-bound financial-schema compatibility route | Reconciled/recoverable source; required private artifacts; explicit implementation/test authority; independent review and conservation evidence. No GL-P02/03 execution implicitly added |

Preservation/privacy review and repair design may proceed in parallel under the active grant; integrate preservation before new repairs into the advanced source. Reviewed repair integration is authorized, while all PRs remain unmerged. Each stop point and the separate migration0039, activation, live/priced and fund-acceptance holds remain in force. The earlier proposal table is now bounded by the exact active grant above, not additional implied authority.

## Exact GL-P01 action after authorization and baseline reconciliation

Read GL-P01, reconcile the implementation HEAD plus the manifest's 174 file states, then inspect `ExpectedAnswerSchema.required_calculations` in `src/idis/models/extraction_task.py` and its construction in `src/idis/services/extraction/task_planner.py`. Establish the actual frozen-snapshot ownership and approved version-bound policy-v2 route before writing a RED test. Do not start with provider calls, migration0039, activation, broad refactoring or a new pilot framework.
