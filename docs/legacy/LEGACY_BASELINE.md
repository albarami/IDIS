# IDIS Legacy Baseline — v6.3 As-Built

**Freeze Date:** 2026-02-05  
**Last Commit:** `fa2d4d2` (main)  
**Tag:** `legacy-v6.3-asbuilt`  
**Purpose:** Document the state of IDIS before the E2E rebuild so "completed work" is distinct from "still missing"

---

## 1. Summary

This document freezes the legacy baseline of IDIS v6.3. All work completed before this tag represents stable, tested components that should not be re-implemented during the rebuild. The rebuild focuses on the **E2E glue** connecting these components into a working pipeline.

---

## 2. Commit & Gate Status

| Item | Value |
|------|-------|
| **Last Commit Hash** | `fa2d4d2` |
| **Branch** | `main` |
| **Gate 0 (Lint/Type/Test)** | ✅ PASS |
| **Gate 1 (Structural Trust)** | ✅ PASS |
| **Gate 2 (Core Quality)** | ✅ PASS |
| **Gate 3 (Full E2E Regression)** | 🔴 BLOCKED |

---

## 3. Completed Components (Do Not Re-Implement)

### 3.1 Phase 0 — Project Setup ✅
- CI/CD (GitHub Actions)
- Pre-commit hooks (ruff format, ruff check)
- FastAPI app with `/health` endpoint
- OpenAPI spec loader

### 3.2 Phase 1 — Ingestion & Parsing ✅
| Component | Module | Tests |
|-----------|--------|-------|
| Object storage abstraction | `storage/object_store.py` | `test_object_store_filesystem.py` |
| PDF parser | `parsers/pdf.py` | `test_pdf_parser.py` |
| XLSX parser | `parsers/xlsx.py` | `test_xlsx_parser.py` |
| DOCX parser | `parsers/docx.py` | `test_docx_parser.py` |
| PPTX parser | `parsers/pptx.py` | `test_pptx_parser.py` |
| Parser registry | `parsers/registry.py` | `test_parser_registry.py` |
| Ingestion service | `services/ingestion/service.py` | `test_ingestion_service.py` |

### 3.3 Phase 2 — API Gate ✅
| Component | Module | Tests |
|-----------|--------|-------|
| Tenant auth (API key) | `api/auth.py` | `test_api_tenancy_auth.py` |
| OpenAPI validation middleware | `api/middleware/openapi_validate.py` | `test_api_openapi_validation.py` |
| Audit middleware | `api/middleware/audit.py` | `test_api_audit_middleware.py` |
| Idempotency + 409 | `api/middleware/idempotency.py` | `test_api_idempotency_middleware.py` |
| RBAC (deny-by-default) | `api/middleware/rbac.py` | `test_api_rbac_middleware.py` |
| Rate limiting | `api/middleware/rate_limit.py` | `test_api_rate_limit_middleware.py` |
| Error model (RFC 7807) | `api/error_model.py` | `test_api_error_model.py` |
| DB transaction middleware | `api/middleware/db_tx.py` | `test_postgres_rls_and_audit_immutability.py` |
| OpenTelemetry tracing | `observability/tracing.py` | `test_observability_tracing.py` |
| Webhook signing/retry | `services/webhooks/*` | `test_webhook_signing.py`, `test_webhook_retry.py` |
| Postgres migrations (0001-0005) | `persistence/migrations/` | — |
| Deals route → Postgres | `api/routes/deals.py` | `test_api_deals_postgres.py` |
| Claims route → Postgres | `api/routes/claims.py` | `test_api_claims_postgres.py` |

**Middleware Stack:** RequestId → DBTx → Audit → OpenAPIValidation → RateLimit → RBAC → Idempotency

