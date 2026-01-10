"""IDIS Debate Stop Conditions Tests — v6.3 Phase 5.1

Tests per go-live plan (10_IDIS_GoLive_Execution_Plan_v6_3.md):
- test_debate_stop_conditions.py — asserts priority order implemented and max rounds = 5

Priority order (normative, highest to lowest):
1. CRITICAL_DEFECT - Grade D claim in material position
2. MAX_ROUNDS - Round limit reached (5)
3. CONSENSUS - All agents within 10% confidence range
4. STABLE_DISSENT - No position change for 2 rounds
5. EVIDENCE_EXHAUSTED - No new evidence available
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import pytest

from idis.debate.stop_conditions import (
    STOP_CONDITION_PRIORITY_ORDER,
    StopConditionChecker,
    check_stop_condition,
)
from idis.models.debate import (
    AgentOutput,
    DebateConfig,
    DebateRole,
    DebateState,
    MuhasabahRecord,
    PositionSnapshot,
    StopReason,
)


def create_test_state(
    round_number: int = 1,
    agent_outputs: list[AgentOutput] | None = None,
    position_history: list[PositionSnapshot] | None = None,
    evidence_retrieval_requested: bool = False,
    evidence_retrieval_completed: bool = False,
    open_questions: list[str] | None = None,
) -> DebateState:
    """Create a minimal valid DebateState for testing."""
    return DebateState(
        tenant_id="test-tenant",
        deal_id="test-deal",
        claim_registry_ref="claim-reg-001",
        sanad_graph_ref="sanad-graph-001",
        round_number=round_number,
        agent_outputs=agent_outputs or [],
        position_history=position_history or [],
        evidence_retrieval_requested=evidence_retrieval_requested,
        evidence_retrieval_completed=evidence_retrieval_completed,
        open_questions=open_questions or [],
    )


def create_agent_output(
    round_number: int,
    confidence: float = 0.5,
    critical_defect_detected: bool = False,
    has_grade_d_material_claim: bool = False,
    new_evidence_found: bool = False,
    agent_id: str | None = None,
) -> AgentOutput:
    """Create an AgentOutput for testing."""
    timestamp = datetime.utcnow()
    agent_id = agent_id or f"agent-{uuid4().hex[:8]}"
    output_id = f"out-{uuid4().hex[:12]}"
    record_id = f"muh-{uuid4().hex[:12]}"

    return AgentOutput(
        output_id=output_id,
        agent_id=agent_id,
        role=DebateRole.ADVOCATE,
        output_type="test",
        content={
            "critical_defect_detected": critical_defect_detected,
            "has_grade_d_material_claim": has_grade_d_material_claim,
            "new_evidence_found": new_evidence_found,
            "position_hash": f"pos-{round_number}-{agent_id}",
        },
        muhasabah=MuhasabahRecord(
            record_id=record_id,
            agent_id=agent_id,
            output_id=output_id,
            supported_claim_ids=[],
            supported_calc_ids=[],
            falsifiability_tests=[],
            uncertainties=[],
            confidence=confidence,
            failure_modes=[],
            timestamp=timestamp,
        ),
        round_number=round_number,
        timestamp=timestamp,
    )


class TestPriorityOrder:
    """Tests for stop condition priority order."""

    def test_priority_order_constant_matches_spec(self) -> None:
        """STOP_CONDITION_PRIORITY_ORDER matches v6.3 specification."""
        expected_order = [
            StopReason.CRITICAL_DEFECT,
            StopReason.MAX_ROUNDS,
            StopReason.CONSENSUS,
            StopReason.STABLE_DISSENT,
            StopReason.EVIDENCE_EXHAUSTED,
        ]
        assert expected_order == STOP_CONDITION_PRIORITY_ORDER

    def test_critical_defect_highest_priority(self) -> None:
        """CRITICAL_DEFECT has highest priority (index 0)."""
        assert STOP_CONDITION_PRIORITY_ORDER[0] == StopReason.CRITICAL_DEFECT

    def test_evidence_exhausted_lowest_priority(self) -> None:
        """EVIDENCE_EXHAUSTED has lowest priority (last index)."""
        assert STOP_CONDITION_PRIORITY_ORDER[-1] == StopReason.EVIDENCE_EXHAUSTED

    def test_priority_order_is_complete(self) -> None:
        """All StopReason values are in priority order."""
        all_reasons = set(StopReason)
        ordered_reasons = set(STOP_CONDITION_PRIORITY_ORDER)
        assert all_reasons == ordered_reasons


class TestMaxRounds:
    """Tests for MAX_ROUNDS stop condition (priority 2)."""

    def test_max_rounds_default_is_5(self) -> None:
        """Default max_rounds is 5 per v6.3 spec."""
        config = DebateConfig()
        assert config.max_rounds == 5

    def test_max_rounds_triggers_at_5(self) -> None:
        """MAX_ROUNDS triggers when round_number >= 5."""
        state = create_test_state(round_number=5)
        result = check_stop_condition(state)
        assert result == StopReason.MAX_ROUNDS

    def test_max_rounds_does_not_trigger_before_5(self) -> None:
        """MAX_ROUNDS does not trigger before round 5."""
        for round_num in [1, 2, 3, 4]:
            state = create_test_state(round_number=round_num)
            result = check_stop_condition(state)
            assert result != StopReason.MAX_ROUNDS

    def test_max_rounds_configurable(self) -> None:
        """MAX_ROUNDS can be configured to different value."""
        config = DebateConfig(max_rounds=3)
        state = create_test_state(round_number=3)
        result = check_stop_condition(state, config)
        assert result == StopReason.MAX_ROUNDS

    def test_max_rounds_hard_limit_5(self) -> None:
        """max_rounds cannot exceed 5 (Pydantic validation)."""
        with pytest.raises(ValueError):
            DebateConfig(max_rounds=6)


class TestCriticalDefect:
    """Tests for CRITICAL_DEFECT stop condition (priority 1)."""

    def test_critical_defect_triggers_on_flag(self) -> None:
        """CRITICAL_DEFECT triggers when output has critical_defect_detected."""
        outputs = [create_agent_output(round_number=1, critical_defect_detected=True)]
        state = create_test_state(round_number=1, agent_outputs=outputs)
        result = check_stop_condition(state)
        assert result == StopReason.CRITICAL_DEFECT

    def test_critical_defect_triggers_on_grade_d(self) -> None:
        """CRITICAL_DEFECT triggers when output has has_grade_d_material_claim."""
        outputs = [create_agent_output(round_number=1, has_grade_d_material_claim=True)]
        state = create_test_state(round_number=1, agent_outputs=outputs)
        result = check_stop_condition(state)
        assert result == StopReason.CRITICAL_DEFECT

    def test_critical_defect_takes_priority_over_max_rounds(self) -> None:
        """CRITICAL_DEFECT has higher priority than MAX_ROUNDS."""
        outputs = [create_agent_output(round_number=5, critical_defect_detected=True)]
        state = create_test_state(round_number=5, agent_outputs=outputs)
        result = check_stop_condition(state)
        assert result == StopReason.CRITICAL_DEFECT


class TestConsensus:
    """Tests for CONSENSUS stop condition (priority 3)."""

    def test_consensus_triggers_when_confidences_within_threshold(self) -> None:
        """CONSENSUS triggers when all confidences within 10% range."""
        outputs = [
            create_agent_output(round_number=1, confidence=0.70, agent_id="agent-1"),
            create_agent_output(round_number=1, confidence=0.75, agent_id="agent-2"),
            create_agent_output(round_number=1, confidence=0.78, agent_id="agent-3"),
        ]
        state = create_test_state(round_number=1, agent_outputs=outputs)
        result = check_stop_condition(state)
        assert result == StopReason.CONSENSUS

    def test_consensus_does_not_trigger_when_spread_exceeds_threshold(self) -> None:
        """CONSENSUS does not trigger when confidence spread > 10%."""
        outputs = [
            create_agent_output(round_number=1, confidence=0.50, agent_id="agent-1"),
            create_agent_output(round_number=1, confidence=0.70, agent_id="agent-2"),
        ]
        state = create_test_state(round_number=1, agent_outputs=outputs)
        result = check_stop_condition(state)
        assert result != StopReason.CONSENSUS

    def test_consensus_does_not_trigger_with_no_outputs(self) -> None:
        """CONSENSUS does not trigger when no agent outputs exist."""
        state = create_test_state(round_number=1, agent_outputs=[])
        result = check_stop_condition(state)
        assert result != StopReason.CONSENSUS

    def test_consensus_uses_only_current_round_outputs(self) -> None:
        """CONSENSUS considers only current round's outputs."""
        outputs = [
            create_agent_output(round_number=1, confidence=0.50, agent_id="agent-1"),
            create_agent_output(round_number=2, confidence=0.55, agent_id="agent-2"),
            create_agent_output(round_number=2, confidence=0.58, agent_id="agent-3"),
        ]
        state = create_test_state(round_number=2, agent_outputs=outputs)
        result = check_stop_condition(state)
        assert result == StopReason.CONSENSUS

    def test_consensus_threshold_configurable(self) -> None:
        """Consensus threshold can be configured."""
        config = DebateConfig(consensus_threshold=0.05)
        outputs = [
            create_agent_output(round_number=1, confidence=0.70, agent_id="agent-1"),
            create_agent_output(round_number=1, confidence=0.78, agent_id="agent-2"),
        ]
        state = create_test_state(round_number=1, agent_outputs=outputs)
        result = check_stop_condition(state, config)
        assert result != StopReason.CONSENSUS

    def test_max_rounds_takes_priority_over_consensus(self) -> None:
        """MAX_ROUNDS has higher priority than CONSENSUS."""
        outputs = [
            create_agent_output(round_number=5, confidence=0.70, agent_id="agent-1"),
            create_agent_output(round_number=5, confidence=0.72, agent_id="agent-2"),
        ]
        state = create_test_state(round_number=5, agent_outputs=outputs)
        result = check_stop_condition(state)
        assert result == StopReason.MAX_ROUNDS


