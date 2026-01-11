"""Claim model for atomic factual assertions.

Phase POST-5.2: Adds claim_type (primary vs derived) for calc loop guardrail.

Primary claims: Extracted from source documents, can trigger calc runs.
Derived claims: Created by calc outputs, cannot auto-trigger more calcs.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from idis.models.calc_sanad import SanadGrade
from idis.models.value_structs import ValueStruct


class ClaimClass(str, Enum):
    """Category of the claim."""

    FINANCIAL = "FINANCIAL"
    TRACTION = "TRACTION"
    MARKET_SIZE = "MARKET_SIZE"
    COMPETITION = "COMPETITION"
    TEAM = "TEAM"
    LEGAL_TERMS = "LEGAL_TERMS"
    TECHNICAL = "TECHNICAL"
    OTHER = "OTHER"


class ClaimVerdict(str, Enum):
    """Verdict on the claim's validity."""

    VERIFIED = "VERIFIED"
    INFLATED = "INFLATED"
    CONTRADICTED = "CONTRADICTED"
    UNVERIFIED = "UNVERIFIED"
    SUBJECTIVE = "SUBJECTIVE"


class ClaimAction(str, Enum):
    """Required action based on grade/verdict."""

    NONE = "NONE"
    REQUEST_DATA = "REQUEST_DATA"
    FLAG = "FLAG"
    RED_FLAG = "RED_FLAG"
    HUMAN_GATE = "HUMAN_GATE"
    PARTNER_OVERRIDE_REQUIRED = "PARTNER_OVERRIDE_REQUIRED"


class ClaimType(str, Enum):
    """Type of claim for calc loop guardrail.

    PRIMARY: Extracted from source documents. Can trigger automated calc runs.
    DERIVED: Created by calc output. Cannot auto-trigger more calcs (loop guard).
    """

    PRIMARY = "primary"
    DERIVED = "derived"


class CorroborationStatus(str, Enum):
    """Corroboration level for the claim."""

    NONE = "NONE"
    AHAD_1 = "AHAD_1"
    AHAD_2 = "AHAD_2"
    MUTAWATIR = "MUTAWATIR"


class Corroboration(BaseModel):
    """Corroboration status for a claim."""

    level: CorroborationStatus = Field(..., description="Corroboration level")
    independent_chain_count: int = Field(
        ..., ge=0, description="Count of independent evidence chains"
    )

    model_config = {"frozen": True, "extra": "forbid"}


