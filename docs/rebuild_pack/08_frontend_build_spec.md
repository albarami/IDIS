# Frontend Build Specification

**Version:** 1.0.0  
**Date:** 2026-02-05  
**Status:** Build Spec  
**Reference:** 09_IDIS_Frontend_Guidelines_v6_3.md

---

## 1. Overview

This document specifies the completion criteria for IDIS frontend screens. Doc 09 provides guidelines; this spec defines exactly what needs to be built to complete the UI.

---

## 2. Technology Stack

| Layer | Technology | Version |
|-------|------------|---------|
| Framework | Next.js | 14+ (App Router) |
| Language | TypeScript | 5.3+ |
| Styling | TailwindCSS | 3.4+ |
| Components | shadcn/ui | Latest |
| Icons | Lucide React | Latest |
| State | React Query | 5.x |
| Forms | React Hook Form + Zod | Latest |

---

## 3. Missing Screens (Priority Order)

### 3.1 Runs List Page — `/runs`

**Status:** ⚠️ Partial (deal selector exists, needs full runs list)

#### Wireframe
```
┌────────────────────────────────────────────────────────────┐
│  IDIS  │ Dashboard │ Deals │ [Runs] │ Settings            │
├────────────────────────────────────────────────────────────┤
│                                                            │
│  Pipeline Runs                                [Refresh]    │
│                                                            │
│  ┌─────────────────────────────────────────────────────┐  │
│  │ Deal: [Select Deal ▼] or [All Deals]                │  │
│  │ Status: [All ▼] [Running] [Completed] [Failed]      │  │
│  └─────────────────────────────────────────────────────┘  │
│                                                            │
│  ┌───────────────────────────────────────────────────────┐│
│  │ Run ID       │ Deal      │ Status   │ Started   │ ... ││
│  ├───────────────────────────────────────────────────────┤│
│  │ run_abc123   │ Acme Corp │ ✅ Done  │ 2h ago    │ →  ││
│  │ run_def456   │ Beta Inc  │ ⏳ Run   │ 5m ago    │ →  ││
│  │ run_ghi789   │ Acme Corp │ ❌ Fail  │ 1d ago    │ →  ││
│  └───────────────────────────────────────────────────────┘│
│                                                            │
│  Showing 1-20 of 156          [← Prev] [1] [2] [3] [Next →]│
└────────────────────────────────────────────────────────────┘
```

#### Requirements
- [ ] Deal selector dropdown (populated from `/v1/deals`)
- [ ] Status filter chips
- [ ] Runs table with columns: Run ID, Deal, Status, Started, Duration, Current Step
- [ ] Status badges: Running (blue), Completed (green), Failed (red), Blocked (yellow)
- [ ] Click row → navigate to `/runs/[runId]`
- [ ] Pagination with cursor-based navigation
- [ ] Auto-refresh toggle for running status

#### API Endpoints
- `GET /v1/deals` — List deals for selector
- `GET /v1/deals/{dealId}/runs` — List runs for selected deal

#### Component Mapping
| Component | File |
|-----------|------|
| Page | `app/runs/page.tsx` |
| RunsTable | `components/runs/RunsTable.tsx` |
| DealSelector | `components/deals/DealSelector.tsx` |
| StatusBadge | `components/ui/StatusBadge.tsx` |

---

### 3.2 Run Detail Page — `/runs/[runId]`

**Status:** ⚠️ Partial (exists, needs pipeline progress + debate viewer)

#### Wireframe
```
┌────────────────────────────────────────────────────────────┐
│  ← Back to Runs                                            │
├────────────────────────────────────────────────────────────┤
│                                                            │
│  Run: run_abc123                              Status: ✅    │
│  Deal: Acme Corp (Series A)                                │
│  Started: 2026-02-05 10:30 UTC    Duration: 4m 32s        │
│                                                            │
│  ┌─────────────────────────────────────────────────────┐  │
│  │ Pipeline Progress                                    │  │
│  │ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━░░░░░░░░░░ 70%     │  │
│  │                                                      │  │
│  │ ✅ Parse Documents (12 docs)                         │  │
│  │ ✅ Extract Claims (47 claims)                        │  │
│  │ ✅ Grade Sanad (A:12, B:20, C:10, D:5)              │  │
│  │ ✅ Run Calculations (8 calcs)                        │  │
│  │ ⏳ Debate (Round 2 of 5)                             │  │
│  │ ○ Generate Deliverables                              │  │
│  │ ○ Human Gate                                         │  │
│  └─────────────────────────────────────────────────────┘  │
│                                                            │
│  ┌─────────────────────────────────────────────────────┐  │
│  │ [Claims] [Debate Transcript] [Deliverables] [Audit] │  │
│  ├─────────────────────────────────────────────────────┤  │
│  │                                                      │  │
│  │  Debate Transcript                    [Raw JSON ◻]  │  │
│  │  ─────────────────────────────────────────────────  │  │
│  │  Round 1                                             │  │
│  │  ┌─────────────────────────────────────────────┐    │  │
│  │  │ 🎯 Advocate                      10:31:05   │    │  │
│  │  │ The company shows strong ARR growth...      │    │  │
│  │  │ [claim:abc] [claim:def]                     │    │  │
│  │  └─────────────────────────────────────────────┘    │  │
│  │  ┌─────────────────────────────────────────────┐    │  │
│  │  │ 🔍 Sanad Breaker                 10:31:42   │    │  │
│  │  │ The ARR claim lacks bank verification...    │    │  │
│  │  │ Defect: INCONSISTENCY [claim:abc]           │    │  │
│  │  └─────────────────────────────────────────────┘    │  │
│  │                                                      │  │
│  └─────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────┘
```

