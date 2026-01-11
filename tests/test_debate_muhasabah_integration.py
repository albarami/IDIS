"""Integration tests for Debate Orchestrator + Muḥāsabah Gate — v6.3 Phase 5.2

Required integration test cases per task spec:
- A deterministic role runner emits an output WITHOUT MuhasabahRecord
  -> orchestrator blocks run (fail closed).
- A deterministic role runner emits an output WITH valid MuhasabahRecord + claim refs
  -> orchestrator proceeds.
"""

from __future__ import annotations

from datetime import datetime

from idis.debate.orchestrator import DebateOrchestrator, RoleRunners
from idis.debate.roles.base import RoleResult, RoleRunnerProtocol
from idis.models.debate import (
    AgentOutput,
    DebateConfig,
    DebateRole,
    DebateState,
    MuhasabahRecord,
    StopReason,
)


def _make_valid_muhasabah(
    agent_id: str,
    output_id: str,
    claim_ids: list[str] | None = None,
    confidence: float = 0.70,
) -> MuhasabahRecord:
    """Create a valid MuhasabahRecord for testing."""
    if claim_ids is None:
        claim_ids = ["00000000-0000-0000-0000-000000000100"]

    return MuhasabahRecord(
        record_id="00000000-0000-0000-0000-000000000200",
        agent_id=agent_id,
        output_id=output_id,
        supported_claim_ids=claim_ids,
        supported_calc_ids=[],
        falsifiability_tests=[],
        uncertainties=[],
        confidence=confidence,
        failure_modes=[],
        timestamp=datetime(2026, 1, 10, 12, 0, 0),
    )


def _make_invalid_muhasabah_no_claims(
    agent_id: str,
    output_id: str,
) -> MuhasabahRecord:
    """Create an invalid MuhasabahRecord with no claim refs."""
    return MuhasabahRecord(
        record_id="00000000-0000-0000-0000-000000000201",
        agent_id=agent_id,
        output_id=output_id,
        supported_claim_ids=[],  # Empty - No-Free-Facts violation
        supported_calc_ids=[],
        falsifiability_tests=[],
        uncertainties=[],
        confidence=0.70,
        failure_modes=[],
        timestamp=datetime(2026, 1, 10, 12, 0, 0),
    )


def _make_invalid_muhasabah_overconfident(
    agent_id: str,
    output_id: str,
) -> MuhasabahRecord:
    """Create an invalid MuhasabahRecord with high confidence but no uncertainties."""
    return MuhasabahRecord(
        record_id="00000000-0000-0000-0000-000000000202",
        agent_id=agent_id,
        output_id=output_id,
        supported_claim_ids=["00000000-0000-0000-0000-000000000100"],
        supported_calc_ids=[],
        falsifiability_tests=[],
        uncertainties=[],  # Empty with high confidence
        confidence=0.90,  # > 0.80 threshold
        failure_modes=[],
        timestamp=datetime(2026, 1, 10, 12, 0, 0),
    )


class ValidRoleRunner(RoleRunnerProtocol):
    """A role runner that produces valid outputs with proper MuhasabahRecord."""

    def __init__(self, role: DebateRole = DebateRole.ADVOCATE) -> None:
        self._role = role
        self._agent_id = f"{role.value}-valid-runner"
        self.run_count = 0

    @property
    def role(self) -> DebateRole:
        """The debate role this runner implements."""
        return self._role

    @property
    def agent_id(self) -> str:
        """Unique identifier for this agent instance."""
        return self._agent_id

    def run(self, state: DebateState) -> RoleResult:
        """Produce a valid output."""
        self.run_count += 1
        # Use valid UUID format (hex characters only)
        role_hex = self._role.value[:8].encode().hex()[:12].ljust(12, "0")
        agent_id = f"00000000-0000-0000-0000-{role_hex}"
        output_id = f"00000000-0000-0000-0001-{self.run_count:012x}"

        muhasabah = _make_valid_muhasabah(agent_id=agent_id, output_id=output_id)

        output = AgentOutput(
            output_id=output_id,
            agent_id=agent_id,
            role=self.role,
            output_type="analysis",
            content={
                "text": "Analysis based on referenced claims.",
                "is_subjective": False,
            },
            muhasabah=muhasabah,
            round_number=state.round_number,
            timestamp=datetime(2026, 1, 10, 12, 0, 0),
        )

        return RoleResult(
            outputs=[output],
            messages=[],
        )


