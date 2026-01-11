# IDIS Enterprise Implementation Plan — v6.3

**Source**: IDIS VC Edition v6.3 (January 2026)  
**Purpose**: Provide a phased, enterprise-grade implementation plan that turns the v6.3 spec into a production system.

---

## 1. Guiding Constraints (Non-Negotiable)

1. Trust gates are code, not “best effort” prompts:
   - No-Free-Facts validator (deterministic)
   - Sanad grade + defect rules (deterministic)
   - Muḥāsabah validator (deterministic)
2. Deterministic calculations own all numbers.
3. Fail closed on material grade D claims.
4. Persist every artifact as audit evidence.

---

## 2. Phase Plan (Recommended)

### Phase 0 — Project Setup (Week 1)

Deliverables:
- Mono-repo initialized (backend + frontend + schema + infra)
- CI/CD (lint, tests, security scan)
- IaC baseline (Terraform)
- Secrets management (Vault/KMS)
- Logging + tracing baseline (OpenTelemetry)

Exit criteria:
- “Hello world” deployment to dev environment
- Tenant + Actor + Deal tables migrated
- RBAC enforced on one sample endpoint

---

### Phase 1 — Ingestion & Parsing (Weeks 2–4)

Implement:
- Document ingestion (upload + at least one connector)
- Document parsing into spans (PDF + XLSX minimum)
- Object storage + versioning (sha256)
- Search indexing (basic keyword; optional embeddings later)

Exit criteria:
- Can ingest a deal room into canonical Document + Span objects
- 95% parse success on internal sample set
- Full audit log for ingestion actions

---

### Phase 2 — Claim Registry + Truth Dashboard v1 (Weeks 5–8)

Implement:
- Claim extraction pipeline (LLM structured output + schema validation)
- Span citations for every claim
- Truth dashboard verdict states:
  - VERIFIED, CONTRADICTED, UNVERIFIED, SUBJECTIVE, UNKNOWN
- Matn validators (deterministic):
  - unit mismatch, time-window mismatch, basic plausibility

Exit criteria:
- Every extracted claim has:
  - claim_id,
  - span refs,
  - claim_type,
  - typed value_struct for numeric claims
- Contradiction detection works on numeric fields across sources

---

### Phase 3 — Sanad Trust Framework + Defects (Weeks 9–12)

Implement:
- EvidenceItem creation + grading rubric (A/B/C/D)
- Internal subgrade field for analytics only
- Transmission chain builder (TransmissionNode schema)
- Corroboration computation (Āḥād vs Mutawātir)
- Defect schema + severity rules + cure protocols
- Claim Sanad Grade algorithm (normative)

Exit criteria:
- 100% of material claims have Sanad objects
- Grade algorithm unit-tested with worked examples
- Defect creation and waiver workflow operational

#### Phase 3.3 — Sanad Methodology v2 Enhancements ✅ COMPLETE

**Implemented (2026-01-09):**

1. **Source Tiers (6-level hierarchy)**
   - `src/idis/services/sanad/source_tiers.py`
   - Tiers: ATHBAT_AL_NAS (1.00) → MAQBUL (0.40)
   - PRIMARY (1-4) vs SUPPORT_ONLY (5-6) admissibility
   - Deterministic assignment based on source_type

2. **Dabt Multi-Dimensional Scoring**
   - `src/idis/services/sanad/dabt.py`
   - 4 dimensions: documentation, transmission, temporal, cognitive
   - Fail-closed: missing dimension → 0.0
   - Quality bands: EXCELLENT/GOOD/FAIR/POOR

3. **Tawatur Independence + Collusion Detection**
   - `src/idis/services/sanad/tawatur.py`
   - Independence key computation (source_system, upstream_origin_id, artifact_id, time_bucket)
   - Collusion risk scoring (system concentration + time clustering + chain overlap)
   - MUTAWATIR requires ≥3 independent AND collusion_risk ≤ 0.30

4. **Shudhudh Reconciliation-First Anomaly Detection**
   - `src/idis/services/sanad/shudhudh.py`
   - Reconciliation heuristics: unit conversion, time window, rounding
   - SHUDHUDH_ANOMALY only if reconciliation fails AND lower-tier contradicts consensus

