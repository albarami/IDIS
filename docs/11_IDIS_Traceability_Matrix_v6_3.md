# IDIS Traceability Matrix (v6.3)

**Version:** 6.3  
**Date:** 2026-01-07  
**Status:** Authoritative requirements traceability for IDIS  
**Audience:** Engineering, QA, Security, Compliance, Audit  
**Source:** Consolidated from all v6.3 specification documents

---

## 0) Purpose

This document provides a **traceability matrix** that maps IDIS v6.3 requirements and trust invariants to:

- Source documentation (exact doc + section)
- Enforcing component (middleware/validator/service/module)
- Tests that prove compliance (test file + test names)
- Phase gate where the requirement becomes mandatory
- Evidence artifact (what gets persisted/audited)

**Design principle:** Every requirement is traceable. No gaps. Auditors can verify coverage.

---

## 1) Trust Invariants Traceability

### 1.1 Tenant Isolation

| Attribute | Value |
|-----------|-------|
| **Requirement ID** | TI-001 |
| **Requirement** | No cross-tenant querying; out-of-scope refs treated as unknown |
| **Source Doc** | Security Threat Model §6; Data Residency §4; API Contracts §2.3 |
| **Source Section** | "Multi-Tenant Isolation Model", "Tenant-level Data Region" |
| **Enforcing Component** | `src/idis/api/middleware/tenant.py` — TenantContextMiddleware |
| **Secondary Enforcement** | PostgreSQL RLS policies; Object store prefix IAM; Cache tenant-keying |
| **Tests** | `tests/test_api_tenancy_auth.py::test_tenant_isolation` |
| | `tests/test_tenant_rls.py::test_cross_tenant_query_blocked` |
| | `tests/test_cache_tenant_keying.py::test_no_cross_tenant_cache` |
| **Phase Gate** | Phase 0 (basic header); Phase 2 (full enforcement); Phase 7 (enterprise RLS) |
| **Evidence Artifact** | `AuditEvent.tenant_id` on every event; `tenant.isolation.violation` event (CRITICAL) |
| **Violation Severity** | SEV-1 |

---

### 1.2 Audit Immutability + Coverage

| Attribute | Value |
|-----------|-------|
| **Requirement ID** | AI-001 |
| **Requirement** | Audit coverage 100% for mutation endpoints; audit logs are append-only |
| **Source Doc** | Audit Taxonomy §2; SLO §3.5; Security Threat Model §9 |
| **Source Section** | "Core Requirements (Non-negotiable)", "Trust-Invariant SLOs" |
| **Enforcing Component** | `src/idis/api/middleware/audit.py` — AuditMiddleware |
| **Secondary Enforcement** | Append-only database table; WORM storage (enterprise) |
| **Tests** | `tests/test_audit_coverage.py::test_all_mutations_emit_audit` |
| | `tests/test_audit_immutability.py::test_audit_no_update` |
| | `tests/test_audit_immutability.py::test_audit_no_delete` |
| **Phase Gate** | Phase 2.3 (mandatory) |
| **Evidence Artifact** | `AuditEvent` records in append-only store; `sanad.integrity.failed` event if tampering detected |
| **Violation Severity** | SEV-1 |

---

### 1.3 No-Free-Facts Enforcement

| Attribute | Value |
|-----------|-------|
| **Requirement ID** | NFF-001 |
| **Requirement** | Any factual statement in IC-bound outputs must reference claim_id or calc_id |
| **Source Doc** | TDD §1.1, §4.4; API Contracts §2.1; Evaluation Harness §6.1 |
| **Source Section** | "Non-Negotiable Invariants", "No-Free-Facts Validator" |
| **Enforcing Component** | `src/idis/validators/no_free_facts.py` — NoFreeFacts validator |
| **Secondary Enforcement** | Deliverable export gate; Muḥāsabah validator check |
| **Tests** | `tests/test_no_free_facts.py::test_rejects_unlinked_fact` |
| | `tests/test_no_free_facts.py::test_accepts_linked_fact` |
| | `tests/test_no_free_facts.py::test_subjective_allowed` |
| | `tests/test_deliverable_validation.py::test_export_blocked_without_refs` |
| **Phase Gate** | Phase 2 (claim registration); Phase 6 (deliverable export) |
| **Evidence Artifact** | `muhasabah.rejected` event with violation details; deliverable validation logs |
| **Violation Severity** | SEV-1 |

