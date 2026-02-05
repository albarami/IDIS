# Testing and GDBS Data Plan

**Version:** 1.0.0  
**Date:** 2026-02-05  
**Status:** Build Spec  
**Reference:** IDIS_Evaluation_Harness_and_Release_Gates_v6_3.md

---

## 1. Overview

This document specifies the Golden Deal Benchmark Suite (GDBS) datasets, synthetic data generation rules, and test-to-gate mappings required for IDIS release gates.

---

## 2. GDBS Dataset Structure

### 2.1 Dataset Tiers

| Tier | Code | Deals | Purpose | Gate |
|------|------|-------|---------|------|
| Screening | GDBS-S | 20 | Quick regression | Gate 1, 2 |
| Full | GDBS-F | 100 | Broad coverage | Gate 3 |
| Adversarial | GDBS-A | 30 | Injected failures | Gate 2, 3 |

### 2.2 Directory Structure

```
datasets/
├── gdbs/
│   ├── README.md
│   ├── gdbs_s/
│   │   ├── manifest.json
│   │   ├── deals/
│   │   │   ├── deal_001/
│   │   │   │   ├── metadata.json
│   │   │   │   ├── documents/
│   │   │   │   │   ├── pitch_deck.pdf
│   │   │   │   │   ├── financial_model.xlsx
│   │   │   │   │   └── ...
│   │   │   │   └── expected/
│   │   │   │       ├── claims.json
│   │   │   │       ├── sanad.json
│   │   │   │       ├── defects.json
│   │   │   │       ├── calcs.json
│   │   │   │       └── debate.json
│   │   │   └── deal_002/
│   │   │       └── ...
│   │   └── golden_expectations.json
│   ├── gdbs_f/
│   │   └── ... (same structure)
│   └── gdbs_a/
│       ├── manifest.json
│       ├── deals/
│       │   └── adversarial_001/
│       │       ├── metadata.json
│       │       ├── documents/
│       │       ├── injections.json      # What was injected
│       │       └── expected/
│       │           ├── defects.json     # Expected defect detection
│       │           └── verdicts.json    # Expected claim verdicts
│       └── injection_catalog.json
└── synthetic/
    ├── generators/
    │   ├── pitch_deck_generator.py
    │   ├── financial_model_generator.py
    │   └── injection_generator.py
    └── templates/
        ├── pitch_deck_template.pptx
        └── financial_model_template.xlsx
```

---

## 3. GDBS-S (Screening) Specification

### 3.1 Coverage Requirements

| Dimension | Values | Min Deals |
|-----------|--------|-----------|
| Stage | Seed, Series A, Series B | 5 each |
| Sector | SaaS, Fintech, Marketplace | 5 each |
| Doc Types | Deck, Model, Statement, Contract | All in each |
| Complexity | Simple (5 claims), Medium (20), Complex (50+) | 5 each |

### 3.2 Golden Expectations Schema

```json
{
  "deal_id": "gdbs_s_001",
  "expected_claims": [
    {
      "claim_text": "ARR of $2.5M as of Q4 2025",
      "claim_class": "FINANCIAL",
      "value_struct": {
        "value": 2500000,
        "currency": "USD",
        "time_window": "Q4 2025"
      },
      "expected_grade": "B",
      "expected_verdict": "VERIFIED",
      "source_doc": "financial_model.xlsx",
      "source_location": {"sheet": "Summary", "cell": "B12"}
    }
  ],
  "expected_defects": [],
  "expected_calcs": [
    {
      "calc_type": "GROWTH_RATE",
      "expected_output": {"yoy_growth": 0.85}
    }
  ],
  "expected_debate_outcome": {
    "stop_reason": "CONSENSUS",
    "max_rounds": 3
  }
}
```

---

## 4. GDBS-F (Full) Specification

### 4.1 Coverage Requirements

| Dimension | Values | Min Deals |
|-----------|--------|-----------|
| Stage | Pre-seed, Seed, A, B, C, Growth | 15 each |
| Sector | SaaS, Fintech, Marketplace, Healthcare, Deeptech, Consumer | 15 each |
| Geography | US, EU, MENA, APAC | 20 each |
| Deal Size | <$1M, $1-10M, $10-50M, >$50M | 20 each |
| Complexity | Simple, Medium, Complex, Very Complex | 25 each |

### 4.2 Edge Cases Required

- [ ] Deals with only pitch deck (no financials)
- [ ] Deals with contradictory metrics across documents
- [ ] Deals with stale data (>12 months old)
- [ ] Deals with Grade C primary sources only
- [ ] Deals with multiple funding rounds in docs
- [ ] Deals with foreign currency metrics
- [ ] Deals with ambiguous time windows

---

## 5. GDBS-A (Adversarial) Specification

### 5.1 Injection Types

