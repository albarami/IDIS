"""IDIS Sanad Breaker Role — v6.3 Phase 5.1

The Sanad Breaker challenges weak evidence chains:
- Attacks BROKEN_CHAIN, MISSING_LINK, UNKNOWN_SOURCE defects
- Surfaces grade C/D claims in material positions
- Proposes cure protocols (REQUEST_SOURCE, RECONSTRUCT_CHAIN)

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


class SanadBreakerRole(RoleRunner):
    """Sanad Breaker role runner.

    Responsibilities:
    - Challenge weak Sanad chains
    - Surface grade C/D claims in material positions
    - Propose cure protocols for defects
    """

    def __init__(self, agent_id: str | None = None) -> None:
        """Initialize sanad breaker role.

        Args:
            agent_id: Unique identifier. Auto-generated if not provided.
        """
        super().__init__(
            role=DebateRole.SANAD_BREAKER,
            agent_id=agent_id or f"sanad-breaker-{uuid4().hex[:8]}",
        )

    def run(self, state: DebateState) -> RoleResult:
        """Execute sanad breaker role.

        Args:
            state: Current debate state.

        Returns:
            RoleResult with sanad breaker's challenges.
        """
        timestamp = datetime.utcnow()
        message_id = f"msg-{uuid4().hex[:12]}"
        output_id = f"out-{uuid4().hex[:12]}"
        record_id = f"muh-{uuid4().hex[:12]}"

        message = DebateMessage(
            message_id=message_id,
            role=DebateRole.SANAD_BREAKER,
            agent_id=self.agent_id,
            content=f"[Sanad Breaker challenge for round {state.round_number}]",
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
            role=DebateRole.SANAD_BREAKER,
            output_type="challenge",
            content={
                "challenges": [],
                "cure_protocols_proposed": [],
                "position_hash": f"sanad-breaker-pos-{state.round_number}",
            },
            muhasabah=muhasabah,
            round_number=state.round_number,
            timestamp=timestamp,
        )

        return RoleResult(
            messages=[message],
            outputs=[output],
            position_hash=f"sanad-breaker-pos-{state.round_number}",
        )
