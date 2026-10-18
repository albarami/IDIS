# Agent execution and GitHub checkpoint protocol

This is the common working protocol for Cursor, Devin, Codex, Claude Code, other agents, and cooperating CLIs. It does not depend on a vendor, model family, or CLI flag. Read [START_HERE.md](START_HERE.md) first; [RUN_STATE.md](RUN_STATE.md) owns current authorization and status, [IMPLEMENTATION_BACKLOG.md](IMPLEMENTATION_BACKLOG.md) owns packet scope and dependencies, and [VERIFICATION_AND_RUNBOOKS.md](VERIFICATION_AND_RUNBOOKS.md) owns acceptance procedures. Do not infer permission from a proposed task, budget, prompt, or schedule.

**Current owner-directed roles (AUTH-CLI-HANDOFF-20261017): Claude CLI Fable orchestrates/plans; Gemini CLI is primary implementer using the actual available Gemini 4 model; a separate actual Claude CLI Opus5.5 session independently reviews.** Cursor/Grok is optional only for explicitly assigned focused scope. Codex Astra/Sol are reserve: do not invoke automatically. Fable coordinates review/correction/re-review directly; Salim is not a relay. This supersedes prior Astra/Sol, Cursor and Devin assignments, including stale role text in START_HERE and historical records, without changing technical authority or acceptance gates. Verify actual available model identities and supported invocation; never silently substitute or simulate independent review. The owner ordered transfer before CIR-04 is green, not acceptance or advancement.

**Source boundary (AUTH-FROZEN-PREVIEWS-20261017): every producer, including ordinary legacy/SNAPSHOT previews, requires a genuinely frozen evidence package.** Upload-first UNBOUND storage is allowed but is not consumable preview/answer evidence. Reuse current production freeze/admission mechanisms; make missing prerequisites clear and preserve meaningful upload-to-preview behavior. Do not introduce an unfrozen-preview exception, silently return successful empty answers, fabricate frozen parents/receipts, or weaken security/positive tests. Actual Opus5.5 review, meaningful verification and reviewed push remain required; this ruling does not activate any contract or relax another hold.

**CIR-04 test-profile boundary (AUTH-CI-HEALTH-20261017):** only an explicitly selected, isolated CI test profile may use the labelled fictional79-row workbook, unsigned deny-only declarations, zero-call cap and authority-free disposable public key. Preserve canonical original-workbook/signature/authorization requirements and all validators; no general bypass. Prove zero provider calls/spend and blocked external access with executable controls, seven real checks, literal13routes and unchanged default command. Report success only as **"container health with synthetic fixtures"**, never real-workbook/financial/canonical Task4/FULL/production readiness. RUN_STATE records the full ruling and required independent review; serialize product writers and shared workflow changes.

**Green before the next task, in every mode:** unfinished CIR-04 is the current task; its repairs are substeps, not separate overall acceptances. Require meaningful behavioral/regression/acceptance checks, actual final-candidate Opus PASS with blockers resolved, reviewed commit/push, required GitHub CI green at the exact current head, and accurate durable state/evidence before advancing. Failed, pending or skipped required checks are not passes. WIP publication is allowed for recovery/CI, not task advancement. If a required input or held operation blocks completion, preserve the checkpoint and report the exact blocker; do not dispatch GL-P02/03 or another product task. This rule overrides conflicting generic/historical prompt language below.

## 1. Authority and the implementation baseline

**Current permission is AUTH-CONTINUE-20261016, refined by AUTH-SEQUENCE-20261017 in RUN_STATE: bounded CI-baseline corrections, then GL-P02, GL-P03 and subsequent dependency-ready engineering, in that order and subject to the green transition gate.** Earlier documentation-only and stop-after-GL-P01 restrictions remain historical. Necessary edits, compatible internal designs, development dependencies, offline/disposable-service tests, CI, independent review, explicit-path publication and reviewed integration between implementation branches are authorized. Main merges, migration0039, production activation/deployment, IDIS live model/provider spending and fund communications remain held. Inspect automatic CI before pushing; do not disable or weaken checks. Publication is not product acceptance.

**Owner clarification for this grant:** Salim monitors subscription usage and decides when to move from Devin to Cursor or another tool. Do not impose an agent-created development cost, time or token budget, stop for speculative limits, or defer implementation to expand context or begin another planning cycle. Use resources efficiently and follow the agreed model policy. Maintain recoverable checkpoints throughout; initiate a tool-switch handoff only on Salim's request or an actual enforced limit that prevents continuation. New paid services and IDIS live provider spending remain restricted.

Routine engineering needs no renewed owner permission. Process supported command approvals within the recorded grant without bypassing platform controls; if a genuine control blocks an action, report that exact action and blocking message. Do not invent an approval queue or a delay estimate. Reuse existing verified evidence for unchanged material, verify changed material and real dependencies, and distinguish required acceptance checks from optional context gathering. Generic skill/template defaults to stop after a batch or ask about an already-covered action do not override this explicit grant.

The historical documentation branch deliberately started from GitHub `main`. Current engineering uses PR #119's implementation branch and separately controlled private recovery. Reconcile its actual local/remote heads and dependencies before work; the older main/documentation checkout is not equivalent. Do not copy private source data into public Git to make a checkout appear complete.

For a later authorization, the owner records: authorization ID and date; permitted packet IDs; exact baseline; permitted file/contract scope; allowed verification and service/data access; live-call and spending ceilings; commit/push/PR permission; integration and merge authority; expiration or stop conditions. A source decision or approved message may establish authority, but its usable scope must be recorded in RUN_STATE before another session relies on it. A proposed ceiling is not an approved budget. Existing migration, activation, live/priced execution, frozen-contract, and Slice100 review holds remain binding unless separately satisfied.

