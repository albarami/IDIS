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

### 1.3 Muḥāsabah Gate
**Source:** TDD §4.4

Agent outputs require valid MuḥāsabahRecord with claim/calc refs.
- **Validator:** `src/idis/validators/muhasabah.py`
- **Test:** `tests/test_muhasabah_validator.py`

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
Repo, CI/CD, pre-commit, FastAPI

### Phase 1 — Ingestion (Weeks 2-4) ⏳
- **1.1** Storage primitives, Document/Span models
- **1.2** PDF/XLSX parsers
- **Exit:** 95% parse success, SHA256 tracking

### Phase 2 — API Gate (Weeks 5-8) ✅ MOSTLY DONE
- **2.1-2.5** ✅ Complete
- **2.6** Error model standardization ⏳
- **2.7** Rate limiting ⏳
- **2.8** Webhooks ⏳

### Phase 3 — Sanad Framework (Weeks 9-12) ⏳
- **3.1** Sanad/Defect models
- **3.2** Grader, independence checker
- **Exit:** 100% claims have Sanad, grade algo tested

### Phase 4 — Calc-Sanad (Weeks 13-16) ⏳
- Calc engine, Calc-Sanad model
- Extraction confidence gate
- **Exit:** ≥99.9% reproducibility

### Phase 5 — Debate + Muḥāsabah (Weeks 17-22) ⏳
- LangGraph orchestration
- Agent roles, stop conditions
- Muḥāsabah integration

### Phase 6 — Deliverables (Weeks 23-28) ⏳
- Screening Snapshot, IC Memo
- Frontend Truth Dashboard

### Phase 7 — Enterprise Hardening (Weeks 29-40) ⏳
- SSO, BYOK, data residency
- SOC2 readiness

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

### 4.4 Prompt Registry
- [ ] Version pinning, rollback mechanism
- [ ] CI gates by risk class

### 4.5 Evaluation Harness
- [ ] GDBS-S/F/A benchmarks
- [ ] Gate 0-4 integrated

### 4.6 Tenant Isolation Suite
- [ ] RLS tests, cache keying tests

---

## 5) Next Up (Immediate Queue)

After Codex approval of this doc:

**Phase 3.1 — Ingestion Gate**
- Storage primitives
- Document/Span models
- Deal/doc scaffolding per Data Model §3.2-3.3

---

## 6) Release Gates (Hard vs Soft)

| Gate | Type | Metrics |
|------|------|---------|
| Gate 0 | HARD | Schema, lint, type, unit tests |
| Gate 1 | HARD | No-Free-Facts=0, Muḥāsabah≥98%, audit=100% |
| Gate 2 | HARD | Sanad≥95%, defect recall≥90%, calc repro≥99.9% |
| Gate 3 | SOFT | GDBS-F pass≥95%, debate completion≥98% |
| Gate 4 | SOFT | Human review 10-deal sample |

---

## 7) Revision History

| Date | Version | Changes |
|------|---------|---------|
| 2026-01-07 | 1.0 | Initial creation |
