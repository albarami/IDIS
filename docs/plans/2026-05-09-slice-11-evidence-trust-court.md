# Phase 3.0 Slice 11: Layer 1 Evidence Trust Court

## Base And Branch

- Base: `origin/main` at `db909a62c83f447dff5de5400667ae89695eaa12`.
- Worktree and branch: `phase-3-0k-evidence-trust-court`.
- Stop after TDD implementation and validation. No PR prep and no Slice 12 work.

## Scope

Slice 11 creates an in-memory, run-scoped Layer 1 Evidence Trust Court boundary. It judges evidence integrity, source provenance, Sanad trust, defects, contradictions, and consistency with the full Truth Dashboard record.

Out of scope:

- Validated Evidence Package, deferred to Slice 12.
- Layer 2 Investment Committee analysis.
- GO, CONDITIONAL, or NO-GO decisions.
- Recommendations or deliverable payloads.
- API, UI, OpenAPI, real E2E, enrichment/API checks, and durable court persistence.

## Reuse Rules

Reuse the existing debate stack through a new run-scoped adapter:

- `DebateOrchestrator`
- `DebateState`
- role protocols
- `MuhasabahGate`
- validators

Do not use these as the Slice 11 runtime path:

- `_run_full_debate`
- `LLMRoleRunner`
- API debate routes
- `StepName.DEBATE`

Add a separate FULL-only `METHODOLOGY_EVIDENCE_TRUST_COURT` step after `METHODOLOGY_TRUTH_DASHBOARD` and before `EXTRACT`.

## Required Behaviors

- Use deterministic UUID aliases for both `supported_claim_ids` and `supported_calc_ids` wherever Muḥāsabah validation requires UUID-like references.
- Store only safe run-scoped IDs in court records, shells, and summaries.
- Prove `claim_mth_*` IDs pass through alias validation and map back correctly.
- Use a full Truth Dashboard record for court verdict consistency.
- Treat a Truth Dashboard resume shell as insufficient for factual court assertions. If only a shell is available, fail closed or return a diagnostic blocked result without fabricating findings.
- Treat any tenant, deal, or run mismatch across claims, evidence, Sanads, grades, defects, calculations, calc Sanads, or Truth Dashboard inputs as fatal. Produce no court record, no shell, and no court IDs.

## Safe Summary Rules

Safe summaries must exclude:

- debate transcripts
- `AgentOutput.content`
- claim text
- value structs
- locators
- document names
- defect descriptions
- grade explanations
- Muḥāsabah narrative
- recommendations
- deliverable payloads

Safe summaries may include stable IDs, role names, dispositions, verdict/grade counts, reason codes, and aggregate counts.

## Implementation Plan

1. Write failing model tests for deterministic court IDs, deterministic alias maps, shell safety, safe summaries, and aggregate status.
2. Write failing service tests for trusted, disputed, rejected, unverified, missing provenance, shell-only Truth Dashboard, Muḥāsabah alias validation, and fatal cross-scope behavior.
3. Write failing orchestrator and audit tests for step order, FULL-only wiring, context attachment, missing-context blockers, failure blocking, resume shell handling, and deferred audit wording.
4. Implement run-scoped court models in `src/idis/models/evidence_trust_court_materialization.py`.
5. Implement the in-memory court adapter in `src/idis/services/runs/methodology_evidence_trust_court.py`, splitting helpers if needed to stay under file limits.
6. Add `METHODOLOGY_EVIDENCE_TRUST_COURT` to run-step ordering and orchestrator dispatch without using the legacy `DEBATE` runtime path.
7. Update audit wording to state that the in-memory run-scoped Layer 1 Evidence Trust Court boundary exists, while Validated Evidence Package and all downstream Layer 2/API/deliverable work remain deferred.

## Validation

- `python scripts/forbidden_scan.py`
- `py -3.13 -m ruff format --check .`
- `py -3.13 -m ruff check .`
- `py -3.13 -m mypy src/idis --ignore-missing-imports`
- Focused Slice 11 model, service, orchestrator, and audit tests.
- Full `py -3.13 -m pytest -q` if practical.
- Postgres integration only if available locally.
