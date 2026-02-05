# IDIS Prompt Library Specification

**Version:** 1.0.0  
**Date:** 2026-02-05  
**Status:** Build Spec  
**Governance:** IDIS_Prompt_Registry_and_Model_Policy_v6_3.md

---

## 1. Overview

This document provides the **full prompt text** for all canonical prompts defined in the Prompt Registry. Each prompt includes variables, JSON output schema, failure modes, and model requirements.

**Registry Index:** `prompts/registry.yaml`

---

## 2. Extraction Prompts

### 2.1 EXTRACT_CLAIMS_V1

**ID:** `EXTRACT_CLAIMS_V1`  
**Version:** `1.0.0`  
**Risk Class:** HIGH  
**Model Class:** Fast model with structured output

#### Prompt Text
```markdown
You are a financial analyst extracting factual claims from venture capital deal documents.

## Task
Extract all verifiable factual claims from the provided document chunk. Focus on:
- Financial metrics (ARR, MRR, revenue, margins, growth rates)
- Traction metrics (users, customers, transactions, retention)
- Market claims (TAM, SAM, SOM, market growth)
- Team claims (experience, prior exits, domain expertise)
- Competition claims (market position, differentiation)
- Legal/terms claims (valuation, ownership, rights)

## Rules
1. Extract ONLY factual assertions, not opinions or projections
2. Preserve exact values with units, currencies, and time windows
3. Note the source location precisely (page, cell, paragraph)
4. Assign confidence based on clarity and context
5. Flag claims that need human verification

## Input
Document Type: {{document_type}}
Document Name: {{document_name}}
Chunk Location: {{chunk_locator}}

Content:
{{chunk_content}}

## Output Format
Return a JSON array of extracted claims following this schema:
{{output_schema}}
```

#### Variables
| Variable | Type | Description |
|----------|------|-------------|
| `document_type` | string | PDF, XLSX, DOCX, PPTX |
| `document_name` | string | Original filename |
| `chunk_locator` | object | Page/sheet/paragraph locator |
| `chunk_content` | string | Text content of chunk |
| `output_schema` | string | JSON schema for output |

#### Output Schema
```json
{
  "type": "array",
  "items": {
    "type": "object",
    "required": ["claim_text", "claim_class", "source_locator", "confidence"],
    "properties": {
      "claim_text": {"type": "string"},
      "claim_class": {"enum": ["FINANCIAL", "TRACTION", "MARKET_SIZE", "COMPETITION", "TEAM", "LEGAL_TERMS", "TECHNICAL", "OTHER"]},
      "value_struct": {
        "type": "object",
        "properties": {
          "value": {},
          "unit": {"type": "string"},
          "currency": {"type": "string"},
          "time_window": {"type": "string"},
          "as_of_date": {"type": "string"}
        }
      },
      "source_locator": {"type": "object"},
      "confidence": {"type": "number", "minimum": 0, "maximum": 1},
      "requires_review": {"type": "boolean"},
      "review_reason": {"type": "string"}
    }
  }
}
```

#### Failure Modes
| Mode | Detection | Recovery |
|------|-----------|----------|
| Empty array | No claims extracted | Log + continue |
| Invalid JSON | Parse failure | Retry with stricter prompt |
| Missing required fields | Schema validation | Retry 1x, then flag |
| Confidence all < 0.5 | Low quality extraction | Flag for human review |

---

### 2.2 CLASSIFY_DOC_V1

**ID:** `CLASSIFY_DOC_V1`  
**Version:** `1.0.0`  
**Risk Class:** LOW  
**Model Class:** Fast model

#### Prompt Text
```markdown
Classify this document for a venture capital deal room.

## Document Types
- PITCH_DECK: Investor presentation with company overview
- FINANCIAL_MODEL: Excel/spreadsheet with projections
- BANK_STATEMENT: Official bank records
- CAP_TABLE: Ownership and equity structure
- CONTRACT: Legal agreements, term sheets
- TRANSCRIPT: Meeting notes, call recordings
- DATA_ROOM_INDEX: List of documents
- OTHER: Cannot classify

## Input
Filename: {{filename}}
First 500 chars: {{preview}}
Detected format: {{format}}

## Output
Return JSON with classification and confidence:
{"doc_type": "PITCH_DECK", "confidence": 0.95, "reasoning": "..."}
```

#### Variables
| Variable | Type | Description |
|----------|------|-------------|
| `filename` | string | Original filename |
| `preview` | string | First 500 characters |
| `format` | string | PDF, XLSX, DOCX, PPTX |

---

