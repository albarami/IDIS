# Phase 3.0 Slice 12: Validated Evidence Package

## Base And Branch

- Base: `origin/main` at `a42e6a82abb461cffaa8e3ba01fc1c8e421a7abd`.
- Worktree and branch: `phase-3-0l-validated-evidence-package`.
- Stop after TDD implementation and validation. No PR prep, no Layer 2, and no Slice 13 work.

## Scope

Slice 12 creates an in-memory, run-scoped Layer 1 Validated Evidence Package boundary. It packages the full Slice 11 Evidence Trust Court record into safe, stable IDs and aggregate counts that downstream phases can consume later without exposing raw factual payloads or creating investment recommendations.

Out of scope:

- Layer 2 Investment Committee debate.
- IC-ready packages.
- GO, CONDITIONAL, or NO-GO decisions.
- Recommendations or deliverable payloads.
- API, UI, OpenAPI, real E2E, enrichment/API checks, and durable package persistence.

## Reuse Rules

Build only from a full `RunScopedEvidenceTrustCourtRecord`.

Do not construct a new package from:

- `RunScopedEvidenceTrustCourtShell`.
- Evidence Trust Court summaries.
- deliverables, memo, export, API, or UI payloads.

Add a separate FULL-only `METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE` step after `METHODOLOGY_EVIDENCE_TRUST_COURT` and before legacy `EXTRACT`.

## Required Behaviors

- Use `src/idis/models/validated_evidence_package_materialization.py` for models, matching the repo's materialization naming pattern.
- Build deterministic package IDs from tenant, deal, run, court ID, and sorted claim/finding IDs.
- Package only Layer 1 IDs and metadata: claim IDs by disposition, evidence IDs, source span IDs, Sanad IDs, defect IDs, calculation IDs, finding IDs, finding types, role names if needed, reason codes, and aggregate counts.
- Preserve dissent, contradictions, dashboard consistency, provenance, and Sanad defects as evidence-trust metadata, not recommendations.
- Treat mixed trusted, disputed, rejected, and unverified claims as successful package construction with status `completed`.
- Reserve `partial` for a future explicitly defined assembly condition. Do not use `partial` merely because some claims are not trusted.
- Treat missing court input, shell-only court input, tenant/deal/run mismatch, and missing internal references as failed or blocked construction. Emit no package record in these states.
- Rehydrate a prior VEP shell only when the VEP step itself was already completed and skipped. Never use an Evidence Trust Court shell to construct a new VEP.

## Safe Summary Rules

Safe summaries must exclude:

- raw claim text
- value structs
- document names
- locators
- span text
- defect descriptions
- grade explanations
- `AgentOutput.content`
- debate transcripts
- Muhasabah narrative
- recommendations
- deliverable payloads
- GO, CONDITIONAL, or NO-GO decisions

Safe summaries may include stable IDs, disposition sets, finding IDs and types, role names, reason codes, and aggregate counts by disposition, grade, dashboard verdict, finding type, and reason code.

## Implementation Plan

1. Write failing model tests for deterministic package IDs, safe shells, safe summaries, sorted ID sets, and aggregate counts.
2. Write failing service tests for packaging all dispositions, completed status with mixed dispositions, metadata preservation, calculation propagation, shell-only fail-closed behavior, missing court input, scope mismatch, and internal reference validation.
3. Write failing orchestrator and audit tests for step order, FULL-only wiring, context attachment, missing-context blockers, shell-only blockers, resume shell rehydration, 20-step FULL count, and deferred downstream audit wording.
4. Implement run-scoped VEP models in `src/idis/models/validated_evidence_package_materialization.py`.
5. Implement the in-memory VEP service in `src/idis/services/runs/methodology_validated_evidence_package.py`, splitting helpers if needed to stay under file limits.
6. Add `METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE` to run-step ordering, FULL-only sets, and orchestrator dispatch.
7. Update audit wording to state that the in-memory run-scoped Layer 1 Validated Evidence Package boundary exists, while Layer 2 IC debate, recommendations, deliverables, APIs, persistence, and real E2E remain deferred.

## Validation

- `py -3.13 scripts/forbidden_scan.py`
- `py -3.13 -m ruff format --check .`
- `py -3.13 -m ruff check .`
- `py -3.13 -m mypy src`
- `py -3.13 -m pytest -q tests/test_validated_evidence_package_materialization.py tests/test_run_methodology_validated_evidence_package_service.py tests/test_run_orchestrator_methodology_validated_evidence_package.py`
- `py -3.13 -m pytest -q tests/test_full_system_wiring_baseline.py tests/test_run_orchestrator_new_steps.py tests/test_run_orchestrator_steps.py tests/test_run_orchestrator_debate_step.py`
- `py -3.13 -m pytest -q`
- `py -3.13 -m pytest --xkill`
