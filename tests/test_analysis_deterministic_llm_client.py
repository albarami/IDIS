"""Tests for DeterministicAnalysisLLMClient: schema, NFF, Muhasabah, wiring."""

from __future__ import annotations

import json

import pytest

from idis.analysis.models import (
    AgentReport,
    AnalysisContext,
)
from idis.analysis.no_free_facts import AnalysisNoFreeFactsValidator
from idis.services.extraction.extractors.llm_client import (
    DeterministicAnalysisLLMClient,
)
from idis.validators.muhasabah import validate_muhasabah

CLAIM_A = "00000000-0000-0000-0000-a00000000001"
CLAIM_B = "00000000-0000-0000-0000-a00000000002"
CLAIM_C = "00000000-0000-0000-0000-a00000000003"
CALC_A = "00000000-0000-0000-0000-a00000000010"
CALC_B = "00000000-0000-0000-0000-a00000000020"


def _build_prompt(
    claim_ids: list[str],
    calc_ids: list[str],
) -> str:
    """Build a minimal analysis prompt with CONTEXT PAYLOAD containing registries."""
    claim_registry = {cid: cid for cid in sorted(claim_ids)}
    calc_registry = {cid: cid for cid in sorted(calc_ids)}
    payload = {
        "deal_metadata": {
            "deal_id": "deal-test-1",
            "tenant_id": "tenant-test-1",
            "run_id": "run-test-1",
            "company_name": "Test Corp",
            "stage": "Seed",
            "sector": "SaaS",
        },
        "claim_registry": claim_registry,
        "calc_registry": calc_registry,
        "enrichment_refs": {},
    }
    context_json = json.dumps(payload, sort_keys=True, indent=2)
    return (
        f"Analyze the deal.\n\n---\n\nCONTEXT PAYLOAD:\n{context_json}"
        f"\n\nOUTPUT FORMAT CONSTRAINT: Return a single JSON object."
    )


def _make_context(
    claim_ids: list[str],
    calc_ids: list[str],
) -> AnalysisContext:
    """Build AnalysisContext matching the prompt registries."""
    return AnalysisContext(
        deal_id="deal-test-1",
        tenant_id="tenant-test-1",
        run_id="run-test-1",
        claim_ids=frozenset(claim_ids),
        calc_ids=frozenset(calc_ids),
        enrichment_refs={},
        company_name="Test Corp",
        stage="Seed",
        sector="SaaS",
    )


class TestDeterministicAnalysisLLMClientOutput:
    """Verify stub returns a valid AgentReport-shaped JSON object."""

    def test_returns_dict_not_list(self) -> None:
        """Stub output must be a JSON object, not a list."""
        client = DeterministicAnalysisLLMClient()
        prompt = _build_prompt([CLAIM_A, CLAIM_B], [CALC_A])
        raw = client.call(prompt, json_mode=True)
        parsed = json.loads(raw)

        assert isinstance(parsed, dict)

    def test_contains_all_agent_report_keys(self) -> None:
        """Stub output must contain every required AgentReport key."""
        client = DeterministicAnalysisLLMClient()
        prompt = _build_prompt([CLAIM_A, CLAIM_B], [CALC_A])
        raw = client.call(prompt, json_mode=True)
        parsed = json.loads(raw)

        required_keys = {
            "supported_claim_ids",
            "supported_calc_ids",
            "analysis_sections",
            "risks",
            "questions_for_founder",
            "confidence",
            "confidence_justification",
            "muhasabah",
        }
        assert required_keys.issubset(parsed.keys())


class TestDeterministicAnalysisLLMClientRegistryIDs:
    """Verify IDs in the stub output are sourced from the prompt registries."""

    def test_claim_ids_from_prompt(self) -> None:
        """supported_claim_ids must be a subset of claim_registry IDs."""
        client = DeterministicAnalysisLLMClient()
        prompt = _build_prompt([CLAIM_A, CLAIM_B, CLAIM_C], [CALC_A])
        raw = client.call(prompt, json_mode=True)
        parsed = json.loads(raw)

        prompt_claim_ids = {CLAIM_A, CLAIM_B, CLAIM_C}
        assert set(parsed["supported_claim_ids"]).issubset(prompt_claim_ids)
        assert len(parsed["supported_claim_ids"]) > 0

    def test_calc_ids_from_prompt(self) -> None:
        """supported_calc_ids must be a subset of calc_registry IDs."""
        client = DeterministicAnalysisLLMClient()
        prompt = _build_prompt([CLAIM_A], [CALC_A, CALC_B])
        raw = client.call(prompt, json_mode=True)
        parsed = json.loads(raw)

        prompt_calc_ids = {CALC_A, CALC_B}
        assert set(parsed["supported_calc_ids"]).issubset(prompt_calc_ids)
        assert len(parsed["supported_calc_ids"]) > 0

    def test_muhasabah_claim_ids_match(self) -> None:
        """Muhasabah supported_claim_ids must match top-level supported_claim_ids."""
        client = DeterministicAnalysisLLMClient()
        prompt = _build_prompt([CLAIM_A, CLAIM_B], [CALC_A])
        raw = client.call(prompt, json_mode=True)
        parsed = json.loads(raw)

        assert parsed["muhasabah"]["supported_claim_ids"] == parsed["supported_claim_ids"]
        assert parsed["muhasabah"]["supported_calc_ids"] == parsed["supported_calc_ids"]