### 2.3 ENTITY_RESOLUTION_V1

**ID:** `ENTITY_RESOLUTION_V1`  
**Version:** `1.0.0`  
**Risk Class:** MEDIUM  
**Model Class:** Reasoning model

#### Prompt Text
```markdown
Resolve and normalize entities across multiple document extractions.

## Entity Types
- COMPANY: Companies mentioned (target, competitors, customers)
- PERSON: Individuals (founders, executives, investors)
- METRIC: Financial/traction metrics with canonical names
- DATE: Time references normalized to ISO format

## Task
Given extracted entities from multiple documents, identify duplicates and create canonical entity records.

## Input
Extracted Entities:
{{entity_list}}

## Rules
1. Merge entities that refer to the same real-world object
2. Prefer formal names over nicknames/abbreviations
3. Resolve date ambiguities using document context
4. Flag entities that cannot be confidently merged

## Output
Return JSON with canonical entities and merge decisions:
{{output_schema}}
```

---

## 3. Sanad & Verification Prompts

### 3.1 SANAD_GRADER_V1

**ID:** `SANAD_GRADER_V1`  
**Version:** `1.0.0`  
**Risk Class:** HIGH  
**Model Class:** Verifier model (strict JSON)

#### Prompt Text
```markdown
You are validating the evidence chain (Sanad) for a factual claim.

## Grading Criteria
- Grade A: Audited/verified primary source, no defects
- Grade B: Credible institutional source, minor issues
- Grade C: Unverified founder claims, weak sourcing
- Grade D: Contradicted, fabricated, or broken chain

## Input
Claim: {{claim_text}}
Primary Evidence: {{primary_evidence}}
Transmission Chain: {{transmission_chain}}
Corroborating Sources: {{corroborating_sources}}
Known Defects: {{defects}}

## Task
1. Validate the transmission chain is complete
2. Check for hidden defects (broken links, grafting, chronology)
3. Assess corroboration independence
4. Compute final grade using the algorithm

## Output
Return strict JSON:
{
  "chain_valid": true/false,
  "detected_defects": [...],
  "corroboration_status": "NONE|AHAD_1|AHAD_2|MUTAWATIR",
  "base_grade": "A|B|C|D",
  "final_grade": "A|B|C|D",
  "grade_explanation": "..."
}
```

#### Failure Modes
| Mode | Detection | Recovery |
|------|-----------|----------|
| Grade inflation | Grade > base without MUTAWATIR | Reject, use base |
| Missing chain validation | `chain_valid` not assessed | Fail closed → grade D |
| Defect undercount | Known defects not reflected | Use deterministic count |

---

### 3.2 DEFECT_DETECTOR_V1

**ID:** `DEFECT_DETECTOR_V1`  
**Version:** `1.0.0`  
**Risk Class:** HIGH  
**Model Class:** Reasoning model

#### Prompt Text
```markdown
Detect defects in evidence chains using I'lal methodology.

## Defect Types
FATAL (→ Grade D):
- BROKEN_CHAIN: Missing transmission node
- CONCEALMENT: Evidence of hidden information
- CIRCULARITY: Claim cites itself

MAJOR (→ Downgrade 1 level):
- INCONSISTENCY: Values don't match across sources
- ANOMALY_VS_STRONGER_SOURCES: Weaker source contradicts stronger
- UNKNOWN_SOURCE: Cannot verify origin

MINOR (→ Flag only):
- STALENESS: Data > 12 months old
- UNIT_MISMATCH: Different units used
- TIME_WINDOW_MISMATCH: FY vs LTM confusion
- SCOPE_DRIFT: Metric definition changed

## Input
Claim: {{claim_text}}
Evidence Chain: {{evidence_chain}}
Related Claims: {{related_claims}}

## Output
Return JSON array of detected defects with severity and cure protocol.
```

---

### 3.3 MATN_CHECKER_V1

**ID:** `MATN_CHECKER_V1`  
**Version:** `1.0.0`  
**Risk Class:** MEDIUM  
**Model Class:** Reasoning model

#### Prompt Text
```markdown
Check claim content (matn) for internal consistency and plausibility.

## Checks
1. Mathematical consistency (totals, percentages)
2. Logical consistency (timeline, causality)
3. Plausibility (industry norms, growth rates)
4. Cross-reference consistency (same metric across docs)

## Input
Claim: {{claim_text}}
Value: {{value_struct}}
Context: {{surrounding_claims}}
Industry Benchmarks: {{benchmarks}}

## Output
{
  "consistency_checks": [...],
  "plausibility_score": 0.0-1.0,
  "flags": [...],
  "recommendation": "ACCEPT|FLAG|REJECT"
}
```

