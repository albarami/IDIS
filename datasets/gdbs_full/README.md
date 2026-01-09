# GDBS-FULL: Golden Deal Benchmark Suite (Full)

**Version:** 1.0.0  
**Created:** 2026-01-09  
**Status:** Immutable benchmark dataset for IDIS v6.3 evaluation

---

## Purpose

GDBS-FULL is a **deterministic, adversarial, realistic** synthetic dataset designed to validate IDIS end-to-end behavior against enterprise trust invariants:

- **No-Free-Facts**: Every factual claim has traceable `claim_id` or `calc_id`
- **Sanad Integrity**: Grading, corroboration, defect handling
- **Tenant Isolation**: Cross-tenant access yields `unknown_or_out_of_scope`
- **Deterministic Numerics**: Calc-Sanad reproducibility
- **Audit Completeness**: All mutations emit taxonomy-compliant events

---

## Dataset Structure

```
datasets/gdbs_full/
├── manifest.json                 # Dataset metadata and version info
├── README.md                     # This file
├── tenant/
│   └── tenant_qatar_alpha.json   # Tenant configuration
├── actors/
│   ├── analyst_1.json            # Analyst actor
│   ├── analyst_2.json            # Secondary analyst
│   └── admin_1.json              # Admin actor
├── deals/
│   ├── deal_001_clean/           # Clean baseline deal
│   ├── deal_002_contradiction/   # Contradictory numbers across docs
│   ├── deal_003_unit_mismatch/   # ARR vs revenue confusion
│   ├── deal_004_time_window_mismatch/  # FY vs LTM confusion
│   ├── deal_005_missing_evidence/      # Claims without backing
│   ├── deal_006_calc_conflict/   # Calculation disagreements
│   ├── deal_007_chain_break/     # Broken Sanad chain
│   └── deal_008_version_drift/   # Document version drift
├── expected_outcomes/
│   ├── deal_001_expected.json    # Expected claims, grades, verdicts
│   ├── deal_002_expected.json
│   ├── deal_003_expected.json
│   ├── deal_004_expected.json
│   ├── deal_005_expected.json
│   ├── deal_006_expected.json
│   ├── deal_007_expected.json
│   └── deal_008_expected.json
└── audit_expectations/
    └── required_events.json      # Required audit events per operation
```

---

## Claim Set (C1–C7)

Each deal contains the **same 7 material claims** for consistency:

| ID  | Claim Class | Description                      | Materiality |
|-----|-------------|----------------------------------|-------------|
| C1  | FINANCIAL   | ARR (Annual Recurring Revenue)   | CRITICAL    |
| C2  | FINANCIAL   | Gross Margin (%)                 | HIGH        |
| C3  | FINANCIAL   | Monthly Burn Rate                | HIGH        |
| C4  | FINANCIAL   | Runway (months)                  | CRITICAL    |
| C5  | TRACTION    | CAC (Customer Acquisition Cost)  | MEDIUM      |
| C6  | TRACTION    | NRR (Net Revenue Retention)      | HIGH        |
| C7  | MARKET_SIZE | TAM Estimate                     | MEDIUM      |

---

## Rounding & Currency Rules (Deterministic)

All numeric values follow these deterministic rules:

- **Currency**: USD (ISO 4217)
- **ARR/Revenue**: Round to nearest $1,000 (ROUND_HALF_UP)
- **Percentages (GM, NRR)**: 2 decimal places (ROUND_HALF_UP)
- **Burn/CAC**: Round to nearest $100 (ROUND_HALF_UP)
- **Runway**: 1 decimal place (months, ROUND_HALF_UP)
- **TAM**: Round to nearest $1M (ROUND_HALF_UP)

---

## Adversarial Deal Matrix

| Deal | Scenario              | Injected Issue                         | Expected Defect        | Expected Verdict |
|------|-----------------------|----------------------------------------|------------------------|------------------|
| 001  | Clean                 | None                                   | None                   | VERIFIED         |
| 002  | Contradiction         | Deck ARR ≠ Model ARR                   | INCONSISTENCY          | CONTRADICTED     |
| 003  | Unit Mismatch         | ARR labeled, but value is MRR×12 error | UNIT_MISMATCH          | INFLATED         |
| 004  | Time Window Mismatch  | FY2024 vs LTM mismatch                 | TIME_WINDOW_MISMATCH   | UNVERIFIED       |
| 005  | Missing Evidence      | Claim with no backing span             | MISSING_LINK           | UNVERIFIED       |
| 006  | Calc Conflict         | GM calc differs from stated GM         | INCONSISTENCY          | CONTRADICTED     |
| 007  | Chain Break           | Sanad chain has orphaned node          | BROKEN_CHAIN           | UNVERIFIED       |
| 008  | Version Drift         | Old doc version cited, newer exists    | STALENESS              | UNVERIFIED       |

---

## Realistic VC Ranges

Values are constrained to realistic early-stage VC ranges:

| Metric       | Seed         | Series A      | Series B       |
|--------------|--------------|---------------|----------------|
| ARR          | $0.5M–$2M    | $2M–$10M      | $10M–$30M      |
| Gross Margin | 50%–80%      | 60%–85%       | 65%–90%        |
| Burn Rate    | $50K–$200K   | $200K–$800K   | $500K–$2M      |
| Runway       | 12–24 months | 18–36 months  | 18–48 months   |
| CAC          | $500–$5,000  | $1,000–$10,000| $2,000–$20,000 |
| NRR          | 90%–130%     | 100%–140%     | 105%–150%      |
| TAM          | $1B–$50B     | $5B–$100B     | $10B–$200B     |

---

## Manual Artifacts

Each deal contains **manually created** artifacts (no parsers required):

1. **Pitch Deck PDF**: Minimal single-page PDF with key metrics
2. **Financial Model XLSX**: Simple spreadsheet with P&L summary

Span anchors are **manual JSON** files with stable locators:
- PDF: `{page: N, bbox: [x0, y0, x1, y1]}`
- XLSX: `{sheet: "SheetName", cell: "CellRef"}`

---

## Usage

```python
from idis.testing.gdbs_loader import GDBSLoader

loader = GDBSLoader("datasets/gdbs_full")
dataset = loader.load()

# Access tenant
tenant = dataset.tenant

# Access deals
for deal in dataset.deals:
    print(deal.deal_id, deal.scenario)

# Access expected outcomes
expected = dataset.get_expected_outcome("deal_001")
```

---

## Immutability Contract

This dataset is **versioned and immutable**:
- Any modification requires a new version
- SHA256 checksums in `manifest.json` must match
- Tests validate integrity before use

---

## License

Internal use only. Synthetic data for IDIS evaluation.
