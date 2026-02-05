# Claim Extraction Pipeline Specification

**Version:** 1.0.0  
**Date:** 2026-02-05  
**Status:** Build Spec  
**Phase:** Gate 3 Unblock

---

## 1. Overview

This document specifies the claim extraction pipeline that transforms parsed documents into structured claims with evidence linking. This is the **critical E2E glue** between document ingestion and the Sanad trust framework.

---

## 2. Pipeline Architecture

```
┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│  Document   │───▶│  Chunking   │───▶│  Extraction │───▶│  Entity     │
│  (Parsed)   │    │  Service    │    │  Service    │    │  Resolution │
└─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘
                                                                │
┌─────────────┐    ┌─────────────┐    ┌─────────────┐           │
│  Claim      │◀───│  Conflict   │◀───│  Dedupe     │◀──────────┘
│  Registry   │    │  Resolution │    │  Service    │
└─────────────┘    └─────────────┘    └─────────────┘
```

---

## 3. Chunking Rules by Document Type

### 3.1 PDF Documents
| Strategy | Chunk Size | Overlap | Span Preservation |
|----------|------------|---------|-------------------|
| Page-based | 1 page | 0 | `{page: N, bbox: [...]}` |
| Paragraph-based | ~500 tokens | 50 tokens | `{page: N, para: M, char_start: X, char_end: Y}` |
| Table extraction | Entire table | 0 | `{page: N, table_id: T, row: R, col: C}` |

### 3.2 XLSX Documents
| Strategy | Chunk Size | Overlap | Span Preservation |
|----------|------------|---------|-------------------|
| Sheet-based | 1 sheet | 0 | `{sheet: "Name"}` |
| Named range | Named range | 0 | `{sheet: "Name", range: "A1:F20"}` |
| Table detection | Auto-detected table | 0 | `{sheet: "Name", cell: "B12"}` |

### 3.3 DOCX Documents
| Strategy | Chunk Size | Overlap | Span Preservation |
|----------|------------|---------|-------------------|
| Paragraph-based | 1 paragraph | 0 | `{para_index: N}` |
| Section-based | Heading + content | 0 | `{section: "Heading Text", para_range: [X, Y]}` |
| Table extraction | Entire table | 0 | `{table_index: T, row: R, col: C}` |

### 3.4 PPTX Documents
| Strategy | Chunk Size | Overlap | Span Preservation |
|----------|------------|---------|-------------------|
| Slide-based | 1 slide | 0 | `{slide: N}` |
| Shape-based | 1 text shape | 0 | `{slide: N, shape_id: S}` |
| Table extraction | Entire table | 0 | `{slide: N, table_id: T, row: R, col: C}` |

---

## 4. Claim/Evidence JSON Output Contract

### 4.1 Extracted Claim Schema
```json
{
  "type": "object",
  "required": ["claim_text", "claim_class", "source_span", "extraction_confidence"],
  "properties": {
    "claim_text": {
      "type": "string",
      "description": "The factual assertion extracted"
    },
    "claim_class": {
      "type": "string",
      "enum": ["FINANCIAL", "TRACTION", "MARKET_SIZE", "COMPETITION", "TEAM", "LEGAL_TERMS", "TECHNICAL", "OTHER"]
    },
    "value_struct": {
      "type": "object",
      "properties": {
        "value_type": {"enum": ["NUMBER", "CURRENCY", "PERCENTAGE", "RANGE", "DATE", "TEXT"]},
        "value": {},
        "unit": {"type": "string"},
        "currency": {"type": "string"},
        "time_window": {"type": "string"},
        "as_of_date": {"type": "string", "format": "date"}
      }
    },
    "source_span": {
      "type": "object",
      "required": ["document_id", "span_id", "locator"],
      "properties": {
        "document_id": {"type": "string", "format": "uuid"},
        "span_id": {"type": "string", "format": "uuid"},
        "locator": {"type": "object"},
        "text_excerpt": {"type": "string", "maxLength": 500}
      }
    },
    "extraction_confidence": {
      "type": "number",
      "minimum": 0,
      "maximum": 1
    },
    "materiality_hint": {
      "type": "string",
      "enum": ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    },
    "requires_human_review": {
      "type": "boolean"
    }
  }
}
```