| Injection | Code | Expected Defect | Severity |
|-----------|------|-----------------|----------|
| Unit mismatch | `INJ_UNIT` | `UNIT_MISMATCH` | MINOR |
| Time window mismatch | `INJ_TIME` | `TIME_WINDOW_MISMATCH` | MINOR |
| Circularity | `INJ_CIRC` | `CIRCULARITY` | FATAL |
| Staleness | `INJ_STALE` | `STALENESS` | MINOR |
| Concealment | `INJ_HIDE` | `CONCEALMENT` | FATAL |
| Contradiction | `INJ_CONTRA` | `INCONSISTENCY` | MAJOR |
| Chain grafting | `INJ_GRAFT` | `CHAIN_GRAFTING` | FATAL |
| Broken chain | `INJ_BREAK` | `BROKEN_CHAIN` | FATAL |

### 5.2 Injection Specification

```json
{
  "injection_id": "inj_001",
  "deal_id": "gdbs_a_001",
  "injection_type": "INJ_CONTRA",
  "description": "ARR in deck says $3M, model says $2.5M",
  "injected_values": {
    "deck_value": {"value": 3000000, "location": "slide_5"},
    "model_value": {"value": 2500000, "location": "B12"}
  },
  "expected_detection": {
    "defect_type": "INCONSISTENCY",
    "severity": "MAJOR",
    "affected_claim_class": "FINANCIAL"
  },
  "expected_cure_protocol": "HUMAN_ARBITRATION"
}
```

### 5.3 Adversarial Coverage

| Injection Type | Min Deals | Detection Target |
|----------------|-----------|------------------|
| `INJ_UNIT` | 5 | ≥ 90% |
| `INJ_TIME` | 5 | ≥ 90% |
| `INJ_CIRC` | 3 | 100% (FATAL) |
| `INJ_STALE` | 5 | ≥ 80% |
| `INJ_HIDE` | 3 | 100% (FATAL) |
| `INJ_CONTRA` | 5 | ≥ 90% |
| `INJ_GRAFT` | 2 | 100% (FATAL) |
| `INJ_BREAK` | 2 | 100% (FATAL) |

---

## 6. Synthetic Data Generation

### 6.1 Deterministic Generation Rules

```python
class SyntheticDealGenerator:
    """Generate deterministic synthetic deals for testing."""
    
    def __init__(self, seed: int):
        self.rng = random.Random(seed)
        self.faker = Faker()
        self.faker.seed_instance(seed)
    
    def generate_deal(
        self,
        stage: DealStage,
        sector: Sector,
        complexity: Complexity,
    ) -> SyntheticDeal:
        """Generate a complete synthetic deal with documents."""
        
        # Deterministic company generation
        company = self._generate_company(sector)
        
        # Deterministic metrics based on stage
        metrics = self._generate_metrics(stage, sector)
        
        # Generate documents
        documents = self._generate_documents(company, metrics, complexity)
        
        # Generate expected outputs
        expected = self._generate_expected_outputs(company, metrics, documents)
        
        return SyntheticDeal(
            company=company,
            metrics=metrics,
            documents=documents,
            expected=expected,
        )
```

### 6.2 Metric Generation Rules

```python
METRIC_RANGES = {
    "SEED": {
        "arr": (100_000, 1_000_000),
        "mrr_growth": (0.05, 0.20),
        "burn_rate": (50_000, 200_000),
        "runway_months": (12, 24),
    },
    "SERIES_A": {
        "arr": (1_000_000, 5_000_000),
        "mrr_growth": (0.08, 0.15),
        "burn_rate": (200_000, 500_000),
        "runway_months": (18, 30),
    },
    # ... more stages
}

def generate_metrics(stage: str, seed: int) -> dict:
    """Generate plausible metrics for a stage."""
    rng = random.Random(seed)
    ranges = METRIC_RANGES[stage]
    
    return {
        metric: rng.uniform(low, high)
        for metric, (low, high) in ranges.items()
    }
```

### 6.3 Document Templates

| Doc Type | Template | Variables |
|----------|----------|-----------|
| Pitch Deck | `pitch_deck_template.pptx` | company_name, metrics, team, market |
| Financial Model | `financial_model_template.xlsx` | metrics, projections, cohorts |
| Bank Statement | `bank_statement_template.pdf` | transactions, balances |
| Cap Table | `cap_table_template.xlsx` | shareholders, rounds, options |

---

## 7. Test-to-Gate Mapping

### 7.1 Gate 0: Unit & Schema

| Test | Dataset | Pass Criteria |
|------|---------|---------------|
| JSON schema validation | All | 100% valid |
| Migration checks | N/A | All pass |
| Lint/type checks | N/A | 0 errors |

### 7.2 Gate 1: Structural Trust

| Test | Dataset | Pass Criteria |
|------|---------|---------------|
| No-Free-Facts | GDBS-S | 0 violations |
| Muḥāsabah pass rate | GDBS-S | ≥ 98% |
| Audit coverage | GDBS-S | 100% |
| Tenant isolation | GDBS-S | 0 violations |

### 7.3 Gate 2: Core Quality

| Test | Dataset | Pass Criteria |
|------|---------|---------------|
| Sanad coverage | GDBS-S | ≥ 95% material claims |
| Defect recall | GDBS-A | ≥ 90% |
| Calc reproducibility | GDBS-S | ≥ 99.9% |
| Extraction accuracy | GDBS-S | ≥ 90% |

### 7.4 Gate 3: Full Regression

