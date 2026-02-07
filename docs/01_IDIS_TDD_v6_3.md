# IDIS Technical Design Document (TDD) — VC Edition (Derived from v6.3)

**Source**: *IDIS — Institutional Deal Intelligence System (Venture Capital Edition), Version 6.3 (January 2026)*  
**Generated**: 2026-01-06  
**Audience**: Engineering (backend, ML/AI, data), security/compliance, product, and implementation teams  
**Purpose**: Convert the v6.3 platform spec into an enterprise-grade technical blueprint that an AI coder (and human engineers) can implement with minimal ambiguity.

---

## 1. System Summary

IDIS (Institutional Deal Intelligence System) is an **AI Investment Analyst Layer** for Venture Capital. It ingests heterogeneous deal materials (pitch deck, exec memo, data room files, transcripts), enriches them with external intelligence, executes deterministic financial calculations, and produces **ranked, explainable, IC-ready** outputs.

### 1.1 Non-Negotiable Invariants (Must Hold System-Wide)

1. **Zero Numerical Hallucination**
   - All numeric outputs that influence scoring, routing, and deliverables MUST come from deterministic engines (auditable code), not LLM arithmetic.
2. **Evidence Governance (No-Free-Facts)**
   - Any factual statement MUST map to a `claim_id` (with a Sanad chain) or `calc_id` (with deterministic provenance).
3. **Sanad Trust Framework**
   - Every material claim MUST carry a computed **Claim Sanad Grade** (A/B/C/D), plus structured defects where present.
4. **Muḥāsabah Gate**
   - Every agent output MUST include a valid `MuḥāsabahRecord`; invalid outputs are rejected (hard gate).
5. **Debate Protocol (Game-Theoretic)**
   - Multi-agent debate MUST be implemented as a state machine with explicit stop conditions; dissent MUST be preserved when evidence-backed.
6. **Auditability**
   - Every run MUST produce immutable audit artifacts: document versions, extraction logs, evidence refs, Sanad chains, defects, debate transcripts, Muḥāsabah logs, calculation inputs/outputs.

---

## 2. Scope

### 2.1 In Scope (v6.3 Core)

- Deal ingestion (documents + metadata)
- Extraction of structured data and claims into a **Claim Registry**
- Truth Dashboard generation (verification/contradiction/missingness)
- Sanad Trust Framework:
  - EvidenceItem grading, claim-level grading algorithm, defect taxonomy, corroboration independence
- Deterministic calculation engines (“Zero-Hallucination Zone”)
- Multi-agent analysis engine (specialist roles)
- Game-theoretic debate orchestration + Muḥāsabah gate
- Deliverables generator (screening snapshot, memo, Q&A, etc.)
- Governance & QA metrics, drift monitoring, human approval gates
- Security & compliance controls (RBAC, SSO, encryption, retention, BYOK, audit logs)

### 2.2 Explicit Non-Goals

- Replacing human judgment, IC decision-making, founder meetings, negotiation, or legal counsel
- Deep technical DD (e.g., full code audit, pen test) beyond surface architecture review
- Re-selling proprietary third-party enrichment data beyond BYOL agreements
- Autonomously sending communications (e.g., decline emails) without explicit human approval

---

## 3. Architecture Overview

IDIS is a **six-layer architecture** with cross-cutting “trust middleware” (Sanad + Muḥāsabah + Debate).

### 3.1 High-Level Layers (Logical)

1. **Deal Object Store (Ingestion + Canonical Model)**
2. **Knowledge Enrichment & Contextualization**
3. **Deterministic Calculation Engines**
4. **Multi-Agent Analysis Engine (LangGraph-Orchestrated)**
5. **Deliverables Generator**
6. **Feedback & Learning Loop**

### 3.2 Cross-Cutting Trust Middleware

- **Sanad Trust Framework** (evidence chain + grading + defects)
- **Truth Dashboard** (matn checks + contradiction detection)
- **Muḥāsabah** (self-accounting + validator)
- **Game-Theoretic Debate** (roles, incentives, stop conditions, dissent preservation)
- **No-Free-Facts Enforcement** (tool wrapper + output parser)

### 3.3 Reference Data Flow

