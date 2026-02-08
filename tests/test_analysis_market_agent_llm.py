"""Tests for MarketAgent: valid path, engine integration, fail-closed."""

from __future__ import annotations

import json

import pytest

from idis.analysis.agents.market_agent import MarketAgent
from idis.analysis.models import (
    AgentReport,
    AnalysisContext,
    EnrichmentRef,
)
from idis.analysis.no_free_facts import AnalysisNoFreeFactsValidator
from idis.analysis.registry import AnalysisAgentRegistry
from idis.analysis.runner import AnalysisEngine
from idis.audit.sink import InMemoryAuditSink

CLAIM_1 = "00000000-0000-0000-0000-a00000000001"
CLAIM_2 = "00000000-0000-0000-0000-a00000000002"
CALC_1 = "00000000-0000-0000-0000-a00000000010"
ENRICH_1 = "enrich-mkt-001"
TIMESTAMP = "2026-02-08T14:00:00+00:00"


def _make_context(*, with_enrichment: bool = False) -> AnalysisContext:
    enrichment_refs: dict[str, EnrichmentRef] = {}
    if with_enrichment:
        enrichment_refs[ENRICH_1] = EnrichmentRef(
            ref_id=ENRICH_1,
            provider_id="edgar",
            source_id="10-K-2025-COMPETITOR",
        )
    return AnalysisContext(
        deal_id="deal-mkt-1",
        tenant_id="tenant-1",
        run_id="run-mkt-1",
        claim_ids=frozenset({CLAIM_1, CLAIM_2}),
        calc_ids=frozenset({CALC_1}),
        enrichment_refs=enrichment_refs,
        company_name="WidgetCo",
        stage="Seed",
        sector="Enterprise SaaS",
    )