### 4.2 Evidence Item Schema
```json
{
  "type": "object",
  "required": ["source_span_id", "source_type", "source_grade"],
  "properties": {
    "source_span_id": {"type": "string", "format": "uuid"},
    "source_type": {
      "type": "string",
      "enum": [
        "AUDITED_FINANCIAL", "REGULATORY_FILING", "SEC_FILING",
        "BANK_STATEMENT", "SIGNED_CONTRACT", "NOTARIZED_DOCUMENT",
        "FINANCIAL_MODEL", "INTERNAL_REPORT", "VERSION_CONTROLLED_DOC",
        "PITCH_DECK", "FOUNDER_STATEMENT", "EXEC_MEMO", "EMAIL",
        "PRESS_RELEASE", "THIRD_PARTY_ESTIMATE", "NEWS_ARTICLE",
        "UNKNOWN"
      ]
    },
    "source_grade": {"type": "string", "enum": ["A", "B", "C", "D"]},
    "upstream_origin_id": {"type": "string"},
    "retrieval_timestamp": {"type": "string", "format": "date-time"}
  }
}
```

---

## 5. Entity Resolution + Dedupe Rules

### 5.1 Entity Types
- `COMPANY` — target company, competitors, customers
- `PERSON` — founders, executives, investors
- `METRIC` — ARR, MRR, GMV, NRR, etc.
- `DATE` — fiscal year, quarter, as-of date
- `LOCATION` — HQ, markets, regions

### 5.2 Deduplication Rules
| Rule | Condition | Action |
|------|-----------|--------|
| **Exact match** | `claim_text` identical (normalized) | Merge, keep highest confidence |
| **Value match** | Same metric, same value, same time_window | Merge, link both spans |
| **Near match** | Same metric, values within 1% | Flag for reconciliation |
| **Conflict** | Same metric, values differ > 5% | Create conflict record |

### 5.3 Conflict Records
```json
{
  "conflict_id": "uuid",
  "claim_ids": ["uuid1", "uuid2"],
  "conflict_type": "VALUE_MISMATCH",
  "resolution_status": "PENDING",
  "resolution_strategy": null,
  "resolved_claim_id": null
}
```

---

## 6. Confidence Scoring Rules

### 6.1 Base Confidence Factors
| Factor | Weight | Description |
|--------|--------|-------------|
| Source tier | 0.30 | Higher tier → higher confidence |
| Extraction clarity | 0.25 | Clear structure vs. prose |
| Value precision | 0.20 | Explicit units/dates vs. implied |
| Context quality | 0.15 | Surrounding text supports claim |
| Model confidence | 0.10 | LLM self-reported confidence |

### 6.2 Confidence Calculation
```python
def calculate_extraction_confidence(
    source_tier: SourceTier,
    extraction_clarity: float,  # 0-1
    value_precision: float,     # 0-1
    context_quality: float,     # 0-1
    model_confidence: float,    # 0-1
) -> float:
    tier_score = source_tier.numeric_weight  # 0.4-1.0
    
    confidence = (
        0.30 * tier_score +
        0.25 * extraction_clarity +
        0.20 * value_precision +
        0.15 * context_quality +
        0.10 * model_confidence
    )
    
    return min(1.0, max(0.0, confidence))
```

### 6.3 Human Review Thresholds
| Confidence | Action |
|------------|--------|
| ≥ 0.95 | Auto-accept |
| 0.80 - 0.94 | Accept with flag |
| 0.50 - 0.79 | Requires human review |
| < 0.50 | Reject or escalate |

---

## 7. Failure Taxonomy

### 7.1 Extraction Failures
| Code | Severity | Description | Retry | Skip | Flag |
|------|----------|-------------|-------|------|------|
| `PARSE_ERROR` | ERROR | Document parsing failed | ✅ 3x | ❌ | ✅ |
| `CHUNK_TOO_LARGE` | WARN | Chunk exceeds token limit | ✅ split | ❌ | ❌ |
| `NO_CLAIMS_FOUND` | INFO | No extractable claims | ❌ | ✅ | ❌ |
| `LLM_TIMEOUT` | ERROR | LLM call timed out | ✅ 2x | ❌ | ✅ |
| `LLM_INVALID_JSON` | ERROR | LLM returned invalid JSON | ✅ 3x | ❌ | ✅ |
| `SCHEMA_MISMATCH` | ERROR | Output doesn't match schema | ✅ 1x | ❌ | ✅ |
| `LOW_CONFIDENCE` | WARN | All claims below threshold | ❌ | ❌ | ✅ |

