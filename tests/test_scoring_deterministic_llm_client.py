"""Tests for DeterministicScoringLLMClient: schema, dimensions, NFF, Muhasabah, wiring."""

from __future__ import annotations

import json

import pytest

from idis.analysis.models import AnalysisContext
from idis.analysis.scoring.models import DimensionScore, ScoreDimension
from idis.services.extraction.extractors.llm_client import (
    DeterministicScoringLLMClient,
)
from idis.validators.muhasabah import validate_muhasabah

CLAIM_A = "00000000-0000-0000-0000-b00000000001"
CLAIM_B = "00000000-0000-0000-0000-b00000000002"
CLAIM_C = "00000000-0000-0000-0000-b00000000003"
CALC_A = "00000000-0000-0000-0000-b00000000010"
CALC_B = "00000000-0000-0000-0000-b00000000020"

ALL_DIMENSION_NAMES = sorted(d.value for d in ScoreDimension)


def _build_scoring_prompt(
    claim_ids: list[str],
    calc_ids: list[str],
) -> str:
    """Build a minimal scoring prompt with CONTEXT PAYLOAD containing registries.

    Mirrors the format produced by llm_scorecard_runner._build_context_payload:
    prompt text, then ---separator, then CONTEXT PAYLOAD:\\n{json}.
    """
    claim_registry = {cid: cid for cid in sorted(claim_ids)}
    calc_registry = {cid: cid for cid in sorted(calc_ids)}
    payload = {
        "stage": "SEED",
        "deal_metadata": {
            "deal_id": "deal-score-1",
            "tenant_id": "tenant-score-1",
            "run_id": "run-score-1",
            "company_name": "ScoreCo",
            "stage": "Seed",
            "sector": "SaaS",
        },
        "claim_registry": claim_registry,
        "calc_registry": calc_registry,
        "enrichment_refs": {},
        "agent_reports": [],
    }
    context_json = json.dumps(payload, sort_keys=True, indent=2)
    return f"Score the deal.\n\n---\n\nCONTEXT PAYLOAD:\n{context_json}"


def _make_scoring_context(
    claim_ids: list[str],
    calc_ids: list[str],
) -> AnalysisContext:
    """Build AnalysisContext matching the prompt registries."""
    return AnalysisContext(
        deal_id="deal-score-1",
        tenant_id="tenant-score-1",
        run_id="run-score-1",
        claim_ids=frozenset(claim_ids),
        calc_ids=frozenset(calc_ids),
        enrichment_refs={},
        company_name="ScoreCo",
        stage="Seed",
        sector="SaaS",
    )


class TestDeterministicScoringLLMClientSchema:
    """Verify stub returns correct top-level scoring schema."""

    def test_returns_dict_not_list(self) -> None:
        client = DeterministicScoringLLMClient()
        prompt = _build_scoring_prompt([CLAIM_A], [CALC_A])
        raw = client.call(prompt, json_mode=True)
        parsed = json.loads(raw)

        assert isinstance(parsed, dict)

    def test_contains_dimension_scores_key(self) -> None:
        client = DeterministicScoringLLMClient()
        prompt = _build_scoring_prompt([CLAIM_A], [CALC_A])
        raw = client.call(prompt, json_mode=True)
        parsed = json.loads(raw)

        assert "dimension_scores" in parsed
        assert isinstance(parsed["dimension_scores"], dict)


class TestDeterministicScoringLLMClientDimensions:
    """Verify all 8 required dimensions are present."""

    def test_contains_all_8_dimensions(self) -> None:
        client = DeterministicScoringLLMClient()
        prompt = _build_scoring_prompt([CLAIM_A, CLAIM_B], [CALC_A])
        raw = client.call(prompt, json_mode=True)
        parsed = json.loads(raw)

        returned_dims = sorted(parsed["dimension_scores"].keys())
        assert returned_dims == ALL_DIMENSION_NAMES

    def test_each_dimension_has_required_keys(self) -> None:
        client = DeterministicScoringLLMClient()
        prompt = _build_scoring_prompt([CLAIM_A], [CALC_A])
        raw = client.call(prompt, json_mode=True)
        parsed = json.loads(raw)

        required_keys = {
            "dimension",
            "score",
            "rationale",
            "supported_claim_ids",
            "supported_calc_ids",
            "confidence",
            "confidence_justification",
            "muhasabah",
        }
        for dim_name, dim_data in parsed["dimension_scores"].items():
            assert required_keys.issubset(dim_data.keys()), (
                f"Dimension {dim_name} missing keys: {required_keys - dim_data.keys()}"
            )