---

### 1.4 Muḥāsabah Deterministic Validator

| Attribute | Value |
|-----------|-------|
| **Requirement ID** | MUH-001 |
| **Requirement** | Every agent output must include valid MuḥāsabahRecord; validator rejects invalid |
| **Source Doc** | TDD §4.4; Data Model §7.2; Evaluation Harness §6.2 |
| **Source Section** | "MuḥāsabahRecord — Normative Output Contract", "Muḥāsabah Validator" |
| **Enforcing Component** | `src/idis/validators/muhasabah.py` — MuhasabahValidator |
| **Secondary Enforcement** | Debate orchestrator hard gate; Agent output wrapper |
| **Tests** | `tests/test_muhasabah_validator.py::test_rejects_empty_claim_ids` |
| | `tests/test_muhasabah_validator.py::test_rejects_overconfident_no_uncertainty` |
| | `tests/test_muhasabah_validator.py::test_rejects_missing_falsifiability` |
| | `tests/test_muhasabah_validator.py::test_accepts_valid_record` |
| **Phase Gate** | Phase 5 (debate); mandatory before any IC output |
| **Evidence Artifact** | `muhasabah.recorded` event; `muhasabah.rejected` event with rejection reasons |
| **Violation Severity** | SEV-2 (rejection); SEV-1 (bypass) |

**Validator Rules (Normative):**
| Rule | Condition | Action |
|------|-----------|--------|
| No-Free-Facts | factual assertions AND supported_claim_ids empty | REJECT |
| Overconfidence | confidence > 0.80 AND uncertainties empty | REJECT |
| Falsifiability Missing | confidence > 0.50 AND falsifiability_tests empty | REJECT |

---

### 1.5 Deterministic Numerics (Calc-Sanad Reproducibility Hash)

| Attribute | Value |
|-----------|-------|
| **Requirement ID** | DN-001 |
| **Requirement** | All numeric metrics from deterministic engines; reproducibility hash must match for identical inputs |
| **Source Doc** | TDD §1.1; Implementation Plan §1; Tech Stack §1.4; SLO §3.5 |
| **Source Section** | "Zero Numerical Hallucination", "Deterministic Engines" |
| **Enforcing Component** | `src/idis/calc/engine.py` — CalcEngine |
| **Secondary Enforcement** | `src/idis/models/calc_sanad.py` — CalcSanad model with formula_hash |
| **Tests** | `tests/test_calc_reproducibility.py::test_same_inputs_same_hash` |
| | `tests/test_calc_reproducibility.py::test_formula_hash_stable` |
| | `tests/test_calc_sanad.py::test_provenance_complete` |
| **Phase Gate** | Phase 4 (mandatory) |
| **Evidence Artifact** | `CalcSanad` record with formula_hash, code_version, reproducibility_hash; `calc.completed` audit event |
| **Violation Severity** | SEV-2 |

**SLO Target:** ≥ 99.9% reproducibility (failures ≤ 0.1%)

---

### 1.6 Fail-Closed Validators Everywhere

| Attribute | Value |
|-----------|-------|
| **Requirement ID** | FC-001 |
| **Requirement** | All validators, auth, tenant scoping, audit logging must fail closed |
| **Source Doc** | TDD §10; Global Rules §3.3 |
| **Source Section** | "Implementation Notes", "Fail Closed Rule" |
| **Enforcing Component** | All validator classes; middleware; auth handlers |
| **Secondary Enforcement** | Default deny in RBAC; extraction gate |
| **Tests** | `tests/test_fail_closed.py::test_auth_fails_closed` |
| | `tests/test_fail_closed.py::test_tenant_fails_closed` |
| | `tests/test_fail_closed.py::test_validator_fails_closed` |
| | `tests/test_extraction_gate.py::test_low_confidence_blocked` |
| **Phase Gate** | All phases (from Phase 0) |
| **Evidence Artifact** | Rejection events with explicit deny reason; `rbac.denied` audit events |
| **Violation Severity** | Depends on context (SEV-1 for auth/tenant; SEV-2 for validators) |

