"""Tests for analysis engine determinism: same inputs → stable output."""

from __future__ import annotations

import pytest

from idis.analysis.models import (
    AgentReport,
    AnalysisBundle,
    AnalysisContext,
    AnalysisMuhasabahRecord,
    Risk,
    RiskSeverity,
)
from idis.analysis.registry import AgentNotRegisteredError, AnalysisAgentRegistry
from idis.analysis.runner import AnalysisEngine, AnalysisEngineError
from idis.audit.sink import AuditSinkError, InMemoryAuditSink

CLAIM_1 = "00000000-0000-0000-0000-000000000001"
CLAIM_2 = "00000000-0000-0000-0000-000000000002"
CALC_1 = "00000000-0000-0000-0000-000000000010"
TIMESTAMP = "2026-02-08T12:00:00+00:00"


def _make_context() -> AnalysisContext:
    return AnalysisContext(
        deal_id="deal-1",
        tenant_id="tenant-1",
        run_id="run-1",
        claim_ids=frozenset({CLAIM_1, CLAIM_2}),
        calc_ids=frozenset({CALC_1}),
    )


class ExampleAgent:
    """Minimal deterministic agent producing grounded, valid output.

    Used only for tests. Returns the same output for the same context.
    """

    def __init__(self, agent_id: str, agent_type: str) -> None:
        self._agent_id = agent_id
        self._agent_type = agent_type

    @property
    def agent_id(self) -> str:
        return self._agent_id

    @property
    def agent_type(self) -> str:
        return self._agent_type

    def run(self, ctx: AnalysisContext) -> AgentReport:
        """Return deterministic output grounded in the context."""
        sorted_claims = sorted(ctx.claim_ids)
        sorted_calcs = sorted(ctx.calc_ids)
        return AgentReport(
            agent_id=self._agent_id,
            agent_type=self._agent_type,
            supported_claim_ids=sorted_claims,
            supported_calc_ids=sorted_calcs,
            analysis_sections={
                "summary": f"Deterministic analysis by {self._agent_id}",
                "deal_id": ctx.deal_id,
            },
            risks=[
                Risk(
                    risk_id=f"{self._agent_id}-risk-1",
                    description="Concentration risk in revenue",
                    severity=RiskSeverity.MEDIUM,
                    claim_ids=sorted_claims[:1],
                ),
            ],
            questions_for_founder=[
                "What is the customer retention rate?",
            ],
            confidence=0.72,
            confidence_justification="Moderate evidence with single corroboration",
            muhasabah=AnalysisMuhasabahRecord(
                agent_id=self._agent_id,
                output_id=f"{self._agent_id}-output-1",
                supported_claim_ids=sorted_claims,
                supported_calc_ids=sorted_calcs,
                evidence_summary="Audited financials support revenue claim",
                counter_hypothesis="Revenue may include non-recurring items",
                falsifiability_tests=[],
                uncertainties=[],
                failure_modes=["Data room incomplete"],
                confidence=0.72,
                confidence_justification="Moderate evidence with single corroboration",
                timestamp=TIMESTAMP,
                is_subjective=False,
            ),
        )


class TestDeterministicOutput:
    """Same inputs must produce equivalent outputs."""

    def test_same_inputs_same_outputs(self) -> None:
        ctx = _make_context()
        agent = ExampleAgent("agent-1", "example_agent")

        report_a = agent.run(ctx)
        report_b = agent.run(ctx)

        assert report_a.model_dump() == report_b.model_dump()

    def test_engine_deterministic_ordering(self) -> None:
        """Agents are sorted (agent_type, agent_id); order is stable."""
        registry = AnalysisAgentRegistry()
        registry.register(ExampleAgent("z-agent", "beta_type"))
        registry.register(ExampleAgent("a-agent", "alpha_type"))
        registry.register(ExampleAgent("m-agent", "alpha_type"))

        sink = InMemoryAuditSink()
        engine = AnalysisEngine(registry=registry, audit_sink=sink)
        ctx = _make_context()

        bundle = engine.run(ctx, ["z-agent", "a-agent", "m-agent"])

        agent_ids = [r.agent_id for r in bundle.reports]
        assert agent_ids == ["a-agent", "m-agent", "z-agent"]

    def test_engine_multiple_runs_equivalent(self) -> None:
        """Multiple engine runs with same inputs yield equivalent bundles."""
        registry = AnalysisAgentRegistry()
        registry.register(ExampleAgent("agent-1", "example_agent"))
        registry.register(ExampleAgent("agent-2", "example_agent"))

        ctx = _make_context()

        bundles: list[AnalysisBundle] = []
        for _ in range(3):
            sink = InMemoryAuditSink()
            engine = AnalysisEngine(registry=registry, audit_sink=sink)
            bundles.append(engine.run(ctx, ["agent-1", "agent-2"]))

        reports_0 = [r.model_dump(exclude={"muhasabah": {"timestamp"}}) for r in bundles[0].reports]
        for bundle in bundles[1:]:
            reports_i = [r.model_dump(exclude={"muhasabah": {"timestamp"}}) for r in bundle.reports]
            assert reports_0 == reports_i


