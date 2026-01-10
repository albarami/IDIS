# IDIS v6.3 Traceability Matrix

**Version:** 6.3 | **Date:** 2026-01-07 | **Status:** Authoritative

---

## 1) Purpose

Maps v6.3 requirements/invariants → implementation → tests → phase gates.
Ensures deterministic, phase-disciplined delivery aligned to v6.3 spec.

---

## 2) Requirement Traceability Table

| ID | Requirement / Invariant | Spec Reference | Implementation | Tests | Status | Phase | Notes |
|----|------------------------|----------------|----------------|-------|--------|-------|-------|
| **INV-01** | Tenant Isolation (Leakage Rule) | Security §6, API §3 | `api/auth.py`, `middleware/idempotency.py` | `test_api_tenancy_auth.py`, `test_api_idempotency_middleware.py` | DONE | 2.1, 2.5 | Actor scoping added 2.5 |
| **INV-02** | Audit Coverage (Mutations) | Audit Taxonomy §2 | `middleware/audit.py`, `audit/sink.py` | `test_api_audit_middleware.py`, `test_audit_event_validator.py` | DONE | 2.3 | JSONL sink; DB=Stage B |
| **INV-03** | No-Free-Facts Enforcement | TDD §1.1, §3.2 | `validators/no_free_facts.py` | `test_no_free_facts.py` | DONE | 0 | Validator complete |
| **INV-04** | Sanad Integrity Validator | TDD §4.2-4.3, §5 | `validators/sanad_integrity.py` | `test_sanad_integrity.py` | DONE | 0 | Validator; services=Phase 3 |
| **INV-05** | Sanad Defect Rules | TDD §4.3, Data Model §3.4 | `validators/sanad_integrity.py` | `test_sanad_integrity.py` | DONE | 0 | FATAL/MAJOR/MINOR rules |
| **INV-06** | Muḥāsabah Gate | TDD §4.4, Eval §2.1 | `validators/muhasabah.py` | `test_muhasabah.py`, `test_muhasabah_validator.py` | DONE | 0 | Validator complete |
| **INV-07** | Calc-Sanad Determinism | TDD §1.1, §6 | `calc/engine.py`, `models/calc_sanad.py` | `test_calc_reproducibility.py`, `test_calc_sanad.py`, `test_postgres_rls_and_audit_immutability.py` | DONE | 4.1 | DN-001: material-aware calc_grade, FK constraints, RLS tests |
| **INV-08** | Request ID Propagation | API §4 | `middleware/request_id.py` | `test_api_openapi_validation.py` | DONE | 2.1 | |
| **INV-09** | OpenAPI Validation | API §5 | `middleware/openapi_validate.py` | `test_api_openapi_validation.py` | DONE | 2.2 | |
| **INV-10** | Idempotency-Key | API §4.3 | `middleware/idempotency.py`, `idempotency/store.py` | `test_api_idempotency_middleware.py` | DONE | 2.4-2.5 | Tenant+actor scoped |
| **INV-11** | Fail-Closed (store.put) | API §4.3 | `middleware/idempotency.py` | `test_api_idempotency_middleware.py::TestStorePutFailure` | DONE | 2.5 | Returns 500 |
| **INV-12** | Prompt Registry + Rollback | Prompt Registry §4 | `services/prompts/registry.py`, `services/prompts/versioning.py` | `test_prompt_registry.py`, `test_prompt_rollback.py` | PLANNED | 7 | Exit: Gate 4; audit events required |
| **INV-13** | Evaluation Harness | Eval Harness §3-7 | `evaluation/harness.py`, `evaluation/benchmarks/` | `test_evaluation_harness.py`, `test_gdbs_runner.py` | PLANNED | 7 | Exit: Gate 4; GDBS-S/F/A |
| **INV-14** | Frontend Evidence-First UI | Frontend §2-3 | — | — | PLANNED | 6 | Backend deps only |
| **INV-15** | SLO/SLA Compliance | SLO/SLA §3 | `monitoring/slo_dashboard.py`, `monitoring/alerts.py` | `test_slo_metrics.py`, `test_alert_rules.py` | PLANNED | 7 | Exit: Gate 4; SLO dashboards |

---

## 3) Trust Invariant Detail

### 3.1 Tenant Isolation (INV-01)

| Aspect | Requirement | Implementation | Test |
|--------|-------------|----------------|------|
| Auth scoping | Every request has tenant_id | `TenantContext` in `auth.py` | `test_api_tenancy_auth.py` |
| Actor identity | Idempotency scoped by actor | `actor_id` in `TenantContext` | `test_api_idempotency_middleware.py::TestActorIsolation` |
| Cache isolation | Caches keyed by tenant | Planned (Stage B) | Planned |
| RLS | DB queries filtered by tenant | Planned (Phase 3) | Planned |

### 3.2 Audit Coverage (INV-02)

| Aspect | Requirement | Implementation | Test |
|--------|-------------|----------------|------|
| 100% mutation coverage | All POST/PATCH/DELETE emit event | `AuditMiddleware` | `test_audit_emitted_on_valid_request` |
| Append-only | No event modification | `JsonlFileAuditSink.write` | `test_audit_event_validator.py` |
| Fail-closed | Sink failure → 500 | `AuditMiddleware` | `test_fail_closed_on_sink_write_failure` |
| Schema validation | Events match schema | `validate_audit_event()` | `test_audit_event_validator.py` |