### 3.4 Phase 3 — Sanad Trust Framework ✅
| Component | Module | Tests |
|-----------|--------|-------|
| Source Tiers (6-level) | `services/sanad/source_tiers.py` | `test_sanad_methodology_v2_unit.py` |
| Dabt Scoring | `services/sanad/dabt.py` | `test_sanad_methodology_v2_unit.py` |
| Tawatur Independence | `services/sanad/tawatur.py` | `test_sanad_methodology_v2_unit.py` |
| Shudhudh Detection | `services/sanad/shudhudh.py` | `test_sanad_methodology_v2_unit.py` |
| I'lal Defects | `services/sanad/ilal.py` | `test_sanad_methodology_v2_unit.py` |
| COI Handling | `services/sanad/coi.py` | `test_sanad_methodology_v2_unit.py` |
| Grader v2 | `services/sanad/grader.py` | `test_sanad_methodology_v2_gdbs.py` |
| Sanad Integrity Validator | `validators/sanad_integrity.py` | `test_sanad_integrity.py` |
| EvidenceItem model | `models/evidence_item.py` | `test_evidence_item_model.py` |
| Sanad model | `models/sanad.py` | `test_sanad_model.py` |
| TransmissionNode model | `models/transmission_node.py` | `test_transmission_node_model.py` |
| Defect model | `models/defect.py` | `test_defect_model.py` |
| Claim service | `services/claims/service.py` | `test_claim_service.py` |
| Extraction service | `services/extraction/service.py` | `test_extraction_service.py` |
| Sanad API endpoints | `api/routes/sanad.py` | `test_api_sanad.py` |
| Defect API endpoints | `api/routes/defects.py` | `test_api_defects.py` |

### 3.5 Phase 4 — Deterministic Engines ✅
| Component | Module | Tests |
|-----------|--------|-------|
| Calc engine | `calc/engine.py` | `test_calc_reproducibility.py` |
| Calc-Sanad provenance | `models/calc_sanad.py` | `test_calc_sanad.py` |
| Formula registry | `calc/formulas/registry.py` | `test_calc_reproducibility.py` |
| Extraction gate validator | `validators/extraction_gate.py` | `test_extraction_gate.py` |
| ValueStruct types | `models/value_structs.py` | `test_value_structs.py` |
| Calc loop guardrail | `models/claim.py` | `test_calc_loop_guardrail.py` |

### 3.6 Phase 5 — Multi-Agent Debate ✅
| Component | Module | Tests |
|-----------|--------|-------|
| Debate orchestrator | `debate/orchestrator.py` | `test_debate_node_graph.py` |
| Stop conditions | `debate/stop_conditions.py` | `test_debate_stop_conditions.py` |
| Agent roles (5) | `debate/roles/*.py` | `test_debate_role_determinism.py` |
| Muḥāsabah gate | `debate/muhasabah_gate.py` | `test_muhasabah_gate.py` |
| Muḥāsabah validator | `validators/muhasabah.py` | `test_muhasabah_validator.py` |

### 3.7 Phase 6.1 — Deliverables ✅
| Component | Module | Tests |
|-----------|--------|-------|
| Screening Snapshot | `deliverables/screening.py` | `test_screening_snapshot.py` |
| IC Memo | `deliverables/memo.py` | `test_ic_memo.py` |
| PDF/DOCX export | `deliverables/export.py` | `test_export_formats.py` |
| Deliverable NFF validator | `validators/deliverable.py` | `test_deliverable_no_free_facts.py` |

### 3.8 Trust Validators ✅
| Component | Module | Tests |
|-----------|--------|-------|
| No-Free-Facts | `validators/no_free_facts.py` | `test_no_free_facts.py` |
| Audit event validator | `validators/audit_event_validator.py` | `test_audit_event_validator.py` |

### 3.9 API Endpoints Implemented ✅
- `/health` — Health check
- `/v1/tenants/me` — Tenant context
- `/v1/deals` — Deal CRUD
- `/v1/deals/{dealId}/claims` — Claims list
- `/v1/deals/{dealId}/truth-dashboard` — Truth dashboard
- `/v1/claims/{claimId}` — Claim detail
- `/v1/claims/{claimId}/sanad` — Sanad chain
- `/v1/deals/{dealId}/deliverables` — Deliverables
- `/v1/deals/{dealId}/runs` — Runs list
- `/v1/runs/{runId}` — Run detail
- `/v1/deals/{dealId}/debate` — Debate
- `/v1/debate/{debateId}` — Debate detail
- `/v1/deals/{dealId}/human-gates` — Human gates
- `/v1/deals/{dealId}/overrides` — Overrides
- `/v1/audit/events` — Audit query
- `/v1/webhooks` — Webhook management
- `/v1/deals/{dealId}/documents` — Documents list
- `/v1/documents/{docId}/ingest` — Ingestion trigger

