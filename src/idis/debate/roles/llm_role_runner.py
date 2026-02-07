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
    ) -> None:
        """Initialize the LLM role runner.

        Args:
            role: The debate role this runner implements.
            llm_client: Provider-agnostic LLM client.
            system_prompt: Role-specific system prompt text.
            agent_id: Optional agent identifier override.
        """
        resolved_agent_id = agent_id or f"{role.value}-llm"
        super().__init__(role=role, agent_id=resolved_agent_id)
        self._llm_client = llm_client
        self._system_prompt = system_prompt

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

        narrative = content.get("narrative", "") if isinstance(content, dict) else ""
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
        """Build the user prompt from debate state.

        Args:
            state: Current debate state.

        Returns:
            Formatted user prompt string.
        """
        prior_messages = []
        for msg in state.messages[-10:]:
            prior_messages.append(f"[{msg.role.value}] {msg.content}")

        return json.dumps(
            {
                "round_number": state.round_number,
                "tenant_id": state.tenant_id,
                "deal_id": state.deal_id,
                "claim_registry_ref": state.claim_registry_ref,
                "sanad_graph_ref": state.sanad_graph_ref,
                "prior_messages_count": len(state.messages),
                "recent_messages": prior_messages,
                "agent_outputs_count": len(state.agent_outputs),
                "open_questions": state.open_questions[:5],
            },
            indent=2,
        )

    def _parse_response(self, raw: str) -> dict[str, Any]:
        """Parse LLM response as JSON, fail-closed on invalid.

        Args:
            raw: Raw response string.

        Returns:
            Parsed dict.

        Raises:
            ValueError: If response is not valid JSON or not a dict.
        """
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"LLM returned invalid JSON for {self.role.value}: {exc}") from exc

        if not isinstance(parsed, dict):
            raise ValueError(
                f"LLM returned non-object JSON for {self.role.value}: got {type(parsed).__name__}"
            )

        return parsed

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
