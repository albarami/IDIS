# IDIS Evaluation Harness & Release Gates (v6.3)
**Version:** 6.3 (derived from IDIS v6.3 FINAL)  
**Date:** 2026-01-06  
**Status:** Normative baseline for production-quality verification and safe iteration  
**Audience:** Data/ML, Backend, SRE, Product, QA, Security/Compliance

---

## 0) Purpose

This document defines the **evaluation harness** and **release gating system** for IDIS, ensuring the platform can evolve safely while maintaining enterprise-grade trust invariants:

- **No‑Free‑Facts** (all factual statements traceable to claim_id/calc_id)
- **Sanad chain integrity** (grading, corroboration, defect handling)
- **Muḥāsabah gate compliance**
- **Deterministic calculation reproducibility**
- **Debate convergence and non-gaming**
- **Audit event completeness**

This is required for:
- safe prompt/model updates
- calc engine versioning
- ingestion/extraction improvements
- connector changes
- UI/templating changes that affect IC deliverables

---

## 1) Evaluation Principles (Non‑negotiable)

1. **Test what you deploy**
   - Every change to prompts, validators, calculators, ingestion rules, or debate policies must pass regression tests on a fixed benchmark set before promotion.

2. **Separate “truth correctness” from “process integrity”**
   - IDIS is decision-support under uncertainty. Evaluation must measure:
     - correctness where ground truth exists
     - detection of uncertainty where ground truth is missing
     - adherence to trust constraints (No‑Free‑Facts, Muḥāsabah, Calc‑Sanad)

3. **Prefer deterministic checks over subjective scoring**
   - Many failures are structural (missing claim refs, wrong units, broken chains). These should be caught by validators, not judges.

4. **Golden datasets are immutable**
   - Benchmarks must be versioned and immutable. Any change requires a new benchmark version and explicit rationale.

---

## 2) What must be evaluated

### 2.1 Structural/Compliance (Hard Gates)
- No‑Free‑Facts violations: **0 tolerated** in IC-bound outputs
- Audit coverage: **100%** for all mutating operations
- Muḥāsabah: **≥ 98%** pass rate on required outputs (rejects must be explainable)
- Calc reproducibility: **≥ 99.9%** reproducible runs on identical inputs
- Tenant isolation tests: **0 tolerated** violations

### 2.2 Extraction & Claim Quality (Soft/Hard depending on tier)
- Extraction confidence distribution (>=0.95 for required fields)
- Sanad coverage rate (target ≥95% for material claims)
- Defect recall for known injected defects (target ≥90% on benchmark)
- False positive defect rate (target ≤10% for MAJOR/FATAL)

### 2.3 Debate & Decision Quality (Soft gates + monitoring)
- Debate completion rate (≥98%)
- Max rounds exceeded rate (≤5%)
- Stable dissent rate (monitor; expected non-zero)
- Utility scoring integrity (anti-gaming checks)

### 2.4 Output Usefulness (Human-in-the-loop evaluation)
- IC memo quality rubric (human review on sample deals)
- Truth Dashboard clarity (human review)
- Diligence request relevance and completeness

---

## 3) Golden Deal Benchmark Suite (GDBS)

### 3.1 Purpose
A set of **representative deals** with curated artifacts and expected system behavior:
- extraction outputs
- claim registry entries
- sanad grades/verdicts/actions
- calc outputs
- defects/cure protocols
- debate session stop reasons
- deliverable validator results

### 3.2 Benchmark Tiers
- **GDBS‑S (Screening):** 20 deals; common patterns; quick regression
- **GDBS‑F (Full):** 100 deals; broad coverage across industries and stages
- **GDBS‑A (Adversarial):** 30 deals; explicitly injected failures and fraud patterns

### 3.3 Required Coverage Dimensions
- stage: seed, A, B, growth
- industries: SaaS, fintech, marketplace, healthcare, deeptech
- doc types: deck, financial model, bank statement, cap table, contracts, transcripts
- edge cases:
  - missing units/time windows
  - contradictory numbers across docs
  - stale metrics
  - manipulated cohort tables
  - deceptive TAM claims
  - “deck-only” claims with Grade C

### 3.4 Benchmark Storage
- Store benchmark artifacts in a dedicated bucket with:
  - WORM policy (write-once)
  - sha256 hashes
  - clear licensing restrictions
- Store expected outputs as JSON (versioned):
  - `expected_claims.json`
  - `expected_sanad.json`
  - `expected_defects.json`
  - `expected_calcs.json`
  - `expected_debate.json`
  - `expected_deliverable_checks.json`

---

## 4) Synthetic & Adversarial Test Generation

### 4.1 Synthetic Docs
Generate synthetic pitch decks and financial models that:
- mimic formatting and structure
- inject controlled errors (units, windows, decimals, currency)
- include known truth values for deterministic evaluation

### 4.2 Adversarial Injections (Must Exist)
Each injection creates known expected defects:

- **Unit mismatch injection** (ARR vs revenue)
- **Time-window mismatch** (FY vs LTM)
- **Circularity injection** (analysis cites itself)
- **Staleness injection** (metrics 18 months old but presented as current)
- **Concealment injection** (missing key rows; inconsistent totals)
- **Contradiction injection** (deck vs export mismatch)
- **Chain grafting injection** (evidence span points to unrelated statement)

Expected outcomes:
- correct `defect_type` created
- correct severity
- correct cure protocol emitted
- claim verdict downgraded appropriately

