# IDIS Data Model and Schema — v6.3 (Canonical)

**Source**: IDIS VC Edition v6.3 (January 2026)  
**Purpose**: Define enterprise-grade canonical data contracts for the IDIS platform, including:
- relational schema (PostgreSQL),
- graph schema (Sanad + Knowledge Graph),
- JSON schemas for key objects,
- event schemas (pipeline/audit),
- enumerations and computed fields.

This document is written as instructions to the implementing engineer/AI coder. “MUST” indicates a normative requirement.

---

## 1. Design Principles

1. **Evidence-First**: Every material fact must be representable as a `Claim` with a linked `Sanad`.
2. **No-Free-Facts**: The platform MUST reject factual outputs that cannot be represented as a `Claim` or `DeterministicCalculation`.
3. **Two-Tier Grading**:
   - Tier 1: `EvidenceItem.source_grade` (A/B/C/D) (+ optional internal `source_rank_subgrade`)
   - Tier 2: `Sanad.sanad_grade` (claim-level A/B/C/D) computed by the normative algorithm.
4. **Audit-Complete**: Every stage produces immutable audit artifacts addressable by IDs.
5. **Multi-Tenant by Default**: Every table MUST include `tenant_id`.

---

## 2. Canonical Entities

### 2.1 Core Objects

- Tenant
- User / Actor
- Deal
- DealArtifact (raw files + connectors)
- Document (parsed representation)
- DocumentSpan (page/paragraph/cell/timecode)
- Entity (company, founder, product, competitor, investor)
- Claim (atomic fact)
- EvidenceItem (source instance and its grading)
- Sanad (claim-level chain, grade, corroboration, defects)
- TransmissionNode (step in chain of custody/transformation)
- Defect (ʿIlal-inspired structured fault)
- DeterministicCalculation (calc engine output)
- CalcSanad (provenance for calculations)
- Agent (role + prompt config)
- AgentOutput (analysis chunks)
- MuḥāsabahRecord (required for every AgentOutput)
- DebateRun / DebateRound / DebateMessage / DebateState
- Deliverable (IC memo, snapshot, Q&A)
- HumanApproval / Override (verification gates)
- GovernanceMetric (drift, coverage, defect rates)
- IntegrationSync (CRM/doc connectors)

---

## 3. Relational Schema (PostgreSQL) — Recommended DDL

> Notes:
> - Use UUIDs for all primary keys.
> - Use `jsonb` columns where sub-structures evolve quickly, but keep query-critical fields normalized.
> - All tables MUST include: `tenant_id`, `created_at`, `updated_at`.

### 3.1 Tenancy & Identity

```sql
CREATE TABLE tenants (
  tenant_id uuid PRIMARY KEY,
  name text NOT NULL,
  data_residency_region text NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE actors (
  actor_id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenants(tenant_id),
  email text NOT NULL,
  display_name text NOT NULL,
  role text NOT NULL, -- Analyst | Partner | IC | Compliance | Admin | System
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, email)
);
```

### 3.2 Deal + Artifacts

```sql
CREATE TABLE deals (
  deal_id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenants(tenant_id),
  external_ref text NULL,                 -- DealCloud/Affinity/etc.
  company_name text NOT NULL,
  stage text NULL,                        -- Seed/SeriesA/SeriesB/Growth
  sector text NULL,
  status text NOT NULL DEFAULT 'INGESTED',-- INGESTED|TRIAGED|IN_REVIEW|IC_READY|DECLINED|INVESTED
  materiality_threshold numeric NULL,     -- per-tenant/per-deal override
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE deal_artifacts (
  artifact_id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenants(tenant_id),
  deal_id uuid NOT NULL REFERENCES deals(deal_id),
  artifact_type text NOT NULL,            -- PITCH_DECK|FIN_MODEL|DATA_ROOM|TRANSCRIPT|NOTE
  storage_uri text NOT NULL,              -- s3://... or blob://...
  connector_type text NULL,               -- DocSend|Drive|Dropbox|SharePoint|Upload
  connector_ref text NULL,                -- provider file id / link
  sha256 text NOT NULL,
  version_label text NULL,
  ingested_at timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_deal_artifacts_deal ON deal_artifacts(tenant_id, deal_id);
```

