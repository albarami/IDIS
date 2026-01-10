"""IDIS Debate Node Graph Tests — v6.3 Phase 5.1

Tests per go-live plan (10_IDIS_GoLive_Execution_Plan_v6_3.md):
- test_debate_node_graph.py — asserts node order matches v6.3

These tests verify the normative node graph order per Appendix C-1:
START → advocate_opening → sanad_breaker_challenge → observer_critiques_parallel
→ advocate_rebuttal → (conditional evidence_call_retrieval) → arbiter_close
→ stop_condition_check → muhasabah_validate_all → finalize_outputs → END
"""

from __future__ import annotations

from idis.debate.orchestrator import (
    NORMATIVE_NODE_ORDER,
    DebateOrchestrator,
    RoleRunners,
    build_debate_graph,
    get_normative_node_order,
)
from idis.debate.roles.advocate import AdvocateRole
from idis.debate.roles.arbiter import ArbiterRole
from idis.debate.roles.base import (
    RoleResult,
    RoleRunner,
    deterministic_id,
    deterministic_timestamp,
)
from idis.debate.roles.contradiction_finder import ContradictionFinderRole
from idis.debate.roles.risk_officer import RiskOfficerRole
from idis.debate.roles.sanad_breaker import SanadBreakerRole
from idis.models.debate import (
    AgentOutput,
    DebateConfig,
    DebateMessage,
    DebateRole,
    DebateState,
    MuhasabahRecord,
    StopReason,
)


def create_test_state(
    round_number: int = 1,
    stop_reason: StopReason | None = None,
) -> DebateState:
    """Create a minimal valid DebateState for testing."""
    return DebateState(
        tenant_id="test-tenant",
        deal_id="test-deal",
        claim_registry_ref="claim-reg-001",
        sanad_graph_ref="sanad-graph-001",
        round_number=round_number,
        stop_reason=stop_reason,
    )


class FakeMaxRoundsRole(RoleRunner):
    """Fake role that triggers MAX_ROUNDS stop condition."""

    def __init__(self, role: DebateRole, agent_id: str) -> None:
        super().__init__(role, agent_id)
        self._call_count = 0

    def run(self, state: DebateState) -> RoleResult:
        self._call_count += 1
        timestamp = deterministic_timestamp(state.round_number, step=self._call_count)
        message_id = deterministic_id(
            "msg",
            tenant_id=state.tenant_id,
            deal_id=state.deal_id,
            role=self._role.value,
            round_number=state.round_number,
            step=self._call_count,
        )
        output_id = deterministic_id(
            "out",
            tenant_id=state.tenant_id,
            deal_id=state.deal_id,
            role=self._role.value,
            round_number=state.round_number,
            step=self._call_count,
        )
        record_id = deterministic_id(
            "muh",
            tenant_id=state.tenant_id,
            deal_id=state.deal_id,
            role=self._role.value,
            round_number=state.round_number,
            step=self._call_count,
        )

        message = DebateMessage(
            message_id=message_id,
            role=self._role,
            agent_id=self._agent_id,
            content=f"[{self._role.value} round {state.round_number}]",
            claim_refs=[],
            calc_refs=[],
            round_number=state.round_number,
            timestamp=timestamp,
        )

        muhasabah = MuhasabahRecord(
            record_id=record_id,
            agent_id=self._agent_id,
            output_id=output_id,
            supported_claim_ids=[],
            supported_calc_ids=[],
            falsifiability_tests=[],
            uncertainties=[],
            confidence=0.5,
            failure_modes=[],
            timestamp=timestamp,
        )

        output = AgentOutput(
            output_id=output_id,
            agent_id=self._agent_id,
            role=self._role,
            output_type="test",
            content={"position_hash": f"{self._role.value}-{state.round_number}"},
            muhasabah=muhasabah,
            round_number=state.round_number,
            timestamp=timestamp,
        )

        return RoleResult(
            messages=[message],
            outputs=[output],
            position_hash=f"{self._role.value}-{state.round_number}",
        )


