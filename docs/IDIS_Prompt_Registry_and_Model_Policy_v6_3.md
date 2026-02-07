# IDIS Prompt Registry & Model Policy (v6.3)
**Version:** 6.3 (derived from IDIS v6.3 FINAL)  
**Date:** 2026-01-06  
**Status:** Normative baseline for prompt/model governance  
**Audience:** Data/ML, Backend, SRE, Security/Compliance, Product

---

## 0) Purpose

IDIS’s trust invariants (No‑Free‑Facts, Sanad integrity, Muḥāsabah gating, deterministic numerics, auditable debate) depend on **prompts**, **tools**, and **model selection** behaving deterministically within constraints.

This document defines:
- A **Prompt Registry** (IDs, semver, ownership, lifecycle)
- Model selection and fallback rules
- Tool policies (what LLMs may and may not do)
- Validation gates for promotion
- Rollback procedures
- Audit requirements

This is required for enterprise readiness (SOC2/ISO27001 trajectory) because prompt/model changes can materially affect outputs.

---

## 1) Non‑negotiable Principles

1. **Prompts are production code**  
   They require versioning, testing, approvals, and rollback.

2. **No prompt may weaken trust gates**  
   Prompts may not instruct the system to bypass:
   - No‑Free‑Facts
   - Muḥāsabah validator rules
   - Calc‑Sanad determinism
   - human gates and overrides
   - audit logging

3. **Separation of responsibilities**
   - LLMs interpret, reason, and draft narratives.
   - Deterministic engines compute numerics.
   - Validators enforce constraints.
   - Humans approve overrides.

4. **Every prompt change is audited**
   A prompt promotion produces an audit event with:
   - prompt_id, old_version, new_version
   - owner
   - reason
   - evaluation results reference
   - approvals

---

## 2) Prompt Registry Overview

### 2.1 Required Fields (PromptArtifact)
Each prompt stored as an immutable artifact:

- `prompt_id` (stable identifier)
- `name`
- `version` (SemVer: MAJOR.MINOR.PATCH)
- `status` (DRAFT | STAGING | PROD | DEPRECATED)
- `owner` (team + person)
- `created_at`, `updated_at`
- `change_summary`
- `risk_class` (LOW | MEDIUM | HIGH)
- `model_requirements`:
  - min context window
  - tool calling support
  - JSON-mode support (if required)
- `tool_contracts[]` (list of tools the prompt is allowed to call)
- `input_schema_ref` / `output_schema_ref` (JSON schemas)
- `validation_gates_required` (Gate 1/2/3/4 from evaluation harness)
- `fallback_policy` (allowed fallback models)
- `evaluation_results_ref` (immutable link)
- `security_notes` (PII exposure risks, redaction rules)

### 2.2 Storage
- Store in Git (preferred) as:
  - `prompts/<prompt_id>/<version>/prompt.md`
  - `prompts/<prompt_id>/<version>/metadata.json`
- Tag releases in Git; optionally store artifacts in object store for immutable references.

### 2.3 Prompt Types
- **System prompts** (highest privilege): strict; small; rarely change
- **Role prompts** (agent role definitions): more frequent changes
- **Task prompts** (specific deliverables): templated
- **Validator prompts** (Muḥāsabah gate assistants): must be deterministic-output oriented

---

## 3) Canonical Prompt Set (v6.3 minimum)

### 3.1 Ingestion & Extraction
- `EXTRACT_CLAIMS_V1`
- `CLASSIFY_DOC_V1`
- `ENTITY_RESOLUTION_V1`

### 3.2 Sanad & Verification
- `SANAD_GRADER_V1`
- `DEFECT_DETECTOR_V1`
- `MATN_CHECKER_V1` (content integrity checks)

### 3.3 Deterministic Calc Narrative Prompts
- `CALC_INTERPRETER_V1` (drafts narrative from deterministic outputs; no new numbers)

### 3.4 Specialist Agents (Layer 2 — IC; Future)

> **Layer:** Layer 2 (IC). These prompts are **not implemented**. They are placeholder registry entries for the future Investment Committee mode. Layer 2 requires Phase 7.C enrichment connectors.

