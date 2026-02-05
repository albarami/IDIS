"""Muḥāsabah Gate — v6.3 Phase 5.2

HARD GATE: Enforces MuḥāsabahRecord + No-Free-Facts at the debate output boundary.

Every debate/agent output is blocked (fail-closed) unless it includes:
1. A valid MuḥāsabahRecord (per MuhasabahValidator rules)
2. Passes No-Free-Facts validation at the output boundary

Gate semantics:
- Missing muhasabah record → REJECT
- Validator error/exception → REJECT (no uncaught exceptions)
- No randomness: no uuid4/datetime.utcnow in gate code paths

This closes the Phase 5 trust loop: debate orchestration (5.1) is complete,
now outputs are auditably self-checking and IC-safe.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from idis.validators.muhasabah import MuhasabahValidator
from idis.validators.no_free_facts import NoFreeFactsValidator
from idis.validators.schema_validator import ValidationError, ValidationResult

if TYPE_CHECKING:
    from idis.models.debate import AgentOutput


class GateRejectionReason(StrEnum):
    """Reasons for gate rejection."""

    MISSING_MUHASABAH = "MISSING_MUHASABAH"
    INVALID_MUHASABAH = "INVALID_MUHASABAH"
    NO_FREE_FACTS_VIOLATION = "NO_FREE_FACTS_VIOLATION"
    VALIDATION_EXCEPTION = "VALIDATION_EXCEPTION"
    MISSING_OUTPUT = "MISSING_OUTPUT"


@dataclass(frozen=True)
class GateDecision:
    """Result of Muḥāsabah gate evaluation.

    Fail-closed: allowed=False by default unless explicitly passed.
    """

    allowed: bool
    reason: GateRejectionReason | None = None
    errors: list[ValidationError] = field(default_factory=list)
    warnings: list[ValidationError] = field(default_factory=list)

    @classmethod
    def allow(cls, warnings: list[ValidationError] | None = None) -> GateDecision:
        """Create an allowed decision."""
        return cls(allowed=True, warnings=warnings or [])

    @classmethod
    def reject(
        cls,
        reason: GateRejectionReason,
        errors: list[ValidationError] | None = None,
    ) -> GateDecision:
        """Create a rejection decision."""
        return cls(allowed=False, reason=reason, errors=errors or [])


class MuhasabahGateError(Exception):
    """Raised when the Muḥāsabah gate blocks an output.

    This is a typed exception for fail-closed gate behavior.
    The orchestrator should catch this and halt the run deterministically.
    """

    def __init__(
        self,
        message: str,
        decision: GateDecision,
        output_id: str | None = None,
        agent_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.decision = decision
        self.output_id = output_id
        self.agent_id = agent_id


class MuhasabahGate:
    """Enforces Muḥāsabah + No-Free-Facts at the debate output boundary.

    FAIL-CLOSED semantics:
    - Missing muhasabah record → reject
    - Any validator error → reject with structured error
    - Any exception → reject (no uncaught exceptions)

    Determinism:
    - No uuid4/datetime.utcnow
    - All IDs/timestamps derived from context if needed
    """

    def __init__(self) -> None:
        """Initialize the gate with validators."""
        self._muhasabah_validator = MuhasabahValidator()
        self._no_free_facts_validator = NoFreeFactsValidator()

    def evaluate(
        self,
        output: AgentOutput | None,
        *,
        context: dict[str, Any] | None = None,
    ) -> GateDecision:
        """Evaluate an agent output against the Muḥāsabah gate.

        Args:
            output: The agent output to validate.
            context: Optional context (deal_id, debate_run_id, etc.) for error reporting.

        Returns:
            GateDecision with allowed status and any errors.

        FAIL-CLOSED: Never returns allowed=True if validation fails or errors.
        """
        # Fail closed on missing output
        if output is None:
            return GateDecision.reject(
                GateRejectionReason.MISSING_OUTPUT,
                errors=[
                    ValidationError(
                        code="MISSING_OUTPUT",
                        message="Output is None - cannot validate",
                        path="$",
                    )
                ],
            )

        try:
            # Step 1: Validate MuhasabahRecord exists and is valid
            muhasabah_decision = self._validate_muhasabah(output)
            if not muhasabah_decision.allowed:
                return muhasabah_decision

            # Step 2: Validate No-Free-Facts at output boundary
            nff_decision = self._validate_no_free_facts(output)
            if not nff_decision.allowed:
                return nff_decision

            # Both validations passed
            all_warnings = muhasabah_decision.warnings + nff_decision.warnings
            return GateDecision.allow(warnings=all_warnings if all_warnings else None)

        except Exception as e:
            # Fail closed on any unexpected exception
            return GateDecision.reject(
                GateRejectionReason.VALIDATION_EXCEPTION,
                errors=[
                    ValidationError(
                        code="VALIDATION_EXCEPTION",
                        message=f"Unexpected validation error: {e}",
                        path="$",
                    )
                ],
            )

    def _validate_muhasabah(self, output: AgentOutput) -> GateDecision:
        """Validate the MuhasabahRecord on the output.

        Returns:
            GateDecision - reject if missing or invalid.
        """
        # Check muhasabah record exists
        if not hasattr(output, "muhasabah") or output.muhasabah is None:
            return GateDecision.reject(
                GateRejectionReason.MISSING_MUHASABAH,
                errors=[
                    ValidationError(
                        code="MISSING_MUHASABAH",
                        message="Agent output missing required muhasabah record",
                        path="$.muhasabah",
                    )
                ],
            )

        # Convert muhasabah to dict for validator
        muhasabah = output.muhasabah
        record_dict = self._muhasabah_to_dict(muhasabah, output)

        # Run validator
        result = self._muhasabah_validator.validate(record_dict)

        if not result.passed:
            return GateDecision.reject(
                GateRejectionReason.INVALID_MUHASABAH,
                errors=result.errors,
            )

        return GateDecision.allow(warnings=result.warnings if result.warnings else None)

    def _validate_no_free_facts(self, output: AgentOutput) -> GateDecision:
        """Validate No-Free-Facts at the output boundary.

        Returns:
            GateDecision - reject if unreferenced factual assertions found.
        """
        # Convert output content to deliverable-like structure for validation
        deliverable_dict = self._output_to_deliverable_dict(output)

        # Run No-Free-Facts validator
        result = self._no_free_facts_validator.validate(deliverable_dict)

        if not result.passed:
            return GateDecision.reject(
                GateRejectionReason.NO_FREE_FACTS_VIOLATION,
                errors=result.errors,
            )

        return GateDecision.allow(warnings=result.warnings if result.warnings else None)

    def _muhasabah_to_dict(self, muhasabah: Any, output: AgentOutput) -> dict[str, Any]:
        """Convert MuhasabahRecord to dict for validator.

        Handles both Pydantic model and dict-like objects.
        """
        data: dict[str, Any]
        if hasattr(muhasabah, "model_dump"):
            # Pydantic v2 model
            data = dict(muhasabah.model_dump())
        elif hasattr(muhasabah, "dict"):
            # Pydantic v1 model
            data = dict(muhasabah.dict())
        elif isinstance(muhasabah, dict):
            data = dict(muhasabah)
        else:
            # Try to access attributes directly
            data = {
                "agent_id": getattr(muhasabah, "agent_id", None),
                "output_id": getattr(muhasabah, "output_id", None),
                "supported_claim_ids": getattr(muhasabah, "supported_claim_ids", []),
                "supported_calc_ids": getattr(muhasabah, "supported_calc_ids", []),
                "falsifiability_tests": getattr(muhasabah, "falsifiability_tests", []),
                "uncertainties": getattr(muhasabah, "uncertainties", []),
                "confidence": getattr(muhasabah, "confidence", None),
                "failure_modes": getattr(muhasabah, "failure_modes", []),
                "timestamp": getattr(muhasabah, "timestamp", None),
                "is_subjective": getattr(muhasabah, "is_subjective", False),
            }

        # Ensure output_id is set (may come from parent output)
        if not data.get("output_id"):
            data["output_id"] = output.output_id

        # Ensure agent_id is set
        if not data.get("agent_id"):
            data["agent_id"] = output.agent_id

        # Convert timestamp to string if datetime
        timestamp = data.get("timestamp")
        if timestamp is not None and hasattr(timestamp, "isoformat"):
            data["timestamp"] = timestamp.isoformat()

        # Handle is_subjective from content if not present
        if "is_subjective" not in data:
            data["is_subjective"] = output.content.get("is_subjective", False)

        # Check for recommendation/decision in content
        content = output.content or {}
        if "recommendation" in content or "decision" in content:
            data["recommendation"] = True

        return data

    def _output_to_deliverable_dict(self, output: AgentOutput) -> dict[str, Any]:
        """Convert AgentOutput to deliverable-like dict for No-Free-Facts validation.

        The No-Free-Facts validator expects a structure with sections.
        We convert the agent output content appropriately.
        """
        content = output.content or {}
        muhasabah = output.muhasabah

        # Get claim/calc refs from muhasabah
        claim_ids: list[str] = []
        calc_ids: list[str] = []
        if muhasabah:
            claim_ids = getattr(muhasabah, "supported_claim_ids", []) or []
            calc_ids = getattr(muhasabah, "supported_calc_ids", []) or []

        # Check if content has sections already
        if "sections" in content and isinstance(content["sections"], list):
            # Already has sections - inject refs into each section
            sections = []
            for section in content["sections"]:
                if isinstance(section, dict):
                    section_copy = dict(section)
                    # Merge in claim/calc refs if not present
                    if "referenced_claim_ids" not in section_copy:
                        section_copy["referenced_claim_ids"] = list(claim_ids)
                    if "referenced_calc_ids" not in section_copy:
                        section_copy["referenced_calc_ids"] = list(calc_ids)
                    sections.append(section_copy)
            return {"sections": sections}

        # Check for is_subjective flag
        is_subjective = content.get("is_subjective", False)
        if muhasabah and hasattr(muhasabah, "is_subjective"):
            is_subjective = getattr(muhasabah, "is_subjective", False) or is_subjective

        # Convert flat content to single section
        text_content = ""
        if "text" in content:
            text_content = str(content["text"])
        elif "narrative" in content:
            text_content = str(content["narrative"])
        elif "summary" in content:
            text_content = str(content["summary"])
        elif "analysis" in content:
            text_content = str(content["analysis"])

        # Build section with refs from muhasabah
        section = {
            "text": text_content,
            "is_subjective": is_subjective,
            "is_factual": not is_subjective and bool(text_content),
            "referenced_claim_ids": list(claim_ids),
            "referenced_calc_ids": list(calc_ids),
        }

        return {"sections": [section]}


def enforce_muhasabah_gate(
    output: AgentOutput | None,
    *,
    context: dict[str, Any] | None = None,
    raise_on_reject: bool = True,
) -> GateDecision:
    """Enforce the Muḥāsabah gate on an agent output.

    This is the primary entry point for gate enforcement in the orchestrator.

    Args:
        output: The agent output to validate.
        context: Optional context for error reporting.
        raise_on_reject: If True (default), raise MuhasabahGateError on rejection.

    Returns:
        GateDecision with validation result.

    Raises:
        MuhasabahGateError: If raise_on_reject=True and output is rejected.
    """
    gate = MuhasabahGate()
    decision = gate.evaluate(output, context=context)

    if not decision.allowed and raise_on_reject:
        output_id = getattr(output, "output_id", None) if output else None
        agent_id = getattr(output, "agent_id", None) if output else None

        error_messages = [e.message for e in decision.errors] if decision.errors else []
        message = f"Muḥāsabah gate rejected output: {decision.reason}. Errors: {error_messages}"

        raise MuhasabahGateError(
            message=message,
            decision=decision,
            output_id=output_id,
            agent_id=agent_id,
        )

    return decision


def validate_muhasabah_gate(output: AgentOutput | None) -> ValidationResult:
    """Validate an output against the Muḥāsabah gate.

    This function returns a ValidationResult for compatibility with
    other validators. Does not raise exceptions.

    Args:
        output: The agent output to validate.

    Returns:
        ValidationResult with pass/fail and errors.
    """
    decision = enforce_muhasabah_gate(output, raise_on_reject=False)

    if decision.allowed:
        return ValidationResult.success(decision.warnings if decision.warnings else None)

    return ValidationResult.fail(decision.errors)
