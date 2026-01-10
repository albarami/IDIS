"""IDIS Debate Role Base — v6.3 Phase 5.1

Base protocol and abstract class for debate role runners.

Role runners are injected into the orchestrator to allow deterministic
testing without LLM calls. In Phase 5.1, role implementations return
structured state updates. LLM integration is deferred to later phases.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from idis.models.debate import AgentOutput, DebateMessage, DebateRole

if TYPE_CHECKING:
    from idis.models.debate import DebateState


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
