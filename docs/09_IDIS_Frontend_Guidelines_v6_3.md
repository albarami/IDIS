# IDIS Frontend Guidelines — v6.3

**Source**: IDIS VC Edition v6.3 (January 2026)  
**Purpose**: Provide concrete frontend requirements and UI/UX guidelines aligned with the trust-first design of IDIS.

---

## 1. Design Principles

1. **Evidence-first UI**
   - Every displayed factual statement must be clickable to reveal its `claim_id` or `calc_id` and underlying evidence.
2. **Trust transparency**
   - Show Sanad grade (A/B/C/D) prominently for material claims and metrics.
3. **Dissent visibility**
   - If debate ends in stable dissent, dissent must be shown (not hidden).
4. **Fail-closed UX**
   - If a “critical defect” is detected (material grade D), the UI must block “IC Ready” export until cured/waived with reason.
5. **Auditability**
   - Provide views to inspect debate transcript and Muḥāsabah logs.

---

## 2. Information Architecture (Primary Screens)

### 2.1 Triage Queue

- List of deals with:
  - stage/sector,
  - ingestion completeness,
  - system status (INGESTED/TRIAGED/IN_REVIEW/IC_READY),
  - top red-flag count,
  - Sanad coverage %.

### 2.2 Deal Overview

- High-level snapshot:
  - company, stage, headline metrics (calc-backed),
  - recommendation status,
  - last run timestamp,
  - critical defect banner (if any).

### 2.3 Truth Dashboard (Part II)

Core table columns:
- Claim summary (short)
- Claim type
- Verdict (VERIFIED/CONTRADICTED/UNVERIFIED/SUBJECTIVE/UNKNOWN)
- Sanad grade (A/B/C/D)
- Evidence count and corroboration status (Āḥād/Mutawātir)
- Defects badge (count + severity)
- “Open questions” link (if unresolved)

Table UX requirements:
- Sort/filter by grade, verdict, materiality
- Bulk export (CSV) for analyst workflows
- Row click opens Claim Detail Drawer

### 2.4 Claim Detail Drawer (Most Important Screen)

Must display:
- Claim text + typed value struct (unit/currency/time window)
- Claim status + materiality
- Sanad:
  - primary source span preview (with page/sheet/cell/timecode)
  - transmission chain (visual timeline or list)
  - corroborating sources and independence explanation
  - sanad_grade explanation
- Defects list:
  - defect_type, severity, description, cure_protocol, status
- Actions:
  - “Request Evidence” (creates task)
  - “Mark Cured” (with evidence)
  - “Waive” (requires reason + role check)

### 2.5 Sanad Graph Visualization (Optional but Valuable)

- Render provenance chain as graph:
  - nodes: EvidenceItem, TransmissionNode, Claim
  - edges: INPUT/OUTPUT/SUPPORTED_BY
- Highlight:
  - weakest-link node (min grade)
  - defect locations
  - independence clusters (upstream_origin_id groups)

### 2.6 Debate Viewer (Appendix C/C-1)

- Debate transcript with:
  - round markers,
  - agent role labels (Advocate, Sanad Breaker, etc.),
  - claim/calc references inline (clickable)
- Stop reason displayed:
  - consensus / stable dissent / evidence exhaustion / max rounds / critical defect
- Utility score summary per agent (optional; internal view)

### 2.7 Muḥāsabah Log Viewer (Appendix E)

For each agent output:
- supported_claim_ids
- evidence summary
- uncertainties (impact)
- falsifiability tests
- confidence
- validator status (PASS/REJECT) + reason if rejected

### 2.8 Deliverables Viewer

- Screening Snapshot preview + export
- IC memo preview + export
- “Dissent section” present if stable dissent
- “Audit appendix” toggle (optional):
  - claim list + grades + evidence refs

### 2.9 Governance Dashboard (Admin/Compliance)

- Sanad coverage %
- Grade distribution over time
- Defect type histogram (FATAL/MAJOR/MINOR)
- Muḥāsabah reject reasons trend
- No-Free-Facts violation counts
- Drift metrics (prompt/model versions)

---

## 3. UI Conventions

### 3.1 Grade Semantics

- A: strong provenance (audited/verified)
- B: institutional/credible but not fully audited
- C: unverified founder/weak sources (requires proof)
- D: contradicted/fabricated/broken chain (critical)

UI MUST:
- avoid implying “true/false” solely from grade; show verdict + grade separately.
- display grade badges consistently.

### 3.2 Evidence and Citation Rendering

- For PDFs: show page thumbnail + highlight bounding box if available
- For spreadsheets: show sheet+cell and a small grid preview
- For transcripts: show timecode + speaker attribution

### 3.3 Materiality

- Show a materiality indicator
- Allow filtering to “material claims only”

---

## 4. Frontend ↔ Backend Contracts (Must)

Frontend must treat backend as source of truth for:
- Sanad grades and defect severities
- validator pass/fail decisions
- export eligibility (critical defect blocks)

Required endpoints (minimum):
- `/deals/{id}`
- `/deals/{id}/truth-dashboard`
- `/claims/{id}` (includes sanad, defects)
- `/debate/{run_id}`
- `/deliverables/{id}`

---

## 5. Accessibility and Security

- Role-based UI gating MUST mirror backend RBAC; no security-by-UI.
- Avoid caching restricted artifacts in the browser.
- Use secure viewer components for sensitive documents.

---

## 6. Performance

- Truth Dashboard must handle 500–2,000 claims without lag:
  - server-side pagination and filtering
- Claim detail drawer should prefetch evidence thumbnails asynchronously
- Use virtualization for large tables

