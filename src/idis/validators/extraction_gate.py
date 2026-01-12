"""Extraction Confidence Gate — Phase 4.2 Deterministic Gate.

HARD GATE: Blocks deterministic calculations when:
- extraction_confidence < 0.95 OR
- dhabt_score < 0.90 OR
- either value is missing/invalid

UNLESS the input is explicitly marked human-verified.

Per Data Model §7.3: Low confidence/dhabt claims "MUST NOT be used as input
to deterministic engines without human verification."

Per Go-Live §1.4: Extraction confidence gate blocks calcs if
extraction_confidence < 0.95 OR dhabt_score < 0.90.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any

from idis.validators.schema_validator import ValidationError, ValidationResult

# Thresholds as Decimal constants (no float math per spec)
CONFIDENCE_THRESHOLD = Decimal("0.95")
DHABT_THRESHOLD = Decimal("0.90")


class VerificationMethod(Enum):
    """How a claim/value was verified."""

    NONE = "NONE"
    HUMAN_VERIFIED = "HUMAN_VERIFIED"
    SYSTEM_VERIFIED = "SYSTEM_VERIFIED"
    DUAL_VERIFIED = "DUAL_VERIFIED"


class ExtractionGateBlockReason(Enum):
    """Reason for blocking at the extraction gate."""

    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    LOW_DHABT = "LOW_DHABT"
    MISSING_CONFIDENCE = "MISSING_CONFIDENCE"
    MISSING_DHABT = "MISSING_DHABT"
    INVALID_CONFIDENCE = "INVALID_CONFIDENCE"
    INVALID_DHABT = "INVALID_DHABT"


@dataclass(frozen=True)
class ExtractionGateInput:
    """Input for extraction gate evaluation.

    Attributes:
        claim_id: Unique identifier for the claim/value.
        extraction_confidence: Confidence score from extraction (0-1). None if missing.
        dhabt_score: Precision/accuracy score (0-1). None if missing.
        is_human_verified: Whether the value has been human-verified.
        verification_method: How the claim was verified (alternative to is_human_verified).
    """

    claim_id: str
    extraction_confidence: Decimal | None
    dhabt_score: Decimal | None
    is_human_verified: bool = False
    verification_method: VerificationMethod = VerificationMethod.NONE


@dataclass(frozen=True)
class ExtractionGateDecision:
    """Decision from extraction gate evaluation.

    Attributes:
        allowed: Whether the input is allowed for deterministic calculations.
        blocked: Whether the input is blocked (inverse of allowed for clarity).
        reason: If blocked, the reason for blocking.
        claim_id: The claim_id that was evaluated.
        extraction_confidence: The confidence value evaluated (None if missing).
        dhabt_score: The dhabt value evaluated (None if missing).
        bypassed_by_human_verification: True if gate was bypassed due to human verification.
    """

    allowed: bool
    blocked: bool
    reason: ExtractionGateBlockReason | None
    claim_id: str
    extraction_confidence: Decimal | None
    dhabt_score: Decimal | None
    bypassed_by_human_verification: bool = False

    def __post_init__(self) -> None:
        """Validate that allowed and blocked are consistent."""
        if self.allowed == self.blocked:
            raise ValueError("allowed and blocked must be opposite")


class ExtractionGateBlockedError(Exception):
    """Raised when extraction gate blocks a calculation.

    This is a typed exception that can be caught at API boundaries
    to return appropriate error responses.
    """

    def __init__(
        self,
        blocked_inputs: list[ExtractionGateDecision],
        calc_type: str | None = None,
    ) -> None:
        self.blocked_inputs = blocked_inputs
        self.calc_type = calc_type
        claim_ids = [d.claim_id for d in blocked_inputs]
        reasons = [d.reason.value if d.reason else "UNKNOWN" for d in blocked_inputs]
        claims_str = str(claim_ids[:3]) + ("..." if len(claim_ids) > 3 else "")
        reasons_str = str(reasons[:3]) + ("..." if len(reasons) > 3 else "")
        super().__init__(
            f"Extraction gate blocked {len(blocked_inputs)} input(s) for calc "
            f"'{calc_type or 'unknown'}': claims={claims_str}, reasons={reasons_str}"
        )


def _is_human_verified(input_data: ExtractionGateInput) -> bool:
    """Check if input is human-verified via either flag or verification_method."""
    has_verification_flag = input_data.is_human_verified
    has_verification_method = input_data.verification_method in (
        VerificationMethod.HUMAN_VERIFIED,
        VerificationMethod.DUAL_VERIFIED,
    )
    return has_verification_flag or has_verification_method


def _to_decimal(value: Any) -> Decimal | None:
    """Convert value to Decimal safely. Returns None if invalid."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def evaluate_extraction_gate(input_data: ExtractionGateInput) -> ExtractionGateDecision:
    """Evaluate extraction gate for a single input.

    Fail-closed semantics:
    - Missing confidence → blocked (unless human-verified)
    - Missing dhabt → blocked (unless human-verified)
    - Invalid confidence → blocked (unless human-verified)
    - Invalid dhabt → blocked (unless human-verified)
    - confidence < 0.95 → blocked (unless human-verified)
    - dhabt < 0.90 → blocked (unless human-verified)

    Human verification bypasses ALL checks.

    Args:
        input_data: The input to evaluate.

    Returns:
        ExtractionGateDecision indicating whether the input is allowed.
    """
    # Human verification bypasses all checks
    if _is_human_verified(input_data):
        return ExtractionGateDecision(
            allowed=True,
            blocked=False,
            reason=None,
            claim_id=input_data.claim_id,
            extraction_confidence=input_data.extraction_confidence,
            dhabt_score=input_data.dhabt_score,
            bypassed_by_human_verification=True,
        )

    # Check extraction_confidence
    confidence = input_data.extraction_confidence

    # Missing confidence → blocked (fail-closed)
    if confidence is None:
        return ExtractionGateDecision(
            allowed=False,
            blocked=True,
            reason=ExtractionGateBlockReason.MISSING_CONFIDENCE,
            claim_id=input_data.claim_id,
            extraction_confidence=None,
            dhabt_score=input_data.dhabt_score,
        )

    # Validate confidence is a valid Decimal in range [0, 1]
    try:
        if not isinstance(confidence, Decimal):
            confidence = Decimal(str(confidence))
        if confidence < Decimal("0") or confidence > Decimal("1"):
            return ExtractionGateDecision(
                allowed=False,
                blocked=True,
                reason=ExtractionGateBlockReason.INVALID_CONFIDENCE,
                claim_id=input_data.claim_id,
                extraction_confidence=confidence,
                dhabt_score=input_data.dhabt_score,
            )
    except (InvalidOperation, ValueError, TypeError):
        return ExtractionGateDecision(
            allowed=False,
            blocked=True,
            reason=ExtractionGateBlockReason.INVALID_CONFIDENCE,
            claim_id=input_data.claim_id,
            extraction_confidence=None,
            dhabt_score=input_data.dhabt_score,
        )

    # Low confidence → blocked
    if confidence < CONFIDENCE_THRESHOLD:
        return ExtractionGateDecision(
            allowed=False,
            blocked=True,
            reason=ExtractionGateBlockReason.LOW_CONFIDENCE,
            claim_id=input_data.claim_id,
            extraction_confidence=confidence,
            dhabt_score=input_data.dhabt_score,
        )

    # Check dhabt_score
    dhabt = input_data.dhabt_score

    # Missing dhabt → blocked (fail-closed)
    if dhabt is None:
        return ExtractionGateDecision(
            allowed=False,
            blocked=True,
            reason=ExtractionGateBlockReason.MISSING_DHABT,
            claim_id=input_data.claim_id,
            extraction_confidence=confidence,
            dhabt_score=None,
        )

    # Validate dhabt is a valid Decimal in range [0, 1]
    try:
        if not isinstance(dhabt, Decimal):
            dhabt = Decimal(str(dhabt))
        if dhabt < Decimal("0") or dhabt > Decimal("1"):
            return ExtractionGateDecision(
                allowed=False,
                blocked=True,
                reason=ExtractionGateBlockReason.INVALID_DHABT,
                claim_id=input_data.claim_id,
                extraction_confidence=confidence,
                dhabt_score=dhabt,
            )
    except (InvalidOperation, ValueError, TypeError):
        return ExtractionGateDecision(
            allowed=False,
            blocked=True,
            reason=ExtractionGateBlockReason.INVALID_DHABT,
            claim_id=input_data.claim_id,
            extraction_confidence=confidence,
            dhabt_score=None,
        )

    # Low dhabt → blocked
    if dhabt < DHABT_THRESHOLD:
        return ExtractionGateDecision(
            allowed=False,
            blocked=True,
            reason=ExtractionGateBlockReason.LOW_DHABT,
            claim_id=input_data.claim_id,
            extraction_confidence=confidence,
            dhabt_score=dhabt,
        )

    # All checks passed → allowed
    return ExtractionGateDecision(
        allowed=True,
        blocked=False,
        reason=None,
        claim_id=input_data.claim_id,
        extraction_confidence=confidence,
        dhabt_score=dhabt,
    )


