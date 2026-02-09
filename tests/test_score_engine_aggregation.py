"""Score engine aggregation tests — Phase 9.

Verifies composite score math, band + routing mapping, NFF enforcement,
Muḥāsabah enforcement, and audit event emission using stub LLM output.
"""

from __future__ import annotations

import json

import pytest

from idis.analysis.models import (
    AnalysisBundle,
    AnalysisContext,
    EnrichmentRef,
)
from idis.analysis.scoring.engine import ScoringEngine, ScoringEngineError
from idis.analysis.scoring.llm_scorecard_runner import LLMScorecardRunner
from idis.analysis.scoring.models import (
    RoutingAction,
    ScoreBand,
    ScoreDimension,
    Stage,
)
from idis.audit.sink import AuditSinkError, InMemoryAuditSink

CLAIM_1 = "00000000-0000-0000-0000-b00000000001"
CLAIM_2 = "00000000-0000-0000-0000-b00000000002"
CALC_1 = "00000000-0000-0000-0000-b00000000010"
ENRICH_1 = "enrich-score-001"
TIMESTAMP = "2026-02-09T12:00:00+00:00"


def _make_context(*, with_enrichment: bool = False) -> AnalysisContext:
    enrichment_refs: dict[str, EnrichmentRef] = {}
    if with_enrichment:
        enrichment_refs[ENRICH_1] = EnrichmentRef(
            ref_id=ENRICH_1,
            provider_id="crunchbase",
            source_id="cb-2025-acme",
        )
    return AnalysisContext(
        deal_id="deal-eng-1",
        tenant_id="tenant-1",
        run_id="run-eng-1",
        claim_ids=frozenset({CLAIM_1, CLAIM_2}),
        calc_ids=frozenset({CALC_1}),
        enrichment_refs=enrichment_refs,
        company_name="EngCo",
        stage="Series A",
        sector="SaaS",
    )


def _make_bundle() -> AnalysisBundle:
    return AnalysisBundle(
        deal_id="deal-eng-1",
        tenant_id="tenant-1",
        run_id="run-eng-1",
        reports=[],
        timestamp=TIMESTAMP,
    )


def _make_muhasabah(dim: str) -> dict:
    return {
        "agent_id": "scoring-agent-01",
        "output_id": f"score-{dim.lower()}",
        "supported_claim_ids": [CLAIM_1],
        "supported_calc_ids": [CALC_1],
        "evidence_summary": f"Evidence for {dim}",
        "counter_hypothesis": f"Counter for {dim}",
        "falsifiability_tests": [
            {
                "test_description": "Falsifiability test",
                "required_evidence": "Required evidence",
                "pass_fail_rule": "Pass/fail rule",
            }
        ],
        "uncertainties": [
            {
                "uncertainty": "Key uncertainty",
                "impact": "MEDIUM",
                "mitigation": "Mitigation",
            }
        ],
        "failure_modes": ["failure_mode"],
        "confidence": 0.65,
        "confidence_justification": f"Confidence justification for {dim}",
        "timestamp": TIMESTAMP,
        "is_subjective": False,
    }


def _make_dimension_dict(dim: str, score: float) -> dict:
    return {
        "dimension": dim,
        "score": score,
        "rationale": f"Rationale for {dim}",
        "supported_claim_ids": [CLAIM_1],
        "supported_calc_ids": [CALC_1],
        "enrichment_refs": [],
        "confidence": 0.65,
        "confidence_justification": f"Confidence for {dim}",
        "muhasabah": _make_muhasabah(dim),
    }


def _build_scoring_response(scores: dict[str, float]) -> str:
    """Build a full scoring LLM response with specified scores per dimension."""
    dimension_scores = {}
    for dim_name, score_val in scores.items():
        dimension_scores[dim_name] = _make_dimension_dict(dim_name, score_val)
    return json.dumps({"dimension_scores": dimension_scores}, sort_keys=True)


def _all_dimensions_score(value: float) -> dict[str, float]:
    """Return a mapping of all 8 dimensions to the same score value."""
    return {d.value: value for d in ScoreDimension}


class _StubLLMClient:
    """Deterministic LLM client returning pre-built JSON."""

    def __init__(self, response: str) -> None:
        self._response = response

    def call(self, prompt: str, *, json_mode: bool = False) -> str:
        return self._response


