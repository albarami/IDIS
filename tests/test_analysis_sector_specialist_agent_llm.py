"""Tests for SectorSpecialistAgent: valid path, engine integration, fail-closed."""

from __future__ import annotations

import json

import pytest

from idis.analysis.agents.sector_specialist_agent import SectorSpecialistAgent
from idis.analysis.models import (
    AgentReport,
    AnalysisContext,
    EnrichmentRef,
)
from idis.analysis.no_free_facts import AnalysisNoFreeFactsValidator
from idis.analysis.registry import AnalysisAgentRegistry
from idis.analysis.runner import AnalysisEngine
from idis.audit.sink import InMemoryAuditSink

CLAIM_1 = "00000000-0000-0000-0000-e00000000001"
CLAIM_2 = "00000000-0000-0000-0000-e00000000002"
CALC_1 = "00000000-0000-0000-0000-e00000000010"
ENRICH_1 = "enrich-sector-001"
TIMESTAMP = "2026-02-09T00:00:00+00:00"


def _make_context(*, with_enrichment: bool = False) -> AnalysisContext:
    enrichment_refs: dict[str, EnrichmentRef] = {}
    if with_enrichment:
        enrichment_refs[ENRICH_1] = EnrichmentRef(
            ref_id=ENRICH_1,
            provider_id="industry-reports",
            source_id="IR-SAAS-2025-Q4",
        )
    return AnalysisContext(
        deal_id="deal-sector-1",
        tenant_id="tenant-1",
        run_id="run-sector-1",
        claim_ids=frozenset({CLAIM_1, CLAIM_2}),
        calc_ids=frozenset({CALC_1}),
        enrichment_refs=enrichment_refs,
        company_name="SectorCo Inc",
        stage="Series A",
        sector="SaaS",
    )


