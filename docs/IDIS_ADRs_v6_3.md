# IDIS Architecture Decision Records (ADRs) — v6.3 Baseline (001–005)
**Version:** 6.3 (aligned to IDIS v6.3 documentation)  
**Date:** 2026-01-07  
**Status:** Approved baseline (lock before Phase 2)  
**Owner:** Salim Al-Barami  
**Audience:** Solo builder (Windsurf/Cursor) + Verifier (Codex)

---

## How to use this file
- Each ADR is a **decision gate**. Engineering work must stay within **Approved** ADR boundaries.
- Cascade may implement only within the bounds of Approved ADRs.
- Codex must **REJECT** any change that contradicts an Approved ADR unless the ADR is updated first.
- Updating an ADR requires:
  - explicit rationale
  - impact analysis (security, ops, migration)
  - commit + review (Codex gate)
- This baseline intentionally contains **only ADR-001..ADR-005**.
  - Any additional ADRs must live in a separate file (e.g., `docs/IDIS_ADRs_Supplemental_v6_3.md`).

---

## ADR-001: Architecture Style — Modular Monolith First, Services Later
**Status:** Approved  
**Context:** IDIS is built solo with AI assistance. Early microservices increase coordination, CI/CD complexity, and operational risk. We need strong module boundaries without distributed-systems overhead.

**Decision Drivers:**
- Speed of Phase 0–4 delivery
- Deterministic validation gates (Truth Layer)
- Minimal operational complexity
- Future extraction path (services later)

**Options considered:**
1) **Microservices from day one**
   - Pros: independent scaling/deploy; team autonomy
   - Cons: heavy ops overhead; slower solo execution; more failure modes
2) **Modular monolith (single deployable, strict internal boundaries)**
   - Pros: fastest iteration; simplest dev/test/CI; easiest end-to-end gating; fewer moving parts
   - Cons: coarse scaling until services extracted
3) **Hybrid split early (core monolith + a few services)**
   - Pros: isolates a small set of hot paths
   - Cons: inherits distributed complexity without clear early ROI

**Decision:** Choose **Modular Monolith** for Phase 0–4 with explicit module boundaries and internal interfaces.

**Consequences:**
- Faster iteration and simpler deployments.
- Stronger reliability for early trust gates.
- Service extraction becomes a deliberate later step.

**Guardrails:**
- Organize by domains/modules (e.g., `tenancy`, `auth`, `deals`, `documents`, `claims`, `sanad`, `defects`, `calcs`, `audit`, `integrations`, `deliverables`).
- Avoid cross-module imports except via explicit interfaces/contracts.
- Shared primitives only in `core/` or `common/` (no "god utils").

---

## ADR-002: System of Record — PostgreSQL + Row-Level Security (RLS) Tenant Isolation
**Status:** Approved  
**Context:** IDIS requires strict tenant isolation, auditable state transitions, and deterministic server-side enforcement. PostgreSQL provides strong transactional guarantees and supports Row-Level Security (RLS).

**Decision Drivers:**
- Tenant isolation as a hard invariant
- Auditability and traceability
- Relational integrity for claims/sanad/defects/calcs
- Predictable migrations and ops maturity

**Options considered:**
1) **PostgreSQL + RLS (DB-enforced isolation)**
   - Pros: strongest practical isolation; central enforcement; standard enterprise posture
   - Cons: requires discipline in RLS policies/migrations
2) **PostgreSQL without RLS (app-only filtering)**
   - Pros: simpler initial setup
   - Cons: higher breach risk from query mistakes; weaker guarantees
3) **Database-per-tenant**
   - Pros: maximum blast-radius isolation
   - Cons: expensive ops; complex migrations/analytics; not needed early

**Decision:** Choose **PostgreSQL + RLS** as the system-of-record; enforce `tenant_id` on all tables and scope all access via tenant context.

**Consequences:**
- Enterprise-grade isolation baseline.
- Simplifies compliance posture and audits.
- Clear foundation for append-only audit events.

**Guardrails:**
- Every table includes `tenant_id` and is protected by RLS.
- Every query must be tenant-scoped (middleware enforces tenant context).
- No cross-tenant existence checks (avoid leakage side-channels).
- Codex rejects any data access not tenant-scoped unless explicitly proven safe and documented.

---

## ADR-003: Sanad / Provenance Graph — Property Graph Store + Postgres Projection
**Status:** Approved  
**Context:** v6.3 defines a Sanad/provenance **graph schema** and expects a **property graph** store (e.g., Neo4j/Arango/Neptune). PostgreSQL remains the system-of-record for structured entities and projections; the graph stores provenance relationships.

**Decision Drivers:**
- Alignment with v6.3 graph schema expectations
- Deterministic integrity validation (cycles/orphans/roots)
- Tenant-safe graph boundaries (no cross-tenant edges)
- Operational feasibility while building solo