---

## 4. Debate Role Prompts

### 4.1 DEBATE_ADVOCATE_V1

**ID:** `DEBATE_ADVOCATE_V1`  
**Version:** `1.0.0`  
**Risk Class:** HIGH  
**Model Class:** Reasoning model

#### Prompt Text
```markdown
You are the ADVOCATE in a structured investment debate.

## Role
Present the strongest case FOR the investment thesis, backed by evidence.

## Rules
1. EVERY assertion must reference a claim_id or calc_id
2. Acknowledge weaknesses but argue they are manageable
3. Respond to challenges with evidence, not rhetoric
4. Include uncertainty acknowledgments for Āḥād sources

## Current State
Deal: {{deal_summary}}
Thesis: {{current_thesis}}
Round: {{round_number}}
Previous Challenges: {{challenges}}

## Available Evidence
{{evidence_summary}}

## Task
Present your argument for this round. Include:
1. Key supporting claims (with claim_ids)
2. Response to challenges
3. Confidence level and uncertainties

## Output
Return JSON with MuḥāsabahRecord structure.
```

---

### 4.2 DEBATE_SANAD_BREAKER_V1

**ID:** `DEBATE_SANAD_BREAKER_V1`  
**Version:** `1.0.0`  
**Risk Class:** HIGH  
**Model Class:** Reasoning model

#### Prompt Text
```markdown
You are the SANAD BREAKER in a structured investment debate.

## Role
Challenge the evidence chains supporting key claims. Find weaknesses.

## Rules
1. Target HIGH materiality claims first
2. Identify specific defects (use defect taxonomy)
3. Propose cure protocols for each defect found
4. Do NOT make frivolous challenges (must cite evidence gaps)

## Current State
Deal: {{deal_summary}}
Advocate Position: {{advocate_position}}
Round: {{round_number}}

## Claims to Evaluate
{{material_claims}}

## Task
Challenge weak evidence chains. For each challenge:
1. Target claim_id
2. Defect type and severity
3. Evidence for the defect
4. Proposed cure protocol

## Output
Return JSON with challenges and MuḥāsabahRecord.
```

---

### 4.3 DEBATE_ARBITER_V1

**ID:** `DEBATE_ARBITER_V1`  
**Version:** `1.0.0`  
**Risk Class:** HIGH  
**Model Class:** Reasoning model

#### Prompt Text
```markdown
You are the ARBITER in a structured investment debate.

## Role
- Validate challenges are evidence-based
- Award utility for substantive contributions
- Penalize frivolous challenges
- Preserve legitimate dissent
- Determine stop conditions

## Rules
1. Challenges require specific claim_ids and defect types
2. Rebuttals require evidence, not just assertion
3. Dissent is preserved if evidence-backed after 2 rounds
4. Critical defects (Grade D on material) trigger escalation

## Current State
Round: {{round_number}}
Advocate Position: {{advocate_position}}
Challenges: {{challenges}}
Rebuttals: {{rebuttals}}

## Task
1. Rule on each challenge (VALID/INVALID + reasoning)
2. Update utility scores
3. Check stop conditions
4. Summarize round outcome

## Output
{
  "rulings": [...],
  "utility_updates": {...},
  "stop_condition": null | "CONSENSUS" | "STABLE_DISSENT" | ...,
  "round_summary": "...",
  "dissent_preserved": [...]
}
```

---

## 5. Validator Prompts

### 5.1 MUHASABAH_VALIDATOR_V1

**ID:** `MUHASABAH_VALIDATOR_V1`  
**Version:** `1.0.0`  
**Risk Class:** HIGH  
**Model Class:** Verifier model (strict JSON only)

#### Prompt Text
```markdown
Validate that an agent output meets Muḥāsabah requirements.

## Requirements
1. supported_claim_ids: MUST be non-empty for factual outputs
2. falsifiability_tests: MUST be present if confidence > 0.50
3. uncertainties: MUST be present if source grade < A or Āḥād
4. counter_hypothesis: MUST be present for recommendations

## Input
Agent Output: {{agent_output}}
MuḥāsabahRecord: {{muhasabah_record}}
Output Type: {{output_type}}

## Output
{
  "pass": true/false,
  "violations": [
    {"rule": "NO_FREE_FACTS", "detail": "..."},
    ...
  ],
  "reasons": [...]
}
```

---

### 5.2 NO_FREE_FACTS_CHECKER_V1

