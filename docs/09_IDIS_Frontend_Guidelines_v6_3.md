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

---

## 7. Implementation Completion Checklist

### 7.1 Screens — Implemented (Legacy Baseline)

| Screen | Route | Status | Tests | Acceptance |
|--------|-------|--------|-------|------------|
| Deals List | `/` | ✅ Done | UI tests pass | Lists deals, links to truth dashboard |
| Truth Dashboard | `/deals/[dealId]/truth-dashboard` | ✅ Done | UI tests pass | Shows claims, grades, verdicts; sort/filter |
| Claim Detail + Sanad | `/deals/[dealId]/truth-dashboard` (drawer) | ✅ Done | UI tests pass | Claim text, grade, sanad chain, defects |
| Audit Events | `/audit` | ✅ Done | UI tests pass | Lists/filters audit events |
| HumanGate Interface | `/deals/[dealId]/truth-dashboard` | ✅ Done | UI tests pass | Approve/reject functional |
| Run Status | `/runs/[runId]` | ✅ Done | UI tests pass | Basic status display |
| Debate Transcript | `/runs/[runId]` (section) | ✅ Done | 16 test cases | Formatted messages, raw JSON toggle |
| Deliverables Page | `/deals/[dealId]/deliverables` | ✅ Done | UI tests pass | List, download, generate |
| Runs List | `/runs` | ✅ Done | UI tests pass | Deal selector, navigation |

### 7.2 Screens — Requiring Completion (Rebuild)

| Screen | Route | Priority | Spec Section | Acceptance Criteria |
|--------|-------|----------|--------------|---------------------|
| **Triage Queue** | `/` (enhance) | 🟡 HIGH | §2.1 | Stage/sector filters, red-flag count, Sanad coverage %, ingestion status badge |
| **Deal Overview** | `/deals/[dealId]` | 🟡 HIGH | §2.2 | Headline calc-backed metrics, recommendation status, critical defect banner, last run timestamp |
| **Claim Detail Drawer (full)** | `/deals/[dealId]/truth-dashboard` | 🔴 CRITICAL | §2.4 | Source span preview, transmission chain timeline, corroboration explanation, defect list with cure actions, "Request Evidence" / "Mark Cured" / "Waive" buttons |
| **Sanad Graph Visualization** | `/deals/[dealId]/sanad-graph` | 🟢 NICE | §2.5 | Interactive graph: EvidenceItem/TransmissionNode/Claim nodes, weakest-link highlight, defect locations, independence clusters |
| **Debate Viewer (enhanced)** | `/runs/[runId]` (enhance) | 🟡 HIGH | §2.6 | Round markers, agent role colors, inline claim/calc refs (clickable), stop reason display, utility score summary |
| **Muhasabah Log Viewer** | `/runs/[runId]/muhasabah` | 🟡 HIGH | §2.7 | Per-agent: supported_claim_ids, evidence summary, uncertainties, falsifiability, confidence, PASS/REJECT status |
| **Deliverables Viewer (enhanced)** | `/deals/[dealId]/deliverables` | 🟡 HIGH | §2.8 | Preview modal, dissent section, audit appendix toggle, format selector (PDF/DOCX) |
| **Governance Dashboard** | `/admin/governance` | 🟢 NICE | §2.9 | Sanad coverage %, grade distribution chart, defect histogram, Muhasabah reject trend, NFF violations, drift metrics |

### 7.3 Components — Requiring Completion

| Component | Used By | Priority | Acceptance Criteria |
|-----------|---------|----------|---------------------|
| **GradeBadge** | Truth Dashboard, Claim Drawer, Deliverables | 🔴 CRITICAL | Consistent A/B/C/D color coding (green/blue/amber/red), tooltip with grade rationale |
| **EvidenceSpanPreview** | Claim Drawer | 🔴 CRITICAL | PDF: page thumbnail + bbox highlight; XLSX: sheet+cell grid; DOCX: paragraph excerpt; PPTX: slide thumbnail |
| **TransmissionChainTimeline** | Claim Drawer, Sanad Graph | 🟡 HIGH | Vertical timeline showing each node: type, actor, timestamp, confidence; weakest node highlighted |
| **DefectCard** | Claim Drawer, Governance | 🟡 HIGH | Type, severity badge, description, cure protocol, status; action buttons (Cure/Waive) with role check |
| **RunProgressStepper** | Run Status | 🟡 HIGH | Step indicators: parse → extract → grade → calc → enrich → debate → deliver; current step highlighted, error state for failed steps |
| **ClaimRefLink** | Debate Viewer, Deliverables | 🟡 HIGH | Inline clickable `claim_id` / `calc_id` references that open the Claim Drawer |
| **MaterialityFilter** | Truth Dashboard | 🟡 HIGH | Toggle: ALL / LOW / MEDIUM / HIGH / CRITICAL; persisted in URL query params |
| **BulkExportButton** | Truth Dashboard | 🟢 NICE | CSV export of filtered claims with grades, verdicts, evidence counts |

