"""IDIS Debate Stop Conditions — v6.3 Phase 5.1

Deterministic stop condition evaluation per v6.3 Appendix C-1.

Priority order (normative, highest to lowest):
1. CRITICAL_DEFECT - Grade D claim in material position
2. MAX_ROUNDS - Round limit reached (5)
3. CONSENSUS - All agents within 10% confidence range
4. STABLE_DISSENT - No position change for 2 rounds
5. EVIDENCE_EXHAUSTED - No new evidence available

The checker is pure and deterministic. It raises StopConditionError
on missing required state fields (fail-closed behavior).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from idis.models.debate import DebateConfig, StopReason

if TYPE_CHECKING:
    from idis.models.debate import DebateState


class StopConditionError(Exception):
    """Raised when stop condition evaluation fails due to invalid state.

    This triggers fail-closed behavior per v6.3 §1.7.
    """

    pass


class StopConditionChecker:
    """Deterministic stop condition evaluator.

    Evaluates stop conditions in strict priority order. All methods are
    pure functions that depend only on the input state and config.

    Priority order (checked in sequence, first match wins):
    1. CRITICAL_DEFECT
    2. MAX_ROUNDS
    3. CONSENSUS
    4. STABLE_DISSENT
    5. EVIDENCE_EXHAUSTED
    """

    def __init__(self, config: DebateConfig | None = None) -> None:
        """Initialize with optional config.

        Args:
            config: Debate configuration. Defaults to DebateConfig() with
                    max_rounds=5, consensus_threshold=0.10, stable_dissent_rounds=2.
        """
        self.config = config or DebateConfig()

    def check(self, state: DebateState) -> StopReason | None:
        """Evaluate all stop conditions in priority order.

        Args:
            state: Current debate state.

        Returns:
            StopReason if a condition is met, None otherwise.

        Raises:
            StopConditionError: If required state fields are missing or invalid.
        """
        self._validate_state(state)

        if self._check_critical_defect(state):
            return StopReason.CRITICAL_DEFECT

        if self._check_max_rounds(state):
            return StopReason.MAX_ROUNDS

        if self._check_consensus(state):
            return StopReason.CONSENSUS

        if self._check_stable_dissent(state):
            return StopReason.STABLE_DISSENT

        if self._check_evidence_exhausted(state):
            return StopReason.EVIDENCE_EXHAUSTED

        return None

    def _validate_state(self, state: DebateState) -> None:
        """Validate that state has required fields for evaluation.

        Raises:
            StopConditionError: On missing or invalid fields.
        """
        if state.round_number < 1:
            raise StopConditionError(f"Invalid round_number: {state.round_number}. Must be >= 1.")
        if state.round_number > self.config.max_rounds:
            raise StopConditionError(
                f"Invalid round_number: {state.round_number}. "
                f"Exceeds max_rounds: {self.config.max_rounds}."
            )

    def _check_critical_defect(self, state: DebateState) -> bool:
        """Check for Grade D claim in material position (priority 1).

        Returns True if any agent output indicates a critical defect via
        its content containing a critical_defect_detected flag.

        This is a deterministic check based on state data, not external lookups.
        """
        has_critical_defect = any(
            output.content.get("critical_defect_detected", False)
            or output.content.get("has_grade_d_material_claim", False)
            for output in state.agent_outputs
        )
        return has_critical_defect

    def _check_max_rounds(self, state: DebateState) -> bool:
        """Check if max rounds reached (priority 2).

        v6.3 mandates max_rounds = 5.
        """
        return state.round_number >= self.config.max_rounds

    def _check_consensus(self, state: DebateState) -> bool:
        """Check if all agents within consensus threshold (priority 3).

        Consensus is reached when the max - min confidence across all
        agent outputs in the current round is <= consensus_threshold (0.10).

        Returns False if no confidence data is available (conservative).
        """
        confidences = self._collect_current_round_confidences(state)
        if not confidences:
            return False

        confidence_spread = max(confidences) - min(confidences)
        return confidence_spread <= self.config.consensus_threshold

    def _check_stable_dissent(self, state: DebateState) -> bool:
        """Check if positions unchanged for N rounds (priority 4).

        Stable dissent occurs when agent positions have not changed
        for stable_dissent_rounds consecutive rounds (default: 2).
        """
        history = state.position_history
        required_rounds = self.config.stable_dissent_rounds

        if len(history) < required_rounds:
            return False

        recent_snapshots = history[-required_rounds:]
        if not recent_snapshots:
            return False

        first_positions = recent_snapshots[0].agent_positions
        return all(snapshot.agent_positions == first_positions for snapshot in recent_snapshots[1:])

    def _check_evidence_exhausted(self, state: DebateState) -> bool:
        """Check if no new evidence is available (priority 5).

        Evidence is exhausted when:
        - Evidence retrieval was requested and completed, AND
        - No new claims were added in the last round, AND
        - Open questions remain unanswered
        """
        if not state.evidence_retrieval_requested:
            return False

        if not state.evidence_retrieval_completed:
            return False

        current_round_outputs = [
            o for o in state.agent_outputs if o.round_number == state.round_number
        ]
        new_evidence_found = any(
            o.content.get("new_evidence_found", False) for o in current_round_outputs
        )

        if new_evidence_found:
            return False

        return len(state.open_questions) > 0

    def _collect_current_round_confidences(self, state: DebateState) -> list[float]:
        """Collect confidence scores from current round outputs."""
        confidences: list[float] = []
        for output in state.agent_outputs:
            if output.round_number == state.round_number:
                confidences.append(output.muhasabah.confidence)
        return confidences


def check_stop_condition(
    state: DebateState, config: DebateConfig | None = None
) -> StopReason | None:
    """Convenience function for stop condition evaluation.

    Args:
        state: Current debate state.
        config: Optional configuration. Uses defaults if not provided.

    Returns:
        StopReason if a condition is met, None otherwise.

    Raises:
        StopConditionError: If state validation fails.
    """
    checker = StopConditionChecker(config)
    return checker.check(state)


STOP_CONDITION_PRIORITY_ORDER: list[StopReason] = [
    StopReason.CRITICAL_DEFECT,
    StopReason.MAX_ROUNDS,
    StopReason.CONSENSUS,
    StopReason.STABLE_DISSENT,
    StopReason.EVIDENCE_EXHAUSTED,
]
"""Normative priority order for stop conditions (highest to lowest)."""