### 3.3 Documents + Spans

```sql
CREATE TABLE documents (
  document_id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenants(tenant_id),
  deal_id uuid NOT NULL REFERENCES deals(deal_id),
  artifact_id uuid NOT NULL REFERENCES deal_artifacts(artifact_id),
  doc_type text NOT NULL,                 -- PDF|PPTX|XLSX|DOCX|AUDIO|VIDEO
  parse_status text NOT NULL DEFAULT 'PENDING', -- PENDING|PARSED|FAILED
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE document_spans (
  span_id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenants(tenant_id),
  document_id uuid NOT NULL REFERENCES documents(document_id),
  span_type text NOT NULL,                -- PAGE_TEXT|PARAGRAPH|CELL|TIMECODE
  locator jsonb NOT NULL,                 -- {page:1, bbox:[...]} or {sheet:"P&L", cell:"B12"} or {t_ms:12345}
  text_excerpt text NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_spans_doc ON document_spans(tenant_id, document_id);
```

### 3.4 Claims + Evidence + Sanad

```sql
CREATE TABLE claims (
  claim_id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenants(tenant_id),
  deal_id uuid NOT NULL REFERENCES deals(deal_id),
  claim_text text NOT NULL,
  claim_class text NOT NULL,              -- FINANCIAL|TRACTION|MARKET_SIZE|COMPETITION|TEAM|LEGAL_TERMS|TECHNICAL|OTHER (category)
  claim_type text NOT NULL DEFAULT 'primary', -- primary|derived (lineage for calc loop guardrail)
  value_struct jsonb NOT NULL DEFAULT '{}'::jsonb, -- ValueStruct typed value (see §5.4)
  materiality text NOT NULL DEFAULT 'MEDIUM',      -- LOW|MEDIUM|HIGH|CRITICAL
  claim_verdict text NOT NULL DEFAULT 'UNVERIFIED', -- VERIFIED|CONTRADICTED|INFLATED|UNVERIFIED|SUBJECTIVE
  claim_grade text NOT NULL,              -- A|B|C|D (Sanad grade)
  claim_action text NOT NULL DEFAULT 'NONE', -- NONE|REQUEST_DATA|FLAG|RED_FLAG|HUMAN_GATE|PARTNER_OVERRIDE_REQUIRED
  sanad_id uuid NULL,                     -- FK to sanads (when created)
  source_calc_id uuid NULL,               -- FK to deterministic_calculations (required when claim_type='derived')
  primary_span_id uuid NULL REFERENCES document_spans(span_id),
  ic_bound boolean NOT NULL DEFAULT false,
  created_by uuid NULL REFERENCES actors(actor_id),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT chk_derived_has_source CHECK (
    claim_type != 'derived' OR source_calc_id IS NOT NULL
  )
);
CREATE INDEX idx_claims_deal ON claims(tenant_id, deal_id);
CREATE INDEX idx_claims_type ON claims(tenant_id, claim_type);

CREATE TABLE evidence_items (
  evidence_id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenants(tenant_id),
  deal_id uuid NOT NULL REFERENCES deals(deal_id),
  source_span_id uuid NULL REFERENCES document_spans(span_id),
  source_system text NULL,                -- Stripe|QuickBooks|Bank|Audit|Deck|ResearchProvider
  upstream_origin_id text NULL,           -- REQUIRED for independence tests
  retrieval_timestamp timestamptz NULL,
  verification_status text NOT NULL DEFAULT 'UNVERIFIED', -- UNVERIFIED|VERIFIED|CONTRADICTED
  source_grade text NOT NULL,             -- A|B|C|D (public)
  source_rank_subgrade text NULL,         -- A+|A|A-|B+|...|D (internal analytics)
  rationale jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_evidence_deal ON evidence_items(tenant_id, deal_id);

-- Join: which evidence supports which claim (many-to-many)
CREATE TABLE claim_evidence (
  tenant_id uuid NOT NULL REFERENCES tenants(tenant_id),
  claim_id uuid NOT NULL REFERENCES claims(claim_id),
  evidence_id uuid NOT NULL REFERENCES evidence_items(evidence_id),
  PRIMARY KEY (tenant_id, claim_id, evidence_id)
);
```

#### Sanad (claim-level)

