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
| **Source Doc** | Security Threat Model section 6; Data Residency section 4; API Contracts section 2.3 |
| **Source Section** | "Multi-Tenant Isolation Model", "Tenant-level Data Region" |
| **Enforcing Component** | `src/idis/api/middleware/tenant.py` - TenantContextMiddleware |
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
| **Source Doc** | Audit Taxonomy section 2; SLO section 3.5; Security Threat Model section 9 |
| **Source Section** | "Core Requirements (Non-negotiable)", "Trust-Invariant SLOs" |
| **Enforcing Component** | `src/idis/api/middleware/audit.py` - AuditMiddleware |
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
| **Source Doc** | TDD section 1.1, section 4.4; API Contracts section 2.1; Evaluation Harness section 6.1 |
| **Source Section** | "Non-Negotiable Invariants", "No-Free-Facts Validator" |
| **Enforcing Component** | `src/idis/validators/no_free_facts.py` - NoFreeFacts validator |
| **Secondary Enforcement** | Deliverable export gate; Muhasabah validator check |
| **Tests** | `tests/test_no_free_facts.py::test_rejects_unlinked_fact` |
| | `tests/test_no_free_facts.py::test_accepts_linked_fact` |
| | `tests/test_no_free_facts.py::test_subjective_allowed` |
| | `tests/test_deliverable_validation.py::test_export_blocked_without_refs` |
| **Phase Gate** | Phase 2 (claim registration); Phase 6 (deliverable export) |
| **Evidence Artifact** | `muhasabah.rejected` event with violation details; deliverable validation logs |
| **Violation Severity** | SEV-1 |

---

### 1.4 Muhasabah Deterministic Validator

| Attribute | Value |
|-----------|-------|
| **Requirement ID** | MUH-001 |
| **Requirement** | Every agent output must include valid MuhasabahRecord; validator rejects invalid |
| **Source Doc** | TDD section 4.4; Data Model section 7.2; Evaluation Harness section 6.2 |
| **Source Section** | "MuhasabahRecord - Normative Output Contract", "Muhasabah Validator" |
| **Enforcing Component** | `src/idis/validators/muhasabah.py` - MuhasabahValidator |
| **Secondary Enforcement** | `src/idis/debate/muhasabah_gate.py` - MuhasabahGate (output boundary); `src/idis/debate/orchestrator.py` - gate enforcement point |
| **Tests** | `tests/test_muhasabah_validator.py::test_rejects_empty_claim_ids` |
| | `tests/test_muhasabah_validator.py::test_rejects_overconfident_no_uncertainty` |
| | `tests/test_muhasabah_validator.py::test_rejects_missing_falsifiability` |
| | `tests/test_muhasabah_validator.py::test_accepts_valid_record` |
| | `tests/test_muhasabah_gate.py::test_gate_blocks_missing_muhasabah_record` |
| | `tests/test_muhasabah_gate.py::test_gate_blocks_invalid_muhasabah_overconfidence_without_uncertainty` |
| | `tests/test_muhasabah_gate.py::test_gate_blocks_missing_falsifiability_when_confident` |
| | `tests/test_muhasabah_gate.py::test_gate_blocks_no_free_facts_violation_at_output_boundary` |
| | `tests/test_muhasabah_gate.py::test_gate_allows_valid_record_with_claim_refs` |
| | `tests/test_debate_muhasabah_integration.py::test_orchestrator_blocks_invalid_output_no_claims` |
| | `tests/test_debate_muhasabah_integration.py::test_orchestrator_proceeds_with_valid_output` |
| **Phase Gate** | Phase 5.2 (debate gate); mandatory before any IC output |
| **Evidence Artifact** | `muhasabah.recorded` event; `muhasabah.rejected` event with rejection reasons |
| **Violation Severity** | SEV-2 (rejection); SEV-1 (bypass) |
| **Implementation Status** | [x] Gate enforced at output boundary (Phase 5.2)

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
| **Source Doc** | TDD section 1.1; Implementation Plan section 1; Tech Stack section 1.4; SLO section 3.5 |
| **Source Section** | "Zero Numerical Hallucination", "Deterministic Engines" |
| **Enforcing Component** | `src/idis/calc/engine.py` - CalcEngine |
| **Secondary Enforcement** | `src/idis/models/calc_sanad.py` - CalcSanad model with formula_hash |
| **Tests** | `tests/test_calc_reproducibility.py::test_same_inputs_same_hash` |
| | `tests/test_calc_reproducibility.py::test_formula_hash_stable` |
| | `tests/test_calc_sanad.py::test_provenance_complete` |
| **Phase Gate** | Phase 4 (mandatory) |
| **Evidence Artifact** | `CalcSanad` record with formula_hash, code_version, reproducibility_hash; `calc.completed` audit event |
| **Violation Severity** | SEV-2 |

**SLO Target:** >= 99.9% reproducibility (failures <= 0.1%)

---

### 1.6 Fail-Closed Validators Everywhere

| Attribute | Value |
|-----------|-------|
| **Requirement ID** | FC-001 |
| **Requirement** | All validators, auth, tenant scoping, audit logging must fail closed |
| **Source Doc** | TDD section 10; Global Rules section 3.3 |
| **Source Section** | "Implementation Notes", "Fail Closed Rule" |
| **Enforcing Component** | All validator classes; middleware; auth handlers |
| **Secondary Enforcement** | Default deny in RBAC; extraction gate (`src/idis/validators/extraction_gate.py`) |
| **Tests** | `tests/test_fail_closed.py::test_auth_fails_closed` |
| | `tests/test_fail_closed.py::test_tenant_fails_closed` |
| | `tests/test_fail_closed.py::test_validator_fails_closed` |
| | `tests/test_extraction_gate.py::test_low_confidence_blocked` [x] |
| | `tests/test_extraction_gate.py::test_missing_values_fail_closed` [x] |
| **Phase Gate** | All phases (from Phase 0) |
| **Evidence Artifact** | Rejection events with explicit deny reason; `rbac.denied` audit events |
| **Violation Severity** | Depends on context (SEV-1 for auth/tenant; SEV-2 for validators) |
| **Implementation Status** | [x] Extraction gate implemented (Phase 4.2) |

---

## 2) Sanad Framework Traceability

### 2.1 Sanad Chain Integrity

