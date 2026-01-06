# IDIS Security Threat Model & Security Architecture (v6.3)
**Version:** 6.3 (derived from IDIS v6.3 FINAL)  
**Date:** 2026-01-06  
**Status:** Normative baseline for production-grade build  
**Audience:** Security Engineering, Backend, DevOps/SRE, Compliance, Product

---

## 0) Purpose

This document defines an enterprise-grade security architecture and threat model for **IDIS (VC Edition)** based on the v6.3 specification, with particular emphasis on the platform’s trust primitives:

- **No‑Free‑Facts** enforcement (claims/calc provenance)
- **Sanad chain governance** (evidence + defects + grading)
- **Muḥāsabah gate** (self-audit validator)
- **Deterministic numeric engines** (Calc‑Sanad)
- **Auditability** and **human approval gates**

The goal is to build IDIS “right and clean” from day one: secure multi-tenant system design, correct access control, immutable audit trails, and a practical threat model tied to concrete mitigations.

---

## 1) Security Objectives (Non‑negotiable)

### 1.1 Confidentiality
- Tenant data must be isolated; cross-tenant access must be impossible by design.
- Sensitive deal content (data rooms, financials, term sheets) must be protected from:
  - unauthorized internal access
  - external breaches
  - accidental leakage (misconfigurations)
- Enrichment provider licenses must not cause data redistribution or leakage across tenants.

### 1.2 Integrity
- Claims, Sanad chains, Calc‑Sanad outputs, debate transcripts, and deliverables must be tamper-evident.
- Any override must be explicitly recorded and auditable.
- Deterministic calculations must be reproducible; inputs/outputs must be verifiable.

### 1.3 Availability
- Platform should operate with defined SLOs for:
  - deal intake
  - pipeline runs
  - deliverable generation
  - audit retrieval
- Resilience against spikes (batch imports, ingestion bursts), partial outages, and provider downtime.

### 1.4 Non‑repudiation and Traceability
- Every mutating action must produce an audit event with actor identity, request ID, and before/after changes.
- Human gates and overrides must be attributable and immutable.

---

## 2) System Context and Trust Boundaries

### 2.1 Actors
- **Users:** Analysts, Partners, IC members, Admins, Auditors
- **Service Accounts:** Integration service, ingestion workers, calc runners, orchestrator
- **External Systems:** CRM (DealCloud/Affinity/Salesforce), doc sources (DocSend/Drive/SharePoint/Dropbox), enrichment providers, IdP (Okta/Azure AD)

### 2.2 Trust Boundaries
1. **Public Internet boundary:** user browser/app ↔ API gateway
2. **Tenant boundary:** request context must always bind to one tenant_id
3. **Data lake boundary:** raw documents in object store, encrypted, access controlled
4. **Computation boundary:** LLM/agent execution vs deterministic calc zone
5. **External provider boundary:** BYOL sources with contractual constraints and rate limits

---

## 3) Data Classification and Handling

### 3.1 Data Classes
- **Class 0: Public**  
  Marketing collateral, non-sensitive product docs.
- **Class 1: Internal**  
  System metadata, logs without customer content, non-sensitive configuration.
- **Class 2: Confidential (Tenant)**  
  Deal metadata, claims, Sanad graph, transcripts, deliverables.
- **Class 3: Highly Confidential (Tenant)**  
  Data room content, financials, cap tables, term sheets, bank statements, founder/customer contracts.

### 3.2 Handling Rules (Mandatory)
- Class 2/3 data must be:
  - encrypted at rest and in transit
  - access-controlled by RBAC + tenant_id enforcement
  - redacted in logs
  - included in audit logs only as references/hashes (avoid raw content)
- Only limited roles may access Class 3:
  - Analysts/Partners assigned to deal; Admin by explicit break-glass; Auditor read-only.

---

## 4) Identity, Authentication, and Authorization

### 4.1 Authentication
- **SSO (OIDC/SAML)** via Okta/Azure AD.
- **JWT** for user sessions, includes:
  - `tenant_id`, `user_id`, `roles`, optional `data_region`, optional `policy_tags`.
