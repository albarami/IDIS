# IDIS Decisions Locked — Do Not Re-Litigate

**Freeze Date:** 2026-02-05  
**Purpose:** Architectural decisions that are final and must not be changed during the rebuild

---

## Overview

These decisions have been validated through implementation, testing, and review. Changing them would require significant rework and risk breaking trust invariants. The rebuild should focus on **connecting existing components**, not redesigning them.

---

## 1. Trust Invariants (NON-NEGOTIABLE)

### 1.1 No-Free-Facts (NFF)
**Decision:** Every factual statement in IC-bound output MUST trace to `claim_id` or `calc_id`.

- Enforced at 3 checkpoints: LLM tool wrapper, Muḥāsabah gate, deliverables generator
- Deterministic code validation, not prompt instruction
- **Rationale:** Core differentiator; audit requirement; cannot be weakened

### 1.2 Deterministic Numerics
**Decision:** LLMs NEVER compute numbers. All calculations go through `calc/engine.py`.

- Every calc output carries CalcSanad: `formula_hash`, `code_version`, `reproducibility_hash`, `input_claim_ids`
- Same inputs + same code version = byte-identical output
- **Rationale:** Zero numerical hallucination guarantee

### 1.3 Sanad Chain Integrity
**Decision:** Every claim traces: source document → span → evidence item → claim.

- Sanad grades (A/B/C/D) assigned by deterministic rules in `services/sanad/`
- Orphaned claims, missing evidence, or unlinked spans = data integrity violation
- **Rationale:** Evidence provenance is the foundation of trust

### 1.4 Muḥāsabah Gate
**Decision:** Every debate agent output must include: falsifiability conditions, uncertainty acknowledgments, counter-hypotheses.

- Validated deterministically by `validators/muhasabah.py`
- Missing fields = hard reject (fail-closed)
- **Rationale:** Prevents overconfident recommendations

### 1.5 Audit Completeness
**Decision:** Every mutation emits an audit event. Audit write failure = request failure.

- Audit is append-only, immutable
- Schema: `event_id`, `tenant_id`, `actor`, `resource`, `action`, `request_id`
- **Rationale:** SOC2/regulatory compliance; forensic capability

### 1.6 Tenant Isolation
**Decision:** All data scoped by `tenant_id`. Postgres RLS enforced at DB level.

- Cross-tenant access must be structurally impossible, not just filtered
- Failed tenant check must not reveal resource existence (no existence oracle)
- **Rationale:** Enterprise multi-tenancy requirement

---

## 2. Fail-Closed Everywhere (NON-NEGOTIABLE)

| Context | Behavior |
|---------|----------|
| Default deny on all access | RBAC/ABAC rejects unless explicitly permitted |
| Invalid parse | Structured failure + audit event (never skip silently) |
| Invalid LLM JSON | Reject + fallback + audit (never store garbage) |
| Missing evidence | Reject claim creation (NFF enforcement) |
| Validator failure | Block pipeline step (never "best effort success") |
| `return True` as default | **BANNED** in any validator or gate |
| Bare `pass` in `except` | **BANNED** in `src/` |

---

## 3. Data Model Decisions (LOCKED)

### 3.1 Primary Key Strategy
**Decision:** UUIDs for all primary keys.
- **Rationale:** Multi-tenant, distributed-friendly, no sequence conflicts

### 3.2 Tenant Column
**Decision:** Every table MUST include `tenant_id`.
- **Rationale:** RLS enforcement, audit scoping, data residency

### 3.3 Timestamps
**Decision:** All tables include `created_at`, `updated_at` as `timestamptz`.
- **Rationale:** Audit trail, debugging, compliance

### 3.4 Claim Structure
**Decision:** Claims are atomic facts with:
- `claim_id`, `deal_id`, `claim_text`, `claim_class`, `claim_type`
- `value_struct` (typed: number/string/range with unit/currency/time_window)
- `materiality` (LOW/MEDIUM/HIGH/CRITICAL)
- `claim_verdict` (VERIFIED/CONTRADICTED/INFLATED/UNVERIFIED/SUBJECTIVE)
- `claim_grade` (A/B/C/D)
- `sanad_id` (FK to sanads)

### 3.5 Sanad Structure
**Decision:** Sanad includes:
- `transmission_chain` (list of TransmissionNodes)
- `corroboration_status` (NONE/AHAD_1/AHAD_2/MUTAWATIR)
- `defects` (list of Defect objects)
- `sanad_grade` (computed by deterministic algorithm)

### 3.6 Defect Taxonomy
**Decision:** Defect types are fixed:
- `BROKEN_CHAIN`, `MISSING_LINK`, `UNKNOWN_SOURCE`, `CONCEALMENT`, `INCONSISTENCY`
- `ANOMALY_VS_STRONGER_SOURCES`, `CHRONO_IMPOSSIBLE`, `CHAIN_GRAFTING`, `CIRCULARITY`
- `STALENESS`, `UNIT_MISMATCH`, `TIME_WINDOW_MISMATCH`, `SCOPE_DRIFT`, `IMPLAUSIBILITY`

Severity rules:
- **FATAL**: `BROKEN_CHAIN`, `CONCEALMENT`, `CIRCULARITY` → forces grade D
- **MAJOR**: `INCONSISTENCY`, `ANOMALY_VS_STRONGER_SOURCES`, `UNKNOWN_SOURCE` → downgrade one level
- **MINOR**: `STALENESS`, `UNIT_MISMATCH`, `TIME_WINDOW_MISMATCH`, `SCOPE_DRIFT` → flag only

---

## 4. API Decisions (LOCKED)

### 4.1 Versioning
**Decision:** All endpoints prefixed `/v1/`.
- **Rationale:** API versioning for backward compatibility

