# Agent Framework and Tools Specification

**Version:** 1.0.0  
**Date:** 2026-02-05  
**Status:** Build Spec  
**Reference:** 03_IDIS_LangGraph_Skeleton_v6_3.md

---

## 1. Overview

This document specifies the agent framework for IDIS debate orchestration, including agent node I/O schemas, tool contracts, governance rules, and Muḥāsabah record generation.

---

## 2. Agent Node Architecture

### 2.1 Base Agent Interface

```python
class AgentNode(ABC):
    """Base class for all debate agents."""
    
    agent_id: str
    role: AgentRole
    model_class: ModelClass
    prompt_id: str
    permitted_tools: list[str]
    
    @abstractmethod
    async def execute(
        self,
        state: DebateState,
        context: AgentContext,
    ) -> AgentOutput:
        """Execute agent logic and return output with MuḥāsabahRecord."""
        pass
    
    @abstractmethod
    def validate_output(self, output: AgentOutput) -> ValidationResult:
        """Validate output meets Muḥāsabah requirements."""
        pass
```

### 2.2 Agent Roles

| Role | ID | Purpose | Model Class |
|------|----|---------|-------------|
| Advocate | `advocate` | Present investment thesis | Reasoning |
| Sanad Breaker | `sanad_breaker` | Challenge evidence chains | Reasoning |
| Contradiction Finder | `contradiction_finder` | Find cross-doc inconsistencies | Reasoning |
| Risk Officer | `risk_officer` | Identify downside risks | Reasoning |
| Arbiter | `arbiter` | Rule on challenges, manage debate | Reasoning |

---

## 3. Agent Node I/O Schemas

### 3.1 DebateState (Input to All Agents)

```json
{
  "type": "object",
  "required": ["deal_id", "round_number", "messages", "claim_registry_ref"],
  "properties": {
    "deal_id": {"type": "string", "format": "uuid"},
    "debate_id": {"type": "string", "format": "uuid"},
    "round_number": {"type": "integer", "minimum": 1},
    "max_rounds": {"type": "integer", "default": 5},
    "claim_registry_ref": {"type": "string"},
    "sanad_graph_ref": {"type": "string"},
    "messages": {
      "type": "array",
      "items": {"$ref": "#/definitions/DebateMessage"}
    },
    "open_questions": {
      "type": "array",
      "items": {"type": "string"}
    },
    "utility_scores": {
      "type": "object",
      "additionalProperties": {"type": "number"}
    },
    "arbiter_decisions": {
      "type": "array",
      "items": {"$ref": "#/definitions/ArbiterDecision"}
    },
    "consensus_reached": {"type": "boolean"},
    "stop_reason": {
      "type": "string",
      "enum": ["CONSENSUS", "STABLE_DISSENT", "EVIDENCE_EXHAUSTED", "MAX_ROUNDS", "CRITICAL_DEFECT", null]
    },
    "preserved_dissent": {
      "type": "array",
      "items": {"$ref": "#/definitions/DissentRecord"}
    }
  }
}
```

### 3.2 AgentOutput (Output from All Agents)

```json
{
  "type": "object",
  "required": ["agent_id", "role", "content", "claim_refs", "muhasabah_record"],
  "properties": {
    "agent_id": {"type": "string"},
    "role": {"type": "string"},
    "round_number": {"type": "integer"},
    "content": {"type": "string"},
    "structured_content": {"type": "object"},
    "claim_refs": {
      "type": "array",
      "items": {"type": "string", "format": "uuid"}
    },
    "calc_refs": {
      "type": "array",
      "items": {"type": "string", "format": "uuid"}
    },
    "tool_calls": {
      "type": "array",
      "items": {"$ref": "#/definitions/ToolCall"}
    },
    "muhasabah_record": {"$ref": "#/definitions/MuhasabahRecord"},
    "timestamp": {"type": "string", "format": "date-time"}
  }
}
```

### 3.3 MuḥāsabahRecord (Required for All Outputs)

