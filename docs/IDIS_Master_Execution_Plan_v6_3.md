# IDIS Master Execution Plan — v6.3

**Version:** 6.3 | **Date:** 2026-01-07 | **Status:** Authoritative

---

## 1) Non-Negotiable Invariants

### 1.1 No-Free-Facts
**Source:** TDD §1.1, §3.2

All factual statements MUST reference `claim_id` or `calc_id`. Unlinked facts = REJECT.
- **Validator:** `src/idis/validators/no_free_facts.py`
- **Test:** `tests/test_no_free_facts.py`

### 1.2 Sanad Integrity
**Source:** TDD §4.2-4.3, §5

Every material claim carries Sanad Grade (A/B/C/D) + defects.
- FATAL defects → Grade D
- **Validator:** `src/idis/validators/sanad_integrity.py`
- **Test:** `tests/test_sanad_integrity.py`

### 1.3 Muḥāsabah Gate (HARD GATE, FAIL-CLOSED)
**Source:** TDD §4.4; Evaluation Harness §2.1

Agent outputs require valid MuḥāsabahRecord with claim/calc refs.
- **Behavior:** FAIL-CLOSED — missing or invalid MuḥāsabahRecord = output REJECTED (not passed through)
- **Reject Rules:**
  - Missing MuḥāsabahRecord → REJECT
  - Empty `supported_claim_ids` for factual output → REJECT
  - Confidence > threshold without evidence → REJECT
- **Validator:** `src/idis/validators/muhasabah.py`
- **Test:** `tests/test_muhasabah_validator.py`
- **Gate:** Gate 1 (≥98% pass rate required)

### 1.4 Deterministic Numerics
**Source:** TDD §1.1

All numbers from deterministic engines, NOT LLM. Calc-Sanad required.

### 1.5 Audit Coverage
**Source:** Audit Taxonomy §2

100% mutation coverage, append-only, tenant-bound.
- **Middleware:** `src/idis/api/middleware/audit.py`
- **Test:** `tests/test_api_audit_middleware.py`

### 1.6 Tenant Isolation
**Source:** Security §6

Zero cross-tenant leakage. Scoped caches/idempotency.
- **Test:** `tests/test_api_tenancy_auth.py`, `tests/test_api_idempotency_middleware.py`

---

## 1b) Key Reference Documents

| Document | Scope |
|----------|-------|
| `IDIS_Data_Architecture_v3_1.md` | 21-source API selection, licensing matrix (GREEN/YELLOW/RED), BYOL model, Phase 1→2A→2B cost structure |
| `IDIS_API_Phased_Integration_Plan_v3_1.md` | Standalone licensing matrix + phased integration timeline |
| `IDIS_Enrichment_Connector_Framework_v0_1.md` | Adapter contract, rights-class gating, caching policy (TTL/no-store), testing strategy |
| `IDIS_Local_Dev_Databases_Runbook_v6_3.md` | Local Postgres + Neo4j setup, env vars, docker compose, bootstrap + migrations |

---

## 2) State of the Repo

### 2.1 Completed (Phase 0 → 2.5)

| Phase | Commit | Description |
|-------|--------|-------------|
| 0 | `5c1412e` | Repo setup, CI/CD, FastAPI |
| 2.1 | `33e8ef8` | Tenant auth |
| 2.2 | `953fe44` | OpenAPI validation |
| 2.3 | `9919a21` | Audit middleware |
| 2.3.1 | `c49ba01` | Audit remediation |
| 2.4 | `1666b48` | Idempotency middleware |
| 2.5 | `257d1fd` | Actor identity + fail-closed |

### 2.2 Middleware Stack
1. `RequestIdMiddleware`
2. `AuditMiddleware`
3. `OpenAPIValidationMiddleware`
4. `IdempotencyMiddleware`

### 2.3 Tests: 15 files, 245 passing

### 2.4 Staged for Later
- Audit: JSONL → DB/object store
- Auth: API key → JWT + SSO
- Rate limiting: Not yet implemented

---

## 3) Phase Plan

### Phase 0 ✅ DONE
**Backlog:** M0 Foundations  
**Deliverables:** Repo, CI/CD, pre-commit, FastAPI  
**Exit Gate:** Gate 0 (schema, lint, type, unit tests)  
**Acceptance:** CI passes, `/health` returns version

