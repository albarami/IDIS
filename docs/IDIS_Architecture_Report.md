# IDIS v6.3 — Architecture & Implementation Report

**Intelligent Due-Diligence Information System**
Enterprise-grade, multi-tenant VC due-diligence platform.

---

## §1 Pipeline Orchestration

### Pipeline Steps

The pipeline executes in strict canonical order via `RunOrchestrator` (`src/idis/services/runs/orchestrator.py`):

| Order | Step          | Mode     | Description                                      |
|-------|---------------|----------|--------------------------------------------------|
| 0     | INGEST_CHECK  | SNAPSHOT, FULL | Verify ≥1 ingested document exists          |
| 1     | EXTRACT       | SNAPSHOT, FULL | Run claim extraction from document spans     |
| 2     | GRADE         | SNAPSHOT, FULL | Auto-grade Sanad chains for extracted claims |
| 3     | CALC          | SNAPSHOT, FULL | Deterministic financial calculations         |
| 4     | DEBATE        | FULL only      | Multi-agent adversarial debate               |

- **Step model** (`src/idis/models/run_step.py`): `StepName`, `StepStatus` (PENDING → RUNNING → COMPLETED/FAILED/BLOCKED), `STEP_ORDER` dict for deterministic iteration.
- **Execution modes**: `SNAPSHOT` (steps 0-3) and `FULL` (steps 0-4).
- **Idempotent resume**: already-COMPLETED steps are skipped on re-execution.
- **Fail-closed**: `AuditSinkError` propagates immediately and aborts the run. Unimplemented steps yield `BLOCKED` status.
- **Final status**: COMPLETED (all pass), FAILED (any fail, none pass), PARTIAL (mix of pass/fail).
- **Audit**: every step transition (started/completed/failed/blocked) emits an audit event.

### Pipeline Executor

`PipelineExecutor` (`src/idis/pipeline/executor.py`) provides the async GDBS demo path: loads synthetic claims from GDBS dataset, creates claim + Sanad records in Postgres. `PipelineWorker` (`src/idis/pipeline/worker.py`) polls for QUEUED runs and dispatches to the executor.

### Run API

`POST /v1/deals/{deal_id}/runs` (`src/idis/api/routes/runs.py`) validates mode (SNAPSHOT/FULL), checks deal existence, verifies ≥1 ingested document, creates run record, and dispatches to `RunOrchestrator`. Supports `Idempotency-Key` header (duplicate = 409).

---

## §2 Service-to-Persistence Mapping

### Service Layer (`src/idis/services/`)

| Service Package   | Responsibility                                                     |
|-------------------|--------------------------------------------------------------------|
| `extraction/`     | Claim extraction with chunking, confidence scoring, NFF validation |
| `enrichment/`     | External data enrichment with rights gate, caching, BYOL support   |
| `sanad/`          | Sanad chain grading and integrity management                       |
| `claims/`         | Claim CRUD and lifecycle                                           |
| `defects/`        | Defect detection and management                                    |
| `ingestion/`      | Document ingestion and parsing                                     |
| `prompts/`        | Prompt registry and versioned prompt loading                       |
| `runs/`           | Pipeline run orchestration                                         |
| `webhooks/`       | Outbound webhook delivery                                          |

### Persistence Layer (`src/idis/persistence/`)

| Component              | Description                                                         |
|------------------------|---------------------------------------------------------------------|
| `db.py`                | SQLAlchemy engine/connection management, Postgres configuration     |
| `repositories/`        | Tenant-scoped repos: `claims.py`, `deals.py`, `runs.py`, `run_steps.py`, `evidence.py`, `enrichment_credentials.py` |
| `graph_repo.py`        | Neo4j-backed graph repository for Sanad graph projection            |
| `graph_consistency.py` | Cross-store consistency checks (Postgres ↔ Neo4j)                   |
| `neo4j_driver.py`      | Neo4j driver wrapper with NodeLabel/EdgeType enums                  |
| `cypher/`              | Parameterized Cypher query builders (§4.4 patterns)                 |
| `saga.py`              | Saga pattern for multi-store transactional consistency              |
| `migrations/`          | Alembic database migrations                                        |

### Layer Flow

```
api/routes → services/ → persistence/repositories → models/
                       → persistence/graph_repo (Neo4j projection)
                       → storage/ (object storage)
```

No reverse dependencies. Services never import from `api/`. Repositories never import from `services/`.

