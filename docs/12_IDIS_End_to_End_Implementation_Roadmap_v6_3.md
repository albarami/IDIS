# IDIS End-to-End Implementation Roadmap — v6.3

**Version:** 6.3 | **Date:** 2026-01-07 | **Status:** Task-Level Implementation Roadmap

---

## How to Use This Roadmap

This document converts the v6.3 spec into a **task-level plan** through go-live. Each phase contains:
- Scope/Objective
- Deliverables with acceptance criteria
- Controls and invariants enforced
- Key modules/files impacted
- Testing requirements
- Exit criteria (objective checklist)

**Derived from v6.3 normative docs:**
- `06_IDIS_Implementation_Plan_v6_3.md` — Phased plan + constraints
- `04_IDIS_Requirements_Backlog_v6_3.md` — Milestones + epics + acceptance
- `IDIS_API_and_Integration_Contracts_v6_3.md` — Idempotency, RBAC, rate limits
- `IDIS_Security_Threat_Model_v6_3.md` — RBAC/ABAC deny-by-default
- `07_IDIS_Tech_Stack_v6_3.md` — Postgres/Object Storage/OTel MUST items
- `05_IDIS_Backend_Structure_v6_3.md` — Modular monolith + API surface
- `IDIS_Evaluation_Harness_and_Release_Gates_v6_3.md` — Release gates
- `IDIS_Data_Residency_and_Compliance_Model_v6_3.md` — Tenant isolation
- `IDIS_SLO_SLA_Runbooks_v6_3.md` — Monitoring, DR, runbooks

---

## 1) Current State + Gap Snapshot

### 1.1 Already Implemented (Phase 0 → 2.5)

| Phase | Commit | Outcome |
|-------|--------|---------|
| 0 | `5c1412e` | Repo, CI/CD (GitHub Actions), pre-commit, FastAPI `/health` |
| 2.1 | `33e8ef8` | Tenant auth via API key, `/v1/tenants/me` endpoint |
| 2.2 | `953fe44` | OpenAPI request validation middleware |
| 2.3 | `9919a21` | Audit middleware + JSONL sink (fail-closed) |
| 2.3.1 | `c49ba01` | Audit remediation (Codex approved) |
| 2.4 | `1666b48` | Idempotency-Key middleware + SQLite store |
| 2.5 | `257d1fd` | Actor identity in TenantContext, fail-closed on store.put |

**Current middleware stack:** RequestId → Audit → OpenAPIValidation → Idempotency

**Tests:** 15 files, 245 passing

### 1.2 Gaps Remaining Before API Gate Completion

| Gap | v6.3 Reference | Status |
|-----|----------------|--------|
| **RBAC/ABAC deny-by-default** | Security §4.2 | ❌ Not implemented |
| **Idempotency 409 conflict on payload mismatch** | API Contracts §4.1 | ❌ Not implemented |
| **Rate limits per tenant** (600 req/min user, 1200 req/min integration) | API Contracts §4.3 | ❌ Not implemented |
| **Postgres as canonical store** (MUST) | Tech Stack §1.3 | ❌ Using SQLite for idempotency |
| **Object Storage abstraction** (MUST) | Tech Stack §1.3 | ❌ Not implemented |
| **OpenTelemetry baseline** (MUST) | Tech Stack §1.5 | ❌ Not implemented |
| **Error model standardization** | API Contracts §8 | ⚠️ Partial |
| **Webhook HMAC signing foundation** | API Contracts §6.1 | ❌ Not implemented |

---

## 2) Phase Crosswalk (Naming Disambiguation)

| Execution Phase | v6.3 Implementation Plan | Backlog Milestone | Weeks |
|-----------------|-------------------------|-------------------|-------|
| Phase 0 | Phase 0 — Project Setup | M0 Foundations | 1 |
| Phase 1 | Phase 1 — Ingestion & Parsing | M0 Foundations | 2-4 |
| Phase 2 | Phase 2 — Claim Registry + Truth Dashboard v1 | M1 Trust Core MVP | 5-8 |
| Phase 3 | Phase 3 — Sanad Trust Framework + Defects | M1 Trust Core MVP | 9-12 |
| Phase 4 | Phase 4 — Deterministic Engines + Calc-Sanad | M2 Engines + Dashboard | 13-16 |
| Phase 5 | Phase 5 — Multi-Agent Debate + Muḥāsabah | M3 Debate + Deliverables | 17-22 |
| Phase 6 | Phase 6 — Deliverables Generator + Frontend v1 | M3 Debate + Deliverables | 23-28 |
| Phase 7 | Phase 7 — Enterprise Hardening | M4 Integrations + Hardening | 29-40 |