| Attribute | Value |
|-----------|-------|
| **Requirement ID** | SAN-001 |
| **Requirement** | Every material claim has Sanad object with transmission_chain, grade, defects |
| **Source Doc** | TDD section 4.2, section 5.1; Data Model section 3.4; Requirements section 3 |
| **Source Section** | "Sanad (Claim-Level)", "Claim Sanad Grade Algorithm" |
| **Enforcing Component** | `src/idis/services/sanad/grader.py` - SanadGrader |
| **Secondary Enforcement** | `src/idis/models/sanad.py` - Sanad model |
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
| min=B, AHAD_2, MAJORx2 | D |

---

### 2.2 Independence Rules for Corroboration

| Attribute | Value |
|-----------|-------|
| **Requirement ID** | SAN-002 |
| **Requirement** | Mutawatir requires >=3 independent sources; independence uses upstream_origin_id |
| **Source Doc** | TDD section 5.2; Data Model section 4.3 |
| **Source Section** | "Independence Rules for Corroboration (Mutawatir)" |
| **Enforcing Component** | `src/idis/services/sanad/independence.py` - IndependenceChecker |
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
| **Source Doc** | TDD section 4.3; Data Model section 3.4, section 5.1, section 6.3 |
| **Source Section** | "Defect ('Ilal-Inspired)", "Defects (normalized)" |
| **Enforcing Component** | `src/idis/services/defects/service.py` - DefectService |
| **Secondary Enforcement** | `src/idis/models/defect.py` - Defect model |
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
| **Source Doc** | OpenAPI IdempotencyKey parameter; API Contracts section 4.1 |
| **Source Section** | "Idempotency & Retries" |
| **Enforcing Component** | `src/idis/api/middleware/idempotency.py` - IdempotencyMiddleware |
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
| **Source Doc** | OpenAPI Error schema; API Contracts section 8 |
| **Source Section** | "Error Model (Normative)" |
| **Enforcing Component** | `src/idis/api/errors.py` - ErrorHandler |
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
| **Source Doc** | API Contracts section 4.3 |
| **Source Section** | "Rate Limits (Default)" |
| **Enforcing Component** | `src/idis/api/middleware/rate_limit.py` - RateLimitMiddleware |
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
| **Source Doc** | OpenAPI /v1/webhooks; API Contracts section 6 |
| **Source Section** | "Webhooks (Outbound Eventing)" |
| **Enforcing Component** | `src/idis/services/webhooks/dispatcher.py` - WebhookDispatcher (signs via `signing.py`, retries via `retry.py`, drains the `webhook_delivery_attempts` outbox) |
| **Tests** | `tests/test_webhook_signing.py::test_hmac_correct` |
| | `tests/test_webhook_retry.py::test_exponential_backoff` |
| | `tests/test_slice97_webhook_dispatcher.py` (sign + deliver + retry + exhaustion) |
| | `tests/test_slice97_webhook_dispatcher_postgres.py` (RLS secret load, no double-delivery) |
| **Phase Gate** | Phase 2.8 |
| **Evidence Artifact** | `webhook.delivery.succeeded/failed` audit events |
| **Implementation Status** | [x] Delivered (Slice97) - durable outbox + signed dispatch + delivery audit/metrics |

---

## 4) Data Residency & Compliance Traceability

### 4.1 Data Residency Region Pinning (Staged)

| Attribute | Value |
|-----------|-------|
| **Requirement ID** | DR-001 |
| **Requirement** | Tenant data stays in assigned region; cross-region operations forbidden by default |
| **Source Doc** | Data Residency section 3-4 |
| **Source Section** | "Tenant-level Data Region", "Storage Residency Rules" |
| **Enforcing Component** | `src/idis/compliance/residency.py` (`enforce_residency`); durable `tenants.data_region` (migration 0027); `middleware/residency.py` |
| **Secondary Enforcement** | Flag-gated durable source of truth (`IDIS_ENABLE_DURABLE_RESIDENCY`); fail-closed 403 on missing/mismatch |
| **Tests** | `tests/test_data_residency.py` |
| | `tests/test_slice98_durable_residency.py`, `tests/test_slice98_durable_residency_postgres.py` |
| **Phase Gate** | Phase 7 (Slice98: durable source of truth) |
| **Evidence Artifact** | Tenant config; region metadata in storage |

---

### 4.2 BYOL Provider Licensing Controls

| Attribute | Value |
|-----------|-------|
| **Requirement ID** | BYOL-001 |
| **Requirement** | Enrichment data stored with provider, license_type, tenant_id; no cross-tenant redistribution |
| **Source Doc** | Data Residency section 7 |
| **Source Section** | "BYOL Provider Licensing Controls" |
| **Enforcing Component** | `src/idis/services/enrichment/service.py` - EnrichmentService |
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
| **Source Doc** | SLO/Runbooks section 10 |
| **Source Section** | "Operational Readiness Checklist (Go-Live Gate)" |
| **Enforcing Component** | Manual checklist; `scripts/db_backup_restore.py` + monitoring exports (Slice99) |
| **Tests** | `tests/test_slice99_backup_restore_postgres.py` - CI-ENFORCED via the dedicated `backup-restore-drill` job (disposable Postgres, `IDIS_REQUIRE_POSTGRES=1`), which the `release-gate` job depends on; `tests/test_slice99_metrics_endpoint.py` (export parity + /metrics deploy truth); remaining items manual |
| **Phase Gate** | Phase 7 (go-live gate) |
| **Evidence Artifact** | Checklist sign-off; DR drill reports; backup test results; RB-11 drill runbook |

**Checklist Items:**
| Item | Verification Method |
|------|---------------------|
| SLO dashboards exist | Definitions exported to `deploy/monitoring/dashboards/` (Slice99); Grafana deployment pending |
| Paging alerts configured | Rules exported to `deploy/monitoring/prometheus_alert_rules.yaml` (Slice99); alerting deployment pending |
| Backup/restore tested | CI-enforced restore drill: `backup-restore-drill` job runs `tests/test_slice99_backup_restore_postgres.py` on every CI run and gates `release-gate` (RB-11 covers the manual/local drill procedure) |
| DR failover drill completed | Drill report signed |
| Audit coverage 100% | Automated test passes |
| No-Free-Facts validator enforced | Test deliverable export |
| Muhasabah gate enforced | Test debate output |
| Runbooks published | RB-01..RB-11 in `docs/runbooks/` (RB-11 added Slice99) |
| On-call rotation established | Schedule documented |

---

## 6) Prompt Registry & Evaluation Traceability

### 6.1 Prompt Registry + Promotion/Rollback (Staged)

