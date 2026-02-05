# Phase-Gated Rebuild Tasks

**Version:** 1.0.0  
**Date:** 2026-02-05  
**Status:** Execution Plan  
**Purpose:** Convert rebuild pack specs into executable task list aligned to release gates

---

## 1. Overview

This document provides the phase-gated task plan for completing the IDIS E2E rebuild. Tasks are organized by phase and reference the rebuild pack specifications.

**Gate 3 Unblock Checklist:**
- [ ] Ingestion → Extraction wired
- [ ] Sanad auto-chain built
- [ ] Debate triggers from graded claims
- [ ] Deliverables generated from debate output
- [ ] `/v1/deals/{dealId}/runs` executes full pipeline

---

## 2. Phase 0 — Foundation Verification ✅ COMPLETE

**Status:** Already done in legacy baseline

| Task | Spec Reference | Status |
|------|----------------|--------|
| CI/CD pipeline | — | ✅ |
| Pre-commit hooks | — | ✅ |
| FastAPI /health | — | ✅ |
| OpenAPI loader | — | ✅ |

**Exit Criteria:** ✅ All met

---

## 3. Phase 1 — Ingestion Pipeline Completion

**Goal:** Complete ingestion service and wire to API

### 1.1 Document API Endpoints

| Task | Module | Spec | Priority |
|------|--------|------|----------|
| `POST /v1/deals/{dealId}/documents` | `api/routes/documents.py` | 01_claim_extraction | 🔴 |
| `GET /v1/deals/{dealId}/documents` | `api/routes/documents.py` | 01_claim_extraction | 🔴 |
| `GET /v1/documents/{docId}` | `api/routes/documents.py` | 01_claim_extraction | 🔴 |
| `POST /v1/documents/{docId}/ingest` | `api/routes/documents.py` | 01_claim_extraction | 🔴 |
| `GET /v1/documents/{docId}/spans` | `api/routes/documents.py` | 01_claim_extraction | 🟡 |

### 1.2 Ingestion Service Integration

| Task | Module | Spec | Priority |
|------|--------|------|----------|
| Wire ingestion service to API | `services/ingestion/` | 01_claim_extraction | 🔴 |
| Emit audit events for ingestion | `services/ingestion/` | 03_pipeline_orch | 🔴 |
| Handle ingestion failures | `services/ingestion/` | 01_claim_extraction | 🔴 |

### 1.3 Tests

| Test | File | Status |
|------|------|--------|
| Document API CRUD | `test_api_documents.py` | ⏳ Expand |
| Ingestion E2E | `test_ingestion_service.py` | ⏳ Expand |
| Failure handling | `test_ingestion_failures.py` | ⏳ Create |

**Exit Criteria:**
- [ ] Document upload works via API
- [ ] Ingestion triggers on upload
- [ ] Spans generated and stored
- [ ] Audit events emitted
- [ ] Gate 0 passes

---

## 4. Phase 2 — Claim Extraction Pipeline 🔴 CRITICAL

**Goal:** Extract claims from parsed documents

### 2.1 Chunking Service

| Task | Module | Spec | Priority |
|------|--------|------|----------|
| PDF chunker | `services/extraction/chunking/pdf_chunker.py` | 01_claim_extraction §3.1 | 🔴 |
| XLSX chunker | `services/extraction/chunking/xlsx_chunker.py` | 01_claim_extraction §3.2 | 🔴 |
| DOCX chunker | `services/extraction/chunking/docx_chunker.py` | 01_claim_extraction §3.3 | 🔴 |
| PPTX chunker | `services/extraction/chunking/pptx_chunker.py` | 01_claim_extraction §3.4 | 🔴 |

### 2.2 Extraction Service

| Task | Module | Spec | Priority |
|------|--------|------|----------|
| Claim extractor | `services/extraction/extractors/claim_extractor.py` | 01_claim_extraction §4 | 🔴 |
| Entity extractor | `services/extraction/extractors/entity_extractor.py` | 01_claim_extraction §5 | 🔴 |
| Confidence scorer | `services/extraction/confidence/scorer.py` | 01_claim_extraction §6 | 🔴 |