**Note:** Our "Phase 2" (API Gate) maps to v6.3's trust foundation work, not the claim registry phase.

---

## 3) Work Breakdown Structure (WBS)

### Phase 0 — Project Setup ✅ COMPLETE

**Scope:** Establish repo foundation with CI/CD and quality gates.

**Deliverables:**
- [x] Mono-repo initialized
- [x] CI/CD (GitHub Actions: lint, type, test)
- [x] Pre-commit hooks (ruff format, ruff check)
- [x] FastAPI app with `/health` endpoint
- [x] OpenAPI spec loader

**Exit Criteria:** ✅ All met (commit `5c1412e`)

---

### Phase 1 — Ingestion & Parsing ⏳ PLANNED

**Scope:** Ingest deal room artifacts into canonical Document + Span objects.

#### Task 1.1: Storage Primitives
| Deliverable | Module | Stage |
|-------------|--------|-------|
| Object storage abstraction | `src/idis/storage/object_store.py` | Stage A: interface; Stage B: S3 |
| Document model | `src/idis/models/document.py` | Stage A |
| DocumentSpan model | `src/idis/models/document_span.py` | Stage A |

**Acceptance:** Upload/download/versioning works; SHA256 tracked

#### Task 1.2: Document Parsing
| Deliverable | Module | Stage |
|-------------|--------|-------|
| PDF parser | `src/idis/parsers/pdf.py` | Stage A |
| XLSX parser | `src/idis/parsers/xlsx.py` | Stage A |
| Parser registry | `src/idis/parsers/registry.py` | Stage A |

**Acceptance:** 95% parse success on sample set; spans have stable locators

**Controls Enforced:**
- Tenant isolation: all artifacts scoped by `tenant_id`
- Audit: `document.created`, `document.ingestion.completed` events

**Testing:**
- `test_object_store.py` — upload/download/versioning
- `test_pdf_parser.py`, `test_xlsx_parser.py` — parse coverage

**Exit Criteria:**
- [ ] 95%+ parse success on internal sample set
- [ ] SHA256 tracked for all artifacts
- [ ] Audit events emitted for ingestion
- [ ] Gate 0 passes (lint, type, tests)

---

### Phase 2 — API Gate ⚠️ IN PROGRESS

**Scope:** Establish production-ready API infrastructure.

#### Task 2.1-2.5 ✅ COMPLETE
See §1.1 for completed work.

#### Task 2.6: RBAC/ABAC Enforcement
| Deliverable | Module | Stage |
|-------------|--------|-------|
| RBAC middleware | `src/idis/api/middleware/rbac.py` | Stage A |
| Policy checker | `src/idis/api/auth.py` (extend) | Stage A |
| Role definitions | `src/idis/api/roles.py` | Stage A |

**Acceptance:**
- Deny-by-default: unauthenticated/unauthorized → 401/403
- Roles: ANALYST, PARTNER, IC_MEMBER, ADMIN, AUDITOR, INTEGRATION_SERVICE
- `policy_check(actor, action, resource, tenant_id)` enforced

**Reference:** Security §4.2

#### Task 2.7: Idempotency 409 Conflict
| Deliverable | Module | Stage |
|-------------|--------|-------|
| Payload hash validation | `src/idis/api/middleware/idempotency.py` | Stage A |
| 409 response on mismatch | `src/idis/api/middleware/idempotency.py` | Stage A |

**Acceptance:**
- Same key + different payload hash → 409 Conflict
- Test: `test_idempotency_conflict_on_payload_mismatch`

**Reference:** API Contracts §4.1

#### Task 2.8: Rate Limiting
| Deliverable | Module | Stage |
|-------------|--------|-------|
| Rate limit middleware | `src/idis/api/middleware/rate_limit.py` | Stage A: in-memory; Stage B: Redis |
| Tenant + role limits | Configuration | Stage A |