| Attribute | Value |
|-----------|-------|
| **Requirement ID** | PR-001 |
| **Requirement** | Prompts versioned (semver), stored with metadata, promotion requires gates |
| **Source Doc** | Prompt Registry section 2-11; Go-Live Checklist section 4.4 |
| **Source Section** | "Prompt Registry Overview", "Promotion Pipeline", "Audited Promotion/Rollback" |
| **Enforcing Component** | `src/idis/services/prompts/registry.py` - PromptRegistry |
| | `src/idis/services/prompts/versioning.py` - PromptVersioningService |
| | `src/idis/services/prompts/validate_cli.py` - `python -m idis prompts validate` (CI-wired, Slice99) |
| | `src/idis/services/prompts/promotion_policy.py` - `IDIS_REQUIRE_PROMOTED_PROMPTS` strict policy (Slice99) |
| **Tests** | `tests/test_prompt_registry.py::test_version_loaded` |
| | `tests/test_prompt_registry.py::test_rollback_works` |
| | `tests/test_prompt_registry.py::TestPromptVersioningPromotion` |
| | `tests/test_prompt_registry.py::TestPromptVersioningRollback` |
| | `tests/test_prompt_registry.py::TestAuditFailureIsFatal` |
| | `tests/test_slice99_prompt_governance.py` (tree validation, schema-valid events) |
| | `tests/test_slice99_prompt_model_linkage.py` (runtime linkage + promoted-prompts policy) |
| **Phase Gate** | Phase 7.2 |
| **Evidence Artifact** | `prompt.version.promoted`, `prompt.version.rolledback`, `prompt.version.retired` audit events - schema-valid via `validate_audit_event` with `prompt` registered in BOTH the Python validator and `schemas/audit_event.schema.json` (Slice99) |
| **Implementation Status** | [x] Exists - hardened in Slice99 (validation CLI in CI, core-audit-convention events, promoted-prompts strict policy default-off; actual promotions await real eval evidence) |

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
| **Requirement** | Release gates enforce: No-Free-Facts 0 violations, Muhasabah >=98%, audit 100%, reproducibility >=99.9% |
| **Source Doc** | Evaluation Harness section 2, section 8 |
| **Source Section** | "What must be evaluated", "Release Gates" |
| **Enforcing Component** | CI pipeline; `python -m idis test gdbs-*` CLI; `evaluation/baseline.py` drift gate (Slice99) |
| **Tests** | GDBS-S, GDBS-F, GDBS-A benchmark suites; `tests/test_slice99_gdbs_drift_gate.py` |
| **Phase Gate** | Phase 5 (Gate 1-2); Phase 6 (Gate 3); Phase 7 (Gate 4) |
| **Evidence Artifact** | Gate results in CI; evaluation_results_ref on prompt artifacts; pinned `tests/fixtures/gdbs_baseline/gdbs_mini_gdbs_s_baseline.json` |
| **Implementation Status** | Partial - Gate 0 in CI; Slice99 adds the hermetic GDBS-S drift gate (pinned baseline + explicit thresholds) to the evaluation-harness job and the release-gate aggregation; live execute-mode Gates 1-4 evidence pending |

**Gate Requirements (Hard vs Soft Classification):**
| Gate | Type | Requirements | Environment | Failure Impact |
|------|------|--------------|-------------|----------------|
| Gate 0 | **HARD** | Schema validation, lint, type checks, unit tests | dev | Block merge |
| Gate 1 | **HARD** | No-Free-Facts 0, Muhasabah >=98%, audit 100%, tenant isolation | staging | Block staging deploy |
| Gate 2 | **HARD** | Sanad coverage >=95%, defect recall >=90%, calc repro >=99.9% | staging | Block preprod deploy |
| Gate 3 | **SOFT** | GDBS-F end-to-end, debate completion >=98% | preprod | Flag for review |
| Gate 4 | **SOFT** | Human review (10 deal sample) | preprod | Flag for review |

**Hard Gates:** Automated enforcement, no override without security approval.
**Soft Gates:** Automated check, manual override with documented justification allowed.

---

## 7) Security Traceability

### 7.1 Encryption Requirements

| Attribute | Value |
|-----------|-------|
| **Requirement ID** | SEC-001 |
| **Requirement** | TLS 1.2+ in transit; AES-256 at rest; BYOK option |
| **Source Doc** | Security Threat Model section 5; Data Residency section 5 |
| **Source Section** | "Encryption and Key Management" |
| **Enforcing Component** | BYOK: `src/idis/compliance/byok.py` (durable policy registry, migration 0029; metadata-only, KMS-boundary seam per `docs/architecture/slice98_byok_kms_decision.md`). TLS 1.2+/AES-256 = infrastructure configuration |
| **Tests** | `tests/test_byok.py`, `tests/test_slice98_byok_legal_hold_postgres.py` |
| | (TLS/AES-at-rest verified at the infra layer, not in-repo) |
| **Phase Gate** | Phase 7 (Slice98: durable BYOK); Phase 0 (TLS, infra) |
| **Evidence Artifact** | `byok.*` audit events; KMS-boundary decision note |

---

### 7.2 RBAC Enforcement

| Attribute | Value |
|-----------|-------|
| **Requirement ID** | SEC-002 |
| **Requirement** | Server-side RBAC; deny by default; roles: ANALYST, PARTNER, IC_MEMBER, ADMIN, AUDITOR |
| **Source Doc** | Security Threat Model section 4; API Contracts section 3.2 |
| **Source Section** | "Authorization (RBAC + ABAC)" |
| **Enforcing Component** | `src/idis/api/middleware/rbac.py` (RBAC); `src/idis/api/abac.py` (deny-by-default deal ABAC, durable stores migration 0026) |
| **Tests** | `tests/test_abac.py`, `tests/test_api_rbac_middleware.py` |
| | `tests/test_slice98_abac_durable_store_postgres.py`, `tests/test_slice98_deal_abac_extraction.py` |
| **Phase Gate** | Phase 2 (basic); Phase 7 (Slice98: durable ABAC) |
| **Evidence Artifact** | `rbac.*` / ABAC_DENIED_* codes; break-glass CRITICAL audit |

---

## 8) Full Traceability Matrix (Summary Table)

