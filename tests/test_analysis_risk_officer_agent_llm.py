"""Tests for RiskOfficerAgent: valid path, engine integration, fail-closed."""

from __future__ import annotations

import json

import pytest

from idis.analysis.agents.risk_officer_agent import RiskOfficerAgent
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
ENRICH_1 = "enrich-risk-001"
TIMESTAMP = "2026-02-09T00:00:00+00:00"


def _make_context(*, with_enrichment: bool = False) -> AnalysisContext:
    enrichment_refs: dict[str, EnrichmentRef] = {}
    if with_enrichment:
        enrichment_refs[ENRICH_1] = EnrichmentRef(
            ref_id=ENRICH_1,
            provider_id="litigation-db",
            source_id="LIT-2025-98765",
        )
    return AnalysisContext(
        deal_id="deal-risk-1",
        tenant_id="tenant-1",
        run_id="run-risk-1",
        claim_ids=frozenset({CLAIM_1, CLAIM_2}),
        calc_ids=frozenset({CALC_1}),
        enrichment_refs=enrichment_refs,
        company_name="RiskCo Inc",
        stage="Series A",
        sector="Fintech",
    )


def _valid_risk_officer_response(
    *,
    with_enrichment: bool = False,
    drop_field: str | None = None,
) -> str:
    """Return a deterministic valid risk officer agent JSON response."""
    enrichment_ref_ids: list[str] = [ENRICH_1] if with_enrichment else []
    data: dict = {
        "supported_claim_ids": sorted([CLAIM_1, CLAIM_2]),
        "supported_calc_ids": [CALC_1],
        "analysis_sections": {
            "governance_and_controls": "Board has 3 members per governance claims.",
            "fraud_indicators": "No red flags identified in available claims.",
            "operational_risk": "Key person dependency on CTO per team claims.",
            "legal_and_regulatory": "Fintech license pending per regulatory claims.",
            "financial_risk": "18 months runway per calc engine.",
            "reputational_risk": "No negative press identified.",
            "downside_scenarios": "Loss of fintech license would halt operations.",
            "risk_mitigation": "D&O insurance in place per claims.",
            "aggregate_risk_rating": "MEDIUM overall risk with governance gaps.",
        },
        "risks": [
            {
                "risk_id": "risk-off-001",
                "description": "Fintech license pending; denial would halt operations",
                "severity": "HIGH",
                "claim_ids": [CLAIM_1],
                "calc_ids": [],
                "enrichment_ref_ids": [],
            },
        ],
        "questions_for_founder": [
            "Are there any pending or threatened legal actions?",
            "What internal controls exist for financial reporting?",
        ],
        "confidence": 0.52,
        "confidence_justification": (
            "Limited visibility into governance and legal exposure; "
            "risk assessment based primarily on self-reported claims"
        ),
        "muhasabah": {
            "agent_id": "risk-officer-agent-01",
            "output_id": "risk-output-001",
            "supported_claim_ids": sorted([CLAIM_1, CLAIM_2]),
            "supported_calc_ids": [CALC_1],
            "evidence_summary": "Governance claims supported by board meeting minutes",
            "counter_hypothesis": "Governance gaps may be normal for this stage",
            "falsifiability_tests": [
                {
                    "test_description": "Governance gaps could be standard early-stage",
                    "required_evidence": "Comparison with peer governance structures",
                    "pass_fail_rule": "If peers have similar gaps, risk is overstated",
                }
            ],
            "uncertainties": [
                {
                    "uncertainty": "No independent legal review available",
                    "impact": "HIGH",
                    "mitigation": "Request outside counsel opinion",
                }
            ],
            "failure_modes": ["governance_gap", "undisclosed_litigation"],
            "confidence": 0.52,
            "confidence_justification": (
                "Limited visibility into governance and legal exposure; "
                "risk assessment based primarily on self-reported claims"
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
    """Deterministic LLM client returning pre-built risk officer JSON."""

    def __init__(self, response: str) -> None:
        self._response = response

    def call(self, prompt: str, *, json_mode: bool = False) -> str:
        return self._response


class TestRiskOfficerAgentValidPath:
    """RiskOfficerAgent produces valid AgentReport on well-formed LLM output."""

    def test_returns_valid_agent_report(self) -> None:
        client = _StubLLMClient(_valid_risk_officer_response())
        agent = RiskOfficerAgent(llm_client=client)
        ctx = _make_context()

        report = agent.run(ctx)

        assert isinstance(report, AgentReport)
        assert report.agent_id == "risk-officer-agent-01"
        assert report.agent_type == "risk_officer_agent"
        assert report.confidence == 0.52

    def test_report_has_all_required_fields(self) -> None:
        client = _StubLLMClient(_valid_risk_officer_response())
        agent = RiskOfficerAgent(llm_client=client)

        report = agent.run(_make_context())

        assert len(report.supported_claim_ids) == 2
        assert len(report.supported_calc_ids) == 1
        assert "governance_and_controls" in report.analysis_sections
        assert len(report.risks) >= 1
        assert len(report.questions_for_founder) >= 1
        assert report.confidence_justification
        assert report.muhasabah is not None

    def test_passes_no_free_facts(self) -> None:
        client = _StubLLMClient(_valid_risk_officer_response())
        agent = RiskOfficerAgent(llm_client=client)
        ctx = _make_context()

        report = agent.run(ctx)

        validator = AnalysisNoFreeFactsValidator()
        result = validator.validate(report, ctx)
        assert result.passed, f"NFF failed: {[e.message for e in result.errors]}"

    def test_passes_no_free_facts_with_enrichment(self) -> None:
        client = _StubLLMClient(_valid_risk_officer_response(with_enrichment=True))
        agent = RiskOfficerAgent(llm_client=client)
        ctx = _make_context(with_enrichment=True)

        report = agent.run(ctx)

        validator = AnalysisNoFreeFactsValidator()
        result = validator.validate(report, ctx)
        assert result.passed, f"NFF failed: {[e.message for e in result.errors]}"
        assert ENRICH_1 in report.enrichment_ref_ids


class TestRiskOfficerAgentEngineIntegration:
    """RiskOfficerAgent integrates with AnalysisEngine end-to-end."""

    def test_engine_runs_risk_officer_agent(self) -> None:
        client = _StubLLMClient(_valid_risk_officer_response())
        agent = RiskOfficerAgent(llm_client=client)

        registry = AnalysisAgentRegistry()
        registry.register(agent)

        sink = InMemoryAuditSink()
        engine = AnalysisEngine(registry=registry, audit_sink=sink)

        bundle = engine.run(_make_context(), ["risk-officer-agent-01"])

        assert len(bundle.reports) == 1
        assert bundle.reports[0].agent_type == "risk_officer_agent"
        event_types = [e["event_type"] for e in sink.events]
        assert "analysis.completed" in event_types


class TestRiskOfficerAgentFailClosed:
    """RiskOfficerAgent must fail closed on invalid LLM output."""

    def test_missing_confidence_justification_raises(self) -> None:
        bad_response = _valid_risk_officer_response(drop_field="confidence_justification")
        client = _StubLLMClient(bad_response)
        agent = RiskOfficerAgent(llm_client=client)

        with pytest.raises(ValueError):
            agent.run(_make_context())

    def test_invalid_json_raises(self) -> None:
        client = _StubLLMClient("not valid json {{{")
        agent = RiskOfficerAgent(llm_client=client)

        with pytest.raises(ValueError, match="invalid JSON"):
            agent.run(_make_context())

    def test_missing_muhasabah_raises(self) -> None:
        bad_response = _valid_risk_officer_response(drop_field="muhasabah")
        client = _StubLLMClient(bad_response)
        agent = RiskOfficerAgent(llm_client=client)

        with pytest.raises(ValueError, match="muhasabah"):
            agent.run(_make_context())

    def test_non_object_json_raises(self) -> None:
        client = _StubLLMClient(json.dumps([1, 2, 3]))
        agent = RiskOfficerAgent(llm_client=client)

        with pytest.raises(ValueError, match="non-object"):
            agent.run(_make_context())

    def test_missing_prompt_file_raises(self) -> None:
        from pathlib import Path

        client = _StubLLMClient("")
        agent = RiskOfficerAgent(
            llm_client=client,
            prompt_path=Path("/nonexistent/prompt.md"),
        )

        with pytest.raises(ValueError, match="Prompt file not found"):
            agent.run(_make_context())