class TestDeterministicAnalysisLLMClientValidation:
    """Verify stub output passes real AgentReport, NFF, and Muhasabah validators."""

    def test_pydantic_agent_report(self) -> None:
        """Stub output must parse into a valid AgentReport via the real pipeline path."""
        client = DeterministicAnalysisLLMClient()
        prompt = _build_prompt([CLAIM_A, CLAIM_B], [CALC_A])
        raw = client.call(prompt, json_mode=True)
        parsed = json.loads(raw)

        parsed["agent_id"] = "test-agent-01"
        parsed["agent_type"] = "financial_agent"

        report = AgentReport(**parsed)
        assert report.confidence == 0.65
        assert len(report.supported_claim_ids) == 2

    def test_no_free_facts_validation(self) -> None:
        """Stub output must pass NFF validation with matching context."""
        client = DeterministicAnalysisLLMClient()
        claim_ids = [CLAIM_A, CLAIM_B]
        calc_ids = [CALC_A]
        prompt = _build_prompt(claim_ids, calc_ids)
        raw = client.call(prompt, json_mode=True)
        parsed = json.loads(raw)

        parsed["agent_id"] = "test-agent-01"
        parsed["agent_type"] = "financial_agent"
        report = AgentReport(**parsed)

        ctx = _make_context(claim_ids, calc_ids)
        nff = AnalysisNoFreeFactsValidator()
        result = nff.validate(report, ctx)

        assert result.passed, f"NFF failed: {[e.message for e in result.errors]}"

    def test_muhasabah_validation(self) -> None:
        """Stub output muhasabah must pass the Muhasabah validator."""
        client = DeterministicAnalysisLLMClient()
        prompt = _build_prompt([CLAIM_A, CLAIM_B], [CALC_A])
        raw = client.call(prompt, json_mode=True)
        parsed = json.loads(raw)

        result = validate_muhasabah(parsed["muhasabah"])

        assert result.passed, f"Muhasabah failed: {[e.message for e in result.errors]}"


class TestBuildAnalysisLLMClientWiring:
    """Verify _build_analysis_llm_client returns DeterministicAnalysisLLMClient."""

    def test_default_returns_deterministic_analysis_client(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When no real LLM backend is configured, the analysis client must be the analysis stub."""
        monkeypatch.delenv("IDIS_DEBATE_BACKEND", raising=False)

        from idis.api.routes.runs import _build_analysis_llm_client

        client = _build_analysis_llm_client()
        assert isinstance(client, DeterministicAnalysisLLMClient)

    def test_explicit_deterministic_returns_analysis_client(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Explicit 'deterministic' backend must also return the analysis stub."""
        monkeypatch.setenv("IDIS_DEBATE_BACKEND", "deterministic")

        from idis.api.routes.runs import _build_analysis_llm_client

        client = _build_analysis_llm_client()
        assert isinstance(client, DeterministicAnalysisLLMClient)


class TestDeterministicAnalysisLLMClientFailClosed:
    """Verify fail-closed behavior when prompt is malformed."""

    def test_missing_context_payload_raises(self) -> None:
        """Prompt without CONTEXT PAYLOAD marker must raise."""
        client = DeterministicAnalysisLLMClient()
        with pytest.raises(ValueError, match="DETERMINISTIC_ANALYSIS_CONTEXT_PARSE_FAILED"):
            client.call("no context here", json_mode=True)

    def test_invalid_json_in_payload_raises(self) -> None:
        """Malformed JSON in CONTEXT PAYLOAD must raise."""
        client = DeterministicAnalysisLLMClient()
        prompt = "Analyze.\n\n---\n\nCONTEXT PAYLOAD:\n{not valid json"
        with pytest.raises(ValueError, match="DETERMINISTIC_ANALYSIS_CONTEXT_PARSE_FAILED"):
            client.call(prompt, json_mode=True)
