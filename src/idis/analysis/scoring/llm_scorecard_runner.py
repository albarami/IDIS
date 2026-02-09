"""LLM-backed scorecard runner — Phase 9.

Loads the scoring prompt, constructs a deterministic context payload
from the AnalysisBundle + AnalysisContext + Stage, calls the LLM,
and parses the response into raw DimensionScore objects.

Fail-closed: invalid JSON, missing fields, or Pydantic failures raise ValueError.
Does not synthesize missing Muḥāsabah fields.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from idis.analysis.models import (
    AnalysisBundle,
    AnalysisContext,
    AnalysisMuhasabahRecord,
    EnrichmentRef,
)
from idis.analysis.scoring.models import DimensionScore, ScoreDimension, Stage
from idis.services.extraction.extractors.llm_client import LLMClient

logger = logging.getLogger(__name__)

_DEFAULT_PROMPT_PATH = (
    Path(__file__).resolve().parents[4] / "prompts" / "scoring_agent" / "1.0.0" / "prompt.md"
)


def _load_prompt(prompt_path: Path) -> str:
    """Load prompt text from disk. Fail-closed on missing file.

    Args:
        prompt_path: Path to the prompt file.

    Returns:
        Prompt text content.

    Raises:
        ValueError: If the prompt file does not exist.
    """
    if not prompt_path.exists():
        raise ValueError(f"Prompt file not found: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8")


def _build_context_payload(
    ctx: AnalysisContext,
    bundle: AnalysisBundle,
    stage: Stage,
) -> str:
    """Build a deterministic JSON context payload for the scoring LLM.

    Args:
        ctx: Analysis context with registries.
        bundle: Agent reports from specialist agents.
        stage: Deal stage for scoring.

    Returns:
        JSON string with stable key ordering.
    """
    claim_registry: dict[str, str] = {cid: cid for cid in sorted(ctx.claim_ids)}
    calc_registry: dict[str, str] = {cid: cid for cid in sorted(ctx.calc_ids)}
    enrichment_refs: dict[str, dict[str, str]] = {
        ref_id: {
            "ref_id": ref.ref_id,
            "provider_id": ref.provider_id,
            "source_id": ref.source_id,
        }
        for ref_id, ref in sorted(ctx.enrichment_refs.items())
    }
    agent_reports = [report.model_dump(mode="json") for report in bundle.reports]

    payload = {
        "stage": stage.value,
        "deal_metadata": {
            "deal_id": ctx.deal_id,
            "tenant_id": ctx.tenant_id,
            "run_id": ctx.run_id,
            "company_name": ctx.company_name,
            "stage": ctx.stage,
            "sector": ctx.sector,
        },
        "claim_registry": claim_registry,
        "calc_registry": calc_registry,
        "enrichment_refs": enrichment_refs,
        "agent_reports": agent_reports,
    }
    return json.dumps(payload, sort_keys=True, indent=2)


def _strip_markdown_fences(text: str) -> str:
    """Strip markdown code fences from LLM response text.

    Args:
        text: Raw LLM response text.

    Returns:
        Text with markdown fences removed, trimmed.
    """
    stripped = text.strip()
    fence_pattern = re.compile(
        r"```(?:json)?\s*\n?(.*?)\n?\s*```",
        re.DOTALL,
    )
    match = fence_pattern.search(stripped)
    if match:
        return match.group(1).strip()
    return stripped


def _parse_llm_response(raw: str) -> dict[str, Any]:
    """Parse LLM response as JSON. Fail-closed on invalid.

    Args:
        raw: Raw response string from LLM.

    Returns:
        Parsed dict.

    Raises:
        ValueError: If response is not valid JSON or not a dict.
    """
    cleaned = _strip_markdown_fences(raw)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM returned invalid JSON for scoring_agent: {exc}") from exc

    if not isinstance(parsed, dict):
        raise ValueError(
            f"LLM returned non-object JSON for scoring_agent: got {type(parsed).__name__}"
        )
    return parsed


def _build_enrichment_refs(raw_refs: list[dict[str, Any]]) -> list[EnrichmentRef]:
    """Build EnrichmentRef objects from raw dicts. Fail-closed.

    Args:
        raw_refs: List of enrichment ref dicts from LLM output.

    Returns:
        List of validated EnrichmentRef objects.

    Raises:
        ValueError: If Pydantic validation fails.
    """
    refs: list[EnrichmentRef] = []
    for raw in raw_refs:
        refs.append(
            EnrichmentRef(
                ref_id=raw.get("ref_id", ""),
                provider_id=raw.get("provider_id", ""),
                source_id=raw.get("source_id", ""),
            )
        )
    return refs


def _build_muhasabah(raw: dict[str, Any]) -> AnalysisMuhasabahRecord:
    """Build AnalysisMuhasabahRecord from raw dict. Fail-closed.

    Args:
        raw: Muhasabah dict from LLM output.

    Returns:
        Validated AnalysisMuhasabahRecord.

    Raises:
        ValueError: If Pydantic validation fails.
    """
    return AnalysisMuhasabahRecord(
        agent_id=raw.get("agent_id", ""),
        output_id=raw.get("output_id", ""),
        supported_claim_ids=raw.get("supported_claim_ids", []),
        supported_calc_ids=raw.get("supported_calc_ids", []),
        evidence_summary=raw.get("evidence_summary", ""),
        counter_hypothesis=raw.get("counter_hypothesis", ""),
        falsifiability_tests=raw.get("falsifiability_tests", []),
        uncertainties=raw.get("uncertainties", []),
        failure_modes=raw.get("failure_modes", []),
        confidence=raw.get("confidence", -1),
        confidence_justification=raw.get("confidence_justification", ""),
        timestamp=raw.get("timestamp", ""),
        is_subjective=raw.get("is_subjective", False),
    )


def _build_dimension_score(
    dimension_key: str,
    raw: dict[str, Any],
) -> DimensionScore:
    """Build a DimensionScore from raw LLM output dict. Fail-closed.

    Args:
        dimension_key: Dimension name from LLM output (must match ScoreDimension).
        raw: Raw dimension score dict.

    Returns:
        Validated DimensionScore.

    Raises:
        ValueError: On invalid dimension, missing fields, or Pydantic failure.
    """
    try:
        dimension = ScoreDimension(dimension_key)
    except ValueError as exc:
        raise ValueError(f"Unknown score dimension '{dimension_key}' in LLM output") from exc

    raw_muhasabah = raw.get("muhasabah")
    if not isinstance(raw_muhasabah, dict):
        raise ValueError(f"'muhasabah' must be an object for dimension {dimension_key}")

    raw_enrichment = raw.get("enrichment_refs", [])
    if not isinstance(raw_enrichment, list):
        raise ValueError(f"'enrichment_refs' must be a list for dimension {dimension_key}")

    enrichment_refs = _build_enrichment_refs(raw_enrichment)
    muhasabah = _build_muhasabah(raw_muhasabah)

    return DimensionScore(
        dimension=dimension,
        score=raw.get("score", -1),
        rationale=raw.get("rationale", ""),
        supported_claim_ids=raw.get("supported_claim_ids", []),
        supported_calc_ids=raw.get("supported_calc_ids", []),
        enrichment_refs=enrichment_refs,
        confidence=raw.get("confidence", -1),
        confidence_justification=raw.get("confidence_justification", ""),
        muhasabah=muhasabah,
    )


class LLMScorecardRunner:
    """Runs the scoring LLM to produce raw dimension scores.

    Loads prompt, builds context payload, calls LLM, parses response
    into DimensionScore objects. Fail-closed on any error.
    """

    def __init__(
        self,
        *,
        llm_client: LLMClient,
        prompt_path: Path | None = None,
    ) -> None:
        """Initialize the scorecard runner.

        Args:
            llm_client: Provider-agnostic LLM client (required).
            prompt_path: Override path to prompt file. Defaults to
                prompts/scoring_agent/1.0.0/prompt.md.
        """
        self._llm_client = llm_client
        self._prompt_path = prompt_path or _DEFAULT_PROMPT_PATH

    def run(
        self,
        ctx: AnalysisContext,
        bundle: AnalysisBundle,
        stage: Stage,
    ) -> dict[ScoreDimension, DimensionScore]:
        """Execute the scoring LLM and return raw dimension scores.

        Args:
            ctx: Analysis context with registries.
            bundle: Specialist agent reports.
            stage: Deal stage for scoring context.

        Returns:
            Dict of ScoreDimension -> DimensionScore for all 8 dimensions.

        Raises:
            ValueError: On prompt missing, invalid JSON, missing dimensions,
                or Pydantic validation failure.
        """
        prompt_text = _load_prompt(self._prompt_path)
        context_payload = _build_context_payload(ctx, bundle, stage)
        full_prompt = f"{prompt_text}\n\n---\n\nCONTEXT PAYLOAD:\n{context_payload}"

        raw_response = self._llm_client.call(full_prompt, json_mode=True)
        parsed = _parse_llm_response(raw_response)

        raw_scores = parsed.get("dimension_scores")
        if not isinstance(raw_scores, dict):
            raise ValueError(
                "'dimension_scores' must be an object in scoring LLM output, "
                f"got {type(raw_scores).__name__}"
            )

        dimension_scores: dict[ScoreDimension, DimensionScore] = {}
        for dim_key, dim_raw in raw_scores.items():
            if not isinstance(dim_raw, dict):
                raise ValueError(
                    f"Dimension score for '{dim_key}' must be an object, "
                    f"got {type(dim_raw).__name__}"
                )
            ds = _build_dimension_score(dim_key, dim_raw)
            dimension_scores[ds.dimension] = ds

        return dimension_scores
