# IDIS LangGraph Implementation Skeleton — v6.3

**Source**: IDIS VC Edition v6.3 (Appendix C-1 + debate spec + Sanad/Muḥāsabah gates)  
**Purpose**: Provide an implementation-ready skeleton for the debate + analysis orchestration layer, using LangGraph.

This is intentionally written as “do exactly this” instructions for the AI coder.

---

## 1. Implementation Objectives

Your LangGraph workflow MUST:

1. Implement the **normative node graph**:
   - advocate_opening → sanad_breaker_challenge → observer_critiques_parallel → advocate_rebuttal
   - conditional evidence_call_retrieval
   - arbiter_close
   - stop_condition_check (loop/exit)
   - muhasabah_validate_all (hard gate)
   - finalize_outputs

2. Enforce **No-Free-Facts**:
   - Reject any factual output that does not reference `claim_id` or `calc_id`.
   - This MUST be implemented as a deterministic output parser/validator, not prompt-only.

3. Enforce **Muḥāsabah**:
   - Reject any agent output missing a valid `MuḥāsabahRecord`.

4. Enforce **Stop Conditions** (priority):
   - CRITICAL_DEFECT → MAX_ROUNDS → CONSENSUS → STABLE_DISSENT → EVIDENCE_EXHAUSTED

5. Persist **audit artifacts** for every node execution.

---

## 2. Canonical State

The state MUST include at least:

- deal context + pointers:
  - `deal_id`
  - `tenant_id`
  - `claim_registry_ref`
  - `sanad_graph_ref`

- debate runtime:
  - `round_number` (1–5)
  - `messages` (full transcript with claim/calc references)
  - `open_questions`
  - `utility_scores` (per agent)
  - `arbiter_decisions`
  - `stop_reason`

- outputs:
  - `agent_outputs` (structured, each with MuḥāsabahRecord)
  - `deliverables_ref`

---

## 3. Stop Condition Function (Normative)

```python
def check_stop_condition(state) -> str | None:
    # 1) critical defect (highest priority)
    if any_claim_has_grade_D_in_material_position(state):
        return "CRITICAL_DEFECT"

    # 2) max rounds
    if state.round_number >= 5:
        return "MAX_ROUNDS"

    # 3) consensus: all agents within 10% confidence range
    confs = collect_agent_confidences(state)
    if confs and (max(confs) - min(confs) <= 0.10):
        return "CONSENSUS"

    # 4) stable dissent: no position change for 2 rounds
    if positions_unchanged_for_n_rounds(state, n=2):
        return "STABLE_DISSENT"

    # 5) evidence exhaustion
    if no_new_evidence_available(state):
        return "EVIDENCE_EXHAUSTED"

    return None
```

---

## 4. Recommended Node Responsibilities

### 4.1 advocate_opening()

Inputs:
- ClaimRegistry summary
- Key deterministic calcs (calc_id)
- Truth Dashboard deltas (verified/contradicted/unverified)

Outputs:
- Proposed recommendation + thesis
- Must reference claim_ids/calc_ids for any factual statement
- Must include MuḥāsabahRecord

### 4.2 sanad_breaker_challenge()

Focus:
- Attack weak chains (BROKEN_CHAIN, MISSING_LINK, UNKNOWN_SOURCE)
- Surface grade C/D claims in material positions
- Propose cure protocols (REQUEST_SOURCE, RECONSTRUCT_CHAIN, etc.)

### 4.3 observer_critiques_parallel() fan-out/fan-in

Run at least:
- Contradiction Finder (Matn + reconciliation)
- Risk Officer (downside + fraud + regulatory)
- Optional: Market/Tech/Terms specialists

### 4.4 advocate_rebuttal()

- Respond to challenges
- Update thesis if needed
- Register any new claims (NO-FREE-FACTS)

### 4.5 evidence_call_retrieval() [conditional]

When requested by any agent, run retrieval:
- `retrieve_spans(query, deal_id)` → returns spans
- convert to EvidenceItems → register claims → rebuild Sanad links