class TestCompositeScoreMath:
    """Verify composite_score = 100 * sum(score_i * weight_i)."""

    def test_uniform_scores_series_a(self) -> None:
        response = _build_scoring_response(_all_dimensions_score(0.80))
        client = _StubLLMClient(response)
        runner = LLMScorecardRunner(llm_client=client)
        sink = InMemoryAuditSink()
        engine = ScoringEngine(runner=runner, audit_sink=sink)

        scorecard = engine.score(_make_context(), _make_bundle(), Stage.SERIES_A)

        assert scorecard.composite_score == pytest.approx(80.0)

    def test_uniform_scores_pre_seed(self) -> None:
        response = _build_scoring_response(_all_dimensions_score(0.50))
        client = _StubLLMClient(response)
        runner = LLMScorecardRunner(llm_client=client)
        sink = InMemoryAuditSink()
        engine = ScoringEngine(runner=runner, audit_sink=sink)

        scorecard = engine.score(_make_context(), _make_bundle(), Stage.PRE_SEED)

        assert scorecard.composite_score == pytest.approx(50.0)

    def test_weighted_composite_pre_seed(self) -> None:
        scores = _all_dimensions_score(0.0)
        scores["TEAM_QUALITY"] = 1.0
        scores["MARKET_ATTRACTIVENESS"] = 0.5
        response = _build_scoring_response(scores)
        client = _StubLLMClient(response)
        runner = LLMScorecardRunner(llm_client=client)
        sink = InMemoryAuditSink()
        engine = ScoringEngine(runner=runner, audit_sink=sink)

        scorecard = engine.score(_make_context(), _make_bundle(), Stage.PRE_SEED)

        expected = 100.0 * (1.0 * 0.40 + 0.5 * 0.30)
        assert scorecard.composite_score == pytest.approx(expected)

    def test_all_zero_scores(self) -> None:
        response = _build_scoring_response(_all_dimensions_score(0.0))
        client = _StubLLMClient(response)
        runner = LLMScorecardRunner(llm_client=client)
        sink = InMemoryAuditSink()
        engine = ScoringEngine(runner=runner, audit_sink=sink)

        scorecard = engine.score(_make_context(), _make_bundle(), Stage.SERIES_A)

        assert scorecard.composite_score == pytest.approx(0.0)

    def test_all_perfect_scores(self) -> None:
        response = _build_scoring_response(_all_dimensions_score(1.0))
        client = _StubLLMClient(response)
        runner = LLMScorecardRunner(llm_client=client)
        sink = InMemoryAuditSink()
        engine = ScoringEngine(runner=runner, audit_sink=sink)

        scorecard = engine.score(_make_context(), _make_bundle(), Stage.SERIES_A)

        assert scorecard.composite_score == pytest.approx(100.0)


class TestBandAndRoutingMapping:
    """Verify score band and routing action derivation."""

    def test_high_band_invest(self) -> None:
        response = _build_scoring_response(_all_dimensions_score(0.80))
        client = _StubLLMClient(response)
        runner = LLMScorecardRunner(llm_client=client)
        sink = InMemoryAuditSink()
        engine = ScoringEngine(runner=runner, audit_sink=sink)

        scorecard = engine.score(_make_context(), _make_bundle(), Stage.SERIES_A)

        assert scorecard.score_band == ScoreBand.HIGH
        assert scorecard.routing == RoutingAction.INVEST

    def test_medium_band_hold(self) -> None:
        response = _build_scoring_response(_all_dimensions_score(0.60))
        client = _StubLLMClient(response)
        runner = LLMScorecardRunner(llm_client=client)
        sink = InMemoryAuditSink()
        engine = ScoringEngine(runner=runner, audit_sink=sink)

        scorecard = engine.score(_make_context(), _make_bundle(), Stage.SERIES_A)

        assert scorecard.score_band == ScoreBand.MEDIUM
        assert scorecard.routing == RoutingAction.HOLD

    def test_low_band_decline(self) -> None:
        response = _build_scoring_response(_all_dimensions_score(0.40))
        client = _StubLLMClient(response)
        runner = LLMScorecardRunner(llm_client=client)
        sink = InMemoryAuditSink()
        engine = ScoringEngine(runner=runner, audit_sink=sink)

        scorecard = engine.score(_make_context(), _make_bundle(), Stage.SERIES_A)

        assert scorecard.score_band == ScoreBand.LOW
        assert scorecard.routing == RoutingAction.DECLINE

    def test_boundary_at_75_is_high(self) -> None:
        response = _build_scoring_response(_all_dimensions_score(0.75))
        client = _StubLLMClient(response)
        runner = LLMScorecardRunner(llm_client=client)
        sink = InMemoryAuditSink()
        engine = ScoringEngine(runner=runner, audit_sink=sink)

        scorecard = engine.score(_make_context(), _make_bundle(), Stage.SERIES_A)

        assert scorecard.score_band == ScoreBand.HIGH

    def test_boundary_at_55_is_medium(self) -> None:
        response = _build_scoring_response(_all_dimensions_score(0.55))
        client = _StubLLMClient(response)
        runner = LLMScorecardRunner(llm_client=client)
        sink = InMemoryAuditSink()
        engine = ScoringEngine(runner=runner, audit_sink=sink)

        scorecard = engine.score(_make_context(), _make_bundle(), Stage.SERIES_A)

        assert scorecard.score_band == ScoreBand.MEDIUM


