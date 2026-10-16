# IDIS run state

State date: 2026-10-16. This is the current authority/status entry point. Reconcile it with the actual repository before acting. Historical acceptance remains scoped to its recorded bytes and conditions.

## Current state

| Field | State |
|---|---|
| Active milestone | M0: documentation package published in [PR #118](https://github.com/albarami/IDIS/pull/118); open for owner review, not merged |
| Product implementation | **Not authorized by this task; not started** |
| Next product packet | **GL-P01 — financial-schema compatibility**, after the authorization/baseline prerequisites below |
| Next eligible action now | Inspect PR #118 and its current exact-head CI; owner reviews/merges only under appropriate authority, then separately decides whether to authorize GL-P01. Do not repeat publication or begin product work merely because this package exists |
| Documentation branch | `docs/go-live-execution-package-2026-10-16` |
| Baselines | Exact main/implementation SHAs and local file hashes: [BASELINE_MANIFEST](BASELINE_MANIFEST.md) |
| Implementation checkpoint | Local committed Task7 checkpoint plus later uncommitted Task8/9-related work; no fresh product verification claimed |
| Strict execution | `full-strict-v21` INACTIVE in assessed registry; v27 successor work not activation or full acceptance |
| Pilot route | Production-relevant components plus explicitly labelled replay first; GL-04 separate live admission remains conditional |
| Documentation review | Independent PASS; [DOCUMENTATION_REVIEW](DOCUMENTATION_REVIEW.md) records checks. Reviewed publication commit `7ed25424125dd4d90db3700095c27410e0591046` has all eight remote blobs verified; later state-only publication metadata is tracked by PR head, not confused with this reviewed identity |

## Authorization ledger

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

### Proposed implementation grant — NOT approved

The owner may later authorize GL-P01, then GL-P02/03 or a bounded batch. Record the actual grant here before relying on it across sessions:

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
| GL-P01 compatibility | No new work by this task | No new evidence | No | Owner authorization; reconcile local baseline; verify exact frozen-schema ownership before change |
| GL-P02 calculation/support path | No | No | No | GL-P01 and approved formula/reference/evidence scope; may prepare reference rubric read-only under separate grant |
| GL-P03 answer/correction/export path | No | No | No | GL-P02 typed supported-answer seam; fund output assumptions explicit |
| GL-04 separate live admission | Not selected | Not evaluated as implemented | No | Compare full canonical-route work versus complete separate admission/persistence/recovery cost after M1 |
| GL-01..24 full scope | Planned, with existing foundations preserved | Historical scoped reports only | Not full-system accepted | Follow backlog dependencies and actual source, not blanket planned/done labels |

## Decisions and unresolved prerequisites

| Item | Proposed assumption / responsible decision-maker | Resolve by |
|---|---|---|
| Local-only implementation availability | Owner preserves current tree; separately reviewed publication is necessary for hosted agents and recoverable future checkpoints | Before GL-P01 remote/isolated execution |
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

Documentation checkpoint: [reviewed commit](https://github.com/albarami/IDIS/commit/7ed25424125dd4d90db3700095c27410e0591046), [PR #118](https://github.com/albarami/IDIS/pull/118), [initial CI run](https://github.com/albarami/IDIS/actions/runs/38923027329). At this state update CI was running/queued, not green. Check the current PR head and latest run before relying on status; any state-only follow-up commit is distinct from the reviewed content commit. Publication is complete; merge/CI acceptance and product authority are not implied. The PR receipt records final remote verification and observed CI status without a self-referential commit hash in this file.

For future accepted checkpoints append the template in [AGENT_EXECUTION](AGENT_EXECUTION.md): tested code SHA, review identity, complete diff range, exact-head CI, durable safe evidence, private access owner, acceptance authority, actual costs/user time, and exact next action. Keep code candidate and later evidence-only commit identities separate. A checkpoint missing required GitHub recovery or evidence is **pending**, not accepted synchronized.

## Exact next product action after authorization

Read GL-P01, reconcile the implementation HEAD plus the manifest's 174 file states, then inspect `ExpectedAnswerSchema.required_calculations` in `src/idis/models/extraction_task.py` and its construction in `src/idis/services/extraction/task_planner.py`. Establish the actual frozen-snapshot ownership and approved version-bound policy-v2 route before writing a RED test. Do not start with provider calls, migration0039, activation, broad refactoring or a new pilot framework.