**Acceptance:**
- User endpoints: 600 req/min/tenant
- Integration endpoints: 1200 req/min/tenant
- 429 response when exceeded

**Reference:** API Contracts §4.3

#### Task 2.9: Postgres Migration
| Deliverable | Module | Stage |
|-------------|--------|-------|
| Database abstraction | `src/idis/persistence/db.py` | Stage A |
| Alembic migrations | `src/idis/persistence/migrations/` | Stage A |
| Idempotency store → Postgres | `src/idis/idempotency/store.py` | Stage B |

**Acceptance:**
- PostgreSQL as canonical store (MUST per Tech Stack §1.3)
- RLS enforced for tenant isolation

#### Task 2.10: OpenTelemetry Baseline
| Deliverable | Module | Stage |
|-------------|--------|-------|
| OTel instrumentation | `src/idis/observability/tracing.py` | Stage A |
| Request tracing | Middleware integration | Stage A |

**Acceptance:**
- Traces propagated with request_id
- Span context available in logs

**Reference:** Tech Stack §1.5 (MUST)

**Controls Enforced:**
- Tenant isolation at all layers
- Audit coverage 100% for mutations
- Fail-closed on auth/validation failures

**Exit Criteria:**
- [ ] RBAC deny-by-default enforced
- [ ] Idempotency 409 on payload mismatch
- [ ] Rate limits enforced per tenant
- [ ] Postgres as primary store
- [ ] OTel traces propagated
- [ ] Gate 0 + Gate 1 pass

---

### Phase 3 — Sanad Trust Framework ⚠️ IN PROGRESS

**Scope:** Implement evidence chain building, grading, and defect handling.

#### Task 3.1: Claim Registry ⏳ PLANNED
| Deliverable | Module |
|-------------|--------|
| Claim model | `src/idis/models/claim.py` |
| Claim service | `src/idis/services/claims/service.py` |
| Claim API | `src/idis/api/routes/claims.py` |

**Acceptance:**
- Claims have `claim_id`, `claim_type`, `value_struct`, `source_refs`
- No-Free-Facts validator enforced at creation

#### Task 3.2: Sanad Models ⏳ PLANNED
| Deliverable | Module |
|-------------|--------|
| EvidenceItem model | `src/idis/models/evidence_item.py` |
| Sanad model | `src/idis/models/sanad.py` |
| TransmissionNode model | `src/idis/models/transmission_node.py` |
| Defect model | `src/idis/models/defect.py` |

#### Task 3.3: Sanad Methodology v2 Enhancements ✅ COMPLETE

**Implemented (2026-01-09):** Full Sanad v2 methodology with six enhancements.

| Deliverable | Module | Status |
|-------------|--------|--------|
| Source Tiers (6-level) | `src/idis/services/sanad/source_tiers.py` | ✅ |
| Dabt Scoring | `src/idis/services/sanad/dabt.py` | ✅ |
| Tawatur Independence | `src/idis/services/sanad/tawatur.py` | ✅ |
| Shudhudh Detection | `src/idis/services/sanad/shudhudh.py` | ✅ |
| I'lal Defects | `src/idis/services/sanad/ilal.py` | ✅ |
| COI Handling | `src/idis/services/sanad/coi.py` | ✅ |
| Grader v2 | `src/idis/services/sanad/grader.py` | ✅ |
| Defects Interface | `src/idis/services/sanad/defects.py` | ✅ |
| Methodology Doc | `docs/IDIS_Sanad_Methodology_v2.md` | ✅ |

**Acceptance (Phase 3.3):**
- [x] 6-level source tiers with deterministic assignment
- [x] Dabt multi-dimensional scoring (fail-closed)
- [x] Tawatur independence + collusion detection
- [x] Shudhudh reconciliation-first anomalies
- [x] I'lal hidden defects (VERSION_DRIFT, CHAIN_BREAK, CHAIN_GRAFTING, CHRONOLOGY_IMPOSSIBLE)
- [x] COI handling with cure protocols
- [x] Integrated grader_v2 with all enhancements

