"""Tests for analysis Muḥāsabah enforcement (fail-closed)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from idis.analysis.models import (
    AgentReport,
    AnalysisContext,
    AnalysisMuhasabahRecord,
    Risk,
    RiskSeverity,
)
from idis.analysis.registry import AnalysisAgentRegistry
from idis.analysis.runner import AnalysisEngine, AnalysisEngineError
from idis.audit.sink import InMemoryAuditSink

CLAIM_1 = "00000000-0000-0000-0000-000000000001"
CALC_1 = "00000000-0000-0000-0000-000000000002"
TIMESTAMP = datetime.now(UTC).isoformat()


def _make_context() -> AnalysisContext:
    return AnalysisContext(
        deal_id="deal-1",
        tenant_id="tenant-1",
        run_id="run-1",
        claim_ids=frozenset({CLAIM_1}),
        calc_ids=frozenset({CALC_1}),
    )


def _make_valid_muhasabah(agent_id: str = "agent-1") -> AnalysisMuhasabahRecord:
    return AnalysisMuhasabahRecord(
        agent_id=agent_id,
        output_id="output-1",
        supported_claim_ids=[CLAIM_1],
        supported_calc_ids=[CALC_1],
        evidence_summary="Revenue claim backed by audited financials",
        counter_hypothesis="Revenue could be inflated by one-time contracts",
        falsifiability_tests=[],
        uncertainties=[],
        failure_modes=[],
        confidence=0.75,
        confidence_justification="Strong primary source with single corroboration",
        timestamp=TIMESTAMP,
        is_subjective=False,
    )


def _make_valid_report(agent_id: str = "agent-1") -> AgentReport:
    return AgentReport(
        agent_id=agent_id,
        agent_type="example_agent",
        supported_claim_ids=[CLAIM_1],
        supported_calc_ids=[CALC_1],
        analysis_sections={"summary": "Test analysis"},
        risks=[
            Risk(
                risk_id="risk-1",
                description="Test risk",
                severity=RiskSeverity.MEDIUM,
                claim_ids=[CLAIM_1],
            )
        ],
        questions_for_founder=["What is the retention rate?"],
        confidence=0.75,
        confidence_justification="Strong primary source with single corroboration",
        muhasabah=_make_valid_muhasabah(agent_id),
    )


class _ExampleAgent:
    """Deterministic agent returning configurable reports."""

    def __init__(
        self,
        agent_id: str = "agent-1",
        agent_type: str = "example_agent",
        report: AgentReport | None = None,
    ) -> None:
        self._agent_id = agent_id
        self._agent_type = agent_type
        self._report = report

    @property
    def agent_id(self) -> str:
        return self._agent_id

    @property
    def agent_type(self) -> str:
        return self._agent_type

    def run(self, ctx: AnalysisContext) -> AgentReport:
        if self._report is not None:
            return self._report
        return _make_valid_report(self._agent_id)


class TestMuhasabahMissing:
    """Missing muhasabah must fail closed (Pydantic enforcement)."""

    def test_agent_report_requires_muhasabah(self) -> None:
        with pytest.raises((TypeError, ValueError)):
            AgentReport(
                agent_id="agent-1",
                agent_type="example_agent",
                supported_claim_ids=[CLAIM_1],
                supported_calc_ids=[CALC_1],
                analysis_sections={"summary": "Test"},
                risks=[],
                questions_for_founder=[],
                confidence=0.75,
                confidence_justification="Justified",
                muhasabah=None,  # type: ignore[arg-type]
            )


class TestMuhasabahInvalid:
    """Invalid muhasabah must fail closed in the engine."""

    def test_empty_claim_ids_non_subjective_fails(self) -> None:
        """Non-subjective output with empty supported_claim_ids must be rejected."""
        bad_muhasabah = AnalysisMuhasabahRecord(
            agent_id="agent-1",
            output_id="output-1",
            supported_claim_ids=[],
            supported_calc_ids=[CALC_1],
            evidence_summary="Summary",
            counter_hypothesis="Counter",
            falsifiability_tests=[],
            uncertainties=[],
            failure_modes=[],
            confidence=0.75,
            confidence_justification="Justified",
            timestamp=TIMESTAMP,
            is_subjective=False,
        )
        bad_report = AgentReport(
            agent_id="agent-1",
            agent_type="example_agent",
            supported_claim_ids=[CLAIM_1],
            supported_calc_ids=[CALC_1],
            analysis_sections={"summary": "Test"},
            risks=[
                Risk(
                    risk_id="r-1",
                    description="Risk",
                    severity=RiskSeverity.LOW,
                    claim_ids=[CLAIM_1],
                )
            ],
            questions_for_founder=[],
            confidence=0.75,
            confidence_justification="Justified",
            muhasabah=bad_muhasabah,
        )

        registry = AnalysisAgentRegistry()
        agent = _ExampleAgent(report=bad_report)
        registry.register(agent)
        engine = AnalysisEngine(registry=registry, audit_sink=InMemoryAuditSink())

        with pytest.raises(AnalysisEngineError, match="Muhasabah validation failed"):
            engine.run(_make_context(), ["agent-1"])

    def test_high_confidence_no_uncertainties_fails(self) -> None:
        """Confidence > 0.80 without uncertainties must be rejected."""
        bad_muhasabah = AnalysisMuhasabahRecord(
            agent_id="agent-1",
            output_id="output-1",
            supported_claim_ids=[CLAIM_1],
            supported_calc_ids=[],
            evidence_summary="Summary",
            counter_hypothesis="Counter",
            falsifiability_tests=[],
            uncertainties=[],
            failure_modes=[],
            confidence=0.95,
            confidence_justification="Very confident",
            timestamp=TIMESTAMP,
            is_subjective=False,
        )
        bad_report = AgentReport(
            agent_id="agent-1",
            agent_type="example_agent",
            supported_claim_ids=[CLAIM_1],
            supported_calc_ids=[],
            analysis_sections={"summary": "Test"},
            risks=[
                Risk(
                    risk_id="r-1",
                    description="Risk",
                    severity=RiskSeverity.LOW,
                    claim_ids=[CLAIM_1],
                )
            ],
            questions_for_founder=[],
            confidence=0.95,
            confidence_justification="Very confident",
            muhasabah=bad_muhasabah,
        )

        registry = AnalysisAgentRegistry()
        agent = _ExampleAgent(report=bad_report)
        registry.register(agent)
        engine = AnalysisEngine(registry=registry, audit_sink=InMemoryAuditSink())

        with pytest.raises(AnalysisEngineError, match="Muhasabah validation failed"):
            engine.run(_make_context(), ["agent-1"])


class TestMuhasabahValid:
    """Valid muhasabah must pass."""

    def test_valid_muhasabah_passes(self) -> None:
        registry = AnalysisAgentRegistry()
        agent = _ExampleAgent()
        registry.register(agent)
        sink = InMemoryAuditSink()
        engine = AnalysisEngine(registry=registry, audit_sink=sink)

        bundle = engine.run(_make_context(), ["agent-1"])
        assert len(bundle.reports) == 1
        assert bundle.reports[0].muhasabah.confidence == 0.75

    def test_subjective_output_allows_empty_claims(self) -> None:
        """Subjective muhasabah with empty claim_ids is valid."""
        subjective_muhasabah = AnalysisMuhasabahRecord(
            agent_id="agent-1",
            output_id="output-1",
            supported_claim_ids=[],
            supported_calc_ids=[],
            evidence_summary="Qualitative assessment only",
            counter_hypothesis="Alternative view",
            falsifiability_tests=[],
            uncertainties=[],
            failure_modes=[],
            confidence=0.50,
            confidence_justification="Qualitative judgment",
            timestamp=TIMESTAMP,
            is_subjective=True,
        )
        report = AgentReport(
            agent_id="agent-1",
            agent_type="example_agent",
            supported_claim_ids=[],
            supported_calc_ids=[],
            analysis_sections={"summary": "Qualitative analysis"},
            risks=[],
            questions_for_founder=[],
            confidence=0.50,
            confidence_justification="Qualitative judgment",
            muhasabah=subjective_muhasabah,
        )

        registry = AnalysisAgentRegistry()
        agent = _ExampleAgent(report=report)
        registry.register(agent)
        engine = AnalysisEngine(registry=registry, audit_sink=InMemoryAuditSink())

        bundle = engine.run(_make_context(), ["agent-1"])
        assert len(bundle.reports) == 1


class TestConfidenceJustificationRequired:
    """confidence_justification must be required and non-empty on AgentReport."""

    def test_missing_confidence_justification_raises(self) -> None:
        """Omitting confidence_justification must fail at construction."""
        with pytest.raises((TypeError, ValueError)):
            AgentReport(
                agent_id="agent-1",
                agent_type="example_agent",
                supported_claim_ids=[CLAIM_1],
                supported_calc_ids=[CALC_1],
                analysis_sections={"summary": "Test"},
                risks=[],
                questions_for_founder=[],
                confidence=0.75,
                # confidence_justification intentionally omitted
                muhasabah=_make_valid_muhasabah(),
            )

    def test_empty_confidence_justification_raises(self) -> None:
        """Empty-string confidence_justification must fail (min_length=1)."""
        with pytest.raises(ValueError):
            AgentReport(
                agent_id="agent-1",
                agent_type="example_agent",
                supported_claim_ids=[CLAIM_1],
                supported_calc_ids=[CALC_1],
                analysis_sections={"summary": "Test"},
                risks=[],
                questions_for_founder=[],
                confidence=0.75,
                confidence_justification="",
                muhasabah=_make_valid_muhasabah(),
            )


_VALID_KWARGS: dict[str, object] = {
    "agent_id": "agent-1",
    "agent_type": "example_agent",
    "supported_claim_ids": [CLAIM_1],
    "supported_calc_ids": [CALC_1],
    "analysis_sections": {"summary": "Test"},
    "risks": [],
    "questions_for_founder": [],
    "confidence": 0.75,
    "confidence_justification": "Justified",
}

_TDD_REQUIRED_FIELDS = [
    "supported_claim_ids",
    "supported_calc_ids",
    "analysis_sections",
    "risks",
    "questions_for_founder",
    "confidence",
    "confidence_justification",
    "muhasabah",
]


class TestTdd102Completeness:
    """Every TDD §10.2 required field must be non-optional on AgentReport."""

    @pytest.mark.parametrize("field", _TDD_REQUIRED_FIELDS)
    def test_omitting_required_field_raises(self, field: str) -> None:
        """Dropping any single TDD §10.2 field must fail at construction."""
        kwargs: dict[str, object] = {
            **_VALID_KWARGS,
            "muhasabah": _make_valid_muhasabah(),
        }
        kwargs.pop(field)
        with pytest.raises((TypeError, ValueError)):
            AgentReport(**kwargs)  # type: ignore[arg-type]


_SECTION_UNKNOWN_CLAIM = "00000000-0000-0000-0000-999999999999"


class TestAnalysisSectionReferenceMembershipIntegration:
    """Engine must refuse a report whose sole defect is a bad section ref.

    The deterministic _ExampleAgent returns a fully valid report/global/
    risk/Muhasabah envelope; only the structured section's claim_refs or
    calc_refs carries an ungrounded ID.  The existing No-Free-Facts gate
    inside AnalysisEngine._validate_report must reject before a bundle is
    returned — the exact existing AnalysisEngineError text is asserted so a
    refusal cannot be an incidental import or setup failure.
    """

    def test_engine_rejects_unknown_section_claim(self) -> None:
        bad_report = _make_valid_report().model_copy(
            update={
                "analysis_sections": {
                    "summary": "Test analysis",
                    "financial": {
                        "content": "ARR grew to $1.2M in FY2025.",
                        "claim_refs": [_SECTION_UNKNOWN_CLAIM],
                        "calc_refs": [CALC_1],
                        "sanad_grade": "A",
                        "confidence": 0.8,
                    },
                }
            }
        )
        registry = AnalysisAgentRegistry()
        registry.register(_ExampleAgent(report=bad_report))
        engine = AnalysisEngine(registry=registry, audit_sink=InMemoryAuditSink())

        with pytest.raises(AnalysisEngineError) as excinfo:
            engine.run(_make_context(), ["agent-1"])
        message = str(excinfo.value)
        assert "No-Free-Facts validation failed" in message
        assert "NFF_UNKNOWN_CLAIM_ID" in message
        assert _SECTION_UNKNOWN_CLAIM in message

    def test_engine_accepts_valid_structured_section(self) -> None:
        """Companion: a fully grounded structured section reaches the bundle."""
        valid_report = _make_valid_report().model_copy(
            update={
                "analysis_sections": {
                    "summary": "Test analysis",
                    "financial": {
                        "content": "ARR grew to $1.2M in FY2025.",
                        "claim_refs": [CLAIM_1],
                        "calc_refs": [CALC_1],
                        "sanad_grade": "A",
                        "confidence": 0.8,
                    },
                }
            }
        )
        registry = AnalysisAgentRegistry()
        registry.register(_ExampleAgent(report=valid_report))
        engine = AnalysisEngine(registry=registry, audit_sink=InMemoryAuditSink())

        bundle = engine.run(_make_context(), ["agent-1"])
        assert len(bundle.reports) == 1
        section = bundle.reports[0].analysis_sections["financial"]
        assert section["claim_refs"] == [CLAIM_1]
        assert section["calc_refs"] == [CALC_1]
