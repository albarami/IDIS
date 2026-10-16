# Agent execution and GitHub checkpoint protocol

This is the common working protocol for Cursor, Devin, Codex, Claude Code, other agents, and cooperating CLIs. It does not depend on a vendor, model family, or CLI flag. Read [START_HERE.md](START_HERE.md) first; [RUN_STATE.md](RUN_STATE.md) owns current authorization and status, [IMPLEMENTATION_BACKLOG.md](IMPLEMENTATION_BACKLOG.md) owns packet scope and dependencies, and [VERIFICATION_AND_RUNBOOKS.md](VERIFICATION_AND_RUNBOOKS.md) owns acceptance procedures. Do not infer permission from a proposed task, budget, prompt, or schedule.

## 1. Authority and the implementation baseline

**Current permission is AUTH-CONTINUE-20261016 in RUN_STATE: bounded CI-baseline corrections, GL-P02, GL-P03 and subsequent dependency-ready engineering.** Earlier documentation-only and stop-after-GL-P01 restrictions remain historical. Necessary edits, compatible internal designs, development dependencies, offline/disposable-service tests, CI, independent review, explicit-path publication and reviewed integration between implementation branches are authorized. Main merges, migration0039, production activation/deployment, IDIS live model/provider spending and fund communications remain held. Inspect automatic CI before pushing; do not disable or weaken checks. Publication is not product acceptance.

The historical documentation branch deliberately started from GitHub `main`. Current engineering uses PR #119's implementation branch and separately controlled private recovery. Reconcile its actual local/remote heads and dependencies before work; the older main/documentation checkout is not equivalent. Do not copy private source data into public Git to make a checkout appear complete.

For a later authorization, the owner records: authorization ID and date; permitted packet IDs; exact baseline; permitted file/contract scope; allowed verification and service/data access; live-call and spending ceilings; commit/push/PR permission; integration and merge authority; expiration or stop conditions. A source decision or approved message may establish authority, but its usable scope must be recorded in RUN_STATE before another session relies on it. A proposed ceiling is not an approved budget. Existing migration, activation, live/priced execution, frozen-contract, and Slice100 review holds remain binding unless separately satisfied.

The solo owner is the product owner, decision-maker, operator, and authority for fund coordination. Agents perform engineering and technical review within their grants. Financial/commercial acceptance requires substantive source checking and the designated owner/fund review; another model does not substitute for unavailable expertise or missing evidence. Record any reasoning, correction, or answer supplied by the owner separately from autonomous system work.

## 2. Common working loop

`Read state -> inspect actual checkout -> select authorized dependency-ready packet -> implement -> verify -> review -> correct -> commit -> push -> check required CI against the exact revision -> update evidence and state -> continue`

