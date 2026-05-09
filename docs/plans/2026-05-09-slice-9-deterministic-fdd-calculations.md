# Phase 3.0 Slice 9: Deterministic FDD Calculations

## Base And Scope
- Base: `origin/main` at `eaf2fc1f307c5f26b21dbfdbbaa395a6baa28bff`.
- Goal: consume Slice 6 `RunScopedMaterializedClaim` / safe claim shells and Slice 8 run-scoped Sanad grade records, run eligible deterministic CDD/FDD calculations, attach calculation and CalcSanad outputs to `RunContext`, and persist only safe run-step summaries.
- Stop condition: implementation and validation only. No PR prep unless explicitly requested.

## Reuse Decisions
- Reuse `CalcEngine`, `register_core_formulas()`, `FormulaRegistry`, `DeterministicCalculation`, `CalcOutput`, `CalcSanad`, and `InputGradeInfo` as canonical calculation concepts.
- Reuse `CalcRunner` input/blocker helpers where safe for claim input mapping and decimal extraction.
- Add Slice 9 run-scoped wrappers because existing calculation repositories assume durable persisted claims and engine-generated IDs/timestamps are not deterministic enough for run-scoped summaries.
- Do not reuse fixture/demo scripts in production logic.

## Additional Inventory Checked
- `src/idis/methodology/models.py::RequiredCalculation`: reused as the methodology requirement contract.
- `src/idis/models/extraction_task.py::ExpectedAnswerSchema.required_calculations`: used as the authoritative source for required calculations.
- `src/idis/methodology/importers/fdd_excel.py::_required_calculations_for_sheet`: inventory context only; not used at runtime.
- `src/idis/methodology/templates/commercial_dd_v1.json`: inventory context only for methodology-authored required calculations.
- `scripts/add_calcs_to_adversarial.py`, `scripts/generate_gdbs_full.py`, `scripts/llm_demo_one_deal.py`: not reused in production; fixture/demo behavior only.
- `tests/test_extraction_gate.py`, `tests/test_calc_value_types_integration.py`, and existing calc tests: reused as behavioral reference for gate, formula, and value semantics.

## Implementation Notes
- New FULL-only step: `METHODOLOGY_DETERMINISTIC_CALCULATION`, placed after `METHODOLOGY_SANAD_CREATION_LINKING_GRADING` and before legacy `EXTRACT`.
- Candidate selection starts from `task.expected_answer_schema.required_calculations`; no top-level `ExtractionTask.required_calculations` field is used.
- IDs are deterministic UUID v5 values seeded by tenant, deal, run, calc type, formula hash, input claim IDs, task ID, question ID, and coverage ID.
- Synthetic timestamps use a fixed epoch sequence.
- Summaries include safe IDs, hashes, calc types, scalar output metadata, grades, statuses, counts, and reason codes only.

## Deferred
- Durable Calc/CalcSanad persistence over durable Claim/Sanad inputs remains deferred.
- Truth Dashboard, enrichment/API checks, Layer 1 Evidence Trust Court, Validated Evidence Package, Layer 2 IC Debate, GO/CONDITIONAL/NO-GO package, deliverables, and real data-room E2E remain deferred.