class TestStableDissent:
    """Tests for STABLE_DISSENT stop condition (priority 4)."""

    def test_stable_dissent_triggers_after_unchanged_positions(self) -> None:
        """STABLE_DISSENT triggers when positions unchanged for 2 rounds."""
        positions = {"agent-1": "pos-A", "agent-2": "pos-B"}
        history = [
            PositionSnapshot(round_number=1, agent_positions=positions, agent_confidences={}),
            PositionSnapshot(round_number=2, agent_positions=positions, agent_confidences={}),
        ]
        state = create_test_state(round_number=2, position_history=history)
        result = check_stop_condition(state)
        assert result == StopReason.STABLE_DISSENT

    def test_stable_dissent_does_not_trigger_when_positions_change(self) -> None:
        """STABLE_DISSENT does not trigger when positions change."""
        history = [
            PositionSnapshot(
                round_number=1,
                agent_positions={"agent-1": "pos-A"},
                agent_confidences={},
            ),
            PositionSnapshot(
                round_number=2,
                agent_positions={"agent-1": "pos-B"},
                agent_confidences={},
            ),
        ]
        state = create_test_state(round_number=2, position_history=history)
        result = check_stop_condition(state)
        assert result != StopReason.STABLE_DISSENT

    def test_stable_dissent_requires_minimum_history(self) -> None:
        """STABLE_DISSENT requires at least stable_dissent_rounds history."""
        positions = {"agent-1": "pos-A"}
        history = [
            PositionSnapshot(round_number=1, agent_positions=positions, agent_confidences={}),
        ]
        state = create_test_state(round_number=1, position_history=history)
        result = check_stop_condition(state)
        assert result != StopReason.STABLE_DISSENT

    def test_stable_dissent_rounds_configurable(self) -> None:
        """stable_dissent_rounds can be configured."""
        config = DebateConfig(stable_dissent_rounds=3, max_rounds=5)
        positions = {"agent-1": "pos-A"}
        history = [
            PositionSnapshot(round_number=1, agent_positions=positions, agent_confidences={}),
            PositionSnapshot(round_number=2, agent_positions=positions, agent_confidences={}),
        ]
        state = create_test_state(round_number=2, position_history=history)
        result = check_stop_condition(state, config)
        assert result != StopReason.STABLE_DISSENT

        history.append(
            PositionSnapshot(round_number=3, agent_positions=positions, agent_confidences={})
        )
        state = create_test_state(round_number=3, position_history=history)
        result = check_stop_condition(state, config)
        assert result == StopReason.STABLE_DISSENT