### 2.3 Deduplication & Conflict

| Task | Module | Spec | Priority |
|------|--------|------|----------|
| Deduplicator | `services/extraction/resolution/deduplicator.py` | 01_claim_extraction §5.2 | 🔴 |
| Conflict detector | `services/extraction/resolution/conflict_detector.py` | 01_claim_extraction §5.3 | 🔴 |
| Reconciler | `services/extraction/resolution/reconciler.py` | 01_claim_extraction §8 | 🟡 |

### 2.4 Prompts

| Task | Module | Spec | Priority |
|------|--------|------|----------|
| EXTRACT_CLAIMS_V1 | `prompts/extract_claims/1.0.0/` | 02_prompt_library §2.1 | 🔴 |
| CLASSIFY_DOC_V1 | `prompts/classify_doc/1.0.0/` | 02_prompt_library §2.2 | 🟡 |
| ENTITY_RESOLUTION_V1 | `prompts/entity_resolution/1.0.0/` | 02_prompt_library §2.3 | 🟡 |

### 2.5 Tests

| Test | File | Status |
|------|------|--------|
| Chunking unit tests | `test_chunking.py` | ⏳ Create |
| Extraction unit tests | `test_extraction_unit.py` | ⏳ Create |
| Confidence scoring | `test_confidence_scorer.py` | ⏳ Create |
| Deduplication | `test_deduplication.py` | ⏳ Create |
| GDBS-S extraction | `test_gdbs_s_extraction.py` | ⏳ Create |

**Exit Criteria:**
- [ ] Claims extracted from all doc types
- [ ] Confidence scores assigned
- [ ] Deduplication works
- [ ] Conflicts detected
- [ ] ≥90% extraction accuracy on GDBS-S
- [ ] Gate 0 passes

---

## 5. Phase 3 — Sanad Auto-Chain 🔴 CRITICAL

**Goal:** Automatically build Sanad chains from extracted claims

### 3.1 Chain Builder

| Task | Module | Spec | Priority |
|------|--------|------|----------|
| Auto-chain builder | `services/sanad/chain_builder.py` | IDIS_Sanad_Methodology_v2 | 🔴 |
| Evidence linker | `services/sanad/evidence_linker.py` | 01_claim_extraction §4.2 | 🔴 |
| Independence calculator | `services/sanad/tawatur.py` | Already exists, wire | 🔴 |

### 3.2 Integration

| Task | Module | Spec | Priority |
|------|--------|------|----------|
| Wire extraction → Sanad | `pipeline/steps/grade_step.py` | 03_pipeline_orch | 🔴 |
| Auto-grade on extraction | `services/sanad/grader.py` | Already exists, wire | 🔴 |
| Defect auto-detection | `services/sanad/ilal.py` | Already exists, wire | 🔴 |

### 3.3 Prompts

| Task | Module | Spec | Priority |
|------|--------|------|----------|
| SANAD_GRADER_V1 | `prompts/sanad_grader/1.0.0/` | 02_prompt_library §3.1 | 🔴 |
| DEFECT_DETECTOR_V1 | `prompts/defect_detector/1.0.0/` | 02_prompt_library §3.2 | 🔴 |

### 3.4 Tests

| Test | File | Status |
|------|------|--------|
| Chain building | `test_chain_builder.py` | ⏳ Create |
| Auto-grading E2E | `test_auto_grade_e2e.py` | ⏳ Create |
| GDBS-S sanad coverage | `test_gdbs_s_sanad.py` | ⏳ Create |

**Exit Criteria:**
- [ ] Claims auto-get Sanad objects
- [ ] Grades computed automatically
- [ ] Defects auto-detected
- [ ] ≥95% Sanad coverage on GDBS-S
- [ ] Gate 2 passes

---

## 6. Phase 4 — Pipeline Orchestration 🔴 CRITICAL

**Goal:** Wire all steps into executable pipeline

### 4.1 Pipeline Core