```json
{
  "type": "object",
  "required": ["supported_claim_ids", "confidence"],
  "properties": {
    "supported_claim_ids": {
      "type": "array",
      "items": {"type": "string", "format": "uuid"},
      "minItems": 0,
      "description": "MUST be non-empty for factual assertions"
    },
    "supported_calc_ids": {
      "type": "array",
      "items": {"type": "string", "format": "uuid"}
    },
    "evidence_summary": {"type": "string"},
    "counter_hypothesis": {
      "type": "string",
      "description": "Alternative explanation for the evidence"
    },
    "falsifiability_tests": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "test_description": {"type": "string"},
          "required_evidence": {"type": "string"},
          "pass_fail_rule": {"type": "string"}
        }
      },
      "description": "MUST be present if confidence > 0.50"
    },
    "uncertainties": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "uncertainty": {"type": "string"},
          "impact": {"type": "string"},
          "mitigation": {"type": "string"}
        }
      },
      "description": "MUST be present if source grade < A or Āḥād"
    },
    "failure_modes": {
      "type": "array",
      "items": {"type": "string"}
    },
    "confidence": {
      "type": "number",
      "minimum": 0,
      "maximum": 1
    },
    "confidence_justification": {"type": "string"}
  }
}
```

---

## 4. Tool Contracts

### 4.1 lookup_claim

**Purpose:** Retrieve claim details by ID

```python
@tool
async def lookup_claim(
    claim_id: str,
    include_sanad: bool = False,
    include_evidence: bool = False,
) -> ClaimLookupResult:
    """
    Retrieve a claim from the registry.
    
    Args:
        claim_id: UUID of the claim
        include_sanad: Include Sanad chain details
        include_evidence: Include evidence items
    
    Returns:
        ClaimLookupResult with claim, sanad, evidence
    
    Raises:
        ClaimNotFoundError: If claim_id doesn't exist
        TenantAccessError: If claim belongs to different tenant
    """
```

**Output Schema:**
```json
{
  "claim": {
    "claim_id": "uuid",
    "claim_text": "string",
    "claim_class": "FINANCIAL",
    "value_struct": {...},
    "claim_grade": "A",
    "claim_verdict": "VERIFIED"
  },
  "sanad": {...},
  "evidence": [...]
}
```

**Permitted Agents:** All

---

### 4.2 lookup_calc

**Purpose:** Retrieve calculation details by ID

```python
@tool
async def lookup_calc(
    calc_id: str,
    include_inputs: bool = True,
) -> CalcLookupResult:
    """
    Retrieve a deterministic calculation result.
    
    Args:
        calc_id: UUID of the calculation
        include_inputs: Include input claim details
    
    Returns:
        CalcLookupResult with calc, inputs, provenance
    """
```

**Output Schema:**
```json
{
  "calc": {
    "calc_id": "uuid",
    "calc_type": "IRR",
    "output": {...},
    "formula_hash": "sha256:...",
    "code_version": "1.2.3"
  },
  "inputs": [...],
  "calc_sanad": {...}
}
```

**Permitted Agents:** All

---

### 4.3 search_evidence

**Purpose:** Search for evidence supporting/contradicting a claim

```python
@tool
async def search_evidence(
    query: str,
    deal_id: str,
    claim_class: str | None = None,
    min_grade: str = "D",
    limit: int = 10,
) -> EvidenceSearchResult:
    """
    Search evidence items across deal documents.
    
    Args:
        query: Search query (semantic)
        deal_id: Scope to specific deal
        claim_class: Filter by claim class
        min_grade: Minimum source grade (A/B/C/D)
        limit: Max results to return
    
    Returns:
        List of matching evidence items with relevance scores
    """
```

**Output Schema:**
```json
{
  "results": [
    {
      "evidence_id": "uuid",
      "source_span": {...},
      "source_grade": "B",
      "relevance_score": 0.85,
      "text_excerpt": "..."
    }
  ],
  "total_count": 42
}
```

**Permitted Agents:** Sanad Breaker, Contradiction Finder, Risk Officer

---

### 4.4 flag_defect

**Purpose:** Flag a defect in an evidence chain