---

## §3 API Completeness

### Application Factory

`create_app()` in `src/idis/api/main.py` bootstraps the FastAPI app with:

### Middleware Stack (outermost → innermost)

1. **RequestIdMiddleware** — generates/propagates `X-Request-ID`
2. **DBTransactionMiddleware** — opens Postgres connection per request
3. **AuditMiddleware** — captures all responses for audit trail
4. **OpenAPIValidationMiddleware** — auth, tenant context, DB tenant config
5. **ResidencyMiddleware** — data residency region pinning enforcement
6. **RateLimitMiddleware** — tenant-scoped rate limiting
7. **RBACMiddleware** — deny-by-default RBAC/ABAC authorization
8. **IdempotencyMiddleware** — `Idempotency-Key` dedup (409 on duplicate)
9. **TracingEnrichmentMiddleware** — OpenTelemetry span enrichment

### Route Modules (`src/idis/api/routes/`)

| Router           | Prefix / Resource        | Key Operations                                    |
|------------------|--------------------------|---------------------------------------------------|
| `health.py`      | `/health`                | Health check (no auth)                            |
| `deals.py`       | `/v1/deals`              | CRUD for deals                                    |
| `documents.py`   | `/v1/deals/{}/documents` | Document upload, ingestion, artifact management   |
| `claims.py`      | `/v1/deals/{}/claims`    | Claim CRUD, batch operations                      |
| `sanad.py`       | `/v1/deals/{}/sanads`    | Sanad chain queries                               |
| `runs.py`        | `/v1/deals/{}/runs`      | Pipeline run start/status/steps                   |
| `debate.py`      | `/v1/deals/{}/debates`   | Debate session management                         |
| `defects.py`     | `/v1/deals/{}/defects`   | Defect detection results                          |
| `deliverables.py`| `/v1/deals/{}/deliverables` | Screening snapshot, IC memo, truth dashboard, QA brief, decline letter |
| `enrichment.py`  | `/v1/enrichment`         | External data enrichment                          |
| `human_gates.py` | `/v1/deals/{}/human-gates` | Human review gates                              |
| `overrides.py`   | `/v1/overrides`          | Break-glass overrides                             |
| `audit.py`       | `/v1/audit`              | Audit log queries                                 |
| `tenancy.py`     | `/v1/tenants`            | Tenant management                                 |
| `webhooks.py`    | `/v1/webhooks`           | Webhook configuration                             |

### Error Handling

- `IdisHttpError` — structured error with `status_code`, `code`, `message`, `details`
- Global handlers for `HTTPException`, `RequestValidationError`, and generic `Exception`
- All API errors return structured JSON, never raw strings or stack traces

---

## §4 LLM Integration

### Provider Abstraction

All LLM calls go through a provider-agnostic abstraction. LLMs are **never** called directly. The abstraction supports `json_mode=True` for structured output.

### LLM Boundaries

- **LLMs MAY**: interpret, extract, reason, draft, debate, score
- **LLMs MAY NOT**: compute metrics, assign Sanad grades, bypass validators, decide investments

### Analysis Agents (`src/idis/analysis/agents/`)

8 specialist agents, each with versioned prompts under `prompts/`:

| Agent                   | Focus Areas                                                    |
|-------------------------|----------------------------------------------------------------|
| `financial_agent.py`    | Revenue, unit economics, burn rate, runway                     |
| `market_agent.py`       | TAM/SAM/SOM, competitive landscape, market timing              |
| `technical_agent.py`    | Architecture, scalability, tech debt, security, IP             |
| `terms_agent.py`        | Valuation, dilution, liquidation prefs, cap table              |
| `team_agent.py`         | Founder-market fit, leadership, key person risk                |
| `risk_officer_agent.py` | Governance, fraud, operational/legal/financial risk             |
| `historian_agent.py`    | Historical analogues, pattern recognition, exit pathways       |
| `sector_specialist_agent.py` | Sector dynamics, competitive landscape, regulatory env    |

All agent prompts include **Muḥāsibī disciplines**: Nafs Check (bias awareness), Mujāhada (assumption inversion), Insight Type Classification (conventional/deal_specific/contradictory).

### Analysis Engine (`src/idis/analysis/runner.py`)

