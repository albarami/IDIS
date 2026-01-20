# IDIS Go-Live Execution Plan & Phase Roadmap (v6.3)

**Version:** 6.3  
**Date:** 2026-01-07  
**Status:** Authoritative execution roadmap for IDIS production deployment  
**Audience:** Engineering, SRE, Security, Compliance, Product  
**Source:** Consolidated from all v6.3 specification documents

---

## 0) Purpose

This document is the **single authoritative execution plan** that maps IDIS v6.3 specifications into the IDIS Execution Protocol phases (0→7). It provides:

- Non-negotiable trust invariants that must hold at every phase
- Phase-by-phase deliverables, acceptance criteria, and required tests
- Detailed Phase 2 (API Gate) breakdown with completed and remaining work
- Cross-cutting controls staged plan (data residency, prompt registry, evaluation harness)
- Operational readiness and go-live checklist
- Risk register with mitigations

**Design principle:** No guessing. Every item traces to v6.3 docs. Every gate is testable.

---

## 1) Non-Negotiable Trust Invariants

These invariants MUST hold system-wide at all times. Violation of any invariant is a **SEV-1** incident.

### 1.1 No-Free-Facts (Hard Gate)

**Requirement:** Any factual statement in IC-bound outputs must reference:
- `claim_id` (with Sanad chain), OR
- `calc_id` (with Calc-Sanad lineage)

**Enforcement:**
- `NoFreeFacts` validator integrated into deliverables pipeline
- Tool wrapper/output parser rejects factual statements without references
- Muḥāsabah validator rejects empty `supported_claim_ids` when facts are present

**Source:** TDD §1.1, §4.4; API Contracts §2.1; Evaluation Harness §6.1

### 1.2 Muḥāsabah Gate (Hard Gate)

**Requirement:** Every agent output MUST include a valid `MuḥāsabahRecord`:
- `supported_claim_ids` non-empty for factual outputs
- `falsifiability_tests` present for recommendation-affecting claims
- `uncertainties` present for Āḥād corroboration and/or source grade < A

**Validator Rules (Normative):**
- Reject if confidence > 0.80 AND `uncertainties` empty
- Reject if confidence > 0.50 AND `falsifiability_tests` empty
- Reject if factual assertions present but `supported_claim_ids` empty

**Source:** TDD §4.4; Data Model §7.2; Evaluation Harness §6.2

### 1.3 Sanad Integrity (Hard Gate)

**Requirement:** Every material claim MUST carry a computed Claim Sanad Grade (A/B/C/D) with:
- Structured `transmission_chain`
- `corroboration_status` (NONE/AHAD_1/AHAD_2/MUTAWATIR)
- `defects` list with severity and cure protocols

**Grading Algorithm (Normative):**
1. `base_grade = MIN(source grades across chain)`
2. FATAL defect → D
3. MAJOR defects downgrade stepwise
4. MUTAWATIR with no MAJORs upgrades B→A or C→B
5. Cap at A; corroboration cannot cure FATAL/MAJOR

**Source:** TDD §5.1; Data Model §7.1; Requirements §3

### 1.4 Deterministic Numerics / Calc-Sanad (Hard Gate)

**Requirement:** All numeric metrics used for decisions must come from deterministic engines:
- Python Calc Service producing `Calc-Sanad`
- `formula_hash`, `code_version`, `reproducibility_hash` stored
- Reproducibility: same inputs → same hash

**Enforcement:**
- LLM may explain numbers but NEVER derive them
- Extraction confidence gate: block calcs if `extraction_confidence < 0.95` OR `dhabt_score < 0.90`

**Source:** TDD §1.1, §4.4; Implementation Plan §1; Tech Stack §1.4

### 1.5 Audit Immutability (Hard Gate)

**Requirement:** Audit events are append-only and cannot be modified.

**Coverage:** Every mutating operation (POST/PATCH/DELETE) MUST emit an `AuditEvent` with:
- `event_id`, `tenant_id`, `actor`, `resource`, `event_type`
- `request_id`, `idempotency_key`, `trace_id`
- `diff` (before/after refs), `timestamp`

**Source:** Audit Taxonomy §2-4; SLO §3.5

### 1.6 Tenant Isolation (Hard Gate)

**Requirement:** Everything is tenant-scoped:
- Request context requires `tenant_id`
- Storage is tenant-isolated (RLS, prefix IAM)
- Caches must be tenant-keyed
- No cross-tenant data access

**Violations:** 0 tolerated. Any violation is SEV-1.

**Source:** Security Threat Model §6; Data Residency §4; API Contracts §2.3

