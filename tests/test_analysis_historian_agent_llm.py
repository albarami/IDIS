"""Tests for HistorianAgent: valid path, engine integration, fail-closed."""

from __future__ import annotations

import json

import pytest

from idis.analysis.agents.historian_agent import HistorianAgent
from idis.analysis.models import (
    AgentReport,
    AnalysisContext,
    EnrichmentRef,
)
from idis.analysis.no_free_facts import AnalysisNoFreeFactsValidator
from idis.analysis.registry import AnalysisAgentRegistry
from idis.analysis.runner import AnalysisEngine
from idis.audit.sink import InMemoryAuditSink

CLAIM_1 = "00000000-0000-0000-0000-d00000000001"
CLAIM_2 = "00000000-0000-0000-0000-d00000000002"
CALC_1 = "00000000-0000-0000-0000-d00000000010"
ENRICH_1 = "enrich-hist-001"
TIMESTAMP = "2026-02-09T00:00:00+00:00"


def _make_context(*, with_enrichment: bool = False) -> AnalysisContext:
    enrichment_refs: dict[str, EnrichmentRef] = {}
    if with_enrichment:
        enrichment_refs[ENRICH_1] = EnrichmentRef(
            ref_id=ENRICH_1,
            provider_id="crunchbase",
            source_id="CB-COMP-2025-54321",
        )
    return AnalysisContext(
        deal_id="deal-hist-1",
        tenant_id="tenant-1",
        run_id="run-hist-1",
        claim_ids=frozenset({CLAIM_1, CLAIM_2}),
        calc_ids=frozenset({CALC_1}),
        enrichment_refs=enrichment_refs,
        company_name="HistoryCo Inc",
        stage="Series B",
        sector="SaaS",
    )