`AnalysisEngine` orchestrates agent execution:
1. Resolve agents from registry (fail-closed on unknown)
2. Sort deterministically (by `agent_type`, then `agent_id`)
3. Run each agent
4. Validate No-Free-Facts per report
5. Validate Muḥāsabah per report
6. Emit audit events (fail-closed on sink failure)
7. Return `AnalysisBundle`

### Scoring Framework (`src/idis/analysis/scoring/`)

- **8 dimensions**: Market Attractiveness, Team Quality, Product Defensibility, Traction Velocity, Fund Thesis Fit, Capital Efficiency, Scalability, Risk Profile
- **5 stage packs** (PRE_SEED → GROWTH) with validated weights summing to 1.0
- **Composite score**: 0-100, mapped to bands: HIGH (≥75) → INVEST, MEDIUM (≥55) → HOLD, LOW (<55) → DECLINE
- **LLMScorecardRunner**: deterministic context payload → LLM call (json_mode) → Pydantic validation
- **ScoringEngine**: stage pack → LLM runner → NFF validation → Muḥāsabah validation → composite calculation → band/routing

### Prompt Registry

Versioned prompts stored in `prompts/<agent_id>/<version>/prompt.md` + `metadata.json`. Registry in `prompts/registry.yaml`. SemVer versioning.

---

## §5 Graph Database (Neo4j)

### Graph Repository (`src/idis/persistence/graph_repo.py`)

`GraphRepository` provides tenant-scoped Neo4j operations:

- **Projection**: `upsert_deal_graph_projection()` — Deal → Document → Span structure via MERGE (idempotent)
- **Tenant isolation**: every node carries `tenant_id`; every query filters on it
- **No cross-tenant traversal**: structurally impossible

### §4.4 Query Patterns (`src/idis/persistence/cypher/`)

| Query   | Pattern                    | Description                              |
|---------|----------------------------|------------------------------------------|
| §4.4.1  | Full Chain                 | Complete Sanad provenance chain           |
| §4.4.2  | Deal Claims Grades         | All claims + grades for a deal            |
| §4.4.3  | Independence Clusters      | Independent evidence clusters             |
| §4.4.4  | Weakest Link               | Lowest-grade link in chain                |
| §4.4.5  | Defect Impact              | Defect propagation through graph          |
| §4.4.6  | Entity Co-occurrence       | Entity relationship mapping               |

### Cross-Store Consistency

- `graph_consistency.py` — validates Postgres ↔ Neo4j consistency
- `saga.py` — saga pattern for multi-store transactional writes
- Postgres remains **source of truth**; Neo4j is a read-optimized projection

---

## §6 Vector Search / RAG

**Not implemented.** No `pgvector`, embedding, or semantic search functionality was found in the codebase. The system relies on structured claim extraction and graph-based queries rather than vector similarity search.

---

## §7 Enrichment Connectors

### Framework (`src/idis/services/enrichment/`)

- **Service** (`service.py`): orchestrates rights check → cache lookup → credential load → provider fetch → normalize → cache persist → audit
- **Registry** (`registry.py`): `EnrichmentProviderRegistry` for connector registration
- **Rights Gate** (`rights_gate.py`): environment-mode-aware access control
- **Cache Policy** (`cache_policy.py`): TTL-based caching per provider
- **Models** (`models.py`): `EnrichmentRequest`, `EnrichmentResult`, `EnrichmentStatus`
- **Credential Repository** (`persistence/repositories/enrichment_credentials.py`): tenant-scoped BYOL credential storage

### 15 Registered Connectors (`src/idis/services/enrichment/connectors/`)

**GREEN (no auth required):**
- `edgar.py` — SEC EDGAR filings
- `hackernews.py` — HackerNews discussions (TTL 1800s)
- `world_bank.py` — World Bank data by jurisdiction (TTL 604800s)
- `gdelt.py` — GDELT event data (TTL 3600s)
- `patentsview.py` — USPTO patent data (TTL 604800s)
- `qatar_open_data.py` — Qatar Open Data Portal (TTL 86400s)
- `escwa_catalog.py` — UN ESCWA CKAN catalog (TTL 604800s)
- `escwa_ispar.py` — Arab Development Portal, 22 Arab states (TTL 604800s)

**GREEN (BYOL — Bring Your Own License):**
- `companies_house.py` — UK Companies House (Basic auth)
- `github.py` — GitHub API (Bearer token)
- `fred.py` — Federal Reserve Economic Data (API key)

**RED (BYOL — financial data):**
- `finnhub.py` — Finnhub market data (TTL 300s)
- `fmp.py` — Financial Modeling Prep (TTL 300s)