class Materiality(str, Enum):
    """Materiality level of the claim."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class Claim(BaseModel):
    """An atomic factual assertion extracted from deal materials.

    Every claim has:
    - Sanad chain for evidence provenance
    - Grade (A/B/C/D) computed from Sanad
    - claim_type: PRIMARY (from docs) or DERIVED (from calc output)

    Calc Loop Guardrail:
    - Only PRIMARY claims can trigger automated calc runs
    - DERIVED claims can be created by calc output but must not auto-trigger calcs
    """

    claim_id: str = Field(..., description="UUID for this claim")
    tenant_id: str = Field(..., description="Tenant UUID for isolation")
    deal_id: str = Field(..., description="Deal UUID this claim belongs to")
    claim_class: ClaimClass = Field(..., description="Category of the claim")
    claim_text: str = Field(..., min_length=1, description="The textual assertion")
    claim_type: ClaimType = Field(
        default=ClaimType.PRIMARY,
        description="PRIMARY (from source docs) or DERIVED (from calc output)",
    )
    predicate: str | None = Field(
        default=None, description="Structured predicate form of the claim"
    )
    value: ValueStruct | None = Field(
        default=None, description="Typed value structure for numeric claims"
    )
    sanad_id: str | None = Field(
        default=None, description="Reference to the Sanad chain for this claim"
    )
    claim_grade: SanadGrade = Field(..., description="Computed Sanad grade (A/B/C/D)")
    corroboration: Corroboration | None = Field(
        default=None, description="Corroboration status and independent chain count"
    )
    claim_verdict: ClaimVerdict = Field(..., description="Verdict on the claim's validity")
    claim_action: ClaimAction = Field(..., description="Required action based on grade/verdict")
    defect_ids: list[str] = Field(
        default_factory=list, description="Defect IDs affecting this claim"
    )
    materiality: Materiality = Field(
        default=Materiality.MEDIUM, description="Importance of this claim to the deal"
    )
    ic_bound: bool = Field(
        default=False, description="Whether this claim is bound for IC deliverables"
    )
    primary_span_id: str | None = Field(
        default=None, description="Primary document span for this claim"
    )
    source_calc_id: str | None = Field(
        default=None,
        description="Calc ID that produced this claim (for DERIVED claims only)",
    )
    created_by: str | None = Field(default=None, description="Actor UUID who created this claim")
    created_at: datetime | None = Field(
        default=None, description="Record creation timestamp (caller must provide)"
    )
    updated_at: datetime | None = Field(
        default=None, description="Record update timestamp (caller must provide)"
    )

    def can_trigger_calc(self) -> bool:
        """Check if this claim can trigger automated calculations.

        Only PRIMARY claims can trigger calcs.
        DERIVED claims cannot (calc loop guardrail).

        Returns:
            True if claim can trigger automated calc runs.
        """
        return self.claim_type == ClaimType.PRIMARY

    def to_db_dict(self) -> dict[str, Any]:
        """Convert to dictionary for database insertion."""
        return {
            "claim_id": self.claim_id,
            "tenant_id": self.tenant_id,
            "deal_id": self.deal_id,
            "claim_class": self.claim_class.value,
            "claim_text": self.claim_text,
            "claim_type": self.claim_type.value,
            "predicate": self.predicate,
            "value": self.value.model_dump(mode="json") if self.value else None,
            "sanad_id": self.sanad_id,
            "claim_grade": self.claim_grade.value,
            "corroboration": self.corroboration.model_dump() if self.corroboration else None,
            "claim_verdict": self.claim_verdict.value,
            "claim_action": self.claim_action.value,
            "defect_ids": self.defect_ids,
            "materiality": self.materiality.value,
            "ic_bound": self.ic_bound,
            "primary_span_id": self.primary_span_id,
            "source_calc_id": self.source_calc_id,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    model_config = {"frozen": False, "extra": "forbid"}


class CalcLoopGuard:
    """Guardrail to prevent calc loops from derived claims.

    This enforcer ensures:
    - Only PRIMARY claims can trigger calc runs
    - DERIVED claims cannot auto-trigger additional calculations
    - Provides clear error messages for violations

    Usage:
        guard = CalcLoopGuard()
        guard.validate_calc_trigger(claims)  # Raises if any derived claims
    """

    def validate_calc_trigger(
        self,
        claims: list[Claim],
        allow_derived: bool = False,
    ) -> list[Claim]:
        """Validate that claims can trigger calculation.

        Args:
            claims: List of claims to validate.
            allow_derived: If True, allows derived claims (for explicit overrides).

        Returns:
            List of validated claims that can trigger calc.

        Raises:
            CalcLoopGuardError: If any derived claims found and allow_derived=False.
        """
        if allow_derived:
            return claims

        derived_claims = [c for c in claims if c.claim_type == ClaimType.DERIVED]
        if derived_claims:
            raise CalcLoopGuardError(derived_claims)

        return claims

    def filter_triggerable(self, claims: list[Claim]) -> list[Claim]:
        """Filter claims to only those that can trigger calcs.

        Args:
            claims: List of claims to filter.

        Returns:
            List of PRIMARY claims only (DERIVED filtered out).
        """
        return [c for c in claims if c.can_trigger_calc()]


class CalcLoopGuardError(Exception):
    """Raised when derived claims attempt to trigger calculations.

    This error enforces the calc loop guardrail:
    - DERIVED claims (from calc output) cannot auto-trigger more calcs
    - Prevents infinite calc loops
    """

    def __init__(self, derived_claims: list[Claim]) -> None:
        self.derived_claims = derived_claims
        claim_ids = [c.claim_id for c in derived_claims]
        super().__init__(
            f"Calc loop guardrail violation: {len(derived_claims)} derived claim(s) "
            f"cannot trigger calculations. Claim IDs: {claim_ids}"
        )
