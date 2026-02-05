# Pipeline Orchestration Specification

**Version:** 1.0.0  
**Date:** 2026-02-05  
**Status:** Build Spec — **HIGHEST IMPACT (E2E Glue)**  
**Phase:** Gate 3 Unblock

---

## 1. Overview

This document specifies the **pipeline orchestration** that connects all IDIS components into an end-to-end flow. This is the critical "glue" that enables Gate 3 execution.

---

## 2. Pipeline DAG

```
                              ┌─────────────────┐
                              │  deal_submitted │
                              └────────┬────────┘
                                       │
                              ┌────────▼────────┐
                              │ parse_documents │
                              └────────┬────────┘
                                       │
                              ┌────────▼────────┐
                              │ extract_claims  │
                              └────────┬────────┘
                                       │
                              ┌────────▼────────┐
                              │   grade_sanad   │
                              └────────┬────────┘
                                       │
                              ┌────────▼────────┐
                              │ run_calculations│
                              └────────┬────────┘
                                       │
                              ┌────────▼────────┐
                              │   enrichment    │ (optional)
                              └────────┬────────┘
                                       │
                              ┌────────▼────────┐
                              │ trigger_debate  │
                              └────────┬────────┘
                                       │
                              ┌────────▼────────┐
                              │ muhasabah_gate  │ ◀── HARD GATE
                              └────────┬────────┘
                                       │
                              ┌────────▼────────┐
                              │generate_deliver │
                              └────────┬────────┘
                                       │
                              ┌────────▼────────┐
                              │   human_gate    │ (if required)
                              └────────┬────────┘
                                       │
                              ┌────────▼────────┐
                              │     export      │
                              └─────────────────┘
```

---

## 3. Deal Lifecycle State Machine

### 3.1 States

| State | Description | Transitions |
|-------|-------------|-------------|
| `CREATED` | Deal record created, no documents | → `INGESTING` |
| `INGESTING` | Documents being uploaded/parsed | → `INGESTED`, `FAILED` |
| `INGESTED` | All documents parsed | → `EXTRACTING` |
| `EXTRACTING` | Claims being extracted | → `EXTRACTED`, `FAILED` |
| `EXTRACTED` | Claims in registry | → `GRADING` |
| `GRADING` | Sanad chains being built | → `GRADED`, `FAILED` |
| `GRADED` | All claims have Sanad grades | → `CALCULATING` |
| `CALCULATING` | Deterministic calcs running | → `CALCULATED`, `FAILED` |
| `CALCULATED` | Calc outputs available | → `ENRICHING`, `DEBATING` |
| `ENRICHING` | External enrichment in progress | → `ENRICHED`, `CALCULATED` |
| `ENRICHED` | Enrichment complete | → `DEBATING` |
| `DEBATING` | Multi-agent debate running | → `DEBATED`, `FAILED` |
| `DEBATED` | Debate complete, Muḥāsabah passed | → `GENERATING` |
| `GENERATING` | Deliverables being generated | → `GENERATED`, `FAILED` |
| `GENERATED` | Deliverables ready | → `HUMAN_REVIEW`, `IC_READY` |
| `HUMAN_REVIEW` | Awaiting human gate approval | → `IC_READY`, `BLOCKED` |
| `IC_READY` | Ready for IC presentation | → `EXPORTED` |
| `BLOCKED` | Critical defect, requires resolution | → `EXTRACTING`, `GRADING` |
| `FAILED` | Pipeline failure | → (restart from last good state) |
| `EXPORTED` | Deliverables exported | Terminal |

### 3.2 State Transition Diagram

```
CREATED ──▶ INGESTING ──▶ INGESTED ──▶ EXTRACTING ──▶ EXTRACTED
                │                            │
                ▼                            ▼
             FAILED ◀────────────────────  FAILED
                │                            │
                └────── (retry) ─────────────┘

EXTRACTED ──▶ GRADING ──▶ GRADED ──▶ CALCULATING ──▶ CALCULATED
                 │                        │
                 ▼                        ▼
              BLOCKED ◀───────────────  FAILED

CALCULATED ──┬──▶ ENRICHING ──▶ ENRICHED ──┐
             │                              │
             └──────────────────────────────┼──▶ DEBATING
                                            │
DEBATING ──▶ DEBATED ──▶ GENERATING ──▶ GENERATED
    │                         │               │
    ▼                         ▼               ▼
 FAILED                    FAILED       HUMAN_REVIEW
                                            │
                                   ┌────────┴────────┐
                                   ▼                 ▼
                               IC_READY          BLOCKED
                                   │
                                   ▼
                               EXPORTED
```

---

## 4. Run Model

### 4.1 What is a "Run"?

A **Run** is a single execution of the pipeline for a deal. Each run:
- Has a unique `run_id`
- Tracks state transitions
- Captures all intermediate outputs
- Is idempotent (can be retried)
- Emits audit events at each step

### 4.2 Run Schema