| Req ID | Requirement | Source Doc | Enforcing Component | Test File | Phase | Impl Status | Evidence |
|--------|-------------|------------|---------------------|-----------|-------|-------------|----------|
| TI-001 | Tenant isolation | Security section 6 | `openapi_validate.py` | test_api_tenancy_auth.py | 0/2/7 | [x] Exists | AuditEvent.tenant_id |
| AI-001 | Audit 100% coverage | Audit section 2 | `audit.py` | test_api_audit_middleware.py | 2.3 | [x] Exists | AuditEvent records |
| AI-002 | Audit event validation | Audit section 3 | `audit_event_validator.py` | test_audit_event_validator.py | 2.3.1 | [x] Exists | Schema validation |
| NFF-001 | No-Free-Facts | TDD section 1.1 | `no_free_facts.py` (validator) | test_no_free_facts.py | 2/6 | [x] Exists | muhasabah.rejected |
| MUH-001 | Muhasabah gate | TDD section 4.4 | `muhasabah.py` (validator) | test_muhasabah_validator.py | 5 | [x] Exists | muhasabah.recorded |
| SAN-001 | Sanad integrity | TDD section 4.2 | `sanad/grader.py` | test_sanad_integrity.py, test_sanad_methodology_v2_unit.py | 3 | [x] Exists | Sanad records |
| SAN-002 | Independence rules | TDD section 5.2 | `sanad/tawatur.py` | test_sanad_methodology_v2_unit.py::TestTawatur | 3.3 | [x] Exists | corroboration_status |
| SAN-003 | Source tiers (6-level) | Methodology v2 section 2 | `sanad/source_tiers.py` | test_sanad_methodology_v2_unit.py::TestSourceTiers | 3.3 | [x] Exists | tier assignment |
| SAN-004 | Dabt scoring | Methodology v2 section 3 | `sanad/dabt.py` | test_sanad_methodology_v2_unit.py::TestDabt | 3.3 | [x] Exists | dabt_score |
| SAN-005 | Shudhudh detection | Methodology v2 section 5 | `sanad/shudhudh.py` | test_sanad_methodology_v2_unit.py::TestShudhudh | 3.3 | [x] Exists | SHUDHUDH_ANOMALY |
| SAN-006 | I'lal defects | Methodology v2 section 6 | `sanad/ilal.py` | test_sanad_methodology_v2_unit.py::TestIlal | 3.3 | [x] Exists | ILAL_* defects |
| SAN-007 | COI handling | Methodology v2 section 7 | `sanad/coi.py` | test_sanad_methodology_v2_unit.py::TestCOI | 3.3 | [x] Exists | COI_* defects |
| SAN-008 | Grader v2 | Methodology v2 section 8 | `sanad/grader.py` | test_sanad_methodology_v2_unit.py::TestGraderV2 | 3.3 | [x] Exists | grade_sanad_v2() |
| DEF-001 | Defect handling | TDD section 4.3 | `sanad/defects.py`, `sanad/ilal.py` | test_sanad_methodology_v2_unit.py | 3 | [x] Exists | Defect records |
| DN-001 | Calc reproducibility | TDD section 1.1 | `calc/engine.py` | test_calc_reproducibility.py, test_calc_sanad.py | 4.1 | [x] Exists | CalcSanad record |
| DN-002 | Calc-Sanad grade derivation | TDD section 1.1 | `calc/engine.py` | test_calc_sanad.py::TestGradeDerivation* | 4.1 | [x] Exists | material-aware calc_grade (non-material excluded) |
| DN-003 | Calc RLS tenant isolation | Security section 6 | migration 0005 | test_postgres_rls_and_audit_immutability.py::TestDeterministicCalculationsRLS | 4.1 | [x] Exists | RLS policies |
| DN-004 | CalcSanad RLS tenant isolation | Security section 6 | migration 0005 | test_postgres_rls_and_audit_immutability.py::TestCalcSanadsRLS | 4.1 | [x] Exists | RLS policies |
| FC-001 | Fail-closed | TDD section 10 | All validators, `extraction_gate.py` | test_extraction_gate.py | 0+/4.2 | [x] Exists | Rejection events |
| DB-001 | Debate orchestration | TDD section 6, Appendix C-1 | `debate/orchestrator.py` | test_debate_node_graph.py, test_debate_stop_conditions.py | 5.1 | [x] Exists | nodes_visited, stop_reason |
| DB-002 | Stop condition priority | Go-Live section 5, Appendix C-1 | `debate/stop_conditions.py` | test_debate_stop_conditions.py | 5.1 | [x] Exists | StopReason enum |
| DB-003 | Max rounds = 5 | Go-Live section 5, TDD section 6 | `debate/stop_conditions.py` | test_debate_stop_conditions.py::test_max_rounds* | 5.1 | [x] Exists | DebateConfig.max_rounds |
| DB-004 | Role runner injection | Implementation Plan section 5.1 | `debate/roles/base.py` | test_debate_node_graph.py | 5.1 | [x] Exists | RoleRunnerProtocol |
| MUH-002 | Muhasabah gate at output boundary | TDD section 4.4; Implementation Plan section 5.2 | `debate/muhasabah_gate.py`, `debate/orchestrator.py` | test_muhasabah_gate.py, test_debate_muhasabah_integration.py | 5.2 | [x] Exists | GateDecision, StopReason.CRITICAL_DEFECT |
| API-001 | Idempotency | API section 4.1 | `idempotency.py` | test_api_idempotency_middleware.py | 2.5 | [x] Exists | request_id |
| API-002 | Error model | API section 8 | `errors.py` | test_error_model.py | 2.6 | Planned | Error responses |
| API-003 | Rate limiting | API section 4.3 | `rate_limit.py` | test_rate_limiting.py | 2.7 | Planned | 429 responses |
| WH-001 | Webhook signing | API section 6 | `webhooks/dispatcher.py`, `webhooks/signing.py`, `webhooks/retry.py` | test_slice97_webhook_dispatcher.py, test_webhook_signing.py, test_webhook_retry.py | 2.8 | [x] Delivered (Slice97) | `webhook.delivery.succeeded/failed` audit events |
| DR-001 | Data residency | Residency section 3 | `compliance/residency.py`, durable `tenants.data_region` (migration 0027, flag `IDIS_ENABLE_DURABLE_RESIDENCY`) | test_data_residency.py, test_slice98_durable_residency.py, test_slice98_durable_residency_postgres.py | 7 (Slice98) | [x] Delivered (Slice98) | Region source of truth; fail-closed 403 |
| BYOL-001 | BYOL isolation | Residency section 7 | `enrichment/service.py` | test_byol_isolation.py | 7 | Planned | EnrichmentRecord |
| OPS-001 | Ops readiness | SLO section 10 | Manual checklist | Manual | 7 | Planned | Checklist sign-off |
| PR-001 | Prompt registry | Prompt section 2; Go-Live section 4.4 | `services/prompts/registry.py`, `services/prompts/versioning.py` | test_prompt_registry.py | 7.2 | [x] Exists | prompt.version.promoted/rolledback/retired |
| EH-001 | Eval harness | Eval section 8 | CI pipeline | GDBS suites | 5/6/7 | Planned | Gate results |
| SEC-001 | Encryption / BYOK | Security section 5 | BYOK: `compliance/byok.py` (durable, migration 0029). TLS 1.2+/AES-256 remain infra-config | test_byok.py, test_slice98_byok_legal_hold_postgres.py | 0/7 (Slice98 BYOK) | [x] BYOK delivered (Slice98); TLS/AES = infra | `byok.*` audit; KMS-boundary decision note |
| SEC-002 | RBAC + ABAC | Security section 4 | `middleware/rbac.py`, `api/abac.py` (deny-by-default deal ABAC, durable stores, migration 0026) | test_abac.py, test_slice98_abac_durable_store_postgres.py, test_slice98_deal_abac_extraction.py | 2/7 (Slice98) | [x] Delivered (Slice98) | `rbac.*` audit; ABAC_DENIED_* |
| AUTH-001 | SSO/JWT + IdP-MFA verification | Security section 4 | `api/auth_sso.py` (`validate_jwt`, `amr` MFA check, flag `IDIS_REQUIRE_MFA`) | test_auth_sso.py, test_slice98_mfa_enforcement.py | 7 (Slice98) | [x] Delivered (Slice98) | `auth.mfa.failed` audit |
| BG-001 | Break-glass single-use grants | Security section 4; ADR-007 | `api/break_glass.py`, `api/break_glass_grants.py` (migration 0028, flag `IDIS_ENABLE_DURABLE_BREAK_GLASS`) | test_break_glass_audit.py, test_slice98_break_glass_workflow_postgres.py | 7 (Slice98) | [x] Delivered (Slice98) | `break_glass.issued/used` CRITICAL |
| BYOK-001 | Durable BYOK + legal holds + mgmt API | Residency section 5-6; ADR-012 | `compliance/byok.py`, `compliance/retention.py`, `routes/compliance_admin.py` (migration 0029) | test_byok.py, test_retention_hold.py, test_slice98_byok_legal_hold_postgres.py | 7 (Slice98) | [x] Delivered (Slice98) | `byok.*`, `legal_hold.*` audit |
| RET-001 | Retention enforcement janitor | Residency section 6 | `services/compliance/janitor.py` (dry-run default, double opt-in) | test_slice98_retention_janitor.py, test_slice98_retention_janitor_postgres.py | 7 (Slice98) | [x] Delivered (Slice98) | `retention.sweep.executed` HIGH |
| ERZ-001 | Per-deal erasure + per-tenant export | Residency section 6.2 | `compliance/erasure.py`, `compliance/erasure_postgres.py`, `compliance/compliance_export.py`, `routes/erasure_export.py` (migration 0030) | test_slice98_erasure_export.py, test_slice98_erasure_export_postgres.py | 7 (Slice98) | [x] Delivered (Slice98) | `erasure.*`, `export.created` audit; full deal removal, audit survives |