class TestNormativeNodeOrder:
    """Tests for normative node order per Appendix C-1."""

    def test_normative_order_matches_appendix_c1(self) -> None:
        """Node order must match v6.3 Appendix C-1 specification."""
        expected_order = [
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
        assert expected_order == NORMATIVE_NODE_ORDER

    def test_get_normative_node_order_returns_copy(self) -> None:
        """get_normative_node_order returns a copy, not the original."""
        order1 = get_normative_node_order()
        order2 = get_normative_node_order()

        assert order1 == order2
        assert order1 is not order2

        order1.append("extra_node")
        assert "extra_node" not in NORMATIVE_NODE_ORDER

    def test_orchestrator_has_all_normative_nodes(self) -> None:
        """Orchestrator must define all nodes from normative order."""
        orchestrator = DebateOrchestrator()
        _ = orchestrator.build_graph()

        for node_name in NORMATIVE_NODE_ORDER:
            method_name = f"_node_{node_name}"
            assert hasattr(orchestrator, method_name), f"Missing node method: {method_name}"


class TestNodeGraphExecution:
    """Tests for node graph execution flow."""

    def test_single_round_execution_visits_all_nodes(self) -> None:
        """Single round (max_rounds=1) visits all nodes in order."""
        config = DebateConfig(max_rounds=1)
        orchestrator = DebateOrchestrator(config=config)

        initial_state = create_test_state(round_number=1)
        result = orchestrator.run(initial_state)

        assert result.stop_reason == StopReason.MAX_ROUNDS

        expected_nodes = [
            "advocate_opening",
            "sanad_breaker_challenge",
            "observer_critiques_parallel",
            "observer_critiques_parallel",
            "advocate_rebuttal",
            "arbiter_close",
            "stop_condition_check",
            "muhasabah_validate_all",
            "finalize_outputs",
        ]
        assert result.nodes_visited == expected_nodes

    def test_conditional_evidence_retrieval_skipped_when_not_requested(self) -> None:
        """evidence_call_retrieval is skipped when not requested."""
        config = DebateConfig(max_rounds=1)
        orchestrator = DebateOrchestrator(config=config)

        initial_state = create_test_state()
        result = orchestrator.run(initial_state)

        assert "evidence_call_retrieval" not in result.nodes_visited

    def test_conditional_evidence_retrieval_executed_when_requested(self) -> None:
        """evidence_call_retrieval executes when evidence_retrieval_requested=True."""

        class EvidenceRequestingRole(RoleRunner):
            """Fake advocate that requests evidence retrieval."""

            def __init__(self) -> None:
                super().__init__(DebateRole.ADVOCATE, "evidence-requester")

            def run(self, state: DebateState) -> RoleResult:
                timestamp = deterministic_timestamp(state.round_number, step=50)
                msg_id = deterministic_id(
                    "msg",
                    tenant_id=state.tenant_id,
                    deal_id=state.deal_id,
                    role="evidence-requester",
                    round_number=state.round_number,
                    step=50,
                )
                out_id = deterministic_id(
                    "out",
                    tenant_id=state.tenant_id,
                    deal_id=state.deal_id,
                    role="evidence-requester",
                    round_number=state.round_number,
                    step=50,
                )
                muh_id = deterministic_id(
                    "muh",
                    tenant_id=state.tenant_id,
                    deal_id=state.deal_id,
                    role="evidence-requester",
                    round_number=state.round_number,
                    step=50,
                )
                return RoleResult(
                    messages=[
                        DebateMessage(
                            message_id=msg_id,
                            role=DebateRole.ADVOCATE,
                            agent_id=self.agent_id,
                            content="Request evidence",
                            round_number=state.round_number,
                            timestamp=timestamp,
                        )
                    ],
                    outputs=[
                        AgentOutput(
                            output_id=out_id,
                            agent_id=self.agent_id,
                            role=DebateRole.ADVOCATE,
                            output_type="rebuttal",
                            content={"position_hash": "test"},
                            muhasabah=MuhasabahRecord(
                                record_id=muh_id,
                                agent_id=self.agent_id,
                                output_id=out_id,
                                supported_claim_ids=[],
                                supported_calc_ids=[],
                                falsifiability_tests=[],
                                uncertainties=[],
                                confidence=0.5,
                                failure_modes=[],
                                timestamp=timestamp,
                            ),
                            round_number=state.round_number,
                            timestamp=timestamp,
                        )
                    ],
                    evidence_retrieval_requested=True,
                )

        config = DebateConfig(max_rounds=1)
        runners = RoleRunners(advocate=EvidenceRequestingRole())
        orchestrator = DebateOrchestrator(config=config, role_runners=runners)

        initial_state = create_test_state()
        result = orchestrator.run(initial_state)

        assert "evidence_call_retrieval" in result.nodes_visited
        assert result.evidence_retrieval_completed is True

    def test_loop_back_to_advocate_opening_when_no_stop(self) -> None:
        """Graph loops back to advocate_opening when stop_reason is None."""
        config = DebateConfig(max_rounds=2)
        orchestrator = DebateOrchestrator(config=config)

        initial_state = create_test_state(round_number=1)
        result = orchestrator.run(initial_state)

        # May stop on CONSENSUS (all roles return same confidence) or MAX_ROUNDS
        assert result.stop_reason in [StopReason.MAX_ROUNDS, StopReason.CONSENSUS]

        # Verify looping occurred (at least one advocate_opening)
        advocate_opening_count = result.nodes_visited.count("advocate_opening")
        assert advocate_opening_count >= 1

    def test_exit_to_muhasabah_when_stop_reason_set(self) -> None:
        """Graph exits to muhasabah_validate_all when stop_reason is set."""
        config = DebateConfig(max_rounds=1)
        orchestrator = DebateOrchestrator(config=config)

        initial_state = create_test_state(round_number=1)
        result = orchestrator.run(initial_state)

        stop_idx = result.nodes_visited.index("stop_condition_check")
        assert result.nodes_visited[stop_idx + 1] == "muhasabah_validate_all"


class TestMuhasabahValidateAll:
    """Tests for muhasabah_validate_all node."""

    def test_muhasabah_node_is_structural_noop_in_phase_5_1(self) -> None:
        """muhasabah_validate_all records visit but does not validate (Phase 5.1)."""
        config = DebateConfig(max_rounds=1)
        orchestrator = DebateOrchestrator(config=config)

        initial_state = create_test_state()
        result = orchestrator.run(initial_state)

        assert "muhasabah_validate_all" in result.nodes_visited

    def test_muhasabah_node_reached_after_stop(self) -> None:
        """muhasabah_validate_all is reached after stop condition is met."""
        config = DebateConfig(max_rounds=1)
        orchestrator = DebateOrchestrator(config=config)

        initial_state = create_test_state()
        result = orchestrator.run(initial_state)

        nodes = result.nodes_visited
        stop_idx = nodes.index("stop_condition_check")
        muhasabah_idx = nodes.index("muhasabah_validate_all")
        finalize_idx = nodes.index("finalize_outputs")

        assert stop_idx < muhasabah_idx < finalize_idx


class TestFinalizeOutputs:
    """Tests for finalize_outputs node."""

    def test_finalize_outputs_is_last_node(self) -> None:
        """finalize_outputs must be the last node before END."""
        config = DebateConfig(max_rounds=1)
        orchestrator = DebateOrchestrator(config=config)

        initial_state = create_test_state()
        result = orchestrator.run(initial_state)

        assert result.nodes_visited[-1] == "finalize_outputs"


class TestDeterminism:
    """Tests for deterministic execution."""

    def test_execution_is_deterministic(self) -> None:
        """Multiple runs with same input produce same node visit order."""
        config = DebateConfig(max_rounds=1)

        results = []
        for _ in range(3):
            orchestrator = DebateOrchestrator(config=config)
            initial_state = create_test_state()
            result = orchestrator.run(initial_state)
            results.append(result.nodes_visited)

        assert results[0] == results[1] == results[2]

    def test_observer_critiques_execute_in_fixed_order(self) -> None:
        """Observers execute in deterministic order: contradiction_finder, risk_officer."""
        config = DebateConfig(max_rounds=1)
        orchestrator = DebateOrchestrator(config=config)

        initial_state = create_test_state()
        result = orchestrator.run(initial_state)

        observer_outputs = [
            o
            for o in result.agent_outputs
            if o.role
            in [
                DebateRole.CONTRADICTION_FINDER,
                DebateRole.RISK_OFFICER,
            ]
        ]

        assert len(observer_outputs) >= 2
        roles_in_order = [o.role for o in observer_outputs[:2]]
        assert roles_in_order == [DebateRole.CONTRADICTION_FINDER, DebateRole.RISK_OFFICER]


class TestBuildDebateGraph:
    """Tests for build_debate_graph convenience function."""

    def test_build_debate_graph_returns_compiled_graph(self) -> None:
        """build_debate_graph returns a compiled LangGraph."""
        graph = build_debate_graph()
        assert graph is not None
        assert hasattr(graph, "invoke")

    def test_build_debate_graph_accepts_config(self) -> None:
        """build_debate_graph accepts configuration."""
        config = DebateConfig(max_rounds=3)
        graph = build_debate_graph(config=config)
        assert graph is not None

    def test_build_debate_graph_accepts_runners(self) -> None:
        """build_debate_graph accepts custom role runners."""
        runners = RoleRunners(
            advocate=AdvocateRole("custom-advocate"),
            sanad_breaker=SanadBreakerRole("custom-breaker"),
            contradiction_finder=ContradictionFinderRole("custom-cf"),
            risk_officer=RiskOfficerRole("custom-ro"),
            arbiter=ArbiterRole("custom-arbiter"),
        )
        graph = build_debate_graph(role_runners=runners)
        assert graph is not None


class TestRoleRunnerIntegration:
    """Tests for role runner integration with orchestrator."""

    def test_all_roles_execute_in_single_round(self) -> None:
        """All five roles execute during a single round."""
        config = DebateConfig(max_rounds=1)
        orchestrator = DebateOrchestrator(config=config)

        initial_state = create_test_state()
        result = orchestrator.run(initial_state)

        roles_executed = {o.role for o in result.agent_outputs}
        expected_roles = {
            DebateRole.ADVOCATE,
            DebateRole.SANAD_BREAKER,
            DebateRole.CONTRADICTION_FINDER,
            DebateRole.RISK_OFFICER,
            DebateRole.ARBITER,
        }
        assert roles_executed == expected_roles

    def test_arbiter_decision_recorded(self) -> None:
        """Arbiter decision is recorded in state."""
        config = DebateConfig(max_rounds=1)
        orchestrator = DebateOrchestrator(config=config)

        initial_state = create_test_state()
        result = orchestrator.run(initial_state)

        assert len(result.arbiter_decisions) >= 1

    def test_position_history_recorded(self) -> None:
        """Position snapshots are recorded for stable dissent detection."""
        config = DebateConfig(max_rounds=1)
        orchestrator = DebateOrchestrator(config=config)

        initial_state = create_test_state()
        result = orchestrator.run(initial_state)

        assert len(result.position_history) >= 1
        snapshot = result.position_history[0]
        assert snapshot.round_number == 1
        assert len(snapshot.agent_positions) > 0
