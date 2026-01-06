"""IDIS LangGraph Debate Orchestrator Skeleton (v6.3)

This file is a starting point for implementing the IDIS debate layer per:
- Appendix C-1: LangGraph Orchestration Specification
- Appendix E: Muḥāsabah Protocol
- Appendix F: Game-Theoretic Debate Mechanism Design Notes

Non-negotiable:
- No-Free-Facts enforced deterministically
- MuḥāsabahRecord required for every agent output
- Stop conditions per v6.3 priority order
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Literal

from langgraph.graph import StateGraph, END  # type: ignore

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
    claim_refs: List[str] = field(default_factory=list)  # claim_id/calc_id refs
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
    content: Dict[str, Any]  # structured + narrative
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

_FACT_REGEXES = [
    # conservative detectors (extend as needed)
    r"\b\$?\d+(?:\.\d+)?\b",  # numbers
    r"\b\d{4}\b",  # years
    r"\b(?:MRR|ARR|NRR|GM|CAC|LTV|IRR|MOIC)\b",  # metric tokens
]


def _looks_like_fact(text: str) -> bool:
    import re

    for pat in _FACT_REGEXES:
        if re.search(pat, text):
            return True
    return False


def _content_contains_facts(content: Dict[str, Any]) -> bool:
    # Conservative: stringify and scan
    return _looks_like_fact(str(content))


def no_free_facts_validator(text: str, claim_refs: List[str]) -> None:
    """Raise if text contains factual assertions without references."""
    if _looks_like_fact(text) and not claim_refs:
        raise ValueError(
            "No-Free-Facts violation: factual statement without claim_id/calc_id references."
        )


def muhasabah_validator(out: AgentOutput) -> None:
    """Normative Muḥāsabah validator per v6.3."""
    m = out.muhasabah
    if m.confidence > 0.80 and len(m.uncertainties) == 0:
        raise ValueError("Muḥāsabah violation: overconfidence without uncertainties.")
    if m.confidence > 0.50 and len(m.falsifiability_tests) == 0:
        raise ValueError("Muḥāsabah violation: missing falsifiability tests.")
    if len(m.supported_claim_ids) == 0 and _content_contains_facts(out.content):
        raise ValueError(
            "Muḥāsabah violation: supported_claim_ids empty but factual assertions present."
        )


# ---------------------------
# Stop conditions (normative)
# ---------------------------


def any_claim_has_grade_D_in_material_position(state: DebateState) -> bool:
    # TODO: consult claim registry + materiality thresholds
    return False


def collect_agent_confidences(state: DebateState) -> List[float]:
    # TODO: extract from structured agent outputs
    return []


def positions_unchanged_for_n_rounds(state: DebateState, n: int) -> bool:
    # TODO: implement by comparing per-round thesis snapshots
    return False


def no_new_evidence_available(state: DebateState) -> bool:
    # TODO: implement retrieval budget/exhaustion logic
    return False


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
    # TODO: conditional retrieval + claim registration + sanad rebuild
    return state


def arbiter_close(state: DebateState) -> DebateState:
    # TODO: validate challenges, assign utility (Brier + penalties), preserve dissent
    return state


def stop_condition_check(state: DebateState) -> DebateState:
    state.stop_reason = check_stop_condition(state)
    return state


def muhasabah_validate_all(state: DebateState) -> DebateState:
    for out in state.agent_outputs:
        muhasabah_validator(out)
    return state


def finalize_outputs(state: DebateState) -> DebateState:
    # TODO: build final consensus + dissent section + next actions + deliverables refs
    return state


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

    def route_after_stop(state: DebateState) -> str:
        return "muhasabah_validate_all" if state.stop_reason is not None else "advocate_opening"

    g.add_conditional_edges(
        "stop_condition_check",
        route_after_stop,
        {
            "advocate_opening": "advocate_opening",
            "muhasabah_validate_all": "muhasabah_validate_all",
        },
    )

    g.add_edge("muhasabah_validate_all", "finalize_outputs")
    g.add_edge("finalize_outputs", END)

    return g.compile()


if __name__ == "__main__":
    graph = build_graph()
    # TODO: initialize DebateState with real refs and invoke graph
    # result = graph.invoke(initial_state)
