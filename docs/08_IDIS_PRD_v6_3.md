# IDIS Product Requirements Document (PRD) — VC Edition v6.3

**Source**: IDIS VC Edition v6.3 (January 2026)  
**Purpose**: Define product requirements, user journeys, and measurable outcomes for the enterprise-grade IDIS platform.

---

## 1. Product Vision

IDIS is the **institutional-grade AI investment analyst layer** for venture capital, designed to:
- evaluate 10x more deals,
- reduce analyst hours materially,
- improve decision quality,
- and produce auditable, evidence-governed outputs suitable for IC scrutiny.

---

## 2. Target Users and Personas

1. **Associate / Analyst**
   - Needs fast screening, structured data extraction, and evidence-backed answers
2. **Principal / Partner**
   - Needs trustworthy summaries, red flags, and conviction drivers with traceability
3. **Investment Committee (IC)**
   - Needs auditability, conflict resolution, dissent visibility, and defensible reasoning
4. **Platform / Ops**
   - Needs ingestion automation, integrations, permissions, and workflow tracking
5. **Compliance / Risk**
   - Needs controls: evidence chain, audit artifacts, RBAC, retention, drift monitoring

---

## 3. Primary User Journeys

### 3.1 Deal Intake → Screening

1. Create deal record (company/stage/sector)
2. Ingest artifacts (upload or connector)
3. System parses and extracts claims
4. Truth Dashboard displays:
   - verified claims,
   - contradictions,
   - missing data,
   - subjective assertions
5. Screening Snapshot generated with:
   - key metrics + grade labels,
   - red flags,
   - missing info requests,
   - next-step recommendation

### 3.2 Deep Dive → IC Memo Pack

1. Analyst requests “Full Memo”
2. System runs:
   - enrichment connectors,
   - deterministic engines,
   - multi-agent debate (LangGraph),
   - Muḥāsabah gates
3. IC pack generated with:
   - evidence-linked sections,
   - scenario/sensitivity tables,
   - dissent section (if stable dissent)
4. Partner reviews and approves export

### 3.3 Verification Workflow

1. System flags grade D claim in material position → “Critical Defect”
2. Analyst sees cure protocol:
   - request documents,
   - re-audit,
   - reconstruct chain,
   - discard claim
3. Analyst uploads additional evidence or waives with reason (compliance logged)
4. System recomputes grades and updates deliverables

---

## 4. Functional Requirements

### 4.1 Ingestion & Parsing

- Support upload + at least one enterprise connector
- Parse PDF + XLSX with stable span locators
- Version control for artifacts (hash + timestamps)

### 4.2 Claim Registry

- Extract claims with typed value structs (unit/currency/time window)
- Each claim links to at least one span OR is marked UNKNOWN/SUBJECTIVE
- Claims are stable IDs within a run; support deterministic mapping across runs

### 4.3 Sanad Trust Framework

- EvidenceItem grading (A/B/C/D) + optional subgrade
- Transmission chain and corroboration status
- Defect schema + severity rules
- Claim Sanad Grade computed by normative algorithm
- Independence test for Mutawātir (≥3 independent sources)

### 4.4 Truth Dashboard

- Verdicts: VERIFIED / CONTRADICTED / UNVERIFIED / SUBJECTIVE / UNKNOWN
- Deterministic validators:
  - unit mismatch, time-window mismatch, ratio identity checks
- Contradiction reconciliation attempts (explain both values and why)

### 4.5 Deterministic Engines

- Deterministic calculations for all numeric outputs in scoring and deliverables
- Calc-Sanad with reproducibility hashes
- Extraction confidence gate before using claims as calc inputs

### 4.6 Debate + Muḥāsabah

- Implement debate node graph per v6.3
- Stop conditions (priority) and max rounds
- Utility scoring with proper scoring rule (Brier) and penalties
- MuḥāsabahRecord required per agent output; deterministic validator enforcement
- Dissent preserved when stable dissent

### 4.7 Deliverables

- Screening Snapshot (1 page)
- IC Memo Pack (10–20 pages)
- Q&A list (for management call)
- Exports to PDF/DOCX/PPTX/XLSX
- All factual outputs include claim_id/calc_id references

### 4.8 Governance & QA

- Metrics dashboards:
  - Sanad coverage, grade distribution, defect rates
  - Muḥāsabah pass/reject reasons
  - No-Free-Facts violation counts
- Config versioning and rollback
- Sampling audits (weekly) for Muḥāsabah calibration

---

## 5. Non-Functional Requirements

- Multi-tenant isolation
- RBAC + SSO + MFA
- Encryption at rest/in transit
- Auditability and retention controls
- Target latency:
  - screening snapshot: minutes
  - full memo: under an hour of system compute (excluding human verification)

---

## 6. KPIs / Success Metrics

- Screening throughput: deals/week per analyst
- Analyst hours per screened deal
- Citation coverage rate (>=95%)
- Deterministic numeric provenance rate (100%)
- Red flag precision/recall (baseline + improvement)
- Muḥāsabah rejection rate trend (downward while quality holds)
- Partner satisfaction: time-to-confidence in decision

---

## 7. Risks

- Hallucinated facts → mitigate with No-Free-Facts + Muḥāsabah validators
- Overconfidence → mitigate with Muḥāsabah required uncertainties + Brier scoring
- Evidence gaps → mitigate with explicit missing-info outputs
- Compliance concerns → mitigate with audit artifacts + BYOK + data residency

---

## 8. Release Criteria (Pilot)

A “pilot-ready” release MUST:
- generate a screening snapshot with evidence links for every factual claim
- block grade D material claims (critical defect) and route to human verification
- generate deterministic calc outputs with reproducibility hash
- run debate with stop conditions and produce dissent section when applicable
- record Muḥāsabah logs and show them in UI