1. **Read and reconcile.** Read the package and relevant project instructions. Inspect branch, HEAD, worktree status, current diff, local-only dependencies, and applicable contracts before trusting a status label. Resolve identity or authority conflicts before writes. Do not apply instructions for an unrelated project merely because an editor rule is present. Historical reports are claims about their recorded revisions, not proof of the current tree.
2. **Choose one bounded packet.** Select the next dependency-ready packet under RUN_STATE's active grant. State its user-visible result, permitted files, verification, evidence requirements and remaining holds. Current order is bounded CI-baseline corrections, then GL-P02 and GL-P03; GL-P01 retains scoped acceptance. Read the packet, controlling source sections, affected contracts/interfaces and verification instructions; do not reload every historical document.
3. **Reserve ownership.** Record writer, branch/worktree, baseline, allowed paths, and coordination owner in RUN_STATE or the packet handoff. Preserve unrelated changes. Never use blanket staging, destructive reset/clean, force-push, or automatic stash/pop to make an unsafe checkout appear clean.
4. **Implement within scope.** Follow packet-specific RED-first requirements, frozen-schema ownership checks, and existing production interfaces. Do not conceal a type/contract defect with a cast, fabricate a persistence parent, weaken a test, or silently activate a contract. A new issue outside the packet becomes a linked task or decision, not unreviewed scope growth.
5. **Verify the actual candidate.** Run only authorized checks. Record commands, exit statuses, test counts/skips, environment/tool versions, contract/model/prompt versions where relevant, and evidence hashes. A command listed in a plan is not proof it ran. Separate offline replay, component evaluation, and canonical FULL execution. The two-question result cannot be labelled a comprehensive investment score or recommendation.
6. **Review the actual diff and evidence.** Give a fresh reviewer the baseline, candidate identity, diff, evidence, invariants, and unresolved issues. The reviewer inspects source and evidence directly, not only the implementer's summary. Separate architectural/contract compliance, correctness, security/privacy, and completeness. If only self-review is available, label it self-review; do not satisfy a separately-required review gate with it.
7. **Correct and recheck.** Each material finding receives a disposition, correction, or owner ruling. Re-run affected checks, obtain review of the new candidate, and invalidate superseded evidence. Unresolved material failures prevent acceptance. A reviewer may reject a claimed failure with evidence; disagreement is not permission to ignore it.
8. **Publish only the approved scope.** Inspect both the complete outgoing commit range and final file diff against the intended base. Stage explicit paths. Confirm no unrelated implementation commits, private data, secret values, or machine-only reports are included. Commit and push only when the packet's grant allows publication. Open/update the PR with scope, baseline, acceptance evidence, remaining holds, and exact next action.
9. **Check the exact revision.** Verify the remote branch points to the pushed SHA and required files exist at that SHA. Read current merge rules and CI results. A green run for an earlier SHA is not acceptance of a later one. Record PR head SHA, CI run/check IDs, their head SHA, and any synthetic PR merge SHA separately. New code or evidence-affecting changes require appropriate checks and review again.
10. **Record a recoverable checkpoint.** Update the packet state and evidence index, then continue eligible work without re-requesting already-covered routine permission. At a blocker, select another non-dependent authorized packet if safe; otherwise stop with an exact next action.

## 3. Branches, integration, PRs, and merge authority

| Concern | Rule in every execution mode |
|---|---|
| Documentation publication | Use the dedicated documentation branch and documentation-only outgoing range. Its base is not the future implementation baseline. Do not merge it under the current grant. |
| Implementation branch | Create one branch per packet from the owner-approved implementation baseline, for example `go-live/gl-p01-financial-schema`. The name is illustrative; confirm it is unused. Record the exact base SHA, not just a branch name. |
| Local-only implementation | Review and establish the necessary local source as an explicitly approved baseline before relying on it. Publish only approved, sanitized source commits to make an accepted checkpoint recoverable. Until then label the dependency local-only; do not claim GitHub reproducibility. |
| Parallel work | Initially at most two non-overlapping implementation packets plus coordination/review. Separate worktrees and explicit path ownership; no two writers in the same tree. Parallelism must not bypass dependencies. |
| Shared changes | Serialize schema, migration, contract registry, orchestrator, public API, prompt registry, and shared adapter changes. A coordinator integrates one reviewed candidate at a time. Re-review/re-verify the combined candidate. |
| Dependent packets | Prefer starting from an accepted synchronized baseline. A stacked PR requires explicit dependency/base records and an authorized integration plan; do not silently import an unaccepted predecessor. |
| Integration | The owner is integration authority by default. A recorded bounded grant can delegate integration of named packets to one agent; integration is not merge, release, activation, or deployment authority. |
| Merge | Owner-operated by default. An agent may merge only under an explicit future grant naming allowed PRs/conditions. Overnight permission alone does not grant merge. Never bypass protection, checks, review, or unresolved contract holds. |
| Remote rules | Re-read rules before proposing a merge. On 2026-10-16 the publication inspection found no branch-protection configuration for `main` and an empty ruleset list; absence of platform enforcement is not permission or a waiver of this protocol. |
| CI | `.github/workflows/ci.yml` currently runs on PRs to `main` and pushes to `main`. Its jobs include `ui-check`, `check`, `postgres-integration`, `evaluation-harness`, `container-build`, `k8s-validate`, `terraform-validate`, `backup-restore-drill`, and aggregate `release-gate`. Treat applicable CI and separately required packet gates as acceptance requirements even if GitHub does not enforce them. Reinspect the workflow at the candidate revision. |
| Target movement | Record current target SHA and combined merge candidate. If the target changes, re-evaluate integration evidence; a green old synthetic merge is not proof for a new target. Record merge SHA if a later authorized merge occurs. |
| Failure or abandonment | Preserve branch/worktree and evidence. Do not delete user work, rewrite published history, or revert unrelated changes. A recovery/revert is a separately reviewed scoped change, with special handling for non-reversible data changes. |