---

## 5) Deterministic Test Harness

### 5.1 Calculator Regression Tests
For each calc engine:
- fixed input claims
- expected outputs
- formula hash pinned

Hard requirements:
- any change to formula requires:
  - version bump
  - re-baselining expected outputs
  - approval gate

### 5.2 Reproducibility Checks
- Run each calc twice with same inputs:
  - output value must match
  - reproducibility hash must match
- Fail build if mismatch rate > 0.1% in benchmark

---

## 6) Validators as First-Class Tests

### 6.1 No‑Free‑Facts Validator
Test cases:
- deliverable includes unreferenced factual claim → must fail
- deliverable includes number without calc_id or numeric claim_id → must fail
- deliverable includes purely subjective section → allowed if labeled SUBJECTIVE and no factual assertions

### 6.2 Muḥāsabah Validator
Test cases:
- confidence > 0.8 with Ahad + Grade C and no uncertainties → fail
- recommendation output missing falsifiability tests → fail
- supported_claim_ids empty but facts present → fail

### 6.3 Sanad Integrity Validator
Test cases:
- claim missing primary evidence span → fail
- sanad chain with missing node fields → fail
- independence count computed incorrectly → fail
- defect severity mismatch vs schema → fail

---

## 7) Debate Evaluation

### 7.1 Stop Condition Tests
Benchmark debate sessions must satisfy:
- stop_reason is one of: CONSENSUS, STABLE_DISSENT, EVIDENCE_EXHAUSTED, MAX_ROUNDS, CRITICAL_DEFECT
- no session loops indefinitely
- max_rounds exceeded rate <= 5% on GDBS‑F

### 7.2 Anti-Gaming Tests
- Sanad Breaker utility only awarded if arbiter validates defect or evidence gap
- Penalize frivolous challenges (no claim refs, no defects, no corroboration arguments)
- Detect repeated low-value challenges → throttle or penalize

### 7.3 Proper Scoring (Brier) — Offline Calibration
- Use Brier score for probabilistic forecasts where ground truth later becomes available (post-IC outcomes or verified diligence results)
- Do not use Brier as a runtime gate unless truth labels exist

---

## 8) Release Gates (Promotion Policy)

### 8.1 Environments
- **dev**: unrestricted; unit tests required
- **staging**: must pass GDBS‑S
- **preprod**: must pass GDBS‑F + security suite
- **prod**: only promote with approval gates

### 8.2 Gate Types
**Gate 0: Unit & Schema**
- All JSON schema validations pass
- Migration checks pass
- Lint/type checks pass

**Gate 1: Structural Trust**
- No‑Free‑Facts: 0 violations on GDBS‑S
- Muḥāsabah pass rate ≥98% on required outputs
- Audit coverage 100% on test mutations
- Tenant isolation tests pass

**Gate 2: Core Quality**
- Sanad coverage ≥95% for material claims on GDBS‑S
- Defect recall ≥90% on adversarial injections
- Calc reproducibility ≥99.9%

**Gate 3: Full Regression**
- Run GDBS‑F end-to-end:
  - ingestion → claims → sanad → calcs → debate → deliverables
- Debate completion ≥98%
- Max rounds ≤5%

**Gate 4: Human Review**
- Sample 10 deals:
  - analyst review of Truth Dashboard correctness
  - partner review of IC memo usefulness
- Any critical issue blocks release

### 8.3 Rollback Policy
Rollback must be possible for:
- prompt versions
- calc engine versions
- validator rules
- orchestration policies

Rollbacks require:
- audit log event
- incident ticket ID
- clear reason

---

## 9) Prompt Registry & Versioning (Enforced)

### 9.1 Prompt Registry Requirements
Each prompt is stored as:
- `prompt_id` (stable)
- `version` (semver)
- `owner`
- `change_summary`
- `risk_class` (low/medium/high)
- `dependencies` (tools, schemas)
- `evaluation_results_ref` (link to gating results)

### 9.2 Promotion Rules
- Any prompt change touching:
  - No‑Free‑Facts behavior
  - Muḥāsabah outputs
  - debate policy
  - claim classification
  requires Gate 3 (Full Regression).

---

## 10) Observability for Evaluation

### 10.1 Metrics to Track
- No‑Free‑Facts violations count
- Validator reject reasons distribution
- Sanad grade distribution drift
- Defect frequency by type/severity
- Calc reproducibility failures
- Debate stop_reason distribution
- Deliverable generation failures

### 10.2 Alerting
- Any No‑Free‑Facts violation in prod → SEV‑1
- Any tenant isolation issue → SEV‑1
- Calc reproducibility failures spike → SEV‑2
- Audit coverage drop → SEV‑1/2 depending on scope

---

## 11) Implementation Checklist

- Create `benchmarks/` repo with immutable versions
- Build test harness CLI:
  - `idis test gdbs-s`
  - `idis test gdbs-f`
  - `idis test gdbs-a`
- Integrate into CI/CD pipelines
- Add preprod gates and approvals
- Implement prompt registry with semver and rollback hooks
- Establish human review rubric and workflow

---

## 12) Definition of Done

Evaluation harness is production-ready when:
- GDBS‑S and GDBS‑F run end-to-end in CI
- All release gates implemented
- Prompt registry operational with versioning and rollback
- Metrics dashboards exist for ongoing quality monitoring
- A documented process exists for re-baselining benchmarks

