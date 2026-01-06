# IDIS Architecture Decision Records — Supplemental (ADR-006+)
**Version:** 6.3  
**Date:** 2026-01-07  
**Status:** Approved supplemental decisions  
**Owner:** Salim Al-Barami  
**Note:** These ADRs were moved from the baseline file to keep `IDIS_ADRs_v6_3.md` limited to ADR-001..ADR-005.

---

## ADR-006: API Style — REST + OpenAPI as Source of Truth
**Status:** Approved  
**Context:** Enterprise integrations require stable contracts. The OpenAPI spec is already defined.

**Decision:**
- REST API with strict OpenAPI contract.
- Server validates request bodies against schemas.
- Contract tests run in CI to prevent drift.

**Consequences:**
- Predictable integrations.
- Easier client generation.

**Guardrails:**
- No endpoint added without OpenAPI update.
- No breaking changes without versioning (v1/v2).

---

## ADR-007: Auth — SSO via OIDC, RBAC + Deal-Level ABAC
**Status:** Approved (baseline)  
**Context:** Enterprise customers expect SSO and strict access controls.

**Decision:**
- Use OIDC (Okta/Azure AD).
- JWT contains tenant_id, roles.
- Enforce RBAC roles:
  - ANALYST, PARTNER, IC_MEMBER, ADMIN, AUDITOR, INTEGRATION_SERVICE
- Enforce deal-level ABAC via assignments/groups.

**Guardrails:**
- Auth enforced server-side.
- Break-glass requires justification and audit event.

---

## ADR-008: Audit Logging — Append-Only Events in Postgres + Optional External Sink
**Status:** Approved  
**Context:** IDIS requires immutable traceability.

**Decision:**
- Append-only `audit_events` table in Postgres (tenant-scoped).
- Optional external immutable sink (object store WORM) for enterprise tier.
- Audit taxonomy enforced (see `IDIS_Audit_Event_Taxonomy_v6_3.md`).

**Guardrails:**
- Every mutating operation emits an audit event.
- No raw Class-3 content in audit payloads (refs/hashes only).

---

## ADR-009: Testing Strategy — Gates First
**Status:** Approved  
**Context:** The project must be enterprise-grade and built solo.

**Decision:**
- CI requires:
  - ruff format/check
  - mypy
  - pytest
- Evaluation harness gates must be implemented before prompt/calc changes are promoted.

**Guardrails:**
- No-Free-Facts, Muḥāsabah, Sanad integrity: 0 tolerance regressions.

---

## ADR-010: Deployment — Docker First, K8s Later
**Status:** Approved (phased)  
**Context:** Solo build: K8s adds overhead. Enterprise deployments can later use K8s.

**Decision:**
- Phase 0–4: Docker-based local dev; GitHub Actions CI.
- Phase 5+: add Helm/Terraform and k8s manifests as needed.

**Guardrails:**
- Environments: dev/staging/prod separated.
- Secrets managed via vault/secrets manager (never in git).

---

## ADR-011: "No Cross-Tenant Existence Checks" (Leakage Rule)
**Status:** Approved  
**Context:** Prevent side-channel leakage about other tenants.

**Decision:**
- When validating references, treat unknown refs as `unknown_or_out_of_scope`.
- Never query other tenants to see if a ref exists.
- Audit events must not reveal cross-tenant existence.

**Guardrails:**
- Codex rejects any code that attempts cross-tenant existence lookups.

---

## ADR-012: Human Gates and Overrides — Always Explicit and Audited
**Status:** Approved  
**Context:** IDIS has human verification gates and partner overrides.

**Decision:**
- Any override requires:
  - role PARTNER+
  - justification string
  - audit event (CRITICAL)
- Human gate actions are immutable records.

**Guardrails:**
- Overrides never silent.
- Overrides never remove audit history.

---

## Decision Log
- 2026-01-07: ADR-006..ADR-012 moved to supplemental file to keep baseline locked to ADR-001..ADR-005.