GitHub acceptance is not merely a successful push. A checkpoint must have recoverable source, durable evidence references, a status record, and a next action. An open PR can preserve an accepted branch-level checkpoint without implying it has merged. Product release additionally requires the release gates, owner/fund acceptance, and deployment authorization.

## 4. State vocabulary and durable evidence

| State | Meaning |
|---|---|
| Planned / blocked | Not implemented; dependency, input, authority, or budget is missing. |
| In progress | Authorized edits exist; no completion claim. |
| Implemented | Bounded change exists; verification and acceptance may be outstanding. |
| Verified | Specified checks passed for an identified candidate. Missing checks, skips, and limits remain explicit. |
| Reviewed | The identified candidate received a recorded review; distinguish fresh independent AI session, self-review, owner review, and fund review. |
| Acceptance-ready | Technical criteria appear satisfied, but required publication, CI, reviewer, owner/fund decision, or durable evidence is still outstanding. |
| Accepted, synchronized | Applicable acceptance authority has approved the exact candidate; source and durable evidence references are recoverable from GitHub; required exact-revision CI is green; next action is recorded. This is scoped packet acceptance, not full-product acceptance. |

Store non-confidential evidence summaries and checkpoint records in Git. Use append-only checkpoint entries rather than rewriting historical claims. For private source evidence, record an opaque reference, content hash, retention/location class, access owner and ACL requirement, plus the public-safe validation summary. Do not publish credentials, environment-variable values, company/document names, source passages, fund workbook contents, or data-room files. A hash is an identity check, not proof of content quality or authorization.

An accepted checkpoint must not depend solely on an expiring CI artifact. Retain permitted reports durably or retain a privacy-safe signed/hashed result manifest with a recoverable controlled artifact location and retention responsibility. If required evidence cannot be recovered by an authorized reviewer, mark the checkpoint incomplete. Public reproducibility is not required for confidential inputs; truthful disclosure and controlled reviewer access are.

Use this handoff/checkpoint template; omit no required field silently:

```text
Packet ID / title / state:
Authorization ID / owner / scope / expiry:
Implementer / reviewer / review type / coordinator:
Repository / worktree / branch / approved base SHA:
Source candidate SHA / tree identity / uncommitted diff hash (if any):
Changed paths / schema, API, UI, prompt or contract changes:
User-visible result / explicit non-results:
Verification commands / exit results / counts and skips:
Environment + config fingerprint (names/versions/hashes, no secret values):
Evidence references + hashes / private-access owner / retention:
Review candidate SHA or diff identity / findings / dispositions:
PR URL / pushed SHA / remote verification:
Required checks / CI run URLs + IDs / CI head SHA / synthetic merge SHA:
Acceptance authority / decision / scope / unresolved qualifications:
Merge SHA and target SHA (if merged under explicit authority):
Cost actuals / remaining approved budget / owner active time / elapsed / waiting:
Local-only requirements / recovery steps / unfinished work:
Exact next command or action / prerequisites / reason to stop or continue:
```

**Avoid a self-referential commit identity.** Let `C` be the tested/reviewed code commit. A later evidence/state commit `E` records `C` and its CI results; it does not pretend its own unknown future SHA is `C`. Verify and push `E`, inspect its complete diff, and check CI for the current PR head as required. A later handoff or PR record can identify `E`. Keep the code acceptance claim pinned to `C`, report the current head separately, and never assume an evidence-only follow-up excuses a failed required check. If `E` changes product code or execution inputs, it is a new code candidate requiring affected verification/review.