```sql
CREATE TABLE sanads (
  sanad_id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenants(tenant_id),
  claim_id uuid NOT NULL REFERENCES claims(claim_id),
  primary_evidence_id uuid NULL REFERENCES evidence_items(evidence_id),
  extraction_confidence numeric NOT NULL,           -- 0..1
  dhabt_score numeric NULL,                         -- 0..1 historical precision
  corroboration_status text NOT NULL DEFAULT 'NONE',-- NONE|AHAD_1|AHAD_2|MUTAWATIR
  sanad_grade text NOT NULL,                        -- A|B|C|D (computed)
  grade_explanation jsonb NOT NULL DEFAULT '[]'::jsonb,
  transmission_chain jsonb NOT NULL DEFAULT '[]'::jsonb, -- list[TransmissionNode]; also stored in graph DB
  corroborating_evidence_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
  defects jsonb NOT NULL DEFAULT '[]'::jsonb,       -- list[Defect]; also normalized in defects table below
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, claim_id)
);
```

#### Defects (normalized)

```sql
CREATE TABLE defects (
  defect_id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenants(tenant_id),
  deal_id uuid NOT NULL REFERENCES deals(deal_id),
  defect_type text NOT NULL,            -- enum set (see §6)
  severity text NOT NULL,               -- FATAL|MAJOR|MINOR
  detected_by uuid NULL REFERENCES actors(actor_id),
  description text NOT NULL,
  evidence_refs jsonb NOT NULL DEFAULT '[]'::jsonb, -- SourceRef|ClaimRef
  cure_protocol text NOT NULL,          -- REQUEST_SOURCE|REQUIRE_REAUDIT|HUMAN_ARBITRATION|RECONSTRUCT_CHAIN|DISCARD_CLAIM
  status text NOT NULL DEFAULT 'OPEN',  -- OPEN|CURED|WAIVED
  waiver_reason text NULL,
  waived_by uuid NULL REFERENCES actors(actor_id),
  affected_claim_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
  timestamp timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_defects_deal ON defects(tenant_id, deal_id);
```

### 3.5 Deterministic Calculations (Calc-Sanad)

```sql
CREATE TABLE deterministic_calculations (
  calc_id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenants(tenant_id),
  deal_id uuid NOT NULL REFERENCES deals(deal_id),
  calc_type text NOT NULL,                      -- IRR|MOIC|GM|NRR|CAC_PAYBACK|VALUATION_MULTIPLE|...
  inputs jsonb NOT NULL,                        -- claim_ids + raw numeric inputs
  formula_hash text NOT NULL,
  code_version text NOT NULL,                   -- git sha / package version
  output jsonb NOT NULL,                        -- typed numeric outputs
  reproducibility_hash text NOT NULL,           -- hash(inputs+formula_hash+code_version+output)
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE calc_sanads (
  calc_sanad_id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenants(tenant_id),
  calc_id uuid NOT NULL REFERENCES deterministic_calculations(calc_id),
  input_claim_ids jsonb NOT NULL,
  input_min_sanad_grade text NOT NULL,          -- computed at runtime
  calc_grade text NOT NULL,                     -- A|B|C|D derived (min of inputs; D if any D in material)
  explanation jsonb NOT NULL DEFAULT '[]'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, calc_id)
);
```

### 3.6 Debate + Agent Outputs + Muḥāsabah