```json
{
  "run_id": "uuid",
  "deal_id": "uuid",
  "tenant_id": "uuid",
  "status": "RUNNING|COMPLETED|FAILED|BLOCKED",
  "current_state": "DEBATING",
  "started_at": "2026-02-05T10:00:00Z",
  "completed_at": null,
  "steps": [
    {
      "step_id": "uuid",
      "step_name": "parse_documents",
      "status": "COMPLETED",
      "started_at": "...",
      "completed_at": "...",
      "input_hash": "sha256:...",
      "output_hash": "sha256:...",
      "artifacts": ["doc_id_1", "doc_id_2"],
      "error": null
    }
  ],
  "config": {
    "skip_enrichment": false,
    "max_debate_rounds": 5,
    "human_gate_required": true
  },
  "trigger": "API|WEBHOOK|SCHEDULED|MANUAL",
  "triggered_by": "actor_id"
}
```

### 4.3 `/v1/deals/{dealId}/runs` Endpoint

**POST** — Start a new pipeline run
```json
{
  "config": {
    "skip_enrichment": false,
    "priority": "NORMAL"
  }
}
```

**Response:**
```json
{
  "run_id": "uuid",
  "status": "RUNNING",
  "started_at": "2026-02-05T10:00:00Z"
}
```

**GET** — List runs for a deal
```json
{
  "items": [...],
  "cursor": "..."
}
```

### 4.4 `/v1/runs/{runId}` Endpoint

**GET** — Get run status and details
```json
{
  "run_id": "uuid",
  "deal_id": "uuid",
  "status": "RUNNING",
  "current_state": "DEBATING",
  "progress": {
    "completed_steps": 5,
    "total_steps": 9,
    "percentage": 55
  },
  "steps": [...]
}
```

---

## 5. Idempotency + Resumability

### 5.1 Idempotency Rules

| Step | Idempotency Key | Behavior on Replay |
|------|-----------------|-------------------|
| `parse_documents` | `doc_id + sha256` | Skip if already parsed |
| `extract_claims` | `doc_id + chunk_hash` | Skip if claims exist |
| `grade_sanad` | `claim_id` | Recompute (deterministic) |
| `run_calculations` | `calc_type + input_hash` | Return cached if match |
| `trigger_debate` | `run_id + round` | Resume from last round |
| `generate_deliverables` | `run_id + type` | Regenerate (deterministic) |

### 5.2 Resumability

Pipeline can resume from any failed step:

```python
def resume_run(run_id: str) -> Run:
    run = get_run(run_id)
    last_completed = get_last_completed_step(run)
    next_step = get_next_step(last_completed)
    return execute_from_step(run, next_step)
```

### 5.3 Checkpoint Storage

Each step completion stores a checkpoint:

```json
{
  "run_id": "uuid",
  "step_name": "extract_claims",
  "checkpoint_id": "uuid",
  "state_snapshot": {...},
  "output_refs": [...],
  "timestamp": "..."
}
```

---

## 6. Audit Event Emission

### 6.1 Required Events per State Transition

| Transition | Event Type | Required Fields |
|------------|------------|-----------------|
| `CREATED → INGESTING` | `run.started` | run_id, deal_id, trigger |
| `INGESTING → INGESTED` | `documents.parsed` | doc_ids, span_count |
| `INGESTED → EXTRACTING` | `extraction.started` | run_id, doc_count |
| `EXTRACTING → EXTRACTED` | `claims.extracted` | claim_count, confidence_dist |
| `EXTRACTED → GRADING` | `grading.started` | run_id, claim_count |
| `GRADING → GRADED` | `sanad.graded` | grade_distribution |
| `GRADED → CALCULATING` | `calc.started` | run_id, calc_types |
| `CALCULATING → CALCULATED` | `calc.completed` | calc_ids, repro_hashes |
| `CALCULATED → DEBATING` | `debate.started` | run_id, debate_id |
| `DEBATING → DEBATED` | `debate.completed` | stop_reason, round_count |
| `DEBATED → GENERATING` | `deliverables.started` | run_id, types |
| `GENERATED → IC_READY` | `run.completed` | run_id, duration |
| Any → `FAILED` | `run.failed` | step, error_code, error_msg |
| Any → `BLOCKED` | `run.blocked` | reason, blocking_defects |

### 6.2 Audit Event Schema

```json
{
  "event_id": "uuid",
  "event_type": "run.started",
  "tenant_id": "uuid",
  "actor_id": "uuid",
  "resource_type": "run",
  "resource_id": "uuid",
  "action": "create",
  "request_id": "uuid",
  "timestamp": "2026-02-05T10:00:00Z",
  "payload": {
    "deal_id": "uuid",
    "trigger": "API",
    "config": {...}
  }
}
```

### 6.3 Fail-Closed Audit

```python
async def transition_state(run: Run, new_state: str) -> Run:
    event = build_audit_event(run, new_state)
    
    # Audit write MUST succeed before state transition
    try:
        await audit_sink.emit(event)
    except AuditWriteError as e:
        # Fail closed: no transition without audit
        raise PipelineError(
            code="AUDIT_FAILURE",
            message="Cannot transition state: audit write failed",
            original_error=e
        )
    
    run.state = new_state
    await run_repo.save(run)
    return run
```

