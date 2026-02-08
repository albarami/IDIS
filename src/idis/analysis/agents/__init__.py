"""IDIS Analysis Specialist Agents — Phase 8.B.

Layer 2 specialist agents for the multi-agent analysis engine:
- FinancialAgent: financial analysis specialist
- MarketAgent: market analysis specialist

Both implement the AnalysisAgent protocol and use injected LLMClient.
"""

from idis.analysis.agent_protocol import AnalysisAgent
from idis.analysis.agents.financial_agent import FinancialAgent
from idis.analysis.agents.market_agent import MarketAgent
from idis.services.extraction.extractors.llm_client import LLMClient

__all__ = [
    "FinancialAgent",
    "MarketAgent",
    "build_default_specialist_agents",
]


def build_default_specialist_agents(llm_client: LLMClient) -> list[AnalysisAgent]:
    """Build the default set of specialist analysis agents.

    Args:
        llm_client: Provider-agnostic LLM client for agent calls.

    Returns:
        List of AnalysisAgent instances (FinancialAgent, MarketAgent).
    """
    return [
        FinancialAgent(llm_client=llm_client),
        MarketAgent(llm_client=llm_client),
    ]