### Phase 1 — Ingestion (Weeks 2-4) ⏳
**Backlog:** Epic 1 (Ingestion & Deal Object Store)  
**Deliverables:**
- **1.1** Storage primitives, Document/Span models
- **1.2** PDF/XLSX parsers  
**Exit Gate:** Gate 0 + audit events for ingestion  
**Acceptance:** 95% parse success, SHA256 tracking, `document.created` events emitted  
**Go-Live Blocker:** Cannot proceed to Phase 3 without ingestion working

### Phase 2 — API Gate (Weeks 5-8) ✅ MOSTLY DONE
**Backlog:** M0 Foundations (auth, logging)  
**Deliverables:**
- **2.1-2.5** ✅ Complete (auth, OpenAPI, audit, idempotency)
- **2.6** Error model standardization ⏳
- **2.7** Rate limiting ⏳
- **2.8** Webhooks ⏳  
**Exit Gate:** Gate 0 + Gate 1 (audit=100%)  
**Acceptance:** All /v1 mutations audited, tenant isolation enforced  
**Go-Live Blocker:** API infra required for all subsequent phases

### Phase 3 — Sanad Framework (Weeks 9-12) ⏳
**Backlog:** Epic 3 (Sanad Trust Framework)  
**Deliverables:**
- **3.1** Sanad/Defect models
- **3.2** Grader, independence checker  
**Exit Gate:** Gate 2 (Sanad≥95%, defect recall≥90%)  
**Acceptance:** 100% claims have Sanad, grade algo unit-tested with worked examples  
**Go-Live Blocker:** No IC-ready outputs without Sanad coverage

### Phase 4 — Calc-Sanad (Weeks 13-16) ⏳
**Backlog:** Epic 5 (Deterministic Engines)  
**Deliverables:**
- Calc engine framework (`src/idis/calc/engine.py`)
- Calc-Sanad model (`src/idis/models/calc_sanad.py`)
- Extraction confidence gate  
**Required Tests:**
- `tests/test_calc_reproducibility.py` — same inputs → same hash
- `tests/test_calc_sanad.py` — inputs traced to claim_ids  
**Exit Gate:** Gate 2 (calc repro≥99.9%)  
**Acceptance:** Calc outputs reproducible, no LLM arithmetic in deliverables  
**Go-Live Blocker:** Numbers in IC outputs must have Calc-Sanad

### Phase 5 — Debate + Muḥāsabah (Weeks 17-22) ⏳
**Backlog:** M3 (Multi-Agent Debate)  
**Deliverables:**
- LangGraph orchestration
- Agent roles, stop conditions
- Muḥāsabah integration (fail-closed)  
**Exit Gate:** Gate 3 (debate completion≥98%, Muḥāsabah≥98%)  
**Acceptance:** Outputs blocked if Muḥāsabah missing or No-Free-Facts violated  
**Go-Live Blocker:** Debate required for IC memo generation

### Phase 6 — Deliverables (Weeks 23-28) ⏳
**Backlog:** M3 (Deliverables Generator)  
**Deliverables:**
- Screening Snapshot, IC Memo
- Frontend Truth Dashboard  
**Exit Gate:** Gate 3 (GDBS-F pass≥95%)  
**Acceptance:** Every fact in memo has claim_id/calc_id reference  
**Go-Live Blocker:** Deliverables generator required for production

### Phase 7 — Enterprise Hardening (Weeks 29-40) ⏳
**Backlog:** M4 (Integrations + Governance + Security)  
**Deliverables:**
- SSO, BYOK, data residency
- SOC2 readiness
- Prompt registry with audited promotion/rollback
- **7.A Neo4j Wiring** — Neo4j driver + tenant-safe repository + Cypher queries + Postgres↔Neo4j consistency checks (see `IDIS_Local_Dev_Databases_Runbook_v6_3.md` for local setup)
- **7.B Enrichment Connector Framework** — adapter contracts + cache policy + rights gating (GREEN→YELLOW→RED rollout) per `IDIS_Enrichment_Connector_Framework_v0_1.md` and `IDIS_API_Phased_Integration_Plan_v3_1.md`  
**Exit Gate:** Gate 4 (human review 10-deal sample)  
**Acceptance:** Security review passed, pilot fund onboarded  
**Go-Live Blocker:** All Gate 0-4 passed

