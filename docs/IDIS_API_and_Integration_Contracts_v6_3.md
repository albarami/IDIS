# IDIS API & Integration Contracts (v6.3)
**Version:** 6.3 (derived from IDIS v6.3 FINAL)  
**Date:** 2026-01-06  
**Scope:** Normative API behaviors, integration flows, webhooks, retries, idempotency, and audit/event contracts.

---

## 1) Purpose

This document defines the production-ready API and integration contracts required to start building IDIS cleanly:
- Backend endpoints and request/response conventions.
- Idempotency and retry rules.
- Webhook event contracts.
- Integration contracts for CRM, document sources, and enrichment providers.
- Enterprise-grade auditability and security requirements.

**Source of truth:** IDIS v6.3 (VC Edition).  

---

## 2) API Principles (Non-negotiable)

### 2.1 No‑Free‑Facts Enforcement
- Any IC-bound output must reference `claim_id` or `calc_id`.
- API responses may include free text, but the platform must store structured references:
  - `referenced_claim_ids[]`
  - `referenced_calc_ids[]`

### 2.2 Deterministic Numerics
- Numeric metrics used for decisions must be stored as:
  - Claim value (typed) or Calc output with Calc‑Sanad.
- LLM free-form numeric estimates must never enter IC deliverables.

### 2.3 Multi‑Tenant Isolation
- Every request executes in a single tenant context:
  - via JWT claims or API key association.
- Data must not be queryable across tenants by design.

### 2.4 Immutable Audit Trail
Every mutating API call emits an `AuditEvent` with:
- `event_type`
- actor identity
- resource identity
- before/after diff (where applicable)
- request_id / idempotency key
- timestamp

---

## 3) Authentication & Authorization

### 3.1 Auth Methods
1. **Bearer JWT (SSO)**
   - Issued via enterprise IdP (Okta/Azure AD).
   - Includes `tenant_id`, `user_id`, `roles`, and optional data residency policies.

2. **API Key (service-to-service)**
   - `X-IDIS-API-Key` header.
   - Used for internal services and secure integrations (e.g., CRM sync worker).

### 3.2 RBAC (Minimum Roles)
- `ANALYST`
- `PARTNER`
- `IC_MEMBER`
- `ADMIN`
- `AUDITOR`
- `INTEGRATION_SERVICE`

**Policy highlights:**
- Only `ADMIN` can create webhooks and integration connections.
- Only `PARTNER` (or higher) can approve overrides.

---

## 4) Idempotency & Retries (Production Requirement)

### 4.1 Idempotency
All POST/PATCH endpoints accept optional header:
- `Idempotency-Key: <string>`

Server behavior:
- If same key + same endpoint + same actor + same payload hash is received → return stored response.
- If same key but payload hash differs → return `409 Conflict`.

### 4.2 Retry Policy
- Clients may retry on 429/502/503/504 with exponential backoff + jitter.
- `RunRef` responses must be safe to poll.

### 4.3 Rate Limits (Default)
- User endpoints: 600 req/min/tenant (burst 2x)
- Integration endpoints: 1200 req/min/tenant (burst 2x)
- Bulk export endpoints: lower (TBD; depends on tenant plan)

---

## 5) Core Resources and Ownership

| Resource | Owner Service | Primary Storage | Notes |
|---|---|---|---|
| Deal | Deal Service | Postgres | lifecycle + routing |
| DocumentArtifact | Ingestion Service | Postgres + Object Store | raw docs immutable |
| Claim | Claim Registry | Postgres | canonical No-Free-Facts unit |
| Sanad | Sanad Graph | Graph DB (+ materialized in Postgres) | chain + grade + defects |
| Defect | Defects Service | Postgres | typed + cure protocol |
| CalcSanad | Calc Service | Postgres + Object Store | deterministic provenance |
| DebateSession | Orchestrator | Postgres | transcripts + decisions |
| HumanGate | Gatekeeper | Postgres | verification actions |
| Override | Gatekeeper | Postgres | partner approvals |
| Deliverable | Generator | Object Store + Postgres | PDFs/DOCX + artifacts |
| AuditEvent | Audit Service | Append-only store | compliance |

---

## 6) Webhooks (Outbound Eventing)

### 6.1 Delivery Requirements
- HMAC signature supported via shared secret.
- Retries: 10 attempts over 24 hours with exponential backoff.
- Ordering: best-effort; consumers must be idempotent.

### 6.2 Webhook Payload Contract
- `event_id` (UUID)
- `event_type`
- `occurred_at`
- `tenant_id`
- `resource_type`
- `resource_id`
- `payload` (event-specific fields)

---

## 7) Integration Contracts (Enterprise)

### 7.1 CRM Integrations (DealCloud / Affinity / Salesforce)

**Integration pattern:**
- CRM → IDIS: deal creation and metadata updates
- IDIS → CRM: status updates, deliverable links, red flags summary

**Idempotent upsert keys:**
- `external_refs.crm.deal_id` (string)
- Each inbound update emits an `AuditEvent` (actor: integration service)

### 7.2 Document Sources (DocSend / Drive / SharePoint / Dropbox)

**Ingestion pattern:**
- Prefer link ingestion (URI + OAuth token)
- Store immutable raw copy with `sha256`
- New sha256 implies new `version_id`

### 7.3 Enrichment Providers (BYOL)

**Contract:**
- Enrichment records stored with provenance:
  - provider, record_id, retrieved_at, method, licensing metadata

---

## 8) Error Model (Normative)

All errors are JSON:
- `code`
- `message`
- `details`
- `request_id`

---

## 9) Mandatory Production Checks

Before production:
- Contract tests against OpenAPI
- Audit events for every mutating route
- No‑Free‑Facts validator integrated into deliverables
- Muḥāsabah gate integrated into debate
- RBAC enforced for overrides
- Webhook signing + retry