```sql
CREATE TABLE agents (
  agent_id text PRIMARY KEY, -- stable string id
  tenant_id uuid NOT NULL REFERENCES tenants(tenant_id),
  role text NOT NULL,         -- Advocate|SanadBreaker|ContradictionFinder|RiskOfficer|Arbiter|...
  model_provider text NOT NULL,
  model_name text NOT NULL,
  prompt_template text NOT NULL,
  tools jsonb NOT NULL DEFAULT '[]'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, agent_id)
);

CREATE TABLE debate_runs (
  debate_run_id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenants(tenant_id),
  deal_id uuid NOT NULL REFERENCES deals(deal_id),
  started_at timestamptz NOT NULL DEFAULT now(),
  ended_at timestamptz NULL,
  stop_reason text NULL,      -- CONSENSUS|STABLE_DISSENT|EVIDENCE_EXHAUSTED|MAX_ROUNDS|CRITICAL_DEFECT
  state_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE debate_messages (
  message_id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenants(tenant_id),
  debate_run_id uuid NOT NULL REFERENCES debate_runs(debate_run_id),
  round_number int NOT NULL,
  agent_id text NULL,         -- null for system
  role text NOT NULL,         -- advocate|sanad_breaker|arbiter|system
  content text NOT NULL,
  claim_refs jsonb NOT NULL DEFAULT '[]'::jsonb, -- list[claim_id|calc_id]
  timestamp timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE agent_outputs (
  output_id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenants(tenant_id),
  deal_id uuid NOT NULL REFERENCES deals(deal_id),
  debate_run_id uuid NULL REFERENCES debate_runs(debate_run_id),
  agent_id text NOT NULL,
  output_type text NOT NULL,  -- MARKET_ANALYSIS|FIN_ANALYSIS|RISK_MEMO|...
  content jsonb NOT NULL,     -- structured fields + narrative
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE muhasabah_records (
  muhasabah_id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenants(tenant_id),
  output_id uuid NOT NULL REFERENCES agent_outputs(output_id),
  agent_id text NOT NULL,
  supported_claim_ids jsonb NOT NULL,           -- MUST be non-empty unless SUBJECTIVE
  falsifiability_tests jsonb NOT NULL,          -- list[{test_description, required_evidence, pass_fail_rule}]
  uncertainties jsonb NOT NULL,                 -- list[{uncertainty, impact, mitigation}]
  confidence numeric NOT NULL,
  failure_modes jsonb NOT NULL DEFAULT '[]'::jsonb,
  timestamp timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, output_id)
);
```

---

## 4. Graph Schema (Sanad Graph + Knowledge Graph)

Use Neo4j/Arango/Neptune (any property graph). The relational layer stores summaries; the graph stores provenance and relationships.

### 4.1 Node Types

- `Deal`
- `Document`
- `Span`
- `EvidenceItem`
- `Claim`
- `TransmissionNode`
- `Agent`
- `Calculation`
- `Defect`
- `Entity` (Company/Founder/Product/Competitor/Investor)
- `Market` / `Sector`

### 4.2 Edge Types

- `(Deal)-[:HAS_DOCUMENT]->(Document)`
- `(Document)-[:HAS_SPAN]->(Span)`
- `(Claim)-[:SUPPORTED_BY]->(EvidenceItem)`
- `(Claim)-[:HAS_SANAD_STEP]->(TransmissionNode)`
- `(TransmissionNode)-[:INPUT]->(Span|EvidenceItem|Claim|Calculation)`
- `(TransmissionNode)-[:OUTPUT]->(Claim)`
- `(Claim)-[:HAS_DEFECT]->(Defect)`
- `(Calculation)-[:DERIVED_FROM]->(Claim)`
- `(Entity)-[:MENTIONED_IN]->(Span)`
- `(Company)-[:COMPETES_WITH]->(Company)`
- `(Deal)-[:IN_SECTOR]->(Sector)`

### 4.3 Independence Computation (Corroboration)

Independence MUST be computed using:
- distinct `upstream_origin_id`,
- no shared `TransmissionNode` segments,
- no shared preparer identity (when known),
- format/time evidence.

Store computed `corroboration_status` on the `Sanad` node.

---

## 5. JSON Schemas (Key Objects)

### 5.1 Defect (Normative)

```json
{
  "type": "object",
  "required": ["defect_id", "defect_type", "severity", "description", "cure_protocol", "status", "affected_claim_ids", "timestamp"],
  "properties": {
    "defect_id": {"type":"string","format":"uuid"},
    "defect_type": {"type":"string","enum":[
      "BROKEN_CHAIN","MISSING_LINK","UNKNOWN_SOURCE","CONCEALMENT","INCONSISTENCY",
      "ANOMALY_VS_STRONGER_SOURCES","CHRONO_IMPOSSIBLE","CHAIN_GRAFTING","CIRCULARITY",
      "STALENESS","UNIT_MISMATCH","TIME_WINDOW_MISMATCH","SCOPE_DRIFT","IMPLAUSIBILITY"
    ]},
    "severity": {"type":"string","enum":["FATAL","MAJOR","MINOR"]},
    "detected_by": {"type":["string","null"],"format":"uuid"},
    "description": {"type":"string"},
    "evidence_refs": {"type":"array","items":{"type":"object"}},
    "cure_protocol": {"type":"string","enum":["REQUEST_SOURCE","REQUIRE_REAUDIT","HUMAN_ARBITRATION","RECONSTRUCT_CHAIN","DISCARD_CLAIM"]},
    "status": {"type":"string","enum":["OPEN","CURED","WAIVED"]},
    "waiver_reason": {"type":["string","null"]},
    "waived_by": {"type":["string","null"],"format":"uuid"},
    "affected_claim_ids": {"type":"array","items":{"type":"string","format":"uuid"}},
    "timestamp": {"type":"string","format":"date-time"}
  }
}
```

