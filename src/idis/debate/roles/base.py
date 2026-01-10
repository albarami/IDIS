"""IDIS Debate Role Base — v6.3 Phase 5.1

Base protocol and abstract class for debate role runners.

Role runners are injected into the orchestrator to allow deterministic
testing without LLM calls. In Phase 5.1, role implementations return
structured state updates. LLM integration is deferred to later phases.

Determinism: All role runners MUST be pure and deterministic.
- No uuid4/uuid1/random (use deterministic_id instead)
- No datetime.utcnow/datetime.now (use deterministic_timestamp instead)
- All outputs derived from state fields only
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol, runtime_checkable
from uuid import UUID, uuid5

from idis.models.debate import AgentOutput, DebateMessage, DebateRole

if TYPE_CHECKING:
    from idis.models.debate import DebateState

# Namespace UUID for IDIS deterministic IDs (fixed, never changes)
IDIS_NAMESPACE = UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")

# Base epoch for logical timestamps (2026-01-01 00:00:00 UTC)
LOGICAL_EPOCH = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)


def deterministic_id(
    prefix: str,
    *,
    tenant_id: str,
    deal_id: str,
    role: str,
    round_number: int,
    step: int = 0,
    extra: str = "",
) -> str:
    """Generate a deterministic ID from state fields.

    Uses uuid5 (SHA-1 based) to produce a stable UUID from a canonical
    string built from the provided fields. Same inputs always produce
    the same output.

    Args:
        prefix: ID prefix (e.g., "msg", "out", "muh", "dec")
        tenant_id: Tenant identifier from state
        deal_id: Deal identifier from state
        role: Role name (e.g., "advocate", "arbiter")
        round_number: Current round number
        step: Step counter within round (for multiple IDs in same role)
        extra: Additional discriminator if needed

    Returns:
        Deterministic ID string: "{prefix}-{uuid5_hex[:12]}"
    """
    # Build canonical string with sorted components for stability
    canonical = "|".join(
        [
            f"tenant:{tenant_id}",
            f"deal:{deal_id}",
            f"role:{role}",
            f"round:{round_number}",
            f"step:{step}",
            f"extra:{extra}",
        ]
    )
    deterministic_uuid = uuid5(IDIS_NAMESPACE, canonical)
    return f"{prefix}-{deterministic_uuid.hex[:12]}"


def deterministic_timestamp(
    round_number: int,
    step: int = 0,
) -> datetime:
    """Generate a deterministic logical timestamp from round and step.

    Uses a fixed epoch plus offsets based on round and step. Same inputs
    always produce the same timestamp. This is NOT wall-clock time.

    Args:
        round_number: Current round number (1-5)
        step: Step counter within round

    Returns:
        Deterministic datetime in UTC
    """
    # Each round is 1 hour offset, each step is 1 minute offset
    hours = round_number - 1
    minutes = step
    return LOGICAL_EPOCH.replace(
        hour=hours,
        minute=minutes,
        second=0,
        microsecond=0,
    )


def deterministic_position_hash(
    role: str,
    round_number: int,
    content_summary: str,
) -> str:
    """Generate a deterministic position hash from role and content.

    Used for stable dissent detection. Same inputs produce same hash.

    Args:
        role: Role name
        round_number: Current round
        content_summary: Summary of position content (must be stable)

    Returns:
        Hex digest of SHA-256 hash (first 16 chars)
    """
    canonical = f"{role}|{round_number}|{content_summary}"
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def default_agent_id(role: DebateRole) -> str:
    """Return the deterministic default agent_id for a role.

    Args:
        role: The debate role

    Returns:
        Deterministic agent ID based on role name
    """
    return f"{role.value}-default"


@runtime_checkable
class RoleRunnerProtocol(Protocol):
    """Protocol for role runners.

    Role runners are responsible for producing structured outputs
    that update the debate state. They must:
    - Return deterministic results for the same inputs
    - Produce outputs with valid MuhasabahRecords
    - Reference claim_ids/calc_ids for factual content (No-Free-Facts)
    """

    @property
    def role(self) -> DebateRole:
        """The debate role this runner implements."""
        ...

    @property
    def agent_id(self) -> str:
        """Unique identifier for this agent instance."""
        ...

    def run(self, state: DebateState) -> RoleResult:
        """Execute the role and return state updates.

        Args:
            state: Current debate state (read-only).

        Returns:
            RoleResult with messages and outputs to add to state.
        """
        ...


class RoleResult:
    """Result from a role runner execution.

    Contains the updates to be applied to the debate state.
    The orchestrator is responsible for applying these updates.
    """

    def __init__(
        self,
        messages: list[DebateMessage] | None = None,
        outputs: list[AgentOutput] | None = None,
        evidence_retrieval_requested: bool = False,
        position_hash: str | None = None,
    ) -> None:
        """Initialize role result.

        Args:
            messages: Messages to add to transcript.
            outputs: Structured outputs with MuhasabahRecords.
            evidence_retrieval_requested: Flag for conditional retrieval.
            position_hash: Hash of agent's current position for stable dissent.
        """
        self.messages = messages or []
        self.outputs = outputs or []
        self.evidence_retrieval_requested = evidence_retrieval_requested
        self.position_hash = position_hash


class RoleRunner(ABC):
    """Abstract base class for role runners.

    Provides common functionality for all role implementations.
    Subclasses must implement the `run` method.
    """

    def __init__(self, role: DebateRole, agent_id: str) -> None:
        """Initialize role runner.

        Args:
            role: The debate role this runner implements.
            agent_id: Unique identifier for this agent instance.
        """
        self._role = role
        self._agent_id = agent_id

    @property
    def role(self) -> DebateRole:
        """The debate role this runner implements."""
        return self._role

    @property
    def agent_id(self) -> str:
        """Unique identifier for this agent instance."""
        return self._agent_id

    @abstractmethod
    def run(self, state: DebateState) -> RoleResult:
        """Execute the role and return state updates.

        Args:
            state: Current debate state (read-only).

        Returns:
            RoleResult with messages and outputs to add to state.
        """
        raise NotImplementedError