---

## 7. Retry/Backoff + Rate Limiting

### 7.1 LLM Rate Limiting

```python
LLM_RATE_LIMITS = {
    "fast_model": {
        "requests_per_minute": 60,
        "tokens_per_minute": 100_000,
    },
    "reasoning_model": {
        "requests_per_minute": 20,
        "tokens_per_minute": 50_000,
    },
    "verifier_model": {
        "requests_per_minute": 30,
        "tokens_per_minute": 30_000,
    },
}
```

### 7.2 Retry Policy

```python
RETRY_CONFIG = {
    "parse_documents": {
        "max_retries": 3,
        "backoff_base": 2.0,
        "backoff_max": 60.0,
        "retry_on": ["TIMEOUT", "RATE_LIMIT"],
    },
    "extract_claims": {
        "max_retries": 3,
        "backoff_base": 5.0,
        "backoff_max": 120.0,
        "retry_on": ["LLM_ERROR", "INVALID_JSON", "RATE_LIMIT"],
    },
    "trigger_debate": {
        "max_retries": 2,
        "backoff_base": 10.0,
        "backoff_max": 300.0,
        "retry_on": ["LLM_ERROR", "RATE_LIMIT"],
    },
}
```

### 7.3 Backoff Implementation

```python
async def with_retry(
    func: Callable,
    config: RetryConfig,
) -> Any:
    for attempt in range(config.max_retries + 1):
        try:
            return await func()
        except RetryableError as e:
            if attempt == config.max_retries:
                raise
            
            delay = min(
                config.backoff_base * (2 ** attempt),
                config.backoff_max
            )
            await asyncio.sleep(delay)
            
            # Emit retry audit event
            await emit_audit_event("step.retry", {
                "attempt": attempt + 1,
                "delay_seconds": delay,
                "error": str(e),
            })
```

### 7.4 Storage Rate Limiting

```python
STORAGE_RATE_LIMITS = {
    "object_store": {
        "writes_per_second": 100,
        "reads_per_second": 500,
    },
    "postgres": {
        "connections_per_tenant": 10,
        "transactions_per_second": 100,
    },
}
```

---

## 8. Connector Rate Limiting

### 8.1 External Connector Limits

| Connector | Rate Limit | Backoff |
|-----------|------------|---------|
| PitchBook | 100 req/hour | Exponential |
| Crunchbase | 200 req/hour | Exponential |
| LinkedIn | 50 req/hour | Fixed 60s |
| SEC EDGAR | 10 req/sec | None |

### 8.2 Connector Failure Handling

```python
CONNECTOR_FAILURE_POLICY = {
    "pitchbook": {
        "on_failure": "SKIP",  # Continue without enrichment
        "fallback": None,
        "alert": True,
    },
    "crunchbase": {
        "on_failure": "SKIP",
        "fallback": "pitchbook",  # Try alternate source
        "alert": True,
    },
}
```

---

## 9. Module Structure

```
src/idis/pipeline/
├── __init__.py
├── orchestrator.py      # Main pipeline coordinator
├── state_machine.py     # Deal state transitions
├── run_manager.py       # Run lifecycle management
├── steps/
│   ├── __init__.py
│   ├── base.py          # Abstract step
│   ├── parse_step.py
│   ├── extract_step.py
│   ├── grade_step.py
│   ├── calc_step.py
│   ├── enrich_step.py
│   ├── debate_step.py
│   ├── deliver_step.py
│   └── export_step.py
├── checkpoints/
│   ├── __init__.py
│   └── checkpoint_store.py
└── rate_limit/
    ├── __init__.py
    ├── llm_limiter.py
    └── connector_limiter.py
```

---

## 10. Acceptance Criteria

### 10.1 Functional Requirements
- [ ] Pipeline executes all steps in correct order
- [ ] State transitions are atomic and audited
- [ ] Runs can be resumed from any checkpoint
- [ ] Rate limiting prevents API abuse
- [ ] Failed steps retry with backoff
- [ ] Muḥāsabah gate blocks invalid outputs

### 10.2 Quality Requirements
- [ ] ≥ 95% run completion rate on GDBS-S
- [ ] ≥ 98% run completion rate on GDBS-F
- [ ] Mean run time < 5 minutes for standard deal
- [ ] 100% audit event coverage

### 10.3 Test Hooks

```python
# Unit tests
def test_state_machine_transitions()
def test_idempotency_key_generation()
def test_checkpoint_storage()
def test_retry_backoff()

# Integration tests
def test_full_pipeline_e2e()
def test_resume_from_failure()
def test_rate_limit_enforcement()
def test_audit_event_emission()

# GDBS tests
def test_gdbs_s_pipeline_completion()
def test_gdbs_f_pipeline_completion()
def test_gdbs_a_failure_detection()
```
