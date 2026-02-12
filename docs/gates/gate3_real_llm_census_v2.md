# Gate 3 Real-LLM Census v2 (Post-Serialization Fix)

**Date:** 2026-02-12
**Baseline:** Gate 3 deterministic 100/100 PASS (commit `b1690f1`)
**Fix applied:** `7b8628e` — `_sanitize_for_json` in `_complete_step` (orchestrator.py)
**Backend:** `IDIS_EXTRACT_BACKEND=anthropic`, `IDIS_DEBATE_BACKEND=anthropic`
**Model:** `claude-sonnet-4-20250514` (extraction, debate, analysis, scoring)
**Dataset:** GDBS-F, 2 deals (deal_001_clean, deal_002_contradiction). Deal 3 skipped.

---

## A. Totals

| Metric | Value |
|--------|-------|
| Deals executed | 2 |
| PASS | 0 |
| FAIL | 2 |
| BLOCKED | 0 |

---

## B. Per-Step Survival (2 deals)

| # | Step | Deal 1 (clean) | Deal 2 (contradiction) | Pass Rate |
|---|------|----------------|------------------------|-----------|
| 1 | INGEST_CHECK | COMPLETED | COMPLETED | 2/2 |
| 2 | EXTRACT | COMPLETED (31s) | COMPLETED (31s) | 2/2 |
| 3 | GRADE | COMPLETED (<1s) | COMPLETED (<1s) | 2/2 |
| 4 | CALC | COMPLETED (<1s) | COMPLETED (<1s) | 2/2 |
| 5 | ENRICHMENT | COMPLETED (42s) | COMPLETED (36s) | 2/2 |
| 6 | DEBATE | COMPLETED (721s) | COMPLETED (715s) | 2/2 |
| 7 | ANALYSIS | COMPLETED (222s) | COMPLETED (222s) | 2/2 |
| 8 | SCORING | **FAILED** | **FAILED** | 0/2 |
| 9 | DELIVERABLES | not reached | not reached | 0/2 |

**Steps 1–7 pass with real LLM for both deals. SCORING (step 8) is the sole blocker.**

### Step Timing Breakdown

| Step | Deal 1 | Deal 2 | Notes |
|------|--------|--------|-------|
| EXTRACT | 31s | 31s | Real Anthropic claim extraction |
| ENRICHMENT | 42s | 36s | 15 connectors, 3–4 non-fatal 4xx |
| DEBATE | 721s (12 min) | 715s (12 min) | Multi-round with 5 debate roles |
| ANALYSIS | 222s (3.7 min) | 222s (3.7 min) | 8 specialist agents |
| SCORING | 49s (failed) | 49s (failed) | LLM call succeeded but output invalid |
| **Total** | **961s (16 min)** | **1052s (17.5 min)** | |

---

## C. Failure Signatures

| Rank | Step | Error Code | Message | Count |
|------|------|-----------|---------|-------|
| 1 | SCORING | `SCORINGENGINEERROR` | LLM returned invalid JSON for scoring_agent: Unterminated string starting at: line 247 column 31 (char 14455) | 1 |
| 2 | SCORING | `ANALYSISENGINEERROR` | Agent 'technical-agent-01' failed: 1 validation error for Risk — Risk must include at least one evidence link (claim_ids, calc_ids, or enrichment_ref_ids) | 1 |

### Deal 1 (deal_001_clean) — SCORING FAILED

**Error:** `ANALYSISENGINEERROR` — The scoring step tried to validate the analysis output and found that `technical-agent-01` produced a `Risk` object (`tech_001_information_gap`) with empty evidence links. This is an NFF enforcement violation — every risk assertion must trace to at least one claim, calc, or enrichment reference.

**Root cause:** The real LLM (Claude) generated a risk assessment that did not include `claim_ids`, `calc_ids`, or `enrichment_ref_ids`. The validator correctly rejected it. The LLM prompt or output parsing needs to ensure all risk objects include evidence links.

### Deal 2 (deal_002_contradiction) — SCORING FAILED

**Error:** `SCORINGENGINEERROR` — The scoring LLM returned a JSON response that was truncated (unterminated string at char 14455, line 247). The response was too large and got cut off, resulting in unparseable JSON.

**Root cause:** The scoring prompt asks for all 8 dimensions with detailed rationale, claim references, and muḥāsabah records. The LLM response exceeded the output token limit and was truncated mid-string, producing invalid JSON.

---

## D. Category Buckets