**Implementation Status Legend:**
- [x] Exists - Code and tests implemented in repo
- Planned - Scheduled for indicated phase gate

---

## 9) Test Coverage Matrix

### 9.1 Existing Tests (Implemented)

| Test File | Description | Phase | Status |
|-----------|-------------|-------|--------|
| `tests/test_api_health.py` | Health endpoint tests | 0 | [x] Passing |
| `tests/test_api_openapi_validation.py` | OpenAPI spec validation | 0 | [x] Passing |
| `tests/test_api_tenancy_auth.py` | Tenant authentication + isolation | 0/2 | [x] Passing |
| `tests/test_api_audit_middleware.py` | Audit middleware integration | 2.3 | [x] Passing |
| `tests/test_audit_event_validator.py` | Audit event schema validation | 2.3.1 | [x] Passing |
| `tests/test_api_idempotency_middleware.py` | Idempotency replay/collision/isolation | 2.5 | [x] Passing |
| `tests/test_no_free_facts.py` | No-Free-Facts validator | 2 | [x] Passing |
| `tests/test_muhasabah.py` | Muhasabah core tests | 5 | [x] Passing |
| `tests/test_muhasabah_validator.py` | Muhasabah validator | 5 | [x] Passing |
| `tests/test_sanad_integrity.py` | Sanad integrity tests | 3 | [x] Passing |
| `tests/test_schema_validator.py` | Schema validation utilities | 0 | [x] Passing |
| `tests/test_schema_registry.py` | Schema registry tests | 0 | [x] Passing |
| `tests/test_openapi_loader.py` | OpenAPI loader tests | 0 | [x] Passing |
| `tests/test_cli_validate.py` | CLI validation commands | 0 | [x] Passing |
| `tests/test_health.py` | Health module tests | 0 | [x] Passing |
| `tests/test_calc_reproducibility.py` | Calc engine hash stability | 4.1 | [x] Passing |
| `tests/test_calc_sanad.py` | Calc-Sanad grade derivation + tamper detection | 4.1 | [x] Passing |
| `tests/test_postgres_rls_and_audit_immutability.py` | RLS tenant isolation (incl. calc tables) | 2/4.1 | [x] Passing |
| `tests/test_extraction_gate.py` | Extraction confidence gate (fail-closed) | 4.2 | [x] Passing |
| `tests/test_debate_node_graph.py` | Debate node graph order matches v6.3 | 5.1 | [x] Passing |
| `tests/test_debate_stop_conditions.py` | Stop condition priority order + max rounds | 5.1 | [x] Passing |
| `tests/test_muhasabah_gate.py` | Muhasabah gate blocking/allowing tests | 5.2 | [x] Passing |
| `tests/test_debate_muhasabah_integration.py` | Orchestrator + gate integration tests | 5.2 | [x] Passing |

### 9.2 Planned Tests (By Phase Gate)

