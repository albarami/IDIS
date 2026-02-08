"""Analysis agent protocol — Phase 8.A.

Defines the AnalysisAgent protocol that all specialist agents must implement.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from idis.analysis.models import AgentReport, AnalysisContext


@runtime_checkable
class AnalysisAgent(Protocol):
    """Protocol for analysis specialist agents.

    All agents must implement this protocol. The framework validates
    outputs (muhasabah + NFF) after run() returns.
    """

    @property
    def agent_id(self) -> str:
        """Unique identifier for this agent instance."""
        ...

    @property
    def agent_type(self) -> str:
        """Agent type (e.g., 'financial_agent', 'market_agent')."""
        ...

    def run(self, ctx: AnalysisContext) -> AgentReport:
        """Execute analysis and return a structured report.

        Args:
            ctx: Analysis context with deal data, claim/calc registries,
                and enrichment references.

        Returns:
            AgentReport with all TDD §10.2 required fields populated.

        Raises:
            Any exception on agent failure. The engine will catch and
            emit an audit event.
        """
        ...
