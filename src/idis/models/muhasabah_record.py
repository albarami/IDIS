"""Canonical MuḥāsabahRecord Model — v6.3 Phase 5.2

Defines the normative MuḥāsabahRecord structure per TDD §4.4 and Data Model §5.2.

Every agent output MUST carry a MuḥāsabahRecord with:
- supported_claim_ids (non-empty unless SUBJECTIVE)
- uncertainties (mandatory when confidence > 0.80)
- falsifiability_tests (mandatory for recommendation-driving outputs)

HARD GATE: Invalid records are rejected (fail-closed).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ImpactLevel(StrEnum):
    """Impact level for uncertainties per v6.3 Data Model §5.2."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class FalsifiabilityTest(BaseModel):
    """A test that could falsify the output claim.

    Per v6.3 TDD §4.4: falsifiability_tests MUST be present for
    recommendation-affecting claims (materiality gate).
    """

    model_config = ConfigDict(frozen=True)

    test_description: str = Field(
        ..., min_length=1, description="Description of what the test checks"
    )
    required_evidence: str = Field(
        ..., min_length=1, description="Evidence needed to run this test"
    )
    pass_fail_rule: str = Field(..., min_length=1, description="Rule determining pass/fail outcome")


class Uncertainty(BaseModel):
    """A registered uncertainty in the output.

    Per v6.3 TDD §4.4: uncertainties MUST be present when:
    - confidence > 0.80
    - corroboration status is Āḥād
    - source grade < A
    """

    model_config = ConfigDict(frozen=True)

    uncertainty: str = Field(..., min_length=1, description="Description of the uncertainty")
    impact: ImpactLevel = Field(..., description="Impact level: HIGH, MEDIUM, or LOW")
    mitigation: str = Field(..., min_length=1, description="How this uncertainty is mitigated")


class MuhasabahRecordCanonical(BaseModel):
    """Canonical MuḥāsabahRecord per v6.3 normative contract.

    This model represents the self-accounting record that every agent output
    must carry. The validator enforces:

    1. No-Free-Facts: factual outputs require non-empty supported_claim_ids
    2. Overconfidence: confidence > 0.80 requires non-empty uncertainties
    3. Falsifiability: recommendation/decision requires falsifiability_tests

    All validation is fail-closed: missing or invalid data causes rejection.
    """

    model_config = ConfigDict(frozen=True)

    agent_id: str = Field(..., min_length=1, description="Agent that produced this record")
    output_id: str = Field(..., min_length=1, description="Associated output identifier")
    supported_claim_ids: list[str] = Field(
        default_factory=list, description="Claims supporting the output (non-empty for facts)"
    )
    supported_calc_ids: list[str] = Field(
        default_factory=list, description="Calculations supporting the output"
    )
    falsifiability_tests: list[FalsifiabilityTest] = Field(
        default_factory=list, description="Tests that could falsify the output"
    )
    uncertainties: list[Uncertainty] = Field(
        default_factory=list, description="Registered uncertainties"
    )
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence score 0.0-1.0")
    failure_modes: list[str] = Field(default_factory=list, description="Identified failure modes")
    is_subjective: bool = Field(
        default=False, description="True if output contains no factual assertions"
    )
    has_recommendation: bool = Field(
        default=False, description="True if output contains recommendation/decision"
    )
    timestamp: str = Field(..., min_length=1, description="ISO 8601 timestamp")

    @field_validator("supported_claim_ids", mode="before")
    @classmethod
    def ensure_claim_ids_list(cls, v: Any) -> list[str]:
        """Ensure supported_claim_ids is a list."""
        if v is None:
            return []
        if isinstance(v, list):
            return [str(x) for x in v if x]
        return []

    @field_validator("supported_calc_ids", mode="before")
    @classmethod
    def ensure_calc_ids_list(cls, v: Any) -> list[str]:
        """Ensure supported_calc_ids is a list."""
        if v is None:
            return []
        if isinstance(v, list):
            return [str(x) for x in v if x]
        return []


def muhasabah_to_validator_dict(record: MuhasabahRecordCanonical) -> dict[str, Any]:
    """Convert canonical record to dict format expected by MuhasabahValidator.

    This provides compatibility with the existing validator in
    src/idis/validators/muhasabah.py.
    """
    return {
        "agent_id": record.agent_id,
        "output_id": record.output_id,
        "supported_claim_ids": record.supported_claim_ids,
        "supported_calc_ids": record.supported_calc_ids,
        "falsifiability_tests": [
            {
                "test_description": t.test_description,
                "required_evidence": t.required_evidence,
                "pass_fail_rule": t.pass_fail_rule,
            }
            for t in record.falsifiability_tests
        ],
        "uncertainties": [
            {
                "uncertainty": u.uncertainty,
                "impact": u.impact.value,
                "mitigation": u.mitigation,
            }
            for u in record.uncertainties
        ],
        "confidence": record.confidence,
        "failure_modes": record.failure_modes,
        "is_subjective": record.is_subjective,
        "timestamp": record.timestamp,
        # Include recommendation/decision if has_recommendation is True
        **({"recommendation": True} if record.has_recommendation else {}),
    }
