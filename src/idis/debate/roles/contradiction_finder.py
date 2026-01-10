"""IDIS Contradiction Finder Role — v6.3 Phase 5.1

The Contradiction Finder detects Matn contradictions:
- Numeric inconsistencies across sources
- Temporal impossibilities
- Logical contradictions in claims

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
            agent_id: Unique identifier. Uses deterministic default if not provided.
        """
        super().__init__(
            role=DebateRole.CONTRADICTION_FINDER,
            agent_id=agent_id or default_agent_id(DebateRole.CONTRADICTION_FINDER),
        )

    def run(self, state: DebateState) -> RoleResult:
        """Execute contradiction finder role with deterministic, state-derived outputs.

        Args:
            state: Current debate state.

        Returns:
            RoleResult with contradiction findings derived from state.
        """
        role_name = self.role.value
        timestamp = deterministic_timestamp(state.round_number, step=2)

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

        # Derive contradictions from state - scan claims for potential conflicts
        scanned_claim_ids = self._extract_all_claim_ids(state)
        contradiction_pairs = self._find_contradiction_pairs(state, scanned_claim_ids)
        reconciliation_suggestions = self._derive_reconciliations(contradiction_pairs)

        # Build state-derived content summary for position hash
        content_summary = (
            f"scanned:{len(scanned_claim_ids)}|"
            f"contradictions:{len(contradiction_pairs)}|"
            f"reconciliations:{len(reconciliation_suggestions)}"
        )

        position_hash = deterministic_position_hash(role_name, state.round_number, content_summary)

        # State-derived message content
        message_content = (
            f"Contradiction Finder critique for round {state.round_number}: "
            f"scanned {len(scanned_claim_ids)} claims, "
            f"found {len(contradiction_pairs)} potential contradictions"
        )

        # Collect all claim IDs involved in contradictions
        contradiction_claim_ids = sorted(
            {
                claim_id
                for pair in contradiction_pairs
                for claim_id in [pair.get("claim_a"), pair.get("claim_b")]
                if claim_id
            }
        )

        message = DebateMessage(
            message_id=message_id,
            role=DebateRole.CONTRADICTION_FINDER,
            agent_id=self.agent_id,
            content=message_content,
            claim_refs=contradiction_claim_ids,
            calc_refs=[],
            round_number=state.round_number,
            timestamp=timestamp,
        )

        # Confidence higher when fewer contradictions found
        base_confidence = 0.8
        confidence_penalty = min(0.4, len(contradiction_pairs) * 0.1)
        confidence = max(0.3, base_confidence - confidence_penalty)

        muhasabah = MuhasabahRecord(
            record_id=record_id,
            agent_id=self.agent_id,
            output_id=output_id,
            supported_claim_ids=sorted(scanned_claim_ids),
            supported_calc_ids=[],
            falsifiability_tests=[
                {
                    "test_id": f"test_contradiction_check_{state.round_number}",
                    "type": "contradiction_check",
                }
            ],
            uncertainties=[
                {"type": u, "severity": "medium"}
                for u in self._derive_uncertainties(state, contradiction_pairs)
            ],
            confidence=confidence,
            failure_modes=[f"matn_conflict_round_{state.round_number}"],
            timestamp=timestamp,
        )

        output = AgentOutput(
            output_id=output_id,
            agent_id=self.agent_id,
            role=DebateRole.CONTRADICTION_FINDER,
            output_type="critique",
            content={
                "round_number": state.round_number,
                "scanned_count": len(scanned_claim_ids),
                "scanned_claim_ids": sorted(scanned_claim_ids),
                "contradiction_count": len(contradiction_pairs),
                "contradictions_found": contradiction_pairs,
                "reconciliation_suggestions": reconciliation_suggestions,
                "grouping_keys_used": ["claim_type", "round_number"],
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

    def _find_contradiction_pairs(
        self, state: DebateState, scanned_claim_ids: list[str]
    ) -> list[dict]:
        """Find potential contradiction pairs deterministically.

        In Phase 5.1, deterministically pair claims based on position in
        sorted list (simulating metric-based contradiction detection).
        """
        pairs = []
        # Deterministic pairing: adjacent claims in sorted order
        for i in range(0, len(scanned_claim_ids) - 1, 2):
            if i + 1 < len(scanned_claim_ids):
                pairs.append(
                    {
                        "claim_a": scanned_claim_ids[i],
                        "claim_b": scanned_claim_ids[i + 1],
                        "contradiction_type": "potential_numeric_conflict",
                        "severity": "minor" if i % 2 == 0 else "major",
                    }
                )
        return pairs

    def _derive_reconciliations(self, contradiction_pairs: list[dict]) -> list[dict]:
        """Derive reconciliation suggestions for contradictions (deterministic)."""
        suggestions = []
        for i, pair in enumerate(contradiction_pairs):
            suggestion_type = (
                "unit_conversion_check"
                if i % 3 == 0
                else "time_window_alignment"
                if i % 3 == 1
                else "rounding_tolerance"
            )
            suggestions.append(
                {
                    "pair_index": i,
                    "claim_a": pair.get("claim_a"),
                    "claim_b": pair.get("claim_b"),
                    "suggested_action": suggestion_type,
                    "priority": i + 1,
                }
            )
        return suggestions

    def _derive_uncertainties(
        self, state: DebateState, contradiction_pairs: list[dict]
    ) -> list[str]:
        """Derive uncertainties from state (deterministic)."""
        uncertainties = []
        if len(contradiction_pairs) > 0:
            uncertainties.append(f"contradictions_found_count_{len(contradiction_pairs)}")
        if len(state.messages) < 3:
            uncertainties.append("limited_message_context")
        return sorted(uncertainties)
