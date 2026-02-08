"""Technical specialist analysis agent — Phase 8.C-1.

Implements the AnalysisAgent protocol for technical analysis.
Uses injected LLMClient and fail-closed parsing via llm_specialist_agent.
"""

from __future__ import annotations

from pathlib import Path

from idis.analysis.agents.llm_specialist_agent import run_specialist_agent
from idis.analysis.models import AgentReport, AnalysisContext
from idis.services.extraction.extractors.llm_client import LLMClient

_DEFAULT_PROMPT_PATH = (
    Path(__file__).resolve().parents[4] / "prompts" / "technical_agent" / "1.0.0" / "prompt.md"
)


class TechnicalAgent:
    """Technical specialist agent implementing AnalysisAgent protocol.

    Analyzes architecture, scalability, technical debt, security posture,
    data/IP, development velocity, platform risk, and infrastructure costs.

    Fail-closed: invalid LLM output raises immediately.
    """

    def __init__(
        self,
        *,
        llm_client: LLMClient,
        prompt_path: Path | None = None,
        agent_id: str = "technical-agent-01",
    ) -> None:
        """Initialize the technical agent.

        Args:
            llm_client: Provider-agnostic LLM client (required).
            prompt_path: Override path to prompt file. Defaults to
                prompts/technical_agent/1.0.0/prompt.md.
            agent_id: Unique agent identifier.
        """
        self._llm_client = llm_client
        self._prompt_path = prompt_path or _DEFAULT_PROMPT_PATH
        self._agent_id = agent_id

    @property
    def agent_id(self) -> str:
        """Unique identifier for this agent instance."""
        return self._agent_id

    @property
    def agent_type(self) -> str:
        """Agent type identifier."""
        return "technical_agent"

    def run(self, ctx: AnalysisContext) -> AgentReport:
        """Execute technical analysis and return a structured report.

        Args:
            ctx: Analysis context with deal data, claim/calc registries,
                and enrichment references.

        Returns:
            AgentReport with all TDD §10.2 required fields populated.

        Raises:
            ValueError: On prompt file missing, invalid LLM JSON,
                or Pydantic validation failure (fail-closed).
        """
        return run_specialist_agent(
            agent_id=self._agent_id,
            agent_type=self.agent_type,
            llm_client=self._llm_client,
            prompt_path=self._prompt_path,
            ctx=ctx,
        )
