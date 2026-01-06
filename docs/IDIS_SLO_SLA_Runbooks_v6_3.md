# IDIS SLO/SLA & Runbooks (v6.3)
**Version:** 6.3 (derived from IDIS v6.3 FINAL)  
**Date:** 2026-01-06  
**Status:** Normative baseline for production readiness  
**Audience:** SRE/DevOps, Backend, Security, Product Ops, Support, Compliance

---

## 0) Purpose

This document defines the **operational standards** required to run IDIS as an enterprise-grade platform:
- SLIs/SLOs (reliability and performance targets)
- External SLAs (what we commit to customers)
- Error budget policy
- Incident management model (SEV levels, roles, comms)
- Core runbooks (diagnosis + mitigation + recovery)
- Backup/restore and DR requirements (RPO/RTO)
- Monitoring and alerting requirements

**Design context:** IDIS is a high-stakes system. Reliability must preserve **trust invariants**:
- No‑Free‑Facts enforcement
- Sanad chain integrity
- Calc‑Sanad reproducibility
- Muḥāsabah gating
- Immutable audit trails

---

## 1) Definitions

### 1.1 SLI / SLO / SLA
- **SLI (Service Level Indicator):** measurable metric (e.g., request latency, job success rate).
- **SLO (Objective):** target for the SLI (e.g., 99.9% success).
- **SLA (Agreement):** customer contract commitment; usually <= SLO.

### 1.2 Error Budget
Error budget is the allowed unreliability:
- For availability SLO 99.9% monthly → ~43.2 minutes/month downtime budget.
- Error budget is consumed by:
  - incident downtime
  - severe degradation that violates SLO definitions

When error budget is depleted, changes are restricted (see §4).

---

## 2) Service Catalog (Operational Ownership)

| Service | Purpose | Owner | Criticality |
|---|---|---|---|
| API Gateway | Routing, auth, rate limits | Platform | Critical |
| Deal Service | Deal lifecycle | Backend | High |
| Ingestion Service | Document ingestion + parsing | Data/Backend | Critical |
| Claim Registry | Claims CRUD + validators | Backend | Critical |
| Sanad Graph | Evidence chain + grading | Data/Backend | Critical |
| Defects Service | Defect objects + cure workflows | Backend | High |
| Calc Service | Deterministic engines + Calc‑Sanad | Data/Backend | Critical |
| Orchestrator (Runs) | Pipeline runs | Platform/SRE | High |
| Debate Orchestrator | LangGraph sessions | Platform/ML | High |
| Deliverables Service | Generate PDF/DOCX + artifacts | Backend | High |
| Audit Service | Append-only logs + query | Security/SRE | Critical |
| Integrations Service | CRM/docs/enrichment connectors | Integrations | High |
| Webhooks Service | Outbound events | Integrations | Medium |
| Object Store | Raw docs + outputs | SRE | Critical |
| Datastores | Postgres/Graph/Vector | SRE | Critical |
| Observability | Metrics/logs/traces | SRE | Critical |

---

## 3) SLIs and SLOs (Internal Targets)

### 3.1 Global Availability SLOs (per tenant)
**SLO window:** rolling 30 days

- **API availability:** 99.9%  
  Definition: successful response (2xx/3xx) for non-fault requests; excludes client errors (4xx) and planned maintenance within notice windows.

- **Core pipeline availability:** 99.5%  
  Definition: ability to start and complete runs for eligible deals (excluding user-caused incompleteness or human-gate waiting).

### 3.2 Latency SLOs (p95 unless noted)
- **API Gateway p95:** < 300 ms (GET) / < 600 ms (POST/PATCH)
- **Claim list p95:** < 900 ms (up to 50 items)
- **Sanad retrieval p95:** < 1,200 ms (graph lookup)
- **Audit query p95:** < 1,500 ms (time-bounded)

### 3.3 Job/Run Success SLOs
- **Document ingestion success rate:** ≥ 99.0%  
  Failure = unhandled error, corruption, or repeated retries exhausted.
- **Extraction gate pass rate:** ≥ 95% for supported doc types  
  (Note: This is operational quality; not “truth quality.”)