### 1.7 Fail-Closed Validators (Hard Gate)

**Requirement:** All validators, auth, tenant scoping, and audit logging MUST fail closed:
- If uncertain or missing data → reject / gate / require human verification
- Never "pass" by default

**Source:** TDD §10; Global Rules §3.3

---

## 2) Current State / Completed Gates

### Phase 0 — Foundation (COMPLETED)

**Approving Commit:** Phase 0 completed per repo history  
**Status:** ✅ DONE

**Deliverables Completed:**
- Mono-repo initialized with standard layout
- CI/CD pipeline (GitHub Actions): format, lint, typecheck, test
- Pre-commit hooks enabled
- FastAPI app with `/health` endpoint
- Pydantic v2 models baseline
- JSON schemas in `/schemas/`
- OpenAPI spec in `/openapi/`
- Documentation set in `/docs/`

### Phase 2.3 — Audit Middleware (COMPLETED)

**Approving Commit:** `c49ba01` (Codex APPROVE)  
**Status:** ✅ DONE

**Deliverables Completed:**
- `AuditMiddleware` implementation in `src/idis/api/middleware/audit.py`
- Audit event emission for all mutating endpoints
- Request context with `tenant_id`, `actor_id`, `request_id`
- Integration with FastAPI dependency injection
- Unit tests for audit event emission

### Phase 2.3.1 — Audit Remediation (COMPLETED)

**Approving Commit:** `c49ba01` (Codex APPROVE)  
**Status:** ✅ DONE

**Deliverables Completed:**
- Audit event schema validation against `audit_event.schema.json`
- Tenant isolation enforcement in audit queries
- Audit event taxonomy alignment per `IDIS_Audit_Event_Taxonomy_v6_3.md`

---

## 3) Phase Map (0→7) with Sub-Gates

### Phase 0 — Project Setup ✅ COMPLETED

**Objective:** Establish build-ready repo with enforced quality gates.

**Dependencies:** None

**Deliverables:**
| Module/File | Description | Status |
|-------------|-------------|--------|
| `src/idis/app.py` | FastAPI application | ✅ |
| `src/idis/api/routes/health.py` | Health endpoint | ✅ |
| `.github/workflows/ci.yml` | CI pipeline | ✅ |
| `.pre-commit-config.yaml` | Pre-commit hooks | ✅ |
| `pyproject.toml` | Project config | ✅ |
| `/docs/*.md` | v6.3 documentation | ✅ |
| `/schemas/*.json` | JSON schemas | ✅ |
| `/openapi/IDIS_OpenAPI_v6_3.yaml` | API contract | ✅ |

**Required Tests:**
- `test_api_health.py` — health endpoint returns version ✅
- `test_api_openapi_validation.py` — OpenAPI spec loads ✅

**Acceptance Criteria:**
- [x] `make check` passes
- [x] CI green on main
- [x] Health endpoint returns `{"status": "ok", "version": "6.3"}`

**Primary Doc References:** Implementation Plan §2 Phase 0; Tech Stack §1

---

### Phase 1 — Ingestion & Parsing (Weeks 2-4)

**Objective:** Ingest deal rooms into canonical Document + Span objects.

**Dependencies:** Phase 0

**Deliverables:**
| Module/File | Description | Status |
|-------------|-------------|--------|
| `src/idis/services/ingestion/` | Ingestion service | ⏳ Pending |
| `src/idis/models/document.py` | Document + DocumentSpan models | ⏳ Pending |
| `src/idis/models/deal_artifact.py` | DealArtifact model | ⏳ Pending |
| `src/idis/parsers/pdf.py` | PDF parser | ⏳ Pending |
| `src/idis/parsers/xlsx.py` | XLSX parser | ⏳ Pending |
| Object storage integration | S3/blob connector | ⏳ Pending |

**Required Tests:**
- `test_ingestion_pdf.py` — PDF parsing produces spans
- `test_ingestion_xlsx.py` — XLSX parsing produces cell spans
- `test_artifact_versioning.py` — SHA256 tracking works
- `test_audit_ingestion.py` — Ingestion emits audit events

**Acceptance Criteria:**
- [ ] Can ingest a deal room into canonical Document + Span objects
- [ ] 95% parse success on internal sample set
- [ ] Full audit log for ingestion actions
- [ ] SHA256 stored for each artifact
- [ ] Artifact versions tracked; duplicate detection works

**Trust Invariants Enforced:**
- Audit events for `document.created`, `document.ingestion.*`
- Tenant isolation on all storage