**Graph DB Decision (Closed):** Neo4j Aura is the baseline graph store. Neptune/Memgraph are acceptable alternatives if cloud-provider constraints dictate, but the codebase assumes a Bolt-protocol-compatible graph DB. Driver abstraction in `persistence/` must support swap without service-layer changes.

---

## 4) Go-Live Checklist

### 4.1 Monitoring
- [ ] SLO dashboards (availability, latency)
- [ ] Paging alerts (SEV-1: tenant isolation, No-Free-Facts)

### 4.2 Backup/Recovery
- [ ] Daily backups, tested restores
- [ ] DR drills completed

### 4.3 Runbooks
- [ ] Incident playbooks published
- [ ] On-call rotation established

### 4.4 Prompt Registry (Audited Promotion/Rollback)
- [ ] Version pinning, rollback mechanism
- [ ] CI gates by risk class (A/B/C per Prompt Registry §4)
- [ ] **Audit Events Required:**
  - `prompt.version.promoted` — records version, risk_class, approver, gate_results
  - `prompt.version.rolledback` — records version, reason, actor, rollback_target
  - `prompt.version.retired` — records version, reason, actor
- [ ] **Evidence Artifacts:** promotion gate results stored in object store with SHA256

### 4.5 Evaluation Harness
- [ ] GDBS-S/F/A benchmarks
- [ ] Gate 0-4 integrated

### 4.6 Tenant Isolation Suite
- [ ] RLS tests, cache keying tests

---

## 5) Backlog → Phase → Acceptance Mapping

**Source:** `04_IDIS_Requirements_Backlog_v6_3.md`, `06_IDIS_Implementation_Plan_v6_3.md`

| Backlog Epic/Milestone | Phase | Acceptance Criteria | Go-Live Blocker |
|------------------------|-------|---------------------|----------------|
| M0 Foundations | 0, 2 | CI passes, auth enforced, audit 100% | API infra required |
| Epic 1: Ingestion | 1 | 95% parse, SHA256, audit events | Cannot claim without docs |
| Epic 2: Claim Registry | 2-3 | Claims have claim_id + span refs | No facts without claims |
| Epic 3: Sanad Framework | 3 | 100% Sanad coverage, grade algo tested | No IC outputs without Sanad |
| Epic 4: Truth Dashboard | 3-4 | Verdicts linked to claim_id + evidence | Analyst review requires dashboard |
| Epic 5: Calc Engines | 4 | ≥99.9% reproducibility, Calc-Sanad | Numbers require deterministic provenance |
| M3: Debate + Deliverables | 5-6 | Debate completion ≥98%, Muḥāsabah ≥98% | IC memo requires debate |
| M4: Hardening | 7 | Security review passed, Gate 4 passed | Production launch blocked |

---

## 6) Next Up (Immediate Queue)

After Codex approval of this doc:

**Phase 3.1 — Ingestion Gate**
- Storage primitives
- Document/Span models
- Deal/doc scaffolding per Data Model §3.2-3.3

---

## 7) Release Gates (Hard vs Soft)

| Gate | Type | Metrics |
|------|------|---------|
| Gate 0 | HARD | Schema, lint, type, unit tests |
| Gate 1 | HARD | No-Free-Facts=0, Muḥāsabah≥98%, audit=100% |
| Gate 2 | HARD | Sanad≥95%, defect recall≥90%, calc repro≥99.9% |
| Gate 3 | SOFT | GDBS-F pass≥95%, debate completion≥98% |
| Gate 4 | SOFT | Human review 10-deal sample |

---

## 8) Revision History

| Date | Version | Changes |
|------|---------|---------|
| 2026-01-07 | 1.0 | Initial creation |
| 2026-01-07 | 1.1 | Added backlog mapping, per-phase exit gates, audited prompt registry, Muḥāsabah fail-closed, Calc-Sanad tests |
| 2026-02-07 | 1.2 | Added §1b Key Reference Documents (Data Architecture v3.1, API Phased Plan, Enrichment Connector Framework, Local Dev Runbook). Assigned Neo4j wiring (7.A) and enrichment connectors (7.B) to Phase 7. Closed Graph DB open decision (Neo4j Aura baseline). |