**Testing (Phase 3.3):**
- `tests/test_sanad_methodology_v2_unit.py` — 40+ unit tests
- `tests/test_sanad_methodology_v2_gdbs.py` — GDBS-FULL adversarial deals

**Controls Enforced:**
- Sanad integrity validator (deterministic)
- Defect severity rules (FATAL/MAJOR/MINOR)
- Fail-closed on all components

**Exit Criteria:**
- [ ] 100% claims have Sanad objects
- [x] Grade algorithm unit-tested with worked examples
- [ ] Defect waiver workflow operational
- [ ] Gate 2 (Sanad≥95%, defect recall≥90%)

---

### Phase 4 — Deterministic Engines + Calc-Sanad ⏳ PLANNED

**Scope:** Implement deterministic calculation framework with full provenance.

#### Task 4.1: Calc Engine Framework
| Deliverable | Module |
|-------------|--------|
| Calc engine | `src/idis/calc/engine.py` |
| Calc-Sanad model | `src/idis/models/calc_sanad.py` |
| Formula registry | `src/idis/calc/formulas/registry.py` |

#### Task 4.2: Extraction Confidence Gate
| Deliverable | Module |
|-------------|--------|
| Extraction gate validator | `src/idis/validators/extraction_gate.py` |

**Acceptance:**
- Same inputs → same hash (≥99.9% reproducibility)
- Calc outputs traced to `claim_ids`
- Extraction confidence < 0.95 blocks calcs

**Controls Enforced:**
- Deterministic numerics (no LLM arithmetic)
- Calc-Sanad: formula_hash, code_version, reproducibility_hash

**Testing:**
- `test_calc_reproducibility.py` — hash consistency
- `test_calc_sanad.py` — input tracing
- `test_extraction_gate.py` — confidence blocking

**Exit Criteria:**
- [ ] ≥99.9% reproducibility
- [ ] No LLM-generated arithmetic in deliverables
- [ ] Calcs traceable to claim_ids
- [ ] Gate 2 (calc repro≥99.9%)

---

### Phase 5 — Multi-Agent Debate + Muḥāsabah ⏳ PLANNED

**Scope:** Implement LangGraph debate orchestration with trust gates.

#### Task 5.1: LangGraph Orchestration
| Deliverable | Module |
|-------------|--------|
| Debate orchestrator | `src/idis/debate/orchestrator.py` |
| Agent roles | `src/idis/debate/roles/*.py` |
| Stop conditions | `src/idis/debate/stop_conditions.py` |

**Roles:** Advocate, Sanad Breaker, Contradiction Finder, Risk Officer, Arbiter

#### Task 5.2: Muḥāsabah Integration
| Deliverable | Module |
|-------------|--------|
| Muḥāsabah record model | `src/idis/models/muhasabah_record.py` |
| Muḥāsabah gate (fail-closed) | `src/idis/debate/muhasabah_gate.py` |

**Acceptance:**
- Outputs blocked if Muḥāsabah missing or No-Free-Facts violated
- Stable dissent preserved when evidence-backed

**Controls Enforced:**
- Muḥāsabah gate (HARD, FAIL-CLOSED)
- No-Free-Facts enforcement at output boundary

**Exit Criteria:**
- [ ] Debate runs end-to-end on sample deals
- [ ] Muḥāsabah gate rejects invalid outputs
- [ ] Gate 3 (debate completion≥98%, Muḥāsabah≥98%)

---

### Phase 6 — Deliverables Generator + Frontend ⏳ PLANNED

**Scope:** Generate IC-ready outputs with evidence linking.

#### Task 6.1: Deliverables Generator
| Deliverable | Module |
|-------------|--------|
| Screening Snapshot | `src/idis/deliverables/screening.py` |
| IC Memo | `src/idis/deliverables/memo.py` |
| PDF/DOCX export | `src/idis/deliverables/export.py` |

#### Task 6.2: Frontend (Backend Contracts)
| Deliverable | API |
|-------------|-----|
| Truth Dashboard API | `/v1/deals/{id}/truth-dashboard` |
| Claim Detail API | `/v1/claims/{id}` |
| Sanad Chain API | `/v1/claims/{id}/sanad` |

**Acceptance:**
- Every fact in memo has `claim_id`/`calc_id` reference
- Exports include audit appendix