**Primary Doc References:** Implementation Plan §2 Phase 1; TDD §7.1; Data Model §3.2-3.3

---

### Phase 2 — API Gate + Claim Registry + Truth Dashboard v1 (Weeks 5-8)

**Objective:** Establish API infrastructure, claim extraction, and truth verification.

**Dependencies:** Phase 1

**Sub-Gates:**

#### Phase 2.1 — Core API Infrastructure
| Module/File | Description | Status |
|-------------|-------------|--------|
| `src/idis/api/middleware/tenant.py` | Tenant context middleware | ⏳ Pending |
| `src/idis/api/middleware/auth.py` | Auth middleware (JWT/API key) | ⏳ Pending |
| `src/idis/api/middleware/audit.py` | Audit middleware | ✅ Done |
| `src/idis/api/routes/deals.py` | Deal CRUD endpoints | ⏳ Pending |
| `src/idis/api/routes/documents.py` | Document endpoints | ⏳ Pending |

#### Phase 2.2 — Claim Registry
| Module/File | Description | Status |
|-------------|-------------|--------|
| `src/idis/models/claim.py` | Claim model | ⏳ Pending |
| `src/idis/services/extraction/` | Claim extraction service | ⏳ Pending |
| `src/idis/api/routes/claims.py` | Claims CRUD endpoints | ⏳ Pending |
| `src/idis/validators/no_free_facts.py` | No-Free-Facts validator | ⏳ Pending |

#### Phase 2.3 — Audit Infrastructure ✅ COMPLETED
| Module/File | Description | Status |
|-------------|-------------|--------|
| `src/idis/api/middleware/audit.py` | Audit middleware | ✅ Done |
| `src/idis/models/audit_event.py` | AuditEvent model | ✅ Done |
| `schemas/audit_event.schema.json` | Audit schema | ✅ Done |

**Approving Commit:** `c49ba01` (Codex APPROVE)

#### Phase 2.3.1 — Audit Remediation ✅ COMPLETED
| Module/File | Description | Status |
|-------------|-------------|--------|
| `src/idis/validators/audit_event_validator.py` | Audit event schema validation | ✅ Done |
| `tests/test_audit_event_validator.py` | Audit validator tests | ✅ Done |
| `tests/test_api_audit_middleware.py` | Audit middleware integration tests | ✅ Done |

**Deliverables:**
- Audit event schema validation against `audit_event.schema.json`
- Tenant isolation enforcement in audit queries
- Audit event taxonomy alignment per `IDIS_Audit_Event_Taxonomy_v6_3.md`

**Approving Commit:** `c49ba01` (Codex APPROVE)

#### Phase 2.4 — Truth Dashboard v1
| Module/File | Description | Status |
|-------------|-------------|--------|
| `src/idis/services/truth_dashboard/` | Verdict computation | ⏳ Pending |
| `src/idis/validators/matn.py` | Matn validators | ⏳ Pending |

**Required Tests:**
- `test_api_tenancy_auth.py` — Tenant isolation enforced ✅
- `test_claim_crud.py` — Claim CRUD works
- `test_no_free_facts.py` — Validator rejects unlinked facts
- `test_matn_validators.py` — Unit/time mismatch detection
- `test_audit_coverage.py` — All mutations emit events

**Acceptance Criteria:**
- [ ] Every extracted claim has claim_id, span refs, claim_type, typed value_struct
- [ ] Contradiction detection works on numeric fields across sources
- [ ] Truth Dashboard verdict states operational
- [ ] No-Free-Facts validator integrated
- [ ] 100% audit coverage for mutation endpoints

**Trust Invariants Enforced:**
- No-Free-Facts at claim registration
- Tenant isolation on all queries
- Audit events for `claim.*`, `deal.*`

**Primary Doc References:** Implementation Plan §2 Phase 2; TDD §4.1-4.3; API Contracts §5

---

### Phase 3 — Sanad Trust Framework + Defects (Weeks 9-12)

**Objective:** Implement evidence chain building, grading, and defect handling.

**Dependencies:** Phase 2

**Deliverables:**
| Module/File | Description | Status |
|-------------|-------------|--------|
| `src/idis/models/evidence_item.py` | EvidenceItem model | ⏳ Pending |
| `src/idis/models/sanad.py` | Sanad + TransmissionNode models | ⏳ Pending |
| `src/idis/models/defect.py` | Defect model | ⏳ Pending |
| `src/idis/services/sanad/` | Sanad service (grading, corroboration) | ⏳ Pending |
| `src/idis/services/defects/` | Defect service | ⏳ Pending |
| `src/idis/api/routes/sanad.py` | Sanad endpoints | ⏳ Pending |
| `src/idis/api/routes/defects.py` | Defect endpoints | ⏳ Pending |

