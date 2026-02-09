"""IDIS Analysis Specialist Agents — Phase 8.B / 8.C-1 / 8.C-2.

Layer 2 specialist agents for the multi-agent analysis engine:
- FinancialAgent: financial analysis specialist
- MarketAgent: market analysis specialist
- TechnicalAgent: technical analysis specialist
- TermsAgent: investment terms analysis specialist
- TeamAgent: team analysis specialist
- RiskOfficerAgent: risk-focused analysis specialist
- HistorianAgent: historical-comparative analysis specialist
- SectorSpecialistAgent: sector-specific analysis specialist

All implement the AnalysisAgent protocol and use injected LLMClient.
"""

from idis.analysis.agent_protocol import AnalysisAgent
from idis.analysis.agents.financial_agent import FinancialAgent
from idis.analysis.agents.historian_agent import HistorianAgent
from idis.analysis.agents.market_agent import MarketAgent
from idis.analysis.agents.risk_officer_agent import RiskOfficerAgent
from idis.analysis.agents.sector_specialist_agent import SectorSpecialistAgent
from idis.analysis.agents.team_agent import TeamAgent
from idis.analysis.agents.technical_agent import TechnicalAgent
from idis.analysis.agents.terms_agent import TermsAgent
from idis.services.extraction.extractors.llm_client import LLMClient

__all__ = [
    "FinancialAgent",
    "HistorianAgent",
    "MarketAgent",
    "RiskOfficerAgent",
    "SectorSpecialistAgent",
    "TeamAgent",
    "TechnicalAgent",
    "TermsAgent",
    "build_default_specialist_agents",
]


def build_default_specialist_agents(llm_client: LLMClient) -> list[AnalysisAgent]:
    """Build the default set of specialist analysis agents.

    Args:
        llm_client: Provider-agnostic LLM client for agent calls.

    Returns:
        List of AnalysisAgent instances (all specialist agents).
    """
    return [
        FinancialAgent(llm_client=llm_client),
        HistorianAgent(llm_client=llm_client),
        MarketAgent(llm_client=llm_client),
        RiskOfficerAgent(llm_client=llm_client),
        SectorSpecialistAgent(llm_client=llm_client),
        TechnicalAgent(llm_client=llm_client),
        TermsAgent(llm_client=llm_client),
        TeamAgent(llm_client=llm_client),
    ]
