"""Tests for TechnicalAgent: valid path, engine integration, fail-closed."""

from __future__ import annotations

import json

import pytest

from idis.analysis.agents.technical_agent import TechnicalAgent
from idis.analysis.models import (
    AgentReport,
    AnalysisContext,
    EnrichmentRef,
)
from idis.analysis.no_free_facts import AnalysisNoFreeFactsValidator
from idis.analysis.registry import AnalysisAgentRegistry
from idis.analysis.runner import AnalysisEngine
from idis.audit.sink import InMemoryAuditSink

CLAIM_1 = "00000000-0000-0000-0000-b00000000001"
CLAIM_2 = "00000000-0000-0000-0000-b00000000002"
CALC_1 = "00000000-0000-0000-0000-b00000000010"
ENRICH_1 = "enrich-tech-001"
TIMESTAMP = "2026-02-09T00:00:00+00:00"


def _make_context(*, with_enrichment: bool = False) -> AnalysisContext:
    enrichment_refs: dict[str, EnrichmentRef] = {}
    if with_enrichment:
        enrichment_refs[ENRICH_1] = EnrichmentRef(
            ref_id=ENRICH_1,
            provider_id="patent-office",
            source_id="US-PAT-2025-12345",
        )
    return AnalysisContext(
        deal_id="deal-tech-1",
        tenant_id="tenant-1",
        run_id="run-tech-1",
        claim_ids=frozenset({CLAIM_1, CLAIM_2}),
        calc_ids=frozenset({CALC_1}),
        enrichment_refs=enrichment_refs,
        company_name="TechStartup Inc",
        stage="Seed",
        sector="DevTools",
    )


def _valid_technical_response(
    *,
    with_enrichment: bool = False,
    drop_field: str | None = None,
) -> str:
    """Return a deterministic valid technical agent JSON response."""
    enrichment_ref_ids: list[str] = [ENRICH_1] if with_enrichment else []
    data: dict = {
        "supported_claim_ids": sorted([CLAIM_1, CLAIM_2]),
        "supported_calc_ids": [CALC_1],
        "analysis_sections": {
            "architecture_and_stack": "Microservices on AWS EKS per architecture claims.",
            "scalability": "Auto-scaling configured for 10x current load.",
            "technical_debt": "Minimal debt reported; monolith migration completed.",
            "security_posture": "SOC2 Type II achieved per compliance claims.",
            "data_and_ip": "Two patents filed for core ML algorithms.",
            "development_velocity": "Bi-weekly releases with CI/CD pipeline.",
            "integration_and_platform_risk": "AWS dependency with multi-region failover.",
            "technical_risks_narrative": "Single database instance as bottleneck.",
            "infrastructure_costs": "Cloud spend at $12K/mo per calc engine.",
        },
        "risks": [
            {
                "risk_id": "tech-risk-001",
                "description": "Single database instance creates scaling bottleneck",
                "severity": "HIGH",
                "claim_ids": [CLAIM_1],
                "calc_ids": [],
                "enrichment_ref_ids": [],
            },
        ],
        "questions_for_founder": [
            "What is your current deployment frequency?",
            "Can you share architecture diagrams and infrastructure costs?",
        ],
        "confidence": 0.58,
        "confidence_justification": (
            "Limited technical documentation; architecture claims self-reported"
        ),
        "muhasabah": {
            "agent_id": "technical-agent-01",
            "output_id": "tech-output-001",
            "supported_claim_ids": sorted([CLAIM_1, CLAIM_2]),
            "supported_calc_ids": [CALC_1],
            "evidence_summary": "Architecture claims supported by deployment logs",
            "counter_hypothesis": "Scalability claims may be aspirational, not tested",
            "falsifiability_tests": [
                {
                    "test_description": "Scalability claims could be untested",
                    "required_evidence": "Load test results or production metrics",
                    "pass_fail_rule": "If no load tests exist, scalability is unverified",
                }
            ],
            "uncertainties": [
                {
                    "uncertainty": "No third-party security audit provided",
                    "impact": "MEDIUM",
                    "mitigation": "Request penetration test report",
                }
            ],
            "failure_modes": ["single_point_of_failure", "technology_obsolescence"],
            "confidence": 0.58,
            "confidence_justification": (
                "Limited technical documentation; architecture claims self-reported"
            ),
            "timestamp": TIMESTAMP,
            "is_subjective": False,
        },
        "enrichment_ref_ids": enrichment_ref_ids,
    }
    if drop_field:
        data.pop(drop_field, None)
    return json.dumps(data, sort_keys=True)