**Required Tests:**
- `test_sanad_grade_algorithm.py` — Normative algorithm with worked examples
- `test_independence_rules.py` — Corroboration independence computation
- `test_defect_severity.py` — FATAL/MAJOR/MINOR rules
- `test_defect_cure_protocol.py` — Cure workflows

**Acceptance Criteria:**
- [ ] 100% of material claims have Sanad objects
- [ ] Grade algorithm unit-tested with worked examples from TDD §5.1
- [ ] Defect creation and waiver workflow operational
- [ ] Independence test uses `upstream_origin_id` + chain overlap

**Trust Invariants Enforced:**
- Sanad integrity for all material claims
- Audit events for `sanad.*`, `defect.*`

**Primary Doc References:** Implementation Plan §2 Phase 3; TDD §4.2-4.3, §5; Data Model §3.4

---

### Phase 4 — Deterministic Engines + Calc-Sanad (Weeks 13-16)

**Objective:** Implement deterministic calculation framework with full provenance.

**Dependencies:** Phase 3

**Deliverables:**
| Module/File | Description | Status |
|-------------|-------------|--------|
| `src/idis/calc/engine.py` | Calculation runner framework | ⏳ Pending |
| `src/idis/calc/formulas/` | Individual calc formulas | ⏳ Pending |
| `src/idis/models/calc_sanad.py` | CalcSanad model | ⏳ Pending |
| `src/idis/services/calc/` | Calc service | ⏳ Pending |
| `src/idis/api/routes/calcs.py` | Calc endpoints | ⏳ Pending |
| `src/idis/validators/extraction_gate.py` | Extraction confidence gate | ⏳ Pending |

**Required Tests:**
- `test_calc_reproducibility.py` — Same inputs → same hash
- `test_calc_sanad_provenance.py` — Inputs traced to claim_ids
- `test_extraction_gate.py` — Blocks calcs below confidence threshold
- `test_calc_grade_derivation.py` — calc_grade from min input grades

**Acceptance Criteria:**
- [ ] Calc outputs reproducible (same inputs → same hash)
- [ ] No LLM-generated arithmetic in deliverables
- [ ] Calcs traceable to claim_ids and source evidence
- [ ] Extraction gate blocks calcs if confidence < 0.95 or dhabt < 0.90

**Trust Invariants Enforced:**
- Deterministic numerics (Calc-Sanad)
- Audit events for `calc.*`

**Primary Doc References:** Implementation Plan §2 Phase 4; TDD §7.1 calc-engine-service; Data Model §3.5

---

### Phase 5 — Multi-Agent Debate + Muḥāsabah Gate (Weeks 17-22)

**Objective:** Implement LangGraph debate orchestration with trust gates.

**Dependencies:** Phase 4

**Deliverables:**
| Module/File | Description | Status |
|-------------|-------------|--------|
| `src/idis/orchestrator/debate/` | LangGraph debate orchestrator | ⏳ Pending |
| `src/idis/agents/` | Agent role implementations | ⏳ Pending |
| `src/idis/models/debate.py` | DebateRun, DebateMessage, DebateState | ⏳ Pending |
| `src/idis/models/muhasabah.py` | MuḥāsabahRecord model | ⏳ Pending |
| `src/idis/validators/muhasabah.py` | Muḥāsabah validator | ⏳ Pending |
| `src/idis/api/routes/debate.py` | Debate endpoints | ⏳ Pending |

**Required Tests:**
- `test_debate_node_graph.py` — Node order matches v6.3
- `test_debate_stop_conditions.py` — Priority order implemented
- `test_muhasabah_validator.py` — Rejects overconfident/unsupported outputs
- `test_utility_scoring.py` — Brier + penalties + materiality gate
- `test_stable_dissent.py` — Dissent preserved in deliverables

**Acceptance Criteria:**
- [ ] Debate runs end-to-end on sample deals
- [ ] Outputs blocked if Muḥāsabah missing or No-Free-Facts violated
- [ ] Stable dissent produces deliverables with dissent section
- [ ] Stop conditions in priority order (CRITICAL_DEFECT > MAX_ROUNDS > CONSENSUS > STABLE_DISSENT > EVIDENCE_EXHAUSTED)
- [ ] Max rounds = 5

**Trust Invariants Enforced:**
- Muḥāsabah gate (hard)
- No-Free-Facts (hard)
- Audit events for `debate.*`, `muhasabah.*`