### 7.4 UX States — Required for All Screens

| State | Requirement |
|-------|-------------|
| **Loading** | Skeleton loaders matching final layout shape |
| **Empty** | Descriptive empty state with action prompt (e.g., "No claims yet. Upload a document to start.") |
| **Error** | `ErrorCallout` component with `request_id`, RFC 7807 error details, retry button |
| **Pagination** | Cursor-based; "Load more" or infinite scroll; never client-side only |
| **Mobile** | Responsive breakpoints at 640px, 768px, 1024px; drawer becomes full-screen on mobile |

---

## 8. Screen Wireframe Descriptions

### 8.1 Triage Queue (Enhanced Deals List)

```
┌──────────────────────────────────────────────────────────┐
│  IDIS — Triage Queue                    [+ New Deal]     │
├──────────────────────────────────────────────────────────┤
│  Filters: [Stage ▾] [Sector ▾] [Status ▾] [Search...]   │
├──────────────────────────────────────────────────────────┤
│  Company        Stage    Status     Red Flags  Sanad %   │
│  ─────────────  ───────  ─────────  ─────────  ───────   │
│  Acme Robotics  Ser. A   IN_REVIEW  2 🔴       87%       │
│  Beta Health    Seed     SCREENING  0          95%       │
│  Gamma AI       Ser. B   IC_READY   1 🟡       100%      │
└──────────────────────────────────────────────────────────┘
```

### 8.2 Claim Detail Drawer

```
┌─────────────────────────────────────┐
│  ← Back to Truth Dashboard          │
│                                     │
│  "ARR of $4.2M as of Q3 2025"      │
│  Grade: [B]  Verdict: VERIFIED      │
│  Materiality: HIGH                  │
│                                     │
│  ── Source Span ──────────────────  │
│  📄 pitch_deck_v3.pdf, Page 12      │
│  [Page thumbnail with highlight]    │
│                                     │
│  ── Transmission Chain ───────────  │
│  INGEST → EXTRACT → NORMALIZE      │
│  (timeline with timestamps)         │
│                                     │
│  ── Corroboration ────────────────  │
│  AHAD_2 (2 independent sources)     │
│  • Pitch deck (SADUQ)              │
│  • Bank statement (THIQAH_THABIT)   │
│                                     │
│  ── Defects (1) ──────────────────  │
│  ⚠️ STALENESS (MINOR)               │
│  "Source is >6 months old"          │
│  Cure: REQUEST_SOURCE               │
│                                     │
│  [Request Evidence] [Mark Cured]    │
│  [Waive (requires reason)]          │
└─────────────────────────────────────┘
```

### 8.3 Governance Dashboard

```
┌──────────────────────────────────────────────────────────┐
│  IDIS — Governance Dashboard                             │
├──────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐      │
│  │ Sanad Covg  │  │ NFF Violns  │  │ Muhasabah   │      │
│  │   94.2%     │  │      3      │  │ Pass: 97.1% │      │
│  └─────────────┘  └─────────────┘  └─────────────┘      │
│                                                          │
│  Grade Distribution          Defect Histogram            │
│  ┌──────────────┐           ┌──────────────┐             │
│  │ A ████████ 42│           │ FATAL  ██ 4  │             │
│  │ B ██████ 31  │           │ MAJOR  ████ 12│            │
│  │ C ████ 18    │           │ MINOR  ██████ 28│          │
│  │ D ██ 9       │           └──────────────┘             │
│  └──────────────┘                                        │
│                                                          │
│  Muhasabah Reject Reasons (30d)                          │
│  ┌──────────────────────────────┐                        │
│  │ No-Free-Facts      ████ 8   │                        │
│  │ Overconfidence      ██ 3    │                        │
│  │ Missing Falsif.     █ 2     │                        │
│  └──────────────────────────────┘                        │
└──────────────────────────────────────────────────────────┘
```

