"""LLM scorecard runner fail-closed tests — Phase 9.

Verifies that the LLM scorecard runner rejects invalid LLM output:
invalid JSON, JSON array, missing dimension_scores, missing one dimension,
missing confidence_justification, missing muhasabah.
All must raise ValueError.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from idis.analysis.models import (
    AnalysisBundle,
    AnalysisContext,
)
from idis.analysis.scoring.llm_scorecard_runner import LLMScorecardRunner
from idis.analysis.scoring.models import ScoreDimension, Stage

CLAIM_1 = "00000000-0000-0000-0000-a00000000001"
CLAIM_2 = "00000000-0000-0000-0000-a00000000002"
CALC_1 = "00000000-0000-0000-0000-a00000000010"
TIMESTAMP = "2026-02-09T10:00:00+00:00"


def _make_context() -> AnalysisContext:
    return AnalysisContext(
        deal_id="deal-score-1",
        tenant_id="tenant-1",
        run_id="run-score-1",
        claim_ids=frozenset({CLAIM_1, CLAIM_2}),
        calc_ids=frozenset({CALC_1}),
        enrichment_refs={},
        company_name="ScoreCo",
        stage="Series A",
        sector="SaaS",
    )


def _make_bundle() -> AnalysisBundle:
    return AnalysisBundle(
        deal_id="deal-score-1",
        tenant_id="tenant-1",
        run_id="run-score-1",
        reports=[],
        timestamp=TIMESTAMP,
    )


def _make_dimension_score_dict(
    dimension: str,
    *,
    drop_field: str | None = None,
) -> dict:
    """Build a single valid dimension score dict."""
    data: dict = {
        "dimension": dimension,
        "score": 0.70,
        "rationale": f"Rationale for {dimension} based on evidence.",
        "supported_claim_ids": [CLAIM_1],
        "supported_calc_ids": [CALC_1],
        "enrichment_refs": [],
        "confidence": 0.65,
        "confidence_justification": f"Moderate confidence for {dimension}.",
        "muhasabah": {
            "agent_id": "scoring-agent-01",
            "output_id": f"score-output-{dimension.lower()}",
            "supported_claim_ids": [CLAIM_1],
            "supported_calc_ids": [CALC_1],
            "evidence_summary": f"Evidence summary for {dimension}",
            "counter_hypothesis": f"Counter hypothesis for {dimension}",
            "falsifiability_tests": [
                {
                    "test_description": "Test for this dimension",
                    "required_evidence": "Required evidence",
                    "pass_fail_rule": "Pass/fail rule",
                }
            ],
            "uncertainties": [
                {
                    "uncertainty": "Key uncertainty",
                    "impact": "MEDIUM",
                    "mitigation": "Mitigation approach",
                }
            ],
            "failure_modes": ["generic_failure"],
            "confidence": 0.65,
            "confidence_justification": f"Moderate confidence for {dimension}.",
            "timestamp": TIMESTAMP,
            "is_subjective": False,
        },
    }
    if drop_field:
        data.pop(drop_field, None)
    return data


def _valid_full_response(
    *,
    drop_dimension: str | None = None,
    drop_field_in_first: str | None = None,
) -> str:
    """Build a valid full scoring response with all 8 dimensions."""
    dimensions = [d.value for d in ScoreDimension]
    scores: dict[str, dict] = {}
    for dim in dimensions:
        if dim == drop_dimension:
            continue
        if drop_field_in_first and dim == dimensions[0]:
            scores[dim] = _make_dimension_score_dict(dim, drop_field=drop_field_in_first)
        else:
            scores[dim] = _make_dimension_score_dict(dim)
    return json.dumps({"dimension_scores": scores}, sort_keys=True)


class _StubLLMClient:
    """Deterministic LLM client returning pre-built JSON."""

    def __init__(self, response: str) -> None:
        self._response = response

    def call(self, prompt: str, *, json_mode: bool = False) -> str:
        return self._response


class TestLLMScorecardRunnerFailClosed:
    """LLM scorecard runner must fail closed on invalid output."""

    def test_invalid_json_raises(self) -> None:
        client = _StubLLMClient("not valid json {{{")
        runner = LLMScorecardRunner(llm_client=client)

        with pytest.raises(ValueError, match="invalid JSON"):
            runner.run(_make_context(), _make_bundle(), Stage.SERIES_A)

    def test_json_array_raises(self) -> None:
        client = _StubLLMClient(json.dumps([1, 2, 3]))
        runner = LLMScorecardRunner(llm_client=client)

        with pytest.raises(ValueError, match="non-object"):
            runner.run(_make_context(), _make_bundle(), Stage.SERIES_A)

    def test_missing_dimension_scores_key_raises(self) -> None:
        client = _StubLLMClient(json.dumps({"something_else": {}}))
        runner = LLMScorecardRunner(llm_client=client)

        with pytest.raises(ValueError, match="dimension_scores"):
            runner.run(_make_context(), _make_bundle(), Stage.SERIES_A)

    def test_missing_one_dimension_returns_incomplete(self) -> None:
        response = _valid_full_response(drop_dimension="MARKET_ATTRACTIVENESS")
        client = _StubLLMClient(response)
        runner = LLMScorecardRunner(llm_client=client)

        result = runner.run(_make_context(), _make_bundle(), Stage.SERIES_A)
        assert ScoreDimension.MARKET_ATTRACTIVENESS not in result

    def test_missing_confidence_justification_raises(self) -> None:
        response = _valid_full_response(drop_field_in_first="confidence_justification")
        client = _StubLLMClient(response)
        runner = LLMScorecardRunner(llm_client=client)

        with pytest.raises(ValueError):
            runner.run(_make_context(), _make_bundle(), Stage.SERIES_A)

    def test_missing_muhasabah_raises(self) -> None:
        dims = [d.value for d in ScoreDimension]
        scores: dict[str, dict] = {}
        for dim in dims:
            d = _make_dimension_score_dict(dim)
            if dim == dims[0]:
                d["muhasabah"] = "not_a_dict"
            scores[dim] = d
        response = json.dumps({"dimension_scores": scores}, sort_keys=True)
        client = _StubLLMClient(response)
        runner = LLMScorecardRunner(llm_client=client)

        with pytest.raises(ValueError, match="muhasabah"):
            runner.run(_make_context(), _make_bundle(), Stage.SERIES_A)

    def test_missing_prompt_file_raises(self) -> None:
        client = _StubLLMClient("")
        runner = LLMScorecardRunner(
            llm_client=client,
            prompt_path=Path("/nonexistent/prompt.md"),
        )

        with pytest.raises(ValueError, match="Prompt file not found"):
            runner.run(_make_context(), _make_bundle(), Stage.SERIES_A)

    def test_valid_response_returns_all_dimensions(self) -> None:
        response = _valid_full_response()
        client = _StubLLMClient(response)
        runner = LLMScorecardRunner(llm_client=client)

        result = runner.run(_make_context(), _make_bundle(), Stage.SERIES_A)

        assert len(result) == 8
        for dim in ScoreDimension:
            assert dim in result
            assert result[dim].score == pytest.approx(0.70)