**YELLOW (no auth, specialized parsing):**
- `wayback.py` — Internet Archive Wayback Machine CDX API (TTL 86400s)
- `google_news_rss.py` — Google News RSS/XML (TTL 1800s)

---

## §8 Frontend

### Next.js UI (`ui/`)

- **Framework**: Next.js (React) with TypeScript
- **Node**: v20 (CI validated)
- **Styling**: Tailwind CSS (`globals.css`)
- **Linting**: ESLint (`.eslintrc.json`)

### Page Routes (`ui/src/app/`)

| Route       | Description          |
|-------------|----------------------|
| `/`         | Landing page         |
| `/login`    | Authentication       |
| `/deals`    | Deal management      |
| `/claims`   | Claim viewer         |
| `/runs`     | Pipeline run status  |
| `/audit`    | Audit log viewer     |

### API Proxy

`ui/src/app/api/` — Next.js API routes proxying to the backend FastAPI server.

### CI Validation

UI pipeline in CI: `npm ci` → `npm run lint` → `npm run typecheck` → `npm run test` → `npm run build`.

---

## §9 Models & Validators

### Domain Models (`src/idis/models/`)

| Model File                  | Key Types                                              |
|-----------------------------|--------------------------------------------------------|
| `claim.py`                  | Claim with metric type, value, time period             |
| `sanad.py`                  | Sanad chain with grade (A/B/C/D), evidence links       |
| `calc_sanad.py`             | CalcSanad: formula_hash, code_version, reproducibility_hash, input_claim_ids |
| `deterministic_calculation.py` | CalcType, CalcInputs, CalcOutput, DeterministicCalculation |
| `document.py`               | Document metadata                                      |
| `document_span.py`          | Document text spans with positional info               |
| `document_artifact.py`      | Parsed document artifacts                              |
| `evidence_item.py`          | Evidence items linking spans to claims                 |
| `debate.py`                 | DebateState, AgentOutput, RoleResult                   |
| `defect.py`                 | Defect records with severity and impact                |
| `deliverables.py`           | ScreeningSnapshot, ICMemo, TruthDashboard, QABrief, DeclineLetter, DeliverablesBundle |
| `muhasabah_record.py`       | Muḥāsabah validation records                           |
| `run_step.py`               | RunStep, StepName, StepStatus                          |
| `transmission_node.py`      | Graph transmission nodes                               |
| `value_structs.py`          | Shared value objects                                   |

### Validators (`src/idis/validators/`)

| Validator                  | Enforcement                                             |
|----------------------------|---------------------------------------------------------|
| `no_free_facts.py`         | **NFF**: every factual statement traces to `claim_id` or `calc_id` |
| `muhasabah.py`             | **Muḥāsabah gate**: falsifiability, uncertainty, counter-hypotheses required |
| `sanad_integrity.py`       | Sanad chain completeness — no orphaned claims/evidence  |
| `extraction_gate.py`       | Extraction confidence gate — blocks low-confidence claims |
| `deliverable.py`           | Deliverable-specific NFF validation (snapshot, memo, truth dashboard, QA brief, decline letter) |
| `audit_event_validator.py` | Audit event schema and completeness validation          |
| `schema_validator.py`      | Generic JSON schema validation                          |

### Trust Invariants

1. **No-Free-Facts (NFF)**: enforced at LLM tool wrapper, Muḥāsabah gate, and deliverables generator
2. **Deterministic Numerics**: all calculations via `calc/engine.py` with `Decimal` arithmetic, `CalcSanad` with reproducibility hash
3. **Sanad Chain Integrity**: source document → span → evidence item → claim, grades assigned by deterministic rules
4. **Muḥāsabah Gate**: mandatory falsifiability conditions, uncertainty acknowledgments, counter-hypotheses

---

## §10 Security, Compliance & Enterprise

### Authentication

**SSO via OIDC/JWT** (`src/idis/api/auth_sso.py`):
- JWKS fetching with TTL cache
- Full JWT validation: issuer, audience, exp, nbf, iat with clock skew tolerance
- IDIS-specific claims: `tenant_id`, `user_id`, `roles` (required); `data_region`, `policy_tags` (optional)
- Fail-closed on any validation failure

### Authorization