### 3.10 Frontend UI — Fully Completed Components ✅
| Component | Status | Verification |
|-----------|--------|--------------|
| Next.js 14 foundation | ✅ COMPLETE | App router works, builds pass |
| Audit Events UI | ✅ COMPLETE | Lists/filters audit events |
| ErrorCallout component | ✅ COMPLETE | Displays RFC 7807 errors |
| HumanGate interface (OpenAPI-aligned) | ✅ COMPLETE | Approve/reject functional |
| Truth Dashboard table | ✅ COMPLETE | Shows claim grades |

---

## 4. E2E Blockers (Gate 3 Status)

**Source:** `docs/gates/gate_3_blocked_status.json`

| Blocker | Impact | Priority |
|---------|--------|----------|
| Document ingestion pipeline not integrated with claim extraction | Cannot process deals E2E | 🔴 CRITICAL |
| Claim extraction service not operational | No claims from documents | 🔴 CRITICAL |
| Sanad chain building not automated | Only manual test scripts exist | 🔴 CRITICAL |
| Debate execution not integrated with deliverable generation | No E2E pipeline | 🔴 CRITICAL |
| No `/v1/deals/{dealId}/runs` endpoint that executes full pipeline | Cannot trigger E2E run | 🔴 CRITICAL |

---

## 5. Frontend — Incomplete Components (Rebuild Required)

### 5.1 Components Needing Completion

| Screen/Component | Completed | Missing | Spec Reference |
|------------------|-----------|---------|----------------|
| Run Status UI | [x] Basic status display | [ ] Real-time WebSocket updates<br>[ ] Step-level error display<br>[ ] Resume/retry buttons | Frontend Guidelines §2.3 |
| DebateTranscript viewer | [x] Raw JSON display | [ ] Formatted message bubbles<br>[ ] Agent role colors<br>[ ] Citation links | Frontend Guidelines §2.6 |
| Deliverables page | [x] List view | [ ] Download buttons<br>[ ] Preview modal<br>[ ] Export format selector | Frontend Guidelines §2.8 |
| `/runs` list page | [x] Basic table | [ ] Pagination<br>[ ] Filter by status<br>[ ] Sort by date | Frontend Guidelines §2.3 |

### 5.2 Components Not Started

| Screen/Component | Status | Spec Reference |
|------------------|--------|----------------|
| Sanad Graph Visualization | ⏳ NOT STARTED | Frontend Guidelines §2.5 |
| Claim Detail Drawer (full) | ⏳ NOT STARTED | Frontend Guidelines §2.4 |
| Governance Dashboard | ⏳ NOT STARTED | Frontend Guidelines §2.9 |

---

## 6. Test Coverage Summary

| Category | Test Files | Status |
|----------|------------|--------|
| API Endpoints | 25+ | ✅ |
| Middleware | 8 | ✅ |
| Sanad Methodology | 4 | ✅ |
| Calc Engine | 5 | ✅ |
| Debate System | 5 | ✅ |
| Deliverables | 4 | ✅ |
| Validators | 8 | ✅ |
| Parsers | 5 | ✅ |
| Postgres Integration | 4 | ✅ |
| **Total** | **86+ files** | ✅ |

---

## 7. Verification Commands

```bash
# Verify baseline state
git fetch --all --tags
git log -1 --oneline
# Expected: fa2d4d2 fix(phase-7): correct postgres claim ABAC resolution...

# Run gates
make check          # or make.bat check on Windows
make postgres_integration  # Requires IDIS_REQUIRE_POSTGRES=1
```

---

## 8. Next Steps (Rebuild Focus)

See `docs/rebuild_pack/09_phase_gated_rebuild_tasks.md` for the full task plan.

**Priority 1 (E2E Unblock):**
1. Wire ingestion → extraction pipeline
2. Automate Sanad chain building
3. Wire debate → deliverables
4. Implement `/runs` full execution endpoint

**Priority 2 (GDBS Ready):**
1. Create synthetic benchmark datasets (GDBS-S, GDBS-F, GDBS-A)
2. Implement Gate 3 evaluation harness execution

**Priority 3 (Production Ready):**
1. Complete frontend missing screens
2. Add IaC/deployment artifacts
3. Implement enrichment connectors
