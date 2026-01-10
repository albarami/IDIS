"""IDIS Debate Orchestration — v6.3 Phase 5.1

LangGraph-based debate orchestration with deterministic stop conditions.

Modules:
- stop_conditions: Priority-ordered stop condition evaluation
- roles: Agent role interfaces and runners
- orchestrator: LangGraph state machine implementation
"""

from idis.debate.orchestrator import DebateOrchestrator, build_debate_graph
from idis.debate.stop_conditions import (
    StopConditionChecker,
    StopConditionError,
    check_stop_condition,
)

__all__ = [
    "DebateOrchestrator",
    "StopConditionChecker",
    "StopConditionError",
    "build_debate_graph",
    "check_stop_condition",
]