---

## 2) Sanad Framework Traceability

### 2.1 Sanad Chain Integrity

| Attribute | Value |
|-----------|-------|
| **Requirement ID** | SAN-001 |
| **Requirement** | Every material claim has Sanad object with transmission_chain, grade, defects |
| **Source Doc** | TDD §4.2, §5.1; Data Model §3.4; Requirements §3 |
| **Source Section** | "Sanad (Claim-Level)", "Claim Sanad Grade Algorithm" |
| **Enforcing Component** | `src/idis/services/sanad/grader.py` — SanadGrader |
| **Secondary Enforcement** | `src/idis/models/sanad.py` — Sanad model |
| **Tests** | `tests/test_sanad_grade_algorithm.py::test_base_grade_min` |
| | `tests/test_sanad_grade_algorithm.py::test_fatal_defect_forces_d` |
| | `tests/test_sanad_grade_algorithm.py::test_major_downgrades` |
| | `tests/test_sanad_grade_algorithm.py::test_mutawatir_upgrade` |
| | `tests/test_sanad_coverage.py::test_material_claims_have_sanad` |
| **Phase Gate** | Phase 3 (mandatory) |
| **Evidence Artifact** | `Sanad` records in DB; `sanad.created`, `sanad.updated` audit events |
| **Violation Severity** | SEV-2 |

**Normative Algorithm Test Cases:**
| Inputs | Expected Grade |
|--------|----------------|
| min=B, MUTAWATIR, no defects | A |
| min=A, AHAD_1, MAJOR(INCONSISTENCY) | B |
| min=B, MUTAWATIR, FATAL(BROKEN_CHAIN) | D |
| min=C, NONE, no defects | C |
| min=B, AHAD_2, MAJOR×2 | D |

---

### 2.2 Independence Rules for Corroboration

| Attribute | Value |
|-----------|-------|
| **Requirement ID** | SAN-002 |
| **Requirement** | Mutawātir requires ≥3 independent sources; independence uses upstream_origin_id |
| **Source Doc** | TDD §5.2; Data Model §4.3 |
| **Source Section** | "Independence Rules for Corroboration (Mutawātir)" |
| **Enforcing Component** | `src/idis/services/sanad/independence.py` — IndependenceChecker |
| **Tests** | `tests/test_independence_rules.py::test_same_origin_not_independent` |
| | `tests/test_independence_rules.py::test_mutawatir_requires_three` |
| | `tests/test_independence_rules.py::test_chain_overlap_not_independent` |
| **Phase Gate** | Phase 3 (mandatory) |
| **Evidence Artifact** | `Sanad.corroboration_status`; independence computation stored |

**Independence Rules:**
| Rule | Condition for Independence |
|------|---------------------------|
| upstream_origin_id | Must differ (hard rule) |
| Chain overlap | No shared TransmissionNode segments |
| Preparer identity | No shared human preparer |
| Timestamp evidence | Suggests independent creation |

---

### 2.3 Defect Handling

| Attribute | Value |
|-----------|-------|
| **Requirement ID** | DEF-001 |
| **Requirement** | Defects created with type, severity, cure protocol; FATAL forces grade D |
| **Source Doc** | TDD §4.3; Data Model §3.4, §5.1, §6.3 |
| **Source Section** | "Defect (ʿIlal-Inspired)", "Defects (normalized)" |
| **Enforcing Component** | `src/idis/services/defects/service.py` — DefectService |
| **Secondary Enforcement** | `src/idis/models/defect.py` — Defect model |
| **Tests** | `tests/test_defect_severity.py::test_fatal_types` |
| | `tests/test_defect_severity.py::test_major_types` |
| | `tests/test_defect_severity.py::test_minor_types` |
| | `tests/test_defect_cure_protocol.py::test_cure_workflow` |
| | `tests/test_defect_waiver.py::test_waiver_requires_actor_reason` |
| **Phase Gate** | Phase 3 (mandatory) |
| **Evidence Artifact** | `Defect` records; `defect.created`, `defect.waived` audit events (HIGH severity) |