- **Calc run success rate:** ≥ 99.5%
- **Deliverable generation success rate:** ≥ 99.0%
- **Debate session completion rate:** ≥ 98.0% (within max rounds)

### 3.4 Queue/Backlog SLOs
- **Ingestion queue time (p95):** < 10 minutes
- **OCR queue time (p95):** < 30 minutes
- **Run scheduling delay (p95):** < 5 minutes

### 3.5 Trust-Invariant SLOs (Quality-as-Operations)
These are operationally enforced gates; violations are SEV-1/2 depending on scope:

- **No‑Free‑Facts violations in IC deliverables:** 0 tolerated  
  Definition: any IC memo or Truth Dashboard containing factual statements without `claim_id` or `calc_id` references.

- **Audit event coverage:** 100% for mutating operations  
  Definition: every POST/PATCH/override/human-gate action emits audit event.

- **Calc‑Sanad reproducibility failures:** ≤ 0.1%  
  Definition: rerun with same inputs/formula hash yields different output.

- **Tenant isolation violations:** 0 tolerated

---

## 4) External SLA (Customer Commitments)

### 4.1 Default SLA (Enterprise)
- **API uptime SLA:** 99.5% monthly
- **Support response time:**
  - SEV-1: 30 minutes
  - SEV-2: 2 hours
  - SEV-3: 1 business day
- **Planned maintenance:** with ≥ 72 hours notice; ≤ 2 hours/month

### 4.2 Credits (Template)
- 99.0–99.5%: 5% monthly credit
- 98.0–99.0%: 10% credit
- <98.0%: 20% credit  
(Actual SLA credits should be finalized by legal/commercial.)

---

## 5) Error Budget Policy

When remaining monthly error budget < 25%:
- Freeze non-essential releases
- Allow only:
  - security patches
  - bug fixes linked to SLO recovery
  - scaling actions and infra hardening

When < 10%:
- Mandatory approval from SRE lead + product owner for any deploy
- Postmortem required for every incident impacting budget

---

## 6) Incident Management

### 6.1 Severity Levels

**SEV‑1 (Critical)**
- Tenant isolation breach
- Audit log tampering or missing audit events for critical actions
- No‑Free‑Facts failure reaching IC deliverables
- Large-scale outage affecting multiple tenants
- Key compromise / widespread data exposure

**SEV‑2 (High)**
- Single-tenant major outage
- Deliverable generation failing for many deals
- Deterministic calc correctness/regression detected
- Debate system stuck across many sessions
- Enrichment/CRM integration failure causing material workflow blockage

**SEV‑3 (Medium)**
- Partial degradation (latency, queue delays)
- Single integration provider outage (with fallbacks)
- Non-critical UI issues

**SEV‑4 (Low)**
- Cosmetic issues, minor bugs, non-urgent improvements

### 6.2 Roles During Incident
- **Incident Commander (IC):** directs response, sets priorities
- **Communications Lead:** customer updates, internal status
- **Operations Lead:** infra actions, scaling, rollback
- **Subject Matter Experts (SMEs):** ingestion, graph, calc, debate, security
- **Scribe:** timeline + actions + evidence for postmortem

### 6.3 Communication Cadence
- SEV-1: updates every 30 minutes
- SEV-2: updates every 60 minutes
- SEV-3: as needed; at least initial + resolution notes

---

## 7) Backup, Restore, and Disaster Recovery

### 7.1 RPO/RTO Targets (Default)
- **Postgres (transactional):**
  - RPO: 15 minutes
  - RTO: 2 hours
- **Graph DB:**
  - RPO: 1 hour
  - RTO: 4 hours
- **Object Store (docs/deliverables):**
  - RPO: 0 (versioned, replicated) where supported
  - RTO: 4 hours
- **Audit store:**
  - RPO: 15 minutes
  - RTO: 2 hours

### 7.2 Backup Requirements
- Daily full backups + continuous WAL for Postgres
- Daily snapshots for Graph DB
- Object store versioning + lifecycle policies
- Quarterly restore drills (minimum)