**RBAC/ABAC** (`src/idis/api/policy.py`):
- **Roles**: ANALYST, PARTNER, IC_MEMBER, ADMIN, AUDITOR, INTEGRATION_SERVICE
- **Deny-by-default**: operations must be in `POLICY_RULES` explicitly
- **AUDITOR immutability**: AUDITOR role cannot perform mutations
- **Deal-scoping**: deal-scoped operations require `deal_id`
- Enforced via `RBACMiddleware`

### Break-Glass Access (`src/idis/api/break_glass.py`)

- Time-bound HMAC-signed tokens with mandatory justification
- Actor-bound, optionally deal-scoped or tenant-wide
- Validation: well-formedness, signature, expiration, tenant/deal matching
- **Audit emission is fatal**: if audit fails, the override is denied

### BYOK — Bring Your Own Key (`src/idis/compliance/byok.py`)

- Key states: ACTIVE, REVOKED
- Data classification: CLASS_0 → CLASS_3
- Class2/3 data access denied if key is REVOKED
- Key operations (configure, rotate, revoke) emit CRITICAL audit events
- Audit emission failure = operation failure (fail-closed)

### Data Residency (`src/idis/compliance/residency.py`)

- Region pinning via `IDIS_SERVICE_REGION` environment variable
- `enforce_region_pin()` called for every `/v1/*` request
- Tenant `data_region` must match service region — mismatch = 403
- Missing/empty region = 403 (fail-closed)
- Generic error messages prevent existence leakage

### Data Retention & Legal Hold (`src/idis/compliance/retention.py`)

- Hold targets: DEAL, DOCUMENT, ARTIFACT
- Retention classes: RAW_DOCUMENTS, DELIVERABLES, AUDIT_EVENTS
- Active legal holds block hard deletion
- Hold creation/release emit CRITICAL audit events (fail-closed)
- `evaluate_retention()` determines if resource is within retention period

### Compliance-Enforced Storage (`src/idis/storage/compliant_store.py`)

`ComplianceEnforcedStore` wraps any `ObjectStore` with non-bypassable boundary enforcement:
- BYOK key revocation check for Class2/3 read/write
- Legal hold deletion protection
- Customer key metadata persisted as sidecar evidence

### Tenant Isolation

- All data scoped by `tenant_id`
- Postgres RLS enforced at DB level
- Cross-tenant access structurally impossible
- Failed tenant check reveals no resource existence (no existence oracle)

---

## §11 Infrastructure & CI/CD

### Dockerfile

Multi-stage production build (`Dockerfile`):
- **Builder stage**: `python:3.11-slim-bookworm` (pinned digest), virtualenv, pip install
- **Runtime stage**: non-root user, minimal runtime deps, port 8000
- **Health check**: `curl -f http://localhost:8000/health || exit 1` (30s interval, 3 retries)
- **Entrypoint**: `uvicorn idis.api.main:create_app --factory`

### Docker Compose

`docker-compose.yml` for local development with service dependencies.

### Kubernetes (`deploy/k8s/`)

8 manifests: `configmap.yaml`, `deployment.yaml`, `hpa.yaml`, `ingress.yaml`, `namespace.yaml`, `pdb.yaml`, `secrets.yaml`, `service.yaml`.

### Terraform (`deploy/terraform/`)

IaC for cloud infrastructure: `main.tf`, `variables.tf`, `outputs.tf`, `versions.tf`.

### CI Pipeline (`.github/workflows/ci.yml`)

6 parallel jobs on push/PR to `main`:

| Job                    | Description                                           |
|------------------------|-------------------------------------------------------|
| `ui-check`             | Node 20: npm ci → lint → typecheck → test → build     |
| `check`                | Python 3.11: `make check` (format + lint + typecheck + test + forbidden-scan) |
| `postgres-integration` | Postgres 16 service container, RLS + audit immutability tests |
| `evaluation-harness`   | GDBS-S evaluation harness (validate mode)             |
| `container-build`      | Docker build + health check + release manifest        |
| `k8s-validate`         | kubeconform v0.6.4 strict validation (K8s 1.29.0)     |
| `terraform-validate`   | `terraform fmt -check` + `terraform validate`         |

### Makefile

```
make format      — ruff format
make lint        — ruff check
make typecheck   — mypy src/idis
make test        — pytest
make forbidden-scan — scripts/forbidden_scan.py
make check       — all of the above in sequence
```

### Pre-Commit

`.pre-commit-config.yaml` for automated pre-commit hooks.

---

