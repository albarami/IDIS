# IDIS Audit Event Taxonomy & Logging Contract (v6.3)
**Version:** 6.3 (derived from IDIS v6.3 FINAL)  
**Date:** 2026-01-06  
**Status:** Normative baseline for audit logging and compliance

---

## 1) Purpose

This document defines the **audit event taxonomy** and a **logging contract** for IDIS that supports:
- non-repudiation
- forensic audit
- governance requirements
- SOC2/ISO27001 alignment
- reproducibility of decisions (claims, sanad, calc-sanad, debate)

Audit events are **append-only** and must be emitted for every mutating operation.

---

## 2) Core Requirements (Non-negotiable)

1. **Append-only**: events cannot be modified; only new events can be appended.
2. **Complete coverage**: every POST/PATCH/DELETE-like operation emits an event.
3. **Correlation**: all events carry `request_id`, optional `idempotency_key`, and trace IDs.
4. **Identity**: include actor identity and role at time of action.
5. **Tenant binding**: every event includes `tenant_id` and must be queryable only by that tenant.
6. **Redaction**: avoid raw Class-3 content in audit payloads; store references/hashes instead.
7. **Time integrity**: use server-side timestamps.

---

## 3) Canonical Audit Event Schema (JSON)

```json
{
  "event_id": "uuid",
  "occurred_at": "2026-01-06T12:00:00Z",
  "tenant_id": "uuid",

  "actor": {
    "actor_type": "HUMAN|SERVICE",
    "actor_id": "string",
    "roles": ["ANALYST","PARTNER"],
    "ip": "string",
    "user_agent": "string"
  },

  "request": {
    "request_id": "req_123",
    "idempotency_key": "optional",
    "trace_id": "optional",
    "span_id": "optional",
    "method": "POST",
    "path": "/v1/deals",
    "status_code": 201
  },

  "resource": {
    "resource_type": "deal|document|claim|sanad|defect|calc|debate|deliverable|human_gate|override|integration|webhook",
    "resource_id": "uuid",
    "deal_id": "uuid (nullable)"
  },

  "event_type": "string",
  "severity": "LOW|MEDIUM|HIGH|CRITICAL",
  "summary": "string",

  "diff": {
    "before_ref": "object_store://... (optional)",
    "after_ref": "object_store://... (optional)",
    "changed_fields": ["status","stage"]
  },

  "payload": {
    "safe": "fields only",
    "hashes": ["sha256:..."],
    "refs": ["claim_id:...", "calc_id:..."]
  }
}
```

---

## 4) Event Taxonomy (Required)

### 4.1 Deals
- `deal.created` (MEDIUM)
- `deal.updated` (MEDIUM)
- `deal.status.changed` (MEDIUM/HIGH if IC_READY or DECLINED)
- `deal.assigned` (LOW)
- `deal.archived` (LOW)

### 4.2 Documents & Ingestion
- `document.created` (MEDIUM)
- `document.version.created` (MEDIUM)
- `document.ingestion.started` (LOW)
- `document.ingestion.completed` (LOW)
- `document.ingestion.failed` (MEDIUM)
- `document.malware.detected` (HIGH)
- `document.access.denied` (HIGH)

### 4.3 Claims
- `claim.created` (MEDIUM)
- `claim.updated` (MEDIUM)
- `claim.verdict.changed` (HIGH)
- `claim.grade.changed` (HIGH)
- `claim.action.changed` (MEDIUM)
- `claim.deprecated` (LOW)
- `claim.ic_bound.set` (MEDIUM)

### 4.4 Sanad
- `sanad.created` (LOW/MEDIUM)
- `sanad.updated` (MEDIUM)
- `sanad.corroboration.changed` (MEDIUM)
- `sanad.defect.added` (HIGH)
- `sanad.defect.waived` (HIGH)
- `sanad.integrity.failed` (CRITICAL)

### 4.5 Defects
- `defect.created` (HIGH for FATAL)
- `defect.updated` (MEDIUM)
- `defect.cured` (MEDIUM)
- `defect.waived` (HIGH)

### 4.6 Deterministic Calculations (Calc‑Sanad)
- `calc.started` (LOW)
- `calc.completed` (LOW)
- `calc.failed` (MEDIUM)
- `calc.blocked_for_ic` (HIGH)
- `calc.formula.changed` (CRITICAL if in production without approval)

### 4.7 Debate (LangGraph)
- `debate.started` (LOW)
- `debate.round.completed` (LOW)
- `debate.completed` (LOW)
- `debate.stopped.max_rounds` (MEDIUM)
- `debate.stopped.critical_defect` (HIGH)
- `debate.utility.awarded` (LOW)
- `debate.utility.penalized` (MEDIUM)

### 4.8 Muḥāsabah
- `muhasabah.recorded` (LOW)
- `muhasabah.rejected` (MEDIUM)
- `muhasabah.override` (HIGH)

### 4.9 Human Gates & Overrides
- `human_gate.created` (MEDIUM)
- `human_gate.action.approved` (HIGH if affects IC_READY)
- `human_gate.action.corrected` (HIGH)
- `human_gate.action.rejected` (MEDIUM)
- `override.created` (HIGH)
- `override.approved` (CRITICAL)
- `override.rejected` (HIGH)

### 4.10 Deliverables & Export
- `deliverable.requested` (LOW)
- `deliverable.ready` (LOW)
- `deliverable.exported` (MEDIUM/HIGH if IC_MEMO)
- `deliverable.access.denied` (HIGH)

### 4.11 Integrations & Webhooks
- `integration.connected` (MEDIUM)
- `integration.disconnected` (MEDIUM)
- `integration.error` (MEDIUM)
- `webhook.created` (MEDIUM)
- `webhook.deleted` (MEDIUM)
- `webhook.delivery.succeeded` (LOW)
- `webhook.delivery.failed` (MEDIUM)

### 4.12 Security Events
- `auth.login.succeeded` (LOW)
- `auth.login.failed` (MEDIUM)
- `auth.mfa.failed` (MEDIUM)
- `auth.token.revoked` (HIGH)
- `rbac.denied` (MEDIUM)
- `tenant.isolation.violation` (CRITICAL)
- `break_glass.used` (CRITICAL)
- `data.exfiltration.suspected` (CRITICAL)

---

## 5) Retention, Access, and Compliance

### 5.1 Retention Policy (Default)
- Audit events retained: 7 years (configurable per tenant)
- Legal holds supported (tenant admin + legal)

### 5.2 Access Policy
- Analysts: read events for deals they have access to
- Partners: read all tenant events
- Auditors: read-only access to audit APIs
- Admin: can configure retention/webhooks but cannot delete audit history

### 5.3 Storage Model
- Append-only store (e.g., immutable log table + object store for large diffs)
- Optional WORM storage class for enterprise tier

---

## 6) Implementation Checklist

- Add audit middleware that:
  - stamps request_id/trace_id
  - captures actor identity/role
  - emits event for all mutations
- Add unit tests ensuring each mutating endpoint emits an audit event
- Ensure payload redaction rules:
  - store hashes/refs, not raw bank statements or contract text
- Implement query filters: by time range, deal_id, event_type