```python
@tool
async def flag_defect(
    claim_id: str,
    defect_type: DefectType,
    severity: DefectSeverity,
    description: str,
    evidence_refs: list[str],
    cure_protocol: CureProtocol,
) -> DefectResult:
    """
    Flag a defect in a claim's evidence chain.
    
    Args:
        claim_id: Claim with the defect
        defect_type: Type from taxonomy
        severity: FATAL/MAJOR/MINOR
        description: Explanation of the defect
        evidence_refs: Supporting evidence for defect
        cure_protocol: Recommended cure action
    
    Returns:
        Created defect record
    
    Audit:
        Emits defect.flagged event
    """
```

**Permitted Agents:** Sanad Breaker (must have evidence_refs)

---

### 4.5 request_human_review

**Purpose:** Escalate to human review

```python
@tool
async def request_human_review(
    claim_ids: list[str],
    reason: str,
    priority: str = "NORMAL",
    required_role: str = "ANALYST",
) -> HumanGateResult:
    """
    Create a human review gate.
    
    Args:
        claim_ids: Claims requiring review
        reason: Why human review is needed
        priority: LOW/NORMAL/HIGH/CRITICAL
        required_role: Minimum role for approval
    
    Returns:
        Human gate record
    
    Audit:
        Emits human_gate.created event
    """
```

**Permitted Agents:** Arbiter only

---

### 4.6 query_enrichment

**Purpose:** Query external enrichment data

```python
@tool
async def query_enrichment(
    entity_type: str,
    entity_name: str,
    data_points: list[str],
) -> EnrichmentResult:
    """
    Query enrichment connectors for external data.
    
    Args:
        entity_type: COMPANY/PERSON/MARKET
        entity_name: Name to query
        data_points: Specific data points needed
    
    Returns:
        Enrichment results with source attribution
    
    Note:
        Results are claims with source_grade = C or D
        Must be corroborated with primary sources
    """
```

**Permitted Agents:** Risk Officer, Contradiction Finder

---

## 5. Tool Permission Matrix

| Tool | Advocate | Sanad Breaker | Contradiction | Risk | Arbiter |
|------|----------|---------------|---------------|------|---------|
| `lookup_claim` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `lookup_calc` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `search_evidence` | ❌ | ✅ | ✅ | ✅ | ✅ |
| `flag_defect` | ❌ | ✅ | ❌ | ❌ | ❌ |
| `request_human_review` | ❌ | ❌ | ❌ | ❌ | ✅ |
| `query_enrichment` | ❌ | ❌ | ✅ | ✅ | ❌ |

---

## 6. Governance + Audit

### 6.1 Tool Call Audit Events

Every tool call emits:

```json
{
  "event_type": "agent.tool_call",
  "agent_id": "advocate",
  "tool_name": "lookup_claim",
  "tool_args": {...},
  "result_status": "SUCCESS",
  "result_hash": "sha256:...",
  "debate_id": "uuid",
  "round_number": 2,
  "timestamp": "..."
}
```

### 6.2 Tool Abuse Detection

```python
TOOL_ABUSE_RULES = {
    "max_calls_per_round": {
        "lookup_claim": 20,
        "lookup_calc": 10,
        "search_evidence": 5,
        "flag_defect": 3,
    },
    "flag_defect_requires": {
        "evidence_refs_min": 1,
        "description_min_chars": 50,
    },
}
```

### 6.3 Frivolous Challenge Detection

Arbiter must validate challenges:

```python
def validate_challenge(challenge: Challenge) -> ValidationResult:
    # Must reference specific claims
    if not challenge.claim_refs:
        return ValidationResult(
            valid=False,
            reason="Challenge must reference specific claim_ids"
        )
    
    # Must specify defect type
    if not challenge.defect_type:
        return ValidationResult(
            valid=False,
            reason="Challenge must specify defect type"
        )
    
    # Must have evidence
    if not challenge.evidence_refs:
        return ValidationResult(
            valid=False,
            reason="Challenge must cite evidence for claimed defect"
        )
    
    return ValidationResult(valid=True)
```

---

## 7. Muḥāsabah Record Generation

### 7.1 Structural Enforcement

