# IDIS run state

State date: 2026-10-16. This is the current authority/status entry point. Reconcile it with the actual repository before acting. Historical acceptance remains scoped to its recorded bytes and conditions.

## Current state

| Field | State |
|---|---|
| Active milestone | Stop at GL-P01's bounded compatibility result. PUB recovery and named repairs are published in open PR #119; whole-system CI/release acceptance remains held |
| Product implementation | AUTH-IMPL-20261016 executed only within this batch. GL-P01 component checks/reviews pass; this is not Slice100, canonical FULL or financial-answer acceptance |
| Next product packet | **GL-P02 — governed calculation and statement-support path**, only after a new grant; not started. A separately bounded CI-baseline follow-up is also required |
| Next eligible action now | Finish exact-head CI/recovery handoff, then stop. Owner decides the separate CI/admission follow-up and any GL-P02 grant. Keep PRs unmerged; do not repair unrelated preserved failures silently |
| Documentation branch | `docs/go-live-execution-package-2026-10-16` |
| Baselines | Exact main/implementation SHAs and local file hashes: [BASELINE_MANIFEST](BASELINE_MANIFEST.md) |
| Implementation checkpoint | Reviewed/tested/published code `b729cf828e83d95b932d541b1d523df53fcaccf8` in open [PR #119](https://github.com/albarami/IDIS/pull/119). [Recovery](../evidence/2026-10-16-batch-recovery.md), [CIR / GL-P01 evidence](../evidence/2026-10-16-cir-glp01.md). Later handoff-only commit and its exact CI are recorded in the PR receipt; they do not change the tested implementation |
| Exact code CI | [Run38935124037](https://github.com/albarami/IDIS/actions/runs/38935124037) at `b729cf8`: completed **FAILURE**; four jobs pass, four fail, release gate skipped. Focused GL-P01 success does not change this result |
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

Batch checklist: [x] complete-history privacy review and sanitized-publication clearance; [x] isolated source preservation; [x] private visibility/upload/restoration; [x] public implementation PR and recovery receipt; [x] CIR RED/GREEN and independent scoped reviews; [x] GL-P01 ownership/golden bytes/RED/GREEN/reviews; [x] exact code revision published and local integrated checks; [ ] whole-system CI acceptance. The final evidence-only head and completed exact-head CI are appended in the PR receipt to avoid a self-referential commit loop. Existing failures remain visible; focused success does not waive them.

### AUTH-DOC-20261016 — historical documentation-only grant

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

### AUTH-DOC-CORRECTION-20261016 — historical focused follow-up

The owner's follow-up authorizes GL-11 documentation correction, read-only CI/base/log/bootstrap-artifact diagnosis, a concrete implementation-publication proposal, and documentation-only commit/push/review on the existing PR #118. It does **not** authorize CIR product repairs, PUB-IMPL-01 execution/publication, merging or GL-P01 execution. No local product tests/installations, service/database operations or provider calls are added. The eight-file package is retained; no overall planning restart.

### Future authorization template — subsequent batches are NOT approved

AUTH-IMPL-20261016 above is the actual grant for this completed bounded implementation work; it is not this blank template. The owner may use the following fields to authorize GL-P02/03 or another subsequent batch. Record that new grant before relying on it across sessions:

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
| PUB-IMPL-01 preservation | Published sanitized source plus private recovery; separate CDD raw-byte correction | Independent privacy review PASS; fresh174/174 overlay match and corrected tracked-template hash; original26 commits/174 states unchanged | Source recovery complete; not live-environment recovery | Original workbook/cases, credentials/profiles, stores and private historical logs remain separately required |
| CIR-01..04 compatibility | Request/error/driver repairs, audited SDK constraint, reproducible dependency resolution, migration guard and behavior-neutral typing supplement | RED-first tests and separate spec/quality reviews PASS; real PostgreSQL bootstrap and five-test recovery drill PASS; integrated full mypy PASS | Scoped implementation verified/published; **CIR-04 full CI acceptance still open** | Preserved lint,0038 CI-role setup, legacy database fixtures, strict-profile admission and other recorded baseline failures remain. No check weakened |
| GL-P01 compatibility | Disjoint version-bound financial expected-answer/task/planning models and consumer decoding | Independent spec/quality PASS; legacy3 schemas/serialization and all68 frozen snapshots conserved;249 integrated Windows passes plus separately passed DB skip;128 Linux passes | Bounded compatibility acceptance at tested code `b729cf8`; global CI/release acceptance **not** claimed | No formula execution or live extraction; legacy calculator explicitly rejects policy-v2. GL-P02 requires new authority |
| GL-P02 calculation/support path | No | No | No | GL-P01 and approved formula/reference/evidence scope; may prepare reference rubric read-only under separate grant |
| GL-P03 answer/correction/export path | No | No | No | GL-P02 typed supported-answer seam; fund output assumptions explicit |
| GL-04 separate live admission | Not selected | Not evaluated as implemented | No | Compare full canonical-route work versus complete separate admission/persistence/recovery cost after M1 |
| GL-01..24 full scope | Planned, with existing foundations preserved | Historical scoped reports only | Not full-system accepted | Follow backlog dependencies and actual source, not blanket planned/done labels |

## Decisions and unresolved prerequisites

| Item | Proposed assumption / responsible decision-maker | Resolve by |
|---|---|---|
| Implementation availability | Resolved for cleared source through PR #119; original26-commit history retained locally, not falsely claimed published. Original private environment remains separate | Recheck exact public/private commit and raw hashes on recovery |
| Four private fund-derived policy/map artifacts | Resolved through verified-private `albarami/IDIS-private-recovery`, commit `775aa7d0d88a1705c7c37805a0b2194eec436407`; fresh restoration verified. No public CI restoration configured | Preserve private ACL and exact hashes; no untrusted-run access |
| Dependency compatibility / full CI | Named compatibility defects repaired and current checker passes; broader preserved baseline failures remain isolated in the receipt. Owner approves any additional correction scope; no weakening checks | Before claiming full CIR-04, release or canonical acceptance |
| Exact expected-answer frozen ownership | Resolved by full68-row recursive ownership inspection: answer/task/planning models are outside; frozen request/candidate-span models unchanged. Disjoint exact-version route implemented | Preserve conservation at every subsequent schema change |
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

The original documentation publication captured the plan, not product acceptance. Current bounded GL-P01 compatibility evidence is now in the [CIR / GL-P01 receipt](../evidence/2026-10-16-cir-glp01.md); it is not full-system acceptance. Historical implementation evidence locations and original local hashes remain in the baseline manifest, and documentation evidence remains in DOCUMENTATION_REVIEW. Do not use May22 outputs as gold answers or infer historical behavior from newer code.

Documentation checkpoint: [reviewed original commit](https://github.com/albarami/IDIS/commit/7ed25424125dd4d90db3700095c27410e0591046) followed by state commit `52c830ed823fff6ff1fb49f0166b8df07daf33bf`, [PR #118](https://github.com/albarami/IDIS/pull/118). The latter's [CI run38923212479](https://github.com/albarami/IDIS/actions/runs/38923212479) is completed **FAILURE**, superseding the previous queued/running handoff. All seven typing diagnostics and the bootstrap failure are attributed in runbook section9; identical base source is established, not a fresh base test. The exact base's July CI passed; its old logs expired. Publication is not CI acceptance. This correction's exact final head, remote eight-file verification and current CI state belong in the PR receipt, avoiding a self-referential commit/hash update loop.

For future accepted checkpoints append the template in [AGENT_EXECUTION](AGENT_EXECUTION.md): tested code SHA, review identity, complete diff range, exact-head CI, durable safe evidence, private access owner, acceptance authority, actual costs/user time, and exact next action. Keep code candidate and later evidence-only commit identities separate. A checkpoint missing required GitHub recovery or evidence is **pending**, not accepted synchronized.

## Authorized batch decomposition — AUTH-IMPL-20261016

| Order | Concrete scope | Required boundary / completion evidence |
|---|---|---|
| 1. PUB-IMPL-01 / GL-01 | Preserve/review the26 local commits and174 overlay states; publish only privacy-cleared code/plans/contracts on a separate implementation PR, with private recovery for four fund-derived artifacts | Owner selects private destination; complete history/diff/privacy review, raw-byte identity and another-machine restoration proof. No repairs hidden in preservation commits; failed CI remains WIP |
| 2. CIR-01..04 | Repair SDK request, HTTP response/error-family and PostgreSQL driver compatibility; record reproducible dependency resolution in existing mechanisms | Explicit code/test/install and disposable-CI-database permissions as needed; RED/GREEN/review and complete exact-head CI. No migration0039, activation, provider calls or check weakening. Apply to both release/main and advanced implementation as reviewed, without overwriting local work |
| 3. GL-P01 | Establish frozen expected-answer ownership and implement the smallest approved version-bound financial-schema compatibility route | Reconciled/recoverable source; required private artifacts; explicit implementation/test authority; independent review and conservation evidence. No GL-P02/03 execution implicitly added |

Preservation/privacy review and repair design may proceed in parallel under the active grant; integrate preservation before new repairs into the advanced source. Reviewed repair integration is authorized, while all PRs remain unmerged. Each stop point and the separate migration0039, activation, live/priced and fund-acceptance holds remain in force. The earlier proposal table is now bounded by the exact active grant above, not additional implied authority.

## Batch stop and exact next actions

GL-P01's compatibility objective is delivered at `b729cf828e83d95b932d541b1d523df53fcaccf8`: the four policy fields reach real extractor-request preparation without legacy coercion; full typecheck passes; frozen and legacy conservation plus independent reviews pass. No meaningful financial answer, formula result or canonical FULL execution is claimed. The final handoff preserves the evidence references and exact CI, with no automatic continuation into GL-P02/03.

1. **Owner: authorize a bounded CI-baseline follow-up if desired.** Its proposed scope is exact0038 disposable-CI role pre-provisioning (not role/RLS weakening), reconciliation of old database fixtures with preserved schema requirements, governed disposition of signed-census lint/negative-test scan findings, the cold-import cycle, and a truthful non-activating container acceptance strategy. The absent v27 candidate artifact remains an evidence/admission dependency; do not generate it merely to make a test pass. These are identified follow-ups, not approved edits in this handoff.
2. **Owner: separately authorize GL-P02 when its reference/evidence prerequisites are ready.** Reuse this branch's governed task shape and existing calculation/Calc-Sanad path; implement only the selected EBITDA calculation and statement-support seam. Approved policy rules, an independent reference calculation and scoped inputs are prerequisites. No autonomous model/provider execution is implied.
3. **Recovery operator:** use the exact published implementation and pinned private recovery, persist `core.autocrlf=false` in new checkouts, and verify hashes. Do not assume the original workbook/cases, operator profiles, databases or local private reports came from GitHub. Both public PRs remain unmerged; migration0039, activation, deployment, new paid services and fund communications remain prohibited.
