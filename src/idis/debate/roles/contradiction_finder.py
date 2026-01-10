"""IDIS Contradiction Finder Role — v6.3 Phase 5.1

The Contradiction Finder detects Matn contradictions:
- Numeric inconsistencies across sources
- Temporal impossibilities
- Logical contradictions in claims

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


class ContradictionFinderRole(RoleRunner):
    """Contradiction Finder role runner.

    Responsibilities:
    - Detect Matn contradictions in claims
    - Flag numeric inconsistencies
    - Identify logical impossibilities
    """

    def __init__(self, agent_id: str | None = None) -> None:
        """Initialize contradiction finder role.

        Args:
            agent_id: Unique identifier. Auto-generated if not provided.
        """
        super().__init__(
            role=DebateRole.CONTRADICTION_FINDER,
            agent_id=agent_id or f"contradiction-finder-{uuid4().hex[:8]}",
        )

    def run(self, state: DebateState) -> RoleResult:
        """Execute contradiction finder role.

        Args:
            state: Current debate state.

        Returns:
            RoleResult with contradiction findings.
        """
        timestamp = datetime.utcnow()
        message_id = f"msg-{uuid4().hex[:12]}"
        output_id = f"out-{uuid4().hex[:12]}"
        record_id = f"muh-{uuid4().hex[:12]}"

        message = DebateMessage(
            message_id=message_id,
            role=DebateRole.CONTRADICTION_FINDER,
            agent_id=self.agent_id,
            content=f"[Contradiction Finder critique for round {state.round_number}]",
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
            role=DebateRole.CONTRADICTION_FINDER,
            output_type="critique",
            content={
                "contradictions_found": [],
                "reconciliation_suggestions": [],
                "position_hash": f"contradiction-finder-pos-{state.round_number}",
            },
            muhasabah=muhasabah,
            round_number=state.round_number,
            timestamp=timestamp,
        )

        return RoleResult(
            messages=[message],
            outputs=[output],
            position_hash=f"contradiction-finder-pos-{state.round_number}",
        )