### 5.2 MuḥāsabahRecord (Normative)

```json
{
  "type": "object",
  "required": ["agent_id","output_id","supported_claim_ids","confidence","timestamp"],
  "properties": {
    "agent_id": {"type":"string"},
    "output_id": {"type":"string","format":"uuid"},
    "supported_claim_ids": {"type":"array","items":{"type":"string","format":"uuid"}},
    "falsifiability_tests": {"type":"array","items":{
      "type":"object",
      "required":["test_description","required_evidence","pass_fail_rule"],
      "properties":{
        "test_description":{"type":"string"},
        "required_evidence":{"type":"string"},
        "pass_fail_rule":{"type":"string"}
      }
    }},
    "uncertainties": {"type":"array","items":{
      "type":"object",
      "required":["uncertainty","impact","mitigation"],
      "properties":{
        "uncertainty":{"type":"string"},
        "impact":{"type":"string","enum":["HIGH","MEDIUM","LOW"]},
        "mitigation":{"type":"string"}
      }
    }},
    "confidence": {"type":"number","minimum":0.0,"maximum":1.0},
    "failure_modes": {"type":"array","items":{"type":"string"}},
    "timestamp": {"type":"string","format":"date-time"}
  }
}
```

### 5.3 DebateState (Normative Minimum)

```json
{
  "type":"object",
  "required":["deal_id","round_number","messages","utility_scores","arbiter_decisions","consensus_reached"],
  "properties":{
    "deal_id":{"type":"string","format":"uuid"},
    "claim_registry_ref":{"type":"string"},
    "sanad_graph_ref":{"type":"string"},
    "open_questions":{"type":"array","items":{"type":"string"}},
    "round_number":{"type":"integer","minimum":1,"maximum":5},
    "messages":{"type":"array","items":{"type":"object"}},
    "utility_scores":{"type":"object","additionalProperties":{"type":"number"}},
    "arbiter_decisions":{"type":"array","items":{"type":"object"}},
    "consensus_reached":{"type":"boolean"},
    "stop_reason":{"type":["string","null"],"enum":[null,"CONSENSUS","STABLE_DISSENT","EVIDENCE_EXHAUSTED","MAX_ROUNDS","CRITICAL_DEFECT"]}
  }
}
```

---

## 5.4 ValueStruct Type Hierarchy (Phase POST-5.2)

ValueStruct provides typed value structures for claims and calculations, replacing untyped dict with validated types.

### Type Variants

| Type | Description | Key Fields |
|------|-------------|------------|
| **MonetaryValue** | Currency amounts | `amount` (Decimal), `currency` (ISO 4217) |
| **PercentageValue** | Percentages (0-1) | `value` (Decimal), `allow_overflow` (for growth rates) |
| **CountValue** | Integer counts | `value` (int), `unit` (optional label) |
| **DateValue** | ISO dates | `value` (date), `label` (semantic meaning) |
| **RangeValue** | Min/max ranges | `min_value`, `max_value`, `unit` |
| **TextValue** | Text with tags | `value` (string), `tags` (semantic labels) |

### Usage Rules

1. **Decimal Precision**: All monetary and percentage values use `Decimal` for deterministic arithmetic
2. **Currency Required**: MonetaryValue requires valid ISO 4217 currency code
3. **Percentage Bounds**: Default 0-1 range; use `allow_overflow=True` for growth rates >100%
4. **Range Validation**: At least one of min/max required; min cannot exceed max
5. **Type Discrimination**: All ValueStruct variants have `type` field for JSON discrimination