Before context exhaustion, interruption, or session termination, write a truthful partial checkpoint. If commit/push is not authorized or fails, leave work intact and label it **local-only, unsynchronized**; do not claim an accepted checkpoint. Record the exact next recovery action. Do not stage the entire dirty tree to achieve synchronization.

## 5. Two-agent review handoff and correction loop

The implementer supplies the template above plus the full diff/commit range and acceptance checklist. The reviewer must be able to inspect the candidate repository and evidence. A summary without accessible source is not a review.

Reviewer response format:

```text
Packet / reviewed baseline and candidate / scope inspected:
Review type and independence limits:
Verdict: PASS | CHANGES REQUIRED | BLOCKED
Findings: stable ID, severity, path:line or evidence reference,
  concrete failure, acceptance impact, requested correction/check.
Checks personally performed / checks only observed / unavailable evidence:
Contract, privacy, data-boundary, and authorization assessment:
Residual uncertainty / owner or fund decision required:
```

The implementer responds to every finding with `fixed + new evidence`, `not applicable + evidence`, or `needs owner decision`. Rejected findings need a reason, not silent dismissal. The reviewer inspects corrections against the new candidate and issues a superseding verdict. Approval of old bytes is not approval of changed bytes. An agent reviewer supplies technical assurance, not a guarantee of truth or a substitute for substantive financial validation.

## 6. Ready-to-copy execution prompts

Replace bracketed values with actual packet/authorization identifiers. These prompts are instructions to obey recorded authority, **not authority themselves**. Use AUTH-CONTINUE-20261016 for its authorized engineering scope and the mandatory role assignments below. Do not request a new grant for already-covered routine work.

### Mode A — one agent or application

**Startup**

```text
Work on IDIS using docs/go-live/START_HERE.md. Read RUN_STATE, the relevant
backlog packet, verification runbooks, and applicable repository instructions.
Inspect the actual repository, branch, HEAD, dirty changes, and local-only
dependencies. Do not confuse the documentation publication branch with the
approved implementation baseline. My authorization reference is [AUTH_ID].
Select the next authorized dependency-ready packet, [PACKET_ID if specified].
Follow AGENT_EXECUTION: implement, verify, fresh review where available,
correct, scoped commit/push/PR where authorized, exact-revision CI, then
durable evidence and state. Preserve all unrelated work and existing holds.
If only self-review is available, say so and retain separate-review gates.
Continue eligible work within this grant; stop only at its limits or a
material blocker. Do not infer spending, activation, merge, or deployment
permission. Report the exact next action and any unsynchronized work.
```

**Continuation**

```text
Continue IDIS under [AUTH_ID]. Re-read RUN_STATE and inspect actual Git state.
Confirm the last candidate/evidence/CI SHAs before trusting acceptance.
Finish the current packet's missing verification, correction, review, or
GitHub synchronization; otherwise select the next dependency-ready authorized
packet. Do not redo accepted work or broaden scope. Use fresh review when
available and publish only within the recorded grant. Update the durable
checkpoint with the current PR/head, evidence, budget, and next action.
```

**Resume after interruption or new context**

```text
Resume IDIS from START_HERE and RUN_STATE, not from assumptions about this
chat. Reconcile branch/HEAD, dirty diff identity, authorization, last durable
checkpoint, remote SHA, CI, unfinished work, and private prerequisites.
Preserve all files. If recorded and actual states disagree, diagnose before
editing. Continue the exact unfinished action only if still authorized;
otherwise choose another eligible packet or report the narrow blocker.
Never promote a local-only or unverified result to accepted.
```

### Mode B — two cooperating CLIs

CLI 1 is implementer/coordinator and the sole writer for its reserved paths. CLI 2 is reviewer, read-only by default. Use a separate review worktree or a pinned commit/diff while the implementer pauses edits; never review a moving target without recording the exact diff identity. The owner remains merge authority unless expressly delegated.

