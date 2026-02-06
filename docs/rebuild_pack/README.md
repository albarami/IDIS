# IDIS E2E Rebuild Pack

**Version:** 1.0.0  
**Date:** 2026-02-05  
**Purpose:** Complete documentation set for rebuilding IDIS E2E pipeline from legacy baseline

---

## Overview

This rebuild pack contains the specifications needed to close the E2E gaps blocking Gate 3. The legacy baseline (`docs/legacy/`) documents what's already complete; this pack documents what needs to be built.

---

## Document Index

| # | Document | Purpose | Priority |
|---|----------|---------|----------|
| 01 | [Claim Extraction Pipeline](01_claim_extraction_pipeline_spec.md) | Chunking, extraction, deduplication, conflicts | 🔴 Critical |
| 02 | [Prompt Library](02_prompt_library.md) | Full prompt texts, schemas, failure modes | 🔴 Critical |
| 03 | [Pipeline Orchestration](03_pipeline_orchestration_spec.md) | E2E pipeline DAG, state machine, runs API | 🔴 Critical |
| 04 | [Agent Framework & Tools](04_agent_framework_and_tools_spec.md) | Agent I/O, tool contracts, Muḥāsabah | 🔴 Critical |
| 05 | [Testing & GDBS Data Plan](05_testing_and_gdbs_data_plan.md) | Benchmark datasets, test-to-gate mapping | 🔴 Critical |
| 06 | [Enrichment Connectors](06_enrichment_connectors_spec.md) | External data integration, BYOL credentials | 🟡 High |
| 07 | [Infrastructure & Deployment](07_infra_and_deployment_artifacts_spec.md) | Docker, K8s, env vars | 🟡 High |
| 08 | [Frontend Build](08_frontend_build_spec.md) | Missing screens, wireframes, components | 🟡 High |
| 09 | [Phase-Gated Tasks](09_phase_gated_rebuild_tasks.md) | Executable task list by phase | 🔴 Critical |

---

## Quick Start

### 1. Review Legacy Baseline
```bash
# See what's already done
cat docs/legacy/LEGACY_BASELINE.md

# See locked decisions (do not change)
cat docs/legacy/DECISIONS_LOCKED.md
```

### 2. Understand Gate 3 Blockers
```bash
# Current blocker status
cat docs/gates/gate_3_blocked_status.json
```

### 3. Follow Phase-Gated Tasks
Start with `09_phase_gated_rebuild_tasks.md` and work through phases in order.

---

## Gate 3 Blocker-to-Task Mapping

Source: `docs/gates/gate_3_blocked_status.json` (verbatim). Each blocker must map to a rebuild pack doc, a concrete implementation task (by stable ID from `09_phase_gated_rebuild_tasks.md`), and named test hooks. **Gate 3 is unblocked only when all 5 rows show ✅ and all named test hooks pass in CI.**

| # | Blocker (verbatim from gate_3_blocked_status.json) | Rebuild Pack Doc | Implementation Task ID | Test Hook(s) | Status |
|---|-----------------------------------------------------|------------------|----------------------|--------------|--------|
| 1 | Document ingestion pipeline not integrated with claim extraction | 01_claim_extraction_pipeline_spec.md, 03_pipeline_orchestration_spec.md | P1-T02 (Ingestion Service Integration), P2-T01 (Extraction Service) | `test_ingestion_triggers_extraction`, `test_extraction_produces_claims_from_ingested_doc` | ⏳ |
| 2 | Claim extraction service not operational | 01_claim_extraction_pipeline_spec.md, 02_prompt_library.md | P2-T01 (Extraction Service), P2-T02 (Chunkers), P2-T03 (Dedup/Conflict) | `test_extraction_service_e2e`, `test_chunker_span_preservation`, `test_dedup_conflict_detection` | ⏳ |
| 3 | Sanad chain building not automated (only manual test scripts exist) | 03_pipeline_orchestration_spec.md | P3-T01 (Chain Builder), P3-T02 (Auto-Grading) | `test_sanad_auto_chain_from_claims`, `test_auto_grading_produces_valid_grades` | ⏳ |
| 4 | Debate execution not integrated with deliverable generation | 03_pipeline_orchestration_spec.md, 04_agent_framework_and_tools_spec.md | P5-T01 (Wire Orchestrator to Debate), P6-T01 (Deliverable Generation from Debate) | `test_debate_output_feeds_deliverable_generator`, `test_deliverable_nff_enforcement` | ⏳ |
| 5 | No /v1/deals/{dealId}/runs endpoint that executes full pipeline | 03_pipeline_orchestration_spec.md §4.3 | P4-T03 (Runs API: `POST /v1/deals/{dealId}/runs`) | `test_runs_api_triggers_full_pipeline`, `test_run_state_transitions_e2e`, `test_run_audit_events_all_steps` | ⏳ |

### Additional Gate 3 Prerequisites

| Requirement | Spec | Status |
|-------------|------|--------|
| GDBS-S dataset created | 05_testing_and_gdbs_data_plan.md | ⏳ |
| GDBS-F dataset created | 05_testing_and_gdbs_data_plan.md | ⏳ |
| Gate 3 script executable | 05_testing_and_gdbs_data_plan.md | ⏳ |

---

## Related Documents

### Legacy (Do Not Re-Implement)
- `docs/legacy/LEGACY_BASELINE.md` — What's already done
- `docs/legacy/DECISIONS_LOCKED.md` — Architectural decisions that cannot change

### v6.3 Specifications (Reference)
- `docs/01_IDIS_TDD_v6_3.md` — Technical design
- `docs/02_IDIS_Data_Model_Schema_v6_3.md` — Data model
- `docs/IDIS_Sanad_Methodology_v2.md` — Sanad grading
- `docs/IDIS_Prompt_Registry_and_Model_Policy_v6_3.md` — Prompt governance
- `docs/IDIS_Evaluation_Harness_and_Release_Gates_v6_3.md` — Release gates

### Machine-Readable
- `prompts/registry.yaml` — Prompt registry index
- `docs/gates/gate_3_blocked_status.json` — Gate 3 status

---

## Verification Commands

```bash
# Run all checks before committing
make format && make lint && make typecheck && make test && make check

# Windows
make.bat check

# Gate 3 execution (when ready)
python scripts/gates/gate_3_gdbs_f.py --execute
```

---

## Contact

For questions about this rebuild pack, refer to the v6.3 specification documents or the implementation roadmap at `docs/12_IDIS_End_to_End_Implementation_Roadmap_v6_3.md`.