### 7.3 DR Drills
- Twice per year: failover simulation
- Validate:
  - tenant isolation
  - audit event continuity
  - run resumption behavior

---

## 8) Monitoring & Alerting

### 8.1 Golden Dashboards (Must exist)
- API availability/latency
- Ingestion throughput + error rates
- Queue depth/backlog
- Claim registry writes + validator rejects
- Sanad grading distribution drift
- Calc success rate + reproducibility checks
- Debate completion rate + max-round stops
- Deliverable generation success rate
- Audit event ingestion lag + coverage checks
- Integration health (CRM/docs/providers)

### 8.2 Core Alerts (Examples)
- API 5xx > threshold for 5 min (SEV-2)
- Ingestion failure rate > 2% for 15 min (SEV-2)
- OCR queue time p95 > 60 min for 30 min (SEV-3)
- Audit ingestion lag > 5 min (SEV-2)
- Missing audit events for mutating endpoint detected (SEV-1)
- No‑Free‑Facts validator failure in deliverables pipeline (SEV-1)
- Tenant isolation violation signal (SEV-1)
- Calc reproducibility check failure > 0.1% in 24h (SEV-2)

---

## 9) Runbooks (Operational Playbooks)

Each runbook includes: **Detection → Triage → Containment → Recovery → Verification → Postmortem**

### RB‑01: API Outage / Elevated 5xx
**Detection:** API 5xx spike, availability SLO breach.  
**Triage:**
1. Check gateway health, auth provider, DB connectivity.
2. Identify failing endpoints (top 5).
3. Inspect last deploys and config changes.  
**Containment:**
- Roll back last deployment if correlated.
- Rate limit abusive traffic patterns.
- Scale API pods/instances.  
**Recovery:**
- Restore DB connections, fix config.
- Verify tenant routing logic.  
**Verification:**
- p95 latency back within SLO
- 5xx rate below threshold
- Smoke tests: create deal, list claims, run snapshot  
**Postmortem:** required for SEV-1/2.

### RB‑02: Ingestion Pipeline Failure (Parsing/OCR)
**Detection:** ingestion failure rate > SLO; queue backlog growing.  
**Triage:**
1. Identify doc types failing.
2. Check OCR worker saturation and CPU/memory limits.
3. Verify object store access and antivirus scan.  
**Containment:**
- Pause auto-ingest for new documents if needed.
- Route OCR to separate pool; increase concurrency.  
**Recovery:**
- Fix parser bug, patch extractor rules.
- Requeue failed docs (idempotent).  
**Verification:**
- Ingestion success returns to ≥99%
- Backlog drains below threshold

### RB‑03: Claim Validator Spike (No‑Free‑Facts / Schema Reject)
**Detection:** high rate of rejected claims or deliverable validation failures.  
**Triage:**
1. Determine whether rejects come from:
   - extraction service
   - agent outputs
   - deliverables generator
2. Inspect recent prompt/validator changes.  
**Containment:**
- Freeze prompt updates; revert last changes.
- Force “SUBJECTIVE” labeling for certain outputs if appropriate (temporary).  
**Recovery:**
- Fix schema mismatch
- Re-run affected pipeline stages  
**Verification:**
- Deliverables pass No‑Free‑Facts gate
- Claim creation success stabilizes

### RB‑04: Sanad Graph Degradation (Graph DB Slow/Down)
**Detection:** Sanad retrieval p95 > 1.2s; graph timeouts; grading stalled.  
**Triage:**
1. Check graph DB CPU/memory, connection pool.
2. Identify heavy queries (hotspots).
3. Check index coverage on node/edge labels.  
**Containment:**
- Reduce query fanout; enable caches (tenant-scoped).
- Rate limit Sanad map visualization requests.  
**Recovery:**
- Scale graph DB; add indexes; optimize traversal depth.
- If needed, serve from materialized Sanad views in Postgres.  
**Verification:**
- p95 restored; grading pipeline unblocked.