| Test File | Description | Phase Gate | Module Dependency |
|-----------|-------------|------------|-------------------|
| `tests/test_audit_coverage.py` | All mutations emit audit events | 2.3+ | `audit.py` (exists) |
| `tests/test_audit_immutability.py` | Audit logs are append-only | 2.3+ | `audit.py` (exists) |
| `tests/test_error_model.py` | Error responses match schema | 2.6 | `errors.py` (exists) |
| `tests/test_rate_limiting.py` | Rate limits enforced | 2.7 | `rate_limit.py` (planned) |
| `tests/test_webhook_signing.py` | HMAC signature generation | 2.8 | `webhooks/signing.py` (exists) |
| `tests/test_webhook_retry.py` | Exponential backoff retry | 2.8 | `webhooks/retry.py` (exists) |
| `tests/test_sanad_grade_algorithm.py` | Normative grading algorithm | 3 | `sanad/grader.py` (planned) |
| `tests/test_independence_rules.py` | Corroboration independence | 3 | `sanad/independence.py` (planned) |
| `tests/test_defect_severity.py` | FATAL/MAJOR/MINOR rules | 3 | `defects/service.py` (planned) |
| `tests/test_defect_cure_protocol.py` | Cure workflows | 3 | `defects/service.py` (planned) |
| `tests/test_defect_waiver.py` | Defect waiver process | 3 | `defects/service.py` (planned) |
| `tests/test_sanad_coverage.py` | Material claims have Sanad | 3 | `sanad/` (planned) |
| `tests/test_calc_reproducibility.py` | Same inputs -> same hash | 4 | `calc/engine.py` [x] |
| `tests/test_calc_sanad.py` | Calc provenance to claim_ids | 4 | `calc_sanad.py` [x] |
| `tests/test_extraction_gate.py` | Blocks low-confidence calcs | 4.2 | `extraction_gate.py` [x] |
| `tests/test_fail_closed.py` | Validators fail closed | 0+ | All validators |
| `tests/test_tenant_rls.py` | Postgres RLS enforcement | 7 | Database config |
| `tests/test_cache_tenant_keying.py` | Cache tenant isolation | 7 | Cache layer |
| `tests/test_data_residency.py` | Region pinning enforced | 7 | `tenant.py` (planned) |
| `tests/test_byol_isolation.py` | No cross-tenant enrichment | 7 | `enrichment/service.py` (planned) |
| `tests/test_rbac.py` | Role-based access control | 2/7 | `auth.py` (partial) |
| `tests/test_encryption.py` | TLS enforced | 0/7 | Infra config |
| `tests/test_byok.py` | Customer keys used | 7 | KMS integration |
| `tests/test_prompt_registry.py` | Prompt version loading | 5/6/7 | `prompts/` (planned) |

### 9.3 Planned Code Modules (By Phase Gate)

| Module | Description | Phase Gate | Status |
|--------|-------------|------------|--------|
| `src/idis/api/middleware/audit.py` | Audit middleware | 2.3 | [x] Exists |
| `src/idis/api/middleware/idempotency.py` | Idempotency middleware | 2.5 | [x] Exists |
| `src/idis/api/middleware/rate_limit.py` | Rate limiting middleware | 2.7 | Planned |
| `src/idis/api/middleware/tenant.py` | Tenant context middleware | 2.1 | Planned (partial in openapi_validate) |
| `src/idis/api/middleware/auth.py` | Auth middleware (JWT/API key) | 2.1 | Planned (partial in auth.py) |
| `src/idis/models/sanad.py` | Sanad + TransmissionNode | 3 | Planned |
| `src/idis/models/defect.py` | Defect model | 3 | Planned |
| `src/idis/models/calc_sanad.py` | CalcSanad model | 4 | Planned |
| `src/idis/models/tenant.py` | Tenant model with data_region | 7 | Planned |
| `src/idis/services/sanad/grader.py` | Sanad grading service | 3 | Planned |
| `src/idis/services/sanad/independence.py` | Independence checker | 3 | Planned |
| `src/idis/services/defects/service.py` | Defect service | 3 | Planned |
| `src/idis/services/webhooks/service.py` | Webhook service | 2.8 | [x] Delivered (Slice97: emitter + durable outbox + dispatcher) |
| `src/idis/services/enrichment/service.py` | Enrichment service | 7 | Planned |
| `src/idis/calc/engine.py` | Calculation engine | 4 | Planned |

---

## 10) Revision History

| Date | Version | Author | Changes |
|------|---------|--------|---------|
| 2026-01-07 | 1.0 | Cascade | Initial creation from v6.3 docs consolidation |
| 2026-01-07 | 1.1 | Cascade | Added Implementation Status column; hard/soft gate classification; corrected test coverage matrix to reflect actual repo state; added planned tests/modules by phase gate |
| 2026-01-09 | 1.2 | Cascade | Added Phase 3.3 Sanad Methodology v2 traceability (SAN-003 through SAN-008); updated SAN-001, SAN-002, DEF-001 to Exists status |
| 2026-01-10 | 1.3 | Cascade | Added Phase 4.1 Deterministic Calc Engine traceability (DN-001 through DN-004); added calc tests to test coverage matrix |
| 2026-01-10 | 1.4 | Cascade | Added Phase 4.2 Extraction Confidence Gate (FC-001 updated); test_extraction_gate.py::test_low_confidence_blocked implemented |
| 2026-01-10 | 1.5 | Cascade | Added Phase 5.1 LangGraph Orchestration Core traceability (DB-001 through DB-004); added debate tests to test coverage matrix |
| 2026-01-11 | 1.6 | Cascade | Added Phase 5.2 Muhasabah Gate traceability (MUH-002); updated MUH-001 with gate enforcement; added test_muhasabah_gate.py and test_debate_muhasabah_integration.py to test coverage matrix |
| 2026-01-11 | 1.7 | Cascade | Added Phase POST-5.2 Architecture Hardening: ValueStruct types (VS-001), Claim types & calc loop guardrail (CLT-001), NFF semantic extensions (NFF-002), Graph-Postgres saga (DW-001), Pattern matching spec (PM-001) |
| 2026-01-11 | 1.8 | Cascade | DOC-ALIGN-001: Clarified claim_class vs claim_type distinction; updated CLT-001 with invariants and key fields; aligned with Data Model section 5.5 |
| 2026-01-11 | 1.9 | Cascade | Added Phase 6.1 Deliverables Generator traceability (DG-001 through DG-004); added test coverage for screening snapshot, IC memo, deliverable validator, export formats |

---

## 11) Phase POST-5.2 Traceability (Architecture Hardening)

### 11.1 ValueStruct Type Hierarchy

| Attribute | Value |
|-----------|-------|
| **Requirement ID** | VS-001 |
| **Requirement** | Typed value structures for claims/calcs replacing untyped dict |
| **Source Doc** | Data Model section 5.4 |
| **Enforcing Component** | `src/idis/models/value_structs.py` |
| **Tests** | `tests/test_value_structs.py`, `tests/test_calc_value_types_integration.py` |
| **Phase Gate** | POST-5.2 |
| **Implementation Status** | [x] Exists |

