# IDIS Enterprise Implementation Plan — v6.3

**Source**: IDIS VC Edition v6.3 (January 2026)  
**Purpose**: Provide a phased, enterprise-grade implementation plan that turns the v6.3 spec into a production system.

---

## 1. Guiding Constraints (Non-Negotiable)

1. Trust gates are code, not “best effort” prompts:
   - No-Free-Facts validator (deterministic)
   - Sanad grade + defect rules (deterministic)
   - Muḥāsabah validator (deterministic)
2. Deterministic calculations own all numbers.
3. Fail closed on material grade D claims.
4. Persist every artifact as audit evidence.

---

## 2. Phase Plan (Recommended)

### Phase 0 — Project Setup (Week 1)

Deliverables:
- Mono-repo initialized (backend + frontend + schema + infra)
- CI/CD (lint, tests, security scan)
- IaC baseline (Terraform)
- Secrets management (Vault/KMS)
- Logging + tracing baseline (OpenTelemetry)

Exit criteria:
- “Hello world” deployment to dev environment
- Tenant + Actor + Deal tables migrated
- RBAC enforced on one sample endpoint

---

### Phase 1 — Ingestion & Parsing (Weeks 2–4)

Implement:
- Document ingestion (upload + at least one connector)
- Document parsing into spans (PDF + XLSX minimum)
- Object storage + versioning (sha256)
- Search indexing (basic keyword; optional embeddings later)

Exit criteria:
- Can ingest a deal room into canonical Document + Span objects
- 95% parse success on internal sample set
- Full audit log for ingestion actions

---

### Phase 2 — Claim Registry + Truth Dashboard v1 (Weeks 5–8)

Implement:
- Claim extraction pipeline (LLM structured output + schema validation)
- Span citations for every claim
- Truth dashboard verdict states:
  - VERIFIED, CONTRADICTED, UNVERIFIED, SUBJECTIVE, UNKNOWN
- Matn validators (deterministic):
  - unit mismatch, time-window mismatch, basic plausibility

Exit criteria:
- Every extracted claim has:
  - claim_id,
  - span refs,
  - claim_type,
  - typed value_struct for numeric claims
- Contradiction detection works on numeric fields across sources

---

### Phase 3 — Sanad Trust Framework + Defects (Weeks 9–12)

Implement:
- EvidenceItem creation + grading rubric (A/B/C/D)
- Internal subgrade field for analytics only
- Transmission chain builder (TransmissionNode schema)
- Corroboration computation (Āḥād vs Mutawātir)
- Defect schema + severity rules + cure protocols
- Claim Sanad Grade algorithm (normative)

Exit criteria:
- 100% of material claims have Sanad objects
- Grade algorithm unit-tested with worked examples
- Defect creation and waiver workflow operational

---

### Phase 4 — Deterministic Engines + Calc-Sanad (Weeks 13–16)

Implement:
- Calculation runner framework
- Calc-Sanad:
  - formula hash, code version, reproducibility hash
  - calc grade derived from input claim grades
- Extraction confidence gate:
  - block calcs if extraction_confidence < 0.95 or dhabt_score < 0.90

Exit criteria:
- Calc outputs reproducible (same inputs → same hash)
- No LLM-generated arithmetic in deliverables
- Calcs are traceable to claim_ids and source evidence

---

### Phase 5 — Multi-Agent Debate + Muḥāsabah Gate (Weeks 17–22)

Implement:
- LangGraph orchestration per Appendix C-1
- Agent roles:
  - advocate, sanad breaker, contradiction finder, risk officer, arbiter
- Stop conditions (priority) and max rounds
- Utility scoring (Brier + penalties) and materiality gate
- MuḥāsabahRecord contract + deterministic validator
- Dissent preservation in stable dissent

Exit criteria:
- Debate runs end-to-end on sample deals
- Outputs blocked if Muḥāsabah missing or No-Free-Facts violated
- Stable dissent produces deliverables with dissent section

---

### Phase 6 — Deliverables Generator + Frontend v1 (Weeks 23–28)

Implement:
- Screening Snapshot generator
- IC memo pack generator
- Exports (PDF/Doc)
- UI:
  - truth dashboard
  - claim detail + Sanad chain view
  - debate transcript + Muḥāsabah view
  - deliverables viewer

Exit criteria:
- Partner can review a deal with auditable evidence links
- Every fact in memo has claim_id/calc_id reference
- Exports include an audit appendix (optional) for compliance

---

### Phase 7 — Enterprise Hardening (Weeks 29–40)

Implement:
- SSO integration (Okta/Azure)
- BYOK option
- Data residency controls
- SOC2 readiness features:
  - audit trails, access reviews, change management logs
- Governance dashboards:
  - Sanad coverage, defect rates, muhasabah pass rate, drift monitoring
- Integrations:
  - CRM sync
  - enrichment providers (BYOL framework)

Exit criteria:
- Security review passed
- Pilot fund onboarded with real deals
- Operational runbooks complete

---

## 3. Workstreams (Parallelization)

1. Platform foundation & infra
2. Data ingestion & parsing
3. Sanad & trust layer
4. Deterministic engines
5. Debate orchestration (LangGraph)
6. Frontend & UX
7. Security & compliance
8. Product & evaluation/metrics

---

## 4. Evaluation and Success Metrics

Minimum success metrics (pilot):
- 95%+ citation coverage for factual statements
- 100% deterministic provenance for numbers
- Reduction in analyst hours per screened deal by 50%+
- Red flag precision/recall targets defined and tracked
- Muḥāsabah rejection rate decreases over time (prompt + validator tuning) without lowering quality

---

## 5. Risks and Mitigations

- Risk: extraction errors contaminate calcs  
  - Mitigation: confidence gate + human verification workflow

- Risk: agents collude or converge prematurely  
  - Mitigation: randomized role assignment + arbiter validation + dissent preservation

- Risk: evidence gaps stall pipeline  
  - Mitigation: “missing info request” output and partial deliverables with explicit unknowns

- Risk: compliance concerns (client data)  
  - Mitigation: no-training policy, BYOK, data residency, immutable audit logs