5. **I'lal Hidden Defect Detection**
   - `src/idis/services/sanad/ilal.py`
   - ILAL_VERSION_DRIFT (MAJOR): sha drift + metric change
   - ILAL_CHAIN_BREAK (FATAL): missing node / broken parent ref
   - ILAL_CHAIN_GRAFTING (FATAL): inconsistent provenance
   - ILAL_CHRONOLOGY_IMPOSSIBLE (FATAL): timestamp causality violation

6. **COI Handling + Cure Protocol**
   - `src/idis/services/sanad/coi.py`
   - HIGH undisclosed → grade cap C unless cured by independent high-tier
   - HIGH disclosed → requires MUTAWATIR or multiple high-tier
   - Deterministic cure evaluation

7. **Integrated Grader v2**
   - `src/idis/services/sanad/grader.py`
   - `grade_sanad_v2()` combines all enhancements
   - FATAL defect → Grade D (hard gate)
   - MAJOR defects → downgrade per defect
   - MUTAWATIR → upgrade (if no MAJOR defects)

**Tests:**
- `tests/test_sanad_methodology_v2_unit.py` — unit tests for all components
- `tests/test_sanad_methodology_v2_gdbs.py` — GDBS-FULL adversarial deal tests

**Documentation:**
- `docs/IDIS_Sanad_Methodology_v2.md` — full methodology specification

---

### Phase 4 — Deterministic Engines + Calc-Sanad (Weeks 13–16)

Implement:
- Calculation runner framework
- Calc-Sanad:
  - formula hash, code version, reproducibility hash
  - calc grade derived from input claim grades
- Extraction confidence gate:
  - block calcs if extraction_confidence < 0.95 or dhabt_score < 0.90

Exit criteria:
- Calc outputs reproducible (same inputs → same hash)
- No LLM-generated arithmetic in deliverables
- Calcs are traceable to claim_ids and source evidence
- Extraction gate enforced at calc engine boundary

#### Phase 4.1 — Deterministic Calc Engine Framework ✅ COMPLETE

**Implemented (2026-01-10):**

1. **Canonical JSON Serialization Rules for Hashes**
   - All JSON keys sorted alphabetically (recursive)
   - Lists maintain order unless explicitly sorted (input_claim_ids sorted before hashing)
   - Decimal values serialized as strings with explicit precision (e.g., "123.4500")
   - UUIDs serialized as lowercase hyphenated strings
   - No whitespace in serialized JSON (`separators=(',', ':')`)
   - UTF-8 encoding for hash input

2. **Decimal Rounding Profile**
   - All calculations use `decimal.Decimal` exclusively (no float arithmetic)
   - Default precision: 28 significant digits (Python Decimal default)
   - Rounding mode: `ROUND_HALF_UP` for all quantize operations
   - Output precision: configurable per calc_type, default 4 decimal places
   - Explicit quantize before storage: `value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)`

3. **Formula Registry Versioning**
   - Each formula has: `calc_type`, `version` (semver), `expression_id`
   - `formula_hash = sha256(canonical_json({calc_type, formula_version, expression_id}))`
   - Formula registry is immutable once deployed; new versions create new entries
   - Code version tracked via package `__version__` or git SHA

4. **Reproducibility Hash Computation**
   - `reproducibility_hash = sha256(canonical_json({tenant_id, deal_id, calc_type, formula_hash, code_version, inputs, output}))`
   - `inputs` includes sorted `input_claim_ids` and all numeric inputs as Decimal strings
   - `output` includes all computed values as Decimal strings
   - Hash is recomputed on verification; mismatch raises `CalcIntegrityError`

5. **Calc-Sanad Grade Derivation**
   - `input_min_sanad_grade = min(grade for all input claims)`
   - Grade ordering: A > B > C > D
   - `calc_grade = input_min_sanad_grade` (worst grade propagates)
   - If any material input has grade D → `calc_grade = D` (hard gate)
   - Phase 4.1 default: all inputs treated as material