### 11.2 Claim Lineage Type & Calc Loop Guardrail

| Attribute | Value |
|-----------|-------|
| **Requirement ID** | CLT-001 |
| **Requirement** | `claim_class` (category: FINANCIAL, etc.) vs `claim_type` (lineage: PRIMARY/DERIVED); derived claims cannot auto-trigger calcs; `source_calc_id` required for derived |
| **Source Doc** | Data Model section 5.5 |
| **Enforcing Component** | `src/idis/models/claim.py` - `Claim`, `ClaimClass`, `ClaimType`, `CalcLoopGuard`, `CalcLoopGuardError` |
| **Key Fields** | `claim_class` (category), `claim_type` (lineage), `source_calc_id` (for derived claims) |
| **Invariants** | CLG-1: PRIMARY claims trigger calcs; CLG-2: DERIVED cannot auto-trigger; CLG-3: Violation is fail-closed |
| **Tests** | `tests/test_claim_type_enforcement.py`, `tests/test_calc_loop_guardrail.py` |
| **Phase Gate** | POST-5.2 |
| **Implementation Status** | [x] Exists |

### 11.3 No-Free-Facts Semantic Extensions

| Attribute | Value |
|-----------|-------|
| **Requirement ID** | NFF-002 |
| **Requirement** | Semantic subject-predicate patterns for enhanced factual detection |
| **Source Doc** | Data Model section 5.7 |
| **Enforcing Component** | `src/idis/validators/no_free_facts.py` - SEMANTIC_RULES |
| **Tests** | `tests/test_no_free_facts_semantic_cases.py` |
| **Phase Gate** | POST-5.2 |
| **Implementation Status** | [x] Exists |

### 11.4 Graph-Postgres Dual-Write Consistency

| Attribute | Value |
|-----------|-------|
| **Requirement ID** | DW-001 |
| **Requirement** | Saga pattern for Postgres + Graph DB consistency with compensation |
| **Source Doc** | Data Model section 5.6 |
| **Enforcing Component** | `src/idis/persistence/saga.py` - DualWriteSagaExecutor |
| **Tests** | `tests/test_graph_postgres_consistency_saga.py` |
| **Phase Gate** | POST-5.2 |
| **Implementation Status** | [x] Exists |

### 11.5 Pattern Matching (Spec Only)

| Attribute | Value |
|-----------|-------|
| **Requirement ID** | PM-001 |
| **Requirement** | DealOutcome, SimilarityFeature, PatternMatch models for deal comparison |
| **Source Doc** | Implementation Plan section Phase 6.5 |
| **Enforcing Component** | N/A (spec only) |
| **Tests** | Planned: `test_deal_outcome.py`, `test_similarity_feature.py`, `test_pattern_match.py` |
| **Phase Gate** | Phase 6.5 (future) |
| **Implementation Status** | Spec documented, implementation pending |

---

## 12) Phase 6.1 Traceability (Deliverables Generator)

### 12.1 Deliverables Generator Core

| Attribute | Value |
|-----------|-------|
| **Requirement ID** | DG-001 |
| **Requirement** | Evidence-linked deliverables generator: Screening Snapshot, IC Memo, PDF/DOCX export |
| **Source Doc** | Implementation Plan section Phase 6; Go-Live section Phase 6; Requirements section 8 |
| **Source Section** | "Deliverables Generator", "Every fact in memo has claim_id/calc_id reference" |
| **Enforcing Component** | `src/idis/deliverables/screening.py` - ScreeningSnapshotBuilder |
| | `src/idis/deliverables/memo.py` - ICMemoBuilder |
| | `src/idis/deliverables/export.py` - DeliverableExporter |
| **Secondary Enforcement** | `src/idis/validators/deliverable.py` - DeliverableValidator |
| **Tests** | `tests/test_screening_snapshot.py` - all facts include refs |
| | `tests/test_ic_memo.py` - sections evidence-linked, dissent has refs |
| | `tests/test_export_formats.py` - PDF/DOCX generation |
| **Phase Gate** | Phase 6.1 |
| **Evidence Artifact** | `deliverable.exported` audit event; audit appendix in export |
| **Implementation Status** | [x] Exists |

**Deliverables Object Model:**
| Type | Description |
|------|-------------|
| `DeliverableFact` | Fact with `claim_refs`, `calc_refs`, `is_factual`, `is_subjective` |
| `DeliverableSection` | Section containing multiple facts |
| `ScreeningSnapshot` | Partner-ready one-pager with metrics, red flags, missing info |
| `ICMemo` | Full IC memo with all sections + dissent + truth dashboard summary |
| `AuditAppendix` | Evidence appendix with sorted refs |
| `DissentSection` | Structured dissent with refs (required when stable dissent exists) |

---

### 12.2 No-Free-Facts at Export (Hard Gate)

| Attribute | Value |
|-----------|-------|
| **Requirement ID** | DG-002 |
| **Requirement** | Every DeliverableFact with is_factual=True must have non-empty claim_refs; export blocked on violation |
| **Source Doc** | TDD section 1.1; Go-Live section Phase 6; Implementation Plan section Phase 6 |
| **Source Section** | "No-Free-Facts at deliverable export (hard gate)" |
| **Enforcing Component** | `src/idis/validators/deliverable.py` - validate_deliverable_no_free_facts() |
| **Secondary Enforcement** | `src/idis/deliverables/export.py` - validation before export |
| **Tests** | `tests/test_deliverable_no_free_facts.py` - factual without refs raises |
| | `tests/test_deliverable_no_free_facts.py` - valid deliverable passes |
| **Phase Gate** | Phase 6.1 |
| **Evidence Artifact** | Validation errors with stable code `NO_FREE_FACTS_UNREFERENCED_FACT` |
| **Implementation Status** | [x] Exists |

**Validator Rules (Normative):**
| Rule | Condition | Action |
|------|-----------|--------|
| Factual without refs | `is_factual=True` AND `claim_refs` empty AND `calc_refs` empty | REJECT |
| Subjective skip | `is_subjective=True` | ALLOW (skip validation) |
| Subjective section skip | `section.is_subjective=True` | ALLOW all facts in section |
| Dissent missing refs | `dissent_section` has empty `claim_refs` | REJECT at build time |

---

### 12.3 Audit Appendix Requirement

