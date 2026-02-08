"""Analysis agent registry — Phase 8.A.

Fail-closed registry for analysis agents. Unknown agent lookups raise.
"""

from __future__ import annotations

from idis.analysis.agent_protocol import AnalysisAgent


class AgentNotRegisteredError(Exception):
    """Raised when looking up an agent that is not registered."""

    def __init__(self, agent_id: str) -> None:
        """Initialize with the unknown agent ID.

        Args:
            agent_id: The agent ID that was not found.
        """
        self.agent_id = agent_id
        super().__init__(f"Agent '{agent_id}' is not registered (fail-closed)")


class AnalysisAgentRegistry:
    """Fail-closed registry for analysis agents.

    Unknown agent lookups raise AgentNotRegisteredError.
    Agents are returned in deterministic order (sorted by agent_type, then agent_id).
    """

    def __init__(self) -> None:
        """Initialize an empty registry."""
        self._agents: dict[str, AnalysisAgent] = {}

    def register(self, agent: AnalysisAgent) -> None:
        """Register an agent.

        Args:
            agent: Agent implementing the AnalysisAgent protocol.

        Raises:
            ValueError: If agent_id is already registered.
        """
        if agent.agent_id in self._agents:
            raise ValueError(f"Agent '{agent.agent_id}' is already registered")
        self._agents[agent.agent_id] = agent

    def get(self, agent_id: str) -> AnalysisAgent:
        """Look up an agent by ID. Fail-closed on unknown agent.

        Args:
            agent_id: Agent identifier.

        Returns:
            The registered AnalysisAgent.

        Raises:
            AgentNotRegisteredError: If agent_id is not registered.
        """
        if agent_id not in self._agents:
            raise AgentNotRegisteredError(agent_id)
        return self._agents[agent_id]

    def list_agents(self) -> list[AnalysisAgent]:
        """Return all registered agents in deterministic order.

        Sorted by (agent_type, agent_id) for deterministic execution.
        """
        return sorted(
            self._agents.values(),
            key=lambda a: (a.agent_type, a.agent_id),
        )

    def __len__(self) -> int:
        """Return the number of registered agents."""
        return len(self._agents)