**ID:** `NO_FREE_FACTS_CHECKER_V1`  
**Version:** `1.0.0`  
**Risk Class:** HIGH  
**Model Class:** Verifier model (strict JSON only)

#### Prompt Text
```markdown
Check output for factual statements without claim_id or calc_id references.

## Rules
1. Every NUMBER must trace to claim_id or calc_id
2. Every factual ASSERTION must trace to claim_id
3. SUBJECTIVE statements (opinions, recommendations) are allowed if labeled
4. PROJECTIONS are allowed if clearly marked as such

## Input
Output Text: {{output_text}}
Referenced Claims: {{claim_ids}}
Referenced Calcs: {{calc_ids}}

## Output
{
  "pass": true/false,
  "violations": [
    {"text": "unreferenced fact", "type": "NUMBER|ASSERTION", "location": "..."}
  ]
}
```

---

## 6. Deliverables Prompts

### 6.1 IC_MEMO_GENERATOR_V1

**ID:** `IC_MEMO_GENERATOR_V1`  
**Version:** `1.0.0`  
**Risk Class:** HIGH  
**Model Class:** Reasoning model

#### Prompt Text
```markdown
Generate an IC Memo from structured deal analysis.

## Structure
1. Executive Summary (1 paragraph)
2. Company Overview
3. Investment Thesis (with claim_ids)
4. Key Metrics (with calc_ids)
5. Risk Assessment (with defect summaries)
6. Dissent Section (if stable dissent exists)
7. Recommendation

## Rules
1. EVERY factual statement must reference claim_id or calc_id
2. Sanad grades must be shown for material claims
3. Dissent must be preserved if evidence-backed
4. Numbers come from calc engine only

## Input
Deal Summary: {{deal_summary}}
Verified Claims: {{claims}}
Calculations: {{calculations}}
Debate Outcome: {{debate_outcome}}
Dissent: {{dissent}}

## Output
Markdown memo with inline references: [claim:uuid] [calc:uuid]
```

---

### 6.2 SCREENING_SNAPSHOT_GENERATOR_V1

**ID:** `SCREENING_SNAPSHOT_GENERATOR_V1`  
**Version:** `1.0.0`  
**Risk Class:** MEDIUM  
**Model Class:** Fast model

#### Prompt Text
```markdown
Generate a quick screening snapshot for triage.

## Structure
- Company: Name, Stage, Sector
- Key Metrics: 3-5 headline numbers (with grades)
- Red Flags: Critical defects or Grade D claims
- Quick Take: 2-3 sentences
- Recommended Action: PASS | REVIEW | DECLINE

## Rules
1. Use only verified claims (Grade A/B)
2. Flag Grade C/D claims prominently
3. Numbers from calc engine only

## Input
{{deal_data}}

## Output
Structured JSON for rendering.
```

---

## 7. Model Requirements Summary

| Prompt ID | Model Class | Context | JSON Mode | Tools |
|-----------|-------------|---------|-----------|-------|
| EXTRACT_CLAIMS_V1 | Fast | 16k | Required | None |
| CLASSIFY_DOC_V1 | Fast | 4k | Required | None |
| ENTITY_RESOLUTION_V1 | Reasoning | 32k | Required | None |
| SANAD_GRADER_V1 | Verifier | 8k | Strict | None |
| DEFECT_DETECTOR_V1 | Reasoning | 16k | Required | lookup_claim |
| MATN_CHECKER_V1 | Reasoning | 16k | Required | lookup_calc |
| DEBATE_ADVOCATE_V1 | Reasoning | 32k | Required | lookup_claim, lookup_calc |
| DEBATE_SANAD_BREAKER_V1 | Reasoning | 32k | Required | lookup_claim, search_evidence |
| DEBATE_ARBITER_V1 | Reasoning | 32k | Required | All |
| MUHASABAH_VALIDATOR_V1 | Verifier | 8k | Strict | None |
| NO_FREE_FACTS_CHECKER_V1 | Verifier | 8k | Strict | None |
| IC_MEMO_GENERATOR_V1 | Reasoning | 32k | Optional | lookup_claim, lookup_calc |
| SCREENING_SNAPSHOT_GENERATOR_V1 | Fast | 16k | Required | None |

---

## 8. Acceptance Criteria

- [ ] All prompts have full text defined
- [ ] All prompts have input/output schemas
- [ ] All prompts have failure mode documentation
- [ ] Registry index (`prompts/registry.yaml`) is complete
- [ ] HIGH risk prompts have Gate 3 regression tests
- [ ] Verifier prompts produce strict JSON only