## §12 Monitoring & Observability

### OpenTelemetry Tracing (`src/idis/observability/tracing.py`)

- **OTLP or console exporter** configurable via env vars
- **Correlation attributes**: `request_id`, `tenant_id`, `actor_id`, `roles`, `operation_id`
- **DB and webhook instrumentation**
- **Fail-closed**: `IDIS_REQUIRE_OTEL=1` fails startup if tracing cannot initialize
- **Security**: never exports API keys, Authorization headers, request bodies, or secrets
- **Test mode**: in-memory exporter for tests (`IDIS_OTEL_TEST_CAPTURE=1`)

### Alert Rules (`src/idis/monitoring/alerts.py`)

8 Prometheus-compatible alert specifications:

| Alert                          | Severity | Trigger                                    |
|--------------------------------|----------|--------------------------------------------|
| API 5xx Error Rate             | SEV-2    | >1% 5xx for 5 min                          |
| Ingestion Failure Rate         | SEV-2    | >2% failure for 15 min                     |
| OCR Queue Time                 | SEV-3    | p95 >60 min for 30 min                     |
| Audit Ingestion Lag            | SEV-2    | >5 min lag                                 |
| Missing Audit Events           | SEV-1    | Mutating endpoint with no audit event       |
| NFF Validator Failure          | SEV-1    | NFF violation in deliverables pipeline      |
| Tenant Isolation Violation     | SEV-1    | Cross-tenant access signal                  |
| Calc Reproducibility Failure   | SEV-2    | >0.1% failure in 24h                       |

All alerts include severity label, runbook annotation, summary/description, and tenant-safe expressions.

### SLO Dashboards (`src/idis/monitoring/slo_dashboard.py`)

10 Grafana-compatible golden dashboards:

1. API Availability and Latency
2. Ingestion Throughput and Error Rates
3. Queue Depth and Backlog
4. Claim Registry Writes and Validator Rejects
5. Sanad Grading Distribution Drift
6. Calc Success Rate and Reproducibility
7. Debate Completion Rate and Max-Round Stops
8. Deliverable Generation Success Rate
9. Audit Event Ingestion Lag and Coverage
10. Integration Health

All dashboards enforce tenant isolation via required `tenant_id` variable and use deterministic JSON export with stable key ordering.

### Audit System (`src/idis/audit/`)

- **AuditSink** (`sink.py`): abstract sink interface with `emit()` method
- **PostgresAuditSink** (`postgres_sink.py`): production Postgres-backed audit storage
- **AuditQuery** (`query.py`): tenant-scoped audit log querying
- **Append-only, immutable**: audit write failure = request failure
- **Schema**: `event_id`, `tenant_id`, `actor`, `resource`, `action`, `request_id`

---

## §13 GDBS Dataset

### Golden Deal Benchmark Suite (`datasets/gdbs_full/`)

- **Version**: 1.0.0, immutable
- **Purpose**: deterministic, adversarial, realistic synthetic dataset for IDIS v6.3 end-to-end validation
- **Compatibility**: IDIS v6.3

### Manifest (`datasets/gdbs_full/manifest.json`)

- **100 deals**: clean and adversarial scenarios
- **Adversarial types**: contradiction, unit mismatch, missing evidence, inflated metrics, temporal inconsistency
- **Claims**: FINANCIAL, TRACTION, MARKET_SIZE with materiality levels
- **Rounding rules**: currency (nearest $1K), percentages (2 dp), burn/CAC (nearest $100), runway (1 dp), TAM (nearest $1M)
- **Actors and tenants**: predefined IDs for reproducible testing
- **Expected outcomes**: per-deal expected results for validation

### Structure

60 subdirectories under `gdbs_full/` including `actors/`, `audit_expectations/`, `deals/` (with per-deal directories containing documents, claims, and expected outputs).

### GDBS-S Evaluation Harness

CLI command `python -m idis test gdbs-s` runs the evaluation harness. CI validates with `gdbs_mini` fixture. Exit codes: 0 (PASS), 1 (FAIL), 2 (BLOCKED).

---

## §14 Configuration & Environment

### Environment Variables

186+ `os.environ.get` / `os.getenv` calls across 59 files. Key categories:

**Database:**
- `IDIS_PG_HOST`, `IDIS_PG_PORT`, `IDIS_PG_DB_NAME`
- `IDIS_PG_APP_USER`, `IDIS_PG_APP_PASSWORD`
- `IDIS_REQUIRE_POSTGRES` — fail if Postgres unavailable

