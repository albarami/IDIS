"""Tests for FinancialAgent: valid path, engine integration, fail-closed."""

from __future__ import annotations

import json

import pytest

from idis.analysis.agents.financial_agent import FinancialAgent
from idis.analysis.models import (
    AgentReport,
    AnalysisContext,
    EnrichmentRef,
)
from idis.analysis.no_free_facts import AnalysisNoFreeFactsValidator
from idis.analysis.registry import AnalysisAgentRegistry
from idis.analysis.runner import AnalysisEngine
from idis.audit.sink import InMemoryAuditSink

CLAIM_1 = "00000000-0000-0000-0000-f00000000001"
CLAIM_2 = "00000000-0000-0000-0000-f00000000002"
CALC_1 = "00000000-0000-0000-0000-f00000000010"
ENRICH_1 = "enrich-fin-001"
TIMESTAMP = "2026-02-08T14:00:00+00:00"


def _make_context(*, with_enrichment: bool = False) -> AnalysisContext:
    enrichment_refs: dict[str, EnrichmentRef] = {}
    if with_enrichment:
        enrichment_refs[ENRICH_1] = EnrichmentRef(
            ref_id=ENRICH_1,
            provider_id="edgar",
            source_id="10-K-2025-ACME",
        )
    return AnalysisContext(
        deal_id="deal-fin-1",
        tenant_id="tenant-1",
        run_id="run-fin-1",
        claim_ids=frozenset({CLAIM_1, CLAIM_2}),
        calc_ids=frozenset({CALC_1}),
        enrichment_refs=enrichment_refs,
        company_name="Acme Corp",
        stage="Series A",
        sector="Fintech",
    )


