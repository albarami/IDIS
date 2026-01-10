"""IDIS Arbiter Role — v6.3 Phase 5.1

The Arbiter validates challenges and assigns utility:
- Validates that challenges reference evidence/claims
- Assigns utility scores (Brier bonus + penalties)
- Decides whether dissent is evidence-backed

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
            agent_id: Unique identifier. Uses deterministic default if not provided.
        """
        super().__init__(
            role=DebateRole.ARBITER,
            agent_id=agent_id or default_agent_id(DebateRole.ARBITER),
        )

    def run(self, state: DebateState) -> RoleResult:
        """Execute arbiter role with deterministic, state-derived outputs.

        Args:
            state: Current debate state.

        Returns:
            RoleResult with arbiter's decision and utility assignments derived from state.
        """
        role_name = self.role.value
        timestamp = deterministic_timestamp(state.round_number, step=4)

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
        decision_id = deterministic_id(
            "dec",
            tenant_id=state.tenant_id,
            deal_id=state.deal_id,
            role=role_name,
            round_number=state.round_number,
            step=0,
        )

        # Derive decision components from state
        challenges_validated = self._validate_challenges(state)
        utility_adjustments = self._compute_utility_adjustments(state)
        dissent_preserved = self._check_dissent_preservation(state)
        rationale = self._derive_rationale(state, challenges_validated, dissent_preserved)

        # Build state-derived content summary for position hash
        content_summary = (
            f"challenges:{len(challenges_validated)}|"
            f"dissent:{dissent_preserved}|"
            f"adjustments:{len(utility_adjustments)}"
        )

        position_hash = deterministic_position_hash(role_name, state.round_number, content_summary)

        # Collect all claim IDs from validated challenges
        validated_claim_ids = sorted(
            {c.get("claim_ref") for c in challenges_validated if c.get("claim_ref")}
        )

        # State-derived message content
        message_content = (
            f"Arbiter decision for round {state.round_number}: "
            f"validated {len(challenges_validated)} challenges, "
            f"dissent_preserved={dissent_preserved}, "
            f"{len(utility_adjustments)} utility adjustments"
        )

        message = DebateMessage(
            message_id=message_id,
            role=DebateRole.ARBITER,
            agent_id=self.agent_id,
            content=message_content,
            claim_refs=validated_claim_ids,
            calc_refs=[],
            round_number=state.round_number,
            timestamp=timestamp,
        )

        # Confidence based on number of challenges validated
        base_confidence = 0.7
        confidence_boost = min(0.2, len(challenges_validated) * 0.05)
        confidence = min(0.9, base_confidence + confidence_boost)

        muhasabah = MuhasabahRecord(
            record_id=record_id,
            agent_id=self.agent_id,
            output_id=output_id,
            supported_claim_ids=validated_claim_ids,
            supported_calc_ids=[],
            falsifiability_tests=[
                {
                    "test_id": f"test_arbiter_decision_{state.round_number}",
                    "type": "decision_validation",
                }
            ],
            uncertainties=[
                {"type": u, "severity": "medium"} for u in self._derive_uncertainties(state)
            ],
            confidence=confidence,
            failure_modes=[f"judgment_error_round_{state.round_number}"],
            timestamp=timestamp,
        )

        decision = ArbiterDecision(
            decision_id=decision_id,
            round_number=state.round_number,
            challenges_validated=challenges_validated,
            dissent_preserved=dissent_preserved,
            utility_adjustments=utility_adjustments,
            rationale=rationale,
            timestamp=timestamp,
        )

        output = AgentOutput(
            output_id=output_id,
            agent_id=self.agent_id,
            role=DebateRole.ARBITER,
            output_type="decision",
            content={
                "decision": decision.model_dump(),
                "round_number": state.round_number,
                "challenges_count": len(challenges_validated),
                "dissent_preserved": dissent_preserved,
                "utility_adjustments_count": len(utility_adjustments),
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

    def _validate_challenges(self, state: DebateState) -> list[dict]:
        """Validate challenges from prior outputs (deterministic).

        In Phase 5.1, deterministically validate based on output content.
        """
        validated = []
        for i, output in enumerate(state.agent_outputs):
            if output.output_type == "challenge":
                content = output.content or {}
                challenged_ids = content.get("challenged_claim_ids", [])
                for j, claim_id in enumerate(challenged_ids):
                    validated.append(
                        {
                            "challenge_index": i,
                            "claim_ref": claim_id,
                            "validation_status": "accepted" if (i + j) % 2 == 0 else "pending",
                            "evidence_quality": "sufficient" if j % 3 == 0 else "partial",
                        }
                    )
        return validated

    def _compute_utility_adjustments(self, state: DebateState) -> dict[str, float]:
        """Compute utility adjustments for agents (deterministic).

        In Phase 5.1, deterministic adjustments based on output counts.
        """
        adjustments: dict[str, float] = {}
        for output in state.agent_outputs:
            agent_id = output.agent_id
            if agent_id not in adjustments:
                adjustments[agent_id] = 0.0
            # Deterministic adjustment based on role and confidence
            confidence = output.muhasabah.confidence if output.muhasabah else 0.5
            adjustment = round((confidence - 0.5) * 0.1, 4)
            adjustments[agent_id] = round(adjustments[agent_id] + adjustment, 4)
        return dict(sorted(adjustments.items()))

    def _check_dissent_preservation(self, state: DebateState) -> bool:
        """Check if dissent should be preserved (deterministic).

        Preserve dissent if there are validated challenges with evidence.
        """
        # Deterministic: preserve if round > 1 and there are outputs with low confidence
        if state.round_number <= 1:
            return False
        low_confidence_count = sum(
            1 for o in state.agent_outputs if o.muhasabah and o.muhasabah.confidence < 0.6
        )
        return low_confidence_count >= 2

    def _derive_rationale(
        self,
        state: DebateState,
        challenges_validated: list[dict],
        dissent_preserved: bool,
    ) -> str:
        """Derive decision rationale from state (deterministic)."""
        parts = [
            f"Round {state.round_number} assessment:",
            f"reviewed {len(state.agent_outputs)} agent outputs,",
            f"validated {len(challenges_validated)} challenges,",
        ]
        if dissent_preserved:
            parts.append("dissent preserved due to evidence-backed disagreement,")
        if state.stop_reason:
            parts.append(f"stop condition: {state.stop_reason.value},")
        parts.append(f"total messages in transcript: {len(state.messages)}")
        return " ".join(parts)

    def _derive_uncertainties(self, state: DebateState) -> list[str]:
        """Derive uncertainties from state (deterministic)."""
        uncertainties = []
        if state.round_number == 1:
            uncertainties.append("first_round_limited_context")
        if len(state.agent_outputs) < 3:
            uncertainties.append("insufficient_agent_outputs")
        return sorted(uncertainties)

    def get_decision_from_result(self, result: RoleResult) -> ArbiterDecision | None:
        """Extract arbiter decision from role result."""
        for output in result.outputs:
            if output.output_type == "decision":
                decision_data = output.content.get("decision")
                if decision_data:
                    return ArbiterDecision(**decision_data)
        return None
