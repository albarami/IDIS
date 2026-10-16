# IDIS execution package — start here

Status: continuous engineering under AUTH-CONTINUE-20261016 and the 2026-10-17 owner refinement in RUN_STATE. This package is not go-live acceptance. The current task is unfinished CIR-04; no next product task may start before its required acceptance and exact-head CI are green.

Current owner-directed continuation: use `C:\Projects\IDIS\IDIS-implementation-batch-20261016` with **Astra xhigh in Codex as orchestrator/planner**, delegate implementation to **Sol 6.1**, and invoke the actual **Claude CLI Opus 5.5** as independent reviewer. The coordinator manages review/correction/re-review directly; Salim is not a message relay. Read RUN_STATE's current Codex checkpoint first. The earlier Cursor/Sol-all-roles handoff is historical and superseded, not a second active writer. Do not open the older main checkout or restart a broad audit.

## Objective

Complete the existing Institutional Deal Intelligence System (VC Edition) so a fund can perform financial, market and commercial diligence using its own methodology and templates, reducing measured analyst work without weakening analytical quality. Retain the existing trust, multiagent, calculation, market-intelligence and persistence architecture. Do not create a parallel framework.

## Read in this order

1. [RUN_STATE.md](RUN_STATE.md): current permissions, decisions, blockers and exact next action.
2. [BASELINE_MANIFEST.md](BASELINE_MANIFEST.md): GitHub versus local implementation identity and private prerequisites.
3. [GO_LIVE_MASTER_PLAN.md](GO_LIVE_MASTER_PLAN.md): authoritative scope, architecture, requirements, modernization choices and estimates.
4. [IMPLEMENTATION_BACKLOG.md](IMPLEMENTATION_BACKLOG.md): executable packets and dependencies; bounded CI-baseline corrections precede **GL-P02**, then GL-P03. GL-P01 retains scoped compatibility acceptance.
5. [VERIFICATION_AND_RUNBOOKS.md](VERIFICATION_AND_RUNBOOKS.md): acceptance evidence, release and operations.
6. [AGENT_EXECUTION.md](AGENT_EXECUTION.md): choose Mode A (one agent/application), B (two cooperating CLIs) or C (overnight CLI); use its startup/resume prompt.
7. [DOCUMENTATION_REVIEW.md](DOCUMENTATION_REVIEW.md): documentation checks and independent review findings; not product test evidence.

No prior chat is required to understand the plan. The sanitized implementation is recoverable through PR #119 plus the separately controlled four-artifact recovery. Original private inputs, operator credentials/profiles, stores and historical private reports remain separately required; neither repository is a portable live environment.

## Repository identity and safe entry

Repository: [albarami/IDIS](https://github.com/albarami/IDIS), **public**. Use implementation branch `go-live/implementation-batch-2026-10-16` and open PR #119. The older documentation branch and main are not equivalent implementation baselines. RUN_STATE records the current checkpoint; BASELINE_MANIFEST preserves historical source identities and private prerequisites.

Before acting, inspect the actual working tree, applicable project instructions, branch, HEAD, status and current remote state. Preserve all uncommitted work. Code paths in this package are repository-relative and refer to the assessed Slice100 implementation unless explicitly labelled otherwise; many are absent from main. Do not invent missing files or treat old main as an equivalent baseline.

Current milestone: **complete CIR-04 and pass its required checks, actual Opus review and exact-current-head CI → GL-P02 → GL-P03 → subsequent dependency-ready packets**, under AUTH-CONTINUE-20261016 as refined in RUN_STATE. A passing CIR-04 substep is not a green baseline. Failed, pending or skipped required checks prohibit advancing; WIP publication is allowed but is not acceptance. Preserve the existing master plan and scoped GL-P01 acceptance. Main merges, migration0039, production activation/deployment, new paid services, IDIS live model/provider spending and fund communications remain held.

Historical handoff correction: GL-11 preserves the source Muḥāsibī disciplines; completed CI at `52c830e` is **failed**, not pending/green; and PUB-IMPL-01 specifies public-code/private-artifact recovery. AUTH-IMPL-20261016 subsequently authorized the completed bounded preservation, compatibility-repair and GL-P01 batch. AUTH-CONTINUE-20261016 now supersedes its stop-after-GL-P01 restriction; historical evidence and acceptance scopes remain unchanged.

## Authority and conflict resolution

1. Explicit current owner authorization and its scope; privacy, safety and spending limits.
2. Applicable repository instructions and frozen technical contracts. This plan does not silently amend schemas, contract identities, gate semantics, protected evidence boundaries or reviewed requirements.
3. Active Slice100 plan and later accepted amendments: `docs/plans/2026-07-15-slice100-strict-real-example-live-dry-run.md` in the implementation baseline, especially Tasks 8–15 and appended decisions. Local-only Task8 v27 and Task9 plans are listed in the manifest. Later accepted amendments supersede earlier version prose, not unrelated requirements.
4. This package governs delivery sequencing, solo-owner execution, incremental costs, modernization proposals and handoff. Where a proposal changes a frozen contract, obtain the required ruling first.

An unrelated Whale Hunter rule file exists under `.cursor/rules/` in the assessed checkout; its unrelated product/build order must not be silently applied to IDIS. If any tool presents conflicting instructions as applicable, report the conflict before affected work.

The scoped supersessions are in the master plan's decision record. Historical plans remain evidence; no source plan is deleted. The Slice100 plans are now in the separately reviewed implementation publication, with provenance in the recovery receipt. Use those actual plans and current RUN_STATE, not reconstructed authority from this summary.

## Completion means

The full requirement set is substantively answered or explicitly dispositioned, trust and challenge paths execute with governed evidence, analyst corrections regenerate affected outputs, all four fund deliverables are reviewable, and release/operational/fund gates pass. Two-question pilot outputs must be visibly partial and cannot stand in for a comprehensive investment score or recommendation. An interim fund release is not completion of modernization.