**Primary Doc References:** Implementation Plan §2 Phase 5; TDD §6; Data Model §3.6

---

### Phase 6 — Deliverables Generator + Frontend v1 (Weeks 23-28)

**Objective:** Generate IC-ready outputs with full evidence linking.

**Dependencies:** Phase 5

**Deliverables:**
| Module/File | Description | Status |
|-------------|-------------|--------|
| `src/idis/services/deliverables/` | Deliverables generator | ⏳ Pending |
| `src/idis/templates/` | IC memo, snapshot templates | ⏳ Pending |
| `src/idis/api/routes/deliverables.py` | Deliverable endpoints | ⏳ Pending |
| `src/idis/validators/deliverable.py` | Deliverable validator (No-Free-Facts check) | ⏳ Pending |
| `/ui/` | Frontend application | ⏳ Pending |

**Required Tests:**
- `test_screening_snapshot.py` — All facts linked to claim_id/calc_id
- `test_ic_memo.py` — Evidence-linked sections
- `test_deliverable_no_free_facts.py` — Validator enforced at export
- `test_export_formats.py` — PDF/DOCX generation

**Acceptance Criteria:**
- [ ] Partner can review a deal with auditable evidence links
- [ ] Every fact in memo has claim_id/calc_id reference
- [ ] Exports include audit appendix (optional) for compliance
- [ ] Truth Dashboard UI operational
- [ ] Claim detail + Sanad chain view operational

**Trust Invariants Enforced:**
- No-Free-Facts at deliverable export (hard gate)
- Audit events for `deliverable.*`

**Primary Doc References:** Implementation Plan §2 Phase 6; TDD §7.1 deliverables-service; Requirements §8

---

### Phase 7 — Enterprise Hardening (Weeks 29-40)

**Objective:** Production-ready enterprise features and compliance.

**Dependencies:** Phase 6

**Deliverables:**
| Module/File | Description | Status |
|-------------|-------------|--------|
| SSO integration | Okta/Azure AD | ⏳ Pending |
| BYOK option | Customer-managed keys | ⏳ Pending |
| Data residency controls | Region pinning | ⏳ Pending |
| SOC2 readiness features | Access reviews, change logs | ⏳ Pending |
| Governance dashboards | Sanad coverage, defect rates | ⏳ Pending |
| CRM integrations | DealCloud/Affinity/Salesforce | ⏳ Pending |
| BYOL framework | Enrichment providers | ⏳ Pending |

**Required Tests:**
- `test_sso_integration.py` — SSO flow works
- `test_byok.py` — Customer keys used for encryption
- `test_data_residency.py` — Region pinning enforced
- `test_governance_metrics.py` — Dashboards populated

**Acceptance Criteria:**
- [ ] Security review passed
- [ ] Pilot fund onboarded with real deals
- [ ] Operational runbooks complete
- [ ] SOC2 controls evidence collection operational
- [ ] Drift monitoring active

**Trust Invariants Enforced:**
- All invariants from Phases 0-6
- Tenant isolation at enterprise scale
- Full audit trail for compliance

**Primary Doc References:** Implementation Plan §2 Phase 7; Security Threat Model; Data Residency; SLO/Runbooks

---

## 4) Immediate Next Work (Phase 2.5+)

Based on OpenAPI spec and API Contracts, the following items are the immediate next work after Phase 2.3/2.3.1 completion:

### Phase 2.5 — Idempotency-Key Behavior

**Source:** OpenAPI `IdempotencyKey` parameter; API Contracts §4.1

**Deliverables:**
- `src/idis/api/middleware/idempotency.py` — Idempotency middleware
- `src/idis/models/idempotency_record.py` — Storage for idempotency keys

**Behavior:**
- Same key + same endpoint + same actor + same payload hash → return stored response
- Same key but payload hash differs → return `409 Conflict`
- TTL for idempotency records (24-48 hours recommended)

**Required Tests:**
- `test_idempotency_replay.py` — Replay returns stored response
- `test_idempotency_conflict.py` — Different payload returns 409

### Phase 2.6 — Error Model Consistency

**Source:** OpenAPI `Error` schema; API Contracts §8

**Deliverables:**
- `src/idis/api/errors.py` — Standardized error handler
- Ensure all endpoints return consistent `Error` schema:
  ```json
  {"code": "...", "message": "...", "details": {}, "request_id": "..."}
  ```

**Required Tests:**
- `test_error_model.py` — All error responses match schema

### Phase 2.7 — Rate Limiting

**Source:** API Contracts §4.3