The solo owner is the product owner, decision-maker, operator, and authority for fund coordination. Agents perform engineering and technical review within their grants. Financial/commercial acceptance requires substantive source checking and the designated owner/fund review; another model does not substitute for unavailable expertise or missing evidence. Record any reasoning, correction, or answer supplied by the owner separately from autonomous system work.

## 2. Common working loop

`Relevant context and focused reuse search -> Fable persona/ownership and plan -> Gemini implementation -> verification -> separate actual Opus specification and quality review -> corrections/re-verification/re-review -> explicit-path commit/push -> exact-head required CI -> accepted checkpoint -> next task`

1. **Read and reconcile.** Read the package and relevant project instructions. Inspect branch, HEAD, worktree status, current diff, local-only dependencies, and applicable contracts before trusting a status label. Before claiming something is absent, search relevant preserved worktrees, folders and archives and distinguish missing-from-checkout, unintegrated and unverified existing work. Resolve identity or authority conflicts before writes. Do not apply instructions for an unrelated project merely because an editor rule is present. Historical reports are claims about their recorded revisions, not proof of the current tree.
2. **Choose one bounded current task/substep.** CIR-04 remains current until the green transition gate passes; its substeps must not be relabelled as completion. Before each task select Fable/Gemini/reviewer personas with required expertise, objective, owned scope, invariants and acceptance evidence. Record actual model identities, documents/sections read and skills actually applied. Read START_HERE/RUN_STATE/AGENT_EXECUTION and the checkpoint initially, then only task requirements, controlling contracts, affected callers/consumers and verification instructions. Use the existing inventory/capability map and focused searches of relevant worktrees/branches/helpers/archives before coding. Classify existing usable, unintegrated, unverified/defective and genuinely missing capabilities within the searched scope. Inspect implementations and callers, not keyword hits alone; reuse/connect/repair rather than duplicate.
3. **Reserve ownership.** Record writer, branch/worktree, baseline, allowed paths, and coordination owner in RUN_STATE or the packet handoff. Preserve unrelated changes. Never use blanket staging, destructive reset/clean, force-push, or automatic stash/pop to make an unsafe checkout appear clean.
4. **Implement within scope.** Follow packet-specific RED-first requirements, frozen-schema ownership checks, and existing production interfaces. Do not conceal a type/contract defect with a cast, fabricate a persistence parent, weaken a test, or silently activate a contract. A new issue outside the packet becomes a linked task or decision, not unreviewed scope growth.
5. **Verify the actual candidate.** Run only authorized checks. Record commands, exit statuses, test counts/skips, environment/tool versions, contract/model/prompt versions where relevant, and evidence hashes. A command listed in a plan is not proof it ran. Separate offline replay, component evaluation, and canonical FULL execution. The two-question result cannot be labelled a comprehensive investment score or recommendation.
6. **Review the actual diff and evidence.** Give a fresh reviewer the baseline, candidate identity, diff, evidence, invariants, and unresolved issues. The reviewer inspects source and evidence directly, not only the implementer's summary. Separate architectural/contract compliance, correctness, security/privacy, and completeness. If only self-review is available, label it self-review; do not satisfy a separately-required review gate with it.
7. **Correct and recheck.** Each material finding receives a disposition, correction, or owner ruling. Re-run affected checks, obtain review of the new candidate, and invalidate superseded evidence. Unresolved material failures prevent acceptance. A reviewer may reject a claimed failure with evidence; disagreement is not permission to ignore it.
8. **Publish only the approved scope.** Inspect both the complete outgoing commit range and final file diff against the intended base. Stage explicit paths. Confirm no unrelated implementation commits, private data, secret values, or machine-only reports are included. Commit and push only when the packet's grant allows publication. Open/update the PR with scope, baseline, acceptance evidence, remaining holds, and exact next action.
9. **Check the exact revision.** Verify the remote branch points to the pushed SHA and required files exist at that SHA. Read current merge rules and CI results. A green run for an earlier SHA is not acceptance of a later one. Record PR head SHA, CI run/check IDs, their head SHA, and any synthetic PR merge SHA separately. New code or evidence-affecting changes require appropriate checks and review again.
10. **Record a recoverable checkpoint.** Update state/evidence without routine reconfirmation. Continue eligible substeps within the current task. Advance to the next task only after all transition gates are green. At a genuine unavailable prerequisite or held operation, preserve work and report the exact blocker/next action, not a substitute task.

## 3. Branches, integration, PRs, and merge authority

| Concern | Rule in every execution mode |
|---|---|
| Documentation publication | Use the dedicated documentation branch and documentation-only outgoing range. Its base is not the future implementation baseline. Do not merge it under the current grant. |
| Implementation branch | Create one branch per packet from the owner-approved implementation baseline, for example `go-live/gl-p01-financial-schema`. The name is illustrative; confirm it is unused. Record the exact base SHA, not just a branch name. |
| Local-only implementation | Review and establish the necessary local source as an explicitly approved baseline before relying on it. Publish only approved, sanitized source commits to make an accepted checkpoint recoverable. Until then label the dependency local-only; do not claim GitHub reproducibility. |
| Parallel work | Safe investigation and non-overlapping substeps within the current task only; at most two isolated writers plus coordination/review. No two writers in one tree and no early dispatch of the next product task. Parallelism cannot bypass the green transition gate. |
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