**Defect Severity Matrix:**
| Severity | Defect Types | Grade Impact |
|----------|--------------|--------------|
| FATAL | BROKEN_CHAIN, CONCEALMENT, CIRCULARITY | Forces D |
| MAJOR | INCONSISTENCY, ANOMALY_VS_STRONGER_SOURCES, UNKNOWN_SOURCE | Downgrade 1 level each |
| MINOR | STALENESS, UNIT_MISMATCH, TIME_WINDOW_MISMATCH, SCOPE_DRIFT | Flag only |

---

## 3) API & Integration Traceability

### 3.1 Idempotency-Key Behavior

| Attribute | Value |
|-----------|-------|
| **Requirement ID** | API-001 |
| **Requirement** | POST/PATCH endpoints accept Idempotency-Key; same key returns stored response |
| **Source Doc** | OpenAPI IdempotencyKey parameter; API Contracts §4.1 |
| **Source Section** | "Idempotency & Retries" |
| **Enforcing Component** | `src/idis/api/middleware/idempotency.py` — IdempotencyMiddleware |
| **Tests** | `tests/test_idempotency.py::test_replay_returns_stored` |
| | `tests/test_idempotency.py::test_different_payload_409` |
| **Phase Gate** | Phase 2.5 |
| **Evidence Artifact** | Idempotency records in DB; request_id in audit events |

---

### 3.2 Error Model Consistency

| Attribute | Value |
|-----------|-------|
| **Requirement ID** | API-002 |
| **Requirement** | All errors return JSON with code, message, details, request_id |
| **Source Doc** | OpenAPI Error schema; API Contracts §8 |
| **Source Section** | "Error Model (Normative)" |
| **Enforcing Component** | `src/idis/api/errors.py` — ErrorHandler |
| **Tests** | `tests/test_error_model.py::test_400_matches_schema` |
| | `tests/test_error_model.py::test_401_matches_schema` |
| | `tests/test_error_model.py::test_500_matches_schema` |
| **Phase Gate** | Phase 2.6 |
| **Evidence Artifact** | Error responses logged with request_id |

---

### 3.3 Rate Limiting

| Attribute | Value |
|-----------|-------|
| **Requirement ID** | API-003 |
| **Requirement** | Rate limits enforced: 600 req/min/tenant (user), 1200 req/min/tenant (integration) |
| **Source Doc** | API Contracts §4.3 |
| **Source Section** | "Rate Limits (Default)" |
| **Enforcing Component** | `src/idis/api/middleware/rate_limit.py` — RateLimitMiddleware |
| **Tests** | `tests/test_rate_limiting.py::test_user_limit_enforced` |
| | `tests/test_rate_limiting.py::test_429_returned` |
| **Phase Gate** | Phase 2.7 |
| **Evidence Artifact** | Rate limit metrics; 429 responses logged |

---

### 3.4 Webhook Signing + Retry

| Attribute | Value |
|-----------|-------|
| **Requirement ID** | WH-001 |
| **Requirement** | Webhooks signed with HMAC; retries 10 attempts over 24 hours |
| **Source Doc** | OpenAPI /v1/webhooks; API Contracts §6 |
| **Source Section** | "Webhooks (Outbound Eventing)" |
| **Enforcing Component** | `src/idis/services/webhooks/service.py` — WebhookService |
| **Tests** | `tests/test_webhook_signing.py::test_hmac_correct` |
| | `tests/test_webhook_retry.py::test_exponential_backoff` |
| **Phase Gate** | Phase 2.8 |
| **Evidence Artifact** | `webhook.delivery.succeeded/failed` audit events |