| Prompt ID | Layer | Inputs | Output Schema | Status |
|-----------|-------|--------|---------------|--------|
| `AGENT_FINANCIAL_V1` | Layer 2 (IC) | Validated Evidence Package + enrichment context | IC analysis record (TBD) | Placeholder |
| `AGENT_MARKET_V1` | Layer 2 (IC) | Validated Evidence Package + enrichment context | IC analysis record (TBD) | Placeholder |
| `AGENT_TEAM_V1` | Layer 2 (IC) | Validated Evidence Package + enrichment context | IC analysis record (TBD) | Placeholder |
| `AGENT_TECHNICAL_V1` | Layer 2 (IC) | Validated Evidence Package + enrichment context | IC analysis record (TBD) | Placeholder |
| `AGENT_TERMS_V1` | Layer 2 (IC) | Validated Evidence Package + enrichment context | IC analysis record (TBD) | Placeholder |
| `AGENT_RISK_V1` | Layer 2 (IC) | Validated Evidence Package + enrichment context | IC analysis record (TBD) | Placeholder |
| `AGENT_HISTORIAN_V1` | Layer 2 (IC) | Validated Evidence Package + enrichment context | IC analysis record (TBD) | Placeholder |
| `AGENT_SECTOR_V1` | Layer 2 (IC) | Validated Evidence Package + enrichment context | IC analysis record (TBD) | Placeholder (optional) |
| `IC_RISK_OFFICER_V1` | Layer 2 (IC) | Validated Evidence Package + enrichment context | IC analysis record (TBD) | Placeholder (if distinct from Layer 1 Risk Officer) |

### 3.5 Debate Roles (Layer 1 — Evidence Trust)

> **Layer:** Layer 1 (Evidence Trust). These are the current debate roles for evidence integrity validation.

- `DEBATE_ADVOCATE_V1`
- `DEBATE_SANAD_BREAKER_V1`
- `DEBATE_COUNTER_ADVOCATE_V1` (recommended)
- `DEBATE_ARBITER_V1`
- `DEBATE_OBSERVER_V1`

### 3.6 Validators (hard gates)
- `MUHASABAH_VALIDATOR_V1` (must produce strict JSON)
- `NO_FREE_FACTS_CHECKER_V1` (must output pass/fail + violations)

### 3.7 IC Mechanism Prompts (Layer 2 — IC; Future)

> **Layer:** Layer 2 (IC). These prompts are **not implemented**. They are placeholder registry entries for the future Investment Committee mechanism. Layer 2 requires Phase 7.C enrichment connectors and consumes the Validated Evidence Package from Layer 1.

| Prompt ID | Layer | Inputs | Output Schema | Status |
|-----------|-------|--------|---------------|--------|
| `IC_ADVOCATE_THESIS_V1` | Layer 2 (IC) | Validated Evidence Package + enrichment context | IC thesis record (TBD) | Placeholder |
| `IC_CHALLENGER_V1` | Layer 2 (IC) | Validated Evidence Package + IC thesis | IC challenge record (TBD) | Placeholder |
| `IC_ARBITER_V1` | Layer 2 (IC) | Validated Evidence Package + IC thesis + challenges | IC-Ready Package: GO / CONDITIONAL / NO-GO (TBD) | Placeholder |

### 3.8 Deliverables
- `IC_MEMO_GENERATOR_V1`
- `SCREENING_SNAPSHOT_GENERATOR_V1`
- `TRUTH_DASHBOARD_GENERATOR_V1`
- `DILIGENCE_QNA_GENERATOR_V1`
- `DECLINE_DRAFT_GENERATOR_V1` (internal)

---

## 4) Prompt Versioning Policy (SemVer)

### 4.1 Version rules
- **PATCH**: wording changes that do not alter output schema or behavior materially
- **MINOR**: improves performance but keeps output schema stable
- **MAJOR**: changes output schema, gating behavior, or tool policy; requires full regression + approvals

### 4.2 Deprecation
- Deprecate prompts only with:
  - replacement prompt_id/version
  - migration plan
  - audit event
- Never delete prompts used in past IC deliverables (auditability).

---

## 5) Output Schemas and Strictness

### 5.1 JSON-Only Outputs for Validators
Validator prompts must output strict JSON, never prose. Examples:
- Muḥāsabah validator returns `{ "pass": true, "reasons": [], "violations": [] }`
- No‑Free‑Facts checker returns `{ "pass": false, "violations": [ ... ] }`

### 5.2 Schema enforcement
- Output validated via JSON Schema at runtime.
- Fail closed: invalid JSON or schema mismatch → reject output and re-run with fallback model or safe mode.

---

## 6) Tool Policy (LLM Capabilities Boundaries)

### 6.1 Allowed behaviors
LLMs may:
- interpret evidence already in claim registry
- generate structured arguments referencing claim_id/calc_id
- propose diligence questions and cure protocols
- summarize debate with citations to claim IDs