Maintain truthful recoverable checkpoints throughout execution and before an actual interruption or session termination. Checkpointing does not justify stopping eligible work or initiating a tool switch because of a speculative context/resource limit. If authorized commit/push fails, leave work intact and label it **local-only, unsynchronized**; do not claim an accepted checkpoint. Record the exact next recovery action. Do not stage the entire dirty tree to achieve synchronization.

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
Finish the current task before selecting the next authorized packet.
Follow the mandatory Fable/Gemini/separate-Opus roles: delegate, verify, actual review,
correct, scoped commit/push/PR where authorized, exact-revision CI, then
durable evidence and state. Preserve all unrelated work and existing holds.
If actual Opus review is unavailable, record the blocker; self-review cannot replace it.
Continue eligible work within this grant; stop only at its limits or a
material blocker. Do not infer spending, activation, merge, or deployment
permission. Report the exact next action and any unsynchronized work.
```

**Continuation**

```text
Continue IDIS under [AUTH_ID]. Re-read RUN_STATE and inspect actual Git state.
Confirm the last candidate/evidence/CI SHAs before trusting acceptance.
Finish the current packet's missing verification, correction, review, or
GitHub synchronization; select the next task only when every current required
gate is green. Do not redo accepted work or broaden scope. Use actual Opus
review and publish only within the recorded grant. Update the durable
checkpoint with the current PR/head, evidence, budget, and next action.
```

**Resume after interruption or new context**

```text
Resume IDIS from START_HERE and RUN_STATE, not from assumptions about this
chat. Reconcile branch/HEAD, dirty diff identity, authorization, last durable
checkpoint, remote SHA, CI, unfinished work, and private prerequisites.
Preserve all files. If recorded and actual states disagree, diagnose before
editing. Continue the exact unfinished action only if still authorized;
otherwise preserve the checkpoint and report the narrow blocker; do not skip ahead.
Never promote a local-only or unverified result to accepted.
```

### Mode B — two cooperating CLIs

Fable's Claude CLI session coordinates and delegates implementation to Gemini with sole writer ownership of reserved paths. A separate actual Claude Opus5.5 CLI session is reviewer, read-only by default. Thus this mode uses two cooperating engineering roles plus an independent reviewer session; its historical heading is not a two-process restriction. Use a separate review worktree or pinned commit/diff while the implementer pauses edits; never review a moving target without recording its exact identity. The coordinator handles messages/corrections directly. The owner remains merge authority unless expressly delegated. Do not collapse planner, implementer and independent reviewer into one role or launch Codex as fallback.

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

Before starting, record the actual owner grant, authorized tools/checks/data, permitted branch/PR actions and exception handling. Record only limits the owner actually set; do not invent a development budget or run window. Unattended execution changes scheduling, not authority or roles. Default: no merge, activation, deployment, new subscriptions, budget increases or destructive operations. Work task-by-task with the green transition gate; at most two non-overlapping substep writers within the current task when ownership/tool support permits. Actual Opus review remains mandatory. A sleeping owner is not approval of an exception.

**Startup**

```text
Run the IDIS overnight batch [BATCH_ID] under the authorization in RUN_STATE
[AUTH_ID], obeying any actually specified stop time or defined stop condition.
Read START_HERE, backlog and runbooks, then inspect actual checkout and
baseline. Work through dependency-ready authorized packets sequentially.
Use bounded non-overlapping Gemini delegation within the current task, at most two writers,
with one integration coordinator. For each packet: implement, verify, fresh
actual Opus review, correct, scoped commit/push/PR if authorized, inspect
required CI at the exact revision, and record durable state/evidence.
Do not wait for routine permissions already granted. If the current task is blocked,
record why and preserve it; no next task until all required gates are green.
No merge or new spending/activation/service/deployment authority is implied.
Stop on exhausted limits or material authority/safety failures. Before ending,
leave every partial result recoverable where authorized and an exact next action.
```

**Continuation**

```text
Continue overnight batch [BATCH_ID] under unchanged [AUTH_ID]. Reconcile
state, remaining time/cash limits, writer ownership and actual Git/CI status.
Complete missing correction, review or synchronization before claiming a
checkpoint accepted. Continue the next authorized task only after every required gate is green; do not
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
report the exact blocker without skipping ahead. Preserve all local-only work and report its
unsynchronized status if publication is blocked. Default remains no merge.
```

## 7. Tool-specific launch notes, not commands to execute

The common prompts above are authoritative; launch mechanics are conveniences. No launcher is run by this document. In Cursor or Codex, select/open the approved implementation worktree and start a new agent/session with the matching startup prompt. In Claude Code or another CLI, launch the installed tool from that same worktree using its locally documented invocation, then paste the prompt. Do not assume flags or install/upgrade tools to match an example. For two CLIs, use separate sessions and the writer/reviewer ownership described above. For Devin or any hosted agent, first confirm approved repository access and approved availability of local-only source; **do not upload private fund data or credentials** to compensate for missing local access. If those prerequisites are absent, hosted implementation is blocked, not equivalent to the local environment.

Historical Devin/Cursor/Codex assignments remain historical. **Current selection is Claude CLI Fable planner/coordinator, Cursor/Grok 4.7 implementer (after the Gemini/Antigravity quota exhaustion recorded below; `agy` with `gemini-4-argon-high` was the verified route until 2026-10-17 11:25 UTC), and separate Claude CLI Opus5.5 reviewer; Codex stays in reserve.** **Verified implementer route (2026-10-17):** the legacy `gemini` CLI is absent and unauthenticated on this machine and knows no Gemini 4 identifier; the owner directed use of the Antigravity subscription. Installed `agy` 1.4.0 at `%LOCALAPPDATA%\agy\bin\agy.exe` (SHA256 f5466e6aaf9f1345cae476fecb9aebb9e82c2ca8dfd43136fa9747fcf075e062) from the official checksum-verified installer; it authenticated silently from the Google session the Antigravity app had stored in Windows Credential Manager (no API key, no billing change). `agy models` lists `gemini-4-argon-{low,medium,high}`; headless probes with `--model gemini-4-argon-high` returned SUCCESS (conversations 5f57afd1…, ff54983b…). Working invocation: `Get-Content <brief> -Raw | agy --model gemini-4-argon-high --effort high --mode plan|accept-edits --add-dir <main checkout> --output-format json --print-timeout <n>m` from the owned worktree (the brief goes on stdin; stdin is ignored when `-p` is also passed). Headless mode soft-denies shell commands without an allow rule; Windows needs `command(regex:…)` targets in `~/.gemini/antigravity-cli/settings.json`, where a scoped allow/deny file (git read-only subcommands, python/pytest/ruff/mypy, the qualified venv interpreter; git write/network, docker, pip, removal and shell-spawn commands denied; its hash is recorded in RUN_STATE) was qualified by probe. The implementer never commits or pushes; Fable performs reviewed publication and exact-head CI verification. RUN_STATE records each run's conversation id, brief hash and output hash. Codex Astra/Sol are reserve only; Cursor/Grok needs an explicit focused assignment. Installed Claude Code2.1.301 at `C:/Users/baram/AppData/Roaming/npm/claude.ps1` and the actual `claude-opus-5-5` review route are verified. Claude uses the existing subscription; no separately billed API key is required or authorized here. Fable's exact supported CLI model identifier has NOT been verified in this handoff (historical sentence: it was verified later on 2026-10-17 as `claude-fable-5-1`, see RUN_STATE's AUTH-CLI-HANDOFF record). `Get-Command gemini` found no command and `gemini --version` failed in the current PowerShell PATH; that does not establish absence in another shell/location. The Gemini launcher, login/entitlement and actual Gemini4 identifier must be confirmed through existing installations/subscriptions before assigning a writer (historical: confirmed on 2026-10-17; see the verified-route paragraph). Do not invent `--model` IDs, install tooling, change billing or select another model silently. An unavailable role is a narrow launch prerequisite, not grounds for another broad audit or a Codex fallback.

**Owner routing instruction AUTH-ROUTING-20261017 (2026-10-17 ≈19:03 UTC; supersedes every conflicting historical routing sentence in this document and START_HERE):** use only Grok and Anthropic models — Claude Fable (existing session) as orchestrator/planner, the Cursor Agent CLI with the verified `grok-4.7-xhigh` as implementer, and a separate actual Claude CLI Opus 5.5 session as independent reviewer. Codex/OpenAI, Gemini/Antigravity, Devin, Composer and Auto routing are not invoked; no silent model substitution, paid overage or billing change; existing authenticated subscriptions only. The verbatim instruction and the checkpoint reconciliation are in RUN_STATE ("AUTH-ROUTING-20261017"). Historical paragraphs below that name Gemini as implementer or Codex as reserve are superseded. **Writer reservations (2026-10-17 20:40 UTC):** C3 writer — batch 5c, exactly the dev-0039 migration under `scripts/dev_only/`, its driver `scripts/run_cir04_restart_reference_dev_migration.py` and the two new test modules; J writer — J-04, `.github/workflows/ci.yml` (postgres-integration job only), `scripts/pg_bootstrap_ci.py`, `tests/test_pg_bootstrap_ci_required_preview.py`, plus `tests/test_ci_private_inputs.py` for J4-7..J4-9 (the removal review's IR-R1/R2/R3 hardening) and the J4-10 archive fence; K writer idle; Fable assembles by explicit paths, verifies on fresh disposable databases, obtains the separate Opus reviews and publishes. Per-batch ownership and dispatch records stay in RUN_STATE.

**Verified implementer route change (2026-10-17, 11:25 UTC onward):** the Antigravity/Gemini route exhausted its subscription quota (HTTP 429, reset ≈2026-10-24) during batch 2, and the owner directed the Cursor/Grok route for the remaining writer scopes with Codex kept in reserve (never invoked automatically). Verified route: Cursor Agent CLI 2026.10.16-e3cb207 (`%APPDATA%/Cursor/User/globalStorage/anysphere.cursor-agent-worker/agent-cli/.local/share/cursor-agent/versions/2026.10.16-e3cb207/cursor-agent.cmd`), owner browser login, model `grok-4.7-xhigh` (headless init reports "Grok 4.7 256K Extra High"). Working invocation: `cursor-agent -p "<short pointer to a brief file>" --model grok-4.7-xhigh --output-format json --force --trust --workspace <worktree> --add-dir <read-only dir>`, launched with a per-process `CURSOR_CONFIG_DIR=<short path>` holding that worker's `cli-config.json` (`approvalMode: allowlist`, scoped Shell allow/deny); the shared `~/.cursor/cli-config.json` keeps the owner's original permissions. Controls actually in force: Shell deny rules are enforced under `--force` (git push/commit/add/stash/checkout, docker, pip, psql, gh, network tools denied; verified); `Write(...)` deny patterns are NOT enforced under `--force`, so path ownership is a detection and acceptance control — every returned candidate is compared file by file against its reserved paths and frozen manifest before verification. Briefs are route-agnostic files; multi-line `-p` arguments are fragile in PowerShell 5.1. Fable owns reviewed GitHub publication and exact-head CI verification for every route. **Coordinator byte corrections are exceptional:** when one is unavoidable for publication (first instance CC-01, 2026-10-17 16:18 UTC, forbidden-scan compliance in two C3 test files), it is disclosed in RUN_STATE with before/after hashes, carried in the publication manifest beside the reviewed hashes, mirrored into the owning worker's worktree, and covered by the next independent review before publication.

Existing actual read-only review method (separate session; placeholders must be real local paths, output must not already exist):

```powershell
Get-Content -Raw <review-prompt-path> | claude -p --model claude-opus-5-5 --effort high --permission-mode plan --permission-prompts none --tools Read,Glob,Grep,Skill --allowedTools Read,Glob,Grep,Skill --strict-mcp-config --no-chrome --output-format json | Tee-Object <new-review-json-path>
```

For a correction review, the already used `--resume <recorded-session-UUID>` retains the original review lineage; final combined-candidate review still needs the required independent scope. The actual response must identify the resolved model/first-party route and inspected candidate. CLI telemetry is not an incremental-cash invoice. For Fable/Gemini, inspect existing local help/model availability first and record the working command plus resolved identity; this document does not assert an untested invocation. Do not print credentials, authentication state files or secret-bearing process arguments.

Before each task, record concise role briefs in its existing execution record: expertise/objective, owned files/interfaces, invariants/acceptance evidence, scope and responsibilities. Fable owns coordination/integration records; the current implementer (Cursor/Grok 4.7 since 2026-10-17 11:25 UTC; Gemini historically) owns reserved implementation paths. Use separate worktrees for concurrent writers and serialize shared interfaces. The separate Opus reviewer remains read-only against a pinned candidate and inspects source, actual diff, governing requirements and direct evidence. Gemini's self-review or Fable's planning review is not independent Opus review. All outgoing Codex writer reservations are released at the handoff; incoming roles must explicitly reserve paths before editing.

Discover, read and apply relevant available skills for each role. Record the skills actually used and document sections read; if none apply, record that honestly. Personas and skills grant no human approval authority. The proposed IDIS runtime model matrix remains a separate qualification decision from development models.

## 8. Stop, recover, or continue elsewhere

Stop the affected action for missing/expired authority, an exhausted owner-approved spending ceiling on separately authorized priced operations, an actual enforced platform/account limit, an unauthorized destructive operation, credential/private-data exposure risk, conflicting writer ownership, an unapproved schema/contract change, unexpected live/provider access or an unavailable required input. An unresolved material acceptance failure blocks acceptance and next-task advancement and calls for correction within the grant. Do not invent a development budget or use speculative resource exhaustion as a stop condition. Preserve evidence, report the exact blocker and continue only eligible work within the current task. Security incidents follow the runbook; do not publish suspected secrets.

A missing service, inactive strict contract, draft prompt, unresolved fund instruction, or inaccessible expert evidence is a real prerequisite when the packet needs it. Do not simulate success, create placeholder bytes, fabricate native database parents, use legacy nullability as admission, silently reduce coverage, or claim a replay is canonical production. The separate pilot-admission route remains conditional; production-relevant work plus explicitly labelled replay is the default until its decision gate is resolved.

For a routine check failure within the current task, diagnose and correct within its scope and actual owner-approved limits. If correction requires unavailable input or authority outside the grant, record the precise blocker and preserve the checkpoint; do not move to another product task, repeatedly retry unchanged blockers or request already-covered routine permission. Never increase spending limits, weaken thresholds, hide skipped checks or count owner-supplied reasoning as autonomous capability. Do not relabel required checks as optional or change acceptance criteria merely to obtain green.

The final handoff for any session states: what changed; what was actually verified and reviewed; exact local and remote revisions; what is accepted versus pending; cost and owner time; unresolved risks; local-only dependencies; and the **single exact next action**. Documentation completion never implies implementation, Slice100, fund acceptance, or go-live completion.

## 9. Fable continuation after the owner-directed handoff

Open the exact implementation folder and branch in RUN_STATE; reconcile HEAD with the final publication receipt before edits. Keep tested product code separate from the later documentation-only head. Read the current checkpoint and only the selected packet's controlling sections, interfaces, contracts and verification instructions. Reuse the recorded94-checkout discovery and verified unchanged evidence; search affected material when it changes or before a new absence claim.

Verify the actual Fable and Gemini4 identities through existing supported CLI access; do not treat their requested role names as verified model IDs. Keep the separate working Claude CLI Opus5.5 route. Discover/read/apply relevant available skills for each role and record actual use, not names alone; accurately state when none apply. Earlier execution used Superpowers4.3.1 source files and a historical Claude review reported7.0.0; do not assume cached versions are current. Local Codex skill sources used in the outgoing work are under `C:/Users/baram/.codex/skills/`: systematic-debugging, test-driven-development, executing-plans, using-git-worktrees, receiving-code-review, requesting-code-review and verification-before-completion. Read applicable current instructions before use. Generic skills do not override the owner grant, green transition gate or role change; do not spawn reserve Codex agents because an old template says to delegate to Sol.

Continue only CIR-04: record Fable/Gemini/Opus briefs and ownership, focused reuse/plan, Gemini implementation, meaningful checks, independent Opus review, correction/re-review, explicit-path publication and exact-head required CI. Only green accepted checkpoints permit the next task. Do not ask routine permission again, invent accounting assumptions or human approvals, use a model answer as a numerical oracle or weaken genuine failures into green checks. Transfer of control while checks are red is explicitly authorized; it is not completion. The restart amendment is preparation/review/publication only until the separate owner ruling described in RUN_STATE.

The next concrete action is in RUN_STATE's checkpoint. No previous chat, broad re-audit or platform rewrite is required. Main merges, 0039 outside the development-only scope of AUTH-CIR04-DEV0039-20261017 (owner decisions 2 and 3 of amendment v6), production activation/deployment, IDIS live spending, new paid services and fund communications remain held. Salim continues to control usage and tool-switch decisions.

**Continuation state at checkpoint 08 (2026-10-18 07:43 UTC, Fable).** The integration checkout `C:/Projects/IDIS/IDIS-implementation-batch-20261016` on `go-live/implementation-batch-2026-10-16` carries checkpoint 08 (C6 Stage A) on top of f99595ef; RUN_STATE's current-state row is the source of truth for the published head, the required-failure register (R1–R11) and the owner-open items (b)–(l). Reviewed work NOT yet published lives in fresh worktrees cut from f99595ef: H1b (container-health composition, `IDIS-cir04h1b-20261018`, awaiting Linux non-root evidence and a delta re-review), H3-1/H3-2 (Linux governed Poppler binding, `IDIS-cir04hc-20261018`, H3-2b in progress; integration blocked on owner decision (j)), X1 (CRLF-pinned literals, `IDIS-cir04x1-20261018`), X2 (bare PostgreSQL URL normalisation, `IDIS-cir04x2-20261018`). The H2a native-asset root is `C:/Projects/IDIS/.local_reports/cir04h2a-20261018/` (never published). Each worker runs as a Cursor/Grok 4.7 agent with its own Cursor configuration directory (at most four concurrently); every batch is RED-first, verified by the coordinator, reviewed by a separate Claude CLI Opus 5.5 session, and published only by explicit path through `publish_checkpoint.sh`. The P5 target is always the waiting `check` job of the newest published head; superseded runs are never approved.

**Continuation state at checkpoint 09 (2026-10-18 09:05 UTC, Fable).** Checkpoint 09 adds the H1b container-health composition (synthetic fixtures only; expected RED at the governed-Poppler step until H3), the X2 psycopg2 driver normalisation at four product sites, and the X1-b `.gitattributes` lines that make the hash-pinned files check out as the bytes their frozen pins name (after checking out this revision on Windows, re-materialise `tests/__init__.py`, the two methodology test modules and the two runtime-health manifests with `git checkout` if they were already present). Still unpublished: H3-1/H3-2/H3-2b in `IDIS-cir04hc-20261018` (delta re-review in progress; integration and push wait for owner decision (j) and a rebase onto this checkpoint); CIR-04X3 (subprocess-transport handle closure, OCR and media) is in investigation in `IDIS-cir04x3-20261018`. The main Windows checkout's working tree holds stale CRLF copies of about 2,400 LF blobs from an earlier `core.autocrlf` state; run hash-pinned tests only in fresh worktrees or the pinned Linux image until it is re-materialised from the index. The check job's `make check` pytest classes R8–R11 are recorded in RUN_STATE with their corrections; after P5, the remaining hosted reds there are the Linux Poppler class (H3), the uncovered private artifacts and the workbook path (owner item (k)), and the listed singles.

**Continuation state at checkpoint 10 (2026-10-18 09:57 UTC, Fable).** Checkpoint 10 carries only H1b-1g: the container-build composition's three H1B_PY snippets now register `prepare_bootstrap_health.py` in `sys.modules` before executing it (the hosted step-7 failure of run 39114108162), with tests that execute the embedded snippet text itself. The hosted expectation returns to the reviewed one: RED first at step 21 (governed Poppler) until CIR-04H3. Still unpublished: H3 (H3-2c pending in `IDIS-cir04hc-20261018`, rebased onto a26218cf; integration waits for owner decision (j)); X3 (subprocess-transport handle closure, OCR and media; investigation in `IDIS-cir04x3-20261018`). After each publication the superseded run's waiting environment review needs the owner's rejection grant before the newest `check` job can reach the gate.

**Continuation state at checkpoint 11 (2026-10-18 11:00 UTC, Fable).** Checkpoint 11 carries only H1b-1h: the container-build model-receipt write uses `subprocess.run` (the hosted step-11 `check_call(input=)` failure of run 39117332145), with a static keyword check over every embedded H1B_PY snippet and execution tests of the step-11 body (including a Linux end-to-end run with the real pinned tiny.en tree, recorded NOT_EXECUTED elsewhere without skipping). The hosted expectation is the reviewed one: steps 7–20 pass, RED first at step 21 (governed Poppler) until CIR-04H3. Still unpublished: H3 (reviewed PASS_FOR_INTEGRATION_AFTER_DECISION_J; waits for owner decision (j), the X3 serialisation and H3-3/H3-4); X3 (X3-2 reviewed, X3-3 in implementation: the Linux reap-steal in `_terminate_process_tree`). Item (b) is closed; item (c) (key storage) is actionable against the newest reviewed revision's run; after each publication the superseded run's waiting review is rejected under the owner's standing authority.

**Continuation state at checkpoint 12 (2026-10-18 13:09 UTC, Fable).** Checkpoint 12 carries H1b-1i/1j (the container-build step-20 uid-1000 qualify hand-off runs the root parent's real interpreter with `-S`, PYTHONPATH limited to a root-published, uid-1000-owned run-root import closure and a run-root helper copy; the hand-off tests record `os.chown` on every platform and pin the 1000:1000 ownership targets) and X3-3 (`_terminate_process_tree` in ocr.py and media.py signals and waits through psutil only on grandchildren, terminates the root via `process.terminate()` and leaves root reaping to `process.join()`; a 22-test reap module). P5 state: the recovery key is stored (environment secret, one write 11:47:19Z, with the reviewed guard byte; deploy key 166838245 read-only); attempt 2 of run 39121404113 completed the private fetch and the whole restoration path for the first time on hosted CI, then `make check` failed closed (exit 2, no pytest footer in the captured tail) — P5 is PARTIAL (mechanism qualified; the make-check result element open, being identified by a hosted-like local reproduction, with a reviewed privacy-safe diagnostics change as the owner-decided fallback). Every approved `check` run is now a key-using run: applicability confirmation for the revision, tuple + one approval, key-pattern sink scan, signed-in rendered read. Still unpublished: H3 (reviewed PASS_FOR_INTEGRATION_AFTER_DECISION_J; waits for owner decision (j), the rebase onto this X3 head with its ocr.py delta re-reviewed, and H3-3/H3-4). CIR-04 remains open: check, postgres-integration (R2/R3), container-build (step 21 until H3) and release-gate are red; all holds unchanged.

**Continuation state at checkpoint 13 (2026-10-18 15:17 UTC, Fable).** Checkpoint 13 carries H1b-1l only: container-build step 23 "Record admission codes when the API is blocked" (one `docker run --rm` of the built image in the API's exact mounts and network namespace, printing allowlisted component/code pairs and boolean/revision Postgres facts; stderr in the private maintenance directory; DIAGNOSTIC_UNAVAILABLE with exit 0 on failure; the `check` job byte-identical; `strict_launcher.py` untouched) and the deterministic H1b-1k composition test. What the hosted reads established on checkpoint 12: step 20 passes (the uid-1000 hand-off works); the API exits at the strict launcher's dependency admission (STRICT_FULL_LIVE_BLOCKED) before create_app — step 23 now names the blocking component(s); the key-using `check` run fetched and restored the private inputs again, then `make check` ended with exit 2 and no pytest footer for the second time (hosted-only; the local pinned-image and hosted-like runs reach their footer); P5 remains PARTIAL (mechanism qualified; make-check result element open). Reserved for the owner: changing `strict_launcher.py` to carry blocker codes; the Slice56 source-freeze contract decision; the capture-bootstrap diagnostics change if the diagnostic-image reproduction does not identify the hosted no-footer end. Still unpublished: H3 (waits for owner decision (j), the rebase onto X3's published head and H3-3/H3-4). CIR-04 remains open: check, postgres-integration (R2/R3), container-build (step 21) and release-gate are red; all holds unchanged.

**Continuation state at checkpoint 14 (2026-10-18 17:07 UTC, Fable).** Checkpoint 14 carries H1b-1n only: `SchemaValidator` resolves the packaged `idis/_schemas` first (when it holds the audit schema), then the checkout `schemas/`, failing closed otherwise, with an explicit `schema_dir` authoritative and no environment or working-directory lookup; the ten `schemas/*.schema.json` files are force-included into the wheel; the H1b-1k composition test is deterministic and the insert test opt-in. What checkpoint 13's admission line established: inside the built image only the audit readiness check failed (AUDIT_EMIT_FAILED) while Postgres was at head 0038 with RLS forced — H1b-1m demonstrated the cause as the image's non-editable install failing to locate the audit schema (every `validate_audit_event` caller in the image was affected: the sink, the launch audit, the middleware). Expected now: admission passes, the launch audit writes its row, and the first red moves to the governed-Poppler binding (CIR-04H3). P5: PARTIAL — the private mechanism qualified twice with the stored key; the hosted `make check` ended with exit 2 and no pytest footer on 206db8e6 and 671bc39b; three local reproductions (pinned image; hosted-like with loopback Postgres/Redis, overlays and sockets; the OCR diagnostic image) all reach a footer; the complete-input reproduction (all nine private artifacts, the derived v27 staging, the historical commit, ruff and mypy first) decides whether the owner-reserved capture-bootstrap diagnostics change is required. Approvals of further key-using `check` runs are deferred by the J reviewer until that path yields something worth testing on hosted; each publication rejects the superseded waiting review as housekeeping. Reserved for the owner: the strict-launcher blocker-codes change, the Slice56 source-freeze contract decision, the capture-bootstrap diagnostics change if needed, decision (j) (H3 identity and the Task4 closure re-pin set), item (k) (uncovered private artifacts). Still unpublished: H3. CIR-04 remains open: check, postgres-integration (R2/R3), container-build (step 21) and release-gate are red; all holds unchanged.

**Continuation state at checkpoint 15 (2026-10-18 18:59 UTC, Fable).** Checkpoint 15 carries H1b-1o and C1-b. H1b-1o (container-build job only; the `check` job byte-identical): CI step 23's closed allowlist gains the four strict_auth reason codes, both revision-id filters accept only ASCII `[A-Za-z0-9_]{1,64}`, the step has `timeout-minutes: 10` and a bounded named probe container that Cleanup removes, the missing admission/filter/inner-script tests exist, the outside-tmp inventory distinguishes an absent path from an empty directory, and the wheel byte-identity test decides hatchling inapplicability before building; the summary-test harness runs step scripts from a file, the health-root inventory test uses a tmp-rooted root, and the wheel meta-tests control their required-mode variable. C1-b (test-only): the Slice75b and RBAC retry/resume seeds carry an explicit full-strict-v21 RESUMABLE identity only at the 19 previously failing nodes, six legacy-409 pins exist, and the worker-half resume assertions are recorded `SLICE75B_WORKER_HALF NOT_EXECUTED` as an open item for the Task 7 completion packet. What checkpoint 14's hosted run established: inside the built image the strict launcher's dependency admission passes, the launch audit writes its row and the API's first red is the governed-Poppler binding (R3, closes with CIR-04H3 under owner decision (j); the H reviewer's condition: the governed prefix must also reach the `check` runner or the affected tests need a reviewed inapplicability gate). The C1 classification: the v16–v20 harness-hash rows in the local reproductions were a coordinator export artefact (LF blobs where `.gitattributes` materialises CRLF; no re-pin); LEGACY_RUN_RESUME_UNSAFE was a superseded-contract test class (corrected here); the orchestrator-preflight rows are the R3 class. P5: PARTIAL — the private mechanism qualified twice; the hosted `make check` ended at 65:41 and 67:22 with exit 2 and no footer, while the complete-input local reproduction with the CRLF materialisation reaches its footer in 68.5 minutes; the J reviewer ordered one last no-mechanism-change step, `make check` through the bootstrap's own `capture_script`/`capture_environment` (running), after which either a found cause is routed or the owner-reserved privacy-safe diagnostics change (Makefile line and make error code, pytest's last progress percentage, six abnormal-end booleans; P1–P4 re-qualification) is required before any further key-using run; approvals of key-using `check` runs stay deferred; each publication rejects the superseded waiting review. Reserved for the owner: decision (j) (H3 identity and the governed prefix in the `check` job), item (h) (R2), the R5 runner re-pin (option B), the Slice56 source-freeze contract decision, item (k) (the operator workbook path and runtime-derived private inputs), the capture-bootstrap diagnostics change if the capture-semantics reproduction ends normally. Still unpublished: H3. CIR-04 remains open: check, postgres-integration (R2/R3), container-build (R3 at step 21) and release-gate are red; all holds unchanged.

**Continuation state at checkpoint 16 (2026-10-18 20:40 UTC, Fable).** Checkpoint 16 carries the P5 parser correction only: `numeric_counts` in the CI private-inputs bootstrap reads the counts from the last pytest footer-shaped line of the captured log's 64 KiB tail whatever its `=` padding (the J rule), closed labels and numbers only, SUMMARY_MISSING semantics unchanged; `_REVIEWED_BOOTSTRAP_SHA256` and the check job's `IDIS_BOOTSTRAP_SHA256` env line are re-pinned to the new bootstrap bytes; container-build is byte-identical. What was established: the hosted check summary `{counts: {}, exit_code: 2}` of both key-using runs was this parser rejecting every full-suite footer (pytest pads a summary longer than the 80-column non-TTY width with a single `=` on each side); `make check` ran to completion on hosted twice, so the private mechanism — fetch, restoration, overlay, derivation, historical fetch, make check, cleanup — is complete end to end; the abnormal-end hypotheses are withdrawn. P5 acceptance now needs one key-using run on this revision showing real counts consistent with its exit code, a clean key-pattern sink scan (also checking the wheel-build test produced no artifact or upload), the deferred rendered reads (attempt 2's k8s panel, run 39130501664, the new run) and the anomaly rule; approval of that run proceeds under the recorded procedure with the J verdict's hash in the tuple; each publication rejects the superseded waiting review. The `check` job's make check is still expected red: governed Poppler (R3, 49 tests plus the preflight rows, until H3 under decision (j) with the governed prefix reaching the check runner), item (k) private inputs (the operator workbook path and runtime-derived receipts), the Slice56 source-freeze contract conflict, and the singles routed for correction. Reserved for the owner: decision (j), item (h) (R2), the R5 runner re-pin (option B), the Slice56 contract decision, item (k). Still unpublished: H3. CIR-04 remains open: check, postgres-integration (R2/R3), container-build (R3 at step 21) and release-gate are red; all holds unchanged.

**Continuation state at checkpoint 17 (2026-10-19 23:46 UTC, Fable).** Checkpoint 17 carries the C2 test-only corrections: the Task 5 run-selection fixture, the Task 7 atomic-admission and completion test doubles, the launcher grant test's identity stub, and the operator-workbook inapplicability gate (the owner's confidential workbook is never a CI input; absent → `OPERATOR_WORKBOOK NOT_EXECUTED` recorded and the node passes; `IDIS_REQUIRE_OPERATOR_WORKBOOK=1` fails; present → unchanged identity checks). What the key-using run on checkpoint 16 established: the private mechanism is qualified end to end on hosted (three successful fetches with the stored key; every private stage and cleanup exit 0; `make check` completed with real counts consistent with its exit code; three clean sink scans; no wheel artifact); P5 acceptance needs only the four deferred signed-in rendered reads and the final receipt bundle; no further key-using run is needed for P5 — later key-using runs only when a reviewed correction changes the qualified mechanism, when a publication can change the `check` outcome (batched), and for CIR-04 acceptance on the final head, each under the recorded procedure. The `check` job's make check remains red on the owner-reserved classes: governed Poppler R3 (decision (j), H3 with the governed prefix reaching the check runner), item (k) private inputs (the oracle rows and runtime-derived receipts), the Slice56 source-freeze contract conflict, the R5 runner pin (option B); the C2 corrections remove the routed singles and turn the workbook-path rows into recorded NOT_EXECUTED passes. Reserved for the owner: decision (j), item (h) (R2), the R5 re-pin, the Slice56 decision, item (k). Still unpublished: H3. CIR-04 remains open: check, postgres-integration (R2/R3), container-build (R3 at step 21) and release-gate are red; all holds unchanged.