### 4.2 Idempotency
**Decision:** Idempotency via `Idempotency-Key` header; duplicate = 409.
- **Rationale:** Safe retries, exactly-once semantics

### 4.3 Error Model
**Decision:** RFC 7807-compliant error envelope:
```json
{
  "error": {
    "code": "ERROR_CODE",
    "message": "Human-readable message",
    "details": {},
    "request_id": "uuid"
  }
}
```

### 4.4 Middleware Order
**Decision:** Fixed middleware stack (outermost → innermost):
1. RequestId
2. DBTx
3. Audit
4. OpenAPIValidation
5. RateLimit
6. RBAC
7. Idempotency

### 4.5 Rate Limits
**Decision:** 600 req/min user, 1200 req/min integration; 429 on exceed.

---

## 5. Sanad Methodology Decisions (LOCKED)

### 5.1 Six-Level Source Tiers
| Tier | Code | Weight | Admissibility |
|------|------|--------|---------------|
| 1 | `ATHBAT_AL_NAS` | 1.00 | PRIMARY |
| 2 | `THIQAH_THABIT` | 0.90 | PRIMARY |
| 3 | `THIQAH` | 0.80 | PRIMARY |
| 4 | `SADUQ` | 0.65 | PRIMARY |
| 5 | `SHAYKH` | 0.50 | SUPPORT_ONLY |
| 6 | `MAQBUL` | 0.40 | SUPPORT_ONLY |

### 5.2 Grade Algorithm
```
1. base_grade = MIN(source_grade for node in transmission_chain)
2. If any FATAL defect → return D
3. major_count = count(defects where severity == MAJOR)
4. grade = downgrade(base_grade, steps=major_count)
5. If grade == B and corroboration == MUTAWATIR and major_count == 0 → upgrade to A
6. If grade == C and corroboration == MUTAWATIR and major_count == 0 → upgrade to B
7. Cannot upgrade beyond A; corroboration cannot cure FATAL/MAJOR defects
```

### 5.3 Independence Rules (Mutawātir)
Two sources are independent iff:
- Different source_system
- Different upstream_origin_id (HARD RULE)
- Different artifact identity
- Time separation ≥ 1 hour
- No shared transmission nodes

Mutawātir threshold: ≥3 independent sources AND collusion_risk ≤ 0.30

---

## 6. Debate Decisions (LOCKED)

### 6.1 Role Set
- **Advocate** — proposes best-supported thesis
- **Sanad Breaker** — attacks weak chains, missing links
- **Contradiction Finder** — cross-doc inconsistencies
- **Risk Officer** — downside, fraud, regulatory risk
- **Arbiter** — rules enforcement, validates challenges, preserves dissent

### 6.2 Stop Conditions (Priority Order)
1. `CRITICAL_DEFECT` — material claim has grade D → escalate to human
2. `MAX_ROUNDS` — round_number ≥ 5
3. `CONSENSUS` — confidence spread ≤ 0.10
4. `STABLE_DISSENT` — positions unchanged for 2 rounds → preserve dissent
5. `EVIDENCE_EXHAUSTED` — no new evidence available

### 6.3 Node Graph
```
START → advocate_opening()
      → sanad_breaker_challenge()
      → observer_critiques_parallel()
      → advocate_rebuttal()
      → evidence_call_retrieval() [conditional]
      → arbiter_close()
      → stop_condition_check() [LOOP or EXIT]
      → muhasabah_validate_all() [HARD GATE]
      → finalize_outputs() → END
```

---

## 7. Technology Decisions (LOCKED)

| Layer | Technology | Version |
|-------|------------|---------|
| Runtime | Python | 3.11+ |
| Web Framework | FastAPI | 0.109+ |
| Validation | Pydantic | 2.5+ |
| Database ORM | SQLAlchemy | 2.0+ |
| Migrations | Alembic | 1.13+ |
| Database | PostgreSQL | 16+ |
| Tracing | OpenTelemetry | 1.22+ |
| Frontend | Next.js | 14+ |
| Styling | TailwindCSS | 3.4+ |

---

## 8. Forbidden Patterns (BANNED)

| Pattern | Why Banned |
|---------|------------|
| `print()` in `src/` | Use structured logging |
| `import *` | Always explicit imports |
| Raw SQL strings | Use SQLAlchemy or parameterized queries |
| `datetime.now()` | Use `datetime.now(timezone.utc)` |
| `json.loads()` on LLM output without Pydantic | Must validate schema |
| `# TODO` without task ID | Use `# TODO(IDIS-XXX):` |
| Bare `except:` | Must specify exception type |
| `except Exception:` without explicit handling | Must log at minimum |
| `return True` as default in validators | Fail-closed required |
| Bare `pass` in `except` blocks | Must handle or re-raise |

---

## 9. Process Decisions (LOCKED)

### 9.1 Commit Convention
```
<type>(<scope>): <description>

Types: feat, fix, refactor, test, docs, chore
Scope: phase identifier (e.g., phase-1, api, sanad, calc, debate)
```

### 9.2 Pre-Commit Gates
```bash
make format && make lint && make typecheck && make test && make check
```

### 9.3 Prompt Versioning
- Prompts stored in `prompts/<prompt_id>/<version>/prompt.md` + `metadata.json`
- SemVer: MAJOR.MINOR.PATCH
- HIGH risk prompts require Gate 3 regression

---

## 10. What CAN Change

The following are **not locked** and can be modified during rebuild:

- Pipeline orchestration implementation details
- Prompt text content (with proper versioning)
- Frontend UI implementation specifics
- Deployment configuration (Docker, K8s manifests)
- Test data and benchmark datasets
- Performance optimizations that don't change semantics
- Additional API endpoints (not removing existing ones)
- New enrichment connectors