**Options considered:**
1) **Property graph DB as primary provenance store (Neo4j/Arango/Neptune) + Postgres projection**
   - Pros: aligns with v6.3; enables traversals/visualization; clean provenance queries
   - Cons: additional datastore to operate
2) **Postgres-only (JSONB/adjacency)**
   - Pros: minimal infra
   - Cons: conflicts with v6.3 graph-layer expectations; harder long-term traversal/visualization
3) **Dual-write from day one (Postgres + Graph)**
   - Pros: immediate graph availability
   - Cons: higher complexity; consistency risks early

**Decision:** Choose **Option 1**:
- Maintain **PostgreSQL** as the system-of-record (rows + projections).
- Maintain a **property graph DB** for Sanad/provenance relationships per v6.3 graph schema.
- Implementation sequencing:
  - Early phases may validate and store a materialized chain/projection in Postgres, but the architecture remains graph-backed for provenance.
  - Introduce the graph write/read path as soon as the datastore is provisioned; do not treat the graph as optional.

**Consequences:**
- Aligns the system to the v6.3 data model.
- Enables graph traversal and UI visualization without redesign.
- Requires operating an additional datastore when enabled.

**Guardrails:**
- Graph nodes/edges must be tenant-scoped; no cross-tenant edges/traversals.
- Provide a repository/interface layer so code is not hardwired to one graph vendor.
- Keep stable graph-friendly IDs (UUIDs) across Postgres and the graph store.
- Any change to graph vendor/strategy requires a new ADR with migration plan.

---

## ADR-004: Eventing / Async Jobs — RabbitMQ/Queue for MVP; Kafka/Redpanda When Needed
**Status:** Approved  
**Context:** v6.3 tech stack recommends **Kafka/Redpanda** as the event bus, with **RabbitMQ** acceptable for MVP. IDIS needs async ingestion/OCR/calcs/deliverables with retries and idempotency; avoid heavy ops early while keeping an upgrade path.

**Decision Drivers:**
- Minimal operational burden for solo build
- Deterministic job execution and retry semantics
- Idempotency and tenant-scoped job payloads
- Clear migration path to Kafka/Redpanda if required

**Options considered:**
1) **Kafka/Redpanda from day one**
   - Pros: replay, ordering, high throughput ecosystem
   - Cons: heavier ops; slows solo build early
2) **RabbitMQ / managed queue for MVP (queue-first)**
   - Pros: simpler ops; sufficient for Phase 2–4 workflows; straightforward DLQ patterns
   - Cons: fewer streaming semantics; later migration if replay/ordering becomes essential
3) **Synchronous-only processing**
   - Pros: simplest code
   - Cons: poor UX; fragile; long request times; limits scalability

**Decision:** Choose **Option 2** for MVP:
- Start with **RabbitMQ or a managed queue** abstraction for async jobs.
- Keep an internal event/job interface so migration to **Kafka/Redpanda** is a controlled swap if requirements demand it.

**Consequences:**
- Faster delivery with manageable ops.
- Kafka/Redpanda remains the scale path when justified.

**Guardrails:**
- All jobs must be idempotent (idempotency keys + dedupe).
- Every job payload includes `tenant_id` and `deal_id`.
- Retries with backoff + Dead Letter Queue pattern.
- Emit audit events for job lifecycle once audit pipeline is wired.

---

## ADR-005: Deployment Target — Docker + GitHub Actions Now; Kubernetes Later
**Status:** Approved  
**Context:** Early Kubernetes introduces unnecessary operational overhead. Docker-based workflows with GitHub Actions provide strong CI discipline and reproducible builds; Kubernetes can be added later for enterprise orchestration.

**Decision Drivers:**
- Deterministic CI gates
- Reproducible builds and deployments
- Minimal ops overhead early
- Clean migration path to Kubernetes

**Options considered:**
1) **Docker + GitHub Actions CI (Phase 0–4)**
   - Pros: simplest; portable; repeatable; aligns with solo build
   - Cons: less orchestration capability than Kubernetes
2) **Kubernetes from day one**
   - Pros: standard enterprise orchestration; scaling primitives
   - Cons: heavy overhead; slows early phases
3) **Serverless-first**
   - Pros: minimal infra
   - Cons: awkward for stateful system-of-record workflows and deterministic pipelines

**Decision:** Choose **Option 1** for Phase 0–4:
- Docker images built and pinned to commit SHA.
- GitHub Actions CI enforces ruff/mypy/pytest gates.
- Kubernetes deferred until justified; adoption requires a new ADR with migration/rollback.

**Consequences:**
- Faster iteration and simpler deployments.
- Strong reproducibility for trust gates.

**Guardrails:**
- Separate dev/staging/prod environments.
- Secrets never in git; use a secrets manager/vault pattern.
- Immutable build artifacts; deploy by digest/tag pinned to commit.

---

## Decision Log
- 2026-01-07: Locked ADR-001..ADR-005 as the v6.3 baseline architecture decisions prior to Phase 2.
