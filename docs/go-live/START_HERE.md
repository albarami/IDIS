# IDIS execution package — start here

Status: documentation handoff, not implementation authorization or go-live acceptance. Updated 2026-10-16.

## Objective

Complete the existing Institutional Deal Intelligence System (VC Edition) so a fund can perform financial, market and commercial diligence using its own methodology and templates, reducing measured analyst work without weakening analytical quality. Retain the existing trust, multiagent, calculation, market-intelligence and persistence architecture. Do not create a parallel framework.

## Read in this order

1. [RUN_STATE.md](RUN_STATE.md): current permissions, decisions, blockers and exact next action.
2. [BASELINE_MANIFEST.md](BASELINE_MANIFEST.md): GitHub versus local implementation identity and private prerequisites.
3. [GO_LIVE_MASTER_PLAN.md](GO_LIVE_MASTER_PLAN.md): authoritative scope, architecture, requirements, modernization choices and estimates.
4. [IMPLEMENTATION_BACKLOG.md](IMPLEMENTATION_BACKLOG.md): executable packets and dependencies; first implementation packet is **GL-P01**.
5. [VERIFICATION_AND_RUNBOOKS.md](VERIFICATION_AND_RUNBOOKS.md): acceptance evidence, release and operations.
6. [AGENT_EXECUTION.md](AGENT_EXECUTION.md): choose Mode A (one agent/application), B (two cooperating CLIs) or C (overnight CLI); use its startup/resume prompt.
7. [DOCUMENTATION_REVIEW.md](DOCUMENTATION_REVIEW.md): documentation checks and independent review findings; not product test evidence.

No prior chat is required to understand the plan. Private sources and local-only implementation are still required to execute it; the package does not pretend to contain them.

## Repository identity and safe entry

Repository: [albarami/IDIS](https://github.com/albarami/IDIS), **public**. The publication branch is based on freshly verified GitHub main, not on unpublished implementation commits. Exact SHAs and local paths live only in the baseline manifest to avoid conflicting copies.

Before acting, inspect the actual working tree, applicable project instructions, branch, HEAD, status and current remote state. Preserve all uncommitted work. Code paths in this package are repository-relative and refer to the assessed Slice100 implementation unless explicitly labelled otherwise; many are absent from main. Do not invent missing files or treat old main as an equivalent baseline.

Current milestone: **authorized staged implementation batch PUB-IMPL-01 → CIR-01..04 → GL-P01**, bounded by AUTH-IMPL-20261016 in RUN_STATE. Preserve the approved package at `070fb57e4485e2ff6101cf7d7f26b60f38e9ecbe`; no broad replanning. GL-P02/03, merge, migration0039, activation and live execution remain outside this batch.

Historical handoff correction: GL-11 preserves the source Muḥāsibī disciplines; completed CI at `52c830e` is **failed**, not pending/green; and PUB-IMPL-01 specifies public-code/private-artifact recovery. Those documentation corrections did not authorize product actions. The subsequent owner grant AUTH-IMPL-20261016 in RUN_STATE now authorizes only the bounded preservation, compatibility-repair and GL-P01 batch above.

## Authority and conflict resolution

1. Explicit current owner authorization and its scope; privacy, safety and spending limits.
2. Applicable repository instructions and frozen technical contracts. This plan does not silently amend schemas, contract identities, gate semantics, protected evidence boundaries or reviewed requirements.
3. Active Slice100 plan and later accepted amendments: `docs/plans/2026-07-15-slice100-strict-real-example-live-dry-run.md` in the implementation baseline, especially Tasks 8–15 and appended decisions. Local-only Task8 v27 and Task9 plans are listed in the manifest. Later accepted amendments supersede earlier version prose, not unrelated requirements.
4. This package governs delivery sequencing, solo-owner execution, incremental costs, modernization proposals and handoff. Where a proposal changes a frozen contract, obtain the required ruling first.

An unrelated Whale Hunter rule file exists under `.cursor/rules/` in the assessed checkout; its unrelated product/build order must not be silently applied to IDIS. If any tool presents conflicting instructions as applicable, report the conflict before affected work.

The scoped supersessions are in the master plan's decision record. Historical plans remain evidence; no source plan is deleted. The local Slice100 plan remains necessary until its approved baseline is separately published. If it cannot be accessed, stop affected implementation instead of reconstructing authority from this summary.

## Completion means

The full requirement set is substantively answered or explicitly dispositioned, trust and challenge paths execute with governed evidence, analyst corrections regenerate affected outputs, all four fund deliverables are reviewable, and release/operational/fund gates pass. Two-question pilot outputs must be visibly partial and cannot stand in for a comprehensive investment score or recommendation. An interim fund release is not completion of modernization.
