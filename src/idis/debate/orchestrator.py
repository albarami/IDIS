"""IDIS Debate Orchestrator — v6.3 Phase 5.1 + 5.2

LangGraph-based debate orchestration per Appendix C-1.

Node graph order (normative):
START → advocate_opening → sanad_breaker_challenge → observer_critiques_parallel
→ advocate_rebuttal → (conditional evidence_call_retrieval) → arbiter_close
→ stop_condition_check → muhasabah_validate_all → finalize_outputs → END

Key invariants:
- Deterministic execution order (no randomness)
- Stop conditions evaluated in priority order
- Max rounds = 5 (hard limit)
- Role runners are injected (no LLM calls in Phase 5.1)

Phase 5.2 additions:
- Muḥāsabah gate enforced at output boundary (after each role produces output)
- No-Free-Facts validation at output boundary
- Fail-closed: gate rejection halts the run deterministically
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from langgraph.graph import END, StateGraph

from idis.debate.muhasabah_gate import (
    GateDecision,
    MuhasabahGate,
    MuhasabahGateError,
)
from idis.debate.roles.advocate import AdvocateRole
from idis.debate.roles.arbiter import ArbiterRole
from idis.debate.roles.base import RoleResult, RoleRunnerProtocol
from idis.debate.roles.contradiction_finder import ContradictionFinderRole
from idis.debate.roles.risk_officer import RiskOfficerRole
from idis.debate.roles.sanad_breaker import SanadBreakerRole
from idis.debate.stop_conditions import StopConditionChecker, check_stop_condition
from idis.models.debate import (
    DebateConfig,
    DebateState,
    PositionSnapshot,
    StopReason,
)

if TYPE_CHECKING:
    pass


NORMATIVE_NODE_ORDER: list[str] = [
    "advocate_opening",
    "sanad_breaker_challenge",
    "observer_critiques_parallel",
    "advocate_rebuttal",
    "evidence_call_retrieval",
    "arbiter_close",
    "stop_condition_check",
    "muhasabah_validate_all",
    "finalize_outputs",
]
"""Normative node order per Appendix C-1 (excluding START/END)."""


@dataclass
class RoleRunners:
    """Collection of role runners for injection into orchestrator."""

    advocate: RoleRunnerProtocol = field(default_factory=AdvocateRole)
    sanad_breaker: RoleRunnerProtocol = field(default_factory=SanadBreakerRole)
    contradiction_finder: RoleRunnerProtocol = field(default_factory=ContradictionFinderRole)
    risk_officer: RoleRunnerProtocol = field(default_factory=RiskOfficerRole)
    arbiter: RoleRunnerProtocol = field(default_factory=ArbiterRole)


class DebateOrchestrator:
    """Deterministic debate orchestrator using LangGraph.

    Implements the v6.3 node graph with injected role runners.
    All execution is deterministic - no randomness in node order or evaluation.

    Phase 5.2: Muḥāsabah gate enforced at output boundary.
    - Gate is called immediately after each role produces output
    - Gate rejection halts the run with CRITICAL_DEFECT stop reason
    - No outputs accepted into state without passing gate
    """

    def __init__(
        self,
        config: DebateConfig | None = None,
        role_runners: RoleRunners | None = None,
        *,
        enforce_muhasabah: bool = False,
    ) -> None:
        """Initialize orchestrator.

        Args:
            config: Debate configuration. Uses defaults if not provided.
            role_runners: Injected role runners. Uses defaults if not provided.
            enforce_muhasabah: If True, enforce Muḥāsabah gate on all outputs.
                              Defaults to False for backward compatibility.
                              Production usage SHOULD set this to True.
        """
        self.config = config or DebateConfig()
        self.runners = role_runners or RoleRunners()
        self.stop_checker = StopConditionChecker(self.config)
        self.enforce_muhasabah = enforce_muhasabah
        self._muhasabah_gate = MuhasabahGate() if enforce_muhasabah else None
        self._graph: Any | None = None
        self._gate_failure: MuhasabahGateError | None = None

    def build_graph(self) -> Any:
        """Build and compile the LangGraph state machine.

        Returns:
            Compiled LangGraph ready for invocation.
        """
        if self._graph is not None:
            return self._graph

        g = StateGraph(DebateState)

        g.add_node("advocate_opening", self._node_advocate_opening)
        g.add_node("sanad_breaker_challenge", self._node_sanad_breaker_challenge)
        g.add_node("observer_critiques_parallel", self._node_observer_critiques_parallel)
        g.add_node("advocate_rebuttal", self._node_advocate_rebuttal)
        g.add_node("evidence_call_retrieval", self._node_evidence_call_retrieval)
        g.add_node("arbiter_close", self._node_arbiter_close)
        g.add_node("stop_condition_check", self._node_stop_condition_check)
        g.add_node("muhasabah_validate_all", self._node_muhasabah_validate_all)
        g.add_node("finalize_outputs", self._node_finalize_outputs)

        g.set_entry_point("advocate_opening")

        g.add_edge("advocate_opening", "sanad_breaker_challenge")
        g.add_edge("sanad_breaker_challenge", "observer_critiques_parallel")
        g.add_edge("observer_critiques_parallel", "advocate_rebuttal")

        g.add_conditional_edges(
            "advocate_rebuttal",
            self._route_after_rebuttal,
            {
                "evidence_call_retrieval": "evidence_call_retrieval",
                "arbiter_close": "arbiter_close",
            },
        )

        g.add_edge("evidence_call_retrieval", "arbiter_close")
        g.add_edge("arbiter_close", "stop_condition_check")

        g.add_conditional_edges(
            "stop_condition_check",
            self._route_after_stop_check,
            {
                "advocate_opening": "advocate_opening",
                "muhasabah_validate_all": "muhasabah_validate_all",
            },
        )
        g.add_edge("muhasabah_validate_all", "finalize_outputs")
        g.add_edge("finalize_outputs", END)

        self._graph = g.compile()
        return self._graph

    def run(self, initial_state: DebateState) -> DebateState:
        """Run the debate to completion.

        Args:
            initial_state: Initial debate state with deal context.

        Returns:
            Final debate state after completion.

        Note:
            Phase 5.2: If Muḥāsabah gate rejects an output, the run halts
            with stop_reason=CRITICAL_DEFECT. Use get_gate_failure() to
            retrieve the error details.
        """
        # Reset gate failure state for this run
        self._gate_failure = None

        graph = self.build_graph()
        # Set recursion limit high enough for max_rounds * nodes_per_round
        # Each round visits ~9 nodes, so 5 rounds = 45 nodes + buffer
        recursion_limit = max(50, self.config.max_rounds * 15)
        result = graph.invoke(
            initial_state.model_dump(),
            config={"recursion_limit": recursion_limit},
        )
        return DebateState(**result)

    def get_gate_failure(self) -> MuhasabahGateError | None:
        """Get the Muḥāsabah gate failure from the last run, if any.

        Returns:
            MuhasabahGateError if the last run was halted by a gate rejection,
            None otherwise.
        """
        return self._gate_failure

    def _apply_role_result(
        self, state: DebateState, result: RoleResult, node_name: str
    ) -> DebateState:
        """Apply role result to state.

        Phase 5.2: Muḥāsabah gate is enforced on each output before acceptance.
        If any output fails the gate, the run is halted with gate_failure set.
        """
        updates: dict[str, Any] = {
            "nodes_visited": [*state.nodes_visited, node_name],
        }

        if result.messages:
            updates["messages"] = [*state.messages, *result.messages]

        if result.outputs:
            # Phase 5.2: Enforce Muḥāsabah gate on each output before acceptance
            validated_outputs = []
            for output in result.outputs:
                gate_decision = self._enforce_gate_on_output(output, state)
                if gate_decision is not None and not gate_decision.allowed:
                    # Gate failed - halt the run deterministically
                    # Set stop_reason to CRITICAL_DEFECT and record the failure
                    updates["stop_reason"] = StopReason.CRITICAL_DEFECT
                    self._gate_failure = MuhasabahGateError(
                        message=f"Muḥāsabah gate rejected output from {output.agent_id}",
                        decision=gate_decision,
                        output_id=output.output_id,
                        agent_id=output.agent_id,
                    )
                    # Do not add the invalid output to state
                    break
                validated_outputs.append(output)

            if validated_outputs:
                updates["agent_outputs"] = [*state.agent_outputs, *validated_outputs]

        if result.evidence_retrieval_requested:
            updates["evidence_retrieval_requested"] = True

        return state.model_copy(update=updates)

    def _enforce_gate_on_output(self, output: Any, state: DebateState) -> GateDecision | None:
        """Enforce Muḥāsabah gate on a single output.

        Returns:
            GateDecision if gate is enforced, None if gate is disabled.
        """
        if not self.enforce_muhasabah or self._muhasabah_gate is None:
            return None

        context = {
            "tenant_id": state.tenant_id,
            "deal_id": state.deal_id,
            "round_number": state.round_number,
        }

        return self._muhasabah_gate.evaluate(output, context=context)

    def _node_advocate_opening(self, state: DebateState) -> DebateState:
        """Execute advocate opening node."""
        result = self.runners.advocate.run(state)
        return self._apply_role_result(state, result, "advocate_opening")

    def _node_sanad_breaker_challenge(self, state: DebateState) -> DebateState:
        """Execute sanad breaker challenge node."""
        result = self.runners.sanad_breaker.run(state)
        return self._apply_role_result(state, result, "sanad_breaker_challenge")

    def _node_observer_critiques_parallel(self, state: DebateState) -> DebateState:
        """Execute observer critiques in parallel (deterministic order).

        Per v6.3, observers run in parallel. For determinism, we execute
        them in a fixed order: contradiction_finder, risk_officer.
        """
        cf_result = self.runners.contradiction_finder.run(state)
        state = self._apply_role_result(state, cf_result, "observer_critiques_parallel")

        ro_result = self.runners.risk_officer.run(state)
        state = self._apply_role_result(state, ro_result, "observer_critiques_parallel")

        return state

    def _node_advocate_rebuttal(self, state: DebateState) -> DebateState:
        """Execute advocate rebuttal node."""
        result = self.runners.advocate.run(state)
        return self._apply_role_result(state, result, "advocate_rebuttal")

    def _node_evidence_call_retrieval(self, state: DebateState) -> DebateState:
        """Execute conditional evidence retrieval node.

        This node runs when evidence_retrieval_requested is True.
        In Phase 5.1, it marks retrieval as completed without external calls.
        """
        updates: dict[str, Any] = {
            "nodes_visited": [*state.nodes_visited, "evidence_call_retrieval"],
            "evidence_retrieval_completed": True,
        }
        return state.model_copy(update=updates)

    def _node_arbiter_close(self, state: DebateState) -> DebateState:
        """Execute arbiter close node."""
        result = self.runners.arbiter.run(state)
        state = self._apply_role_result(state, result, "arbiter_close")

        arbiter_role = self.runners.arbiter
        if isinstance(arbiter_role, ArbiterRole):
            decision = arbiter_role.get_decision_from_result(result)
            if decision:
                state = state.model_copy(
                    update={"arbiter_decisions": [*state.arbiter_decisions, decision]}
                )

        position_snapshot = self._build_position_snapshot(state)
        state = state.model_copy(
            update={"position_history": [*state.position_history, position_snapshot]}
        )

        return state

    def _node_stop_condition_check(self, state: DebateState) -> DebateState:
        """Execute stop condition check node."""
        stop_reason = check_stop_condition(state, self.config)

        updates: dict[str, Any] = {
            "nodes_visited": [*state.nodes_visited, "stop_condition_check"],
        }

        if stop_reason is not None:
            updates["stop_reason"] = stop_reason
            if stop_reason == StopReason.CONSENSUS:
                updates["consensus_reached"] = True
        else:
            # Increment round for next iteration (no separate node needed)
            updates["round_number"] = state.round_number + 1

        return state.model_copy(update=updates)

    def _node_muhasabah_validate_all(self, state: DebateState) -> DebateState:
        """Execute muhasabah validation node.

        Phase 5.2: Final validation of all outputs before finalization.
        This is a belt-and-suspenders check - outputs should already be validated
        at the output boundary, but this node re-validates all outputs in case
        any were added without going through the gate.

        If any output fails validation here, the run is halted with CRITICAL_DEFECT.
        """
        updates: dict[str, Any] = {
            "nodes_visited": [*state.nodes_visited, "muhasabah_validate_all"],
        }

        # Check if we already have a gate failure from earlier
        if self._gate_failure is not None:
            updates["stop_reason"] = StopReason.CRITICAL_DEFECT
            return state.model_copy(update=updates)

        # Phase 5.2: Re-validate all outputs as final check
        if self.enforce_muhasabah and self._muhasabah_gate is not None:
            for output in state.agent_outputs:
                decision = self._enforce_gate_on_output(output, state)
                if decision is not None and not decision.allowed:
                    # Found an invalid output - this should not happen if gate
                    # was enforced at output boundary, but we fail closed anyway
                    updates["stop_reason"] = StopReason.CRITICAL_DEFECT
                    self._gate_failure = MuhasabahGateError(
                        message=(
                            f"Muḥāsabah gate rejected output in final validation: {output.agent_id}"
                        ),
                        decision=decision,
                        output_id=output.output_id,
                        agent_id=output.agent_id,
                    )
                    break

        return state.model_copy(update=updates)

    def _node_finalize_outputs(self, state: DebateState) -> DebateState:
        """Execute finalize outputs node."""
        updates: dict[str, Any] = {
            "nodes_visited": [*state.nodes_visited, "finalize_outputs"],
        }
        return state.model_copy(update=updates)

    def _route_after_rebuttal(self, state: DebateState) -> str:
        """Route after advocate rebuttal.

        If evidence retrieval was requested and not yet completed,
        route to evidence_call_retrieval. Otherwise, go to arbiter_close.
        """
        if state.evidence_retrieval_requested and not state.evidence_retrieval_completed:
            return "evidence_call_retrieval"
        return "arbiter_close"

    def _route_after_stop_check(self, state: DebateState) -> str:
        """Route after stop condition check.

        If stop reason is set, proceed to muhasabah validation.
        Otherwise, loop back to advocate_opening for next round.
        """
        if state.stop_reason is not None:
            return "muhasabah_validate_all"

        return "advocate_opening"

    def _build_position_snapshot(self, state: DebateState) -> PositionSnapshot:
        """Build position snapshot from current round outputs."""
        agent_positions: dict[str, str] = {}
        agent_confidences: dict[str, float] = {}

        for output in state.agent_outputs:
            if output.round_number == state.round_number:
                pos_hash = output.content.get("position_hash", "")
                agent_positions[output.agent_id] = pos_hash
                agent_confidences[output.agent_id] = output.muhasabah.confidence

        return PositionSnapshot(
            round_number=state.round_number,
            agent_positions=agent_positions,
            agent_confidences=agent_confidences,
        )


def build_debate_graph(
    config: DebateConfig | None = None,
    role_runners: RoleRunners | None = None,
) -> Any:
    """Build and return a compiled debate graph.

    Convenience function for creating a debate graph with configuration.

    Args:
        config: Debate configuration.
        role_runners: Injected role runners.

    Returns:
        Compiled LangGraph state machine.
    """
    orchestrator = DebateOrchestrator(config=config, role_runners=role_runners)
    return orchestrator.build_graph()


def get_normative_node_order() -> list[str]:
    """Return the normative node order per Appendix C-1."""
    return NORMATIVE_NODE_ORDER.copy()