def _valid_sector_specialist_response(
    *,
    with_enrichment: bool = False,
    drop_field: str | None = None,
) -> str:
    """Return a deterministic valid sector specialist agent JSON response."""
    enrichment_ref_ids: list[str] = [ENRICH_1] if with_enrichment else []
    data: dict = {
        "supported_claim_ids": sorted([CLAIM_1, CLAIM_2]),
        "supported_calc_ids": [CALC_1],
        "analysis_sections": {
            "sector_dynamics": "SaaS market growing at 15% CAGR per industry claims.",
            "sector_specific_metrics": "NRR of 115% is below top-quartile SaaS benchmark of 120%.",
            "competitive_landscape": "Fragmented market with no dominant player in vertical.",
            "business_model_fit": "PLG model aligns with sector best practices.",
            "regulatory_environment": "Minimal regulatory burden for B2B SaaS.",
            "sector_specific_risks": "Platform dependency on cloud provider pricing.",
            "sector_tailwinds_headwinds": "AI integration tailwind; budget tightening headwind.",
            "benchmark_comparison": "Burn multiple of 2.1x is within sector median range.",
            "sector_outlook": "Sector consolidation expected in 18-24 months.",
        },
        "risks": [
            {
                "risk_id": "sector-risk-001",
                "description": "Sector consolidation could commoditize the product category",
                "severity": "MEDIUM",
                "claim_ids": [CLAIM_1],
                "calc_ids": [],
                "enrichment_ref_ids": [],
            },
        ],
        "questions_for_founder": [
            "How does your NRR compare to sector top-quartile benchmarks?",
            "What sector-specific regulatory approvals are pending?",
        ],
        "confidence": 0.56,
        "confidence_justification": (
            "Moderate confidence; sector positioning claims are self-reported "
            "and lack third-party benchmark validation"
        ),
        "muhasabah": {
            "agent_id": "sector-specialist-agent-01",
            "output_id": "sector-output-001",
            "supported_claim_ids": sorted([CLAIM_1, CLAIM_2]),
            "supported_calc_ids": [CALC_1],
            "evidence_summary": "Sector positioning supported by market size and NRR claims",
            "counter_hypothesis": "Company may be miscategorized; actual sector dynamics differ",
            "falsifiability_tests": [
                {
                    "test_description": "Sector classification could be wrong",
                    "required_evidence": "Revenue breakdown by product category",
                    "pass_fail_rule": "If >50% revenue is non-SaaS, sector analysis is invalid",
                }
            ],
            "uncertainties": [
                {
                    "uncertainty": "No third-party sector benchmark data available",
                    "impact": "MEDIUM",
                    "mitigation": "Request industry analyst report or peer comparison",
                }
            ],
            "failure_modes": ["sector_misclassification", "benchmark_mismatch"],
            "confidence": 0.56,
            "confidence_justification": (
                "Moderate confidence; sector positioning claims are self-reported "
                "and lack third-party benchmark validation"
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
    """Deterministic LLM client returning pre-built sector specialist JSON."""

    def __init__(self, response: str) -> None:
        self._response = response

    def call(self, prompt: str, *, json_mode: bool = False) -> str:
        return self._response


class TestSectorSpecialistAgentValidPath:
    """SectorSpecialistAgent produces valid AgentReport on well-formed LLM output."""

    def test_returns_valid_agent_report(self) -> None:
        client = _StubLLMClient(_valid_sector_specialist_response())
        agent = SectorSpecialistAgent(llm_client=client)
        ctx = _make_context()

        report = agent.run(ctx)

        assert isinstance(report, AgentReport)
        assert report.agent_id == "sector-specialist-agent-01"
        assert report.agent_type == "sector_specialist_agent"
        assert report.confidence == 0.56

    def test_report_has_all_required_fields(self) -> None:
        client = _StubLLMClient(_valid_sector_specialist_response())
        agent = SectorSpecialistAgent(llm_client=client)

        report = agent.run(_make_context())

        assert len(report.supported_claim_ids) == 2
        assert len(report.supported_calc_ids) == 1
        assert "sector_dynamics" in report.analysis_sections
        assert len(report.risks) >= 1
        assert len(report.questions_for_founder) >= 1
        assert report.confidence_justification
        assert report.muhasabah is not None

    def test_passes_no_free_facts(self) -> None:
        client = _StubLLMClient(_valid_sector_specialist_response())
        agent = SectorSpecialistAgent(llm_client=client)
        ctx = _make_context()

        report = agent.run(ctx)

        validator = AnalysisNoFreeFactsValidator()
        result = validator.validate(report, ctx)
        assert result.passed, f"NFF failed: {[e.message for e in result.errors]}"

    def test_passes_no_free_facts_with_enrichment(self) -> None:
        client = _StubLLMClient(_valid_sector_specialist_response(with_enrichment=True))
        agent = SectorSpecialistAgent(llm_client=client)
        ctx = _make_context(with_enrichment=True)

        report = agent.run(ctx)

        validator = AnalysisNoFreeFactsValidator()
        result = validator.validate(report, ctx)
        assert result.passed, f"NFF failed: {[e.message for e in result.errors]}"
        assert ENRICH_1 in report.enrichment_ref_ids


class TestSectorSpecialistAgentEngineIntegration:
    """SectorSpecialistAgent integrates with AnalysisEngine end-to-end."""

    def test_engine_runs_sector_specialist_agent(self) -> None:
        client = _StubLLMClient(_valid_sector_specialist_response())
        agent = SectorSpecialistAgent(llm_client=client)

        registry = AnalysisAgentRegistry()
        registry.register(agent)

        sink = InMemoryAuditSink()
        engine = AnalysisEngine(registry=registry, audit_sink=sink)

        bundle = engine.run(_make_context(), ["sector-specialist-agent-01"])

        assert len(bundle.reports) == 1
        assert bundle.reports[0].agent_type == "sector_specialist_agent"
        event_types = [e["event_type"] for e in sink.events]
        assert "analysis.completed" in event_types


class TestSectorSpecialistAgentFailClosed:
    """SectorSpecialistAgent must fail closed on invalid LLM output."""

    def test_missing_confidence_justification_raises(self) -> None:
        bad_response = _valid_sector_specialist_response(drop_field="confidence_justification")
        client = _StubLLMClient(bad_response)
        agent = SectorSpecialistAgent(llm_client=client)

        with pytest.raises(ValueError):
            agent.run(_make_context())

    def test_invalid_json_raises(self) -> None:
        client = _StubLLMClient("not valid json {{{")
        agent = SectorSpecialistAgent(llm_client=client)

        with pytest.raises(ValueError, match="invalid JSON"):
            agent.run(_make_context())

    def test_missing_muhasabah_raises(self) -> None:
        bad_response = _valid_sector_specialist_response(drop_field="muhasabah")
        client = _StubLLMClient(bad_response)
        agent = SectorSpecialistAgent(llm_client=client)

        with pytest.raises(ValueError, match="muhasabah"):
            agent.run(_make_context())

    def test_non_object_json_raises(self) -> None:
        client = _StubLLMClient(json.dumps([1, 2, 3]))
        agent = SectorSpecialistAgent(llm_client=client)

        with pytest.raises(ValueError, match="non-object"):
            agent.run(_make_context())

    def test_missing_prompt_file_raises(self) -> None:
        from pathlib import Path

        client = _StubLLMClient("")
        agent = SectorSpecialistAgent(
            llm_client=client,
            prompt_path=Path("/nonexistent/prompt.md"),
        )

        with pytest.raises(ValueError, match="Prompt file not found"):
            agent.run(_make_context())