---

## 4) Data Residency & Compliance Traceability

### 4.1 Data Residency Region Pinning (Staged)

| Attribute | Value |
|-----------|-------|
| **Requirement ID** | DR-001 |
| **Requirement** | Tenant data stays in assigned region; cross-region operations forbidden by default |
| **Source Doc** | Data Residency §3-4 |
| **Source Section** | "Tenant-level Data Region", "Storage Residency Rules" |
| **Enforcing Component** | `src/idis/models/tenant.py` — data_region field |
| **Secondary Enforcement** | Object store prefix per region; RLS with region checks |
| **Tests** | `tests/test_data_residency.py::test_region_enforced` |
| | `tests/test_data_residency.py::test_cross_region_blocked` |
| **Phase Gate** | Phase 7 (full); Phase 2 (field present) |
| **Evidence Artifact** | Tenant config; region metadata in storage |

---

### 4.2 BYOL Provider Licensing Controls

| Attribute | Value |
|-----------|-------|
| **Requirement ID** | BYOL-001 |
| **Requirement** | Enrichment data stored with provider, license_type, tenant_id; no cross-tenant redistribution |
| **Source Doc** | Data Residency §7 |
| **Source Section** | "BYOL Provider Licensing Controls" |
| **Enforcing Component** | `src/idis/services/enrichment/service.py` — EnrichmentService |
| **Tests** | `tests/test_byol_isolation.py::test_no_cross_tenant_enrichment` |
| **Phase Gate** | Phase 7 |
| **Evidence Artifact** | `EnrichmentRecord` with provider metadata; `enrichment.completed` audit events |

---

## 5) Operational Readiness Traceability

### 5.1 SLO/Ops Readiness Checks

| Attribute | Value |
|-----------|-------|
| **Requirement ID** | OPS-001 |
| **Requirement** | Go-live requires: dashboards, alerts, backups tested, DR drill, runbooks published |
| **Source Doc** | SLO/Runbooks §10 |
| **Source Section** | "Operational Readiness Checklist (Go-Live Gate)" |
| **Enforcing Component** | Manual checklist; automated dashboard checks |
| **Tests** | Manual verification |
| **Phase Gate** | Phase 7 (go-live gate) |
| **Evidence Artifact** | Checklist sign-off; DR drill reports; backup test results |

**Checklist Items:**
| Item | Verification Method |
|------|---------------------|
| SLO dashboards exist | Dashboard URL accessible |
| Paging alerts configured | Alert test fired successfully |
| Backup/restore tested | Restore drill completed |
| DR failover drill completed | Drill report signed |
| Audit coverage 100% | Automated test passes |
| No-Free-Facts validator enforced | Test deliverable export |
| Muḥāsabah gate enforced | Test debate output |
| Runbooks published | Doc review |
| On-call rotation established | Schedule documented |

---

## 6) Prompt Registry & Evaluation Traceability

### 6.1 Prompt Registry + Promotion/Rollback (Staged)

| Attribute | Value |
|-----------|-------|
| **Requirement ID** | PR-001 |
| **Requirement** | Prompts versioned (semver), stored with metadata, promotion requires gates |
| **Source Doc** | Prompt Registry §2-11 |
| **Source Section** | "Prompt Registry Overview", "Promotion Pipeline" |
| **Enforcing Component** | `prompts/` directory; registry JSON files; CI gates |
| **Tests** | `tests/test_prompt_registry.py::test_version_loaded` |
| | `tests/test_prompt_registry.py::test_rollback_works` |
| **Phase Gate** | Phase 5 (basic); Phase 6 (full); Phase 7 (CI gates) |
| **Evidence Artifact** | `prompt.promoted`, `prompt.rollback` audit events |

**Required Gates by Risk Class:**
| Risk Class | Required Gates |
|------------|----------------|
| LOW | Gate 1 + automated review |
| MEDIUM | Gate 1 + Gate 2 |
| HIGH | Gate 1 + Gate 2 + Gate 3 + Gate 4 + security sign-off |