class TestAuditEvents:
    """Audit events must be emitted correctly."""

    def test_audit_events_emitted(self) -> None:
        registry = AnalysisAgentRegistry()
        registry.register(ExampleAgent("agent-1", "example_agent"))

        sink = InMemoryAuditSink()
        engine = AnalysisEngine(registry=registry, audit_sink=sink)
        engine.run(_make_context(), ["agent-1"])

        event_types = [e["event_type"] for e in sink.events]
        assert "analysis.started" in event_types
        assert "analysis.agent.completed" in event_types
        assert "analysis.completed" in event_types

    def test_audit_sink_failure_is_fatal(self) -> None:
        """AuditSinkError must propagate (fail-closed)."""

        class _FailingSink:
            def emit(self, event: dict) -> None:
                raise AuditSinkError("Sink broken")

        registry = AnalysisAgentRegistry()
        registry.register(ExampleAgent("agent-1", "example_agent"))
        engine = AnalysisEngine(registry=registry, audit_sink=_FailingSink())  # type: ignore[arg-type]

        with pytest.raises(AuditSinkError, match="Sink broken"):
            engine.run(_make_context(), ["agent-1"])

    def test_failed_agent_emits_audit_event(self) -> None:
        """Agent failure must emit analysis.failed audit event."""

        class _FailingAgent:
            @property
            def agent_id(self) -> str:
                return "fail-agent"

            @property
            def agent_type(self) -> str:
                return "failing_agent"

            def run(self, ctx: AnalysisContext) -> AgentReport:
                raise RuntimeError("Agent crash")

        registry = AnalysisAgentRegistry()
        registry.register(_FailingAgent())  # type: ignore[arg-type]

        sink = InMemoryAuditSink()
        engine = AnalysisEngine(registry=registry, audit_sink=sink)

        with pytest.raises(AnalysisEngineError, match="Agent 'fail-agent' failed"):
            engine.run(_make_context(), ["fail-agent"])

        event_types = [e["event_type"] for e in sink.events]
        assert "analysis.started" in event_types
        assert "analysis.failed" in event_types


class TestEngineFailClosed:
    """Engine must fail closed on unknown agents."""

    def test_unknown_agent_raises(self) -> None:
        registry = AnalysisAgentRegistry()
        sink = InMemoryAuditSink()
        engine = AnalysisEngine(registry=registry, audit_sink=sink)

        with pytest.raises(AgentNotRegisteredError):
            engine.run(_make_context(), ["nonexistent"])


class TestRiskEvidenceLinks:
    """Risk model must require at least one evidence link."""

    def test_risk_without_evidence_fails(self) -> None:
        with pytest.raises(ValueError, match="at least one evidence link"):
            Risk(
                risk_id="r-1",
                description="No links",
                severity=RiskSeverity.HIGH,
            )

    def test_risk_with_claim_passes(self) -> None:
        risk = Risk(
            risk_id="r-1",
            description="Has claim link",
            severity=RiskSeverity.HIGH,
            claim_ids=[CLAIM_1],
        )
        assert risk.risk_id == "r-1"

    def test_risk_with_enrichment_passes(self) -> None:
        risk = Risk(
            risk_id="r-1",
            description="Has enrichment link",
            severity=RiskSeverity.HIGH,
            enrichment_ref_ids=["enrich-1"],
        )
        assert risk.risk_id == "r-1"