### JSON Schema Reference

See `schemas/value_struct.schema.json` for full JSON Schema definition.

---

## 5.5 Claim Lineage Type and Calc Loop Guardrail (Phase POST-5.2)

Claims have two distinct type fields:
- **`claim_class`** — Category/domain (FINANCIAL, TRACTION, MARKET_SIZE, etc.)
- **`claim_type`** — Lineage (PRIMARY or DERIVED) for calc loop guardrail

### claim_class Field (Category)

| Value | Description |
|-------|-------------|
| `FINANCIAL` | Financial metrics (revenue, margins, burn rate) |
| `TRACTION` | Customer/user traction metrics |
| `MARKET_SIZE` | TAM/SAM/SOM estimates |
| `COMPETITION` | Competitive landscape claims |
| `TEAM` | Team composition and background |
| `LEGAL_TERMS` | Legal/term sheet claims |
| `TECHNICAL` | Technical/product claims |
| `OTHER` | Other claim types |

### claim_type Field (Lineage)

| Value | Description | Can Trigger Calc? |
|-------|-------------|-------------------|
| `primary` | Extracted from source documents | ✅ Yes |
| `derived` | Created by calc output | ❌ No (loop guard) |

### Required Linkage for Derived Claims

When `claim_type = 'derived'`:
- **`source_calc_id`** (required) — UUID of the DeterministicCalculation that produced this claim
- Database constraint enforces: `claim_type != 'derived' OR source_calc_id IS NOT NULL`

### Calc Loop Guardrail Invariants

> **INVARIANT CLG-1**: Deterministic calcs may only consume PRIMARY claims (unless explicitly human-approved).
>
> **INVARIANT CLG-2**: DERIVED claims cannot automatically trigger further deterministic calcs.
>
> **INVARIANT CLG-3**: Violation is fail-closed (typed `CalcLoopGuardError` / defect created).

### Enforcement

| Component | Function | Behavior |
|-----------|----------|----------|
| `src/idis/models/claim.py` | `CalcLoopGuard.validate_calc_trigger()` | Raises `CalcLoopGuardError` if derived claims present |
| `src/idis/models/claim.py` | `CalcLoopGuard.filter_triggerable()` | Returns only PRIMARY claims |
| `src/idis/models/claim.py` | `Claim.can_trigger_calc()` | Returns `True` only for PRIMARY claims |

### Tests

- `tests/test_claim_type_enforcement.py` — claim_type field validation
- `tests/test_calc_loop_guardrail.py` — guardrail enforcement and filtering

---

## 5.6 Graph-DB + Postgres Dual-Write Consistency (Phase POST-5.2)

Saga pattern ensures Postgres and Graph DB writes are consistent.

### Saga Pattern

1. **SagaStep**: Individual write operation with compensation action
2. **DualWriteSagaExecutor**: Orchestrates multi-store writes
3. **Compensation**: On failure, all completed steps are rolled back in reverse order

### Fail-Closed Semantics

- Any step failure triggers compensation for all completed steps
- Compensation failures are logged but saga reports overall failure
- No partial writes left in inconsistent state

### Usage

```python
saga = create_claim_dual_write_saga(
    saga_id="claim-saga-001",
    postgres_insert=...,
    postgres_delete=...,
    graph_insert=...,
    graph_delete=...,
)
result = saga.execute({"claim": claim_data})
if not result.is_success:
    raise DualWriteConsistencyError(result)
```

---

## 5.7 No-Free-Facts Semantic Extensions (Phase POST-5.2)

Enhanced factual assertion detection using semantic subject-predicate patterns.

### Semantic Rule Library

In addition to regex patterns, the validator detects factual assertions via subject-predicate patterns:

| Pattern Category | Example Subject | Example Predicate |
|-----------------|-----------------|-------------------|
| Company Achievement | company, startup | achieved, reached, exceeded |
| Revenue Change | revenue, ARR | grew, increased, declined |
| Funding Event | company, we | raised, secured, closed |
| Market Size | TAM, SAM, market | estimated at, valued at |
| Team Growth | team, headcount | grew, expanded, numbers |
| Valuation Claim | valuation, pre-money | valued at, set at |

### Determinism