def _valid_historian_response(
    *,
    with_enrichment: bool = False,
    drop_field: str | None = None,
) -> str:
    """Return a deterministic valid historian agent JSON response."""
    enrichment_ref_ids: list[str] = [ENRICH_1] if with_enrichment else []
    data: dict = {
        "supported_claim_ids": sorted([CLAIM_1, CLAIM_2]),
        "supported_calc_ids": [CALC_1],
        "analysis_sections": {
            "historical_analogues": "Similar trajectory to mid-2010s vertical SaaS companies.",
            "pattern_recognition": (
                "Growth pattern matches companies that achieved category leadership."
            ),
            "failure_pattern_analysis": "No premature scaling indicators; burn is controlled.",
            "success_pattern_analysis": "Strong NRR mirrors successful SaaS cohorts.",
            "vintage_and_cohort": "2024 vintage with favorable interest rate trajectory.",
            "founder_trajectory": "Second-time founders with domain expertise.",
            "pivot_history": "One strategic pivot from horizontal to vertical focus.",
            "exit_pathway_analysis": "Vertical SaaS at this scale typically exits via M&A.",
            "historical_risk_factors": "Category risk if market consolidates around incumbent.",
        },
        "risks": [
            {
                "risk_id": "hist-risk-001",
                "description": "Historical analogue companies at this stage had 40% failure rate",
                "severity": "MEDIUM",
                "claim_ids": [CLAIM_1],
                "calc_ids": [],
                "enrichment_ref_ids": [],
            },
        ],
        "questions_for_founder": [
            "What previous strategic pivots has the company made and why?",
            "Which companies do you consider your closest historical analogues?",
        ],
        "confidence": 0.48,
        "confidence_justification": (
            "Historical pattern matching is inherently uncertain; "
            "parallels are suggestive but not predictive"
        ),
        "muhasabah": {
            "agent_id": "historian-agent-01",
            "output_id": "hist-output-001",
            "supported_claim_ids": sorted([CLAIM_1, CLAIM_2]),
            "supported_calc_ids": [CALC_1],
            "evidence_summary": "Growth and retention claims match successful SaaS cohort patterns",
            "counter_hypothesis": "Historical parallels may not hold in current macro environment",
            "falsifiability_tests": [
                {
                    "test_description": "Pattern match could be survivorship bias",
                    "required_evidence": "Full cohort data including failures",
                    "pass_fail_rule": "If failure rate in cohort exceeds 50%, parallel weakens",
                }
            ],
            "uncertainties": [
                {
                    "uncertainty": "Macro environment differs from historical cohort vintage",
                    "impact": "MEDIUM",
                    "mitigation": "Adjust for current interest rate and funding environment",
                }
            ],
            "failure_modes": ["false_analogy", "survivorship_bias"],
            "confidence": 0.48,
            "confidence_justification": (
                "Historical pattern matching is inherently uncertain; "
                "parallels are suggestive but not predictive"
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
    """Deterministic LLM client returning pre-built historian JSON."""

    def __init__(self, response: str) -> None:
        self._response = response

    def call(self, prompt: str, *, json_mode: bool = False) -> str:
        return self._response


class TestHistorianAgentValidPath:
    """HistorianAgent produces valid AgentReport on well-formed LLM output."""

    def test_returns_valid_agent_report(self) -> None:
        client = _StubLLMClient(_valid_historian_response())
        agent = HistorianAgent(llm_client=client)
        ctx = _make_context()

        report = agent.run(ctx)

        assert isinstance(report, AgentReport)
        assert report.agent_id == "historian-agent-01"
        assert report.agent_type == "historian_agent"
        assert report.confidence == 0.48

    def test_report_has_all_required_fields(self) -> None:
        client = _StubLLMClient(_valid_historian_response())
        agent = HistorianAgent(llm_client=client)

        report = agent.run(_make_context())

        assert len(report.supported_claim_ids) == 2
        assert len(report.supported_calc_ids) == 1
        assert "historical_analogues" in report.analysis_sections
        assert len(report.risks) >= 1
        assert len(report.questions_for_founder) >= 1
        assert report.confidence_justification
        assert report.muhasabah is not None

    def test_passes_no_free_facts(self) -> None:
        client = _StubLLMClient(_valid_historian_response())
        agent = HistorianAgent(llm_client=client)
        ctx = _make_context()

        report = agent.run(ctx)

        validator = AnalysisNoFreeFactsValidator()
        result = validator.validate(report, ctx)
        assert result.passed, f"NFF failed: {[e.message for e in result.errors]}"

    def test_passes_no_free_facts_with_enrichment(self) -> None:
        client = _StubLLMClient(_valid_historian_response(with_enrichment=True))
        agent = HistorianAgent(llm_client=client)
        ctx = _make_context(with_enrichment=True)

        report = agent.run(ctx)

        validator = AnalysisNoFreeFactsValidator()
        result = validator.validate(report, ctx)
        assert result.passed, f"NFF failed: {[e.message for e in result.errors]}"
        assert ENRICH_1 in report.enrichment_ref_ids


class TestHistorianAgentEngineIntegration:
    """HistorianAgent integrates with AnalysisEngine end-to-end."""

    def test_engine_runs_historian_agent(self) -> None:
        client = _StubLLMClient(_valid_historian_response())
        agent = HistorianAgent(llm_client=client)

        registry = AnalysisAgentRegistry()
        registry.register(agent)

        sink = InMemoryAuditSink()
        engine = AnalysisEngine(registry=registry, audit_sink=sink)

        bundle = engine.run(_make_context(), ["historian-agent-01"])

        assert len(bundle.reports) == 1
        assert bundle.reports[0].agent_type == "historian_agent"
        event_types = [e["event_type"] for e in sink.events]
        assert "analysis.completed" in event_types


class TestHistorianAgentFailClosed:
    """HistorianAgent must fail closed on invalid LLM output."""

    def test_missing_confidence_justification_raises(self) -> None:
        bad_response = _valid_historian_response(drop_field="confidence_justification")
        client = _StubLLMClient(bad_response)
        agent = HistorianAgent(llm_client=client)

        with pytest.raises(ValueError):
            agent.run(_make_context())

    def test_invalid_json_raises(self) -> None:
        client = _StubLLMClient("not valid json {{{")
        agent = HistorianAgent(llm_client=client)

        with pytest.raises(ValueError, match="invalid JSON"):
            agent.run(_make_context())

    def test_missing_muhasabah_raises(self) -> None:
        bad_response = _valid_historian_response(drop_field="muhasabah")
        client = _StubLLMClient(bad_response)
        agent = HistorianAgent(llm_client=client)

        with pytest.raises(ValueError, match="muhasabah"):
            agent.run(_make_context())

    def test_non_object_json_raises(self) -> None:
        client = _StubLLMClient(json.dumps([1, 2, 3]))
        agent = HistorianAgent(llm_client=client)

        with pytest.raises(ValueError, match="non-object"):
            agent.run(_make_context())

    def test_missing_prompt_file_raises(self) -> None:
        from pathlib import Path

        client = _StubLLMClient("")
        agent = HistorianAgent(
            llm_client=client,
            prompt_path=Path("/nonexistent/prompt.md"),
        )

        with pytest.raises(ValueError, match="Prompt file not found"):
            agent.run(_make_context())