class TestEvidenceExhausted:
    """Tests for EVIDENCE_EXHAUSTED stop condition (priority 5)."""

    def test_evidence_exhausted_triggers_when_retrieval_completed_no_new_evidence(self) -> None:
        """EVIDENCE_EXHAUSTED triggers when retrieval done but no new evidence."""
        # Use outputs with spread confidences to avoid CONSENSUS triggering first
        outputs = [
            create_agent_output(
                round_number=1, new_evidence_found=False, confidence=0.30, agent_id="agent-1"
            ),
            create_agent_output(
                round_number=1, new_evidence_found=False, confidence=0.70, agent_id="agent-2"
            ),
        ]
        state = create_test_state(
            round_number=1,
            agent_outputs=outputs,
            evidence_retrieval_requested=True,
            evidence_retrieval_completed=True,
            open_questions=["What is the revenue?"],
        )
        result = check_stop_condition(state)
        assert result == StopReason.EVIDENCE_EXHAUSTED

    def test_evidence_exhausted_does_not_trigger_when_retrieval_not_requested(self) -> None:
        """EVIDENCE_EXHAUSTED does not trigger if retrieval was not requested."""
        state = create_test_state(
            round_number=1,
            evidence_retrieval_requested=False,
            open_questions=["What is the revenue?"],
        )
        result = check_stop_condition(state)
        assert result != StopReason.EVIDENCE_EXHAUSTED

    def test_evidence_exhausted_does_not_trigger_when_new_evidence_found(self) -> None:
        """EVIDENCE_EXHAUSTED does not trigger if new evidence was found."""
        outputs = [create_agent_output(round_number=1, new_evidence_found=True)]
        state = create_test_state(
            round_number=1,
            agent_outputs=outputs,
            evidence_retrieval_requested=True,
            evidence_retrieval_completed=True,
            open_questions=["What is the revenue?"],
        )
        result = check_stop_condition(state)
        assert result != StopReason.EVIDENCE_EXHAUSTED

    def test_evidence_exhausted_does_not_trigger_when_no_open_questions(self) -> None:
        """EVIDENCE_EXHAUSTED does not trigger if no open questions remain."""
        state = create_test_state(
            round_number=1,
            evidence_retrieval_requested=True,
            evidence_retrieval_completed=True,
            open_questions=[],
        )
        result = check_stop_condition(state)
        assert result != StopReason.EVIDENCE_EXHAUSTED