**Exit Criteria:**
- [ ] Deliverables generator produces valid PDFs
- [ ] Every fact linked to claim/calc
- [ ] Gate 3 (GDBS-F pass≥95%)

---

### Phase 7 — Enterprise Hardening ⏳ PLANNED

**Scope:** Production readiness, security, and compliance.

#### Task 7.1: SSO Integration
| Deliverable | Module |
|-------------|--------|
| OIDC/SAML integration | `src/idis/api/auth_sso.py` |
| JWT validation | `src/idis/api/auth.py` (extend) |

#### Task 7.2: Prompt Registry
| Deliverable | Module |
|-------------|--------|
| Prompt registry | `src/idis/services/prompts/registry.py` |
| Version promotion/rollback | `src/idis/services/prompts/versioning.py` |
| Audit events | `prompt.version.promoted`, `prompt.version.rolledback` |

#### Task 7.3: Evaluation Harness
| Deliverable | Module |
|-------------|--------|
| GDBS benchmark runner | `src/idis/evaluation/benchmarks/` |
| Gate 0-4 integration | `src/idis/evaluation/harness.py` |

#### Task 7.4: SLO/SLA Monitoring
| Deliverable | Module |
|-------------|--------|
| SLO dashboards | `src/idis/monitoring/slo_dashboard.py` |
| Alert rules | `src/idis/monitoring/alerts.py` |

**Exit Criteria:**
- [ ] SSO integration working
- [ ] Prompt registry with audited promotion/rollback
- [ ] GDBS benchmarks passing
- [ ] SLO dashboards operational
- [ ] Gate 4 (human review 10-deal sample)

---

## 4) Next Up (Phase 2.6+ Candidates)

Ordered by priority and v6.3 requirement strength:

1. **Task 2.7: Idempotency 409 Conflict** — API Contracts §4.1 requires payload hash validation and 409 response on mismatch
2. **Task 2.6: RBAC/ABAC Enforcement** — Security §4.2 requires deny-by-default; minimum roles ANALYST/PARTNER/ADMIN
3. **Task 2.8: Rate Limiting** — API Contracts §4.3 specifies 600/1200 req/min/tenant limits
4. **Task 2.10: OpenTelemetry Baseline** — Tech Stack §1.5 marks OTel as MUST
5. **Task 2.9: Postgres Migration** — Tech Stack §1.3 marks Postgres as MUST

---

## 5) Go-Live Readiness Checklist

**Source:** `IDIS_SLO_SLA_Runbooks_v6_3.md`

### Monitoring & Alerting
- [ ] SLO dashboards (availability 99.9%, latency p95)
- [ ] Paging alerts (SEV-1: tenant isolation, No-Free-Facts, audit failure)
- [ ] Error budget tracking

### Backup & DR
- [ ] Daily backups, tested restores
- [ ] DR drills completed
- [ ] RPO/RTO documented and tested

### Runbooks
- [ ] Incident playbooks published
- [ ] On-call rotation established
- [ ] Escalation paths defined

### Audit Continuity
- [ ] Audit sink failover tested
- [ ] Immutability verified (append-only)
- [ ] Retention policy enforced

### Security
- [ ] Penetration test completed
- [ ] Security review passed
- [ ] SOC2 controls documented

### Prompt Registry
- [ ] Version pinning active
- [ ] Rollback mechanism tested
- [ ] Promotion audit events verified

### Evaluation Harness
- [ ] GDBS-S/F/A benchmarks passing
- [ ] Gate 0-4 integrated in CI
- [ ] Regression detection active

---

## 6) Open Decisions (Not Yet Grounded)

| Decision | Status | Notes |
|----------|--------|-------|
| Cloud provider (AWS/GCP/Azure) | Open | Security doc notes explicit flexibility |
| Graph DB choice (Neo4j/Neptune) | Open | Tech Stack recommends but doesn't mandate |
| Temporal vs Celery | Open | Tech Stack lists both as acceptable |
| SSO provider (Okta vs Azure AD) | Open | Security doc lists both |

---

## 7) Revision History

| Date | Version | Changes |
|------|---------|---------|
| 2026-01-07 | 1.0 | Initial creation |