**Deliverables:**
- `src/idis/api/middleware/rate_limit.py` — Rate limiting middleware
- Configuration for:
  - User endpoints: 600 req/min/tenant (burst 2x)
  - Integration endpoints: 1200 req/min/tenant (burst 2x)

**Required Tests:**
- `test_rate_limiting.py` — Limits enforced, 429 returned

### Phase 2.8 — Webhook Signing + Retry Primitives

**Source:** OpenAPI `/v1/webhooks`; API Contracts §6

**Deliverables:**
- `src/idis/services/webhooks/` — Webhook service
- HMAC signature generation with shared secret
- Retry: 10 attempts over 24 hours with exponential backoff

**Required Tests:**
- `test_webhook_signing.py` — HMAC signature correct
- `test_webhook_retry.py` — Retry logic works

### Phase 2.9 — Audit Query Surface

**Source:** OpenAPI `/v1/audit/events`; Audit Taxonomy §5

**Deliverables:**
- `src/idis/api/routes/audit.py` — Audit query endpoint
- Filters: time range, deal_id, event_type
- Pagination with cursor

**Required Tests:**
- `test_audit_query.py` — Query filters work
- `test_audit_tenant_isolation.py` — Only tenant's events returned

---

## 5) Cross-Cutting Controls Staged Plan

### 5.1 Data Residency Controls (Staged)

**Phase A (Immediate — can start parallel):**
- Add `data_region` field to Tenant model
- Enforce region in request context
- Document region options

**Phase B (Before Phase 7):**
- Object store prefix per region
- Database RLS includes region checks
- Cross-region operation logging

**Source:** Data Residency §3-4

### 5.2 Prompt Registry (Staged)

**Phase A (Before Phase 5):**
- Create `prompts/` directory structure
- Implement `prompts/<prompt_id>/<version>/prompt.md` + `metadata.json`
- Create registry JSON files: `registry.dev.json`, `registry.staging.json`, `registry.prod.json`

**Phase B (Before Phase 6):**
- Runtime prompt loader with version pinning
- Prompt version logged in every output artifact
- Rollback mechanism (atomic pointer flip)

**Phase C (Before Phase 7):**
- Promotion pipeline integrated into CI/CD
- Gate requirements by risk class enforced
- Audit events for prompt lifecycle

**Source:** Prompt Registry §2-11

### 5.3 Evaluation Harness (Staged)

**Phase A (Before Phase 3):**
- Create `benchmarks/` directory with immutable versions
- Define GDBS-S (20 deals) structure
- Build test harness CLI skeleton

**Phase B (Before Phase 5):**
- Implement GDBS-S end-to-end tests
- Integrate Gate 0 (Unit & Schema) into CI
- Integrate Gate 1 (Structural Trust) into CI

**Phase C (Before Phase 6):**
- Implement GDBS-F (100 deals)
- Implement GDBS-A (30 adversarial deals)
- Integrate Gate 2 (Core Quality) and Gate 3 (Full Regression)

**Phase D (Before Phase 7):**
- Gate 4 (Human Review) process established
- Metrics dashboards for ongoing quality monitoring
- Re-baselining process documented

**Source:** Evaluation Harness §3-8

### 5.4 Release Gate Classification (Hard vs Soft)

| Gate | Type | Failure Impact | Metrics | Mandatory Phase |
|------|------|----------------|---------|-----------------|
| **Gate 0** | HARD | Block merge to main | Schema valid, lint pass, type check pass, unit tests pass | Phase 0+ (all merges) |
| **Gate 1** | HARD | Block staging deploy | No-Free-Facts = 0 violations, Muḥāsabah ≥ 98%, Audit coverage = 100%, Tenant isolation = 0 violations | Phase 2+ |
| **Gate 2** | HARD | Block preprod deploy | Sanad coverage ≥ 95%, Defect recall ≥ 90%, Calc reproducibility ≥ 99.9% | Phase 4+ |
| **Gate 3** | SOFT | Flag for review, deploy allowed with approval | GDBS-F pass rate ≥ 95%, Debate completion ≥ 98%, Utility score regression ≤ 5% | Phase 6+ |
| **Gate 4** | SOFT | Flag for review, deploy allowed with approval | Human review sign-off on 10-deal sample, No critical defects in sample | Phase 7 (go-live) |

**Hard Gate Behavior:**
- Automated enforcement in CI/CD pipeline
- No manual override without security team approval
- Failure blocks deployment to target environment

**Soft Gate Behavior:**
- Automated check with results logged
- Manual override allowed with documented justification
- Engineering lead sign-off required for override

**Source:** Evaluation Harness §8; Implementation Plan §4

---

