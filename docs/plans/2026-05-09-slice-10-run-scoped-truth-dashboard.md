# Phase 3.0 Slice 10: Run-Scoped Truth Dashboard

## Base And Scope
- Base: `origin/main` at `bce6206069abc3719cf516d140d8b0dd125ef887`.
- Worktree/branch: `phase-3-0j-run-scoped-truth-dashboard`, isolated from the Slice 9 worktree and root workspace.
- Goal: add a FULL-only in-memory Truth Dashboard step after `METHODOLOGY_DETERMINISTIC_CALCULATION` and before legacy `EXTRACT`.
- Inputs: Slice 6 run-scoped claims, Slice 7 EvidenceItems/source provenance, Slice 8 Sanads/grades/defects, and Slice 9 calculations/CalcSanads when present.
- Output: run-scoped dashboard rows, summary counts, safe references, and resume shells attached to `RunContext`; persisted only as safe run-step summary.
- Stop condition: implementation and validation only. No PR prep unless explicitly requested.

## Reuse Decisions
- Reuse `src/idis/deliverables/truth_dashboard.py::TruthDashboardBuilder` for deterministic in-memory row construction where safe.
- Reuse `src/idis/models/deliverables.py::TruthDashboard` / `TruthRow` shape where safe for in-memory row construction and No-Free-Facts validation.
- Reuse `src/idis/validators/deliverable.py` validation helpers where possible; rows with assertions must be backed by claim or calc refs.
- Reuse Slice 6-9 run-scoped models as inputs and safe summary references.
- Do not reuse `src/idis/api/routes/claims.py::get_deal_truth_dashboard` at runtime; it is inventory only because it reads durable repositories and expresses an API contract.
- Treat UI/OpenAPI dashboard contracts as inventory only, not Slice 10 runtime dependencies.
- Do not reuse deliverables generator integration; final deliverables remain later-slice work.

## Implementation Notes
- Add `src/idis/models/truth_dashboard_materialization.py` for `RunScopedTruthDashboardRecord`, `RunScopedTruthDashboardShell`, row mappings, summaries, and run results.
- Add `src/idis/services/runs/methodology_truth_dashboard.py`, with helpers if needed to keep files under 500 lines.
- Add `METHODOLOGY_TRUTH_DASHBOARD` to `src/idis/models/run_step.py` as FULL-only after Slice 9 and before legacy `EXTRACT`.
- Extend `src/idis/services/runs/orchestrator.py` with Slice 10 function injection, context fields, step dispatch, blockers, and resume shell rehydration.
- Update `scripts/audit_full_system_wiring.py` and audit tests to state that Slice 10 is an in-memory run-scoped Truth Dashboard boundary only.

## Verdict Rules
- Build one deterministic row per accepted run-scoped claim, enriched with Sanad grade, evidence IDs, defect IDs, and linked calc IDs when available.
- Claim row verdicts are based on Sanad grade, evidence linkage, and defects.
- `CONFIRMED`: Sanad grade `A` or `B`, no fatal defects, and evidence linkage exists.
- `UNVERIFIED`: Sanad grade `C`, missing required evidence linkage, or diagnostic uncertainty that does not rise to disputed/refuted.
- `REFUTED`: Sanad grade `D` or fatal defect count greater than zero.
- `DISPUTED`: major defects or material contradictions when represented by existing defect severity/reason fields.
- Do not mark an otherwise `A`/`B` evidence-backed claim `UNVERIFIED` solely because no optional calculation is linked.
- Calculation linkage is supplemental unless the row assertion is calculation-derived. Required calculation failures should already fail closed in Slice 9.
- Never invent narrative assertions. Use existing full `RunScopedMaterializedClaim.claim_text` only for in-memory rows; shells must not be used to fabricate assertions.

## Safe Summary Rules
- Include only dashboard ID, row IDs, claim IDs, evidence IDs, Sanad IDs, calc IDs, defect IDs, grade/verdict/status counts, reason codes, and row counts.
- Exclude raw span text, locators, document names, paths/URIs, claim text, value structs, full Truth Dashboard rows, full audit appendix, grade explanations, defect descriptions, calculation input values, and deliverable export payloads.
- Resume shells can be rebuilt from summaries, but user-facing assertions cannot.

## Fail-Closed And Resume
- Missing claims, evidence, Sanads, or Sanad grades blocks the orchestrator.
- Empty completed Slice 9 calculation context is allowed when no calculations were produced or requested.
- Explicit empty input lists may return completed diagnostic no-op only when prior context exists.
- Shell-only resume before Slice 10 must fail closed or produce diagnostic blockers only.
- Reject with stable lowercase snake_case reason codes for tenant/deal/run mismatch, duplicate claim rows, missing Sanad grade, missing evidence linkage, missing source provenance, shell-only row construction, and NFF validation failure.

## Tests
- Add model tests for deterministic IDs, shell safety, safe summary exclusion, and validation failure mapping.
- Add service tests for happy path rows, deterministic ordering, verdict mapping, optional calc absence, calc linkage, fatal/major defect effects, duplicate handling, no fake assertions from shells, and safe summaries.
- Add orchestrator tests for step placement, context attachment, missing-context blockers, empty calc context allowance, resume shell rehydration, and no downstream artifacts.
- Update step-count/order tests and full-system audit baseline tests.

## Validation Commands
- `python scripts/forbidden_scan.py`
- `py -3.13 -m ruff format --check .`
- `py -3.13 -m ruff check .`
- `py -3.13 -m mypy src/idis --ignore-missing-imports`
- Focused Slice 10/run/audit tests.
- Full `py -3.13 -m pytest -q`.
- `python scripts/run_postgres_integration_local.py`

## Deferred
- Durable Truth Dashboard persistence remains deferred.
- Durable Claim/Sanad/Defect/Calc promotion remains deferred.
- API routes, UI, OpenAPI, deliverables integration, Layer 1 Evidence Trust Court, Validated Evidence Package, enrichment/API checks, Layer 2 IC Debate, GO/CONDITIONAL/NO-GO package, real data-room E2E, and Slice 11 remain deferred.