def evaluate_extraction_gate_batch(
    inputs: list[ExtractionGateInput],
) -> tuple[list[ExtractionGateDecision], list[ExtractionGateDecision]]:
    """Evaluate extraction gate for multiple inputs.

    Args:
        inputs: List of inputs to evaluate.

    Returns:
        Tuple of (allowed_decisions, blocked_decisions).
    """
    allowed: list[ExtractionGateDecision] = []
    blocked: list[ExtractionGateDecision] = []

    for input_data in inputs:
        decision = evaluate_extraction_gate(input_data)
        if decision.allowed:
            allowed.append(decision)
        else:
            blocked.append(decision)

    return allowed, blocked


def validate_extraction_gate(input_data: ExtractionGateInput) -> ValidationResult:
    """Validate extraction gate as a ValidationResult (for API consistency).

    This provides the same interface as other validators (no_free_facts, etc.)
    for use in validation pipelines.

    Args:
        input_data: The input to validate.

    Returns:
        ValidationResult - FAILS if gate would block the input.
    """
    decision = evaluate_extraction_gate(input_data)

    if decision.blocked:
        reason_msg = decision.reason.value if decision.reason else "UNKNOWN"
        return ValidationResult.fail(
            [
                ValidationError(
                    code=f"EXTRACTION_GATE_{reason_msg}",
                    message=(
                        f"Extraction gate blocked claim {decision.claim_id}: "
                        f"{reason_msg}. confidence={decision.extraction_confidence}, "
                        f"dhabt={decision.dhabt_score}. "
                        f"Human verification required to bypass."
                    ),
                    path=f"$.claim_id[{decision.claim_id}]",
                )
            ]
        )

    return ValidationResult.success()


