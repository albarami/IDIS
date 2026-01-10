"""IDIS Arbiter Role — v6.3 Phase 5.1

The Arbiter validates challenges and assigns utility:
- Validates that challenges reference evidence/claims
- Assigns utility scores (Brier bonus + penalties)
- Decides whether dissent is evidence-backed

Phase 5.1 implements the interface contract. LLM integration is deferred.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from idis.debate.roles.base import RoleResult, RoleRunner
from idis.models.debate import (
    AgentOutput,
    ArbiterDecision,
    DebateMessage,
    DebateRole,
    MuhasabahRecord,
)

if TYPE_CHECKING:
    from idis.models.debate import DebateState


class ArbiterRole(RoleRunner):
    """Arbiter role runner.

    Responsibilities:
    - Validate challenges reference evidence/claims
    - Assign utility (Brier + penalties)
    - Preserve evidence-backed dissent
    """

    def __init__(self, agent_id: str | None = None) -> None:
        """Initialize arbiter role.

        Args:
            agent_id: Unique identifier. Auto-generated if not provided.
        """
        super().__init__(
            role=DebateRole.ARBITER,
            agent_id=agent_id or f"arbiter-{uuid4().hex[:8]}",
        )

    def run(self, state: DebateState) -> RoleResult:
        """Execute arbiter role.

        Args:
            state: Current debate state.

        Returns:
            RoleResult with arbiter's decision and utility assignments.
        """
        timestamp = datetime.utcnow()
        message_id = f"msg-{uuid4().hex[:12]}"
        output_id = f"out-{uuid4().hex[:12]}"
        record_id = f"muh-{uuid4().hex[:12]}"
        decision_id = f"dec-{uuid4().hex[:12]}"

        message = DebateMessage(
            message_id=message_id,
            role=DebateRole.ARBITER,
            agent_id=self.agent_id,
            content=f"[Arbiter decision for round {state.round_number}]",
            claim_refs=[],
            calc_refs=[],
            round_number=state.round_number,
            timestamp=timestamp,
        )

        muhasabah = MuhasabahRecord(
            record_id=record_id,
            agent_id=self.agent_id,
            output_id=output_id,
            supported_claim_ids=[],
            supported_calc_ids=[],
            falsifiability_tests=[],
            uncertainties=[],
            confidence=0.5,
            failure_modes=[],
            timestamp=timestamp,
        )

        decision = ArbiterDecision(
            decision_id=decision_id,
            round_number=state.round_number,
            challenges_validated=[],
            dissent_preserved=False,
            utility_adjustments={},
            rationale=f"[Arbiter rationale for round {state.round_number}]",
            timestamp=timestamp,
        )

        output = AgentOutput(
            output_id=output_id,
            agent_id=self.agent_id,
            role=DebateRole.ARBITER,
            output_type="decision",
            content={
                "decision": decision.model_dump(),
                "position_hash": f"arbiter-pos-{state.round_number}",
            },
            muhasabah=muhasabah,
            round_number=state.round_number,
            timestamp=timestamp,
        )

        return RoleResult(
            messages=[message],
            outputs=[output],
            position_hash=f"arbiter-pos-{state.round_number}",
        )

    def get_decision_from_result(self, result: RoleResult) -> ArbiterDecision | None:
        """Extract arbiter decision from role result."""
        for output in result.outputs:
            if output.output_type == "decision":
                decision_data = output.content.get("decision")
                if decision_data:
                    return ArbiterDecision(**decision_data)
        return None
