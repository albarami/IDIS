"""IDIS Risk Officer Role — v6.3 Phase 5.1

The Risk Officer identifies risks:
- Downside scenarios
- Fraud indicators
- Regulatory concerns

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


class RiskOfficerRole(RoleRunner):
    """Risk Officer role runner.

    Responsibilities:
    - Identify downside scenarios
    - Flag fraud indicators
    - Surface regulatory concerns
    """

    def __init__(self, agent_id: str | None = None) -> None:
        """Initialize risk officer role.

        Args:
            agent_id: Unique identifier. Uses deterministic default if not provided.
        """
        super().__init__(
            role=DebateRole.RISK_OFFICER,
            agent_id=agent_id or default_agent_id(DebateRole.RISK_OFFICER),
        )

    def run(self, state: DebateState) -> RoleResult:
        """Execute risk officer role with deterministic, state-derived outputs.

        Args:
            state: Current debate state.

        Returns:
            RoleResult with risk assessment derived from state.
        """
        role_name = self.role.value
        timestamp = deterministic_timestamp(state.round_number, step=3)

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

        # Derive risk flags from state
        scanned_claim_ids = self._extract_all_claim_ids(state)
        risks_identified = self._derive_risks(state, scanned_claim_ids)
        fraud_indicators = self._derive_fraud_indicators(state)
        regulatory_concerns = self._derive_regulatory_concerns(state)

        # Build state-derived content summary for position hash
        content_summary = (
            f"scanned:{len(scanned_claim_ids)}|"
            f"risks:{len(risks_identified)}|"
            f"fraud:{len(fraud_indicators)}|"
            f"regulatory:{len(regulatory_concerns)}"
        )

        position_hash = deterministic_position_hash(role_name, state.round_number, content_summary)

        total_flags = len(risks_identified) + len(fraud_indicators) + len(regulatory_concerns)

        # State-derived message content
        message_content = (
            f"Risk Officer assessment for round {state.round_number}: "
            f"scanned {len(scanned_claim_ids)} claims, "
            f"identified {total_flags} total flags "
            f"({len(risks_identified)} risks, {len(fraud_indicators)} fraud, "
            f"{len(regulatory_concerns)} regulatory)"
        )

        # Collect all claim IDs referenced in risks (filter None, ensure list[str])
        risk_claim_ids: list[str] = sorted(
            str(claim_ref)
            for risk in risks_identified
            if (claim_ref := risk.get("claim_ref")) is not None
            and isinstance(claim_ref, str)
        )

        message = DebateMessage(
            message_id=message_id,
            role=DebateRole.RISK_OFFICER,
            agent_id=self.agent_id,
            content=message_content,
            claim_refs=risk_claim_ids,
            calc_refs=[],
            round_number=state.round_number,
            timestamp=timestamp,
        )

        # Confidence inversely related to number of risks
        base_confidence = 0.75
        confidence_penalty = min(0.35, total_flags * 0.05)
        confidence = max(0.3, base_confidence - confidence_penalty)

        muhasabah = MuhasabahRecord(
            record_id=record_id,
            agent_id=self.agent_id,
            output_id=output_id,
            supported_claim_ids=sorted(scanned_claim_ids),
            supported_calc_ids=[],
            falsifiability_tests=[
                {"test_id": f"test_risk_assessment_{state.round_number}", "type": "risk_assessment"}
            ],
            uncertainties=[
                {"type": u, "severity": "medium"}
                for u in self._derive_uncertainties(state, total_flags)
            ],
            confidence=confidence,
            failure_modes=[f"risk_blind_spot_round_{state.round_number}"],
            timestamp=timestamp,
        )

        output = AgentOutput(
            output_id=output_id,
            agent_id=self.agent_id,
            role=DebateRole.RISK_OFFICER,
            output_type="risk_assessment",
            content={
                "round_number": state.round_number,
                "scanned_count": len(scanned_claim_ids),
                "scanned_claim_ids": sorted(scanned_claim_ids),
                "risk_count": total_flags,
                "risks_identified": risks_identified,
                "fraud_indicators": fraud_indicators,
                "regulatory_concerns": regulatory_concerns,
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

    def _derive_risks(
        self, state: DebateState, scanned_claim_ids: list[str]
    ) -> list[dict[str, str | int]]:
        """Derive risk flags from state (deterministic).

        In Phase 5.1, deterministically flag risks based on position in
        sorted claim list and round number.
        """
        risks: list[dict[str, str | int]] = []
        # Deterministic risk flagging: every 3rd claim based on round
        for i, claim_id in enumerate(scanned_claim_ids):
            if (i + state.round_number) % 3 == 0:
                risk_type = (
                    "downside_scenario"
                    if i % 3 == 0
                    else "data_quality_concern"
                    if i % 3 == 1
                    else "execution_risk"
                )
                risks.append(
                    {
                        "claim_ref": claim_id,
                        "risk_type": risk_type,
                        "severity": "high" if i % 2 == 0 else "medium",
                        "priority": len(risks) + 1,
                    }
                )
        return risks

    def _derive_fraud_indicators(self, state: DebateState) -> list[dict]:
        """Derive fraud indicators from state (deterministic)."""
        indicators = []
        # Deterministic fraud flagging based on open questions
        for i, _question in enumerate(state.open_questions):
            if i % 2 == 0:
                indicators.append(
                    {
                        "indicator_type": "unanswered_material_question",
                        "question_ref": f"q_{i}",
                        "severity": "medium",
                    }
                )
        return indicators

    def _derive_regulatory_concerns(self, state: DebateState) -> list[dict]:
        """Derive regulatory concerns from state (deterministic)."""
        concerns = []
        # Deterministic regulatory flagging based on round and message count
        if state.round_number >= 2 and len(state.messages) > 5:
            concerns.append(
                {
                    "concern_type": "disclosure_completeness",
                    "severity": "low",
                    "messages_reviewed": len(state.messages),
                }
            )
        return concerns

    def _derive_uncertainties(self, state: DebateState, total_flags: int) -> list[str]:
        """Derive uncertainties from state (deterministic)."""
        uncertainties = []
        if total_flags > 0:
            uncertainties.append(f"risk_flags_count_{total_flags}")
        if len(state.open_questions) > 0:
            uncertainties.append(f"unanswered_questions_{len(state.open_questions)}")
        return sorted(uncertainties)
