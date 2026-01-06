# IDIS Data Residency & Compliance Operating Model (v6.3)
**Version:** 6.3 (derived from IDIS v6.3 FINAL)  
**Date:** 2026-01-06  
**Status:** Implementation-ready baseline for enterprise contracts and architecture decisions  
**Audience:** Backend, SRE/DevOps, Security/Compliance, Product

---

## 0) Purpose

This document defines the **data residency, privacy, and compliance operating model** for IDIS (VC Edition) to ensure the platform can be deployed and sold as an enterprise-grade system.

This doc is deliberately **implementation-focused**:
- where data is stored (by class)
- how it is isolated (tenant boundaries)
- how it is encrypted (KMS/BYOK)
- retention + legal hold
- BYOL provider licensing controls
- audit requirements tied to compliance expectations (SOC2 trajectory)

---

## 1) Key Compliance Posture (v6.3 baseline)

### 1.1 Principles
- **Tenant isolation is non-negotiable** (data access and storage boundaries).
- **No training on customer data** (unless explicitly contracted).
- **Auditability is mandatory** (immutability + traceability).
- **Least privilege** (RBAC + deal-level ABAC).
- **Data minimization** (store only what is needed; redact logs).

### 1.2 Compliance Targets (phased)
- **Phase 1:** SOC2 readiness (controls, evidence, audit logs, access policy, change control).
- **Phase 2:** SOC2 Type II (operational evidence collection over time).
- **Phase 2+:** ISO 27001 alignment (optional depending on buyer demand).

**Note:** The specific certifications are business decisions, but the operating model must support them.

---

## 2) Data Classification (Operational)

IDIS uses the following data classes (aligned to security doc):

- **Class 0 (Public):** marketing / public docs
- **Class 1 (Internal):** non-sensitive metadata, non-tenant logs
- **Class 2 (Confidential Tenant):** deal metadata, claims, sanad graph, debate transcripts
- **Class 3 (Highly Confidential Tenant):** data room docs, financials, cap tables, bank statements, contracts, term sheets

**Rule:** Class 2/3 data must never appear in unredacted logs or telemetry.

---

## 3) Data Residency Model

### 3.1 Tenant-level Data Region
Each tenant is assigned a `data_region` at onboarding:
- Example: `me-south-1`, `eu-west-1`, `us-east-1`

All tenant resources must remain within that region unless:
- tenant explicitly opts into multi-region replication
- required for disaster recovery under contract

### 3.2 Storage Residency Rules
- **Raw documents (Class 3):** stored only in tenant region object store.
- **Derived artifacts (Class 2/3):** stored only in tenant region.
- **Indexes/embeddings:** stored only in tenant region; must be tenant-scoped.

### 3.3 Cross-Region Operations
Permitted only for:
- control-plane metadata (Class 1) that contains no customer content
- global service configuration
- aggregated anonymized metrics (opt-in)

**Forbidden by default:**
- moving raw docs or deliverables out of tenant region
- cross-region retrieval of claim corpora

---

## 4) Tenant Isolation (Storage and Compute)

### 4.1 Storage Boundaries
**Postgres:**
- `tenant_id` column on all tables
- Row Level Security (RLS) enforced
- Optional separate schema per tenant for high-end tiers

**Object store:**
- `s3://idis-<region>/tenants/<tenant_id>/...` prefix model
- IAM policies restricted by prefix
- Pre-signed URLs are short-lived and scoped

**Graph DB / Vector Store:**
- Tenant-scoped labels/partitions OR dedicated instances depending on plan
- Strict query scoping by `tenant_id`

### 4.2 Compute Boundaries
- Request context includes `tenant_id` and is enforced at middleware
- Worker jobs always carry `tenant_id` and only access tenant resources
- Caches must be tenant-keyed; shared caches without tenant-keying are forbidden

---

## 5) Encryption and Key Management

### 5.1 At Rest
- Object store: SSE-KMS
- Databases: disk encryption + optional column-level encryption for Class 3 fields
- Backups: encrypted with separate KMS keys

### 5.2 In Transit
- TLS 1.2+ (TLS 1.3 preferred)
- mTLS for internal service-to-service where feasible