class TestNFFEnforcement:
    """Scoring engine must reject ungrounded references."""

    def test_unknown_claim_id_raises(self) -> None:
        scores = _all_dimensions_score(0.70)
        dim_scores = {}
        for dim_name, score_val in scores.items():
            d = _make_dimension_dict(dim_name, score_val)
            if dim_name == "MARKET_ATTRACTIVENESS":
                d["supported_claim_ids"] = ["00000000-0000-0000-0000-999999999999"]
            dim_scores[dim_name] = d
        response = json.dumps({"dimension_scores": dim_scores}, sort_keys=True)
        client = _StubLLMClient(response)
        runner = LLMScorecardRunner(llm_client=client)
        sink = InMemoryAuditSink()
        engine = ScoringEngine(runner=runner, audit_sink=sink)

        with pytest.raises(ScoringEngineError, match="No-Free-Facts"):
            engine.score(_make_context(), _make_bundle(), Stage.SERIES_A)

    def test_unknown_calc_id_raises(self) -> None:
        scores = _all_dimensions_score(0.70)
        dim_scores = {}
        for dim_name, score_val in scores.items():
            d = _make_dimension_dict(dim_name, score_val)
            if dim_name == "TEAM_QUALITY":
                d["supported_calc_ids"] = ["00000000-0000-0000-0000-999999999999"]
            dim_scores[dim_name] = d
        response = json.dumps({"dimension_scores": dim_scores}, sort_keys=True)
        client = _StubLLMClient(response)
        runner = LLMScorecardRunner(llm_client=client)
        sink = InMemoryAuditSink()
        engine = ScoringEngine(runner=runner, audit_sink=sink)

        with pytest.raises(ScoringEngineError, match="No-Free-Facts"):
            engine.score(_make_context(), _make_bundle(), Stage.SERIES_A)

    def test_unknown_enrichment_ref_raises(self) -> None:
        scores = _all_dimensions_score(0.70)
        dim_scores = {}
        for dim_name, score_val in scores.items():
            d = _make_dimension_dict(dim_name, score_val)
            if dim_name == "RISK_PROFILE":
                d["enrichment_refs"] = [
                    {
                        "ref_id": "unknown-ref",
                        "provider_id": "some-provider",
                        "source_id": "some-source",
                    }
                ]
            dim_scores[dim_name] = d
        response = json.dumps({"dimension_scores": dim_scores}, sort_keys=True)
        client = _StubLLMClient(response)
        runner = LLMScorecardRunner(llm_client=client)
        sink = InMemoryAuditSink()
        engine = ScoringEngine(runner=runner, audit_sink=sink)

        with pytest.raises(ScoringEngineError, match="No-Free-Facts"):
            engine.score(_make_context(), _make_bundle(), Stage.SERIES_A)

    def test_valid_enrichment_ref_passes(self) -> None:
        scores = _all_dimensions_score(0.70)
        dim_scores = {}
        for dim_name, score_val in scores.items():
            d = _make_dimension_dict(dim_name, score_val)
            if dim_name == "RISK_PROFILE":
                d["enrichment_refs"] = [
                    {
                        "ref_id": ENRICH_1,
                        "provider_id": "crunchbase",
                        "source_id": "cb-2025-acme",
                    }
                ]
            dim_scores[dim_name] = d
        response = json.dumps({"dimension_scores": dim_scores}, sort_keys=True)
        client = _StubLLMClient(response)
        runner = LLMScorecardRunner(llm_client=client)
        sink = InMemoryAuditSink()
        engine = ScoringEngine(runner=runner, audit_sink=sink)

        scorecard = engine.score(
            _make_context(with_enrichment=True), _make_bundle(), Stage.SERIES_A
        )
        assert scorecard.composite_score > 0