#### Requirements
- [ ] Run header with status, deal link, timing
- [ ] Pipeline progress visualization (stepper with status)
- [ ] Step details on click (artifacts, errors)
- [ ] Tab navigation: Claims, Debate Transcript, Deliverables, Audit
- [ ] Debate transcript with formatted messages (not raw JSON)
- [ ] Speaker icons and role labels
- [ ] Claim/calc reference links (clickable)
- [ ] Raw JSON toggle for debugging

#### API Endpoints
- `GET /v1/runs/{runId}` — Run detail with steps
- `GET /v1/debate/{debateId}` — Debate transcript

#### Component Mapping
| Component | File |
|-----------|------|
| Page | `app/runs/[runId]/page.tsx` |
| PipelineProgress | `components/runs/PipelineProgress.tsx` |
| DebateTranscript | `components/debate/DebateTranscript.tsx` |
| DebateMessage | `components/debate/DebateMessage.tsx` |
| ClaimLink | `components/claims/ClaimLink.tsx` |

---

### 3.3 Deliverables Page — `/deals/[dealId]/deliverables`

**Status:** ⚠️ Partial (basic list exists, needs download/view)

#### Wireframe
```
┌────────────────────────────────────────────────────────────┐
│  ← Back to Deal                                            │
├────────────────────────────────────────────────────────────┤
│                                                            │
│  Deliverables: Acme Corp                                   │
│                                                            │
│  ┌─────────────────────────────────────────────────────┐  │
│  │ Generate New                                         │  │
│  │ [Screening Snapshot] [IC Memo] [Diligence Q&A]      │  │
│  └─────────────────────────────────────────────────────┘  │
│                                                            │
│  ┌───────────────────────────────────────────────────────┐│
│  │ Type              │ Status  │ Created    │ Actions    ││
│  ├───────────────────────────────────────────────────────┤│
│  │ Screening Snapshot│ ✅ Ready│ 2h ago     │ [↓] [👁]  ││
│  │ IC Memo           │ ✅ Ready│ 1h ago     │ [↓] [👁]  ││
│  │ Screening Snapshot│ ⚠️ Draft│ 1d ago     │ [👁]      ││
│  └───────────────────────────────────────────────────────┘│
│                                                            │
│  Export Options:                                           │
│  [Download PDF] [Download DOCX] [Copy Link]               │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

#### Requirements
- [ ] Generate buttons for each deliverable type
- [ ] Deliverables list with type, status, timestamp
- [ ] Download button (PDF/DOCX)
- [ ] Preview button (opens viewer)
- [ ] Status: Ready, Draft, Generating, Failed
- [ ] URI handling: http/https direct, /v1/ via proxy, copy for others

#### API Endpoints
- `GET /v1/deals/{dealId}/deliverables` — List deliverables
- `POST /v1/deals/{dealId}/deliverables` — Generate new
- `GET /v1/deliverables/{id}` — Get deliverable content

#### Component Mapping
| Component | File |
|-----------|------|
| Page | `app/deals/[dealId]/deliverables/page.tsx` |
| DeliverablesList | `components/deliverables/DeliverablesList.tsx` |
| GenerateButtons | `components/deliverables/GenerateButtons.tsx` |
| DeliverableViewer | `components/deliverables/DeliverableViewer.tsx` |

---

### 3.4 Claim Detail Drawer

**Status:** ⏳ Not started

#### Wireframe
```
┌────────────────────────────────────────────────┐
│                              [×]               │
│  Claim Detail                                  │
│  ─────────────────────────────────────────────│
│                                                │
│  "ARR of $2.5M as of Q4 2025"                 │
│                                                │
│  Type: FINANCIAL    Materiality: HIGH         │
│  Verdict: VERIFIED  Grade: B                  │
│                                                │
│  Value                                         │
│  ┌────────────────────────────────────────┐   │
│  │ $2,500,000 USD                         │   │
│  │ Time Window: Q4 2025                   │   │
│  └────────────────────────────────────────┘   │
│                                                │
│  Sanad Chain                                   │
│  ┌────────────────────────────────────────┐   │
│  │ 📄 financial_model.xlsx:B12            │   │
│  │    ↓ Extracted by: EXTRACT_CLAIMS_V1   │   │
│  │ 📋 Claim created                       │   │
│  │    Grade: B (THIQAH source)            │   │
│  └────────────────────────────────────────┘   │
│                                                │
│  Corroboration: AHAD_1 (1 source)            │
│                                                │
│  Defects (0)                                  │
│  ┌────────────────────────────────────────┐   │
│  │ No defects detected                    │   │
│  └────────────────────────────────────────┘   │
│                                                │
│  Actions                                       │
│  [Request Evidence] [Flag Defect] [Override] │
│                                                │
└────────────────────────────────────────────────┘
```

#### Requirements
- [ ] Drawer/sheet component (slides from right)
- [ ] Claim text, type, materiality, verdict, grade display
- [ ] Value struct rendering with units/currency/time
- [ ] Sanad chain visualization (timeline or list)
- [ ] Source span preview with locator
- [ ] Corroboration status with count
- [ ] Defects list with severity badges
- [ ] Action buttons: Request Evidence, Flag Defect, Override

#### API Endpoints
- `GET /v1/claims/{claimId}` — Claim detail
- `GET /v1/claims/{claimId}/sanad` — Sanad chain

#### Component Mapping
| Component | File |
|-----------|------|
| ClaimDrawer | `components/claims/ClaimDrawer.tsx` |
| SanadChain | `components/sanad/SanadChain.tsx` |
| DefectsList | `components/defects/DefectsList.tsx` |
| ValueDisplay | `components/claims/ValueDisplay.tsx` |

---

### 3.5 Sanad Graph Visualization (Optional)

**Status:** ⏳ Not started (nice-to-have)

#### Requirements
- [ ] Graph visualization library (e.g., react-flow, d3)
- [ ] Nodes: Evidence, TransmissionNode, Claim
- [ ] Edges: INPUT, OUTPUT, SUPPORTED_BY
- [ ] Color-coding by grade
- [ ] Defect highlighting
- [ ] Independence cluster visualization

---

### 3.6 Governance Dashboard — `/admin/governance`

**Status:** ⏳ Not started

#### Wireframe
```
┌────────────────────────────────────────────────────────────┐
│  Governance Dashboard                     [Date Range ▼]   │
├────────────────────────────────────────────────────────────┤
│                                                            │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐       │
│  │ Sanad        │ │ NFF          │ │ Muḥāsabah    │       │
│  │ Coverage     │ │ Violations   │ │ Pass Rate    │       │
│  │   97.2%      │ │     0        │ │   98.7%      │       │
│  │   ↑ 0.3%     │ │   ✓ Clean    │ │   ↓ 0.2%     │       │
│  └──────────────┘ └──────────────┘ └──────────────┘       │
│                                                            │
│  Grade Distribution (30 days)                              │
│  ┌─────────────────────────────────────────────────────┐  │
│  │  A ████████████ 24%                                 │  │
│  │  B ████████████████████ 42%                         │  │
│  │  C ████████████ 26%                                 │  │
│  │  D ████ 8%                                          │  │
│  └─────────────────────────────────────────────────────┘  │
│                                                            │
│  Defect Trend                                              │
│  ┌─────────────────────────────────────────────────────┐  │
│  │     📈 Line chart: defects over time by severity    │  │
│  └─────────────────────────────────────────────────────┘  │
│                                                            │
│  Recent Issues                                             │
│  ┌───────────────────────────────────────────────────────┐│
│  │ • Deal "Beta Inc" has 3 Grade D claims               ││
│  │ • Muḥāsabah rejection rate spike on 2026-02-04       ││
│  └───────────────────────────────────────────────────────┘│
└────────────────────────────────────────────────────────────┘
```

#### Requirements
- [ ] KPI cards: Sanad coverage, NFF violations, Muḥāsabah rate
- [ ] Grade distribution chart (bar or pie)
- [ ] Defect trend line chart
- [ ] Recent issues/alerts list
- [ ] Date range selector
- [ ] Drill-down to specific deals

---

## 4. Loading & Error States

### 4.1 Loading States

```typescript
// Standard loading skeleton
<Skeleton className="h-8 w-full" />