---

### 6.2 Evaluation Harness Gates

| Attribute | Value |
|-----------|-------|
| **Requirement ID** | EH-001 |
| **Requirement** | Release gates enforce: No-Free-Facts 0 violations, Muḥāsabah ≥98%, audit 100%, reproducibility ≥99.9% |
| **Source Doc** | Evaluation Harness §2, §8 |
| **Source Section** | "What must be evaluated", "Release Gates" |
| **Enforcing Component** | CI pipeline; test harness CLI |
| **Tests** | GDBS-S, GDBS-F, GDBS-A benchmark suites |
| **Phase Gate** | Phase 5 (Gate 1-2); Phase 6 (Gate 3); Phase 7 (Gate 4) |
| **Evidence Artifact** | Gate results in CI; evaluation_results_ref on prompt artifacts |

**Gate Requirements:**
| Gate | Requirements | Environment |
|------|--------------|-------------|
| Gate 0 | Schema validation, lint, type checks | dev |
| Gate 1 | No-Free-Facts 0, Muḥāsabah ≥98%, audit 100%, tenant isolation | staging |
| Gate 2 | Sanad coverage ≥95%, defect recall ≥90%, calc repro ≥99.9% | staging |
| Gate 3 | GDBS-F end-to-end, debate completion ≥98% | preprod |
| Gate 4 | Human review (10 deal sample) | preprod |

---

## 7) Security Traceability

### 7.1 Encryption Requirements

| Attribute | Value |
|-----------|-------|
| **Requirement ID** | SEC-001 |
| **Requirement** | TLS 1.2+ in transit; AES-256 at rest; BYOK option |
| **Source Doc** | Security Threat Model §5; Data Residency §5 |
| **Source Section** | "Encryption and Key Management" |
| **Enforcing Component** | Infrastructure configuration; KMS integration |
| **Tests** | `tests/test_encryption.py::test_tls_enforced` |
| | `tests/test_byok.py::test_customer_key_used` |
| **Phase Gate** | Phase 0 (TLS); Phase 7 (BYOK) |
| **Evidence Artifact** | TLS certificates; KMS key metadata |

---

### 7.2 RBAC Enforcement

| Attribute | Value |
|-----------|-------|
| **Requirement ID** | SEC-002 |
| **Requirement** | Server-side RBAC; deny by default; roles: ANALYST, PARTNER, IC_MEMBER, ADMIN, AUDITOR |
| **Source Doc** | Security Threat Model §4; API Contracts §3.2 |
| **Source Section** | "Authorization (RBAC + ABAC)" |
| **Enforcing Component** | `src/idis/api/middleware/auth.py` — AuthMiddleware |
| **Tests** | `tests/test_rbac.py::test_analyst_cannot_approve_override` |
| | `tests/test_rbac.py::test_partner_can_approve_override` |
| | `tests/test_rbac.py::test_deny_by_default` |
| **Phase Gate** | Phase 2 (basic); Phase 7 (full enterprise) |
| **Evidence Artifact** | `rbac.denied` audit events |

---

## 8) Full Traceability Matrix (Summary Table)