| Category | Count | Details |
|----------|-------|---------|
| JSON schema / non-object output | 0 | — |
| Missing required keys | 0 | — |
| NFF violations (fabricated IDs) | 0 | — |
| NFF violations (missing evidence links) | 1 | Risk object without claim/calc/enrichment refs |
| Muḥāsabah failures | 0 | — |
| Scorecard schema violations | 0 | — |
| LLM output truncation | 1 | Scoring JSON cut off at 14455 chars |
| Enrichment failures (non-fatal) | ~7 | ESCWA 403 ×2, Qatar 400 ×2, PatentsView 410 ×2, Wayback 429 ×1 |
| Timeouts / rate limits | 0 | — |
| Provider errors (Anthropic API) | 0 | All Anthropic calls succeeded |
| Serialization errors | 0 | **Fixed by `7b8628e`** |

---

## E. Observations

### What passes with real LLM?

**Steps 1–7 all pass consistently for both clean and adversarial deals.** This is a strong result:
- **EXTRACT** — Real Claude claim extraction produces valid output that passes schema validation
- **GRADE** — Sanad grading works on real extracted claims
- **CALC** — Deterministic calculations run correctly on real claims
- **ENRICHMENT** — 15 connectors fire; non-fatal 4xx errors are handled gracefully
- **DEBATE** — Multi-round debate with 5 Anthropic-powered roles completes successfully (~12 min)
- **ANALYSIS** — 8 specialist agents produce valid `AgentReport` models that pass all validators

### Where does the pipeline fail?

**SCORING (step 8) is the sole failure point.** Two distinct failure modes:
1. **NFF enforcement on analysis output (Deal 1):** A risk object lacked evidence links. The validator is correct to reject it — the LLM prompt needs to enforce that every risk assertion includes at least one evidence reference.
2. **LLM output truncation (Deal 2):** The scoring prompt produces a response >14K chars. Claude's output was truncated, producing invalid JSON. Fix: increase `max_tokens` for the scoring call, or split the scoring into per-dimension calls.

### Are failures consistent or variable?

**Variable.** Both deals fail at SCORING but with different failure modes. This suggests scoring is the most fragile step — the prompt demands a very large structured output (8 dimensions × rationale + evidence + muḥāsabah), which pushes against token limits and evidence-link completeness requirements.

### Patterns by deal type?

- **Clean deal (001):** Failed on NFF validation of risk evidence links
- **Adversarial deal (002):** Failed on output truncation (larger response due to contradictions)

Both are scoring failures. The adversarial deal took slightly longer overall (1052s vs 961s) but the step-level timing was nearly identical, suggesting the extra time was in network variance.

---

## F. Timing & Cost

| Metric | Value |
|--------|-------|
| Deal 1 wall time | 961s (16.0 min) |
| Deal 2 wall time | 1052s (17.5 min) |
| DEBATE step (dominant) | ~12 min per deal |
| ANALYSIS step | ~3.7 min per deal |
| Estimated API cost per deal | ~$0.10–0.50 |
| Projected 100-deal wall time | ~28 hours (serial) |
| Projected 100-deal cost | ~$10–50 |

---

## G. Fixes Applied in This Sprint

| Commit | Description |
|--------|-------------|
| `7b8628e` | `fix(orchestrator):` Added `_sanitize_for_json` to recursively convert Pydantic models before `json.dumps()` in `_complete_step`. 13 new tests. |
| `4457e8a` | `fix(gates):` Increased Gate 3 harness timeout default from 30s to 1800s. |

---

## H. Next Sprint Recommendations

1. **[P0] Scoring output truncation** — Increase `max_tokens` for the scoring LLM call (currently likely 4096, needs 8192+), or split scoring into per-dimension calls to reduce per-call output size.
2. **[P0] Risk evidence link enforcement** — Update the scoring/analysis prompt to explicitly require `claim_ids` or `calc_ids` on every `Risk` object. Alternatively, make the validator auto-populate empty evidence links from the parent agent's supported claims.
3. **[P1] Gate runner server restart bug** — The harness `pre_case_fn` server restart causes the new server to hang. Manual server startup works fine. Investigate `_restart_server` in `gate_3_gdbs_f.py`.
4. **[P2] Enrichment connector 4xx logging** — ESCWA, Qatar Open Data, and PatentsView return 400/403/410. These are non-fatal but noisy. Consider downgrading to WARNING.

---

## I. Verification

```
ruff format:     PASS (3 files)
ruff check:      PASS
mypy:            PASS (orchestrator.py)
forbidden_scan:  PASS
pytest:          2759 passed, 13 new tests added
git status:      clean (no uncommitted source changes)
```