### 5.3 BYOK (Bring Your Own Key) — Enterprise Option
- Tenant may supply KMS key alias
- IDIS uses tenant key for:
  - raw document encryption
  - deliverable encryption
  - sensitive field encryption
- Key rotation supported; key revocation must lock tenant content access until re-keyed.

---

## 6) Data Retention, Deletion, and Legal Hold

### 6.1 Default Retention
- Raw documents: retained while tenant is active, unless tenant defines retention window
- Deliverables: retained per tenant policy (default 3–7 years)
- Audit events: default 7 years (configurable)

### 6.2 Deletion Semantics
**Two modes:**
1. **Soft delete:** hides from UI; preserved for audit/legal
2. **Hard delete:** irreversible removal; requires tenant admin approval and is audited

Hard delete must:
- remove or tombstone Postgres rows
- delete object store artifacts
- delete embeddings/vector entries
- delete graph nodes/edges
- retain audit event references (without payload) unless contract requires deletion

### 6.3 Legal Hold
- Tenant admin can apply legal hold to:
  - a deal
  - a set of artifacts
- Held items cannot be deleted until hold is lifted.
- All hold actions are audited with `CRITICAL` severity.

---

## 7) BYOL Provider Licensing Controls (Critical for enterprise)

### 7.1 Problem
Many enrichment providers have strict licensing. IDIS must prevent:
- redistribution across tenants
- “training” on licensed content
- co-mingling of provider data

### 7.2 Controls (Normative)
- Each `EnrichmentRecord` includes:
  - `provider`, `license_type`, `tenant_id`, `retrieved_at`, `method`
- Provider data is stored in tenant-scoped storage only.
- Provider data must never be included in:
  - cross-tenant benchmarks
  - global corpora
  - shared embeddings

### 7.3 Audit
Every enrichment call emits:
- `enrichment.requested`
- `enrichment.completed`
with provider identity and cost metadata (where applicable).

---

## 8) Privacy and PII/PHI Handling

### 8.1 PII
PII may exist in:
- founder documents
- customer contracts
- cap tables

**Controls:**
- PII redaction for logs
- Access restrictions by role
- Optional DLP scanning for outbound deliverables
- Support data subject deletion requests if applicable (GDPR-like)

### 8.2 PHI (unlikely but possible)
If a tenant processes healthcare deals, documents may contain PHI.
IDIS must support:
- HIPAA-like controls as an enterprise option (separate plan)
- additional access logging and redaction

---

## 9) Compliance Controls (SOC2-aligned baseline)

This section defines controls you can implement immediately to support SOC2 readiness.

### 9.1 Access Control
- SSO required for enterprise
- MFA required
- RBAC roles enforced server-side
- Deal-level ABAC for confidentiality
- Quarterly access reviews (process + evidence)

### 9.2 Change Management
- All changes require:
  - PR review
  - CI tests (evaluation harness)
  - approval gates for high-risk prompts/calcs
- Prompt registry with semver + rollback

### 9.3 Logging and Monitoring
- Centralized logs with redaction
- Audit event taxonomy enforced
- Alerting for:
  - No-Free-Facts violations
  - missing audit events
  - tenant isolation anomalies

### 9.4 Vendor Management
- Document external providers used (IdP, cloud, enrichment)
- Maintain security posture statements
- Track provider incidents affecting IDIS

---

## 10) Implementation Checklist (Actionable)

- Implement `tenant.data_region` at onboarding
- Enforce region selection for:
  - Postgres
  - object store
  - graph DB
  - vector store
- Add RLS policies in Postgres
- Add object store prefix IAM controls per tenant
- Add BYOK integration hooks (optional)
- Add retention policy engine + legal hold system
- Add enrichment licensing guardrails
- Add DLP/redaction pipeline for logs and exports (optional)
- Add compliance evidence collection:
  - access logs
  - change approvals
  - audit event coverage reports

---

## 11) Definition of Done

Data residency & compliance model is considered implemented when:
- tenant region selection is enforced for all storage and compute
- tenant isolation is proven by tests (no cross-tenant access)
- encryption is enabled (in transit + at rest)
- retention and legal holds are supported
- BYOL provider data cannot leak across tenants
- audit events capture all critical compliance actions