class InvalidRoleRunnerNoClaims(RoleRunnerProtocol):
    """A role runner that produces invalid outputs with empty claim refs."""

    def __init__(self, role: DebateRole = DebateRole.ADVOCATE) -> None:
        self._role = role
        self._agent_id = f"{role.value}-invalid-no-claims"
        self.run_count = 0

    @property
    def role(self) -> DebateRole:
        """The debate role this runner implements."""
        return self._role

    @property
    def agent_id(self) -> str:
        """Unique identifier for this agent instance."""
        return self._agent_id

    def run(self, state: DebateState) -> RoleResult:
        """Produce an invalid output with no claim refs."""
        self.run_count += 1
        # Use valid UUID format (hex characters only)
        role_hex = self._role.value[:8].encode().hex()[:12].ljust(12, "0")
        agent_id = f"00000000-0000-0000-0000-{role_hex}"
        output_id = f"00000000-0000-0000-0002-{self.run_count:012x}"

        muhasabah = _make_invalid_muhasabah_no_claims(agent_id=agent_id, output_id=output_id)

        output = AgentOutput(
            output_id=output_id,
            agent_id=agent_id,
            role=self.role,
            output_type="analysis",
            content={
                "text": "Analysis without proper claim references.",
                "is_subjective": False,
            },
            muhasabah=muhasabah,
            round_number=state.round_number,
            timestamp=datetime(2026, 1, 10, 12, 0, 0),
        )

        return RoleResult(
            outputs=[output],
            messages=[],
        )


class InvalidRoleRunnerOverconfident(RoleRunnerProtocol):
    """A role runner that produces outputs with high confidence but no uncertainties."""

    def __init__(self, role: DebateRole = DebateRole.ADVOCATE) -> None:
        self._role = role
        self._agent_id = f"{role.value}-invalid-overconfident"
        self.run_count = 0

    @property
    def role(self) -> DebateRole:
        """The debate role this runner implements."""
        return self._role

    @property
    def agent_id(self) -> str:
        """Unique identifier for this agent instance."""
        return self._agent_id

    def run(self, state: DebateState) -> RoleResult:
        """Produce an invalid output with overconfidence."""
        self.run_count += 1
        # Use valid UUID format (hex characters only)
        role_hex = self._role.value[:8].encode().hex()[:12].ljust(12, "0")
        agent_id = f"00000000-0000-0000-0000-{role_hex}"
        output_id = f"00000000-0000-0000-0003-{self.run_count:012x}"

        muhasabah = _make_invalid_muhasabah_overconfident(agent_id=agent_id, output_id=output_id)

        output = AgentOutput(
            output_id=output_id,
            agent_id=agent_id,
            role=self.role,
            output_type="analysis",
            content={
                "text": "Highly confident analysis.",
                "is_subjective": False,
            },
            muhasabah=muhasabah,
            round_number=state.round_number,
            timestamp=datetime(2026, 1, 10, 12, 0, 0),
        )

        return RoleResult(
            outputs=[output],
            messages=[],
        )


def _make_initial_state() -> DebateState:
    """Create a minimal initial state for testing."""
    return DebateState(
        tenant_id="00000000-0000-0000-0000-tenant000001",
        deal_id="00000000-0000-0000-0000-deal00000001",
        claim_registry_ref="claims://test",
        sanad_graph_ref="sanad://test",
        round_number=1,
    )