class TestPriorityEnforcement:
    """Tests to verify priority order is enforced."""

    def test_priority_critical_defect_over_all(self) -> None:
        """CRITICAL_DEFECT takes priority over all other conditions."""
        positions = {"agent-1": "pos-A"}
        history = [
            PositionSnapshot(round_number=4, agent_positions=positions, agent_confidences={}),
            PositionSnapshot(round_number=5, agent_positions=positions, agent_confidences={}),
        ]
        outputs = [
            create_agent_output(
                round_number=5,
                confidence=0.70,
                critical_defect_detected=True,
                new_evidence_found=False,
                agent_id="agent-1",
            ),
            create_agent_output(
                round_number=5,
                confidence=0.72,
                agent_id="agent-2",
            ),
        ]
        state = create_test_state(
            round_number=5,
            agent_outputs=outputs,
            position_history=history,
            evidence_retrieval_requested=True,
            evidence_retrieval_completed=True,
            open_questions=["question"],
        )
        result = check_stop_condition(state)
        assert result == StopReason.CRITICAL_DEFECT

    def test_priority_max_rounds_over_consensus(self) -> None:
        """MAX_ROUNDS takes priority over CONSENSUS."""
        outputs = [
            create_agent_output(round_number=5, confidence=0.70, agent_id="agent-1"),
            create_agent_output(round_number=5, confidence=0.72, agent_id="agent-2"),
        ]
        state = create_test_state(round_number=5, agent_outputs=outputs)
        result = check_stop_condition(state)
        assert result == StopReason.MAX_ROUNDS

    def test_priority_consensus_over_stable_dissent(self) -> None:
        """CONSENSUS takes priority over STABLE_DISSENT."""
        positions = {"agent-1": "pos-A", "agent-2": "pos-B"}
        history = [
            PositionSnapshot(
                round_number=1,
                agent_positions=positions,
                agent_confidences={"agent-1": 0.70, "agent-2": 0.72},
            ),
            PositionSnapshot(
                round_number=2,
                agent_positions=positions,
                agent_confidences={"agent-1": 0.70, "agent-2": 0.72},
            ),
        ]
        outputs = [
            create_agent_output(round_number=2, confidence=0.70, agent_id="agent-1"),
            create_agent_output(round_number=2, confidence=0.72, agent_id="agent-2"),
        ]
        state = create_test_state(
            round_number=2,
            agent_outputs=outputs,
            position_history=history,
        )
        result = check_stop_condition(state)
        assert result == StopReason.CONSENSUS

    def test_priority_stable_dissent_over_evidence_exhausted(self) -> None:
        """STABLE_DISSENT takes priority over EVIDENCE_EXHAUSTED."""
        positions = {"agent-1": "pos-A"}
        history = [
            PositionSnapshot(round_number=1, agent_positions=positions, agent_confidences={}),
            PositionSnapshot(round_number=2, agent_positions=positions, agent_confidences={}),
        ]
        state = create_test_state(
            round_number=2,
            position_history=history,
            evidence_retrieval_requested=True,
            evidence_retrieval_completed=True,
            open_questions=["question"],
        )
        result = check_stop_condition(state)
        assert result == StopReason.STABLE_DISSENT