6. **Fail-Closed Validation**
   - Missing required inputs → `CalcMissingInputError` (typed exception)
   - Unsupported unit/currency/time_window → `CalcUnsupportedValueError`
   - Integrity mismatch on verify → `CalcIntegrityError`
   - No defaults for missing values; explicit rejection required

**Modules:**
- `src/idis/calc/__init__.py` — package exports
- `src/idis/calc/engine.py` — CalcEngine with run() and verify_reproducibility()
- `src/idis/calc/formulas/__init__.py` — formula package
- `src/idis/calc/formulas/registry.py` — FormulaSpec and FormulaRegistry
- `src/idis/calc/formulas/core.py` — minimal formulas for tests (runway, gross_margin)
- `src/idis/models/calc_sanad.py` — CalcSanad model with grade enum
- `src/idis/models/deterministic_calculation.py` — DeterministicCalculation model

**Persistence:**
- `src/idis/persistence/migrations/versions/0005_deterministic_calculations_and_calc_sanads.py`
- Tables: `deterministic_calculations`, `calc_sanads`
- RLS policies with NULLIF hardening for tenant isolation
- Indexes: `(tenant_id, deal_id)`, `(tenant_id, calc_type)`

**Tests:**
- `tests/test_calc_reproducibility.py` — hash stability tests
- `tests/test_calc_sanad.py` — grade derivation and tamper detection
- `tests/test_postgres_rls_and_audit_immutability.py` — RLS tests for new tables

#### Phase 4.2 — Extraction Confidence Gate ✅ COMPLETE

**Implemented (2026-01-10):**

1. **Extraction Gate Validator**
   - `src/idis/validators/extraction_gate.py`
   - Thresholds as Decimal constants: `CONFIDENCE_THRESHOLD = 0.95`, `DHABT_THRESHOLD = 0.90`
   - Fail-closed semantics: missing/invalid values block unless human-verified

2. **Gate Decision Model**
   - `ExtractionGateInput` dataclass with claim_id, extraction_confidence, dhabt_score, verification flags
   - `ExtractionGateDecision` with allowed/blocked status, reason, and bypass flag
   - `ExtractionGateBlockReason` enum: LOW_CONFIDENCE, LOW_DHABT, MISSING_*, INVALID_*

3. **Human Verification Bypass**
   - `is_human_verified` flag bypasses ALL gate checks
   - `VerificationMethod` enum: NONE, HUMAN_VERIFIED, SYSTEM_VERIFIED, DUAL_VERIFIED
   - HUMAN_VERIFIED and DUAL_VERIFIED bypass the gate

4. **Calc Engine Integration**
   - `InputGradeInfo` extended with extraction_confidence, dhabt_score, is_human_verified
   - `CalcEngine._enforce_extraction_gate_on_inputs()` called before computation
   - `ExtractionGateBlockedError` raised if ANY input fails gate
   - `enforce_extraction_gate` flag (default True) for migration scenarios

5. **Validator Interfaces**
   - `evaluate_extraction_gate(input)` — returns decision
   - `evaluate_extraction_gate_batch(inputs)` — returns (allowed, blocked) lists
   - `validate_extraction_gate(input)` — returns ValidationResult
   - `ExtractionGateValidator` class for consistency with other validators

**Tests:**
- `tests/test_extraction_gate.py` — comprehensive gate tests including:
  - `test_low_confidence_blocked` (required by FC-001)
  - `test_low_dhabt_blocked`
  - `test_missing_values_fail_closed`
  - `test_human_verified_bypasses_gate`
  - CalcEngine integration tests

---

### Phase 5 — Multi-Agent Debate + Muḥāsabah Gate (Weeks 17–22)

Implement:
- LangGraph orchestration per Appendix C-1
- Agent roles:
  - advocate, sanad breaker, contradiction finder, risk officer, arbiter
- Stop conditions (priority) and max rounds
- Utility scoring (Brier + penalties) and materiality gate
- MuḥāsabahRecord contract + deterministic validator
- Dissent preservation in stable dissent

Exit criteria:
- Debate runs end-to-end on sample deals
- Outputs blocked if Muḥāsabah missing or No-Free-Facts violated
- Stable dissent produces deliverables with dissent section