```mermaid
flowchart LR
  A[Ingest Deal Artifacts] --> B[Extract Claims + Entities]
  B --> C[Claim Registry]
  C --> D[Build Sanad Graph + Grade EvidenceItems]
  D --> E[Truth Dashboard: Verify / Contradict / Missing]
  C --> F[Deterministic Engines (calc_id)]
  F --> C
  D --> G[Multi-Agent Debate (LangGraph)]
  E --> G
  F --> G
  G --> H[Muḥāsabah Gate + No-Free-Facts Validator]
  H --> I[Deliverables Generator]
  I --> J[Human Approval Gates]
  J --> K[Exports + CRM Sync]
  J --> L[Outcome Tracking]
  L --> M[Feedback & Learning Loop]
```

---

## 4. Core Data Contracts (Normative)

These objects MUST be implemented exactly (field names may be adjusted to language conventions, but semantics must remain invariant).

### 4.1 Claim (Conceptual)

A **Claim** is the atomic unit of verifiable information, used by:
- Truth Dashboard,
- Sanad grading,
- debate references,
- deliverables linking,
- audit artifacts.

Minimum required fields:
- `claim_id` (UUID)
- `deal_id` (UUID)
- `claim_text` (string)
- `claim_type` (enum; e.g., FINANCIAL_METRIC, MARKET_SIZE, COMPETITION, TEAM, TRACTION, LEGAL, TECH)
- `value_struct` (typed value; number/string/range; include unit/currency/time_window when applicable)
- `materiality` (enum LOW/MEDIUM/HIGH, or numeric 0–1)
- `source_refs` (list of document spans and/or upstream claim refs)
- `sanad_ref` (link to Sanad object)
- `status` (enum: VERIFIED / CONTRADICTED / INFLATED / UNVERIFIED / SUBJECTIVE / UNKNOWN)

### 4.2 Sanad (Claim-Level)

A **Sanad** is the explicit evidence chain proving a claim’s provenance (including intermediate transformations).

Minimum required fields (normative per v6.3):
- `claim_id`
- `primary_source` (SourceRef)
- `transmission_chain` (list[TransmissionNode])
- `corroborating_sources` (list[SourceRef])
- `extractor_agent` (agent_id, model_id, version)
- `extraction_confidence` (0–1)
- `dhabt_score` (historical precision score for extractor)
- `corroboration_status` (NONE, AHAD_1, AHAD_2, MUTAWATIR)
- `defects` (list[Defect])
- `sanad_grade` (A/B/C/D) + explanation

### 4.3 Defect (ʿIlal-Inspired) — Normative Object Schema

A **Defect** represents a structured fault in the chain or content integrity.

- `defect_type` MUST include at least:
  - `BROKEN_CHAIN`, `MISSING_LINK`, `UNKNOWN_SOURCE`, `CONCEALMENT`, `INCONSISTENCY`,
    `ANOMALY_VS_STRONGER_SOURCES`, `CHRONO_IMPOSSIBLE`, `CHAIN_GRAFTING`, `CIRCULARITY`,
    `STALENESS`, `UNIT_MISMATCH`, `TIME_WINDOW_MISMATCH`, `SCOPE_DRIFT`, `IMPLAUSIBILITY`

Severity rules (normative):
- **FATAL**: BROKEN_CHAIN, CONCEALMENT, CIRCULARITY → forces claim grade to **D**
- **MAJOR**: INCONSISTENCY, ANOMALY_VS_STRONGER_SOURCES, UNKNOWN_SOURCE → downgrade one level per defect
- **MINOR**: STALENESS, UNIT_MISMATCH, TIME_WINDOW_MISMATCH, SCOPE_DRIFT → flag; no automatic downgrade

### 4.4 MuḥāsabahRecord — Normative Output Contract

Every agent output MUST attach a MuḥāsabahRecord:

- `supported_claim_ids` MUST be non-empty for factual outputs
- `falsifiability_tests` MUST be present for recommendation-affecting claims (materiality gate)
- `uncertainties` MUST be present for Āḥād corroboration and/or source grade < A

Validator rules (normative):
- Reject if **No-Free-Facts**: factual assertions present but `supported_claim_ids` empty
- Reject if **Overconfidence**: confidence > 0.80 AND `uncertainties` empty
- Reject if **Falsifiability Missing**: confidence > 0.50 AND `falsifiability_tests` empty

---

## 5. Key Algorithms (Normative)

### 5.1 Claim Sanad Grade Algorithm (A/B/C/D)

Inputs:
- `transmission_chain`: list[TransmissionNode] each with `source_grade` ∈ {A,B,C,D}
- `corroboration_status`: {NONE, AHAD_1, AHAD_2, MUTAWATIR}
- `defects`: list[Defect] with severity {FATAL, MAJOR, MINOR}