def _valid_financial_response(
    *,
    with_enrichment: bool = False,
    drop_field: str | None = None,
) -> str:
    """Return a deterministic valid financial agent JSON response."""
    enrichment_ref_ids: list[str] = [ENRICH_1] if with_enrichment else []
    data: dict = {
        "supported_claim_ids": sorted([CLAIM_1, CLAIM_2]),
        "supported_calc_ids": [CALC_1],
        "analysis_sections": {
            "revenue_quality": "Revenue is $2.4M ARR from audited financials.",
            "growth": "YoY growth of 120% per calc engine.",
            "margins": "Gross margin at 72% per calc engine.",
            "burn_and_runway": "Burn $125K/mo, 18 months runway.",
            "unit_economics": "LTV/CAC ratio of 3.2 per calc.",
            "retention": "NRR data not available.",
            "pricing": "SaaS subscription model, $500/seat/month.",
            "cash_needs": "Raising $5M Series A.",
            "financial_risks_narrative": "Customer concentration risk.",
        },
        "risks": [
            {
                "risk_id": "fin-risk-001",
                "description": "Top 3 customers represent 60% of revenue",
                "severity": "HIGH",
                "claim_ids": [CLAIM_1],
                "calc_ids": [],
                "enrichment_ref_ids": [],
            },
        ],
        "questions_for_founder": [
            "What is the net revenue retention rate?",
            "Can you provide a customer cohort breakdown?",
        ],
        "confidence": 0.68,
        "confidence_justification": (
            "Moderate confidence: audited financials, single corroboration"
        ),
        "muhasabah": {
            "agent_id": "financial-agent-01",
            "output_id": "fin-output-001",
            "supported_claim_ids": sorted([CLAIM_1, CLAIM_2]),
            "supported_calc_ids": [CALC_1],
            "evidence_summary": "Audited financials support revenue and burn claims",
            "counter_hypothesis": "Revenue may include non-recurring consulting engagements",
            "falsifiability_tests": [
                {
                    "test_description": "ARR could be inflated by one-time contracts",
                    "required_evidence": "Customer contract breakdown",
                    "pass_fail_rule": "If >30% non-recurring, thesis weakens",
                }
            ],
            "uncertainties": [
                {
                    "uncertainty": "NRR data is self-reported and unverified",
                    "impact": "MEDIUM",
                    "mitigation": "Request audited cohort data",
                }
            ],
            "failure_modes": ["customer_concentration", "margin_compression"],
            "confidence": 0.68,
            "confidence_justification": (
                "Moderate confidence: audited financials, single corroboration"
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
    """Deterministic LLM client returning pre-built financial JSON."""

    def __init__(self, response: str) -> None:
        self._response = response

    def call(self, prompt: str, *, json_mode: bool = False) -> str:
        return self._response


class TestFinancialAgentValidPath:
    """FinancialAgent produces valid AgentReport on well-formed LLM output."""

    def test_returns_valid_agent_report(self) -> None:
        client = _StubLLMClient(_valid_financial_response())
        agent = FinancialAgent(llm_client=client)
        ctx = _make_context()

        report = agent.run(ctx)

        assert isinstance(report, AgentReport)
        assert report.agent_id == "financial-agent-01"
        assert report.agent_type == "financial_agent"
        assert report.confidence == 0.68

    def test_report_has_all_required_fields(self) -> None:
        client = _StubLLMClient(_valid_financial_response())
        agent = FinancialAgent(llm_client=client)

        report = agent.run(_make_context())

        assert len(report.supported_claim_ids) == 2
        assert len(report.supported_calc_ids) == 1
        assert "revenue_quality" in report.analysis_sections
        assert len(report.risks) >= 1
        assert len(report.questions_for_founder) >= 1
        assert report.confidence_justification
        assert report.muhasabah is not None

    def test_passes_no_free_facts(self) -> None:
        client = _StubLLMClient(_valid_financial_response())
        agent = FinancialAgent(llm_client=client)
        ctx = _make_context()

        report = agent.run(ctx)

        validator = AnalysisNoFreeFactsValidator()
        result = validator.validate(report, ctx)
        assert result.passed, f"NFF failed: {[e.message for e in result.errors]}"

    def test_passes_no_free_facts_with_enrichment(self) -> None:
        client = _StubLLMClient(_valid_financial_response(with_enrichment=True))
        agent = FinancialAgent(llm_client=client)
        ctx = _make_context(with_enrichment=True)

        report = agent.run(ctx)

        validator = AnalysisNoFreeFactsValidator()
        result = validator.validate(report, ctx)
        assert result.passed, f"NFF failed: {[e.message for e in result.errors]}"
        assert ENRICH_1 in report.enrichment_ref_ids


class TestFinancialAgentEngineIntegration:
    """FinancialAgent integrates with AnalysisEngine end-to-end."""

    def test_engine_runs_financial_agent(self) -> None:
        client = _StubLLMClient(_valid_financial_response())
        agent = FinancialAgent(llm_client=client)

        registry = AnalysisAgentRegistry()
        registry.register(agent)

        sink = InMemoryAuditSink()
        engine = AnalysisEngine(registry=registry, audit_sink=sink)

        bundle = engine.run(_make_context(), ["financial-agent-01"])

        assert len(bundle.reports) == 1
        assert bundle.reports[0].agent_type == "financial_agent"
        event_types = [e["event_type"] for e in sink.events]
        assert "analysis.completed" in event_types


class TestFinancialAgentFailClosed:
    """FinancialAgent must fail closed on invalid LLM output."""

    def test_missing_confidence_justification_raises(self) -> None:
        bad_response = _valid_financial_response(drop_field="confidence_justification")
        client = _StubLLMClient(bad_response)
        agent = FinancialAgent(llm_client=client)

        with pytest.raises(ValueError):
            agent.run(_make_context())

    def test_invalid_json_raises(self) -> None:
        client = _StubLLMClient("not valid json {{{")
        agent = FinancialAgent(llm_client=client)

        with pytest.raises(ValueError, match="invalid JSON"):
            agent.run(_make_context())

    def test_missing_muhasabah_raises(self) -> None:
        bad_response = _valid_financial_response(drop_field="muhasabah")
        client = _StubLLMClient(bad_response)
        agent = FinancialAgent(llm_client=client)

        with pytest.raises(ValueError, match="muhasabah"):
            agent.run(_make_context())

    def test_non_object_json_raises(self) -> None:
        client = _StubLLMClient(json.dumps([1, 2, 3]))
        agent = FinancialAgent(llm_client=client)

        with pytest.raises(ValueError, match="non-object"):
            agent.run(_make_context())

    def test_missing_prompt_file_raises(self) -> None:
        from pathlib import Path

        client = _StubLLMClient("")
        agent = FinancialAgent(
            llm_client=client,
            prompt_path=Path("/nonexistent/prompt.md"),
        )

        with pytest.raises(ValueError, match="Prompt file not found"):
            agent.run(_make_context())