#### Phase 5.1 — LangGraph Orchestration Core ✅ COMPLETE

**Implemented (2026-01-10):**

1. **Debate State Models**
   - `src/idis/models/debate.py`
   - `DebateState` — canonical state for LangGraph orchestration
   - `DebateMessage`, `AgentOutput`, `MuhasabahRecord` — structured outputs
   - `ArbiterDecision`, `PositionSnapshot` — round artifacts
   - `StopReason` enum with normative priority order
   - `DebateConfig` with max_rounds=5, consensus_threshold=0.10

2. **Stop Conditions Module**
   - `src/idis/debate/stop_conditions.py`
   - Priority order (normative): CRITICAL_DEFECT > MAX_ROUNDS > CONSENSUS > STABLE_DISSENT > EVIDENCE_EXHAUSTED
   - `StopConditionChecker` class with deterministic evaluation
   - `check_stop_condition()` convenience function
   - `StopConditionError` for fail-closed behavior on invalid state
   - Max rounds = 5 (hard limit per v6.3)

3. **Role Runner Interface**
   - `src/idis/debate/roles/base.py`
   - `RoleRunnerProtocol` — typed interface for role implementations
   - `RoleRunner` abstract base class
   - `RoleResult` — structured state updates from role execution
   - Supports injected role runners for deterministic testing

4. **Role Implementations**
   - `src/idis/debate/roles/advocate.py` — Advocate (thesis + rebuttal)
   - `src/idis/debate/roles/sanad_breaker.py` — Sanad Breaker (chain challenges)
   - `src/idis/debate/roles/contradiction_finder.py` — Contradiction Finder (Matn)
   - `src/idis/debate/roles/risk_officer.py` — Risk Officer (downside/fraud/regulatory)
   - `src/idis/debate/roles/arbiter.py` — Arbiter (utility + dissent preservation)

5. **Debate Orchestrator**
   - `src/idis/debate/orchestrator.py`
   - `DebateOrchestrator` class with LangGraph state machine
   - Node graph order matches Appendix C-1 exactly:
     START → advocate_opening → sanad_breaker_challenge → observer_critiques_parallel
     → advocate_rebuttal → (conditional evidence_call_retrieval) → arbiter_close
     → stop_condition_check → muhasabah_validate_all → finalize_outputs → END
   - `muhasabah_validate_all` is structural no-op in Phase 5.1 (hard gate in Phase 5.2)
   - Deterministic execution: no randomness, stable role order
   - `build_debate_graph()` convenience function

**Tests:**
- `tests/test_debate_node_graph.py` — node order matches v6.3
- `tests/test_debate_stop_conditions.py` — priority order and max rounds = 5

**Design Constraints (Per v6.3):**
- Node order matches Appendix C-1 normative graph
- Stop condition priority: CRITICAL_DEFECT > MAX_ROUNDS > CONSENSUS > STABLE_DISSENT > EVIDENCE_EXHAUSTED
- Max rounds = 5 (hard)
- Deterministic: no randomness, stable execution order
- Role runners injected (no LLM calls in Phase 5.1)

#### Phase 5.2 — Muḥāsabah Gate Integration ✅ COMPLETE

**Implemented (2026-01-11):**

1. **Output Boundary Enforcement**
   - `src/idis/debate/muhasabah_gate.py`
   - `enforce_muhasabah_gate()` function called after each role produces output
   - Runs BOTH MuhasabahValidator AND NoFreeFactsValidator at output boundary
   - Gate blocks outputs BEFORE they are accepted into debate state

2. **Fail-Closed Semantics**
   - Missing muhasabah record → REJECT (GateRejectionReason.MISSING_MUHASABAH)
   - Any validator error → REJECT with structured error (no uncaught exceptions)
   - Gate failure halts run with StopReason.CRITICAL_DEFECT
   - No outputs accepted into state without passing gate

3. **Deterministic Handling**
   - No uuid4/datetime.utcnow in gate code paths
   - All validation is deterministic and reproducible
   - Same inputs → same gate decision

4. **No-Free-Facts Linkage**
   - NoFreeFactsValidator called at output boundary
   - Outputs with factual content but empty claim_ids → REJECT
   - Per-section validation: refs elsewhere do NOT satisfy a section