// Table loading
<TableSkeleton rows={10} columns={5} />

// Card loading
<CardSkeleton />
```

### 4.2 Error States

```typescript
interface ErrorState {
  code: string;
  message: string;
  request_id?: string;
  retry?: () => void;
}

// Error callout component
<ErrorCallout
  title="Failed to load runs"
  message={error.message}
  requestId={error.request_id}
  onRetry={refetch}
/>
```

### 4.3 Empty States

```typescript
// Empty table
<EmptyState
  icon={<FileX />}
  title="No runs found"
  description="Start a pipeline run to see results here"
  action={<Button>Start Run</Button>}
/>
```

---

## 5. API Client

### 5.1 Client Structure

```typescript
// lib/idis.ts
export const idisClient = {
  deals: {
    list: (params?: ListDealsParams) => fetchApi('/v1/deals', params),
    get: (dealId: string) => fetchApi(`/v1/deals/${dealId}`),
    getRuns: (dealId: string) => fetchApi(`/v1/deals/${dealId}/runs`),
    getDeliverables: (dealId: string) => fetchApi(`/v1/deals/${dealId}/deliverables`),
  },
  runs: {
    get: (runId: string) => fetchApi(`/v1/runs/${runId}`),
    start: (dealId: string, config?: RunConfig) => 
      fetchApi(`/v1/deals/${dealId}/runs`, { method: 'POST', body: config }),
  },
  claims: {
    get: (claimId: string) => fetchApi(`/v1/claims/${claimId}`),
    getSanad: (claimId: string) => fetchApi(`/v1/claims/${claimId}/sanad`),
  },
  debate: {
    get: (debateId: string) => fetchApi(`/v1/debate/${debateId}`),
  },
  deliverables: {
    get: (id: string) => fetchApi(`/v1/deliverables/${id}`),
    generate: (dealId: string, type: string) =>
      fetchApi(`/v1/deals/${dealId}/deliverables`, { method: 'POST', body: { type } }),
  },
};
```

### 5.2 React Query Hooks

```typescript
// hooks/useRuns.ts
export function useRuns(dealId: string) {
  return useQuery({
    queryKey: ['runs', dealId],
    queryFn: () => idisClient.deals.getRuns(dealId),
    refetchInterval: 5000, // Auto-refresh for running status
  });
}

