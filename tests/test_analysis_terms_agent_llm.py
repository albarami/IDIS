"""Tests for TermsAgent: valid path, engine integration, fail-closed."""

from __future__ import annotations

import json

import pytest

from idis.analysis.agents.terms_agent import TermsAgent
from idis.analysis.models import (
    AgentReport,
    AnalysisContext,
    EnrichmentRef,
)
from idis.analysis.no_free_facts import AnalysisNoFreeFactsValidator
from idis.analysis.registry import AnalysisAgentRegistry
from idis.analysis.runner import AnalysisEngine
from idis.audit.sink import InMemoryAuditSink

CLAIM_1 = "00000000-0000-0000-0000-c00000000001"
CLAIM_2 = "00000000-0000-0000-0000-c00000000002"
CALC_1 = "00000000-0000-0000-0000-c00000000010"
ENRICH_1 = "enrich-terms-001"
TIMESTAMP = "2026-02-09T00:00:00+00:00"


def _make_context(*, with_enrichment: bool = False) -> AnalysisContext:
    enrichment_refs: dict[str, EnrichmentRef] = {}
    if with_enrichment:
        enrichment_refs[ENRICH_1] = EnrichmentRef(
            ref_id=ENRICH_1,
            provider_id="pitchbook",
            source_id="comparable-deals-2025-q4",
        )
    return AnalysisContext(
        deal_id="deal-terms-1",
        tenant_id="tenant-1",
        run_id="run-terms-1",
        claim_ids=frozenset({CLAIM_1, CLAIM_2}),
        calc_ids=frozenset({CALC_1}),
        enrichment_refs=enrichment_refs,
        company_name="DealCo",
        stage="Series A",
        sector="Fintech",
    )


def _valid_terms_response(
    *,
    with_enrichment: bool = False,
    drop_field: str | None = None,
) -> str:
    """Return a deterministic valid terms agent JSON response."""
    enrichment_ref_ids: list[str] = [ENRICH_1] if with_enrichment else []
    data: dict = {
        "supported_claim_ids": sorted([CLAIM_1, CLAIM_2]),
        "supported_calc_ids": [CALC_1],
        "analysis_sections": {
            "valuation": "Pre-money $20M, post-money $25M per term sheet.",
            "dilution": "Founders diluted to 60% post-round per calc engine.",
            "liquidation_preferences": "1x non-participating preferred.",
            "protective_provisions": "Standard protective provisions per term sheet.",
            "pro_rata_and_follow_on": "Pro-rata rights for all Series A investors.",
            "conversion_and_exit": "Standard conversion with drag-along at 2x.",
            "cap_table_dynamics": "Three prior angel rounds, clean cap table.",
            "terms_risks_narrative": "Excessive option pool dilutes founders.",
            "benchmark_comparison": "Terms in line with Series A benchmarks.",
        },
        "risks": [
            {
                "risk_id": "terms-risk-001",
                "description": "20% option pool expansion dilutes founders excessively",
                "severity": "MEDIUM",
                "claim_ids": [CLAIM_1],
                "calc_ids": [CALC_1],
                "enrichment_ref_ids": [],
            },
        ],
        "questions_for_founder": [
            "Can you provide the full cap table including convertible instruments?",
            "What are the specific anti-dilution provisions?",
        ],
        "confidence": 0.65,
        "confidence_justification": ("Term sheet available but cap table details incomplete"),
        "muhasabah": {
            "agent_id": "terms-agent-01",
            "output_id": "terms-output-001",
            "supported_claim_ids": sorted([CLAIM_1, CLAIM_2]),
            "supported_calc_ids": [CALC_1],
            "evidence_summary": "Term sheet and cap table claims provide core evidence",
            "counter_hypothesis": "Hidden convertible notes may alter dilution picture",
            "falsifiability_tests": [
                {
                    "test_description": "Cap table may omit convertible instruments",
                    "required_evidence": "Full capitalization table with all instruments",
                    "pass_fail_rule": "If undisclosed SAFEs exist, dilution analysis invalid",
                }
            ],
            "uncertainties": [
                {
                    "uncertainty": "Convertible note terms not fully disclosed",
                    "impact": "HIGH",
                    "mitigation": "Request full instrument schedule",
                }
            ],
            "failure_modes": ["excessive_dilution", "misaligned_preferences"],
            "confidence": 0.65,
            "confidence_justification": ("Term sheet available but cap table details incomplete"),
            "timestamp": TIMESTAMP,
            "is_subjective": False,
        },
        "enrichment_ref_ids": enrichment_ref_ids,
    }
    if drop_field:
        data.pop(drop_field, None)
    return json.dumps(data, sort_keys=True)