### 7.2 Retry Policy
```python
RETRY_CONFIG = {
    "max_retries": 3,
    "backoff_base": 2.0,  # seconds
    "backoff_max": 30.0,
    "retry_on": ["PARSE_ERROR", "LLM_TIMEOUT", "LLM_INVALID_JSON"],
    "no_retry": ["NO_CLAIMS_FOUND", "LOW_CONFIDENCE"],
}
```

### 7.3 Failure Audit Events
Every failure emits:
```json
{
  "event_type": "extraction.failure",
  "document_id": "uuid",
  "chunk_id": "uuid",
  "failure_code": "LLM_TIMEOUT",
  "retry_count": 2,
  "will_retry": true,
  "timestamp": "2026-02-05T12:00:00Z"
}
```

---

## 8. Multi-Document Reconciliation

### 8.1 Order of Operations
1. Extract claims from each document independently
2. Group claims by `claim_class` + `metric_key`
3. For each group:
   - Identify primary source (highest tier)
   - Attempt reconciliation (unit/time_window normalization)
   - Flag conflicts that cannot be reconciled
4. Build Sanad chains for reconciled claims
5. Create defects for unreconciled conflicts

### 8.2 Reconciliation Heuristics
| Heuristic | Condition | Action |
|-----------|-----------|--------|
| Unit conversion | Values differ by 1000x | Convert k → full |
| Time window | FY vs LTM labeled | Reconcile if clear |
| Rounding | Within 1% | Treat as same |
| Currency | Different currencies | Convert if rates available |
| Staleness | >12 months old | Flag as stale, use newer |

### 8.3 Conflict Resolution Strategies
| Strategy | When | Result |
|----------|------|--------|
| `PREFER_PRIMARY` | Clear tier difference | Use highest tier source |
| `PREFER_RECENT` | Same tier, different dates | Use most recent |
| `HUMAN_ARBITRATION` | Cannot auto-resolve | Create human gate |
| `FLAG_BOTH` | Material conflict | Include both with defect |

---

## 9. Acceptance Criteria

### 9.1 Functional Requirements
- [ ] Extracts claims from PDF, XLSX, DOCX, PPTX documents
- [ ] Preserves source span locators for all claims
- [ ] Assigns confidence scores using defined algorithm
- [ ] Deduplicates claims within and across documents
- [ ] Detects and flags conflicts
- [ ] Emits audit events for all operations
- [ ] Respects human review thresholds

### 9.2 Quality Requirements
- [ ] ≥ 95% extraction success rate on GDBS-S dataset
- [ ] ≥ 90% claim accuracy (verified against ground truth)
- [ ] ≤ 10% false positive rate for conflicts
- [ ] ≤ 5% claims require human review escalation

### 9.3 Test Hooks
```python
# Unit tests
def test_chunking_preserves_spans()
def test_extraction_schema_compliance()
def test_confidence_calculation()
def test_deduplication_rules()
def test_conflict_detection()

# Integration tests
def test_pdf_to_claims_e2e()
def test_xlsx_to_claims_e2e()
def test_multi_doc_reconciliation()
def test_failure_retry_policy()

# GDBS tests
def test_gdbs_s_extraction_accuracy()
def test_gdbs_f_extraction_coverage()
def test_gdbs_a_conflict_detection()
```

---

## 10. Module Structure

```
src/idis/services/extraction/
├── __init__.py
├── service.py           # Main extraction service
├── chunking/
│   ├── __init__.py
│   ├── pdf_chunker.py
│   ├── xlsx_chunker.py
│   ├── docx_chunker.py
│   └── pptx_chunker.py
├── extractors/
│   ├── __init__.py
│   ├── base.py          # Abstract extractor
│   ├── claim_extractor.py
│   └── entity_extractor.py
├── resolution/
│   ├── __init__.py
│   ├── deduplicator.py
│   ├── conflict_detector.py
│   └── reconciler.py
└── confidence/
    ├── __init__.py
    └── scorer.py
```