// hooks/useClaim.ts
export function useClaim(claimId: string) {
  return useQuery({
    queryKey: ['claim', claimId],
    queryFn: () => idisClient.claims.get(claimId),
  });
}
```

---

## 6. File Structure

```
ui/src/
├── app/
│   ├── layout.tsx
│   ├── page.tsx                    # Dashboard
│   ├── deals/
│   │   ├── page.tsx                # Deals list
│   │   └── [dealId]/
│   │       ├── page.tsx            # Deal detail (truth dashboard)
│   │       └── deliverables/
│   │           └── page.tsx        # Deliverables
│   ├── runs/
│   │   ├── page.tsx                # Runs list
│   │   └── [runId]/
│   │       └── page.tsx            # Run detail
│   ├── admin/
│   │   └── governance/
│   │       └── page.tsx            # Governance dashboard
│   └── api/
│       └── idis/
│           └── [...path]/
│               └── route.ts        # API proxy
├── components/
│   ├── ui/                         # shadcn/ui components
│   ├── claims/
│   │   ├── ClaimDrawer.tsx
│   │   ├── ClaimLink.tsx
│   │   └── ValueDisplay.tsx
│   ├── debate/
│   │   ├── DebateTranscript.tsx
│   │   └── DebateMessage.tsx
│   ├── deliverables/
│   │   ├── DeliverablesList.tsx
│   │   ├── GenerateButtons.tsx
│   │   └── DeliverableViewer.tsx
│   ├── runs/
│   │   ├── RunsTable.tsx
│   │   └── PipelineProgress.tsx
│   ├── sanad/
│   │   └── SanadChain.tsx
│   └── defects/
│       └── DefectsList.tsx
├── hooks/
│   ├── useRuns.ts
│   ├── useClaim.ts
│   └── useDeliverables.ts
└── lib/
    ├── idis.ts                     # API client
    ├── debateNormalizer.ts         # Debate message normalization
    └── utils.ts
```

---

## 7. Acceptance Criteria

### 7.1 Runs List
- [ ] Displays runs for selected deal
- [ ] Status badges correct
- [ ] Pagination works
- [ ] Navigation to run detail

### 7.2 Run Detail
- [ ] Pipeline progress visible
- [ ] Debate transcript formatted
- [ ] Claim links clickable
- [ ] Raw JSON toggle works

### 7.3 Deliverables
- [ ] Generation buttons work
- [ ] Download works for PDF/DOCX
- [ ] Preview opens correctly
- [ ] Status updates reflect

### 7.4 Claim Drawer
- [ ] Opens from truth dashboard row click
- [ ] Sanad chain displays
- [ ] Defects list displays
- [ ] Actions functional

### 7.5 General
- [ ] All loading states implemented
- [ ] All error states implemented
- [ ] Empty states implemented
- [ ] Mobile responsive
- [ ] Accessibility (ARIA, keyboard nav)