| Task | Module | Spec | Priority |
|------|--------|------|----------|
| Pipeline orchestrator | `pipeline/orchestrator.py` | 03_pipeline_orch §2 | 🔴 |
| State machine | `pipeline/state_machine.py` | 03_pipeline_orch §3 | 🔴 |
| Run manager | `pipeline/run_manager.py` | 03_pipeline_orch §4 | 🔴 |
| Checkpoint store | `pipeline/checkpoints/checkpoint_store.py` | 03_pipeline_orch §5 | 🔴 |

### 4.2 Pipeline Steps

| Task | Module | Spec | Priority |
|------|--------|------|----------|
| Parse step | `pipeline/steps/parse_step.py` | 03_pipeline_orch | 🔴 |
| Extract step | `pipeline/steps/extract_step.py` | 03_pipeline_orch | 🔴 |
| Grade step | `pipeline/steps/grade_step.py` | 03_pipeline_orch | 🔴 |
| Calc step | `pipeline/steps/calc_step.py` | 03_pipeline_orch | 🔴 |
| Enrich step | `pipeline/steps/enrich_step.py` | 03_pipeline_orch | 🟡 |
| Debate step | `pipeline/steps/debate_step.py` | 03_pipeline_orch | 🔴 |
| Deliver step | `pipeline/steps/deliver_step.py` | 03_pipeline_orch | 🔴 |

### 4.3 Runs API

| Task | Module | Spec | Priority |
|------|--------|------|----------|
| `POST /v1/deals/{dealId}/runs` | `api/routes/runs.py` | 03_pipeline_orch §4.3 | 🔴 |
| `GET /v1/runs/{runId}` | `api/routes/runs.py` | 03_pipeline_orch §4.4 | 🔴 |
| Run status websocket | `api/routes/runs.py` | 03_pipeline_orch | 🟡 |

### 4.4 Tests

| Test | File | Status |
|------|------|--------|
| State transitions | `test_state_machine.py` | ⏳ Create |
| Pipeline E2E | `test_pipeline_e2e.py` | ⏳ Create |
| Checkpoint/resume | `test_checkpoint_resume.py` | ⏳ Create |
| Runs API | `test_api_runs_full.py` | ⏳ Create |

**Exit Criteria:**
- [ ] Pipeline runs E2E
- [ ] State transitions work
- [ ] Checkpoints enable resume
- [ ] Runs API triggers pipeline
- [ ] Audit events at each step
- [ ] Gate 0 passes

---

## 7. Phase 5 — Debate Integration 🔴 CRITICAL

**Goal:** Wire debate orchestrator into pipeline

### 5.1 Debate Wiring

| Task | Module | Spec | Priority |
|------|--------|------|----------|
| Wire orchestrator to pipeline | `pipeline/steps/debate_step.py` | 04_agent_framework | 🔴 |
| Implement all tools | `debate/tools/*.py` | 04_agent_framework §4 | 🔴 |
| Wire Muḥāsabah gate | `debate/muhasabah_gate.py` | Already exists, wire | 🔴 |

### 5.2 Prompts

| Task | Module | Spec | Priority |
|------|--------|------|----------|
| DEBATE_ADVOCATE_V1 | `prompts/debate_advocate/1.0.0/` | 02_prompt_library §4.1 | 🔴 |
| DEBATE_SANAD_BREAKER_V1 | `prompts/debate_sanad_breaker/1.0.0/` | 02_prompt_library §4.2 | 🔴 |
| DEBATE_ARBITER_V1 | `prompts/debate_arbiter/1.0.0/` | 02_prompt_library §4.3 | 🔴 |
| MUHASABAH_VALIDATOR_V1 | `prompts/muhasabah_validator/1.0.0/` | 02_prompt_library §5.1 | 🔴 |

### 5.3 Tests

| Test | File | Status |
|------|------|--------|
| Debate E2E | `test_debate_e2e.py` | ⏳ Create |
| Tool permission matrix | `test_tool_permissions.py` | ⏳ Create |
| GDBS-F debate completion | `test_gdbs_f_debate.py` | ⏳ Create |

**Exit Criteria:**
- [ ] Debate triggers from pipeline
- [ ] All agent roles participate
- [ ] Muḥāsabah gate enforced
- [ ] ≥98% debate completion on GDBS-F
- [ ] Gate 3 (debate) passes