- All patterns are static regex rules (no ML models)
- Same input always produces same detection results
- Rules can be extended without breaking determinism

---

## 6. Enumerations (Canonical)

### 6.1 Public Grades

- `Grade`: A | B | C | D

### 6.2 Internal Subgrades (Analytics Only)

- `source_rank_subgrade`: A+ | A | A- | B+ | B | B- | C+ | C | C- | D  
Mapping:
- A+/A/A- → A  
- B+/B/B- → B  
- C+/C/C- → C  
- D → D  
**Important**: All gating rules and UI MUST use A/B/C/D only.

### 6.3 Defect Severity

- FATAL | MAJOR | MINOR

### 6.4 Corroboration Status

- NONE | AHAD_1 | AHAD_2 | MUTAWATIR

---

## 7. Computed Fields and Validations

### 7.1 Claim Sanad Grade (Computed)

Implement normative algorithm:

1. base_grade = min(source grades across transmission chain)
2. fatal defect → D
3. major defects downgrade stepwise
4. mutawatir with no majors upgrades B→A or C→B
5. cap upgrades at A

### 7.2 Muḥāsabah Validator (Deterministic)

Reject agent output if:
- factual assertions exist but supported_claim_ids is empty
- confidence > 0.80 and uncertainties empty
- confidence > 0.50 and falsifiability_tests empty (for material claims)

### 7.3 Extraction Gate (Deterministic)

If `extraction_confidence < 0.95` OR `dhabt_score < 0.90`:
- the claim MUST NOT be marked VERIFIED,
- the claim MUST NOT be used as input to deterministic engines without human verification.

---

## 8. Event Schemas (Pipeline + Audit)

Recommend CloudEvents envelope. Event types:

- `deal.ingestion.started|completed|failed`
- `document.parsed|failed`
- `claims.extracted|validated`
- `sanad.built|graded`
- `defect.detected|cured|waived`
- `calc.executed`
- `debate.started|round_completed|stopped`
- `muhasabah.validated|rejected`
- `deliverable.generated|approved|exported`

Minimal event fields:
- `event_id`, `tenant_id`, `deal_id`, `timestamp`, `event_type`, `payload_ref`, `hash`

---

## 9. Minimum Repo Structure (Recommended)

```text
schema/
  postgresql/
    001_init.sql
    010_claims.sql
    020_sanad.sql
    030_debate.sql
  jsonschema/
    claim.schema.json
    sanad.schema.json
    defect.schema.json
    muhasabah.schema.json
    debatestate.schema.json
  graph/
    nodes.md
    edges.md
  events/
    cloudevents.md
```

---

## 10. Deal Outcomes & Pattern Matching Schemas (SPEC / Phase 6+)

> **STATUS**: SPEC / PLANNED — Documentation only. No implementation claimed.
>
> This section documents future data models for deal outcome tracking and pattern matching.
> Implementation is planned for Phase 6.5+.

### 10.1 DealOutcome