**Modules:**
- `src/idis/models/muhasabah_record.py` — Canonical MuhasabahRecordCanonical + nested types
- `src/idis/debate/muhasabah_gate.py` — MuhasabahGate, GateDecision, MuhasabahGateError
- `src/idis/debate/orchestrator.py` — Updated with gate enforcement at output boundary

**Tests:**
- `tests/test_muhasabah_gate.py` — gate blocking/allowing tests
- `tests/test_debate_muhasabah_integration.py` — orchestrator integration tests

**Acceptance (Phase 5.2):**
- [x] Gate enforced at output boundary (after each role produces output)
- [x] Gate blocks missing/invalid MuhasabahRecord
- [x] Gate blocks No-Free-Facts violations
- [x] Gate failure halts run with CRITICAL_DEFECT
- [x] No randomness in gate code paths
- [x] Stable dissent preserved (gate does not erase properly-referenced dissent)

---

### Phase POST-5.2 — Architecture Hardening ✅ COMPLETE

**Implemented (2026-01-11):**

Architecture hardening addressing schema gaps and consistency requirements.

#### 1. Calc Loop Guardrail (Primary vs Derived Claims)

Prevents infinite calculation loops by distinguishing claim lineage.

| Aspect | Implementation |
|--------|----------------|
| **Enforcing Component** | `src/idis/models/claim.py` — `CalcLoopGuard`, `CalcLoopGuardError` |
| **Claim Fields** | `claim_class` (category), `claim_type` (lineage: primary/derived), `source_calc_id` |
| **Invariants** | PRIMARY claims trigger calcs; DERIVED claims cannot auto-trigger |
| **Tests** | `tests/test_claim_type_enforcement.py`, `tests/test_calc_loop_guardrail.py` |

#### 2. No-Free-Facts Semantic Extensions

Enhanced factual assertion detection using deterministic subject-predicate patterns.

| Aspect | Implementation |
|--------|----------------|
| **Enforcing Component** | `src/idis/validators/no_free_facts.py` — `SEMANTIC_RULES`, `SemanticMatch` |
| **Pattern Categories** | Company achievement, revenue change, funding event, market size, team growth, valuation claim |
| **Determinism** | Static regex rules only (no ML models); same input → same output |
| **Tests** | `tests/test_no_free_facts_semantic_cases.py` |

#### 3. Cross-DB Dual-Write Saga Consistency

Saga pattern ensuring Postgres + Graph DB writes are atomic.

| Aspect | Implementation |
|--------|----------------|
| **Enforcing Component** | `src/idis/persistence/saga.py` — `DualWriteSagaExecutor`, `SagaStep` |
| **Pattern** | Execute steps in order; on failure, compensate completed steps in reverse |
| **Fail-Closed** | Any step failure triggers full compensation; no partial writes |
| **Helpers** | `create_claim_dual_write_saga()`, `create_sanad_dual_write_saga()` |
| **Tests** | `tests/test_graph_postgres_consistency_saga.py` |

#### 4. ValueStruct Type Hierarchy

Typed value structures replacing untyped dict for claims and calculations.

| Aspect | Implementation |
|--------|----------------|
| **Enforcing Component** | `src/idis/models/value_structs.py` |
| **Types** | `MonetaryValue`, `PercentageValue`, `CountValue`, `DateValue`, `RangeValue`, `TextValue` |
| **Schema** | `schemas/value_struct.schema.json` |
| **Tests** | `tests/test_value_structs.py`, `tests/test_calc_value_types_integration.py` |

**Documentation:**
- Data Model §5.4 (ValueStruct), §5.5 (Claim Lineage), §5.6 (Dual-Write), §5.7 (NFF Semantic)
- Data Model §10 (Pattern Matching schemas — SPEC only)

---

### Phase 6 — Deliverables Generator + Frontend v1 (Weeks 23–28)

Implement:
- Screening Snapshot generator
- IC memo pack generator
- Exports (PDF/Doc)
- UI:
  - truth dashboard
  - claim detail + Sanad chain view
  - debate transcript + Muḥāsabah view
  - deliverables viewer

