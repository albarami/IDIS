"""Provider-agnostic LLM client interface + deterministic test stub.

LLMClient: Protocol for making LLM calls (provider-agnostic).
DeterministicLLMClient: Returns pre-built valid JSON for testing.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class LLMClient(Protocol):
    """Provider-agnostic interface for LLM calls."""

    def call(self, prompt: str, *, json_mode: bool = False) -> str:
        """Make an LLM call and return the raw response text.

        Args:
            prompt: The full prompt text to send.
            json_mode: If True, request JSON-formatted output.

        Returns:
            Raw response string from the LLM.
        """
        ...


class DeterministicLLMClient:
    """Deterministic LLM client for testing — returns valid JSON based on input.

    Parses the chunk content from the prompt and generates structured claims
    deterministically. No external calls are made.
    """

    def call(self, prompt: str, *, json_mode: bool = False) -> str:
        """Return deterministic claim JSON based on prompt content.

        Args:
            prompt: The full prompt text (includes chunk content).
            json_mode: Ignored; always returns JSON.

        Returns:
            JSON string containing an array of extracted claims.
        """
        claims = self._extract_from_prompt(prompt)
        return json.dumps(claims, sort_keys=True)

    def _extract_from_prompt(self, prompt: str) -> list[dict[str, Any]]:
        """Parse prompt content and generate deterministic claims.

        Args:
            prompt: Full prompt text.

        Returns:
            List of claim dicts matching the output schema.
        """
        content_marker = "Content:\n"
        content_start = prompt.find(content_marker)
        if content_start == -1:
            return []

        content = prompt[content_start + len(content_marker) :].strip()
        if not content:
            return []

        lines = [line.strip() for line in content.split("\n") if line.strip()]
        if not lines:
            return []

        claims: list[dict[str, Any]] = []
        for line in lines:
            claim_class = self._classify(line)
            claims.append(
                {
                    "claim_text": line,
                    "claim_class": claim_class,
                    "source_locator": {},
                    "confidence": 0.85,
                    "requires_review": False,
                }
            )

        return claims

    def _classify(self, text: str) -> str:
        """Classify text into a claim class deterministically.

        Args:
            text: Claim text to classify.

        Returns:
            Claim class string.
        """
        text_lower = text.lower()
        if any(kw in text_lower for kw in ["revenue", "arr", "mrr", "margin", "$", "funding"]):
            return "FINANCIAL"
        if any(kw in text_lower for kw in ["customer", "client", "user", "subscriber"]):
            return "TRACTION"
        if any(kw in text_lower for kw in ["tam", "sam", "som", "market size"]):
            return "MARKET_SIZE"
        if any(kw in text_lower for kw in ["competitor", "competition"]):
            return "COMPETITION"
        if any(kw in text_lower for kw in ["team", "employee", "founder", "ceo"]):
            return "TEAM"
        return "OTHER"


class DeterministicAnalysisLLMClient:
    """Deterministic LLM client for analysis agents — returns valid AgentReport JSON.

    Parses the CONTEXT PAYLOAD from the analysis prompt to extract real
    claim/calc IDs, then builds a fully-valid AgentReport dict that passes
    AgentReport Pydantic validation, No-Free-Facts, and Muhasabah gates.
    """

    _CONTEXT_MARKER = "CONTEXT PAYLOAD:\n"
    _CONSTRAINT_MARKER = "\n\nOUTPUT FORMAT CONSTRAINT:"
    _TIMESTAMP = "2026-01-01T00:00:00+00:00"

    def call(self, prompt: str, *, json_mode: bool = False) -> str:
        """Return deterministic AgentReport JSON based on prompt context.

        Args:
            prompt: The full prompt text (includes CONTEXT PAYLOAD JSON block).
            json_mode: Ignored; always returns JSON.

        Returns:
            JSON string containing a single AgentReport-shaped object.
        """
        claim_ids, calc_ids = self._extract_registry_ids(prompt)
        report = self._build_report(claim_ids, calc_ids)
        return json.dumps(report, sort_keys=True)

    def _extract_registry_ids(self, prompt: str) -> tuple[list[str], list[str]]:
        """Extract claim and calc IDs from the CONTEXT PAYLOAD in the prompt.

        Args:
            prompt: Full prompt containing a CONTEXT PAYLOAD JSON block.

        Returns:
            Tuple of (sorted claim_ids, sorted calc_ids).

        Raises:
            ValueError: If context payload cannot be parsed (fail-closed).
        """
        ctx_start = prompt.find(self._CONTEXT_MARKER)
        if ctx_start == -1:
            raise ValueError(
                "DETERMINISTIC_ANALYSIS_CONTEXT_PARSE_FAILED: "
                "no CONTEXT PAYLOAD marker found in prompt"
            )

        json_start = ctx_start + len(self._CONTEXT_MARKER)
        json_text = prompt[json_start:]

        constraint_pos = json_text.find(self._CONSTRAINT_MARKER)
        if constraint_pos != -1:
            json_text = json_text[:constraint_pos]

        json_text = json_text.strip()

        try:
            payload = json.loads(json_text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "DETERMINISTIC_ANALYSIS_CONTEXT_PARSE_FAILED: "
                f"invalid JSON in CONTEXT PAYLOAD: {exc}"
            ) from exc

        if not isinstance(payload, dict):
            raise ValueError(
                "DETERMINISTIC_ANALYSIS_CONTEXT_PARSE_FAILED: "
                f"expected dict, got {type(payload).__name__}"
            )

        claim_registry = payload.get("claim_registry", {})
        calc_registry = payload.get("calc_registry", {})

        claim_ids = sorted(claim_registry.keys()) if isinstance(claim_registry, dict) else []
        calc_ids = sorted(calc_registry.keys()) if isinstance(calc_registry, dict) else []

        return claim_ids, calc_ids

    def _build_report(self, claim_ids: list[str], calc_ids: list[str]) -> dict[str, Any]:
        """Build a valid AgentReport dict using the provided registry IDs.

        Args:
            claim_ids: Sorted claim IDs from the context payload.
            calc_ids: Sorted calc IDs from the context payload.

        Returns:
            Dict matching AgentReport schema, passing NFF and Muhasabah.
        """
        risk_evidence_claim = claim_ids[:1] if claim_ids else []
        risk_evidence_calc = calc_ids[:1] if calc_ids else []

        risks = []
        if risk_evidence_claim or risk_evidence_calc:
            risks.append(
                {
                    "risk_id": "det-risk-001",
                    "description": "Deterministic stub risk based on available evidence",
                    "severity": "MEDIUM",
                    "claim_ids": risk_evidence_claim,
                    "calc_ids": risk_evidence_calc,
                    "enrichment_ref_ids": [],
                }
            )

        return {
            "supported_claim_ids": list(claim_ids),
            "supported_calc_ids": list(calc_ids),
            "analysis_sections": {
                "nafs_check": {
                    "content": "Deterministic analysis of available claims and calculations.",
                    "insight_type": "factual",
                },
                "summary": {
                    "content": "Deterministic stub summary based on extracted evidence.",
                    "insight_type": "synthesis",
                },
            },
            "risks": risks,
            "questions_for_founder": [
                "Can you provide additional documentation for the key claims?",
            ],
            "confidence": 0.65,
            "confidence_justification": (
                "Deterministic stub: moderate confidence based on available evidence"
            ),
            "muhasabah": {
                "agent_id": "deterministic-stub",
                "output_id": "det-output-001",
                "supported_claim_ids": list(claim_ids),
                "supported_calc_ids": list(calc_ids),
                "evidence_summary": "Deterministic stub evidence from claim and calc registries",
                "counter_hypothesis": "Evidence may be incomplete or outdated",
                "falsifiability_tests": [
                    {
                        "test_description": "Verify claims against source documents",
                        "required_evidence": "Original source documents for each claim",
                        "pass_fail_rule": "Claims without traceable sources are ungrounded",
                    }
                ],
                "uncertainties": [
                    {
                        "uncertainty": "Stub output not validated against real LLM analysis",
                        "impact": "MEDIUM",
                        "mitigation": "Run with real LLM backend for production analysis",
                    }
                ],
                "failure_modes": ["incomplete_evidence", "stub_limitations"],
                "confidence": 0.65,
                "confidence_justification": (
                    "Deterministic stub: moderate confidence based on available evidence"
                ),
                "timestamp": self._TIMESTAMP,
                "is_subjective": False,
            },
            "enrichment_ref_ids": [],
        }


_SCORING_DIMENSIONS = (
    "MARKET_ATTRACTIVENESS",
    "TEAM_QUALITY",
    "PRODUCT_DEFENSIBILITY",
    "TRACTION_VELOCITY",
    "FUND_THESIS_FIT",
    "CAPITAL_EFFICIENCY",
    "SCALABILITY",
    "RISK_PROFILE",
)


class DeterministicScoringLLMClient:
    """Deterministic LLM client for scoring agents — returns valid scorecard JSON.

    Parses the CONTEXT PAYLOAD from the scoring prompt to extract real
    claim/calc IDs, then builds a fully-valid scoring response with all 8
    dimensions that passes DimensionScore Pydantic validation, NFF, and
    Muhasabah gates.
    """

    _CONTEXT_MARKER = "CONTEXT PAYLOAD:\n"
    _TIMESTAMP = "2026-01-01T00:00:00+00:00"

    def call(self, prompt: str, *, json_mode: bool = False) -> str:
        """Return deterministic scoring JSON based on prompt context.

        Args:
            prompt: The full scoring prompt (includes CONTEXT PAYLOAD JSON block).
            json_mode: Ignored; always returns JSON.

        Returns:
            JSON string containing a scorecard object with dimension_scores.
        """
        claim_ids, calc_ids = self._extract_registry_ids(prompt)
        response = self._build_scoring_response(claim_ids, calc_ids)
        return json.dumps(response, sort_keys=True)

    def _extract_registry_ids(self, prompt: str) -> tuple[list[str], list[str]]:
        """Extract claim and calc IDs from the CONTEXT PAYLOAD in the prompt.

        The scoring runner embeds the payload as:
            CONTEXT PAYLOAD:\\n{json}
        with claim_registry and calc_registry as dicts keyed by ID.

        Args:
            prompt: Full prompt containing a CONTEXT PAYLOAD JSON block.

        Returns:
            Tuple of (sorted claim_ids, sorted calc_ids).

        Raises:
            ValueError: If context payload cannot be parsed (fail-closed).
        """
        ctx_start = prompt.find(self._CONTEXT_MARKER)
        if ctx_start == -1:
            raise ValueError(
                "DETERMINISTIC_SCORING_CONTEXT_PARSE_FAILED: "
                "no CONTEXT PAYLOAD marker found in prompt"
            )

        json_text = prompt[ctx_start + len(self._CONTEXT_MARKER) :].strip()

        try:
            payload = json.loads(json_text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "DETERMINISTIC_SCORING_CONTEXT_PARSE_FAILED: "
                f"invalid JSON in CONTEXT PAYLOAD: {exc}"
            ) from exc

        if not isinstance(payload, dict):
            raise ValueError(
                "DETERMINISTIC_SCORING_CONTEXT_PARSE_FAILED: "
                f"expected dict, got {type(payload).__name__}"
            )

        claim_registry = payload.get("claim_registry", {})
        calc_registry = payload.get("calc_registry", {})

        claim_ids = sorted(claim_registry.keys()) if isinstance(claim_registry, dict) else []
        calc_ids = sorted(calc_registry.keys()) if isinstance(calc_registry, dict) else []

        return claim_ids, calc_ids

    def _build_dimension_score(
        self,
        dimension: str,
        claim_ids: list[str],
        calc_ids: list[str],
    ) -> dict[str, Any]:
        """Build a single valid DimensionScore dict.

        Args:
            dimension: ScoreDimension value (e.g. MARKET_ATTRACTIVENESS).
            claim_ids: Sorted claim IDs from context.
            calc_ids: Sorted calc IDs from context.

        Returns:
            Dict matching DimensionScore schema with valid Muhasabah.
        """
        return {
            "dimension": dimension,
            "score": 0.65,
            "rationale": (
                f"Deterministic scoring assessment for {dimension} based on available evidence."
            ),
            "supported_claim_ids": list(claim_ids),
            "supported_calc_ids": list(calc_ids),
            "enrichment_refs": [],
            "confidence": 0.60,
            "confidence_justification": (
                f"Deterministic stub: moderate confidence for {dimension}"
            ),
            "muhasabah": {
                "agent_id": "deterministic-scoring-stub",
                "output_id": f"det-score-{dimension.lower()}",
                "supported_claim_ids": list(claim_ids),
                "supported_calc_ids": list(calc_ids),
                "evidence_summary": f"Deterministic evidence for {dimension} from registries",
                "counter_hypothesis": f"Evidence for {dimension} may be incomplete",
                "falsifiability_tests": [
                    {
                        "test_description": f"Verify {dimension} claims against sources",
                        "required_evidence": "Original source documents",
                        "pass_fail_rule": "Claims without traceable sources are ungrounded",
                    }
                ],
                "uncertainties": [
                    {
                        "uncertainty": f"Stub scoring for {dimension} not LLM-validated",
                        "impact": "MEDIUM",
                        "mitigation": "Run with real LLM backend for production scoring",
                    }
                ],
                "failure_modes": ["incomplete_evidence", "stub_limitations"],
                "confidence": 0.60,
                "confidence_justification": (
                    f"Deterministic stub: moderate confidence for {dimension}"
                ),
                "timestamp": self._TIMESTAMP,
                "is_subjective": False,
            },
        }

    def _build_scoring_response(
        self,
        claim_ids: list[str],
        calc_ids: list[str],
    ) -> dict[str, Any]:
        """Build a complete scoring response with all 8 dimensions.

        Args:
            claim_ids: Sorted claim IDs from context.
            calc_ids: Sorted calc IDs from context.

        Returns:
            Dict with dimension_scores containing all 8 required dimensions.
        """
        dimension_scores: dict[str, dict[str, Any]] = {}
        for dim in _SCORING_DIMENSIONS:
            dimension_scores[dim] = self._build_dimension_score(dim, claim_ids, calc_ids)

        return {"dimension_scores": dimension_scores}
