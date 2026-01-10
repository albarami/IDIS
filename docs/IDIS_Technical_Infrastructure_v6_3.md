# IDIS Technical Infrastructure Document v6.3

**Document Version:** 1.0.0
**System Version:** 6.3.0
**Last Updated:** 2026-01-10
**Status:** Reference Architecture

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Architecture Diagram](#2-architecture-diagram)
3. [Technology Stack](#3-technology-stack)
4. [API Layer](#4-api-layer)
5. [Middleware Pipeline](#5-middleware-pipeline)
6. [Authentication & Authorization](#6-authentication--authorization)
7. [Persistence Layer](#7-persistence-layer)
8. [Sanad Methodology Engine](#8-sanad-methodology-engine)
9. [Trust Invariant Validators](#9-trust-invariant-validators)
10. [Webhook Delivery System](#10-webhook-delivery-system)
11. [Observability Stack](#11-observability-stack)
12. [Storage Layer](#12-storage-layer)
13. [Security Architecture](#13-security-architecture)
14. [Deployment Configuration](#14-deployment-configuration)
15. [Environment Variables](#15-environment-variables)

---

## 1. System Overview

**IDIS (Institutional Deal Intelligence System)** is an enterprise-grade AI Investment Analyst Layer designed for Venture Capital firms. The system provides deterministic, auditable deal analysis with evidence grading based on Islamic scholarly methodology (Sanad/Hadith science).

### Core Design Principles

| Principle | Implementation |
|-----------|----------------|
| **Fail-Closed** | All validation errors reject requests; never default-allow |
| **Tenant Isolation** | Row-Level Security (RLS) enforced at database level |
| **No-Free-Facts** | Every factual assertion must reference `claim_id` or `calc_id` |
| **Deterministic Numerics** | Reproducible calculations with defined rounding rules |
| **Audit Completeness** | All mutations emit immutable audit events |
| **Deny-by-Default** | RBAC rejects unless explicitly permitted |

### System Boundaries

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              IDIS v6.3                                       │
├─────────────────────────────────────────────────────────────────────────────┤
│  External Clients (API Consumers)                                            │
│    └── Bearer Token / API Key Authentication                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│  API Gateway Layer                                                           │
│    ├── FastAPI Application                                                   │
│    ├── 8-Layer Middleware Stack                                              │
│    └── OpenAPI 3.0.3 Contract Enforcement                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│  Business Logic Layer                                                        │
│    ├── Sanad Methodology Engine (Grading, Defects, COI)                     │
│    ├── Trust Invariant Validators                                            │
│    └── Webhook Delivery Service                                              │
├─────────────────────────────────────────────────────────────────────────────┤
│  Persistence Layer                                                           │
│    ├── PostgreSQL 16 (Primary Store)                                         │
│    ├── Row-Level Security (RLS)                                              │
│    └── SQLite (Fallback Idempotency Store)                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│  Observability Layer                                                         │
│    ├── OpenTelemetry (Distributed Tracing)                                   │
│    ├── Structured Logging                                                    │
│    └── Audit Event Stream                                                    │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Architecture Diagram

### Request Flow

```
                                    ┌─────────────────┐
                                    │  API Consumer   │
                                    │  (VC Platform)  │
                                    └────────┬────────┘
                                             │
                                             │ HTTPS + Bearer Token
                                             ▼
┌────────────────────────────────────────────────────────────────────────────────┐
│                            MIDDLEWARE PIPELINE                                  │
│                         (Outermost → Innermost)                                │
├────────────────────────────────────────────────────────────────────────────────┤
│  1. RequestIdMiddleware      │ Inject X-Request-Id for tracing                 │
│  2. DBTransactionMiddleware  │ Open Postgres connection, begin transaction      │
│  3. AuditMiddleware          │ Capture mutations, emit audit events             │
│  4. OpenAPIValidationMiddleware │ Auth + JSON + Schema validation              │
│  5. RateLimitMiddleware      │ Tenant-scoped token bucket rate limiting         │
│  6. RBACMiddleware           │ Deny-by-default authorization                    │
│  7. TracingEnrichmentMiddleware │ Add tenant/actor to OpenTelemetry spans      │
│  8. IdempotencyMiddleware    │ Deduplicate requests, 409 on collision           │
└────────────────────────────────────────────────────────────────────────────────┘
                                             │
                                             ▼
┌────────────────────────────────────────────────────────────────────────────────┐
│                              ROUTE HANDLERS                                     │
├────────────────────────────────────────────────────────────────────────────────┤
│  /health                    │ Health check (no auth)                            │
│  /v1/tenants/me             │ Tenant context                                    │
│  /v1/deals                  │ Deal CRUD                                         │
│  /v1/webhooks               │ Webhook management                                │
└────────────────────────────────────────────────────────────────────────────────┘
                                             │
                          ┌──────────────────┼──────────────────┐
                          ▼                  ▼                  ▼
               ┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐
               │  Sanad Engine    │ │  Validators      │ │  Webhook Service │
               │  (Grading)       │ │  (Trust Gates)   │ │  (Delivery)      │
               └────────┬─────────┘ └────────┬─────────┘ └────────┬─────────┘
                        │                    │                    │
                        └────────────────────┼────────────────────┘
                                             │
                                             ▼
┌────────────────────────────────────────────────────────────────────────────────┐
│                           PERSISTENCE LAYER                                     │
├────────────────────────────────────────────────────────────────────────────────┤
│  PostgreSQL 16                                                                  │
│    ├── audit_events (append-only, RLS)                                         │
│    ├── idempotency_records (tenant-scoped)                                     │
│    ├── deals (tenant-scoped)                                                   │
│    └── webhooks (tenant-scoped)                                                │
│                                                                                 │
│  Row-Level Security: SET LOCAL idis.tenant_id = '<uuid>'                       │
└────────────────────────────────────────────────────────────────────────────────┘
```

### Component Interaction

```
┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│   FastAPI   │───▶│  Middleware │───▶│   Routes    │───▶│  Services   │
│    App      │    │   Stack     │    │  (Handlers) │    │  (Logic)    │
└─────────────┘    └─────────────┘    └─────────────┘    └──────┬──────┘
                                                                 │
      ┌──────────────────────────────────────────────────────────┤
      ▼                    ▼                    ▼                ▼
┌───────────┐      ┌───────────┐      ┌───────────┐      ┌───────────┐
│ PostgreSQL│      │  Audit    │      │ Webhook   │      │ Object    │
│   (RLS)   │      │  Sink     │      │ Delivery  │      │ Storage   │
└───────────┘      └───────────┘      └───────────┘      └───────────┘
```

---

## 3. Technology Stack

### Core Technologies

| Layer | Technology | Version | Purpose |
|-------|------------|---------|---------|
| **Runtime** | Python | 3.11+ | Primary language |
| **Web Framework** | FastAPI | 0.109+ | Async API framework |
| **ASGI Server** | Uvicorn | 0.27+ | Production server |
| **Validation** | Pydantic | 2.5+ | Data validation |
| **Database ORM** | SQLAlchemy | 2.0+ | Database abstraction |
| **Migrations** | Alembic | 1.13+ | Schema migrations |
| **Database** | PostgreSQL | 16+ | Primary datastore |
| **HTTP Client** | httpx | 0.26+ | Webhook delivery |
| **Schema Validation** | jsonschema | 4.21+ | JSON Schema validation |
| **Tracing** | OpenTelemetry | 1.22+ | Distributed tracing |

### Dependencies (from pyproject.toml)

```toml
[project]
dependencies = [
    "fastapi>=0.109.0",
    "uvicorn[standard]>=0.27.0",
    "pydantic>=2.5.0",
    "jsonschema>=4.21.0",
    "pyyaml>=6.0.1",
    "sqlalchemy>=2.0.0",
    "alembic>=1.13.0",
    "psycopg2-binary>=2.9.0",
    "httpx>=0.26.0",
    "opentelemetry-api>=1.22.0",
    "opentelemetry-sdk>=1.22.0",
    "opentelemetry-exporter-otlp>=1.22.0",
    "opentelemetry-instrumentation-fastapi>=0.43b0",
    "opentelemetry-instrumentation-sqlalchemy>=0.43b0",
    "opentelemetry-instrumentation-httpx>=0.43b0",
]
```

---

## 4. API Layer

### Application Factory

**Location:** `src/idis/api/main.py`

```python
def create_app(
    audit_sink: AuditSink | None = None,
    idempotency_store: SqliteIdempotencyStore | None = None,
    rate_limiter: TenantRateLimiter | None = None,
    postgres_audit_sink: PostgresAuditSink | None = None,
    postgres_idempotency_store: PostgresIdempotencyStore | None = None,
) -> FastAPI:
    """Create and configure the IDIS FastAPI application."""
```

### Route Modules

| Module | Prefix | Purpose |
|--------|--------|---------|
| `routes/health.py` | `/health` | Health check endpoint |
| `routes/tenancy.py` | `/v1/tenants` | Tenant context retrieval |
| `routes/deals.py` | `/v1/deals` | Deal CRUD operations |
| `routes/webhooks.py` | `/v1/webhooks` | Webhook management |

### OpenAPI Contract

**Location:** `openapi/IDIS_OpenAPI_v6_3.yaml`

- OpenAPI 3.0.3 specification
- Strict request/response validation
- All endpoints defined with schemas
- Security schemes: Bearer Auth, API Key

### Error Response Model

**Location:** `src/idis/api/error_model.py`

RFC 7807-compliant error envelope:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Request validation failed",
    "details": {...},
    "request_id": "uuid"
  }
}
```

---

## 5. Middleware Pipeline

### Execution Order (Outermost to Innermost)

```python
# In create_app() - added in reverse order (last = outermost)
app.add_middleware(IdempotencyMiddleware, ...)      # 8. Innermost
app.add_middleware(RBACMiddleware)                   # 7.
app.add_middleware(RateLimitMiddleware, ...)         # 6.
app.add_middleware(TracingEnrichmentMiddleware)      # 5.
app.add_middleware(OpenAPIValidationMiddleware)      # 4.
app.add_middleware(AuditMiddleware, ...)             # 3.
app.add_middleware(DBTransactionMiddleware)          # 2.
app.add_middleware(RequestIdMiddleware)              # 1. Outermost
```

### Middleware Details

#### 1. RequestIdMiddleware
**Location:** `src/idis/api/middleware/request_id.py`

- Generates UUID for each request
- Sets `request.state.request_id`
- Adds `X-Request-Id` response header

#### 2. DBTransactionMiddleware
**Location:** `src/idis/api/middleware/db_tx.py`

- Opens PostgreSQL connection from pool
- Begins transaction
- Sets tenant context via RLS: `SET LOCAL idis.tenant_id = '<uuid>'`
- Commits on success, rollbacks on error

#### 3. AuditMiddleware
**Location:** `src/idis/api/middleware/audit.py` (~280 lines)

- Applies to POST/PUT/PATCH/DELETE on `/v1`
- Builds v6.3-compliant AuditEvent
- Validates via `validate_audit_event()`
- Emits to PostgreSQL or JSONL sink
- **Fail-closed:** Returns 500 on validation/emission failure

#### 4. OpenAPIValidationMiddleware
**Location:** `src/idis/api/middleware/openapi_validate.py` (~550 lines)

- Auth precedence: 401 before JSON/schema validation
- JSON parsing: invalid JSON → 400 `INVALID_JSON`
- Schema validation: mismatch → 422 `INVALID_REQUEST`
- Exposes `operation_id`, `path_template`, `body_sha256` on request.state

#### 5. RateLimitMiddleware
**Location:** `src/idis/api/middleware/rate_limit.py` (~180 lines)

- Token bucket algorithm
- Tenant-scoped rate limiting
- Configurable limits per tenant tier
- Returns 429 on limit exceeded

#### 6. RBACMiddleware
**Location:** `src/idis/api/middleware/rbac.py` (~160 lines)

- Deny-by-default authorization
- Requires `openapi_operation_id` (fail-closed if missing)
- Extracts resource context from path params
- Calls `policy_check()` → 403 on denial

#### 7. TracingEnrichmentMiddleware
**Location:** `src/idis/api/middleware/tracing.py` (~100 lines)

- Adds tenant_id, actor_id, roles to OpenTelemetry spans
- Enriches spans with request context

#### 8. IdempotencyMiddleware
**Location:** `src/idis/api/middleware/idempotency.py` (~302 lines)

- Tenant-scoped idempotency
- Applies to POST/PATCH on `/v1` with `Idempotency-Key` header
- Replay: same key + same payload → return stored response
- Collision: same key + different payload → 409 `IDEMPOTENCY_KEY_CONFLICT`
- **Fail-closed:** 500 if store unavailable

---

## 6. Authentication & Authorization

### Authentication Flow

**Location:** `src/idis/api/auth.py`

```
Request with Authorization: Bearer <token>
           │
           ▼
┌──────────────────────────────┐
│   authenticate_request()     │
│   - Validate token format    │
│   - Extract tenant_id        │
│   - Extract actor_id         │
│   - Extract roles            │
└──────────────────────────────┘
           │
           ▼
┌──────────────────────────────┐
│   TenantContext              │
│   - tenant_id: str           │
│   - actor_id: str            │
│   - name: str                │
│   - roles: list[str]         │
└──────────────────────────────┘
```

### Authorization (RBAC)

**Location:** `src/idis/api/policy.py` (~320 lines)

```python
def policy_check(
    tenant_id: str,
    actor_id: str,
    roles: list[str],
    operation_id: str,
    method: str,
    deal_id: str | None = None,
    claim_id: str | None = None,
    doc_id: str | None = None,
    run_id: str | None = None,
    debate_id: str | None = None,
) -> PolicyDecision:
    """Deny-by-default RBAC policy check."""
```

### Policy Decision Model

```python
@dataclass
class PolicyDecision:
    allow: bool
    code: str           # "RBAC_DENIED", "RBAC_ALLOWED"
    message: str
    details: dict | None
```

---

## 7. Persistence Layer

### Database Configuration

**Location:** `src/idis/persistence/db.py` (~252 lines)

| Environment Variable | Purpose |
|---------------------|---------|
| `IDIS_DATABASE_URL` | Application connection string (non-superuser) |
| `IDIS_DATABASE_ADMIN_URL` | Admin connection string (migrations/tests) |

### Connection Management

```python
def get_app_engine() -> Engine:
    """Get or create the application database engine."""
    # Pool size: 5, max overflow: 10
    # Pool pre-ping enabled
    # OpenTelemetry instrumentation

@contextmanager
def begin_app_conn() -> Generator[Connection, None, None]:
    """Context manager for app connection with transaction."""

def set_tenant_local(conn: Connection, tenant_id: str) -> None:
    """Set tenant context for RLS."""
    conn.execute(text(f"SET LOCAL idis.tenant_id = '{tenant_id}'"))
```

### Database Migrations

**Location:** `src/idis/persistence/migrations/versions/`

| Migration | Purpose |
|-----------|---------|
| `0001_postgres_foundation.py` | Core tables, RLS, immutability triggers |
| `0002_rls_nullif_empty_tenant.py` | RLS enhancements |
| `0003_webhooks_foundation.py` | Webhook tables |
| `0004_ingestion_gate_storage.py` | Ingestion gate schema |

### Core Tables

```sql
-- audit_events: Append-only audit log
CREATE TABLE audit_events (
    event_id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    event_type TEXT NOT NULL,
    request_id TEXT,
    idempotency_key TEXT,
    event JSONB NOT NULL
);
ALTER TABLE audit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_events FORCE ROW LEVEL SECURITY;

-- idempotency_records: Tenant-scoped deduplication
CREATE TABLE idempotency_records (
    tenant_id UUID NOT NULL,
    actor_id TEXT NOT NULL,
    method TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    status_code INTEGER NOT NULL,
    media_type TEXT NOT NULL,
    body_bytes BYTEA NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (tenant_id, actor_id, method, operation_id, idempotency_key)
);

-- deals: Basic deal storage
CREATE TABLE deals (
    deal_id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL,
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);
```

### Row-Level Security (RLS)

```sql
-- RLS Policy: Tenant can only see own data
CREATE POLICY tenant_isolation ON audit_events
    USING (tenant_id = NULLIF(current_setting('idis.tenant_id', true), '')::uuid);

-- Application sets tenant context per-transaction
SET LOCAL idis.tenant_id = '00000000-0000-0000-0000-000000000001';
```

---

## 8. Sanad Methodology Engine

### Overview

The Sanad (سند) methodology adapts Islamic Hadith science for evidence grading. Each claim's evidence chain is graded A/B/C/D based on source quality, corroboration, and defects.

**Location:** `src/idis/services/sanad/`

### Components

| Module | Lines | Purpose |
|--------|-------|---------|
| `grader.py` | ~374 | Unified v2 grading algorithm |
| `source_tiers.py` | ~250 | PRIMARY/SECONDARY/TERTIARY classification |
| `dabt.py` | ~280 | Data quality (Ḍabṭ) scoring |
| `tawatur.py` | ~450 | Corroboration (Tawātur) assessment |
| `shudhudh.py` | ~478 | Anomaly (Shudhūdh) detection |
| `ilal.py` | ~550 | Defect (ʿIlal) detection |
| `coi.py` | ~450 | Conflict of interest assessment |

### Grading Algorithm

```
┌─────────────────────────────────────────────────────────────────────┐
│                    SANAD GRADING ALGORITHM v2                        │
├─────────────────────────────────────────────────────────────────────┤
│  1. ASSIGN SOURCE TIER                                               │
│     PRIMARY (A) → Direct financial statements, audited reports       │
│     SECONDARY (B) → Management presentations, investor decks         │
│     TERTIARY (C) → Third-party estimates, news articles             │
│                                                                      │
│  2. CALCULATE BASE GRADE                                             │
│     tier_to_base_grade(source_tier) → A/B/C                         │
│                                                                      │
│  3. CALCULATE DABT SCORE                                             │
│     Data quality dimensions → 0.0-1.0 score                          │
│     If dabt_score < 0.50 → Cap at B                                  │
│                                                                      │
│  4. ASSESS TAWATUR (CORROBORATION)                                   │
│     Count independent sources                                        │
│     MUTAWATIR (≥4 independent) → Upgrade one grade                   │
│     AHAD_2 (2-3 sources) → Eligible for upgrade                      │
│     AHAD_1 (1 source) → No upgrade                                   │
│                                                                      │
│  5. DETECT I'LAL (DEFECTS)                                           │
│     FATAL defects → Force Grade D                                    │
│     MAJOR defects → Downgrade one level                              │
│     MINOR defects → Warning only                                     │
│                                                                      │
│  6. DETECT SHUDHUDH (ANOMALIES)                                      │
│     Reconciliation attempts: Unit, Time Window, Rounding             │
│     Unreconciled contradictions → MAJOR defect                       │
│                                                                      │
│  7. EVALUATE COI (CONFLICTS OF INTEREST)                             │
│     Source independence assessment                                   │
│     Potential cap on grade                                           │
│                                                                      │
│  8. APPLY FINAL GRADE                                                │
│     Combine all modifiers deterministically                          │
│     Return SanadGradeResult with full explanation                    │
└─────────────────────────────────────────────────────────────────────┘
```

### Grade Result Structure

```python
@dataclass
class SanadGradeResult:
    grade: str                      # "A", "B", "C", "D"
    explanation: GradeExplanation   # Full rationale
    source_tier: SourceTier         # PRIMARY/SECONDARY/TERTIARY
    dabt: DabtScore                 # Data quality score
    tawatur: TawaturResult          # Corroboration assessment
    shudhudh: ShudhuhResult | None  # Anomaly detection
    ilal_defects: list[IlalDefect]  # Detected defects
    coi_evaluations: list[COIEvaluationResult]
    all_defects: list[DefectSummary]
```

### Defect Types

```python
VALID_DEFECT_TYPES = {
    "BROKEN_CHAIN",           # Sanad chain integrity failure
    "MISSING_LINK",           # Missing evidence reference
    "UNKNOWN_SOURCE",         # Unverifiable source
    "CONCEALMENT",            # Hidden or obscured data
    "INCONSISTENCY",          # Contradictory values
    "ANOMALY_VS_STRONGER_SOURCES",  # Lower-tier contradicts higher
    "CHRONO_IMPOSSIBLE",      # Chronological impossibility
    "CHAIN_GRAFTING",         # Artificial chain connection
    "CIRCULARITY",            # Circular reference
    "STALENESS",              # Outdated evidence
    "UNIT_MISMATCH",          # Unit conversion error
    "TIME_WINDOW_MISMATCH",   # FY vs LTM confusion
    "SCOPE_DRIFT",            # Definition change
    "IMPLAUSIBILITY",         # Unrealistic values
}
```

---

## 9. Trust Invariant Validators

### Overview

Hard gates that enforce IDIS trust guarantees. All validators are **fail-closed**.

**Location:** `src/idis/validators/`

### Validator Summary

| Validator | Lines | Enforces |
|-----------|-------|----------|
| `no_free_facts.py` | ~355 | Every fact needs `claim_id` or `calc_id` |
| `muhasabah.py` | ~330 | Self-audit for agent outputs |
| `sanad_integrity.py` | ~813 | Evidence chain integrity |
| `audit_event_validator.py` | ~600 | Audit event structure |
| `schema_validator.py` | ~200 | JSON Schema validation |

### No-Free-Facts Validator

**Purpose:** Ensures IC-bound outputs have evidence backing.

```python
def validate_no_free_facts(data: Any) -> ValidationResult:
    """
    HARD GATE: Any factual assertion in IC-bound outputs MUST reference:
    - claim_id (with Sanad chain), OR
    - calc_id (with Calc-Sanad lineage)

    If not, the output MUST be labeled SUBJECTIVE or rejected.
    """
```

**Factual Pattern Detection:**
```python
FACTUAL_PATTERNS = [
    r"\$[\d,]+(?:\.\d+)?(?:\s*(?:M|B|K))?",  # Currency amounts
    r"\d+(?:\.\d+)?%",                         # Percentages
    r"\d+x\s+(?:growth|increase)",             # Growth rates
    r"(?:ARR|MRR|revenue)\s+(?:of\s+)?\$?",   # Financial metrics
    # ... more patterns
]
```

### Muḥāsabah Validator

**Purpose:** Self-accounting for agent outputs.

```python
def validate_muhasabah(record: dict[str, Any]) -> ValidationResult:
    """
    HARD GATE: All agent outputs MUST carry MuḥāsabahRecord with:
    - supported_claim_ids (non-empty unless SUBJECTIVE)
    - uncertainty register (mandatory when confidence > 0.80)
    - falsifiability tests (mandatory for recommendations)
    """
```

### Sanad Integrity Validator

**Purpose:** Evidence chain structural integrity.

```python
def validate_sanad_integrity(sanad: dict[str, Any]) -> ValidationResult:
    """
    Enforces:
    1. Claim has sanad
    2. Sanad has primary evidence
    3. Transmission nodes are well-formed
    4. Grade/verdict/action separation is valid
    5. Defect structure is valid
    6. Chain linkage validity (no cycles, orphans, multiple roots)
    7. UUID format validation
    """
```

### Validation Result Model

```python
@dataclass
class ValidationResult:
    passed: bool
    errors: list[ValidationError]
    warnings: list[ValidationError] | None

    @classmethod
    def fail_closed(cls, message: str) -> "ValidationResult":
        """Create fail-closed result for invalid input."""

    @classmethod
    def success(cls, warnings: list | None = None) -> "ValidationResult":
        """Create success result."""

    @classmethod
    def fail(cls, errors: list[ValidationError]) -> "ValidationResult":
        """Create failure result with specific errors."""
```

---

## 10. Webhook Delivery System

### Architecture

**Location:** `src/idis/services/webhooks/`

| Module | Lines | Purpose |
|--------|-------|---------|
| `delivery.py` | ~298 | HTTP delivery with OpenTelemetry |
| `retry.py` | ~220 | Exponential backoff retry logic |
| `signing.py` | ~140 | HMAC-SHA256 request signing |
| `service.py` | ~280 | Registration and dispatch |

### Delivery Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│                     WEBHOOK DELIVERY FLOW                            │
├─────────────────────────────────────────────────────────────────────┤
│  1. EVENT TRIGGER                                                    │
│     └── deal.created, claim.updated, etc.                           │
│                                                                      │
│  2. PAYLOAD CONSTRUCTION                                             │
│     └── JSON payload with event data                                 │
│                                                                      │
│  3. HMAC SIGNING                                                     │
│     └── SHA-256 signature: X-IDIS-Signature header                   │
│                                                                      │
│  4. DELIVERY ATTEMPT                                                 │
│     └── POST to target URL with OpenTelemetry span                   │
│                                                                      │
│  5. RETRY ON FAILURE                                                 │
│     └── Exponential backoff: 1s, 2s, 4s, 8s, ... (max 5 attempts)   │
│                                                                      │
│  6. RESULT RECORDING                                                 │
│     └── Success/failure with attempt metadata                        │
└─────────────────────────────────────────────────────────────────────┘
```

### Delivery Result

```python
@dataclass(frozen=True)
class DeliveryResult:
    success: bool
    status_code: int | None
    error: str | None
    attempt_id: str
    duration_ms: int
```

### Security Measures

1. **URL Sanitization:** No credentials/querystrings in span attributes
2. **Header Filtering:** No Authorization/API-Key in outbound headers
3. **HMAC Signing:** SHA-256 signature for payload verification

---

## 11. Observability Stack

### OpenTelemetry Configuration

**Location:** `src/idis/observability/tracing.py` (~350 lines)

### Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `IDIS_OTEL_ENABLED` | `0` | Enable tracing |
| `IDIS_REQUIRE_OTEL` | `0` | Fail startup if tracing fails |
| `IDIS_OTEL_SERVICE_NAME` | `idis` | Service name for spans |
| `IDIS_OTEL_EXPORTER` | `otlp` | Exporter type: `otlp` or `console` |
| `IDIS_OTEL_EXPORTER_OTLP_ENDPOINT` | - | OTLP endpoint URL |
| `IDIS_OTEL_EXPORTER_OTLP_PROTOCOL` | `grpc` | Protocol: `grpc` or `http` |
| `IDIS_OTEL_RESOURCE_ATTRS` | - | Comma-separated k=v attributes |
| `IDIS_OTEL_TEST_CAPTURE` | `0` | In-memory exporter for tests |

### Instrumentation

```python
# Auto-instrumented components
instrument_fastapi(app)      # FastAPI endpoints
instrument_sqlalchemy(engine) # Database queries
instrument_httpx()           # Outbound HTTP (webhooks)
```

### Span Attributes (Security-Sanitized)

**Allowed:**
- `idis.tenant_id`
- `idis.actor_id`
- `idis.request_id`
- `idis.operation_id`
- `http.method`, `http.status_code`

**Never Exported:**
- API keys, Authorization headers
- Request/response bodies
- Full SQL with bound parameters
- Secrets or credentials

### Audit Trail

**Location:** `src/idis/audit/`

| Component | Purpose |
|-----------|---------|
| `sink.py` | Abstract AuditSink interface |
| `postgres_sink.py` | PostgreSQL in-transaction audit |

**Audit Event Structure:**
```json
{
  "event_id": "uuid",
  "occurred_at": "2026-01-10T12:00:00Z",
  "tenant_id": "uuid",
  "actor": {
    "actor_type": "SERVICE",
    "actor_id": "name",
    "roles": ["ANALYST"],
    "ip": "192.168.1.1",
    "user_agent": "..."
  },
  "request": {
    "request_id": "uuid",
    "method": "POST",
    "path": "/v1/deals",
    "status_code": 201
  },
  "resource": {
    "resource_type": "deal",
    "resource_id": "uuid"
  },
  "event_type": "deal.created",
  "severity": "MEDIUM",
  "summary": "deal.created via POST /v1/deals",
  "payload": {
    "hashes": ["sha256:..."],
    "refs": []
  }
}
```

---

## 12. Storage Layer

### Object Storage

**Location:** `src/idis/storage/`

| Module | Purpose |
|--------|---------|
| `object_store.py` | Abstract ObjectStore interface |
| `filesystem_store.py` | Local filesystem implementation |
| `models.py` | StoredObject, StoredObjectMetadata |
| `errors.py` | Storage-specific exceptions |
| `tracing.py` | OpenTelemetry integration |

### Filesystem Store Features

- **Tenant Isolation:** Physical directory namespacing
- **Path Traversal Protection:** Rejects `..`, absolute paths, null bytes
- **Content Addressing:** SHA256 hashing
- **Versioning:** "latest" pointer with content-addressed versions

### Storage Models

```python
@dataclass
class StoredObject:
    key: str
    content: bytes
    content_type: str
    sha256: str
    size: int
    created_at: datetime
    metadata: dict[str, str]

@dataclass
class StoredObjectMetadata:
    key: str
    content_type: str
    sha256: str
    size: int
    created_at: datetime
    version: str | None
```

---

## 13. Security Architecture

### Defense in Depth

```
┌─────────────────────────────────────────────────────────────────────┐
│                        SECURITY LAYERS                               │
├─────────────────────────────────────────────────────────────────────┤
│  Layer 1: NETWORK                                                    │
│    └── HTTPS/TLS encryption                                          │
│                                                                      │
│  Layer 2: AUTHENTICATION                                             │
│    └── Bearer token validation                                       │
│    └── Tenant context extraction                                     │
│                                                                      │
│  Layer 3: AUTHORIZATION                                              │
│    └── Deny-by-default RBAC                                          │
│    └── Operation-level permissions                                   │
│    └── Resource-level access control                                 │
│                                                                      │
│  Layer 4: TENANT ISOLATION                                           │
│    └── PostgreSQL Row-Level Security                                 │
│    └── SET LOCAL idis.tenant_id per transaction                      │
│    └── Physical directory isolation (storage)                        │
│                                                                      │
│  Layer 5: INPUT VALIDATION                                           │
│    └── OpenAPI schema validation                                     │
│    └── JSON Schema enforcement                                       │
│    └── Path traversal protection                                     │
│                                                                      │
│  Layer 6: AUDIT TRAIL                                                │
│    └── Immutable audit events                                        │
│    └── All mutations logged                                          │
│    └── 7-year retention                                              │
└─────────────────────────────────────────────────────────────────────┘
```

### Security Controls

| Control | Implementation |
|---------|----------------|
| **Authentication** | Bearer token with tenant/actor extraction |
| **Authorization** | RBAC with deny-by-default |
| **Tenant Isolation** | PostgreSQL RLS + directory isolation |
| **Input Validation** | OpenAPI + JSON Schema |
| **Rate Limiting** | Token bucket per tenant |
| **Idempotency** | SHA256 payload hashing |
| **Audit Logging** | Immutable PostgreSQL table |
| **Secret Protection** | Never logged/traced |

### Fail-Closed Behaviors

| Scenario | Response |
|----------|----------|
| Invalid token | 401 Unauthorized |
| Missing operation_id | 403 RBAC_DENIED |
| Schema validation failure | 422 INVALID_REQUEST |
| Invalid JSON | 400 INVALID_JSON |
| Audit emission failure | 500 AUDIT_EMIT_FAILED |
| Idempotency store unavailable | 500 IDEMPOTENCY_STORE_FAILED |
| RLS tenant_id mismatch | Empty result set |

---

## 14. Deployment Configuration

### CI/CD Pipeline

**Location:** `.github/workflows/ci.yml`

```yaml
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -e ".[dev]"
      - run: make check

  postgres-integration:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_USER: postgres
          POSTGRES_PASSWORD: postgres
        ports:
          - 5432/tcp
    steps:
      - run: python scripts/pg_bootstrap_ci.py
      - run: pytest tests/test_postgres_rls_and_audit_immutability.py
```

### Make Targets

```makefile
make check       # Run all quality gates
make format      # Format code with ruff
make lint        # Lint with ruff
make typecheck   # Type check with mypy
make test        # Run pytest
make forbidden-scan  # Scan for forbidden patterns
```

### Production Startup

```bash
# Install
pip install -e ".[dev]"

# Configure environment
export IDIS_DATABASE_URL="postgresql://user:pass@host:5432/idis"
export IDIS_OTEL_ENABLED=1
export IDIS_OTEL_EXPORTER=otlp
export IDIS_OTEL_EXPORTER_OTLP_ENDPOINT="http://otel-collector:4317"

# Run migrations
alembic upgrade head

# Start server
uvicorn idis.api.main:create_app --factory --host 0.0.0.0 --port 8000
```

---

## 15. Environment Variables

### Core Configuration

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `IDIS_DATABASE_URL` | Yes | - | PostgreSQL connection string |
| `IDIS_DATABASE_ADMIN_URL` | No | - | Admin connection (migrations) |

### Observability

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `IDIS_OTEL_ENABLED` | No | `0` | Enable OpenTelemetry |
| `IDIS_REQUIRE_OTEL` | No | `0` | Fail if tracing fails |
| `IDIS_OTEL_SERVICE_NAME` | No | `idis` | Service name |
| `IDIS_OTEL_EXPORTER` | No | `otlp` | Exporter: `otlp`/`console` |
| `IDIS_OTEL_EXPORTER_OTLP_ENDPOINT` | No | - | OTLP endpoint |
| `IDIS_OTEL_EXPORTER_OTLP_PROTOCOL` | No | `grpc` | Protocol: `grpc`/`http` |

### Storage

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `IDIS_OBJECT_STORE_BASE_DIR` | No | `tempdir/idis_objects` | Object storage path |

### Testing

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `IDIS_OTEL_TEST_CAPTURE` | No | `0` | In-memory span capture |

---

## Appendix A: File Structure

```
src/idis/
├── __init__.py                 # Package init, version
├── __main__.py                 # CLI entry point
├── app.py                      # Basic app skeleton
├── cli.py                      # Command-line interface
├── api/
│   ├── main.py                 # Application factory
│   ├── auth.py                 # Authentication
│   ├── errors.py               # Exception definitions
│   ├── error_model.py          # Error response model
│   ├── policy.py               # RBAC policy engine
│   ├── openapi_loader.py       # OpenAPI spec loader
│   ├── middleware/
│   │   ├── audit.py            # Audit middleware
│   │   ├── db_tx.py            # DB transaction middleware
│   │   ├── idempotency.py      # Idempotency middleware
│   │   ├── openapi_validate.py # OpenAPI validation
│   │   ├── rate_limit.py       # Rate limiting
│   │   ├── rbac.py             # RBAC enforcement
│   │   ├── request_id.py       # Request ID injection
│   │   └── tracing.py          # Tracing enrichment
│   └── routes/
│       ├── deals.py            # Deal endpoints
│       ├── health.py           # Health check
│       ├── tenancy.py          # Tenant endpoints
│       └── webhooks.py         # Webhook endpoints
├── services/
│   ├── sanad/
│   │   ├── grader.py           # Unified grading
│   │   ├── source_tiers.py     # Source classification
│   │   ├── dabt.py             # Data quality
│   │   ├── tawatur.py          # Corroboration
│   │   ├── shudhudh.py         # Anomaly detection
│   │   ├── ilal.py             # Defect detection
│   │   └── coi.py              # Conflict of interest
│   └── webhooks/
│       ├── delivery.py         # HTTP delivery
│       ├── retry.py            # Retry logic
│       ├── signing.py          # HMAC signing
│       └── service.py          # Registration/dispatch
├── validators/
│   ├── no_free_facts.py        # No-Free-Facts gate
│   ├── muhasabah.py            # Self-audit gate
│   ├── sanad_integrity.py      # Chain integrity
│   ├── audit_event_validator.py # Audit validation
│   └── schema_validator.py     # JSON Schema
├── persistence/
│   ├── db.py                   # Database connectivity
│   └── migrations/
│       └── versions/           # Alembic migrations
├── audit/
│   ├── sink.py                 # Audit sink interface
│   └── postgres_sink.py        # PostgreSQL sink
├── idempotency/
│   ├── store.py                # SQLite store
│   └── postgres_store.py       # PostgreSQL store
├── rate_limit/
│   └── limiter.py              # Token bucket limiter
├── observability/
│   └── tracing.py              # OpenTelemetry config
├── storage/
│   ├── object_store.py         # Abstract interface
│   ├── filesystem_store.py     # Filesystem backend
│   ├── models.py               # Storage models
│   └── errors.py               # Storage errors
└── testing/
    └── gdbs_loader.py          # Test dataset loader
```

---

## Appendix B: Test Coverage

| Category | Files | Coverage |
|----------|-------|----------|
| Middleware | 7 | Idempotency, RBAC, Rate Limit, Audit, OpenAPI |
| Validators | 6 | No-Free-Facts, Muḥāsabah, Sanad Integrity |
| Integration | 4 | PostgreSQL RLS, GDBS dataset |
| Services | 2 | Webhook signing, retry |
| API | 4 | Health, tenancy, error model |
| GDBS | 3 | 100-deal golden dataset |

**Total:** 28 test files, ~13,000 lines

---

*Document generated from IDIS v6.3.0 codebase analysis*