Exit criteria:
- Partner can review a deal with auditable evidence links
- Every fact in memo has claim_id/calc_id reference
- Exports include an audit appendix (optional) for compliance

#### Phase 6.1 — Deliverables Generator ✅ COMPLETE

**Implemented (2026-01-11):**

1. **Deliverables Object Model**
   - `src/idis/models/deliverables.py`
   - `DeliverableFact` — fact with `claim_refs`, `calc_refs`, `is_factual`, `is_subjective`
   - `DeliverableSection` — section containing multiple facts
   - `ScreeningSnapshot` — partner-ready one-pager
   - `ICMemo` — full IC memo with all sections + dissent
   - `AuditAppendix` — evidence appendix with sorted refs
   - `DissentSection` — structured dissent with refs

2. **Screening Snapshot Builder**
   - `src/idis/deliverables/screening.py`
   - `ScreeningSnapshotBuilder` class with fluent API
   - Sections: summary, metrics, red flags, missing info
   - All facts track claim/calc refs
   - Audit appendix auto-generated from all refs

3. **IC Memo Builder**
   - `src/idis/deliverables/memo.py`
   - `ICMemoBuilder` class with fluent API
   - 8 required sections + optional scenario analysis
   - Dissent section with mandatory refs (fail-closed)
   - Sanad grade distribution summary

4. **PDF/DOCX Exporter**
   - `src/idis/deliverables/export.py`
   - `DeliverableExporter` with validation before export
   - Minimal PDF generator (valid %PDF header)
   - Minimal DOCX generator (valid PK/zip header)
   - Audit appendix rendered in all exports