Algorithm (normative):
1. `base_grade = MIN(source_grade for node in transmission_chain)`
2. If any **FATAL** defect → return **D**
3. `major_count = count(defects where severity == MAJOR)`
4. `grade = downgrade(base_grade, steps=major_count)` (A→B→C→D)
5. If `grade == B` and `corroboration_status == MUTAWATIR` and `major_count == 0` → upgrade to **A**
6. If `grade == C` and `corroboration_status == MUTAWATIR` and `major_count == 0` → upgrade to **B**
7. Constraint: cannot upgrade beyond **A**; corroboration cannot cure FATAL/MAJOR defects

Worked examples (normative):
- min=B, MUTAWATIR, none → A
- min=A, AHAD_1, MAJOR(INCONSISTENCY) → B
- min=B, MUTAWATIR, FATAL(BROKEN_CHAIN) → D

### 5.2 Independence Rules for Corroboration (Mutawātir)

Two sources are independent iff:
- Generated by different systems (e.g., Stripe export vs. bank statement)
- No shared human preparer in chain
- Timestamp evidence suggests independent creation (not copy/paste)
- Structure/format differences suggest independent derivation
- `upstream_origin_id` differs (hard rule)

Mutawātir threshold (normative):
- ≥3 independent sources → MUTAWATIR

---

## 6. Two-Layer Debate Architecture

IDIS employs two distinct debate layers with separate purposes, inputs, and outputs:

**Layer 1 — Evidence Trust Debate (Phase 6):**
- **Inputs:** extracted claims + Sanad grades + calc outputs (with provenance)
- **Output:** **Validated Evidence Package** — trusted claims + defects + contradictions + dissent
- Always required and stage-agnostic. Runs on every deal regardless of investment stage or enrichment availability.

**Layer 2 — Investment Committee (Future / post-Phase 7.C dependency):**
- **Inputs:** Validated Evidence Package + enrichment connectors + stage priors/weights
- **Output:** **IC-Ready Package** — GO / CONDITIONAL / NO-GO + rationale + questions
- Stage-dependent and requires enrichment/context beyond the dataset. Not implemented until enrichment connectors (Phase 7.C) are operational.

**Deliverable Boundaries:**
- **Validated Evidence Package:** The deliverable boundary between Layer 1 and Layer 2. Contains all claims that survived the evidence trust debate, with their final Sanad grades, identified defects, contradictions, and preserved dissent records. This package is self-contained and sufficient for screening snapshots and evidence-only IC memos.
- **IC-Ready Package:** The future deliverable produced by Layer 2. Combines the Validated Evidence Package with domain specialist analysis to produce an investment recommendation with full rationale, counter-hypotheses, and open questions for IC discussion.

### 6.1 Layer 1: Evidence Trust Court (Current — Phase 6)

#### 6.1.1 Roles (Minimum Set)

- Advocate (proposes best-supported thesis)
- Sanad Breaker (attacks weak chains, missing links)
- Contradiction Finder (cross-doc inconsistencies; reconciliation attempts)
- Risk Officer (downside, fraud, regulatory risk)
- Arbiter (rules enforcement; validates challenges; assigns utility; preserves dissent)

Layer 1 enforces **Muḥāsabah** and **No-Free-Facts** at every agent output. Its sole concern is evidence integrity and provenance — it does not perform domain-specialist investment analysis.

#### 6.1.2 Normative Node Graph

```text
START -> advocate_opening()
      -> sanad_breaker_challenge()
      -> observer_critiques_parallel()  [fan-out/fan-in]
      -> advocate_rebuttal()
      -> evidence_call_retrieval()      [conditional]
      -> arbiter_close()
      -> stop_condition_check()         [LOOP or EXIT]
      -> muhasabah_validate_all()       [HARD GATE]
      -> finalize_outputs() -> END
```

#### 6.1.3 Stop Conditions (Normative)

Priority order:
1. CRITICAL_DEFECT: any claim has grade D in a **material position** → escalate to human
2. MAX_ROUNDS: round_number ≥ 5
3. CONSENSUS: confidence spread ≤ 0.10
4. STABLE_DISSENT: positions unchanged for 2 rounds → preserve dissent
5. EVIDENCE_EXHAUSTED: no new evidence available

#### 6.1.4 Incentive Alignment (Utility)

Utility design (normative principles):
- Reward calibrated probabilities using **Brier score**
- Apply penalties when contradicted within the run or later verified enrichment
- Materiality gate: utility only awarded if it changes recommendation/score band/red flag