def _valid_market_response(
    *,
    with_enrichment: bool = False,
    drop_field: str | None = None,
) -> str:
    """Return a deterministic valid market agent JSON response."""
    enrichment_ref_ids: list[str] = [ENRICH_1] if with_enrichment else []
    data: dict = {
        "supported_claim_ids": sorted([CLAIM_1, CLAIM_2]),
        "supported_calc_ids": [CALC_1],
        "analysis_sections": {
            "tam_sam_som": "TAM of $12B per industry report claim.",
            "competition": "Three direct competitors identified.",
            "differentiation": "Unique ML-based approach per product claims.",
            "go_to_market": "PLG motion with enterprise upsell.",
            "pricing_power": "Premium pricing justified by differentiation.",
            "market_risk_narrative": "Market timing risk if adoption slows.",
            "regulatory_and_sector_dynamics": "No major regulatory headwinds identified.",
        },
        "risks": [
            {
                "risk_id": "mkt-risk-001",
                "description": "Market timing risk: enterprise adoption cycle is 12-18 months",
                "severity": "MEDIUM",
                "claim_ids": [CLAIM_2],
                "calc_ids": [],
                "enrichment_ref_ids": [],
            },
        ],
        "questions_for_founder": [
            "What is your estimated serviceable obtainable market?",
            "Who are your top three competitors by revenue?",
        ],
        "confidence": 0.62,
        "confidence_justification": (
            "Limited market data; TAM claims self-reported"
        ),
        "muhasabah": {
            "agent_id": "market-agent-01",
            "output_id": "mkt-output-001",
            "supported_claim_ids": sorted([CLAIM_1, CLAIM_2]),
            "supported_calc_ids": [CALC_1],
            "evidence_summary": "Market size claims from pitch deck and industry report",
            "counter_hypothesis": "TAM may be overstated; actual SAM could be 10x smaller",
            "falsifiability_tests": [
                {
                    "test_description": "TAM estimate could be inflated",
                    "required_evidence": "Third-party market sizing report",
                    "pass_fail_rule": "If TAM is <$2B, thesis weakens materially",
                }
            ],
            "uncertainties": [
                {
                    "uncertainty": "Competitor revenue data is estimated, not verified",
                    "impact": "MEDIUM",
                    "mitigation": "Request EDGAR filings or competitor benchmarks",
                }
            ],
            "failure_modes": ["market_timing", "competitive_response"],
            "confidence": 0.62,
            "confidence_justification": (
                "Limited market data; TAM claims self-reported"
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
    """Deterministic LLM client returning pre-built market JSON."""

    def __init__(self, response: str) -> None:
        self._response = response

    def call(self, prompt: str, *, json_mode: bool = False) -> str:
        return self._response


class TestMarketAgentValidPath:
    """MarketAgent produces valid AgentReport on well-formed LLM output."""

    def test_returns_valid_agent_report(self) -> None:
        client = _StubLLMClient(_valid_market_response())
        agent = MarketAgent(llm_client=client)
        ctx = _make_context()

        report = agent.run(ctx)

        assert isinstance(report, AgentReport)
        assert report.agent_id == "market-agent-01"
        assert report.agent_type == "market_agent"
        assert report.confidence == 0.62

    def test_report_has_all_required_fields(self) -> None:
        client = _StubLLMClient(_valid_market_response())
        agent = MarketAgent(llm_client=client)

        report = agent.run(_make_context())

        assert len(report.supported_claim_ids) == 2
        assert len(report.supported_calc_ids) == 1
        assert "tam_sam_som" in report.analysis_sections
        assert len(report.risks) >= 1
        assert len(report.questions_for_founder) >= 1
        assert report.confidence_justification
        assert report.muhasabah is not None

    def test_passes_no_free_facts(self) -> None:
        client = _StubLLMClient(_valid_market_response())
        agent = MarketAgent(llm_client=client)
        ctx = _make_context()

        report = agent.run(ctx)

        validator = AnalysisNoFreeFactsValidator()
        result = validator.validate(report, ctx)
        assert result.passed, f"NFF failed: {[e.message for e in result.errors]}"

    def test_passes_no_free_facts_with_enrichment(self) -> None:
        client = _StubLLMClient(_valid_market_response(with_enrichment=True))
        agent = MarketAgent(llm_client=client)
        ctx = _make_context(with_enrichment=True)

        report = agent.run(ctx)

        validator = AnalysisNoFreeFactsValidator()
        result = validator.validate(report, ctx)
        assert result.passed, f"NFF failed: {[e.message for e in result.errors]}"
        assert ENRICH_1 in report.enrichment_ref_ids


class TestMarketAgentEngineIntegration:
    """MarketAgent integrates with AnalysisEngine end-to-end."""

    def test_engine_runs_market_agent(self) -> None:
        client = _StubLLMClient(_valid_market_response())
        agent = MarketAgent(llm_client=client)

        registry = AnalysisAgentRegistry()
        registry.register(agent)

        sink = InMemoryAuditSink()
        engine = AnalysisEngine(registry=registry, audit_sink=sink)

        bundle = engine.run(_make_context(), ["market-agent-01"])

        assert len(bundle.reports) == 1
        assert bundle.reports[0].agent_type == "market_agent"
        event_types = [e["event_type"] for e in sink.events]
        assert "analysis.completed" in event_types


class TestMarketAgentFailClosed:
    """MarketAgent must fail closed on invalid LLM output."""

    def test_missing_confidence_justification_raises(self) -> None:
        bad_response = _valid_market_response(drop_field="confidence_justification")
        client = _StubLLMClient(bad_response)
        agent = MarketAgent(llm_client=client)

        with pytest.raises(ValueError):
            agent.run(_make_context())

    def test_invalid_json_raises(self) -> None:
        client = _StubLLMClient("not valid json {{{")
        agent = MarketAgent(llm_client=client)

        with pytest.raises(ValueError, match="invalid JSON"):
            agent.run(_make_context())

    def test_missing_muhasabah_raises(self) -> None:
        bad_response = _valid_market_response(drop_field="muhasabah")
        client = _StubLLMClient(bad_response)
        agent = MarketAgent(llm_client=client)

        with pytest.raises(ValueError, match="muhasabah"):
            agent.run(_make_context())

    def test_non_object_json_raises(self) -> None:
        client = _StubLLMClient(json.dumps([1, 2, 3]))
        agent = MarketAgent(llm_client=client)

        with pytest.raises(ValueError, match="non-object"):
            agent.run(_make_context())

    def test_missing_prompt_file_raises(self) -> None:
        from pathlib import Path

        client = _StubLLMClient("")
        agent = MarketAgent(
            llm_client=client,
            prompt_path=Path("/nonexistent/prompt.md"),
        )

        with pytest.raises(ValueError, match="Prompt file not found"):
            agent.run(_make_context())
