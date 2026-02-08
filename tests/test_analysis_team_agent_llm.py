"""Tests for TeamAgent: valid path, engine integration, fail-closed."""

from __future__ import annotations

import json

import pytest

from idis.analysis.agents.team_agent import TeamAgent
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
ENRICH_1 = "enrich-team-001"
TIMESTAMP = "2026-02-09T00:00:00+00:00"


def _make_context(*, with_enrichment: bool = False) -> AnalysisContext:
    enrichment_refs: dict[str, EnrichmentRef] = {}
    if with_enrichment:
        enrichment_refs[ENRICH_1] = EnrichmentRef(
            ref_id=ENRICH_1,
            provider_id="crunchbase",
            source_id="founder-profile-2025",
        )
    return AnalysisContext(
        deal_id="deal-team-1",
        tenant_id="tenant-1",
        run_id="run-team-1",
        claim_ids=frozenset({CLAIM_1, CLAIM_2}),
        calc_ids=frozenset({CALC_1}),
        enrichment_refs=enrichment_refs,
        company_name="TeamCo",
        stage="Series A",
        sector="HealthTech",
    )


def _valid_team_response(
    *,
    with_enrichment: bool = False,
    drop_field: str | None = None,
) -> str:
    """Return a deterministic valid team agent JSON response."""
    enrichment_ref_ids: list[str] = [ENRICH_1] if with_enrichment else []
    data: dict = {
        "supported_claim_ids": sorted([CLAIM_1, CLAIM_2]),
        "supported_calc_ids": [CALC_1],
        "analysis_sections": {
            "founder_market_fit": "CEO has 10 years in healthcare per LinkedIn.",
            "leadership_completeness": "CTO and VP Eng in place; no VP Sales yet.",
            "track_record": "CEO previously exited a health-data startup.",
            "technical_capability": "CTO built prior platform to 1M users.",
            "team_dynamics": "Co-founders have worked together for 5 years.",
            "organizational_scalability": "Team of 15, hiring plan for 30 in 12 months.",
            "key_person_risk": "CEO is sole customer relationship holder.",
            "team_risks_narrative": "VP Sales gap creates go-to-market risk.",
            "advisory_and_board": "Two domain-expert advisors on board.",
        },
        "risks": [
            {
                "risk_id": "team-risk-001",
                "description": "CEO is sole holder of key customer relationships",
                "severity": "HIGH",
                "claim_ids": [CLAIM_1],
                "calc_ids": [],
                "enrichment_ref_ids": [],
            },
        ],
        "questions_for_founder": [
            "What is the equity split among co-founders?",
            "What are the key hires planned in the next 12 months?",
        ],
        "confidence": 0.60,
        "confidence_justification": (
            "Moderate confidence: founder backgrounds verified but team dynamics self-reported"
        ),
        "muhasabah": {
            "agent_id": "team-agent-01",
            "output_id": "team-output-001",
            "supported_claim_ids": sorted([CLAIM_1, CLAIM_2]),
            "supported_calc_ids": [CALC_1],
            "evidence_summary": "Founder backgrounds verified via enrichment data",
            "counter_hypothesis": "Team cohesion may be overstated; co-founder conflict possible",
            "falsifiability_tests": [
                {
                    "test_description": "Co-founder alignment could be surface-level",
                    "required_evidence": "Vesting schedules and equity agreements",
                    "pass_fail_rule": "If equity is heavily skewed, alignment risk increases",
                }
            ],
            "uncertainties": [
                {
                    "uncertainty": "Co-founder working relationship untested at scale",
                    "impact": "MEDIUM",
                    "mitigation": "Reference checks with prior colleagues",
                }
            ],
            "failure_modes": ["key_person_departure", "founder_conflict"],
            "confidence": 0.60,
            "confidence_justification": (
                "Moderate confidence: founder backgrounds verified but team dynamics self-reported"
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
    """Deterministic LLM client returning pre-built team JSON."""

    def __init__(self, response: str) -> None:
        self._response = response

    def call(self, prompt: str, *, json_mode: bool = False) -> str:
        return self._response


class TestTeamAgentValidPath:
    """TeamAgent produces valid AgentReport on well-formed LLM output."""

    def test_returns_valid_agent_report(self) -> None:
        client = _StubLLMClient(_valid_team_response())
        agent = TeamAgent(llm_client=client)
        ctx = _make_context()

        report = agent.run(ctx)

        assert isinstance(report, AgentReport)
        assert report.agent_id == "team-agent-01"
        assert report.agent_type == "team_agent"
        assert report.confidence == 0.60

    def test_report_has_all_required_fields(self) -> None:
        client = _StubLLMClient(_valid_team_response())
        agent = TeamAgent(llm_client=client)

        report = agent.run(_make_context())

        assert len(report.supported_claim_ids) == 2
        assert len(report.supported_calc_ids) == 1
        assert "founder_market_fit" in report.analysis_sections
        assert len(report.risks) >= 1
        assert len(report.questions_for_founder) >= 1
        assert report.confidence_justification
        assert report.muhasabah is not None

    def test_passes_no_free_facts(self) -> None:
        client = _StubLLMClient(_valid_team_response())
        agent = TeamAgent(llm_client=client)
        ctx = _make_context()

        report = agent.run(ctx)

        validator = AnalysisNoFreeFactsValidator()
        result = validator.validate(report, ctx)
        assert result.passed, f"NFF failed: {[e.message for e in result.errors]}"

    def test_passes_no_free_facts_with_enrichment(self) -> None:
        client = _StubLLMClient(_valid_team_response(with_enrichment=True))
        agent = TeamAgent(llm_client=client)
        ctx = _make_context(with_enrichment=True)

        report = agent.run(ctx)

        validator = AnalysisNoFreeFactsValidator()
        result = validator.validate(report, ctx)
        assert result.passed, f"NFF failed: {[e.message for e in result.errors]}"
        assert ENRICH_1 in report.enrichment_ref_ids


class TestTeamAgentEngineIntegration:
    """TeamAgent integrates with AnalysisEngine end-to-end."""

    def test_engine_runs_team_agent(self) -> None:
        client = _StubLLMClient(_valid_team_response())
        agent = TeamAgent(llm_client=client)

        registry = AnalysisAgentRegistry()
        registry.register(agent)

        sink = InMemoryAuditSink()
        engine = AnalysisEngine(registry=registry, audit_sink=sink)

        bundle = engine.run(_make_context(), ["team-agent-01"])

        assert len(bundle.reports) == 1
        assert bundle.reports[0].agent_type == "team_agent"
        event_types = [e["event_type"] for e in sink.events]
        assert "analysis.completed" in event_types


class TestTeamAgentFailClosed:
    """TeamAgent must fail closed on invalid LLM output."""

    def test_missing_confidence_justification_raises(self) -> None:
        bad_response = _valid_team_response(drop_field="confidence_justification")
        client = _StubLLMClient(bad_response)
        agent = TeamAgent(llm_client=client)

        with pytest.raises(ValueError):
            agent.run(_make_context())

    def test_invalid_json_raises(self) -> None:
        client = _StubLLMClient("not valid json {{{")
        agent = TeamAgent(llm_client=client)

        with pytest.raises(ValueError, match="invalid JSON"):
            agent.run(_make_context())

    def test_missing_muhasabah_raises(self) -> None:
        bad_response = _valid_team_response(drop_field="muhasabah")
        client = _StubLLMClient(bad_response)
        agent = TeamAgent(llm_client=client)

        with pytest.raises(ValueError, match="muhasabah"):
            agent.run(_make_context())

    def test_non_object_json_raises(self) -> None:
        client = _StubLLMClient(json.dumps([1, 2, 3]))
        agent = TeamAgent(llm_client=client)

        with pytest.raises(ValueError, match="non-object"):
            agent.run(_make_context())

    def test_missing_prompt_file_raises(self) -> None:
        from pathlib import Path

        client = _StubLLMClient("")
        agent = TeamAgent(
            llm_client=client,
            prompt_path=Path("/nonexistent/prompt.md"),
        )

        with pytest.raises(ValueError, match="Prompt file not found"):
            agent.run(_make_context())