5. **No-Free-Facts at Export (Hard Gate)**
   - `src/idis/validators/deliverable.py`
   - `validate_deliverable_no_free_facts()` — enforced at export
   - Per-section validation (refs elsewhere don't satisfy)
   - Stable error code: `NO_FREE_FACTS_UNREFERENCED_FACT`
   - Fail-closed: missing refs block export

**Design Details:**
- **Determinism**: no randomness (no uuid4/uuid1/random/datetime.now/utcnow)
- **Stable ordering**: claim_refs sorted lexicographically; audit appendix sorted by (ref_type, ref_id)
- **Fail-closed**: factual outputs without refs rejected at validation
- **Dissent handling**: if stable dissent exists, must have non-empty refs

**Tests:**
- `tests/test_screening_snapshot.py` — builder + validation
- `tests/test_ic_memo.py` — sections evidence-linked + dissent handling
- `tests/test_deliverable_no_free_facts.py` — validator enforcement
- `tests/test_export_formats.py` — PDF/DOCX headers + audit appendix

**Acceptance (Phase 6.1):**
- [x] All facts produced by builder include claim_id/calc_id references
- [x] Missing refs fail-closed via validator
- [x] Stable dissent produces dissent section with refs
- [x] PDF export returns bytes beginning with %PDF
- [x] DOCX export returns bytes beginning with PK
- [x] Exports include audit appendix section

#### Phase 6.2 — Frontend Backend Contracts ✅ COMPLETE

**Implemented (2026-01-11):**

Backend API contracts for frontend Truth Dashboard and Claim/Sanad views.

1. **Truth Dashboard API**
   - `GET /v1/deals/{dealId}/truth-dashboard`
   - Returns aggregated claim statistics (by grade, by verdict, fatal defects count)
   - Paginated claims list with stable ordering (sorted by claim_id)
   - OpenAPI operationId: `getDealTruthDashboard`

2. **Claim Detail API**
   - `GET /v1/claims/{claimId}`
   - Returns full claim body with corroboration, defect_ids, materiality
   - Tenant isolation enforced (404 for cross-tenant reads)
   - OpenAPI operationId: `getClaim`

3. **Sanad Chain API**
   - `GET /v1/claims/{claimId}/sanad`
   - Returns transmission chain with deterministic node ordering (by node_id)
   - Computed grade, corroboration_level, independent_chain_count
   - OpenAPI operationId: `getClaimSanad`

**Modules:**
- `src/idis/api/routes/claims.py` — route handlers for all three endpoints
- `openapi/IDIS_OpenAPI_v6_3.yaml` — TruthDashboard, TruthDashboardSummary schemas

**RBAC Policy:**
- `getDealTruthDashboard` — ALL_ROLES, read-only, deal-scoped
- `getClaim` — ALL_ROLES, read-only
- `getClaimSanad` — ALL_ROLES, read-only

**Tests:**
- `tests/test_api_truth_dashboard.py` — schema validation, counts, determinism, tenant isolation
- `tests/test_api_claim_detail_and_sanad.py` — claim detail, sanad chain ordering, RBAC

**Acceptance (Phase 6.2):**
- [x] Truth Dashboard returns correct schema with summary and claims
- [x] Summary counts match seeded data (by_grade, by_verdict, fatal_defects)
- [x] Stable ordering: two calls produce identical JSON
- [x] Cross-tenant access returns 404 (no info leak)
- [x] Sanad chain transmission_chain sorted by node_id
- [x] RBAC enforced for all endpoints
- [x] OpenAPI validation passes for all responses

---

### Phase 6.5 — Pattern Matching & Deal Outcome Analysis (Weeks 28–30) — SPEC ONLY

> **Note:** This phase is documented for future implementation. No code is implemented yet.

#### Overview

Pattern matching enables IDIS to identify similar historical deals and predict outcomes based on comparable company characteristics. This phase introduces:

- **DealOutcome**: Structured outcome data for historical deals
- **SimilarityFeature**: Feature vectors for deal comparison
- **PatternMatch**: Similarity scoring and match results

#### Data Models (Specification)

**DealOutcome** — Outcome record for a historical deal:
```python
class DealOutcome(BaseModel):
    outcome_id: str           # UUID
    tenant_id: str            # Tenant isolation
    deal_id: str              # Reference to Deal
    outcome_type: OutcomeType # INVESTED | PASSED | EXITED | WRITTEN_OFF
    investment_date: date | None
    exit_date: date | None
    irr: Decimal | None       # Internal Rate of Return (if exited)
    moic: Decimal | None      # Multiple on Invested Capital (if exited)
    holding_period_months: int | None
    exit_type: ExitType | None  # IPO | M&A | SECONDARY | WRITE_OFF
    notes: str | None
    created_at: datetime
    updated_at: datetime
```

**SimilarityFeature** — Feature vector for deal comparison:
```python
class SimilarityFeature(BaseModel):
    feature_id: str           # UUID
    tenant_id: str
    deal_id: str
    # Company characteristics
    sector: str               # Primary sector (e.g., "fintech", "healthtech")
    sub_sector: str | None    # Sub-sector refinement
    stage: str                # Seed | Series A | Series B | Growth
    geography: str            # Primary market geography
    # Financial metrics (normalized)
    revenue_range: RangeValue | None
    arr_growth_rate: PercentageValue | None
    gross_margin: PercentageValue | None
    burn_rate: MonetaryValue | None
    runway_months: int | None
    # Team metrics
    team_size: CountValue | None
    founder_experience_score: Decimal | None  # 0-1 composite
    # Market metrics
    tam_estimate: MonetaryValue | None
    market_growth_rate: PercentageValue | None
    # Computed embedding (optional, for ML similarity)
    embedding_vector: list[float] | None
    embedding_model_version: str | None
    created_at: datetime
    updated_at: datetime
```

**PatternMatch** — Similarity match result:
```python
class PatternMatch(BaseModel):
    match_id: str             # UUID
    tenant_id: str
    target_deal_id: str       # Deal being analyzed
    matched_deal_ids: list[str]  # Historical deals matched
    similarity_scores: dict[str, Decimal]  # deal_id -> score (0-1)
    pattern_confidence: Decimal  # Overall confidence (0-1)
    match_method: MatchMethod  # FEATURE_VECTOR | EMBEDDING | HYBRID
    feature_weights: dict[str, Decimal]  # Feature importance weights
    outcome_distribution: dict[str, int]  # OutcomeType -> count
    predicted_outcome: OutcomeType | None
    prediction_confidence: Decimal | None
    created_at: datetime
    analyst_reviewed: bool
    reviewed_by: str | None
    review_notes: str | None
```

#### JSON Schema (Documentation Example)

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://idis.example.com/schemas/pattern_match.schema.json",
  "title": "PatternMatch",
  "type": "object",
  "required": ["match_id", "tenant_id", "target_deal_id", "matched_deal_ids", "similarity_scores", "pattern_confidence", "match_method"],
  "properties": {
    "match_id": {"type": "string", "format": "uuid"},
    "tenant_id": {"type": "string", "format": "uuid"},
    "target_deal_id": {"type": "string", "format": "uuid"},
    "matched_deal_ids": {"type": "array", "items": {"type": "string", "format": "uuid"}},
    "similarity_scores": {"type": "object", "additionalProperties": {"type": "number", "minimum": 0, "maximum": 1}},
    "pattern_confidence": {"type": "number", "minimum": 0, "maximum": 1},
    "match_method": {"type": "string", "enum": ["FEATURE_VECTOR", "EMBEDDING", "HYBRID"]},
    "predicted_outcome": {"type": ["string", "null"], "enum": ["INVESTED", "PASSED", "EXITED", "WRITTEN_OFF", null]}
  }
}
```

#### Implementation Roadmap

| Week | Deliverable |
|------|-------------|
| 28 | DealOutcome model + schema + migrations |
| 28 | SimilarityFeature model + feature extraction pipeline |
| 29 | PatternMatch model + basic feature-vector matching |
| 29 | UI: Similar deals panel in deal view |
| 30 | Embedding-based similarity (optional, requires vector DB) |
| 30 | Pattern match confidence calibration + tests |

#### Expected Tests

| Test File | Description |
|-----------|-------------|
| `tests/test_deal_outcome.py` | DealOutcome CRUD and validation |
| `tests/test_similarity_feature.py` | Feature extraction and normalization |
| `tests/test_pattern_match.py` | Similarity scoring and ranking |
| `tests/test_pattern_match_integration.py` | End-to-end matching pipeline |

#### Trust Invariants for Pattern Matching

1. **Tenant Isolation**: Pattern matches only consider deals within same tenant
2. **Audit Trail**: All pattern matches logged with full feature inputs
3. **Human Review Gate**: Predictions marked `analyst_reviewed=False` until reviewed
4. **Confidence Thresholds**: Matches below 0.6 confidence flagged for review
5. **No Outcome Leakage**: Target deal outcome (if known) excluded from similarity

---

### Phase 7 — Enterprise Hardening (Weeks 29–40)

Implement:
- SSO integration (Okta/Azure)
- BYOK option
- Data residency controls
- SOC2 readiness features:
  - audit trails, access reviews, change management logs
- Governance dashboards:
  - Sanad coverage, defect rates, muhasabah pass rate, drift monitoring
- Integrations:
  - CRM sync
  - enrichment providers (BYOL framework)

Exit criteria:
- Security review passed
- Pilot fund onboarded with real deals
- Operational runbooks complete

---

## 3. Workstreams (Parallelization)

1. Platform foundation & infra
2. Data ingestion & parsing
3. Sanad & trust layer
4. Deterministic engines
5. Debate orchestration (LangGraph)
6. Frontend & UX
7. Security & compliance
8. Product & evaluation/metrics

---

## 4. Evaluation and Success Metrics

Minimum success metrics (pilot):
- 95%+ citation coverage for factual statements
- 100% deterministic provenance for numbers
- Reduction in analyst hours per screened deal by 50%+
- Red flag precision/recall targets defined and tracked
- Muḥāsabah rejection rate decreases over time (prompt + validator tuning) without lowering quality

---

## 5. Risks and Mitigations

- Risk: extraction errors contaminate calcs  
  - Mitigation: confidence gate + human verification workflow

- Risk: agents collude or converge prematurely  
  - Mitigation: randomized role assignment + arbiter validation + dissent preservation

- Risk: evidence gaps stall pipeline  
  - Mitigation: “missing info request” output and partial deliverables with explicit unknowns

- Risk: compliance concerns (client data)  
  - Mitigation: no-training policy, BYOK, data residency, immutable audit logs

