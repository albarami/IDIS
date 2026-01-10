"""IDIS Advocate Role — v6.3 Phase 5.1

The Advocate proposes the investment thesis based on claim registry
and deterministic calculations. All factual statements must reference
claim_ids or calc_ids (No-Free-Facts).

Phase 5.1 implements the interface contract. LLM integration is deferred.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from idis.debate.roles.base import RoleResult, RoleRunner
from idis.models.debate import (
    AgentOutput,
    DebateMessage,
    DebateRole,
    MuhasabahRecord,
)

if TYPE_CHECKING:
    from idis.models.debate import DebateState


class AdvocateRole(RoleRunner):
    """Advocate role runner.

    Responsibilities:
    - Propose investment thesis with claim/calc references
    - Respond to challenges in rebuttal phase
    - Register any new claims (No-Free-Facts)
    """

    def __init__(self, agent_id: str | None = None) -> None:
        """Initialize advocate role.

        Args:
            agent_id: Unique identifier. Auto-generated if not provided.
        """
        super().__init__(
            role=DebateRole.ADVOCATE,
            agent_id=agent_id or f"advocate-{uuid4().hex[:8]}",
        )

    def run(self, state: DebateState) -> RoleResult:
        """Execute advocate role.

        In Phase 5.1, this returns a structured placeholder result.
        The orchestrator injects the actual implementation.

        Args:
            state: Current debate state.

        Returns:
            RoleResult with advocate's messages and outputs.
        """
        timestamp = datetime.utcnow()
        message_id = f"msg-{uuid4().hex[:12]}"
        output_id = f"out-{uuid4().hex[:12]}"
        record_id = f"muh-{uuid4().hex[:12]}"

        is_opening = state.round_number == 1 and not any(
            m.role == DebateRole.ADVOCATE for m in state.messages
        )

        output_type = "opening_thesis" if is_opening else "rebuttal"

        message = DebateMessage(
            message_id=message_id,
            role=DebateRole.ADVOCATE,
            agent_id=self.agent_id,
            content=f"[Advocate {output_type} for round {state.round_number}]",
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

        output = AgentOutput(
            output_id=output_id,
            agent_id=self.agent_id,
            role=DebateRole.ADVOCATE,
            output_type=output_type,
            content={
                "thesis": f"[Structured thesis for round {state.round_number}]",
                "position_hash": f"advocate-pos-{state.round_number}",
            },
            muhasabah=muhasabah,
            round_number=state.round_number,
            timestamp=timestamp,
        )

        return RoleResult(
            messages=[message],
            outputs=[output],
            position_hash=f"advocate-pos-{state.round_number}",
        )