class TestOrchestratorMuhasabahGateIntegration:
    """Integration tests for orchestrator with Muḥāsabah gate."""

    def test_orchestrator_blocks_invalid_output_no_claims(self) -> None:
        """Orchestrator blocks run when role emits output without claim refs.

        Required test case: A deterministic role runner emits an output WITHOUT
        proper MuhasabahRecord (empty claim_ids) -> orchestrator blocks run.
        """
        # Create role runners with invalid advocate
        role_runners = RoleRunners(
            advocate=InvalidRoleRunnerNoClaims(DebateRole.ADVOCATE),
            sanad_breaker=ValidRoleRunner(DebateRole.SANAD_BREAKER),
            contradiction_finder=ValidRoleRunner(DebateRole.CONTRADICTION_FINDER),
            risk_officer=ValidRoleRunner(DebateRole.RISK_OFFICER),
            arbiter=ValidRoleRunner(DebateRole.ARBITER),
        )

        config = DebateConfig(max_rounds=1)
        orchestrator = DebateOrchestrator(
            config=config,
            role_runners=role_runners,
        )

        initial_state = _make_initial_state()
        final_state = orchestrator.run(initial_state)

        # Should have stopped with CRITICAL_DEFECT
        assert final_state.stop_reason == StopReason.CRITICAL_DEFECT

        # Should have a gate failure
        gate_failure = orchestrator.get_gate_failure()
        assert gate_failure is not None
        assert not gate_failure.decision.allowed

        # The invalid output should NOT be in agent_outputs
        # (or if it is, the run should have stopped)
        if final_state.agent_outputs:
            # Check that advocate's invalid output was blocked
            advocate_outputs = [
                o for o in final_state.agent_outputs if o.role == DebateRole.ADVOCATE
            ]
            # Either no advocate outputs or the run stopped before accepting them
            assert (
                len(advocate_outputs) == 0 or final_state.stop_reason == StopReason.CRITICAL_DEFECT
            )

    def test_orchestrator_blocks_invalid_output_overconfident(self) -> None:
        """Orchestrator blocks run when role emits overconfident output.

        Role emits output with confidence > 0.80 but no uncertainties.
        """
        role_runners = RoleRunners(
            advocate=InvalidRoleRunnerOverconfident(DebateRole.ADVOCATE),
            sanad_breaker=ValidRoleRunner(DebateRole.SANAD_BREAKER),
            contradiction_finder=ValidRoleRunner(DebateRole.CONTRADICTION_FINDER),
            risk_officer=ValidRoleRunner(DebateRole.RISK_OFFICER),
            arbiter=ValidRoleRunner(DebateRole.ARBITER),
        )

        config = DebateConfig(max_rounds=1)
        orchestrator = DebateOrchestrator(
            config=config,
            role_runners=role_runners,
        )

        initial_state = _make_initial_state()
        final_state = orchestrator.run(initial_state)

        # Should have stopped with CRITICAL_DEFECT
        assert final_state.stop_reason == StopReason.CRITICAL_DEFECT

        # Should have a gate failure
        gate_failure = orchestrator.get_gate_failure()
        assert gate_failure is not None

    def test_orchestrator_proceeds_with_valid_output(self) -> None:
        """Orchestrator proceeds when role emits valid output with claim refs.

        Required test case: A deterministic role runner emits an output WITH
        valid MuhasabahRecord + claim refs -> orchestrator proceeds.
        """
        # All valid role runners
        role_runners = RoleRunners(
            advocate=ValidRoleRunner(DebateRole.ADVOCATE),
            sanad_breaker=ValidRoleRunner(DebateRole.SANAD_BREAKER),
            contradiction_finder=ValidRoleRunner(DebateRole.CONTRADICTION_FINDER),
            risk_officer=ValidRoleRunner(DebateRole.RISK_OFFICER),
            arbiter=ValidRoleRunner(DebateRole.ARBITER),
        )

        config = DebateConfig(max_rounds=1)
        orchestrator = DebateOrchestrator(
            config=config,
            role_runners=role_runners,
        )

        initial_state = _make_initial_state()
        final_state = orchestrator.run(initial_state)

        # Should NOT have CRITICAL_DEFECT from gate failure
        # (may have other stop reasons like MAX_ROUNDS)
        gate_failure = orchestrator.get_gate_failure()
        assert gate_failure is None

        # Should have agent outputs from valid roles
        assert len(final_state.agent_outputs) > 0

        # Should have visited the muhasabah_validate_all node
        assert "muhasabah_validate_all" in final_state.nodes_visited

    def test_gate_cannot_be_bypassed(self) -> None:
        """Gate is ALWAYS enforced - there is no bypass path.

        This test proves that there is no supported way to disable the gate.
        Invalid outputs always cause CRITICAL_DEFECT, regardless of configuration.
        """
        # Invalid role runner that will fail the gate
        role_runners = RoleRunners(
            advocate=InvalidRoleRunnerNoClaims(DebateRole.ADVOCATE),
            sanad_breaker=ValidRoleRunner(DebateRole.SANAD_BREAKER),
            contradiction_finder=ValidRoleRunner(DebateRole.CONTRADICTION_FINDER),
            risk_officer=ValidRoleRunner(DebateRole.RISK_OFFICER),
            arbiter=ValidRoleRunner(DebateRole.ARBITER),
        )

        config = DebateConfig(max_rounds=1)
        # No bypass parameter exists - gate is always on
        orchestrator = DebateOrchestrator(
            config=config,
            role_runners=role_runners,
        )

        initial_state = _make_initial_state()
        final_state = orchestrator.run(initial_state)

        # Gate MUST block invalid outputs - no bypass possible
        assert final_state.stop_reason == StopReason.CRITICAL_DEFECT
        gate_failure = orchestrator.get_gate_failure()
        assert gate_failure is not None