class ExtractionGateValidator:
    """Validator class for extraction gate (matches other validator patterns).

    Provides class-based interface consistent with NoFreeFactsValidator,
    SanadIntegrityValidator, etc.
    """

    def __init__(self) -> None:
        """Initialize the validator."""
        self.confidence_threshold = CONFIDENCE_THRESHOLD
        self.dhabt_threshold = DHABT_THRESHOLD

    def validate(self, input_data: ExtractionGateInput) -> ValidationResult:
        """Validate a single input."""
        return validate_extraction_gate(input_data)

    def validate_batch(
        self, inputs: list[ExtractionGateInput]
    ) -> tuple[list[ValidationResult], list[ExtractionGateDecision]]:
        """Validate multiple inputs, returning results and blocked decisions.

        Args:
            inputs: List of inputs to validate.

        Returns:
            Tuple of (validation_results, blocked_decisions).
        """
        results: list[ValidationResult] = []
        blocked: list[ExtractionGateDecision] = []

        for input_data in inputs:
            decision = evaluate_extraction_gate(input_data)
            results.append(validate_extraction_gate(input_data))
            if decision.blocked:
                blocked.append(decision)

        return results, blocked

    def enforce_gate(
        self,
        inputs: list[ExtractionGateInput],
        calc_type: str | None = None,
    ) -> None:
        """Enforce extraction gate - raises if ANY input is blocked.

        This is the fail-closed enforcement method for use in calc engine.

        Args:
            inputs: List of inputs to validate.
            calc_type: Optional calc type for error message.

        Raises:
            ExtractionGateBlockedError: If any input is blocked.
        """
        _, blocked = evaluate_extraction_gate_batch(inputs)
        if blocked:
            raise ExtractionGateBlockedError(blocked, calc_type)
