"""Shared LLM-backed specialist agent base — Phase 8.B.

Fail-closed base for specialist analysis agents that:
1. Loads prompt from disk
2. Constructs a deterministic context payload
3. Calls LLMClient.call(prompt, json_mode=True)
4. Parses JSON (fail-closed on invalid)
5. Builds AgentReport via Pydantic validation (fail-closed)
6. Returns it (no mutation / no synthesis)
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from idis.analysis.models import (
    AgentReport,
    AnalysisContext,
    AnalysisMuhasabahRecord,
    Risk,
    RiskSeverity,
)
from idis.services.extraction.extractors.llm_client import LLMClient

logger = logging.getLogger(__name__)

_VALID_SEVERITIES = frozenset(item.value for item in RiskSeverity)

_JSON_OBJECT_CONSTRAINT = (
    "\n\nOUTPUT FORMAT CONSTRAINT: "
    "Return a single JSON object at the top level. "
    "Do not return a JSON array. "
    "If you would otherwise return an array, unwrap it and return the single object."
)


def _load_prompt(prompt_path: Path) -> str:
    """Load prompt text from disk. Fail-closed on missing file.

    Args:
        prompt_path: Absolute or relative path to the prompt file.

    Returns:
        Prompt text content.

    Raises:
        ValueError: If the prompt file does not exist.
    """
    if not prompt_path.exists():
        raise ValueError(f"Prompt file not found: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8")


def _build_context_payload(ctx: AnalysisContext) -> str:
    """Build a deterministic JSON context payload for the LLM.

    Sorting ensures identical output for identical inputs.

    Args:
        ctx: Analysis context with registries and deal metadata.

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

    payload = {
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


def _parse_llm_response(raw: str, agent_type: str) -> dict[str, Any]:
    """Parse LLM response as JSON. Fail-closed on invalid.

    Accepts a single-element list wrapping a dict ([{...}] -> {...}).
    Rejects all other non-object shapes.

    Args:
        raw: Raw response string from LLM.
        agent_type: Agent type for error context.

    Returns:
        Parsed dict.

    Raises:
        ValueError: If response is not valid JSON or not a dict.
    """
    cleaned = _strip_markdown_fences(raw)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM returned invalid JSON for {agent_type}: {exc}") from exc

    if isinstance(parsed, dict):
        return parsed

    if isinstance(parsed, list):
        if len(parsed) == 1 and isinstance(parsed[0], dict):
            return parsed[0]
        raise ValueError(
            f"LLM returned non-object JSON for {agent_type}: "
            f"got list (len={len(parsed)}). "
            f"Only single-element list wrapping is accepted."
        )

    raise ValueError(f"LLM returned non-object JSON for {agent_type}: got {type(parsed).__name__}")


def _build_risks(raw_risks: list[dict[str, Any]]) -> list[Risk]:
    """Build Risk objects from raw dicts. Fail-closed on invalid.

    Args:
        raw_risks: List of risk dicts from LLM output.

    Returns:
        List of validated Risk objects.

    Raises:
        ValueError: If any risk fails Pydantic validation.
    """
    risks: list[Risk] = []
    for raw in raw_risks:
        severity_val = raw.get("severity", "")
        if isinstance(severity_val, str) and severity_val.upper() in _VALID_SEVERITIES:
            severity_val = severity_val.upper()
        risks.append(
            Risk(
                risk_id=raw.get("risk_id", ""),
                description=raw.get("description", ""),
                severity=RiskSeverity(severity_val),
                claim_ids=raw.get("claim_ids", []),
                calc_ids=raw.get("calc_ids", []),
                enrichment_ref_ids=raw.get("enrichment_ref_ids", []),
            )
        )
    return risks


def _build_muhasabah(
    raw: dict[str, Any],
) -> AnalysisMuhasabahRecord:
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


def run_specialist_agent(
    *,
    agent_id: str,
    agent_type: str,
    llm_client: LLMClient,
    prompt_path: Path,
    ctx: AnalysisContext,
) -> AgentReport:
    """Execute a specialist agent: load prompt, call LLM, parse, validate.

    This is the shared execution logic for all LLM-backed specialist agents.
    Fail-closed: any parsing or validation error raises immediately.

    Args:
        agent_id: Unique agent identifier.
        agent_type: Agent type string.
        llm_client: Provider-agnostic LLM client.
        prompt_path: Path to the prompt file on disk.
        ctx: Analysis context with registries.

    Returns:
        Validated AgentReport.

    Raises:
        ValueError: On prompt file missing, invalid JSON, or Pydantic failure.
    """
    prompt_text = _load_prompt(prompt_path)
    context_payload = _build_context_payload(ctx)
    full_prompt = (
        f"{prompt_text}\n\n---\n\nCONTEXT PAYLOAD:\n{context_payload}{_JSON_OBJECT_CONSTRAINT}"
    )

    raw_response = llm_client.call(full_prompt, json_mode=True)
    parsed = _parse_llm_response(raw_response, agent_type)

    raw_risks = parsed.get("risks", [])
    if not isinstance(raw_risks, list):
        raise ValueError(f"'risks' must be a list for {agent_type}, got {type(raw_risks).__name__}")

    raw_muhasabah = parsed.get("muhasabah")
    if not isinstance(raw_muhasabah, dict):
        raise ValueError(f"'muhasabah' must be an object for {agent_type}")

    risks = _build_risks(raw_risks)
    muhasabah = _build_muhasabah(raw_muhasabah)

    return AgentReport(
        agent_id=agent_id,
        agent_type=agent_type,
        supported_claim_ids=parsed.get("supported_claim_ids", []),
        supported_calc_ids=parsed.get("supported_calc_ids", []),
        analysis_sections=parsed.get("analysis_sections", {}),
        risks=risks,
        questions_for_founder=parsed.get("questions_for_founder", []),
        confidence=parsed.get("confidence", -1),
        confidence_justification=parsed.get("confidence_justification", ""),
        muhasabah=muhasabah,
        enrichment_ref_ids=parsed.get("enrichment_ref_ids", []),
    )