**Startup, CLI 1**

```text
Implement/co-ordinate IDIS packet [PACKET_ID] under [AUTH_ID]. Read START_HERE,
RUN_STATE, backlog and runbooks; inspect the approved implementation checkout
and preserve dirty work. Reserve paths and publish a bounded plan. Implement
and verify only authorized work, then freeze the candidate identity and send
CLI 2 the AGENT_EXECUTION handoff with the full diff and evidence. Do not
self-label an independent review. Correct findings, re-verify and request
review of changed bytes. Commit/push/open the PR only within authority;
check exact-revision CI, record the checkpoint and next action. No merge
without a separate explicit grant. Wait for review where it is a gate.
```

**Startup, CLI 2**

```text
You are the read-only reviewer for IDIS packet [PACKET_ID]. Read START_HERE,
RUN_STATE, packet criteria, runbooks and the implementer's handoff. Verify
baseline/candidate identity, inspect the full diff and referenced evidence,
and assess correctness, acceptance coverage, contracts, privacy and scope.
Use the AGENT_EXECUTION reviewer response format with concrete findings.
Only run checks covered by [AUTH_ID]; do not edit, fix, commit, push, merge,
start services or call providers. Distinguish evidence inspected from claims.
Return PASS, CHANGES REQUIRED or BLOCKED with residual uncertainty.
```

**Continuation, both CLIs**

```text
Continue your recorded IDIS role under [AUTH_ID] for [PACKET_ID]. Reconcile
RUN_STATE and the latest candidate identity. Implementer: disposition every
review finding, correct within scope, re-verify, and hand off the new diff.
Reviewer: inspect corrections and affected paths, preserve unresolved findings,
and issue a superseding verdict pinned to the new identity. Do not have both
CLIs write the same files. The implementer completes authorized publication,
exact-revision CI and evidence/state synchronization after review.
```

**Resume, both CLIs**

```text
Resume the IDIS two-CLI workflow from START_HERE, RUN_STATE and the latest
handoff. Confirm your role, writer ownership, authorization, baseline,
candidate/diff identity, review findings, PR/head and CI. Do not trust a
review of different bytes. Preserve partial work and avoid duplicate writes.
Implementer resumes the exact unfinished correction or publication action;
reviewer stays read-only and resumes the outstanding evidence review.
Escalate an unavailable candidate or disputed material finding; do not bypass it.
```

### Mode C — one overnight CLI

Before starting, the owner records the bounded packet batch, authorized tools/checks and data, cost ceilings, maximum run window, permitted branch/PR actions, and exception handling. Unattended execution changes scheduling, not authority. Default: no merge, activation, deployment, new subscriptions, budget increases, or destructive operations. Work sequentially; delegate at most two non-overlapping implementation packets only when dependencies, ownership and tool support permit. A fresh review worker may run separately. A sleeping owner is not approval of an exception.

**Startup**

```text
Run the IDIS overnight batch [BATCH_ID] under the authorization in RUN_STATE
[AUTH_ID], ending at [STOP_TIME] or earlier on a defined stop condition.
Read START_HERE, backlog and runbooks, then inspect actual checkout and
baseline. Work through dependency-ready authorized packets sequentially.
Use bounded non-overlapping delegation where supported, at most two writers,
with one integration coordinator. For each packet: implement, verify, fresh
review if available, correct, scoped commit/push/PR if authorized, inspect
required CI at the exact revision, and record durable state/evidence.
Do not wait for routine permissions already granted. If one packet is blocked,
record why and continue another eligible packet without crossing dependencies.
No merge or new spending/activation/service/deployment authority is implied.
Stop on exhausted limits or material authority/safety failures. Before ending,
leave every partial result recoverable where authorized and an exact next action.
```

**Continuation**

```text
Continue overnight batch [BATCH_ID] under unchanged [AUTH_ID]. Reconcile
state, remaining time/cash limits, writer ownership and actual Git/CI status.
Complete missing correction, review or synchronization before claiming a
checkpoint accepted. Continue the next eligible authorized packet; do not
reopen settled design or repeat completed work without new evidence. Keep
blocked items visible and do not solve them by weakening gates. Leave a
checkpoint before context exhaustion and stop at the recorded end condition.
```

