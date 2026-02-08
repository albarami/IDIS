"""Analysis engine orchestrator — Phase 8.A.

Runs analysis agents in deterministic order, validates outputs,
emits audit events, and returns an AnalysisBundle.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from idis.analysis.models import AgentReport, AnalysisBundle, AnalysisContext
from idis.analysis.no_free_facts import AnalysisNoFreeFactsValidator
from idis.analysis.registry import AnalysisAgentRegistry
from idis.audit.sink import AuditSink, AuditSinkError
from idis.validators.muhasabah import validate_muhasabah

logger = logging.getLogger(__name__)


class AnalysisEngineError(Exception):
    """Raised when the analysis engine encounters a fatal error."""


class AnalysisEngine:
    """Orchestrates analysis agent execution with full validation.

    Execution flow:
    1. Resolve requested agents from registry (fail-closed on unknown)
    2. Sort agents deterministically (by agent_type, then agent_id)
    3. Run each agent
    4. Validate No-Free-Facts for each report
    5. Validate Muḥāsabah for each report
    6. Emit audit events (fail-closed on audit sink failure)
    7. Return AnalysisBundle
    """

    def __init__(
        self,
        registry: AnalysisAgentRegistry,
        audit_sink: AuditSink,
    ) -> None:
        """Initialize the engine.

        Args:
            registry: Agent registry with registered agents.
            audit_sink: Audit event sink (fail-closed on failure).
        """
        self._registry = registry
        self._audit_sink = audit_sink
        self._nff_validator = AnalysisNoFreeFactsValidator()

    def run(self, ctx: AnalysisContext, agent_ids: list[str]) -> AnalysisBundle:
        """Execute analysis run.

        Args:
            ctx: Analysis context.
            agent_ids: IDs of agents to run. Each must be registered.

        Returns:
            AnalysisBundle with validated reports.

        Raises:
            AnalysisEngineError: On any validation or agent failure.
            AuditSinkError: On audit sink failure (fatal).
        """
        self._emit_audit(
            "analysis.started",
            {
                "deal_id": ctx.deal_id,
                "tenant_id": ctx.tenant_id,
                "run_id": ctx.run_id,
                "agent_ids": sorted(agent_ids),
            },
        )

        agents = [self._registry.get(aid) for aid in agent_ids]
        agents = sorted(agents, key=lambda a: (a.agent_type, a.agent_id))

        reports: list[AgentReport] = []
        for agent in agents:
            try:
                report = agent.run(ctx)
                self._validate_report(report, ctx)
                reports.append(report)
                self._emit_audit(
                    "analysis.agent.completed",
                    {
                        "deal_id": ctx.deal_id,
                        "tenant_id": ctx.tenant_id,
                        "run_id": ctx.run_id,
                        "agent_id": agent.agent_id,
                        "agent_type": agent.agent_type,
                        "confidence": report.confidence,
                    },
                )
            except AuditSinkError:
                raise
            except AnalysisEngineError:
                self._emit_audit(
                    "analysis.failed",
                    {
                        "deal_id": ctx.deal_id,
                        "tenant_id": ctx.tenant_id,
                        "run_id": ctx.run_id,
                        "agent_id": agent.agent_id,
                        "error_type": "validation_failure",
                    },
                )
                raise
            except Exception as exc:
                self._emit_audit(
                    "analysis.failed",
                    {
                        "deal_id": ctx.deal_id,
                        "tenant_id": ctx.tenant_id,
                        "run_id": ctx.run_id,
                        "agent_id": agent.agent_id,
                        "error": str(exc),
                    },
                )
                raise AnalysisEngineError(f"Agent '{agent.agent_id}' failed: {exc}") from exc

        bundle = AnalysisBundle(
            deal_id=ctx.deal_id,
            tenant_id=ctx.tenant_id,
            run_id=ctx.run_id,
            reports=reports,
            timestamp=datetime.now(UTC).isoformat(),
        )

        self._emit_audit(
            "analysis.completed",
            {
                "deal_id": ctx.deal_id,
                "tenant_id": ctx.tenant_id,
                "run_id": ctx.run_id,
                "agent_count": len(reports),
            },
        )

        return bundle

    def _validate_report(self, report: AgentReport, ctx: AnalysisContext) -> None:
        """Validate NFF and Muḥāsabah on a report. Fail-closed.

        Args:
            report: Agent report to validate.
            ctx: Analysis context with known registries.

        Raises:
            AnalysisEngineError: If any validation fails.
        """
        nff_result = self._nff_validator.validate(report, ctx)
        if not nff_result.passed:
            error_details = [f"{e.code}: {e.message}" for e in nff_result.errors]
            raise AnalysisEngineError(
                f"No-Free-Facts validation failed for agent '{report.agent_id}': {error_details}"
            )

        muhasabah_dict = report.muhasabah.to_validator_dict()
        muhasabah_result = validate_muhasabah(muhasabah_dict)
        if not muhasabah_result.passed:
            error_details = [f"{e.code}: {e.message}" for e in muhasabah_result.errors]
            raise AnalysisEngineError(
                f"Muhasabah validation failed for agent '{report.agent_id}': {error_details}"
            )

    def _emit_audit(self, event_type: str, data: dict[str, Any]) -> None:
        """Emit an audit event. Fail-closed on sink failure.

        Args:
            event_type: Audit event type identifier.
            data: Event payload.

        Raises:
            AuditSinkError: If the audit sink fails.
        """
        event = {
            "event_type": event_type,
            "timestamp": datetime.now(UTC).isoformat(),
            **data,
        }
        try:
            self._audit_sink.emit(event)
        except AuditSinkError:
            raise
        except Exception as exc:
            raise AuditSinkError(f"Audit sink failure for event '{event_type}': {exc}") from exc
