"""IDIS Sanad Breaker Role — v6.3 Phase 5.1

The Sanad Breaker challenges weak evidence chains:
- Attacks BROKEN_CHAIN, MISSING_LINK, UNKNOWN_SOURCE defects
- Surfaces grade C/D claims in material positions
- Proposes cure protocols (REQUEST_SOURCE, RECONSTRUCT_CHAIN)

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
            agent_id: Unique identifier. Uses deterministic default if not provided.
        """
        super().__init__(
            role=DebateRole.SANAD_BREAKER,
            agent_id=agent_id or default_agent_id(DebateRole.SANAD_BREAKER),
        )

    def run(self, state: DebateState) -> RoleResult:
        """Execute sanad breaker role with deterministic, state-derived outputs.

        Args:
            state: Current debate state.

        Returns:
            RoleResult with sanad breaker's challenges derived from state.
        """
        role_name = self.role.value
        timestamp = deterministic_timestamp(state.round_number, step=1)

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

        # Derive challenged claims from state - scan prior outputs for weak claims
        scanned_claim_ids = self._extract_all_claim_ids(state)
        challenged_claim_ids = self._derive_challenged_claims(state, scanned_claim_ids)
        cure_protocols = self._derive_cure_protocols(challenged_claim_ids)

        # Build state-derived content summary for position hash
        content_summary = (
            f"scanned:{len(scanned_claim_ids)}|"
            f"challenged:{len(challenged_claim_ids)}|"
            f"cures:{len(cure_protocols)}"
        )

        position_hash = deterministic_position_hash(role_name, state.round_number, content_summary)

        # State-derived message content
        message_content = (
            f"Sanad Breaker challenge for round {state.round_number}: "
            f"scanned {len(scanned_claim_ids)} claims, "
            f"challenged {len(challenged_claim_ids)}, "
            f"proposed {len(cure_protocols)} cure protocols"
        )

        message = DebateMessage(
            message_id=message_id,
            role=DebateRole.SANAD_BREAKER,
            agent_id=self.agent_id,
            content=message_content,
            claim_refs=sorted(challenged_claim_ids),
            calc_refs=[],
            round_number=state.round_number,
            timestamp=timestamp,
        )

        # Confidence inversely related to number of challenged claims
        base_confidence = 0.7
        confidence_penalty = min(0.3, len(challenged_claim_ids) * 0.1)
        confidence = max(0.3, base_confidence - confidence_penalty)

        muhasabah = MuhasabahRecord(
            record_id=record_id,
            agent_id=self.agent_id,
            output_id=output_id,
            supported_claim_ids=sorted(scanned_claim_ids),
            supported_calc_ids=[],
            falsifiability_tests=[
                {"test_id": f"test_sanad_chain_{state.round_number}", "type": "chain_validation"}
            ],
            uncertainties=[
                {"type": u, "severity": "medium"}
                for u in self._derive_uncertainties(state, challenged_claim_ids)
            ],
            confidence=confidence,
            failure_modes=[f"weak_chain_round_{state.round_number}"],
            timestamp=timestamp,
        )

        output = AgentOutput(
            output_id=output_id,
            agent_id=self.agent_id,
            role=DebateRole.SANAD_BREAKER,
            output_type="challenge",
            content={
                "round_number": state.round_number,
                "scanned_count": len(scanned_claim_ids),
                "challenged_count": len(challenged_claim_ids),
                "scanned_claim_ids": sorted(scanned_claim_ids),
                "challenged_claim_ids": sorted(challenged_claim_ids),
                "cure_protocols_proposed": cure_protocols,
                "position_hash": position_hash,
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

    def _extract_all_claim_ids(self, state: DebateState) -> list[str]:
        """Extract all claim IDs from state messages (deterministic)."""
        claim_ids: set[str] = set()
        for msg in state.messages:
            claim_ids.update(msg.claim_refs)
        return sorted(claim_ids)

    def _derive_challenged_claims(
        self, state: DebateState, scanned_claim_ids: list[str]
    ) -> list[str]:
        """Derive which claims to challenge based on state (deterministic).

        In Phase 5.1, deterministically select claims based on round number
        and position in sorted list (simulating grade-based selection).
        """
        if not scanned_claim_ids:
            return []
        # Deterministic selection: challenge every Nth claim based on round
        n = max(1, state.round_number)
        challenged = [c for i, c in enumerate(scanned_claim_ids) if i % n == 0]
        return sorted(challenged)

    def _derive_cure_protocols(self, challenged_claim_ids: list[str]) -> list[dict]:
        """Derive cure protocols for challenged claims (deterministic)."""
        protocols = []
        for i, claim_id in enumerate(challenged_claim_ids):
            protocol_type = "REQUEST_SOURCE" if i % 2 == 0 else "RECONSTRUCT_CHAIN"
            protocols.append(
                {
                    "claim_id": claim_id,
                    "protocol": protocol_type,
                    "priority": i + 1,
                }
            )
        return protocols

    def _derive_uncertainties(
        self, state: DebateState, challenged_claim_ids: list[str]
    ) -> list[str]:
        """Derive uncertainties from state (deterministic)."""
        uncertainties = []
        if len(challenged_claim_ids) > 0:
            uncertainties.append(f"challenged_claims_count_{len(challenged_claim_ids)}")
        if state.round_number == 1:
            uncertainties.append("first_round_limited_context")
        return sorted(uncertainties)