### 4.6 arbiter_close()

Responsibilities:
- Validate that all challenges reference evidence/claims
- Assign utility (Brier bonus + penalties)
- Decide whether dissent is “evidence-backed” and must be preserved

### 4.7 stop_condition_check() [loop/exit]

### 4.8 muhasabah_validate_all() [HARD GATE]

Deterministic validator:
- Reject No-Free-Facts violations
- Reject missing MuḥāsabahRecord
- Reject overconfidence (confidence>0.80 with no uncertainties)
- Reject missing falsifiability tests for recommendations

### 4.9 finalize_outputs()

- Produce:
  - consensus summary (with dissent section if stable dissent)
  - claim table (top material claims with grades + evidence)
  - action list: missing docs, tests, requests
  - deliverables references to generator

---

## 5. Skeleton Code (Python)

Below is a working skeleton structure. You MUST replace `TODO` parts with your implementation.

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Literal

# LangGraph imports (exact import paths may vary by version)
from langgraph.graph import StateGraph, END

StopReason = Literal[
    "CONSENSUS",
    "STABLE_DISSENT",
    "EVIDENCE_EXHAUSTED",
    "MAX_ROUNDS",
    "CRITICAL_DEFECT",
]

@dataclass
class DebateMessage:
    role: str
    agent_id: Optional[str]
    content: str
    claim_refs: List[str] = field(default_factory=list)  # claim_id/calc_id
    timestamp: str = ""

@dataclass
class MuhasabahRecord:
    agent_id: str
    output_id: str
    supported_claim_ids: List[str]
    falsifiability_tests: List[Dict[str, Any]]
    uncertainties: List[Dict[str, Any]]
    confidence: float
    failure_modes: List[str]
    timestamp: str

@dataclass
class AgentOutput:
    output_id: str
    agent_id: str
    output_type: str
    content: Dict[str, Any]               # structured + narrative
    muhasabah: MuhasabahRecord

@dataclass
class DebateState:
    tenant_id: str
    deal_id: str
    claim_registry_ref: str
    sanad_graph_ref: str
    open_questions: List[str] = field(default_factory=list)
    round_number: int = 1
    messages: List[DebateMessage] = field(default_factory=list)
    utility_scores: Dict[str, float] = field(default_factory=dict)
    arbiter_decisions: List[Dict[str, Any]] = field(default_factory=list)
    agent_outputs: List[AgentOutput] = field(default_factory=list)
    consensus_reached: bool = False
    stop_reason: Optional[StopReason] = None


# ---------------------------
# Deterministic validators
# ---------------------------

def no_free_facts_validator(text: str, claim_refs: List[str]) -> None:
    """Raise if text contains factual assertions without references.
    Implementation MUST be conservative: prefer false positives over leaking unreferenced facts.
    """
    # TODO: implement robust detector (regex + structured output requirement)
    if _looks_like_fact(text) and not claim_refs:
        raise ValueError("No-Free-Facts violation: factual statement without claim_id/calc_id references.")

def muhasabah_validator(out: AgentOutput) -> None:
    m = out.muhasabah
    if m.confidence > 0.80 and len(m.uncertainties) == 0:
        raise ValueError("Muḥāsabah violation: overconfidence without uncertainties.")
    if m.confidence > 0.50 and len(m.falsifiability_tests) == 0:
        raise ValueError("Muḥāsabah violation: missing falsifiability tests.")
    # No-Free-Facts at the output level:
    # TODO: tie supported_claim_ids to actual referenced claims in content
    if len(m.supported_claim_ids) == 0 and _content_contains_facts(out.content):
        raise ValueError("Muḥāsabah violation: supported_claim_ids empty but factual assertions present.")


# ---------------------------
# Stop conditions (normative)
# ---------------------------