class TestDeterministicScoringLLMClientRegistryIDs:
    """Verify IDs in the stub output are sourced from the prompt registries."""

    def test_claim_ids_from_prompt(self) -> None:
        client = DeterministicScoringLLMClient()
        prompt = _build_scoring_prompt([CLAIM_A, CLAIM_B, CLAIM_C], [CALC_A])
        raw = client.call(prompt, json_mode=True)
        parsed = json.loads(raw)

        prompt_claim_ids = {CLAIM_A, CLAIM_B, CLAIM_C}
        for dim_name, dim_data in parsed["dimension_scores"].items():
            assert set(dim_data["supported_claim_ids"]).issubset(prompt_claim_ids), (
                f"Dimension {dim_name} has claim IDs not in prompt"
            )

    def test_calc_ids_from_prompt(self) -> None:
        client = DeterministicScoringLLMClient()
        prompt = _build_scoring_prompt([CLAIM_A], [CALC_A, CALC_B])
        raw = client.call(prompt, json_mode=True)
        parsed = json.loads(raw)

        prompt_calc_ids = {CALC_A, CALC_B}
        for dim_name, dim_data in parsed["dimension_scores"].items():
            assert set(dim_data["supported_calc_ids"]).issubset(prompt_calc_ids), (
                f"Dimension {dim_name} has calc IDs not in prompt"
            )

    def test_muhasabah_ids_match_dimension_ids(self) -> None:
        client = DeterministicScoringLLMClient()
        prompt = _build_scoring_prompt([CLAIM_A, CLAIM_B], [CALC_A])
        raw = client.call(prompt, json_mode=True)
        parsed = json.loads(raw)

        for _dim_name, dim_data in parsed["dimension_scores"].items():
            assert dim_data["muhasabah"]["supported_claim_ids"] == dim_data["supported_claim_ids"]
            assert dim_data["muhasabah"]["supported_calc_ids"] == dim_data["supported_calc_ids"]


class TestDeterministicScoringLLMClientValidation:
    """Verify stub output passes real DimensionScore, NFF, and Muhasabah validators."""

    def test_pydantic_dimension_score(self) -> None:
        """Each dimension must parse into a valid DimensionScore."""
        client = DeterministicScoringLLMClient()
        prompt = _build_scoring_prompt([CLAIM_A, CLAIM_B], [CALC_A])
        raw = client.call(prompt, json_mode=True)
        parsed = json.loads(raw)

        for dim_name, dim_data in parsed["dimension_scores"].items():
            ds = DimensionScore(**dim_data)
            assert ds.dimension == ScoreDimension(dim_name)
            assert ds.score == pytest.approx(0.65)

    def test_nff_all_ids_grounded(self) -> None:
        """All claim/calc IDs must exist in a matching AnalysisContext."""
        client = DeterministicScoringLLMClient()
        claim_ids = [CLAIM_A, CLAIM_B]
        calc_ids = [CALC_A]
        prompt = _build_scoring_prompt(claim_ids, calc_ids)
        raw = client.call(prompt, json_mode=True)
        parsed = json.loads(raw)

        ctx = _make_scoring_context(claim_ids, calc_ids)
        for dim_name, dim_data in parsed["dimension_scores"].items():
            for cid in dim_data["supported_claim_ids"]:
                assert cid in ctx.claim_ids, f"{dim_name}: claim {cid} not in context"
            for cid in dim_data["supported_calc_ids"]:
                assert cid in ctx.calc_ids, f"{dim_name}: calc {cid} not in context"
            for cid in dim_data["muhasabah"]["supported_claim_ids"]:
                assert cid in ctx.claim_ids, f"{dim_name} muhasabah: claim {cid} not in context"
            for cid in dim_data["muhasabah"]["supported_calc_ids"]:
                assert cid in ctx.calc_ids, f"{dim_name} muhasabah: calc {cid} not in context"

    def test_muhasabah_validation(self) -> None:
        """Each dimension's muhasabah must pass the Muhasabah validator."""
        client = DeterministicScoringLLMClient()
        prompt = _build_scoring_prompt([CLAIM_A, CLAIM_B], [CALC_A])
        raw = client.call(prompt, json_mode=True)
        parsed = json.loads(raw)

        for dim_name, dim_data in parsed["dimension_scores"].items():
            result = validate_muhasabah(dim_data["muhasabah"])
            assert result.passed, (
                f"Muhasabah failed for {dim_name}: {[e.message for e in result.errors]}"
            )


class TestBuildScoringLLMClientWiring:
    """Verify _build_scoring_llm_client returns DeterministicScoringLLMClient."""

    def test_default_returns_deterministic_scoring_client(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("IDIS_DEBATE_BACKEND", raising=False)

        from idis.api.routes.runs import _build_scoring_llm_client

        client = _build_scoring_llm_client()
        assert isinstance(client, DeterministicScoringLLMClient)

    def test_explicit_deterministic_returns_scoring_client(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("IDIS_DEBATE_BACKEND", "deterministic")

        from idis.api.routes.runs import _build_scoring_llm_client

        client = _build_scoring_llm_client()
        assert isinstance(client, DeterministicScoringLLMClient)


class TestDeterministicScoringLLMClientFailClosed:
    """Verify fail-closed behavior when prompt is malformed."""

    def test_missing_context_payload_raises(self) -> None:
        client = DeterministicScoringLLMClient()
        with pytest.raises(ValueError, match="DETERMINISTIC_SCORING_CONTEXT_PARSE_FAILED"):
            client.call("no context here", json_mode=True)

    def test_invalid_json_in_payload_raises(self) -> None:
        client = DeterministicScoringLLMClient()
        prompt = "Score.\n\n---\n\nCONTEXT PAYLOAD:\n{not valid json"
        with pytest.raises(ValueError, match="DETERMINISTIC_SCORING_CONTEXT_PARSE_FAILED"):
            client.call(prompt, json_mode=True)

    def test_non_dict_payload_raises(self) -> None:
        client = DeterministicScoringLLMClient()
        prompt = "Score.\n\n---\n\nCONTEXT PAYLOAD:\n[1, 2, 3]"
        with pytest.raises(ValueError, match="DETERMINISTIC_SCORING_CONTEXT_PARSE_FAILED"):
            client.call(prompt, json_mode=True)