class _StubLLMClient:
    """Deterministic LLM client returning pre-built terms JSON."""

    def __init__(self, response: str) -> None:
        self._response = response

    def call(self, prompt: str, *, json_mode: bool = False) -> str:
        return self._response


class TestTermsAgentValidPath:
    """TermsAgent produces valid AgentReport on well-formed LLM output."""

    def test_returns_valid_agent_report(self) -> None:
        client = _StubLLMClient(_valid_terms_response())
        agent = TermsAgent(llm_client=client)
        ctx = _make_context()

        report = agent.run(ctx)

        assert isinstance(report, AgentReport)
        assert report.agent_id == "terms-agent-01"
        assert report.agent_type == "terms_agent"
        assert report.confidence == 0.65

    def test_report_has_all_required_fields(self) -> None:
        client = _StubLLMClient(_valid_terms_response())
        agent = TermsAgent(llm_client=client)

        report = agent.run(_make_context())

        assert len(report.supported_claim_ids) == 2
        assert len(report.supported_calc_ids) == 1
        assert "valuation" in report.analysis_sections
        assert len(report.risks) >= 1
        assert len(report.questions_for_founder) >= 1
        assert report.confidence_justification
        assert report.muhasabah is not None

    def test_passes_no_free_facts(self) -> None:
        client = _StubLLMClient(_valid_terms_response())
        agent = TermsAgent(llm_client=client)
        ctx = _make_context()

        report = agent.run(ctx)

        validator = AnalysisNoFreeFactsValidator()
        result = validator.validate(report, ctx)
        assert result.passed, f"NFF failed: {[e.message for e in result.errors]}"

    def test_passes_no_free_facts_with_enrichment(self) -> None:
        client = _StubLLMClient(_valid_terms_response(with_enrichment=True))
        agent = TermsAgent(llm_client=client)
        ctx = _make_context(with_enrichment=True)

        report = agent.run(ctx)

        validator = AnalysisNoFreeFactsValidator()
        result = validator.validate(report, ctx)
        assert result.passed, f"NFF failed: {[e.message for e in result.errors]}"
        assert ENRICH_1 in report.enrichment_ref_ids


class TestTermsAgentEngineIntegration:
    """TermsAgent integrates with AnalysisEngine end-to-end."""

    def test_engine_runs_terms_agent(self) -> None:
        client = _StubLLMClient(_valid_terms_response())
        agent = TermsAgent(llm_client=client)

        registry = AnalysisAgentRegistry()
        registry.register(agent)

        sink = InMemoryAuditSink()
        engine = AnalysisEngine(registry=registry, audit_sink=sink)

        bundle = engine.run(_make_context(), ["terms-agent-01"])

        assert len(bundle.reports) == 1
        assert bundle.reports[0].agent_type == "terms_agent"
        event_types = [e["event_type"] for e in sink.events]
        assert "analysis.completed" in event_types


class TestTermsAgentFailClosed:
    """TermsAgent must fail closed on invalid LLM output."""

    def test_missing_confidence_justification_raises(self) -> None:
        bad_response = _valid_terms_response(drop_field="confidence_justification")
        client = _StubLLMClient(bad_response)
        agent = TermsAgent(llm_client=client)

        with pytest.raises(ValueError):
            agent.run(_make_context())

    def test_invalid_json_raises(self) -> None:
        client = _StubLLMClient("not valid json {{{")
        agent = TermsAgent(llm_client=client)

        with pytest.raises(ValueError, match="invalid JSON"):
            agent.run(_make_context())

    def test_missing_muhasabah_raises(self) -> None:
        bad_response = _valid_terms_response(drop_field="muhasabah")
        client = _StubLLMClient(bad_response)
        agent = TermsAgent(llm_client=client)

        with pytest.raises(ValueError, match="muhasabah"):
            agent.run(_make_context())

    def test_non_object_json_raises(self) -> None:
        client = _StubLLMClient(json.dumps([1, 2, 3]))
        agent = TermsAgent(llm_client=client)

        with pytest.raises(ValueError, match="non-object"):
            agent.run(_make_context())

    def test_missing_prompt_file_raises(self) -> None:
        from pathlib import Path

        client = _StubLLMClient("")
        agent = TermsAgent(
            llm_client=client,
            prompt_path=Path("/nonexistent/prompt.md"),
        )

        with pytest.raises(ValueError, match="Prompt file not found"):
            agent.run(_make_context())