### 3.3 No-Free-Facts (INV-03)

| Aspect | Requirement | Implementation | Test |
|--------|-------------|----------------|------|
| Claim reference | Facts need claim_id | `validate_no_free_facts()` | `test_no_free_facts.py` |
| Calc reference | Numbers need calc_id | `validate_no_free_facts()` | `test_no_free_facts.py` |
| SUBJECTIVE label | Unreferenced OK if labeled | `validate_no_free_facts()` | `test_no_free_facts.py` |

### 3.4 Sanad Integrity (INV-04, INV-05)

| Aspect | Requirement | Implementation | Test |
|--------|-------------|----------------|------|
| Grade computation | A/B/C/D per algorithm | `validate_sanad_integrity()` | `test_sanad_integrity.py` |
| FATAL → D | BROKEN_CHAIN, etc. | `validate_sanad_integrity()` | `test_fatal_defect_forces_grade_d` |
| Corroboration | AHAD/MUTAWATIR rules | `validate_sanad_integrity()` | `test_sanad_integrity.py` |
| Independence | upstream_origin_id check | Planned (Phase 3) | Planned |

### 3.5 Muḥāsabah Gate (INV-06)

| Aspect | Requirement | Implementation | Test |
|--------|-------------|----------------|------|
| Record required | All agent outputs | `validate_muhasabah()` | `test_muhasabah_validator.py` |
| Claim refs | supported_claim_ids | `MuhasabahValidator` | `test_muhasabah.py` |
| Uncertainty | uncertainty_register | `MuhasabahValidator` | `test_muhasabah.py` |
| Reject rules | Missing record → REJECT | `MuhasabahValidator` | `test_reject_on_missing_record` |

### 3.6 Calc-Sanad (INV-07)

| Aspect | Requirement | Implementation | Test |
|--------|-------------|----------------|------|
| Reproducibility | Same input → same hash | `calc/engine.py` | `test_calc_reproducibility.py` |
| Formula hash | Tracked in Calc-Sanad | `models/calc_sanad.py` | `test_calc_sanad.py` |
| Input tracing | Links to claim_ids | `models/calc_sanad.py` | `test_calc_sanad.py` |
| **Exit Gate** | Gate 2: repro≥99.9% | Eval Harness integration | `test_evaluation_harness.py` |

---

## 4) Test Coverage Matrix

| Module | Test File | Tests | Coverage |
|--------|-----------|-------|----------|
| `api/auth.py` | `test_api_tenancy_auth.py` | 10 | Auth, tenant context |
| `api/middleware/audit.py` | `test_api_audit_middleware.py` | 10 | Emission, fail-closed |
| `api/middleware/idempotency.py` | `test_api_idempotency_middleware.py` | 40+ | Replay, collision, actor |
| `api/middleware/openapi_validate.py` | `test_api_openapi_validation.py` | 20+ | Validation, errors |
| `validators/no_free_facts.py` | `test_no_free_facts.py` | 15+ | All validation rules |
| `validators/sanad_integrity.py` | `test_sanad_integrity.py` | 28 | Grade, defects |
| `validators/muhasabah.py` | `test_muhasabah.py`, `test_muhasabah_validator.py` | 27 | Record validation |
| `validators/audit_event_validator.py` | `test_audit_event_validator.py` | 10+ | Schema validation |
| `schemas/registry.py` | `test_schema_registry.py` | 10+ | Schema loading |

---

## 5) Phase Gate Mapping

| Phase | Gate | Requirements Covered |
|-------|------|---------------------|
| 0 | Gate 0 | INV-03, INV-04, INV-05, INV-06 (validators) |
| 2.1 | Gate 0 | INV-01 (auth), INV-08 (request_id) |
| 2.2 | Gate 0 | INV-09 (OpenAPI validation) |
| 2.3 | Gate 1 | INV-02 (audit coverage) |
| 2.4-2.5 | Gate 1 | INV-10, INV-11 (idempotency) |
| 3 | Gate 2 | INV-04, INV-05 (Sanad services) |
| 4 | Gate 2 | INV-07 (Calc-Sanad engine) |
| 5 | Gate 3 | INV-06 (Muḥāsabah integration) |
| 6 | Gate 3 | INV-14 (Frontend contracts) |
| 7 | Gate 4 | INV-12, INV-13, INV-15 (hardening) |

---

## 6) Stage A vs Stage B Controls

| Component | Stage A (Current) | Stage B (Later) |
|-----------|-------------------|-----------------|
| Audit Sink | JSONL file | Immutable DB/object store |
| Auth | API key | JWT + SSO |
| Idempotency Store | SQLite | Postgres |
| Rate Limiting | None | Redis-backed |
| RBAC | Tenant-level | Role-level (ANALYST/PARTNER) |

---

## 7) Revision History

| Date | Version | Changes |
|------|---------|---------|
| 2026-01-07 | 1.0 | Initial creation |
| 2026-01-07 | 1.1 | Added planned modules/tests for INV-07, INV-12, INV-13, INV-15; added gate references |