- **API Keys** for service-to-service calls (short-lived rotated keys or mTLS).

### 4.2 Authorization (RBAC + ABAC)
**RBAC roles (minimum):**
- ANALYST
- PARTNER
- IC_MEMBER
- ADMIN
- AUDITOR
- INTEGRATION_SERVICE

**ABAC constraints (mandatory):**
- Deal-level access (assignment or group membership).
- Optional ethical wall constraints (fund separation, matter separation).

**Access enforcement pattern:**
- Every request requires:
  - `tenant_id` bound at auth layer
  - `policy_check(actor, action, resource, tenant_id, deal_id)` executed in middleware
- Deny by default.

### 4.3 Break-glass Access
- Admin break-glass requires:
  - justification
  - time-bound token
  - dual approval (optional)
  - explicit audit event with severity=HIGH

---

## 5) Encryption and Key Management

### 5.1 In Transit
- TLS 1.2+ (TLS 1.3 preferred)
- HSTS on all web frontends
- mTLS for internal service-to-service where feasible

### 5.2 At Rest
- Object store: SSE-KMS with per-tenant keys where possible
- Postgres: disk encryption + column-level encryption for specific fields (Class 3)
- Graph DB: encryption at rest (vendor-specific)
- Backups: encrypted with separate KMS key

### 5.3 BYOK (Bring Your Own Key) — Optional Enterprise Feature
- Tenant may supply KMS key alias; IDIS uses it for:
  - raw document encryption
  - deliverable encryption
  - sensitive field encryption

### 5.4 Secrets Management
- No secrets in code or config files.
- Use AWS Secrets Manager / Vault:
  - rotate OAuth tokens
  - rotate integration keys
  - rotate database credentials
- Support scoped secrets per tenant.

---

## 6) Multi‑Tenant Isolation Model

### 6.1 Recommended Model (Phase 1–2)
- Single cluster, shared services
- **Logical tenant isolation**:
  - tenant_id required in every table
  - row-level security (RLS) in Postgres
  - per-tenant KMS keys (where feasible)
  - per-tenant object storage prefixes + IAM scoping
- Strict request-scoped tenant context in middleware.

### 6.2 Strong Isolation Model (Enterprise/Regulated)
- Separate namespaces per tenant
- Optional dedicated DB schema per tenant
- Optional dedicated graph DB instance per tenant (high-end plan)
- Optional dedicated encryption keys and audit stores.

### 6.3 Hard Rules
- No cross-tenant queries.
- No shared caches without tenant keying.
- No shared “vector retrieval” across tenants.

---

## 7) Threat Model (STRIDE)

This section enumerates key threats and mitigations. Each mitigation should be implementable and testable.

### 7.1 Spoofing Identity
**Threats**
- Stolen JWT, session hijacking
- API key leakage

**Mitigations**
- MFA + SSO
- Short-lived JWT + refresh tokens
- Device/session binding
- API key rotation and scoped privileges
- mTLS for internal services

### 7.2 Tampering
**Threats**
- Modification of claims/Sanad chains to mislead IC
- Altering calc outputs
- Editing audit logs

**Mitigations**
- Immutable audit log store (append-only)
- Content hashes on:
  - raw docs (sha256)
  - evidence spans (hash pointers)
  - Calc‑Sanad formula hash + reproducibility hash
- Signed deliverables (optional)
- Strict role-based write access:
  - Only system or verified human gate can change verdict/grade
- Integrity checks on read: compare stored hash.

### 7.3 Repudiation
**Threats**
- User denies changing a claim, approving an override

**Mitigations**
- Audit events include actor_id, role, tenant_id, request_id, idempotency key, timestamp
- For high-risk actions: require justification and optional second approval
- Store signature of webhook deliveries and human approvals.

### 7.4 Information Disclosure
**Threats**
- Cross-tenant leakage
- Doc links shared incorrectly
- Logs containing excerpts
- Prompt injection causing agents to exfiltrate sensitive content

