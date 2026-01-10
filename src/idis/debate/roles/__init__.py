"""IDIS Debate Roles — v6.3 Phase 5.1

Agent role interfaces and implementations for debate orchestration.

Roles per v6.3 roadmap:
- Advocate: Proposes thesis with claim/calc references
- SanadBreaker: Challenges weak evidence chains
- ContradictionFinder: Detects Matn contradictions
- RiskOfficer: Identifies downside/regulatory risks
- Arbiter: Validates challenges and assigns utility
"""

from idis.debate.roles.advocate import AdvocateRole
from idis.debate.roles.arbiter import ArbiterRole
from idis.debate.roles.base import RoleRunner, RoleRunnerProtocol
from idis.debate.roles.contradiction_finder import ContradictionFinderRole
from idis.debate.roles.risk_officer import RiskOfficerRole
from idis.debate.roles.sanad_breaker import SanadBreakerRole

__all__ = [
    "AdvocateRole",
    "ArbiterRole",
    "ContradictionFinderRole",
    "RiskOfficerRole",
    "RoleRunner",
    "RoleRunnerProtocol",
    "SanadBreakerRole",
]