### 6.2 Disallowed behaviors (must be prevented)
LLMs must not:
- invent facts not present in claim registry/enrichment records
- compute or adjust numbers (must call deterministic calc)
- bypass human gates or overrides
- dump raw documents or large excerpts by default

### 6.3 Evidence Budget
Each prompt has an “evidence budget”:
- max number of doc spans and tokens retrieved
- encourages structured claim references over raw text

---

## 7) Model Policy

### 7.1 Model Classes
Define 3 model classes:
- **Reasoning model**: arbitration, conflict resolution, debate
- **Fast model**: extraction triage, light summarization
- **Verifier model**: schema/validation, strict JSON outputs

### 7.2 Model Selection Rules (Default)
- Arbiter and Sanad Breaker: reasoning model
- Extractors: fast model + structured output
- Validators: verifier model with strict JSON reliability

### 7.3 Fallback Policy
For each prompt:
- Primary model
- Secondary model
- Safe fallback (minimal features but stable)

Fallback triggers:
- invalid JSON
- tool call failure
- repeated hallucination flags
- latency SLA breach

All fallbacks must be logged as audit events.

---

## 8) Promotion Pipeline (Release Gates)

This section references the evaluation harness gates.

### 8.1 Promotion Steps
1. Submit PR with:
   - prompt changes
   - updated metadata.json
   - updated tests or expected outputs (if required)
2. Run Gate 1: structural trust checks
3. Run Gate 2: quality checks
4. Run Gate 3: full regression (if HIGH risk class)
5. Gate 4: human review for IC memo changes
6. Approvals:
   - prompt owner
   - security/compliance reviewer for HIGH risk prompts
7. Promote to PROD by updating a registry pointer:
   - `prompts/registry.prod.json`

### 8.2 Required Gates by Risk Class
- LOW: Gate 1 + automated review
- MEDIUM: Gate 1 + Gate 2
- HIGH: Gate 1 + Gate 2 + Gate 3 + Gate 4 + security sign-off

---

## 9) Rollback Policy

### 9.1 Rollback triggers
- No‑Free‑Facts violation in prod
- Muḥāsabah pass rate drops below threshold
- Debate max-round stops spike
- IC memo regressions (human reported)
- security incident

### 9.2 Rollback procedure (normative)
1. Flip registry pointer to previous prompt version (atomic)
2. Emit audit event: `prompt.version.rolledback` (with version, reason, actor, rollback_target, incident_ticket_id)
3. Re-run affected runs if needed
4. Open incident ticket + postmortem for SEV-1/2

---

## 10) Audit Requirements

### 10.1 Prompt lifecycle events
Audit event types (aligned with Go-Live checklist §4.4):
- `prompt.version.promoted` — records version, risk_class, approver, gate_results, evaluation_results_ref, evaluation_results_sha256
- `prompt.version.rolledback` — records version, reason, actor, rollback_target, incident_ticket_id
- `prompt.version.retired` — records version, reason, actor
- `model.policy.updated`

Each must include:
- prompt_id, version
- actor identity
- approvals (where applicable)
- evaluation_results_ref (for promotion)
- sha256 hash of evidence artifacts (for promotion)

---

## 11) Implementation Checklist

- Create `prompts/` directory structure with metadata
- Implement registry service or registry JSON files:
  - `registry.dev.json`
  - `registry.staging.json`
  - `registry.prod.json`
- Implement runtime prompt loader:
  - fetch by prompt_id + env
  - validate schema refs
  - log prompt version in every output artifact
- Integrate promotion pipeline into CI/CD
- Integrate rollback into incident response

---

## 12) Definition of Done

Prompt registry and model policy are production-ready when:
- all prompts used in pipeline are versioned and referenced in outputs
- validators enforce strict JSON
- promotion gates are implemented
- rollback is one-click and audited
- prompt changes cannot bypass trust invariants

---

## 13) Revision History

| Date | Version | Changes |
|------|---------|--------|
| 2026-01-06 | 1.0 | Initial creation (v6.3 baseline) |
| 2026-02-07 | 1.1 | Added two-layer debate architecture annotations: §3.4 Specialist Agents marked as Layer 2 (IC) placeholders with input/output schema table. §3.5 Debate Roles annotated as Layer 1 (Evidence Trust). Added §3.7 IC Mechanism Prompts (Layer 2 placeholders: IC_ADVOCATE_THESIS, IC_CHALLENGER, IC_ARBITER). Renumbered §3.7 Deliverables to §3.8. No implementation changes. |
