"""IDIS Analysis Agent Framework — Phase 8.A (Layer 2 / IC mode foundation).

Provides the scaffolding for specialist analysis agents:
- Agent protocol and registry (fail-closed on unknown agent)
- AgentReport structured output schema per TDD §10.2
- Muḥāsabah enforcement (fail-closed; no synthesis)
- No-Free-Facts enforcement with enrichment provenance validation
"""

from idis.analysis.agent_protocol import AnalysisAgent
from idis.analysis.models import (
    AgentReport,
    AnalysisBundle,
    AnalysisContext,
    AnalysisMuhasabahRecord,
    EnrichmentRef,
    Risk,
    RiskSeverity,
)
from idis.analysis.no_free_facts import AnalysisNoFreeFactsValidator
from idis.analysis.registry import AgentNotRegisteredError, AnalysisAgentRegistry
from idis.analysis.runner import AnalysisEngine, AnalysisEngineError

__all__ = [
    "AgentNotRegisteredError",
    "AgentReport",
    "AnalysisAgent",
    "AnalysisAgentRegistry",
    "AnalysisBundle",
    "AnalysisContext",
    "AnalysisEngine",
    "AnalysisEngineError",
    "AnalysisMuhasabahRecord",
    "AnalysisNoFreeFactsValidator",
    "EnrichmentRef",
    "Risk",
    "RiskSeverity",
]