class TestAuditEvents:
    """Scoring engine must emit correct audit events."""

    def test_success_emits_started_and_completed(self) -> None:
        response = _build_scoring_response(_all_dimensions_score(0.70))
        client = _StubLLMClient(response)
        runner = LLMScorecardRunner(llm_client=client)
        sink = InMemoryAuditSink()
        engine = ScoringEngine(runner=runner, audit_sink=sink)

        engine.score(_make_context(), _make_bundle(), Stage.SERIES_A)

        event_types = [e["event_type"] for e in sink.events]
        assert "analysis.scoring.started" in event_types
        assert "analysis.scoring.completed" in event_types

    def test_completed_event_includes_composite_score(self) -> None:
        response = _build_scoring_response(_all_dimensions_score(0.70))
        client = _StubLLMClient(response)
        runner = LLMScorecardRunner(llm_client=client)
        sink = InMemoryAuditSink()
        engine = ScoringEngine(runner=runner, audit_sink=sink)

        engine.score(_make_context(), _make_bundle(), Stage.SERIES_A)

        completed = [e for e in sink.events if e["event_type"] == "analysis.scoring.completed"]
        assert len(completed) == 1
        assert "composite_score" in completed[0]
        assert "score_band" in completed[0]
        assert "routing" in completed[0]

    def test_failure_emits_failed_event(self) -> None:
        client = _StubLLMClient("invalid json {{{")
        runner = LLMScorecardRunner(llm_client=client)
        sink = InMemoryAuditSink()
        engine = ScoringEngine(runner=runner, audit_sink=sink)

        with pytest.raises(ScoringEngineError):
            engine.score(_make_context(), _make_bundle(), Stage.SERIES_A)

        event_types = [e["event_type"] for e in sink.events]
        assert "analysis.scoring.started" in event_types
        assert "analysis.scoring.failed" in event_types

    def test_audit_sink_failure_is_fatal(self) -> None:
        response = _build_scoring_response(_all_dimensions_score(0.70))
        client = _StubLLMClient(response)
        runner = LLMScorecardRunner(llm_client=client)

        class _FailingSink:
            def emit(self, event: dict) -> None:
                raise AuditSinkError("Sink down")

        engine = ScoringEngine(runner=runner, audit_sink=_FailingSink())  # type: ignore[arg-type]

        with pytest.raises(AuditSinkError, match="Sink down"):
            engine.score(_make_context(), _make_bundle(), Stage.SERIES_A)


class TestScorecardModel:
    """Scorecard model validation tests."""

    def test_missing_dimension_raises(self) -> None:
        from idis.analysis.scoring.models import Scorecard

        response = _build_scoring_response(_all_dimensions_score(0.70))
        client = _StubLLMClient(response)
        runner = LLMScorecardRunner(llm_client=client)
        sink = InMemoryAuditSink()
        engine = ScoringEngine(runner=runner, audit_sink=sink)

        scorecard = engine.score(_make_context(), _make_bundle(), Stage.SERIES_A)

        dim_scores = dict(scorecard.dimension_scores)
        del dim_scores[ScoreDimension.MARKET_ATTRACTIVENESS]

        with pytest.raises(ValueError, match="missing required dimensions"):
            Scorecard(
                stage=Stage.SERIES_A,
                dimension_scores=dim_scores,
                composite_score=70.0,
                score_band=ScoreBand.MEDIUM,
                routing=RoutingAction.HOLD,
            )

    def test_all_stages_produce_valid_scorecard(self) -> None:
        response = _build_scoring_response(_all_dimensions_score(0.70))
        client = _StubLLMClient(response)

        for stage in Stage:
            runner = LLMScorecardRunner(llm_client=client)
            sink = InMemoryAuditSink()
            engine = ScoringEngine(runner=runner, audit_sink=sink)

            scorecard = engine.score(_make_context(), _make_bundle(), stage)

            assert scorecard.stage == stage
            assert len(scorecard.dimension_scores) == 8
