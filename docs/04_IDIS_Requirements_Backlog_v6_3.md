# IDIS Requirements → Backlog Mapping (Epics, Milestones, Acceptance Criteria) — v6.3

**Source**: IDIS VC Edition v6.3 (January 2026)  
**Purpose**: Convert v6.3 requirements into a delivery plan: epics → capabilities → stories → acceptance criteria.

This backlog is enterprise-grade and assumes:
- multi-tenant SaaS + optional sovereign/on-prem deployment,
- auditability + compliance from day one,
- hard trust gates (Sanad + deterministic calcs + Muḥāsabah + No-Free-Facts),
- LangGraph debate orchestration.

---

## 0. Milestones (Suggested)

> These are sequencing recommendations; adjust to your team size.

### M0 — Foundations (2–4 weeks)
- Repo + CI/CD + IaC baseline
- Multi-tenant auth/RBAC skeleton
- Storage primitives (object store + Postgres)
- Event/audit logging skeleton
- Basic document ingestion + parsing POC

### M1 — Trust Core MVP (4–8 weeks)
- Claim Registry
- EvidenceItems + Source grading rubric
- Sanad chain building + claim grade algorithm
- Defect object + severity rules
- No-Free-Facts validator (deterministic)
- MuḥāsabahRecord contract + validator (deterministic)

### M2 — Deterministic Engines + Truth Dashboard (4–8 weeks)
- Calc engine framework + Calc-Sanad
- Truth Dashboard (verified/contradicted/unverified/inflated)
- Matn validators (unit/time/window plausibility checks)
- Human verification workflows (gates)

### M3 — Multi-Agent Debate (LangGraph) + Deliverables (6–10 weeks)
- LangGraph orchestration (Appendix C-1)
- Game-theoretic roles + arbiter utility scoring
- Stop conditions + dissent preservation
- Deliverables generator (Screening Snapshot + Memo v1)

### M4 — Integrations + Governance + Security Hardening (6–12 weeks)
- CRM sync (DealCloud/Affinity/Salesforce)
- Data providers BYOL connectors
- Drift monitoring + QA dashboards
- SOC2 readiness controls (logging, access, change mgmt)
- Data residency + BYOK options

### M5 — Production Pilot (ongoing)
- Parallel running on 10–20 deals
- Accuracy targets validation
- Prompt/version governance
- Ops runbooks + incident response

---

## 1. Epic: Ingestion & Deal Object Store (Layer 1)

### Capabilities
- Ingest: upload + connectors (DocSend/Drive/Dropbox/SharePoint)
- Parse: PDF/PPTX/XLSX/DOCX; generate Document + Spans
- Versioning/provenance: track artifact versions; detect newer versions
- Entity resolution: normalize company/founder names

### Stories (Representative)
1. **As an analyst, I can ingest a data room link and see all artifacts ingested with hashes.**
   - Acceptance:
     - Each artifact stored with SHA256
     - Artifact versions tracked; duplicate detection
     - Ingestion audit event stream present

2. **As the system, I can parse an XLSX and create cell spans with sheet+cell locators.**
   - Acceptance:
     - 95%+ parse success on sample set
     - Spans have stable locators (sheet/cell)
     - Sensitive docs marked “Restricted” in metadata

---

## 2. Epic: Claim Registry (Atomic Facts)

### Capabilities
- Extract claims with typed value structs (unit/currency/time window)
- Link each claim to one or more DocumentSpans
- Support “SUBJECTIVE” classification for non-verifiable statements

### Stories
1. **Claim extraction produces a Claim Registry with stable claim_ids.**
   - Acceptance:
     - Every extracted claim includes claim_type + value_struct
     - Each claim links to at least one span OR is marked UNKNOWN/SUBJECTIVE
     - Claims are idempotent across re-runs (same input → same claim_id) OR provide a deterministic mapping table

2. **No-Free-Facts enforcement at registration time.**
   - Acceptance:
     - Any agent output introducing a new factual claim MUST register it first
     - Unregistered facts are rejected deterministically

---

## 3. Epic: Sanad Trust Framework (Part II-A)

### Capabilities
- EvidenceItem grading (A/B/C/D) + internal subgrades
- Sanad chain building (TransmissionNodes)
- Corroboration status (Āḥād vs Mutawātir) with independence rules
- Defect detection & cure protocols
- Claim Sanad Grade algorithm (normative)

### Stories
1. **Compute claim Sanad Grade using normative algorithm.**
   - Acceptance:
     - Implements base=min(source grades across chain)
     - FATAL defects force D
     - MAJOR defects downgrade stepwise
     - Mutawātir upgrades B→A or C→B when no MAJOR defects
     - Unit tests include worked examples

2. **Independence test using upstream_origin_id + chain overlap.**
   - Acceptance:
     - If upstream_origin_id matches, sources are NOT independent
     - Mutawātir requires ≥3 independent sources
     - Independence computation stored and explainable

3. **Defect object is created for each detected defect with severity + cure protocol.**
   - Acceptance:
     - Defect enum includes all v6.3 types
     - Severity rules match v6.3
     - Waiver workflow requires actor + reason

---

## 4. Epic: Truth Dashboard (Part II)

### Capabilities
- Claim taxonomy
- Cross-document contradiction detection
- External validation (enrichment)
- Verdict states: VERIFIED / RED FLAG / INFLATED / FALSE / UNVERIFIED / SUBJECTIVE
- Matn checks as deterministic validators:
  - unit mismatch, time-window mismatch, plausibility bounds

### Stories
1. **Truth Dashboard table renders and links each verdict to evidence.**
   - Acceptance:
     - Each row links to claim_id and shows sanad_grade
     - Clicking shows exact span/cell/timecode
     - Contradictions show both values + sources