---

## 8. Phase 6 — Deliverables Generation

**Goal:** Generate deliverables from debate output

### 6.1 Deliverables Wiring

| Task | Module | Spec | Priority |
|------|--------|------|----------|
| Wire to pipeline | `pipeline/steps/deliver_step.py` | 03_pipeline_orch | 🔴 |
| NFF validator gate | `validators/deliverable.py` | Already exists, enforce | 🔴 |

### 6.2 Prompts

| Task | Module | Spec | Priority |
|------|--------|------|----------|
| IC_MEMO_GENERATOR_V1 | `prompts/ic_memo/1.0.0/` | 02_prompt_library §6.1 | 🔴 |
| SCREENING_SNAPSHOT_GENERATOR_V1 | `prompts/screening/1.0.0/` | 02_prompt_library §6.2 | 🔴 |

### 6.3 Tests

| Test | File | Status |
|------|------|--------|
| Deliverable generation E2E | `test_deliverable_e2e.py` | ⏳ Create |
| NFF enforcement | `test_deliverable_nff.py` | Exists, expand |

**Exit Criteria:**
- [ ] Deliverables generated from debate
- [ ] NFF validator enforced
- [ ] Export to PDF/DOCX works
- [ ] ≥98% deliverable generation on GDBS-F

---

## 9. Phase 7 — GDBS & Gate 3 Execution

**Goal:** Create benchmark data and pass Gate 3

### 7.1 GDBS Creation

| Task | Module | Spec | Priority |
|------|--------|------|----------|
| GDBS-S dataset (20 deals) | `datasets/gdbs/gdbs_s/` | 05_testing_gdbs §3 | 🔴 |
| GDBS-F dataset (100 deals) | `datasets/gdbs/gdbs_f/` | 05_testing_gdbs §4 | 🔴 |
| GDBS-A dataset (30 deals) | `datasets/gdbs/gdbs_a/` | 05_testing_gdbs §5 | 🔴 |
| Synthetic generator | `datasets/synthetic/generators/` | 05_testing_gdbs §6 | 🔴 |

### 7.2 Gate Scripts

| Task | Module | Spec | Priority |
|------|--------|------|----------|
| Gate 3 evaluation script | `scripts/gates/gate_3_gdbs_f.py` | 05_testing_gdbs §8 | 🔴 |
| Adversarial test script | `scripts/gates/gate_adversarial.py` | 05_testing_gdbs §8.2 | 🔴 |

### 7.3 Tests

| Test | File | Status |
|------|------|--------|
| GDBS-S full pipeline | `test_gdbs_s_full.py` | ⏳ Create |
| GDBS-F full pipeline | `test_gdbs_f_full.py` | ⏳ Create |
| GDBS-A defect detection | `test_gdbs_a_defects.py` | ⏳ Create |

**Exit Criteria:**
- [ ] GDBS datasets created
- [ ] Gate 3 script runs
- [ ] ≥95% pipeline completion
- [ ] ≥98% debate completion
- [ ] 100% FATAL defect detection
- [ ] **GATE 3 PASSES**

---

## 10. Phase 8 — Frontend Completion

**Goal:** Complete missing frontend screens

### 8.1 Screens

| Task | File | Spec | Priority |
|------|------|------|----------|
| Runs list page | `ui/src/app/runs/page.tsx` | 08_frontend §3.1 | 🟡 |
| Run detail page | `ui/src/app/runs/[runId]/page.tsx` | 08_frontend §3.2 | 🟡 |
| Deliverables page | `ui/src/app/deals/[dealId]/deliverables/page.tsx` | 08_frontend §3.3 | 🟡 |
| Claim drawer | `ui/src/components/claims/ClaimDrawer.tsx` | 08_frontend §3.4 | 🟡 |
| Governance dashboard | `ui/src/app/admin/governance/page.tsx` | 08_frontend §3.6 | 🟢 |

### 8.2 Tests