| Attribute | Value |
|-----------|-------|
| **Requirement ID** | DG-003 |
| **Requirement** | Exports include audit appendix with all unique refs (stable ordering) |
| **Source Doc** | Implementation Plan section Phase 6; Requirements section 8.2 |
| **Source Section** | "Exports include audit appendix (optional) for compliance" |
| **Enforcing Component** | `src/idis/models/deliverables.py` - AuditAppendix, AuditAppendixEntry |
| | `src/idis/deliverables/export.py` - _render_audit_appendix_text() |
| **Tests** | `tests/test_screening_snapshot.py::test_audit_appendix_contains_all_refs` |
| | `tests/test_screening_snapshot.py::test_audit_appendix_entries_sorted` |
| | `tests/test_export_formats.py::test_pdf_includes_audit_appendix_text` |
| **Phase Gate** | Phase 6.1 |
| **Evidence Artifact** | Audit appendix section in PDF/DOCX exports |
| **Implementation Status** | [x] Exists |

**Stable Ordering Rules:**
| Element | Ordering |
|---------|----------|
| `claim_refs` within fact | Lexicographically sorted |
| `calc_refs` within fact | Lexicographically sorted |
| `AuditAppendixEntry` list | Sorted by `(ref_type, ref_id)` |

---

### 12.4 Dissent Section Handling

| Attribute | Value |
|-----------|-------|
| **Requirement ID** | DG-004 |
| **Requirement** | If debate state indicates stable dissent, include as structured section with explicit refs |
| **Source Doc** | Implementation Plan section Phase 5; Requirements section 6.3 |
| **Source Section** | "Stable dissent produces deliverables with dissent section" |
| **Enforcing Component** | `src/idis/models/deliverables.py` - DissentSection |
| | `src/idis/deliverables/memo.py` - ICMemoBuilder.set_dissent() |
| **Tests** | `tests/test_ic_memo.py::test_stable_dissent_produces_dissent_section` |
| | `tests/test_ic_memo.py::test_empty_dissent_refs_rejected` |
| **Phase Gate** | Phase 6.1 |
| **Evidence Artifact** | DissentSection in ICMemo with claim_refs |
| **Implementation Status** | [x] Exists |

---

## 13) Phase 6.2 - Frontend Backend Contracts Traceability

### 13.1 Truth Dashboard API

| Attribute | Value |
|-----------|-------|
| **Requirement ID** | FE-001 |
| **Requirement** | GET /v1/deals/{dealId}/truth-dashboard returns aggregated claim statistics and paginated claims |
| **Source Doc** | Implementation Plan section Phase 6.2; Roadmap section 6.2 |
| **Source Section** | "Frontend Backend Contracts - Truth Dashboard API" |
| **Enforcing Component** | `src/idis/api/routes/claims.py` - get_deal_truth_dashboard() |
| **Secondary Enforcement** | `src/idis/api/policy.py` - getDealTruthDashboard RBAC rule |
| **Tests** | `tests/test_api_truth_dashboard.py::test_returns_200_with_correct_schema` |
| | `tests/test_api_truth_dashboard.py::test_summary_counts_match_seeded_data` |
| | `tests/test_api_truth_dashboard.py::test_stable_ordering_determinism` |
| | `tests/test_api_truth_dashboard.py::test_cross_tenant_access_blocked` |
| **Phase Gate** | Phase 6.2 |
| **Evidence Artifact** | TruthDashboard response with summary (by_grade, by_verdict, fatal_defects) |
| **Implementation Status** | [x] Exists |

**Response Schema (TruthDashboard):**
| Field | Type | Description |
|-------|------|-------------|
| `deal_id` | uuid | Deal UUID |
| `summary.total_claims` | integer | Total claim count |
| `summary.by_grade` | object | {A, B, C, D} counts |
| `summary.by_verdict` | object | {VERIFIED, INFLATED, CONTRADICTED, UNVERIFIED, SUBJECTIVE} counts |
| `summary.fatal_defects` | integer | Fatal defect count |
| `claims` | PaginatedClaimList | Paginated claims (sorted by claim_id) |

---

### 13.2 Claim Detail API

| Attribute | Value |
|-----------|-------|
| **Requirement ID** | FE-002 |
| **Requirement** | GET /v1/claims/{claimId} returns full claim body with tenant isolation |
| **Source Doc** | Implementation Plan section Phase 6.2; Roadmap section 6.2 |
| **Source Section** | "Frontend Backend Contracts - Claim Detail API" |
| **Enforcing Component** | `src/idis/api/routes/claims.py` - get_claim() |
| **Secondary Enforcement** | `src/idis/api/policy.py` - getClaim RBAC rule |
| **Tests** | `tests/test_api_claim_detail_and_sanad.py::test_returns_200_with_correct_body` |
| | `tests/test_api_claim_detail_and_sanad.py::test_cross_tenant_read_blocked` |
| | `tests/test_api_claim_detail_and_sanad.py::test_claim_not_found_returns_404` |
| **Phase Gate** | Phase 6.2 |
| **Evidence Artifact** | ClaimResponse with claim_id, corroboration, defect_ids, verdict |
| **Implementation Status** | [x] Exists |

---

### 13.3 Sanad Chain API

| Attribute | Value |
|-----------|-------|
| **Requirement ID** | FE-003 |
| **Requirement** | GET /v1/claims/{claimId}/sanad returns transmission chain with deterministic ordering |
| **Source Doc** | Implementation Plan section Phase 6.2; Roadmap section 6.2 |
| **Source Section** | "Frontend Backend Contracts - Sanad Chain API" |
| **Enforcing Component** | `src/idis/api/routes/claims.py` - get_claim_sanad() |
| **Secondary Enforcement** | `src/idis/api/policy.py` - getClaimSanad RBAC rule |
| **Tests** | `tests/test_api_claim_detail_and_sanad.py::test_returns_200_with_chain_structure` |
| | `tests/test_api_claim_detail_and_sanad.py::test_stable_ordering_for_transmission_chain` |
| | `tests/test_api_claim_detail_and_sanad.py::test_cross_tenant_read_blocked` |
| **Phase Gate** | Phase 6.2 |
| **Evidence Artifact** | SanadResponse with transmission_chain sorted by node_id |
| **Implementation Status** | [x] Exists |

**Determinism Guarantee:**
| Aspect | Rule |
|--------|------|
| Transmission chain ordering | Sorted by `node_id` (lexicographic) |
| Multiple calls | Identical JSON response |
| No randomness | No uuid4/datetime.now in response path |