No-Free-Facts enforcement points (normative):
- Tool wrapper/output parser rejects factual statements without claim_id/calc_id references
- Muḥāsabah validator rejects empty `supported_claim_ids` when facts are present

### 6.2 Layer 2: Multi-Agent Analysis Engine — Specialist Agents (Future)

Layer 2 is the **Investment Committee** mode. It consumes the Validated Evidence Package produced by Layer 1 and applies domain-specialist analysis to produce an IC-Ready Package.

**Specialist agent set (future):**
- Financial Agent — unit economics, revenue model, burn rate analysis
- Market Agent — TAM/SAM/SOM validation, competitive landscape
- Technical Agent — architecture review, technical risk assessment
- Terms Agent — term sheet analysis, governance, liquidation preferences
- Team Agent — founder track record, team composition gaps
- Historian Agent — comparable deal outcomes, vintage analysis
- Sector Specialist Agent — vertical-specific domain expertise

**IC mechanism roles (future):**
- IC Advocate (thesis construction from Validated Evidence Package)
- IC Challenger (stress-tests thesis with counter-evidence)
- IC Arbiter (synthesizes GO / CONDITIONAL / NO-GO with rationale)
- IC Risk Officer (if distinct from Layer 1 Risk Officer — focuses on portfolio-level and strategic risk)

**Stage-specific weighting packs** (future config, not implemented):
- Pre-seed / Seed / Series A / Series B+ weight profiles that adjust specialist agent influence on the final IC recommendation.

**Dependencies:**
- Requires Phase 7.C enrichment connectors for external data inputs.
- Consumes Layer 1 Validated Evidence Package as its trust-verified input.
- No new phase numbers — Layer 2 is future work beyond the current Phase 7 gate.

---

## 7. Service Decomposition (Recommended Implementation)

This section is “engineering recommendation” to implement the v6.3 spec cleanly.

### 7.1 Core Services / Modules

1. **ingestion-service**
   - Upload/connect data rooms (DocSend/Drive/Dropbox/SharePoint)
   - Extract text + structure (PDF/PPT/XLSX)
   - Persist raw artifacts (object storage) + metadata (Postgres)

2. **extraction-service**
   - Entity resolution (company/founders/products)
   - Claim extraction into Claim Registry
   - Source span linking (page/paragraph/cell/timecode)
   - Output: Claim objects + EvidenceItems

3. **sanad-service**
   - Construct Sanad chains + transmission nodes
   - Compute grades + defects
   - Manage independence and corroboration computation
   - Persist Sanad graph (graph DB) + relational summaries

4. **calc-engine-service**
   - Deterministic calculations (Python)
   - Calc-Sanad: inputs, formula_hash, code_version, output, reproducibility
   - Enforce extraction confidence gate (≥0.95) or require human verification

5. **enrichment-service**
   - BYOL connectors: PitchBook/Crunchbase/IDC/etc.
   - Cache + lineage + conflict display

6. **debate-orchestrator**
   - LangGraph state machine execution
   - Agent roles + prompts + tools
   - Utility scoring + stop conditions + dissent policy
   - Muḥāsabah validation hard gate

7. **deliverables-service**
   - Produce IC-ready memo packs, snapshots, Q&A, models, exports

8. **governance-service**
   - Drift monitoring, QA dashboards, audit artifact retention
   - Human approval workflow / overrides

### 7.2 Storage (Recommended)

- Object storage: raw docs + extracted artifacts
- PostgreSQL: canonical deal objects, claims, audit logs, configs
- Vector DB: semantic search over documents and prior deals
- Graph DB: Sanad graph + knowledge graph relationships
- Event bus (Kafka/Redpanda): pipeline events, audit stream
- OLAP/warehouse: analytics, KPIs, drift reports

---

## 8. Security & Compliance Requirements (Normative + Practical)

### 8.1 Security Controls

- TLS 1.3 in transit; AES-256 at rest
- SSO (Okta/Azure AD) + MFA
- RBAC roles: Analyst, Partner, IC, Compliance, Admin
- Tenant isolation: logical and (optionally) physical (VPC / DB schema)
- BYOK / client-managed KMS keys (enterprise tier)
- Audit logs: immutable, queryable, exportable

### 8.2 Data Handling

- No model training on client data (enforced via provider settings + contracts)
- Configurable retention; legal hold support
- Data residency controls (including GCC-compliant regions)
- “Restricted” data classification for raw deal documents; strict access

---