class TestFailClosed:
    """Tests for fail-closed behavior."""

    def test_invalid_round_number_blocked_by_pydantic(self) -> None:
        """Invalid round_number is blocked by Pydantic validation (fail-closed at model level)."""
        with pytest.raises(ValueError):
            DebateState(
                tenant_id="test",
                deal_id="test",
                claim_registry_ref="ref",
                sanad_graph_ref="ref",
                round_number=0,
            )

    def test_round_exceeding_max_blocked_by_pydantic(self) -> None:
        """Round number > 5 is blocked by Pydantic validation (fail-closed at model level)."""
        with pytest.raises(ValueError):
            DebateState(
                tenant_id="test",
                deal_id="test",
                claim_registry_ref="ref",
                sanad_graph_ref="ref",
                round_number=6,
            )


class TestStopConditionChecker:
    """Tests for StopConditionChecker class."""

    def test_checker_uses_default_config(self) -> None:
        """Checker uses default DebateConfig if not provided."""
        checker = StopConditionChecker()
        assert checker.config.max_rounds == 5
        assert checker.config.consensus_threshold == 0.10
        assert checker.config.stable_dissent_rounds == 2

    def test_checker_accepts_custom_config(self) -> None:
        """Checker accepts custom configuration."""
        config = DebateConfig(max_rounds=3, consensus_threshold=0.05)
        checker = StopConditionChecker(config)
        assert checker.config.max_rounds == 3
        assert checker.config.consensus_threshold == 0.05

    def test_check_returns_none_when_no_condition_met(self) -> None:
        """check() returns None when no stop condition is met."""
        checker = StopConditionChecker()
        state = create_test_state(round_number=1)
        result = checker.check(state)
        assert result is None