def check_stop_condition(state: DebateState) -> Optional[StopReason]:
    if any_claim_has_grade_D_in_material_position(state):
        return "CRITICAL_DEFECT"

    if state.round_number >= 5:
        return "MAX_ROUNDS"

    confs = collect_agent_confidences(state)
    if confs and (max(confs) - min(confs) <= 0.10):
        return "CONSENSUS"

    if positions_unchanged_for_n_rounds(state, n=2):
        return "STABLE_DISSENT"

    if no_new_evidence_available(state):
        return "EVIDENCE_EXHAUSTED"

    return None


# ---------------------------
# Graph nodes (TODO implement)
# ---------------------------

def advocate_opening(state: DebateState) -> DebateState:
    # TODO: call LLM with structured output, register claims, attach muhasabah
    return state

def sanad_breaker_challenge(state: DebateState) -> DebateState:
    # TODO
    return state

def observer_critiques_parallel(state: DebateState) -> DebateState:
    # TODO: fan-out to multiple agents; then merge
    return state

def advocate_rebuttal(state: DebateState) -> DebateState:
    # TODO
    return state

def evidence_call_retrieval(state: DebateState) -> DebateState:
    # TODO: conditional retrieval + claim registration
    return state

def arbiter_close(state: DebateState) -> DebateState:
    # TODO: validate challenges, assign utility (Brier + penalties), preserve dissent
    return state

def stop_condition_check(state: DebateState) -> DebateState:
    state.stop_reason = check_stop_condition(state)
    return state

def muhasabah_validate_all(state: DebateState) -> DebateState:
    # Validate all agent outputs generated in this round
    for out in state.agent_outputs:
        muhasabah_validator(out)
        # Optionally validate No-Free-Facts at message level too
    return state

def finalize_outputs(state: DebateState) -> DebateState:
    # TODO: build final consensus + dissent section + next actions
    return state


# ---------------------------
# LangGraph wiring
# ---------------------------

def build_graph() -> Any:
    g = StateGraph(DebateState)

    g.add_node("advocate_opening", advocate_opening)
    g.add_node("sanad_breaker_challenge", sanad_breaker_challenge)
    g.add_node("observer_critiques_parallel", observer_critiques_parallel)
    g.add_node("advocate_rebuttal", advocate_rebuttal)
    g.add_node("evidence_call_retrieval", evidence_call_retrieval)
    g.add_node("arbiter_close", arbiter_close)
    g.add_node("stop_condition_check", stop_condition_check)
    g.add_node("muhasabah_validate_all", muhasabah_validate_all)
    g.add_node("finalize_outputs", finalize_outputs)

    g.set_entry_point("advocate_opening")
    g.add_edge("advocate_opening", "sanad_breaker_challenge")
    g.add_edge("sanad_breaker_challenge", "observer_critiques_parallel")
    g.add_edge("observer_critiques_parallel", "advocate_rebuttal")
    g.add_edge("advocate_rebuttal", "evidence_call_retrieval")
    g.add_edge("evidence_call_retrieval", "arbiter_close")
    g.add_edge("arbiter_close", "stop_condition_check")

    def _route_after_stop(state: DebateState) -> str:
        return "muhasabah_validate_all" if state.stop_reason is not None else "advocate_opening"

    g.add_conditional_edges("stop_condition_check", _route_after_stop, {
        "advocate_opening": "advocate_opening",               # continue loop
        "muhasabah_validate_all": "muhasabah_validate_all",   # exit loop -> validate -> finalize
    })

    g.add_edge("muhasabah_validate_all", "finalize_outputs")
    g.add_edge("finalize_outputs", END)

    return g.compile()
```

---

## 6. Required Unit Tests (Minimum)

You MUST implement tests for:

1. `check_stop_condition` priority ordering  
2. Claim Sanad grade algorithm (A/B/C/D)  
3. Muḥāsabah validator rules  
4. No-Free-Facts validator (at least for numbers/dates/entities patterns)  
5. Independence computation (`upstream_origin_id` + chain segment overlap)

---

## 7. Artifacts to Persist (Audit)

For each node execution:
- input state hash
- output state hash
- messages appended
- agent outputs + MuḥāsabahRecords
- any newly registered claims/evidence/sanads/defects
- stop_reason decisions and arbiter rationale