Records the outcome of a historical deal for pattern matching analysis.

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://idis.example.com/schemas/deal_outcome.schema.json",
  "title": "DealOutcome",
  "description": "Outcome record for a historical deal (SPEC / Phase 6+)",
  "type": "object",
  "required": ["outcome_id", "tenant_id", "deal_id", "outcome_type", "confidence"],
  "properties": {
    "outcome_id": {"type": "string", "format": "uuid"},
    "tenant_id": {"type": "string", "format": "uuid"},
    "deal_id": {"type": "string", "format": "uuid"},
    "outcome_type": {
      "type": "string",
      "enum": ["INVESTED", "PASSED", "EXITED", "WRITTEN_OFF"],
      "description": "Final disposition of the deal"
    },
    "exit_date": {
      "type": ["string", "null"],
      "format": "date",
      "description": "Date of exit (if exited)"
    },
    "irr": {
      "type": ["number", "null"],
      "description": "Internal Rate of Return (if exited), as decimal"
    },
    "moic": {
      "type": ["number", "null"],
      "description": "Multiple on Invested Capital (if exited)"
    },
    "confidence": {
      "type": "number",
      "minimum": 0,
      "maximum": 1,
      "description": "Confidence in outcome data accuracy"
    },
    "notes": {
      "type": ["string", "null"],
      "description": "Optional notes on outcome"
    },
    "created_at": {"type": "string", "format": "date-time"},
    "updated_at": {"type": "string", "format": "date-time"}
  }
}
```

### 10.2 SimilarityFeature

Canonical typed features for deal comparison, referencing ValueStruct where applicable.

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://idis.example.com/schemas/similarity_feature.schema.json",
  "title": "SimilarityFeature",
  "description": "Feature vector for deal comparison (SPEC / Phase 6+)",
  "type": "object",
  "required": ["feature_id", "tenant_id", "deal_id", "sector", "stage", "geography", "feature_version"],
  "properties": {
    "feature_id": {"type": "string", "format": "uuid"},
    "tenant_id": {"type": "string", "format": "uuid"},
    "deal_id": {"type": "string", "format": "uuid"},
    "sector": {"type": "string", "description": "Primary sector (e.g., fintech, healthtech)"},
    "sub_sector": {"type": ["string", "null"]},
    "stage": {"type": "string", "enum": ["SEED", "SERIES_A", "SERIES_B", "GROWTH"]},
    "geography": {"type": "string", "description": "Primary market geography"},
    "revenue_range": {
      "type": ["object", "null"],
      "description": "RangeValue (see §5.4) — revenue range"
    },
    "arr_growth_rate": {
      "type": ["object", "null"],
      "description": "PercentageValue (see §5.4) — ARR growth rate"
    },
    "gross_margin": {
      "type": ["object", "null"],
      "description": "PercentageValue (see §5.4) — gross margin"
    },
    "burn_rate": {
      "type": ["object", "null"],
      "description": "MonetaryValue (see §5.4) — monthly burn rate"
    },
    "team_size": {
      "type": ["object", "null"],
      "description": "CountValue (see §5.4) — team headcount"
    },
    "tam_estimate": {
      "type": ["object", "null"],
      "description": "MonetaryValue (see §5.4) — TAM estimate"
    },
    "feature_version": {"type": "string", "description": "Version of feature extraction algorithm"},
    "created_at": {"type": "string", "format": "date-time"},
    "updated_at": {"type": "string", "format": "date-time"}
  }
}
```

### 10.3 PatternMatch

Similarity match result linking a target deal to historical comparables.

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://idis.example.com/schemas/pattern_match.schema.json",
  "title": "PatternMatch",
  "description": "Similarity match result (SPEC / Phase 6+)",
  "type": "object",
  "required": ["match_id", "tenant_id", "target_deal_id", "historical_deal_ids", "similarity_scores", "pattern_confidence", "feature_version", "computed_at"],
  "properties": {
    "match_id": {"type": "string", "format": "uuid"},
    "tenant_id": {"type": "string", "format": "uuid"},
    "target_deal_id": {
      "type": "string",
      "format": "uuid",
      "description": "Deal being analyzed"
    },
    "historical_deal_ids": {
      "type": "array",
      "items": {"type": "string", "format": "uuid"},
      "description": "Historical deals matched as comparables"
    },
    "similarity_scores": {
      "type": "object",
      "additionalProperties": {"type": "number", "minimum": 0, "maximum": 1},
      "description": "Map of deal_id → similarity score (0-1)"
    },
    "pattern_confidence": {
      "type": "number",
      "minimum": 0,
      "maximum": 1,
      "description": "Overall confidence in pattern match"
    },
    "feature_version": {
      "type": "string",
      "description": "Version of feature extraction used"
    },
    "computed_at": {
      "type": "string",
      "format": "date-time",
      "description": "When pattern match was computed"
    },
    "analyst_reviewed": {
      "type": "boolean",
      "default": false,
      "description": "Whether an analyst has reviewed this match"
    },
    "review_notes": {
      "type": ["string", "null"],
      "description": "Analyst notes on match quality"
    }
  }
}
```

### 10.4 Trust Invariants for Pattern Matching (PLANNED)

1. **Tenant Isolation**: Pattern matches only consider deals within same tenant
2. **Audit Trail**: All pattern matches logged with full feature inputs
3. **Human Review Gate**: Predictions marked `analyst_reviewed=false` until reviewed
4. **Confidence Thresholds**: Matches below 0.6 confidence flagged for review
5. **No Outcome Leakage**: Target deal outcome (if known) excluded from similarity

