"""LLM-backed debate role runner — v6.3

Implements the RoleRunnerProtocol by calling an LLMClient with a role-specific
system prompt, parsing the response into AgentOutput with a valid MuhasabahRecord,
and validating through the Muhasabah validator before returning.

Fail-closed: invalid JSON or failed validation raises ValueError.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid5

from idis.debate.roles.base import RoleResult, RoleRunner
from idis.models.debate import (
    AgentOutput,
    DebateMessage,
    DebateRole,
    MuhasabahRecord,
)
from idis.services.extraction.extractors.llm_client import LLMClient
from idis.validators.muhasabah import validate_muhasabah

if TYPE_CHECKING:
    from idis.models.debate import DebateState

logger = logging.getLogger(__name__)

_IDIS_NAMESPACE = UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")

MAX_CLAIM_TEXT_LENGTH = 200

_GRADE_SORT_ORDER: dict[str, int] = {"D": 0, "C": 1, "B": 2, "A": 3, "": 4}


@dataclass
class DebateContext:
    """Rich context injected into LLM debate prompts.

    Assembled by the pipeline from extraction + grading + calc results.
    Gives debate agents actual evidence to reason over.
    """

    deal_name: str
    deal_sector: str
    deal_stage: str
    deal_summary: str
    claims: list[dict]
    calc_results: list[dict]
    conflicts: list[dict]


def _deterministic_id(prefix: str, *, seed: str) -> str:
    """Generate a deterministic ID from a seed string.

    Args:
        prefix: ID prefix (e.g., "msg", "out", "muh").
        seed: Canonical seed string for uuid5.

    Returns:
        Deterministic ID: "{prefix}-{uuid5_hex[:12]}".
    """
    return f"{prefix}-{uuid5(_IDIS_NAMESPACE, seed).hex[:12]}"


def _deterministic_timestamp(round_number: int, step: int = 0) -> datetime:
    """Generate a deterministic logical timestamp.

    Args:
        round_number: Current round number (1-5).
        step: Step counter within round.

    Returns:
        Deterministic datetime in UTC.
    """
    return datetime(2026, 1, 1, round_number - 1, step, 0, tzinfo=UTC)


def _position_hash(role: str, round_number: int, content_summary: str) -> str:
    """Generate a deterministic position hash.

    Args:
        role: Role name.
        round_number: Current round.
        content_summary: Summary of position content.

    Returns:
        Hex digest (first 16 chars).
    """
    canonical = f"{role}|{round_number}|{content_summary}"
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


class LLMRoleRunner(RoleRunner):
    """LLM-backed role runner for debate orchestration.

    Calls an LLMClient with a role-specific system prompt, parses the JSON
    response into AgentOutput with MuhasabahRecord, validates through the
    Muhasabah validator, and returns a RoleResult.

    Fail-closed: invalid JSON or failed Muhasabah validation raises ValueError.
    """

    def __init__(
        self,
        *,
        role: DebateRole,
        llm_client: LLMClient,
        system_prompt: str,
        agent_id: str | None = None,
        context: DebateContext | None = None,
    ) -> None:
        """Initialize the LLM role runner.

        Args:
            role: The debate role this runner implements.
            llm_client: Provider-agnostic LLM client.
            system_prompt: Role-specific system prompt text.
            agent_id: Optional agent identifier override.
            context: Optional rich debate context from pipeline results.
        """
        resolved_agent_id = agent_id or f"{role.value}-llm"
        super().__init__(role=role, agent_id=resolved_agent_id)
        self._llm_client = llm_client
        self._system_prompt = system_prompt
        self._context = context

    def run(self, state: DebateState) -> RoleResult:
        """Execute the role by calling the LLM and validating output.

        Args:
            state: Current debate state (read-only).

        Returns:
            RoleResult with validated messages and outputs.

        Raises:
            ValueError: If LLM returns invalid JSON or output fails
                Muhasabah validation (fail-closed).
        """
        role_name = self.role.value
        seed_base = f"{state.tenant_id}|{state.deal_id}|{role_name}|{state.round_number}"
        timestamp = _deterministic_timestamp(state.round_number, step=0)

        message_id = _deterministic_id("msg", seed=f"{seed_base}|msg")
        output_id = _deterministic_id("out", seed=f"{seed_base}|out")
        record_id = _deterministic_id("muh", seed=f"{seed_base}|muh")

        user_prompt = self._build_user_prompt(state)
        full_prompt = f"{self._system_prompt}\n\n---\n\n{user_prompt}"

        raw_response = self._llm_client.call(full_prompt, json_mode=True)

        parsed = self._parse_response(raw_response)

        content = parsed.get("content", {})
        output_type = parsed.get("output_type", role_name)

        muhasabah_raw = self._extract_muhasabah(parsed)

        muhasabah_raw["record_id"] = record_id
        muhasabah_raw["agent_id"] = self.agent_id
        muhasabah_raw["output_id"] = output_id
        muhasabah_raw["timestamp"] = timestamp.isoformat()

        try:
            muhasabah = MuhasabahRecord(**muhasabah_raw)
        except Exception as exc:
            raise ValueError(
                f"Muhasabah record construction failed for {role_name}: {exc}"
            ) from exc

        self._validate_muhasabah_record(muhasabah, output_id)

        claim_refs = list(muhasabah.supported_claim_ids)
        calc_refs = list(muhasabah.supported_calc_ids)

        content_summary = f"llm|claims:{len(claim_refs)}|calcs:{len(calc_refs)}"
        pos_hash = _position_hash(role_name, state.round_number, content_summary)

        if isinstance(content, dict):
            content["position_hash"] = pos_hash
            content["is_subjective"] = muhasabah.is_subjective
        else:
            content = {
                "raw": str(content),
                "position_hash": pos_hash,
                "is_subjective": muhasabah.is_subjective,
            }

        output = AgentOutput(
            output_id=output_id,
            agent_id=self.agent_id,
            role=self.role,
            output_type=output_type,
            content=content,
            muhasabah=muhasabah,
            round_number=state.round_number,
            timestamp=timestamp,
        )

        narrative = ""
        if isinstance(content, dict):
            narrative = str(content.get("text") or content.get("narrative") or "")
        message = DebateMessage(
            message_id=message_id,
            role=self.role,
            agent_id=self.agent_id,
            content=str(narrative)[:500],
            claim_refs=sorted(claim_refs),
            calc_refs=sorted(calc_refs),
            round_number=state.round_number,
            timestamp=timestamp,
        )

        return RoleResult(
            messages=[message],
            outputs=[output],
            position_hash=pos_hash,
        )

    def _build_user_prompt(self, state: DebateState) -> str:
        """Build the user prompt from debate state and optional context.

        Args:
            state: Current debate state.

        Returns:
            Formatted user prompt string with deal context and debate state.
        """
        parts: list[str] = []

        if self._context is not None:
            parts.append(self._serialize_context())

        parts.append(self._serialize_debate_state(state))

        return "\n\n".join(parts)

    def _serialize_context(self) -> str:
        """Serialize DebateContext into a readable text block for the LLM.

        Returns:
            Markdown-formatted context string with deal overview,
            claim registry table, conflicts, and calc results.
        """
        ctx = self._context
        if ctx is None:
            return ""

        lines: list[str] = [
            "## DEAL OVERVIEW",
            f"Company: {ctx.deal_name}",
            f"Sector: {ctx.deal_sector}",
            f"Stage: {ctx.deal_stage}",
        ]
        if ctx.deal_summary:
            lines.append(f"Summary: {ctx.deal_summary}")

        lines.append("")
        lines.append(f"## CLAIM REGISTRY ({len(ctx.claims)} claims extracted)")
        if ctx.claims:
            lines.append("| claim_id | claim_text | class | sanad_grade | source | confidence |")
            lines.append("|----------|-----------|-------|-------------|--------|------------|")
            sorted_claims = sorted(
                ctx.claims,
                key=lambda c: _GRADE_SORT_ORDER.get(str(c.get("sanad_grade", "")), 4),
            )
            for claim in sorted_claims:
                cid = claim.get("claim_id", "")
                text = str(claim.get("claim_text", ""))[:MAX_CLAIM_TEXT_LENGTH]
                cls = claim.get("claim_class", "")
                grade = claim.get("sanad_grade", "")
                source = claim.get("source_doc", "")
                conf = claim.get("confidence", 0.0)
                lines.append(f"| {cid} | {text} | {cls} | {grade} | {source} | {conf} |")

        lines.append("")
        lines.append(f"## CONFLICTS DETECTED ({len(ctx.conflicts)})")
        for conflict in ctx.conflicts:
            a = conflict.get("claim_id_a", "")
            b = conflict.get("claim_id_b", "")
            desc = conflict.get("description", "")
            lines.append(f"- {a} vs {b}: {desc}")

        lines.append("")
        lines.append(f"## CALC RESULTS ({len(ctx.calc_results)})")
        if ctx.calc_results:
            for calc in ctx.calc_results:
                cid = calc.get("calc_id", "")
                name = calc.get("calc_name", "")
                val = calc.get("result_value", "")
                lines.append(f"- {cid}: {name} = {val}")
        else:
            lines.append("(no deterministic calculations produced for this run)")

        return "\n".join(lines)

    def _serialize_debate_state(self, state: DebateState) -> str:
        """Serialize DebateState into a readable text block for the LLM.

        Args:
            state: Current debate state.

        Returns:
            Markdown-formatted debate state string.
        """
        prior_messages: list[str] = []
        for msg in state.messages[-10:]:
            prior_messages.append(f"[{msg.role.value}] {msg.content}")

        lines: list[str] = [
            "## DEBATE STATE",
            f"Round: {state.round_number}",
            f"Deal ID: {state.deal_id}",
            f"Claim Registry Ref: {state.claim_registry_ref}",
            f"Sanad Graph Ref: {state.sanad_graph_ref}",
            f"Agent Outputs So Far: {len(state.agent_outputs)}",
        ]

        if prior_messages:
            lines.append(
                f"\nRecent Messages (last {len(prior_messages)} of {len(state.messages)} total):"
            )
            for msg_line in prior_messages:
                lines.append(f"  {msg_line}")
        else:
            lines.append("\nPrior Messages: (none \u2014 this is the opening round)")

        if state.open_questions[:5]:
            lines.append(f"\nOpen Questions ({len(state.open_questions)}):")
            for q in state.open_questions[:5]:
                lines.append(f"  - {q}")

        return "\n".join(lines)

    def _parse_response(self, raw: str) -> dict[str, Any]:
        """Parse LLM response as JSON, fail-closed on invalid.

        Strips markdown code fences before parsing, since some models
        (especially Opus) wrap JSON in ```json ... ``` blocks.

        Args:
            raw: Raw response string.

        Returns:
            Parsed dict.

        Raises:
            ValueError: If response is not valid JSON or not a dict.
        """
        cleaned = self._strip_markdown_fences(raw)
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise ValueError(f"LLM returned invalid JSON for {self.role.value}: {exc}") from exc

        if not isinstance(parsed, dict):
            raise ValueError(
                f"LLM returned non-object JSON for {self.role.value}: got {type(parsed).__name__}"
            )

        return parsed

    @staticmethod
    def _strip_markdown_fences(text: str) -> str:
        """Strip markdown code fences from LLM response text.

        Some models (especially Opus) wrap JSON in ```json ... ``` blocks
        or add prose before/after the JSON. This method extracts the JSON
        content from within fences, or returns the original text if no
        fences are found.

        Args:
            text: Raw LLM response text.

        Returns:
            Text with markdown fences removed, trimmed.
        """
        import re

        stripped = text.strip()
        fence_pattern = re.compile(
            r"```(?:json)?\s*\n?(.*?)\n?\s*```",
            re.DOTALL,
        )
        match = fence_pattern.search(stripped)
        if match:
            return match.group(1).strip()
        return stripped

    def _extract_muhasabah(self, parsed: dict[str, Any]) -> dict[str, Any]:
        """Extract and validate the muhasabah object from parsed LLM response.

        Fail-closed checks:
        1. Missing "muhasabah" key → ValueError
        2. "muhasabah" not a dict → ValueError
        3. Missing required fields (supported_claim_ids, confidence,
           uncertainties) → ValueError

        Args:
            parsed: Parsed LLM response dict.

        Returns:
            The muhasabah dict (validated for required keys).

        Raises:
            ValueError: If muhasabah is missing, not a dict, or incomplete.
        """
        if "muhasabah" not in parsed:
            raise ValueError("LLM response missing muhasabah record")

        muhasabah_raw = parsed["muhasabah"]

        if not isinstance(muhasabah_raw, dict):
            raise ValueError("LLM response muhasabah must be an object")

        required_keys = {"supported_claim_ids", "confidence", "uncertainties"}
        missing = required_keys - muhasabah_raw.keys()
        if missing:
            raise ValueError(f"muhasabah missing required fields: {sorted(missing)}")

        return dict(muhasabah_raw)

    def _validate_muhasabah_record(
        self,
        record: MuhasabahRecord,
        output_id: str,
    ) -> None:
        """Validate MuhasabahRecord through the Muhasabah validator.

        Args:
            record: The record to validate.
            output_id: Output ID for error context.

        Raises:
            ValueError: If validation fails (fail-closed).
        """
        record_dict = record.model_dump()
        if record_dict.get("timestamp") and hasattr(record_dict["timestamp"], "isoformat"):
            record_dict["timestamp"] = record_dict["timestamp"].isoformat()

        result = validate_muhasabah(record_dict)
        if not result.passed:
            error_details = [f"{e.code}: {e.message}" for e in result.errors]
            raise ValueError(
                f"Muhasabah validation failed for {self.role.value} "
                f"output {output_id}: {error_details}"
            )