| Test | Dataset | Pass Criteria |
|------|---------|---------------|
| Pipeline completion | GDBS-F | ≥ 95% |
| Debate completion | GDBS-F | ≥ 98% |
| Max rounds exceeded | GDBS-F | ≤ 5% |
| Deliverable generation | GDBS-F | ≥ 98% |
| FATAL defect detection | GDBS-A | 100% |

### 7.5 Gate 4: Human Review

| Test | Dataset | Pass Criteria |
|------|---------|---------------|
| Truth Dashboard accuracy | 10 sampled | Analyst approval |
| IC Memo usefulness | 10 sampled | Partner approval |
| Critical issue check | All sampled | 0 critical issues |

---

## 8. Test Execution Scripts

### 8.1 Gate 3 Script

```python
# scripts/gates/gate_3_gdbs_f.py

async def run_gate_3():
    """Execute Gate 3 evaluation on GDBS-F."""
    
    results = Gate3Results()
    
    # Load GDBS-F dataset
    dataset = load_gdbs_f()
    
    for deal in dataset.deals:
        try:
            # Run full pipeline
            run = await pipeline.execute(deal)
            
            # Check completion
            if run.status == "COMPLETED":
                results.completed += 1
            else:
                results.failed.append({
                    "deal_id": deal.id,
                    "status": run.status,
                    "error": run.error,
                })
            
            # Check debate
            if run.debate:
                results.debate_rounds.append(run.debate.round_count)
                if run.debate.stop_reason == "MAX_ROUNDS":
                    results.max_rounds_exceeded += 1
            
            # Check deliverables
            if run.deliverables:
                results.deliverables_generated += 1
            
        except Exception as e:
            results.errors.append({
                "deal_id": deal.id,
                "error": str(e),
            })
    
    # Calculate metrics
    results.completion_rate = results.completed / len(dataset.deals)
    results.debate_completion_rate = (
        (results.completed - results.max_rounds_exceeded) / results.completed
    )
    
    # Check pass criteria
    results.passed = (
        results.completion_rate >= 0.95 and
        results.debate_completion_rate >= 0.98 and
        (results.max_rounds_exceeded / results.completed) <= 0.05
    )
    
    return results
```

### 8.2 Adversarial Test Script

```python
# scripts/gates/gate_adversarial.py

async def run_adversarial_tests():
    """Test defect detection on GDBS-A."""
    
    results = AdversarialResults()
    dataset = load_gdbs_a()
    
    for deal in dataset.deals:
        injections = deal.injections
        
        # Run pipeline
        run = await pipeline.execute(deal)
        
        # Check each injection was detected
        for injection in injections:
            detected = check_defect_detected(
                run.defects,
                injection.expected_detection,
            )
            
            if detected:
                results.detected[injection.type] += 1
            else:
                results.missed[injection.type].append({
                    "deal_id": deal.id,
                    "injection": injection,
                })
            
            results.total[injection.type] += 1
    
    # Calculate detection rates
    for inj_type in InjectionType:
        results.detection_rate[inj_type] = (
            results.detected[inj_type] / results.total[inj_type]
        )
    
    # Check FATAL detection is 100%
    fatal_types = ["INJ_CIRC", "INJ_HIDE", "INJ_GRAFT", "INJ_BREAK"]
    results.fatal_detection_100 = all(
        results.detection_rate[t] == 1.0
        for t in fatal_types
        if results.total[t] > 0
    )
    
    return results
```

---

## 9. Fixture Management

### 9.1 Fixture Determinism

All fixtures must be deterministic:

```python
@pytest.fixture
def gdbs_s_deal_001():
    """Load GDBS-S deal 001 with deterministic state."""
    return load_deal(
        path="datasets/gdbs/gdbs_s/deals/deal_001",
        freeze_time="2026-01-15T00:00:00Z",
    )

@pytest.fixture
def synthetic_seed_deal(request):
    """Generate synthetic deal with parameterized seed."""
    seed = request.param.get("seed", 42)
    return SyntheticDealGenerator(seed).generate_deal(
        stage="SEED",
        sector="SAAS",
        complexity="MEDIUM",
    )
```

### 9.2 Fixture Versioning

```json
{
  "dataset": "gdbs_s",
  "version": "1.0.0",
  "created_at": "2026-02-05",
  "sha256": "abc123...",
  "deal_count": 20,
  "expectations_version": "1.0.0"
}
```

---

## 10. Acceptance Criteria

### 10.1 Dataset Requirements
- [ ] GDBS-S: 20 deals with all coverage dimensions
- [ ] GDBS-F: 100 deals with full coverage
- [ ] GDBS-A: 30 deals with all injection types
- [ ] All datasets have golden expectations
- [ ] All fixtures are deterministic

### 10.2 Test Requirements
- [ ] Gate 1 tests implemented and passing
- [ ] Gate 2 tests implemented and passing
- [ ] Gate 3 test script executable
- [ ] Adversarial detection tests implemented

### 10.3 Documentation Requirements
- [ ] Dataset README with usage instructions
- [ ] Injection catalog documented
- [ ] Test-to-gate mapping complete