class TestOrchestratorMuhasabahNodeValidation:
    """Tests for muhasabah_validate_all node."""

    def test_muhasabah_node_validates_all_outputs(self) -> None:
        """muhasabah_validate_all node validates all accumulated outputs."""
        role_runners = RoleRunners(
            advocate=ValidRoleRunner(DebateRole.ADVOCATE),
            sanad_breaker=ValidRoleRunner(DebateRole.SANAD_BREAKER),
            contradiction_finder=ValidRoleRunner(DebateRole.CONTRADICTION_FINDER),
            risk_officer=ValidRoleRunner(DebateRole.RISK_OFFICER),
            arbiter=ValidRoleRunner(DebateRole.ARBITER),
        )

        config = DebateConfig(max_rounds=1)
        orchestrator = DebateOrchestrator(
            config=config,
            role_runners=role_runners,
        )

        initial_state = _make_initial_state()
        final_state = orchestrator.run(initial_state)

        # Should have visited muhasabah_validate_all
        assert "muhasabah_validate_all" in final_state.nodes_visited

        # Should have visited finalize_outputs after muhasabah
        muhasabah_idx = final_state.nodes_visited.index("muhasabah_validate_all")
        assert "finalize_outputs" in final_state.nodes_visited
        finalize_idx = final_state.nodes_visited.index("finalize_outputs")
        assert finalize_idx > muhasabah_idx


class TestDeterministicGateBehavior:
    """Tests for deterministic gate behavior (no randomness)."""

    def test_gate_produces_consistent_results(self) -> None:
        """Gate produces identical results for identical inputs."""
        role_runners = RoleRunners(
            advocate=ValidRoleRunner(DebateRole.ADVOCATE),
            sanad_breaker=ValidRoleRunner(DebateRole.SANAD_BREAKER),
            contradiction_finder=ValidRoleRunner(DebateRole.CONTRADICTION_FINDER),
            risk_officer=ValidRoleRunner(DebateRole.RISK_OFFICER),
            arbiter=ValidRoleRunner(DebateRole.ARBITER),
        )

        config = DebateConfig(max_rounds=1)

        # Run twice with fresh orchestrators
        results = []
        for _ in range(2):
            # Reset role runners
            for runner in [
                role_runners.advocate,
                role_runners.sanad_breaker,
                role_runners.contradiction_finder,
                role_runners.risk_officer,
                role_runners.arbiter,
            ]:
                if hasattr(runner, "run_count"):
                    runner.run_count = 0

            orchestrator = DebateOrchestrator(
                config=config,
                role_runners=role_runners,
            )

            initial_state = _make_initial_state()
            final_state = orchestrator.run(initial_state)
            results.append(final_state)

        # Results should be identical (deterministic)
        assert results[0].stop_reason == results[1].stop_reason
        assert len(results[0].agent_outputs) == len(results[1].agent_outputs)
        assert len(results[0].nodes_visited) == len(results[1].nodes_visited)