Muḥāsabah is enforced structurally, not by prompts:

```python
class MuhasabahEnforcer:
    """Validates MuḥāsabahRecord meets requirements."""
    
    def validate(
        self,
        output: AgentOutput,
        context: AgentContext,
    ) -> MuhasabahValidationResult:
        record = output.muhasabah_record
        violations = []
        
        # Rule 1: No-Free-Facts
        if self._has_factual_assertions(output.content):
            if not record.supported_claim_ids:
                violations.append(MuhasabahViolation(
                    rule="NO_FREE_FACTS",
                    detail="Factual assertions without claim_ids"
                ))
        
        # Rule 2: Falsifiability
        if record.confidence > 0.50:
            if not record.falsifiability_tests:
                violations.append(MuhasabahViolation(
                    rule="FALSIFIABILITY_MISSING",
                    detail="High confidence without falsifiability tests"
                ))
        
        # Rule 3: Uncertainties
        if self._has_weak_sources(record.supported_claim_ids, context):
            if not record.uncertainties:
                violations.append(MuhasabahViolation(
                    rule="UNCERTAINTIES_MISSING",
                    detail="Weak sources without uncertainty acknowledgment"
                ))
        
        # Rule 4: Overconfidence
        if record.confidence > 0.80:
            if not record.uncertainties and not record.counter_hypothesis:
                violations.append(MuhasabahViolation(
                    rule="OVERCONFIDENCE",
                    detail="Very high confidence without uncertainty or counter-hypothesis"
                ))
        
        return MuhasabahValidationResult(
            valid=len(violations) == 0,
            violations=violations
        )
```

### 7.2 Fail-Closed Gate

```python
async def muhasabah_gate(output: AgentOutput) -> AgentOutput:
    """Hard gate: invalid Muḥāsabah = rejected output."""
    
    result = muhasabah_enforcer.validate(output, context)
    
    if not result.valid:
        # Emit audit event
        await emit_audit_event("muhasabah.rejected", {
            "agent_id": output.agent_id,
            "violations": [v.to_dict() for v in result.violations],
        })
        
        # Hard reject
        raise MuhasabahGateError(
            agent_id=output.agent_id,
            violations=result.violations,
        )
    
    return output
```

---

## 8. Module Structure

```
src/idis/debate/
├── __init__.py
├── orchestrator.py          # LangGraph state machine
├── state.py                 # DebateState model
├── roles/
│   ├── __init__.py
│   ├── base.py              # AgentNode base class
│   ├── advocate.py
│   ├── sanad_breaker.py
│   ├── contradiction_finder.py
│   ├── risk_officer.py
│   └── arbiter.py
├── tools/
│   ├── __init__.py
│   ├── registry.py          # Tool registry
│   ├── lookup_claim.py
│   ├── lookup_calc.py
│   ├── search_evidence.py
│   ├── flag_defect.py
│   ├── request_human_review.py
│   └── query_enrichment.py
├── muhasabah/
│   ├── __init__.py
│   ├── enforcer.py          # Structural validation
│   └── gate.py              # Hard gate implementation
└── stop_conditions.py
```

---

## 9. Acceptance Criteria

### 9.1 Functional Requirements
- [ ] All 5 agent roles implemented
- [ ] All 6 tools implemented with audit
- [ ] Tool permission matrix enforced
- [ ] Muḥāsabah gate rejects invalid outputs
- [ ] Frivolous challenge detection works

### 9.2 Quality Requirements
- [ ] ≥ 98% Muḥāsabah pass rate on valid outputs
- [ ] 0 tool permission violations in production
- [ ] 100% tool call audit coverage

### 9.3 Test Hooks

```python
# Unit tests
def test_agent_output_schema()
def test_muhasabah_validation_rules()
def test_tool_permission_matrix()
def test_frivolous_challenge_detection()

# Integration tests
def test_debate_round_e2e()
def test_muhasabah_gate_rejection()
def test_tool_audit_emission()

# GDBS tests
def test_gdbs_debate_completion()
def test_gdbs_muhasabah_pass_rate()
```
