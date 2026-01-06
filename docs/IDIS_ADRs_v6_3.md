# IDIS Architecture Decision Records (ADRs) — v6.3 Baseline
**Version:** 6.3 (derived from IDIS v6.3 FINAL)  
**Date:** 2026-01-06  
**Status:** Draft decisions (lock these before Phase 2)  
**Owner:** Salim Al‑Barami  
**Audience:** Solo builder (Windsurf/Cursor) + Verifier (Codex)

---

## How to use this file
- Each ADR should be treated as a **decision gate**.
- Cascade may implement only within the bounds of **Approved** ADRs.
- Codex must reject work that contradicts an approved ADR unless the ADR is updated first.
- Update ADRs only via commit with clear rationale and impact analysis.

---

## ADR-001: Architecture Style — Modular Monolith First, Services Later
**Status:** Approved (default)  
**Context:** IDIS is being built solo with AI assistance. Early microservices increase coordination overhead and slow progress. We need strong module boundaries but minimal operational complexity.

**Decision:** Start as a **modular monolith**:
- One deployable backend initially.
- Clear module boundaries and interfaces:
  - `deal`, `documents`, `claims`, `sanad`, `defects`, `calcs`, `debate`, `deliverables`, `audit`, `integrations`, `auth`
- Evolve to services only if and when:
  - scaling demands it, or
  - enterprise tenant isolation requires dedicated deployments.

**Consequences:**
- Faster Phase 0–3 execution.
- Easier local dev and testing.
- Clean extraction to services later via module boundaries.

**Guardrails:**
- No cross-module imports except via defined interfaces.
- Shared utilities only in `core/` or `common/` packages.

---

## ADR-002: System of Record — PostgreSQL + Row-Level Security (RLS)
**Status:** Approved  
**Context:** IDIS requires tenant isolation and auditability. Postgres is a reliable system-of-record and supports RLS for tenant scoping.

**Decision:**
- Use **PostgreSQL** as system of record for:
  - deals
  - documents metadata
  - claims registry
  - defects
  - human gates
  - overrides
  - audit events (append-only table) + optional immutable external sink
  - debate session metadata/transcripts (structured storage)
- Enforce **tenant_id everywhere** and enable **RLS policies** for tenant isolation.

**Consequences:**
- Strong enterprise-grade isolation.
- Straightforward migrations.
- Queryable audit events.

**Guardrails:**
- Every table must have `tenant_id`.
- Every query must be tenant-scoped (middleware enforces tenant context).
- Codex rejects any query not scoped by tenant_id (unless explicitly safe and documented).

---

## ADR-003: Sanad Graph Storage — Postgres First, Graph DB Later
**Status:** Approved (phased)  
**Context:** The Sanad graph is core, but a full graph DB increases operational complexity. Early phases can operate on Postgres tables + adjacency-style joins.

**Decision:**
- Phase 1–4:
  - Store Sanad objects in Postgres with structured fields:
    - primary evidence ref
    - transmission_chain JSONB
    - corroboration metadata
    - grade/verdict/action fields
    - defects list (FK to defects table)
  - Provide an interface layer that abstracts graph queries.
- Phase 5+ (optional):
  - Evaluate Neo4j/ArangoDB/Neptune only when:
    - graph traversal is a bottleneck, or
    - Sanad Map visualization and cross-claim traversals require it.

**Consequences:**
- Faster implementation.
- Less operational overhead.
- Graph DB can be added later without breaking contracts.

**Guardrails:**
- Maintain graph-friendly IDs and edges in schema even if stored in Postgres.
- Do not embed cross-tenant graph edges.

---

## ADR-004: Vector Retrieval — pgvector First
**Status:** Approved (default)  
**Context:** IDIS may need semantic search over docs and claims. Dedicated vector DB adds complexity.

**Decision:**
- Use **pgvector** inside Postgres for:
  - embeddings of document spans (optional)
  - embeddings of claims (optional)
- Dedicated vector stores (Pinecone/Weaviate/Qdrant) considered only after:
  - retrieval latency or cost becomes problematic
  - enterprise requirements demand it

**Consequences:**
- Minimal infrastructure.
- Simple tenant scoping with RLS.

**Guardrails:**
- Embeddings must be tenant-scoped.
- No cross-tenant retrieval.

---

## ADR-005: Eventing / Async Jobs — Queue First (SQS-like), Kafka Later
**Status:** Approved (phased)  
**Context:** IDIS ingestion and OCR are asynchronous. Kafka is heavy early.

**Decision:**
- Use a managed queue pattern first (SQS/PubSub/Service Bus) or a simple job runner abstraction:
  - ingestion tasks
  - OCR tasks
  - calc runs
  - deliverable generation
- Kafka introduced only if:
  - high throughput demands it
  - strict ordering and replay semantics needed beyond current webhooks/audit logs

**Consequences:**
- Faster implementation.
- Lower operational cost.

**Guardrails:**
- All jobs must be idempotent (use Idempotency-Key and job dedupe).
- Jobs must carry tenant_id and deal_id.

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

## ADR-011: “No Cross-Tenant Existence Checks” (Leakage Rule)
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
- 2026-01-06: Initial ADR set created from IDIS v6.3; approved as default baseline.