## 6) Operational Readiness / Go-Live Checklist

Derived from SLO/Runbooks §10 and Security Threat Model §11.

### 6.1 Monitoring & Alerting

- [ ] Golden dashboards exist:
  - API availability/latency
  - Ingestion throughput + error rates
  - Queue depth/backlog
  - Claim registry writes + validator rejects
  - Sanad grading distribution drift
  - Calc success rate + reproducibility checks
  - Debate completion rate + max-round stops
  - Deliverable generation success rate
  - Audit event ingestion lag + coverage
  - Integration health

- [ ] Paging alerts configured:
  - SEV-1: Tenant isolation, No-Free-Facts violation, audit tampering
  - SEV-2: Service outages, calc reproducibility failures

### 6.2 Backup & Recovery

- [ ] Daily full backups + continuous WAL for Postgres
- [ ] Daily snapshots for Graph DB
- [ ] Object store versioning + lifecycle policies
- [ ] Quarterly restore drills completed (minimum once before go-live)
- [ ] RPO/RTO validated:
  - Postgres: RPO 15 min, RTO 2 hours
  - Graph DB: RPO 1 hour, RTO 4 hours
  - Object Store: RPO 0, RTO 4 hours

### 6.3 DR Drills

- [ ] Failover simulation completed at least once
- [ ] Tenant isolation validated post-failover
- [ ] Audit event continuity validated
- [ ] Run resumption behavior validated

### 6.4 Incident Response

- [ ] Runbooks published for all 10 core scenarios (RB-01 through RB-10)
- [ ] On-call rotation established
- [ ] SEV levels defined and communicated
- [ ] Communication cadence documented

### 6.5 Security Readiness

- [ ] RBAC + tenant isolation enforced on all endpoints
- [ ] Encryption at rest + in transit enabled
- [ ] Secrets stored and rotated (Vault/KMS)
- [ ] Object store access scoped and audited
- [ ] Audit event taxonomy implemented
- [ ] Incident runbooks tested
- [ ] SAST + dependency scanning in CI
- [ ] Pen test completed (before enterprise launch)

### 6.6 Trust Invariant Verification

- [ ] No-Free-Facts validator enforced at deliverable export
- [ ] Muḥāsabah gate enforced in debate pipeline
- [ ] Audit coverage = 100% for all mutating endpoints
- [ ] Calc-Sanad reproducibility ≥ 99.9%
- [ ] Tenant isolation tests pass (0 violations)

### 6.7 SLO Readiness

- [ ] API availability SLO dashboards operational (target 99.9%)
- [ ] Core pipeline availability SLO dashboards operational (target 99.5%)
- [ ] Latency SLOs measured and within targets
- [ ] Error budget policy documented

---

## 7) Risk Register + Mitigations

### 7.1 Trust-Invariant Regressions

**Risk:** Prompt or validator changes cause No-Free-Facts or Muḥāsabah violations.

**Likelihood:** Medium  
**Impact:** Critical (SEV-1)

**Mitigations:**
- Prompt registry with semver and approval gates
- Evaluation harness regression tests (GDBS-S minimum)
- Rollback mechanism (atomic pointer flip)
- Alerting on validator rejection rate spikes

### 7.2 Tenant Isolation Leakage

**Risk:** Bug allows cross-tenant data access.

**Likelihood:** Low (if RLS enforced)  
**Impact:** Critical (SEV-1)

**Mitigations:**
- Row Level Security (RLS) in Postgres
- Tenant ID in request context, validated at middleware
- Object store prefix IAM scoping
- Tenant isolation test suite (automated, run in CI)
- Cache keying includes tenant_id

### 7.3 Audit Tampering

**Risk:** Audit logs modified or deleted.

**Likelihood:** Low  
**Impact:** Critical (compliance failure)

**Mitigations:**
- Append-only storage (immutable log table)
- Optional WORM storage class
- Audit events for audit access attempts
- No delete permissions on audit tables (even for admins)

### 7.4 Deterministic Calc Drift

**Risk:** Calc outputs differ for same inputs due to environment drift.

**Likelihood:** Medium  
**Impact:** High (trust compromise)

**Mitigations:**
- `formula_hash`, `code_version`, `reproducibility_hash` stored
- Pinned dependencies and containerized execution
- Reproducibility checks in CI (run calcs twice, compare hashes)
- Alert on reproducibility failure rate > 0.1%

### 7.5 Prompt Regressions

**Risk:** Prompt changes degrade output quality or violate trust gates.

**Likelihood:** Medium  
**Impact:** High