**Mitigations**
- Tenant middleware enforcement + RLS
- Redaction policies for logs
- Access-controlled signed URLs for deliverables
- Prompt/tool policy:
  - tools must enforce “allowed retrieval scope” (tenant+deal)
  - “No external calls” from agent runtime unless explicitly allowed
- DLP scanning (optional) for outbound content

### 7.5 Denial of Service
**Threats**
- Large batch ingestion overload
- OCR floods
- API abuse

**Mitigations**
- Rate limits per tenant and per user/service
- Queues with backpressure (SQS/Kafka)
- Separate worker pools for OCR (expensive)
- Circuit breakers for provider APIs
- WAF + bot protections

### 7.6 Elevation of Privilege
**Threats**
- Analyst escalates to partner
- Vulnerable integration token used to access other tenants

**Mitigations**
- RBAC enforcement server-side only
- Fine-grained integration scopes
- Token vault with tenant scoping
- Security reviews and least privilege IAM
- Regular permission audits

---

## 8) Application Security Controls

### 8.1 Input Validation
- Validate all inbound payloads against JSON Schema (claim creation, updates).
- Reject malformed `Idempotency-Key`, path traversal in URIs, dangerous file types.

### 8.2 File Ingestion Security
- Malware scanning on uploaded/fetched files
- File type whitelisting
- Sandbox parsing for untrusted formats
- OCR only for images/PDFs with controlled resource limits

### 8.3 Prompt Injection Mitigation (Agent Runtime)
- Never allow agents to directly execute arbitrary code.
- Tools must:
  - enforce tenant+deal scoping
  - return only allowed fields (no raw doc dumps by default)
- Retrieval limits:
  - cap excerpt lengths
  - use structured claims over long text
- Output validators:
  - No‑Free‑Facts (reject unsupported facts)
  - Muḥāsabah validator (reject missing falsifiers/uncertainty when required)

### 8.4 Dependency and Supply Chain
- SCA (dependency scanning) in CI
- Signed containers
- SBOM generation

---

## 9) Audit Logging (Security Requirements)

**See companion document:** `IDIS_Audit_Event_Taxonomy_v6_3.md` (produced next to this file).

Core requirements:
- Audit log is append-only; cannot be edited by users.
- Every mutating request emits an audit event with:
  - actor, tenant, resource, action, before/after diff pointers, timestamp
- Include override decisions and human verification actions.

---

## 10) Incident Response and Recovery

### 10.1 Incident Severity
- SEV1: cross-tenant leakage, key compromise, audit log tampering
- SEV2: partial data exposure within tenant, persistent downtime, corrupted outputs
- SEV3: degraded performance, non-critical integration failures

### 10.2 Runbooks (minimum)
- Token revocation and forced logout
- Key rotation and object store re-encryption process
- Restore from backups (RPO/RTO defined by SLO doc)
- Containment and notification (legal/compliance)

---

## 11) Security Testing Plan (Build Gates)

### 11.1 Required before production
- Threat model review sign-off
- SAST + dependency scanning
- Secrets scanning
- DAST on staging
- Pen test for enterprise readiness (phase 2)
- Tenant isolation tests:
  - “cannot access other tenant’s deal via ID guess”
  - “cannot access other tenant’s object store prefix”
- Audit completeness tests:
  - every mutating endpoint must generate an audit event

### 11.2 Continuous Controls
- Access log anomaly detection
- Elevated action alerts (override approvals, break-glass usage)
- Security posture dashboards (controls health)

---

## 12) Security Open Decisions (TBD)

- Select default cloud provider (AWS/Azure/GCP).
- Decide whether graph DB is multi-tenant or per-tenant.
- Decide if deliverable PDFs are signed with a tenant key.
- Decide if DLP scanning is required for outbound deliverables.

---

## 13) Definition of Done (Security Baseline)

The IDIS security baseline is considered implemented when:
- RBAC + tenant isolation enforced on all endpoints
- encryption at rest + in transit enabled
- secrets stored and rotated
- object store access is scoped and audited
- audit event taxonomy implemented and events emitted for all mutations
- No‑Free‑Facts and Muḥāsabah gates implemented as validators
- incident runbooks exist and are tested
