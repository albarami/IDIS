"""IDIS Advocate Role — v6.3 Phase 5.1

The Advocate proposes the investment thesis based on claim registry
and deterministic calculations. All factual statements must reference
claim_ids or calc_ids (No-Free-Facts).

Phase 5.1 implements the interface contract with deterministic outputs.
LLM integration is deferred to later phases.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from idis.debate.roles.base import (
    RoleResult,
    RoleRunner,
    default_agent_id,
    deterministic_id,
    deterministic_position_hash,
    deterministic_timestamp,
)
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
            agent_id: Unique identifier. Uses deterministic default if not provided.
        """
        super().__init__(
            role=DebateRole.ADVOCATE,
            agent_id=agent_id or default_agent_id(DebateRole.ADVOCATE),
        )

    def run(self, state: DebateState) -> RoleResult:
        """Execute advocate role with deterministic, state-derived outputs.

        Args:
            state: Current debate state.

        Returns:
            RoleResult with advocate's messages and outputs derived from state.
        """
        role_name = self.role.value
        timestamp = deterministic_timestamp(state.round_number, step=0)

        # Deterministic IDs from state
        message_id = deterministic_id(
            "msg",
            tenant_id=state.tenant_id,
            deal_id=state.deal_id,
            role=role_name,
            round_number=state.round_number,
            step=0,
        )
        output_id = deterministic_id(
            "out",
            tenant_id=state.tenant_id,
            deal_id=state.deal_id,
            role=role_name,
            round_number=state.round_number,
            step=0,
        )
        record_id = deterministic_id(
            "muh",
            tenant_id=state.tenant_id,
            deal_id=state.deal_id,
            role=role_name,
            round_number=state.round_number,
            step=0,
        )

        # Determine output type from state
        is_opening = state.round_number == 1 and not any(
            m.role == DebateRole.ADVOCATE for m in state.messages
        )
        output_type = "opening_thesis" if is_opening else "rebuttal"

        # Derive content from state - extract claim/calc refs from prior outputs
        prior_claim_refs = self._extract_prior_claim_refs(state)
        prior_calc_refs = self._extract_prior_calc_refs(state)
        messages_count = len(state.messages)
        outputs_count = len(state.agent_outputs)

        # Build state-derived content summary for position hash
        content_summary = (
            f"claims:{len(prior_claim_refs)}|"
            f"calcs:{len(prior_calc_refs)}|"
            f"msgs:{messages_count}|"
            f"outs:{outputs_count}"
        )

        position_hash = deterministic_position_hash(role_name, state.round_number, content_summary)

        # State-derived message content
        message_content = (
            f"Advocate {output_type} for round {state.round_number}: "
            f"reviewed {len(prior_claim_refs)} claims, {len(prior_calc_refs)} calcs, "
            f"{messages_count} prior messages"
        )

        message = DebateMessage(
            message_id=message_id,
            role=DebateRole.ADVOCATE,
            agent_id=self.agent_id,
            content=message_content,
            claim_refs=sorted(prior_claim_refs),
            calc_refs=sorted(prior_calc_refs),
            round_number=state.round_number,
            timestamp=timestamp,
        )

        # Confidence derived from state (more data = higher confidence)
        base_confidence = 0.5
        confidence_boost = min(0.3, len(prior_claim_refs) * 0.05)
        confidence = min(0.95, base_confidence + confidence_boost)

        # Mark as subjective if no claims to reference (allows gate passage)
        is_subjective = len(prior_claim_refs) == 0 and len(prior_calc_refs) == 0

        muhasabah = MuhasabahRecord(
            record_id=record_id,
            agent_id=self.agent_id,
            output_id=output_id,
            supported_claim_ids=sorted(prior_claim_refs),
            supported_calc_ids=sorted(prior_calc_refs),
            falsifiability_tests=[
                {
                    "test_description": f"Validate claim references for round {state.round_number}",
                    "required_evidence": "Claim registry verification",
                    "pass_fail_rule": "All referenced claims must exist and be valid",
                }
            ],
            uncertainties=[
                {"uncertainty": u, "impact": "MEDIUM", "mitigation": "Further analysis required"}
                for u in self._derive_uncertainties(state)
            ],
            confidence=confidence,
            failure_modes=[f"data_gap_round_{state.round_number}"],
            timestamp=timestamp,
            is_subjective=is_subjective,
        )

        output = AgentOutput(
            output_id=output_id,
            agent_id=self.agent_id,
            role=DebateRole.ADVOCATE,
            output_type=output_type,
            content={
                "thesis_type": output_type,
                "round_number": state.round_number,
                "claims_reviewed": len(prior_claim_refs),
                "calcs_reviewed": len(prior_calc_refs),
                "prior_messages_count": messages_count,
                "claim_refs": sorted(prior_claim_refs),
                "calc_refs": sorted(prior_calc_refs),
                "position_hash": position_hash,
                "is_subjective": is_subjective,
            },
            muhasabah=muhasabah,
            round_number=state.round_number,
            timestamp=timestamp,
        )

        return RoleResult(
            messages=[message],
            outputs=[output],
            position_hash=position_hash,
        )

    def _extract_prior_claim_refs(self, state: DebateState) -> list[str]:
        """Extract all claim references from prior messages (deterministic)."""
        claim_refs: set[str] = set()
        for msg in state.messages:
            claim_refs.update(msg.claim_refs)
        return sorted(claim_refs)

    def _extract_prior_calc_refs(self, state: DebateState) -> list[str]:
        """Extract all calc references from prior messages (deterministic)."""
        calc_refs: set[str] = set()
        for msg in state.messages:
            calc_refs.update(msg.calc_refs)
        return sorted(calc_refs)

    def _derive_uncertainties(self, state: DebateState) -> list[str]:
        """Derive uncertainties from state (deterministic)."""
        uncertainties = []
        if len(state.messages) == 0:
            uncertainties.append("no_prior_discussion")
        if len(state.open_questions) > 0:
            uncertainties.append(f"open_questions_count_{len(state.open_questions)}")
        return sorted(uncertainties)