### RB‑05: Calc Service Failures / Reproducibility Issues
**Detection:** calc run failure rate; reproducibility checks fail.  
**Triage:**
1. Identify calc_name + version causing issue.
2. Check dependency changes / environment drift.
3. Validate input claims and grades.  
**Containment:**
- Block affected calculators from IC outputs.
- Force `blocked_for_ic=true` and create defects.  
**Recovery:**
- Roll back calc version.
- Re-run with pinned env and formula hash.  
**Verification:**
- reproducibility hash stable
- outputs match golden tests

### RB‑06: Debate Sessions Stuck / Max-Rounds Exceeded
**Detection:** debate completion rate below 98%, many stop_reason=MAX_ROUNDS.  
**Triage:**
1. Identify which role is looping (advocate vs breaker).
2. Check evidence exhaustion vs missing retrieval.
3. Verify Muḥāsabah gate rejects.  
**Containment:**
- Reduce max_rounds temporarily.
- Require Evidence Call stage before continuing.
- Escalate to human review for unresolved issues.  
**Recovery:**
- Patch prompts to focus on claim-level disputes.
- Improve retrieval for missing claims.  
**Verification:**
- completion rate restored; stable dissent preserved rather than loops.

### RB‑07: Deliverable Generation Failures (PDF/DOCX)
**Detection:** deliverable success rate < 99%.  
**Triage:**
1. Identify failing templates (IC memo vs snapshot).
2. Check object store write permissions and URL signing.
3. Confirm claim/calc references present.  
**Containment:**
- Fall back to JSON deliverables temporarily.
- Disable large graphs in PDF if causing timeouts.  
**Recovery:**
- Patch generator; rerun with idempotency keys.  
**Verification:**
- deliverables ready; access controls correct.

### RB‑08: Audit Ingestion Lag / Missing Audit Events
**Detection:** audit lag > 5 minutes or coverage < 100%.  
**Triage:**
1. Validate audit middleware on API gateway.
2. Check audit store availability and queue.  
**Containment:**
- Pause high-risk operations (overrides, IC export) if audit integrity compromised.  
**Recovery:**
- Restore audit pipeline; backfill events from request logs where possible.
- If backfill impossible → SEV-1 and compliance escalation.  
**Verification:**
- coverage restored; integrity checks pass.

### RB‑09: Integration Provider Outage (CRM / Docs / Enrichment)
**Detection:** integration errors spike; webhook failures.  
**Triage:**
1. Identify provider and failing endpoints.
2. Check rate limits and auth token expiry.  
**Containment:**
- Switch to polling fallback (if applicable).
- Delay enrichment steps; mark as “stale” defects.  
**Recovery:**
- Re-auth integration, rotate tokens.
- Reconcile missed events using provider replay APIs.  
**Verification:**
- sync resumes; audit shows recovered events.

### RB‑10: Security Incident — Tenant Isolation / Exfiltration Suspected
**Detection:** isolation alarm, unusual access patterns, DLP trigger.  
**Immediate actions (SEV-1):**
1. Contain: disable affected credentials, freeze exports, revoke tokens.
2. Preserve evidence: logs, traces, audit events, snapshots.
3. Notify security/compliance leads.  
**Recovery:**
- Root cause fix (RBAC bug, misconfigured RLS, cache keying).
- Validate isolation tests across tenants.
- Document regulatory/customer notifications if required.  
**Verification:**
- isolation test suite passes
- audit integrity verified

---

## 10) Operational Readiness Checklist (Go-Live Gate)

**Must be true before production launch:**
- All SLO dashboards exist and are reviewed
- Paging alerts configured for SEV‑1/2
- Backup/restore tested successfully (Postgres + object store)
- DR failover drill completed at least once
- Audit coverage tests pass for all mutating endpoints
- No‑Free‑Facts validator enforced at deliverable export
- Muḥāsabah gate enforced in debate pipeline
- Runbooks published and on-call rotation established

---

## 11) Appendix: SLO Ownership and Review Cadence

- Weekly SLO review meeting (SRE + backend + product)
- Monthly error budget review and release policy adjustment
- Quarterly DR and restore drill
- Quarterly audit/compliance review (SOC2 readiness)