**Mitigations:**
- Prompt registry with versioning
- Required gates by risk class (Gate 1/2/3/4)
- Human review for IC memo template changes
- Rollback within 24 hours capability

### 7.6 Extraction Errors Contaminate Calcs

**Risk:** Low-confidence extractions used in deterministic calculations.

**Likelihood:** Medium  
**Impact:** High (incorrect IC outputs)

**Mitigations:**
- Extraction confidence gate (≥ 0.95)
- dhabt_score gate (≥ 0.90)
- Human verification workflow for below-threshold claims
- Claims flagged with `requires_human_verification`

### 7.7 Agents Collude or Converge Prematurely

**Risk:** Debate agents agree without proper evidence examination.

**Likelihood:** Low-Medium  
**Impact:** Medium (reduced trust value)

**Mitigations:**
- Randomized role assignment
- Arbiter validation of challenges
- Dissent preservation in stable_dissent
- Utility scoring with penalties for frivolous agreement
- Anti-gaming tests in evaluation harness

### 7.8 Evidence Gaps Stall Pipeline

**Risk:** Missing data prevents pipeline completion.

**Likelihood:** High (expected for some deals)  
**Impact:** Medium (workflow delay)

**Mitigations:**
- "Missing info request" output supported
- Partial deliverables with explicit unknowns
- EVIDENCE_EXHAUSTED stop condition
- Defect creation with cure protocol REQUEST_SOURCE

---

## 8) Phase Gate Summary Table

| Phase | Name | Key Deliverable | Trust Gates | Status |
|-------|------|-----------------|-------------|--------|
| 0 | Foundation | CI/CD, Health endpoint | Basic tenant header | ✅ Done |
| 1 | Ingestion | Document + Span objects | Audit events | ⏳ Pending |
| 2 | API Gate + Claims | Claim Registry, Truth Dashboard | No-Free-Facts, Audit 100% | 🔄 In Progress |
| 2.3 | Audit Middleware | AuditMiddleware | Audit coverage | ✅ Done |
| 2.5+ | API Hardening | Idempotency, Rate limits | Error model | ⏳ Pending |
| 3 | Sanad Framework | Sanad + Defects | Sanad integrity | ⏳ Pending |
| 4 | Calc Engines | Calc-Sanad | Deterministic numerics | ⏳ Pending |
| 5 | Debate | LangGraph + Muḥāsabah | Muḥāsabah gate | ⏳ Pending |
| 6 | Deliverables | IC Memo, Frontend | No-Free-Facts at export | ⏳ Pending |
| 7 | Enterprise | SSO, BYOK, SOC2 | All invariants | ⏳ Pending |

---

## 9) Document References

| Document | Key Sections Used |
|----------|-------------------|
| `01_IDIS_TDD_v6_3.md` | §1 Invariants, §4 Contracts, §5 Algorithms, §6 Debate, §7 Services |
| `02_IDIS_Data_Model_Schema_v6_3.md` | §3 Relational Schema, §5 JSON Schemas, §7 Validations |
| `04_IDIS_Requirements_Backlog_v6_3.md` | §0 Milestones, §1-12 Epics, §13 DoD |
| `06_IDIS_Implementation_Plan_v6_3.md` | §2 Phase Plan, §4 Metrics, §5 Risks |
| `07_IDIS_Tech_Stack_v6_3.md` | §1-6 Stack Recommendations |
| `IDIS_OpenAPI_v6_3.yaml` | All paths and schemas |
| `IDIS_API_and_Integration_Contracts_v6_3.md` | §2-8 API Rules |
| `IDIS_Audit_Event_Taxonomy_v6_3.md` | §2-6 Event Types and Requirements |
| `IDIS_Evaluation_Harness_and_Release_Gates_v6_3.md` | §2-11 Gates and Harness |
| `IDIS_Prompt_Registry_and_Model_Policy_v6_3.md` | §2-11 Registry and Policy |
| `IDIS_Data_Residency_and_Compliance_Model_v6_3.md` | §3-10 Residency and Controls |
| `IDIS_Security_Threat_Model_v6_3.md` | §1-13 Threats and Mitigations |
| `IDIS_SLO_SLA_Runbooks_v6_3.md` | §2-10 SLOs and Runbooks |

---

## 10) Revision History

| Date | Version | Author | Changes |
|------|---------|--------|---------|
| 2026-01-07 | 1.0 | Cascade | Initial creation from v6.3 docs consolidation |
| 2026-01-07 | 1.1 | Cascade | Added Phase 2.3.1 as explicit sub-gate; added hard/soft gate classification table (§5.4); linked approving commits to completed phases |
