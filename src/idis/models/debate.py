"""IDIS Debate Domain Models — v6.3 Phase 5.1

Pydantic models for debate orchestration per Appendix C-1.
These models represent the canonical state for LangGraph debate runs.

Non-negotiables:
- All fields required by v6.3 node graph are present
- StopReason enum matches priority order exactly
- DebateState is immutable-friendly (frozen where appropriate)
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StopReason(str, Enum):
    """Stop condition reasons in v6.3 priority order (highest to lowest).

    Priority order (normative):
    1. CRITICAL_DEFECT - Grade D claim in material position
    2. MAX_ROUNDS - Round limit reached (5)
    3. CONSENSUS - All agents within 10% confidence range
    4. STABLE_DISSENT - No position change for 2 rounds
    5. EVIDENCE_EXHAUSTED - No new evidence available
    """

    CRITICAL_DEFECT = "CRITICAL_DEFECT"
    MAX_ROUNDS = "MAX_ROUNDS"
    CONSENSUS = "CONSENSUS"
    STABLE_DISSENT = "STABLE_DISSENT"
    EVIDENCE_EXHAUSTED = "EVIDENCE_EXHAUSTED"


class DebateRole(str, Enum):
    """Agent roles in the debate per v6.3 roadmap."""

    ADVOCATE = "advocate"
    SANAD_BREAKER = "sanad_breaker"
    CONTRADICTION_FINDER = "contradiction_finder"
    RISK_OFFICER = "risk_officer"
    ARBITER = "arbiter"


class DebateMessage(BaseModel):
    """A single message in the debate transcript.

    All factual content must reference claim_ids or calc_ids (No-Free-Facts).
    """

    model_config = ConfigDict(frozen=True)

    message_id: str = Field(..., description="Unique message identifier")
    role: DebateRole = Field(..., description="Role of the agent sending this message")
    agent_id: str = Field(..., description="Identifier of the agent instance")
    content: str = Field(..., description="Message content")
    claim_refs: list[str] = Field(
        default_factory=list, description="Referenced claim_ids for No-Free-Facts"
    )
    calc_refs: list[str] = Field(
        default_factory=list, description="Referenced calc_ids for No-Free-Facts"
    )
    round_number: int = Field(..., ge=1, le=5, description="Round when message was sent")
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class MuhasabahRecord(BaseModel):
    """Muḥāsabah record for agent outputs per v6.3 Appendix E.

    Phase 5.1: This is a structural model only. The hard gate validation
    is implemented in Phase 5.2.
    """

    model_config = ConfigDict(frozen=True)

    record_id: str = Field(..., description="Unique record identifier")
    agent_id: str = Field(..., description="Agent that produced this record")
    output_id: str = Field(..., description="Associated output identifier")
    supported_claim_ids: list[str] = Field(
        default_factory=list, description="Claims supporting the output"
    )
    supported_calc_ids: list[str] = Field(
        default_factory=list, description="Calculations supporting the output"
    )
    falsifiability_tests: list[dict[str, Any]] = Field(
        default_factory=list, description="Tests that could falsify the output"
    )
    uncertainties: list[dict[str, Any]] = Field(
        default_factory=list, description="Registered uncertainties"
    )
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence score")
    failure_modes: list[str] = Field(default_factory=list, description="Identified failure modes")
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class AgentOutput(BaseModel):
    """Structured output from an agent with required MuḥāsabahRecord."""

    model_config = ConfigDict(frozen=True)

    output_id: str = Field(..., description="Unique output identifier")
    agent_id: str = Field(..., description="Agent that produced this output")
    role: DebateRole = Field(..., description="Role of the agent")
    output_type: str = Field(..., description="Type of output (e.g., thesis, challenge)")
    content: dict[str, Any] = Field(..., description="Structured output content")
    muhasabah: MuhasabahRecord = Field(..., description="Required Muḥāsabah record")
    round_number: int = Field(..., ge=1, le=5, description="Round when output was produced")
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class ArbiterDecision(BaseModel):
    """Decision record from the arbiter."""

    model_config = ConfigDict(frozen=True)

    decision_id: str = Field(..., description="Unique decision identifier")
    round_number: int = Field(..., ge=1, le=5)
    challenges_validated: list[str] = Field(
        default_factory=list, description="Challenge IDs validated as evidence-backed"
    )
    dissent_preserved: bool = Field(
        False, description="Whether evidence-backed dissent was preserved"
    )
    utility_adjustments: dict[str, float] = Field(
        default_factory=dict, description="Utility score adjustments per agent"
    )
    rationale: str = Field(..., description="Arbiter's rationale")
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class PositionSnapshot(BaseModel):
    """Snapshot of agent positions for stable dissent detection."""

    model_config = ConfigDict(frozen=True)

    round_number: int = Field(..., ge=1, le=5)
    agent_positions: dict[str, str] = Field(
        ..., description="Agent ID -> position hash for change detection"
    )
    agent_confidences: dict[str, float] = Field(
        ..., description="Agent ID -> confidence for consensus detection"
    )


class DebateState(BaseModel):
    """Canonical debate state for LangGraph orchestration.

    This state flows through all nodes in the debate graph per Appendix C-1:
    START → advocate_opening → sanad_breaker_challenge → observer_critiques_parallel
    → advocate_rebuttal → (conditional evidence_call_retrieval) → arbiter_close
    → stop_condition_check → muhasabah_validate_all → finalize_outputs → END

    All fields are typed and validated. Missing required fields cause fail-closed
    behavior in the stop condition checker.
    """

    tenant_id: str = Field(..., description="Tenant scope for isolation")
    deal_id: str = Field(..., description="Deal being analyzed")
    claim_registry_ref: str = Field(..., description="Reference to claim registry")
    sanad_graph_ref: str = Field(..., description="Reference to Sanad graph")

    round_number: int = Field(default=1, ge=1, le=5, description="Current round (1-5)")
    messages: list[DebateMessage] = Field(
        default_factory=list, description="Full debate transcript"
    )
    open_questions: list[str] = Field(
        default_factory=list, description="Unanswered questions requiring evidence"
    )

    utility_scores: dict[str, float] = Field(
        default_factory=dict, description="Cumulative utility per agent ID"
    )
    arbiter_decisions: list[ArbiterDecision] = Field(
        default_factory=list, description="Arbiter decisions per round"
    )
    agent_outputs: list[AgentOutput] = Field(
        default_factory=list, description="All structured agent outputs"
    )

    position_history: list[PositionSnapshot] = Field(
        default_factory=list, description="Position snapshots for stable dissent detection"
    )

    evidence_retrieval_requested: bool = Field(
        default=False, description="Flag for conditional evidence retrieval"
    )
    evidence_retrieval_completed: bool = Field(
        default=False, description="Flag indicating retrieval was executed"
    )

    consensus_reached: bool = Field(default=False, description="Whether consensus was reached")
    stop_reason: StopReason | None = Field(default=None, description="Reason debate stopped")

    nodes_visited: list[str] = Field(
        default_factory=list, description="Audit trail of visited nodes"
    )

    deliverables_ref: str | None = Field(
        default=None, description="Reference to generated deliverables"
    )

    def model_copy_increment_round(self) -> DebateState:
        """Create a copy with incremented round number (bounded by max 5)."""
        new_round = min(self.round_number + 1, 5)
        return self.model_copy(update={"round_number": new_round})

    def model_copy_add_message(self, message: DebateMessage) -> DebateState:
        """Create a copy with a new message appended."""
        return self.model_copy(update={"messages": [*self.messages, message]})

    def model_copy_add_output(self, output: AgentOutput) -> DebateState:
        """Create a copy with a new agent output appended."""
        return self.model_copy(update={"agent_outputs": [*self.agent_outputs, output]})

    def model_copy_add_node_visited(self, node_name: str) -> DebateState:
        """Create a copy recording a visited node."""
        return self.model_copy(update={"nodes_visited": [*self.nodes_visited, node_name]})


class DebateConfig(BaseModel):
    """Configuration for debate orchestration."""

    model_config = ConfigDict(frozen=True)

    max_rounds: int = Field(default=5, ge=1, le=5, description="Maximum rounds (v6.3: 5)")
    consensus_threshold: float = Field(
        default=0.10, ge=0.0, le=1.0, description="Max confidence spread for consensus"
    )
    stable_dissent_rounds: int = Field(
        default=2, ge=1, description="Rounds without change for stable dissent"
    )