class _StubLLMClient:
    """Deterministic LLM client returning pre-built technical JSON."""

    def __init__(self, response: str) -> None:
        self._response = response

    def call(self, prompt: str, *, json_mode: bool = False) -> str:
        return self._response


class TestTechnicalAgentValidPath:
    """TechnicalAgent produces valid AgentReport on well-formed LLM output."""

    def test_returns_valid_agent_report(self) -> None:
        client = _StubLLMClient(_valid_technical_response())
        agent = TechnicalAgent(llm_client=client)
        ctx = _make_context()

        report = agent.run(ctx)

        assert isinstance(report, AgentReport)
        assert report.agent_id == "technical-agent-01"
        assert report.agent_type == "technical_agent"
        assert report.confidence == 0.58

    def test_report_has_all_required_fields(self) -> None:
        client = _StubLLMClient(_valid_technical_response())
        agent = TechnicalAgent(llm_client=client)

        report = agent.run(_make_context())

        assert len(report.supported_claim_ids) == 2
        assert len(report.supported_calc_ids) == 1
        assert "architecture_and_stack" in report.analysis_sections
        assert len(report.risks) >= 1
        assert len(report.questions_for_founder) >= 1
        assert report.confidence_justification
        assert report.muhasabah is not None

    def test_passes_no_free_facts(self) -> None:
        client = _StubLLMClient(_valid_technical_response())
        agent = TechnicalAgent(llm_client=client)
        ctx = _make_context()

        report = agent.run(ctx)

        validator = AnalysisNoFreeFactsValidator()
        result = validator.validate(report, ctx)
        assert result.passed, f"NFF failed: {[e.message for e in result.errors]}"

    def test_passes_no_free_facts_with_enrichment(self) -> None:
        client = _StubLLMClient(_valid_technical_response(with_enrichment=True))
        agent = TechnicalAgent(llm_client=client)
        ctx = _make_context(with_enrichment=True)

        report = agent.run(ctx)

        validator = AnalysisNoFreeFactsValidator()
        result = validator.validate(report, ctx)
        assert result.passed, f"NFF failed: {[e.message for e in result.errors]}"
        assert ENRICH_1 in report.enrichment_ref_ids


class TestTechnicalAgentEngineIntegration:
    """TechnicalAgent integrates with AnalysisEngine end-to-end."""

    def test_engine_runs_technical_agent(self) -> None:
        client = _StubLLMClient(_valid_technical_response())
        agent = TechnicalAgent(llm_client=client)

        registry = AnalysisAgentRegistry()
        registry.register(agent)

        sink = InMemoryAuditSink()
        engine = AnalysisEngine(registry=registry, audit_sink=sink)

        bundle = engine.run(_make_context(), ["technical-agent-01"])

        assert len(bundle.reports) == 1
        assert bundle.reports[0].agent_type == "technical_agent"
        event_types = [e["event_type"] for e in sink.events]
        assert "analysis.completed" in event_types


class TestTechnicalAgentFailClosed:
    """TechnicalAgent must fail closed on invalid LLM output."""

    def test_missing_confidence_justification_raises(self) -> None:
        bad_response = _valid_technical_response(drop_field="confidence_justification")
        client = _StubLLMClient(bad_response)
        agent = TechnicalAgent(llm_client=client)

        with pytest.raises(ValueError):
            agent.run(_make_context())

    def test_invalid_json_raises(self) -> None:
        client = _StubLLMClient("not valid json {{{")
        agent = TechnicalAgent(llm_client=client)

        with pytest.raises(ValueError, match="invalid JSON"):
            agent.run(_make_context())

    def test_missing_muhasabah_raises(self) -> None:
        bad_response = _valid_technical_response(drop_field="muhasabah")
        client = _StubLLMClient(bad_response)
        agent = TechnicalAgent(llm_client=client)

        with pytest.raises(ValueError, match="muhasabah"):
            agent.run(_make_context())

    def test_non_object_json_raises(self) -> None:
        client = _StubLLMClient(json.dumps([1, 2, 3]))
        agent = TechnicalAgent(llm_client=client)

        with pytest.raises(ValueError, match="non-object"):
            agent.run(_make_context())

    def test_missing_prompt_file_raises(self) -> None:
        from pathlib import Path

        client = _StubLLMClient("")
        agent = TechnicalAgent(
            llm_client=client,
            prompt_path=Path("/nonexistent/prompt.md"),
        )

        with pytest.raises(ValueError, match="Prompt file not found"):
            agent.run(_make_context())