| Test | File | Status |
|------|------|--------|
| UI component tests | `ui/__tests__/` | ⏳ Expand |
| E2E tests (Playwright) | `ui/e2e/` | ⏳ Create |

**Exit Criteria:**
- [ ] All missing screens implemented
- [ ] Loading/error states complete
- [ ] Mobile responsive
- [ ] UI tests pass

---

## 11. Phase 9 — Enterprise Hardening

**Goal:** Production-ready infrastructure

### 9.1 Infrastructure

| Task | File | Spec | Priority |
|------|------|------|----------|
| docker-compose.yml | `docker-compose.yml` | 07_infra §2.1 | 🟡 |
| Dockerfile (backend) | `Dockerfile` | 07_infra §2.2 | 🟡 |
| Dockerfile (frontend) | `ui/Dockerfile` | 07_infra §2.3 | 🟡 |
| K8s manifests | `k8s/base/` | 07_infra §3 | 🟢 |

### 9.2 Connectors

| Task | Module | Spec | Priority |
|------|--------|------|----------|
| Connector framework | `services/enrichment/` | 06_enrichment | 🟢 |
| PitchBook connector | `services/enrichment/connectors/pitchbook.py` | 06_enrichment §8.1 | 🟢 |
| SEC EDGAR connector | `services/enrichment/connectors/sec_edgar.py` | 06_enrichment §8.2 | 🟢 |

### 9.3 Prompt Registry

| Task | File | Spec | Priority |
|------|------|------|----------|
| Registry index | `prompts/registry.yaml` | 02_prompt_library | 🟡 |
| All prompt files | `prompts/<id>/<version>/` | 02_prompt_library | 🟡 |

**Exit Criteria:**
- [ ] Docker setup works
- [ ] K8s manifests apply
- [ ] Prompt registry complete
- [ ] Connectors functional

---

## 12. Summary: Gate 3 Unblock Path

### 12.1 Critical Dependency Chain (NON-SKIPPABLE)

**Gate 3 unblock requires Phase 2 → Phase 3 → Phase 4 → Phase 6 glue. Phases CANNOT be skipped or reordered.**

```
Phase 2 (Extraction) ──▶ Phase 3 (Sanad) ──▶ Phase 4 (Orchestration) ──▶ Phase 6 (Deliverables)
       │                       │                      │                         │
       │                       │                      │                         │
   REQUIRED:              REQUIRED:              REQUIRED:                 REQUIRED:
   - Claims exist         - Sanad chains         - Pipeline runs E2E       - Deliverables
   - Evidence linked      - Grades computed      - State transitions       - NFF enforced
   - Spans preserved      - Defects detected     - Audit complete          - Export works
```

**Why phases cannot be skipped:**
- **Phase 3 depends on Phase 2:** Sanad chains require extracted claims with evidence links
- **Phase 4 depends on Phase 3:** Pipeline orchestration wires graded claims to debate
- **Phase 6 depends on Phase 4:** Deliverables require completed debate output from pipeline
- **Gate 3 requires Phase 6:** Full E2E from document upload → deliverable export

### 12.2 Critical Path (in order)

1. **Week 1-2:** Phase 2 (Claim Extraction)
   - Chunkers for all doc types
   - Extraction service with prompts
   - Deduplication/conflict detection

2. **Week 2-3:** Phase 3 (Sanad Auto-Chain)
   - Chain builder
   - Wire to extraction
   - Auto-grading

3. **Week 3-4:** Phase 4 (Pipeline Orchestration)
   - State machine
   - All pipeline steps
   - Runs API

4. **Week 4-5:** Phase 5 (Debate Integration)
   - Wire orchestrator
   - All tools implemented
   - Muḥāsabah enforcement

5. **Week 5-6:** Phase 6-7 (Deliverables + GDBS)
   - Deliverable generation
   - GDBS datasets
   - Gate 3 execution

**Gate 3 Target:** Week 6

---

## 13. Verification Commands

```bash
# After each phase
make format && make lint && make typecheck && make test && make check

# Gate 3 execution
python scripts/gates/gate_3_gdbs_f.py --execute

# Full check
make.bat check  # Windows
make check      # Linux/Mac
```