2. **Matn validator flags unit mismatch and time-window mismatch.**
   - Acceptance:
     - Deterministic rules; explainable messages
     - Creates MINOR defects (UNIT_MISMATCH, TIME_WINDOW_MISMATCH)

---

## 5. Epic: Deterministic Calculation Engines (Layer 3)

### Capabilities
- Python calc engine runner
- Input validation + reproducibility hashes
- Calc-Sanad linking to claims
- Extraction confidence gate (≥0.95) before computations

### Stories
1. **Run CAC payback and produce calc_id + Calc-Sanad.**
   - Acceptance:
     - Inputs list claim_ids
     - formula_hash + code_version + reproducibility_hash stored
     - calc_grade derived from min input grades

2. **Fail-closed extraction gate for calcs.**
   - Acceptance:
     - If extraction_confidence < 0.95 OR dhabt_score < 0.90, calc job is blocked until human verification record exists

---

## 6. Epic: Multi-Agent Analysis + Debate (Layer 4 + Appendix C/C-1 + Appendix F)

### Capabilities
- Agent roles: Advocate, Sanad Breaker, Contradiction Finder, Risk Officer, Arbiter
- Debate state machine with stop conditions
- Utility scoring (Brier + penalties)
- Dissent preservation in stable dissent
- Evidence-call retrieval (conditional)

### Stories
1. **LangGraph execution of normative node graph.**
   - Acceptance:
     - Node order matches v6.3
     - Stop reasons implemented in priority order
     - Max rounds = 5

2. **Utility scoring implements Brier and penalties with materiality gate.**
   - Acceptance:
     - Brier score computed on verifiable outcomes
     - Penalties applied for contradictions and No-Free-Facts violations
     - Utility only awarded for material challenges

3. **Stable dissent is preserved and shown in deliverables.**
   - Acceptance:
     - Dissent section lists dissenting agent + evidence refs + rationale
     - No forced consensus if evidence ambiguous

---

## 7. Epic: Muḥāsabah Gate (Appendix E)

### Capabilities
- MuḥāsabahRecord required for every agent output
- Deterministic validator rules
- Audit storage + sampling audits + humility metrics

### Stories
1. **Muḥāsabah validator rejects overconfident outputs without uncertainty.**
   - Acceptance:
     - Implements v6.3 validator pseudo-logic
     - Rejections are logged and surfaced to devops dashboard

2. **Humility metrics dashboard.**
   - Acceptance:
     - Tracks uncertainty disclosure frequency vs later corrections
     - Weekly sampling workflow (5% of records) supported

---

## 8. Epic: Deliverables Generator (Layer 5)

### Capabilities
- Screening Snapshot
- Full IC memo pack
- Agent consensus summary + dissent section
- Auto-generated Q&A for management calls
- Export to PDF/Doc/PPT/Excel

### Stories
1. **Generate Screening Snapshot with citations and grade labels.**
   - Acceptance:
     - All facts linked to claim_id/calc_id
     - Includes top red flags + missing info requests
     - Partner-ready one pager

2. **Generate IC memo with evidence-linked sections.**
   - Acceptance:
     - Each section has citations
     - Includes Truth Dashboard summary + Sanad grade distribution
     - Includes scenario table from deterministic engines

---

## 9. Epic: Governance, QA, Drift Control (Part XII-A / XII)

### Capabilities
- Drift checks weekly + manual monthly review
- Config versioning and rollback
- Accuracy tracking (red flag precision/recall)
- Audit artifacts retention + export

### Stories
1. **Configuration versioning with impact analysis and rollback.**
   - Acceptance:
     - Every weight/threshold change logged with actor + reason
     - “simulate change” on historical deals before apply
     - rollback within 24 hours

2. **Quality gates dashboard.**
   - Acceptance:
     - Citation coverage ≥95%
     - Numerical accuracy 100% for deterministic outputs
     - Cross-doc reconciliation coverage 100% for numeric claims

---

## 10. Epic: Security & Compliance (Part XIV)

### Capabilities
- Encryption at rest/in transit
- SSO/MFA/RBAC
- Data residency + BYOK
- Audit logs + legal hold + retention
- SOC2 readiness work items

### Stories
1. **RBAC enforcement for Restricted artifacts.**
   - Acceptance:
     - Access denied without proper role
     - Access logged with actor_id + reason

2. **BYOK integration.**
   - Acceptance:
     - Tenant can configure KMS key
     - Key rotation supported

---

## 11. Epic: Integrations (Part VIII)

### Capabilities
- CRM sync (DealCloud/Affinity/Salesforce)
- Doc connectors
- Enrichment connectors (BYOL)
- Webhook + API exports

### Stories
1. **BYOL connector framework.**
   - Acceptance:
     - Credentials never stored in plaintext
     - Retrieval_timestamp and provider attribution stored

---

## 12. Epic: Frontend (Analyst + Partner UI)

### Capabilities
- Triage queue
- Truth Dashboard UI
- Claim detail view with Sanad chain visualization
- Debate transcript + Muḥāsabah viewer
- Deliverables viewer/export
- Governance dashboards

### Stories
1. **Claim detail drawer shows Sanad chain and defects.**
   - Acceptance:
     - shows primary_source span
     - shows transmission nodes
     - shows grade explanation + defects + cure protocol

---

## 13. Definition of Done (Platform-Wide)

A capability is “Done” only when:
- API endpoints implemented + documented
- deterministic validators implemented + unit tested
- audit artifacts persisted
- RBAC enforced
- observable: metrics/logs/traces wired
- sample dataset run end-to-end without No-Free-Facts violations
- documentation updated (TDD + schemas + runbooks)

