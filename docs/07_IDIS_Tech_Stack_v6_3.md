# IDIS Recommended Tech Stack — v6.3

**Source**: IDIS VC Edition v6.3 (January 2026)  
**Purpose**: Provide an enterprise-grade, implementable tech stack aligned to the v6.3 architecture and trust requirements.

This is a recommendation document; treat “MUST” as required for compliance with v6.3 invariants.

---

## 1. Backend

### 1.1 Language & Framework

- **Python 3.11+** (MUST)
- **FastAPI** (recommended) for REST + OpenAPI
- Pydantic v2 for strict schema validation (MUST)

Rationale:
- best ecosystem for LangGraph + deterministic engines,
- strong typing at boundaries,
- rapid iteration.

### 1.2 Orchestration & Jobs

Choose one:
- **Temporal** (recommended for enterprise workflows; durable, observable)
- Celery + Redis (acceptable for MVP)
- Prefect (acceptable for dataflow, less ideal for strict state machines)

LangGraph:
- **LangGraph** (MUST for v6.3 debate layer spec)

### 1.3 Datastores

- **PostgreSQL** (MUST): canonical store for deals/claims/sanads/audit
- **Object Storage** (MUST): S3/GCS/Azure Blob for raw artifacts
- **Graph DB** (RECOMMENDED): Neo4j/Neptune/Arango for Sanad graph
- **Vector Search** (RECOMMENDED): pgvector (simple) or Pinecone/Weaviate (scale)
- **Cache**: Redis
- **Event Bus**: Kafka/Redpanda (recommended) or RabbitMQ (MVP)

### 1.4 Deterministic Engines

- Pure Python libraries + internal calc modules
- Use reproducibility hashing:
  - `formula_hash`, `code_version`, `reproducibility_hash` (MUST)

### 1.5 Observability

- OpenTelemetry (MUST)
- Prometheus + Grafana (recommended)
- Centralized logs: ELK/OpenSearch or Datadog

---

## 2. AI / LLM Layer

### 2.1 Provider Abstraction

MUST implement a provider-agnostic interface:
- OpenAI / Anthropic / Azure OpenAI / (local models later)
- Must support:
  - structured outputs (JSON schema),
  - tool calls,
  - request/response logging (with redaction),
  - model version pinning.

### 2.2 Safety/Trust Controls

- No-Free-Facts validator (MUST; deterministic)
- Muḥāsabah validator (MUST; deterministic)
- Prompt versioning (MUST)
- Brier score calibration support (recommended)

---

## 3. Frontend

- **React + Next.js** (recommended)
- TypeScript (MUST)
- Component system: Radix UI / MUI / Chakra (choose one)
- Graph visualization:
  - Cytoscape.js or React Flow for Sanad graph

Key UI pages:
- Triage Queue
- Truth Dashboard
- Claim Detail (Sanad chain + defects)
- Debate Transcript + Muḥāsabah viewer
- Deliverables viewer/export
- Governance dashboard

---

## 4. Security & Compliance

### 4.1 Identity & Access

- SSO: Okta / Azure AD (SAML/OIDC) (MUST for enterprise)
- MFA enforced via IdP (MUST)
- RBAC enforced server-side (MUST)

### 4.2 Key Management

- KMS (AWS KMS/Azure Key Vault/GCP KMS)
- BYOK option for enterprise tenants (recommended)

### 4.3 Data Residency

- Region pinning for tenant data (recommended)

### 4.4 Audit & Compliance

- Immutable audit logs (MUST)
- SOC2 readiness controls (recommended)
- Retention policies + legal hold (recommended)

---

## 5. Deployment & DevOps

- Docker (MUST)
- Kubernetes (recommended for enterprise)
- Terraform (recommended)
- CI/CD: GitHub Actions / GitLab CI (recommended)
- Secrets: Vault or cloud secret manager

Environments:
- dev / staging / prod with strict config separation (MUST)

---

## 6. Suggested Default: “Enterprise SaaS” Reference Stack

- FastAPI + Pydantic
- Postgres + pgvector
- S3 + CloudFront (or equivalent)
- Neo4j (managed)
- Temporal
- Kafka/Redpanda
- Next.js frontend
- OTel + Prometheus + Grafana
- Okta/Azure AD SSO
- KMS + BYOK

---

## 7. Alternatives (When Needed)

- If avoiding graph DB: store Sanad chains in Postgres JSONB + build adjacency in-memory (acceptable early; harder to visualize/trace at scale).
- If avoiding Kafka: use Postgres outbox pattern for audit events (MVP).
- If on-prem: replace managed services with self-hosted equivalents.