**Compliance:**
- `IDIS_SERVICE_REGION` — data residency region pinning (required, fail-closed)

**Observability:**
- `IDIS_OTEL_ENABLED`, `IDIS_REQUIRE_OTEL`
- `IDIS_OTEL_SERVICE_NAME`, `IDIS_OTEL_EXPORTER`, `IDIS_OTEL_EXPORTER_OTLP_ENDPOINT`
- `IDIS_OTEL_EXPORTER_OTLP_PROTOCOL`, `IDIS_OTEL_RESOURCE_ATTRS`
- `IDIS_OTEL_TEST_CAPTURE`

**Application:**
- `IDIS_VERSION` — set in Dockerfile (6.3.0)

### Configuration Pattern

- Config via Pydantic `BaseSettings` + env vars, never hardcoded
- Fail-closed: missing required config raises structured errors at startup
- No defaults for security-critical settings (region, DB credentials)

---

## Architecture Summary

### Modular Monolith Structure

```
src/idis/
  api/           → FastAPI routes, middleware, auth, error model
  services/      → Business logic (ingestion, extraction, sanad, calc, debate, deliverables)
  models/        → Pydantic + SQLAlchemy models
  validators/    → Trust gates (NFF, Muḥāsabah, Sanad integrity)
  calc/          → Deterministic calculation engine (Decimal only)
  debate/        → LangGraph orchestrator, agent roles, stop conditions
  analysis/      → 8 specialist agents, scoring framework, analysis engine
  deliverables/  → Screening snapshot, IC memo, truth dashboard, QA brief, decline letter, PDF/DOCX export
  persistence/   → Postgres repos, Neo4j graph, Alembic migrations, saga
  storage/       → Object storage abstraction with compliance enforcement
  compliance/    → BYOK, data residency, retention/legal hold
  observability/ → OTel tracing
  monitoring/    → Alert rules, SLO dashboards
  audit/         → Append-only audit sink (Postgres-backed)
  pipeline/      → Pipeline executor and async worker
  enrichment/    → 15 external data connectors
```

### Debate System (`src/idis/debate/`)

LangGraph-based multi-agent adversarial debate:
- **Roles**: Advocate, Risk Officer, Contradiction Finder, Sanad Breaker, Arbiter
- **Stop conditions**: max rounds, consensus, critical defect
- **Muḥāsabah gate**: enforced on every agent output (no bypass path)
- **Gate failure**: halts run with `CRITICAL_DEFECT` stop reason

### Deliverables (`src/idis/deliverables/`)

5 deliverable types generated by `DeliverablesGenerator`:
- **Screening Snapshot** — initial deal overview
- **IC Memo** — investment committee memorandum
- **Truth Dashboard** — claim-level truth assertions with verdicts
- **QA Brief** — agent questions organized by topic
- **Decline Letter** — generated only when routing = DECLINE

All deliverables undergo NFF validation before bundling. Generation emits audit events (fail-closed).

### Deterministic Calculation Engine (`src/idis/calc/engine.py`)

- All arithmetic uses `Decimal` exclusively — no float operations
- `CalcSanad`: `formula_hash`, `code_version`, `reproducibility_hash`, `input_claim_ids`
- Same inputs + same code version = byte-identical output
- `CalcMissingInputError` (fail-closed on missing inputs)
- `CalcIntegrityError` (reproducibility hash mismatch = potential tampering)
- Formula registry in `src/idis/calc/formulas/`

### Test Suite

~2800+ tests in `tests/` mirroring `src/` structure:
- Unit tests for all services, models, validators
- Integration tests for Postgres (RLS, audit immutability, break attempts)
- Agent LLM tests (11 per agent × 8 agents)
- Scoring tests (116 tests)
- Deliverable tests (40 tests)
- Enrichment tests (~205 tests)
- GDBS evaluation harness
- Prompt contract tests

### Key Design Principles

1. **Fail-closed everywhere**: default deny, missing config = error, audit failure = request failure
2. **No-Free-Facts**: every factual statement traces to evidence
3. **Deterministic numerics**: LLMs never compute numbers
4. **Tenant isolation**: Postgres RLS + structural impossibility of cross-tenant access
5. **Audit completeness**: every mutation emits an audit event, append-only and immutable
6. **Sanad chain integrity**: unbroken provenance from source document to claim