**Resume**

```text
Resume overnight batch [BATCH_ID] only if [AUTH_ID] remains valid. Read
START_HERE and RUN_STATE, reconcile actual source and remote revisions,
partial diffs, reviews, CI, budgets, time window and required local inputs.
An expired run window or uncertain expenditure requires renewed authority;
do not silently restart the clock or budget. Resume the exact safe action or
choose another eligible packet. Preserve all local-only work and report its
unsynchronized status if publication is blocked. Default remains no merge.
```

## 7. Tool-specific launch notes, not commands to execute

The common prompts above are authoritative; launch mechanics are conveniences. No launcher is run by this document. In Cursor or Codex, select/open the approved implementation worktree and start a new agent/session with the matching startup prompt. In Claude Code or another CLI, launch the installed tool from that same worktree using its locally documented invocation, then paste the prompt. Do not assume flags or install/upgrade tools to match an example. For two CLIs, use separate sessions and the writer/reviewer ownership described above. For Devin or any hosted agent, first confirm approved repository access and approved availability of local-only source; **do not upload private fund data or credentials** to compensate for missing local access. If those prerequisites are absent, hosted implementation is blocked, not equivalent to the local environment.

For AUTH-CONTINUE-20261016, Astra at xhigh is the orchestrator/planner, Sol 6.1 is the implementer, and an actual separate Claude CLI is the independent reviewer. Select Sonnet for routine scoped work, Opus 5.5 for architecture/financial correctness/evidence lineage/component interactions, and Fable only for justified exceptional escalation. Verify supported invocation, authentication, repository access and resolved model identities. Record actual identities and a short selection reason; an unavailable model is a blocker for that role, never permission for silent substitution. Claude subscription authentication is sufficient; this workflow does not require a separately billed API key.

Before each packet, record concise role briefs in its existing execution record: expertise/objective, owned files/interfaces, invariants/acceptance evidence, scope and responsibilities. Astra owns coordination/integration records; Sol owns its reserved implementation paths in a separate worktree when writers overlap; Claude is read-only against a pinned candidate and inspects source, diff, requirements and evidence directly. A reviewer must not rely solely on the implementer's summary.

Discover, read and apply relevant available skills for each role. Record the skills actually used and document sections read; if none apply, record that honestly. Personas and skills grant no human approval authority. The proposed IDIS runtime model matrix remains a separate qualification decision from development models.

## 8. Stop, recover, or continue elsewhere

Stop the affected work immediately for missing/expired authority, an exhausted or uncertain budget, a destructive operation, credential/private-data exposure risk, conflicting writer ownership, an unapproved schema/contract change, unexpected live/provider access, unavailable required inputs, or an unresolved material acceptance failure. Preserve evidence and report the specific owner decision or input needed. Security incidents follow the runbook; do not continue publishing suspected secrets.

A missing service, inactive strict contract, draft prompt, unresolved fund instruction, or inaccessible expert evidence is a real prerequisite when the packet needs it. Do not simulate success, create placeholder bytes, fabricate native database parents, use legacy nullability as admission, silently reduce coverage, or claim a replay is canonical production. The separate pilot-admission route remains conditional; production-relevant work plus explicitly labelled replay is the default until its decision gate is resolved.

For a routine check failure within an authorized packet, diagnose and correct within its scope and budget. If correction needs new authority, record it and move to another eligible independent packet; do not keep asking the same question or repeatedly retry an unchanged blocker. Never increase budgets, weaken thresholds, hide skipped checks, or count owner-supplied reasoning as autonomous capability.

The final handoff for any session states: what changed; what was actually verified and reviewed; exact local and remote revisions; what is accepted versus pending; cost and owner time; unresolved risks; local-only dependencies; and the **single exact next action**. Documentation completion never implies implementation, Slice100, fund acceptance, or go-live completion.