## 9. Observability, QA, and Metrics

### 9.1 Runtime SLIs/SLOs

- Screening snapshot compute time (system compute)
- Full memo compute time (system compute)
- Pipeline error rate per stage
- Extraction confidence distribution
- Deterministic engine reproducibility checks (hash match)

### 9.2 Trust Metrics (Must Be Measured)

- Sanad coverage: % of output claims with valid Sanad
- Grade distribution: A/B/C/D across claims; drift over time
- Defect rate: defects per deal; by type/severity
- Muḥāsabah pass rate; top validation failures
- No-Free-Facts violation count
- Debate outcomes: consensus rate vs stable dissent vs evidence exhaustion

---

## 10. Multi-Agent Analysis Engine — Specialist Agents (Layer 2)

§10 describes the **Layer 2: Investment Committee** system. This is the future multi-agent analysis engine that uses specialist agents for IC-level domain analysis. It is **not** the evidence provenance layer (see §11).

Layer 2 consumes the **Validated Evidence Package** produced by Layer 1 (§11) and combines it with enrichment data to produce an **IC-Ready Package** (GO / CONDITIONAL / NO-GO + rationale + questions).

Layer 2 is stage-dependent and requires enrichment/context beyond the dataset. It is not operational until Phase 7.C enrichment connectors are implemented.

See §6.2 for the specialist agent set, IC mechanism roles, and stage-specific weighting packs.

---

## 11. Debate Layer — Evidence Trust Court (Layer 1)

§11 describes the **Layer 1: Evidence Trust Debate**. This layer validates evidence integrity and provenance using the following roles: Advocate, Sanad Breaker, Contradiction Finder, Risk Officer, and Arbiter.

Layer 1 enforces Muḥāsabah + No-Free-Facts at every output boundary. Its purpose is to preserve dissent, surface contradictions, and produce a **Validated Evidence Package** of trusted claims with defects and provenance chains.

**Layer 1 is always required and stage-agnostic.** It runs on every deal regardless of investment stage, enrichment availability, or IC context. It does not perform domain-specialist investment analysis — that is Layer 2 (§10).

**Layer 2 is stage-dependent and requires enrichment/context beyond the dataset.** It cannot operate without the Validated Evidence Package from Layer 1, and it requires Phase 7.C enrichment connectors for external data inputs.

See §6.1 for the normative node graph, stop conditions, and incentive alignment.

---

## 12. Implementation Notes for the AI Coder

When implementing, follow these rules:

1. **Respect the two-layer debate architecture** (§6, §10, §11):
   - Layer 1 (Evidence Trust Court) is always required; implement first.
   - Layer 2 (Investment Committee) is future work; depends on Phase 7.C enrichment.
2. **Implement the data contracts first** (Claim, EvidenceItem, Sanad, Defect, MuḥāsabahRecord, DebateState).
3. **Build validation as code, not prompts**:
   - No-Free-Facts validator must be deterministic.
   - Muḥāsabah validator must be deterministic.
4. **Never compute financial numbers in the LLM**:
   - LLM may explain numbers but never derive them.
5. **Fail closed**:
   - If grade=D in material claim → stop and escalate.
   - If extraction_confidence < 0.95 → require human verification before calc/debate propagation.
6. **Persist everything**:
   - Every intermediate output is an audit artifact.

---

## Appendix: Normative Snippets (Directly Implement)

### A. DebateState (Required Fields)

```json
{
  "deal_id": "UUID",
  "claim_registry": "ClaimRegistry reference",
  "sanad_graph_ref": "Graph DB reference",
  "open_questions": ["string"],
  "round_number": 1,
  "messages": [{"role":"...", "content":"...", "claim_refs":["..."], "timestamp":"..."}],
  "utility_scores": {"agent_id": 0},
  "arbiter_decisions": [{"round":1, "decision":"...", "rationale":"..."}],
  "consensus_reached": false,
  "stop_reason": "CONSENSUS | STABLE_DISSENT | EVIDENCE_EXHAUSTED | MAX_ROUNDS | CRITICAL_DEFECT | null"
}
```

### B. Muḥāsabah Prompt Template (Embed in Every Agent)

- Claim summary (1–3 sentences)  
- Supported claim_ids / calc_ids (required)  
- Evidence summary (strongest evidence and why)  
- Counter-hypothesis (alternative explanation)  
- Falsifiability tests (what would disconfirm?)  
- Uncertainties (unknowns + impact)  
- Failure modes / red flags  
- Confidence (0–1) + justification