| Req ID | Requirement | Source Doc | Enforcing Component | Test File | Phase | Evidence |
|--------|-------------|------------|---------------------|-----------|-------|----------|
| TI-001 | Tenant isolation | Security §6 | TenantContextMiddleware | test_api_tenancy_auth.py | 0/2/7 | AuditEvent.tenant_id |
| AI-001 | Audit 100% coverage | Audit §2 | AuditMiddleware | test_audit_coverage.py | 2.3 | AuditEvent records |
| NFF-001 | No-Free-Facts | TDD §1.1 | NoFreeFacts validator | test_no_free_facts.py | 2/6 | muhasabah.rejected |
| MUH-001 | Muḥāsabah gate | TDD §4.4 | MuhasabahValidator | test_muhasabah_validator.py | 5 | muhasabah.recorded |
| DN-001 | Calc reproducibility | TDD §1.1 | CalcEngine | test_calc_reproducibility.py | 4 | CalcSanad record |
| FC-001 | Fail-closed | TDD §10 | All validators | test_fail_closed.py | 0+ | Rejection events |
| SAN-001 | Sanad integrity | TDD §4.2 | SanadGrader | test_sanad_grade_algorithm.py | 3 | Sanad records |
| SAN-002 | Independence rules | TDD §5.2 | IndependenceChecker | test_independence_rules.py | 3 | corroboration_status |
| DEF-001 | Defect handling | TDD §4.3 | DefectService | test_defect_severity.py | 3 | Defect records |
| API-001 | Idempotency | API §4.1 | IdempotencyMiddleware | test_idempotency.py | 2.5 | request_id |
| API-002 | Error model | API §8 | ErrorHandler | test_error_model.py | 2.6 | Error responses |
| API-003 | Rate limiting | API §4.3 | RateLimitMiddleware | test_rate_limiting.py | 2.7 | 429 responses |
| WH-001 | Webhook signing | API §6 | WebhookService | test_webhook_signing.py | 2.8 | delivery events |
| DR-001 | Data residency | Residency §3 | data_region field | test_data_residency.py | 7 | Region metadata |
| BYOL-001 | BYOL isolation | Residency §7 | EnrichmentService | test_byol_isolation.py | 7 | EnrichmentRecord |
| OPS-001 | Ops readiness | SLO §10 | Manual checklist | Manual | 7 | Checklist sign-off |
| PR-001 | Prompt registry | Prompt §2 | prompts/ directory | test_prompt_registry.py | 5/6/7 | prompt.* events |
| EH-001 | Eval harness | Eval §8 | CI pipeline | GDBS suites | 5/6/7 | Gate results |
| SEC-001 | Encryption | Security §5 | Infra config | test_encryption.py | 0/7 | TLS certs |
| SEC-002 | RBAC | Security §4 | AuthMiddleware | test_rbac.py | 2/7 | rbac.denied |

---

## 9) Test Coverage Matrix

### 9.1 Existing Tests (Phase 0 / 2.3)

| Test File | Tests | Status |
|-----------|-------|--------|
| `tests/test_api_health.py` | test_health_endpoint | ✅ Passing |
| `tests/test_api_openapi_validation.py` | test_openapi_loads | ✅ Passing |
| `tests/test_api_tenancy_auth.py` | test_tenant_isolation | ✅ Passing |

### 9.2 Required Tests (Pending)

| Test File | Tests | Phase |
|-----------|-------|-------|
| `tests/test_no_free_facts.py` | test_rejects_unlinked_fact, test_accepts_linked_fact | 2 |
| `tests/test_muhasabah_validator.py` | test_rejects_empty_claim_ids, test_rejects_overconfident | 5 |
| `tests/test_sanad_grade_algorithm.py` | test_base_grade_min, test_fatal_defect_forces_d | 3 |
| `tests/test_calc_reproducibility.py` | test_same_inputs_same_hash | 4 |
| `tests/test_audit_coverage.py` | test_all_mutations_emit_audit | 2 |
| `tests/test_idempotency.py` | test_replay_returns_stored, test_different_payload_409 | 2.5 |
| `tests/test_rate_limiting.py` | test_user_limit_enforced, test_429_returned | 2.7 |
| `tests/test_webhook_signing.py` | test_hmac_correct | 2.8 |
| `tests/test_independence_rules.py` | test_same_origin_not_independent | 3 |
| `tests/test_defect_severity.py` | test_fatal_types, test_major_types | 3 |
| `tests/test_data_residency.py` | test_region_enforced | 7 |
| `tests/test_rbac.py` | test_analyst_cannot_approve_override | 2/7 |

---

## 10) Revision History

| Date | Version | Author | Changes |
|------|---------|--------|---------|
| 2026-01-07 | 1.0 | Cascade | Initial creation from v6.3 docs consolidation |
