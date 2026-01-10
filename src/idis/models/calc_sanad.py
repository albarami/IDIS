"""Calc-Sanad model for deterministic calculation provenance.

Phase 4.1: Calc-Sanad with grade derivation and explanation entries.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class SanadGrade(str, Enum):
    """Evidence quality grade for claims and calculations.

    Ordering: A > B > C > D (A is highest quality).
    """

    A = "A"
    B = "B"
    C = "C"
    D = "D"

    def __lt__(self, other: object) -> bool:
        """Compare grades: D < C < B < A."""
        if not isinstance(other, SanadGrade):
            return NotImplemented
        order = {"A": 0, "B": 1, "C": 2, "D": 3}
        return order[self.value] > order[other.value]

    def __le__(self, other: object) -> bool:
        """Compare grades: D <= C <= B <= A."""
        if not isinstance(other, SanadGrade):
            return NotImplemented
        return self == other or self < other

    def __gt__(self, other: object) -> bool:
        """Compare grades: A > B > C > D."""
        if not isinstance(other, SanadGrade):
            return NotImplemented
        order = {"A": 0, "B": 1, "C": 2, "D": 3}
        return order[self.value] < order[other.value]

    def __ge__(self, other: object) -> bool:
        """Compare grades: A >= B >= C >= D."""
        if not isinstance(other, SanadGrade):
            return NotImplemented
        return self == other or self > other

    @classmethod
    def min_grade(cls, grades: list[SanadGrade]) -> SanadGrade:
        """Return the minimum (worst) grade from a list.

        Args:
            grades: List of grades to compare.

        Returns:
            The worst (lowest quality) grade.

        Raises:
            ValueError: If grades list is empty.
        """
        if not grades:
            raise ValueError("Cannot compute min_grade of empty list")
        return max(grades, key=lambda g: {"A": 0, "B": 1, "C": 2, "D": 3}[g.value])


class GradeExplanationEntry(BaseModel):
    """Single entry explaining a grade derivation step."""

    step: str = Field(..., description="Description of the grading step")
    input_grade: SanadGrade | None = Field(
        default=None, description="Input grade considered in this step"
    )
    claim_id: str | None = Field(default=None, description="Claim ID this step relates to")
    is_material: bool = Field(
        default=True, description="Whether this input is material to the calculation"
    )
    impact: str | None = Field(
        default=None, description="Impact on final grade (e.g., 'downgrade to D')"
    )


class CalcSanad(BaseModel):
    """Provenance record for a deterministic calculation.

    Links a DeterministicCalculation to its input claims and computes
    the derived grade based on the minimum input grade.
    """

    calc_sanad_id: str = Field(..., description="UUID for this calc sanad record")
    tenant_id: str = Field(..., description="Tenant UUID for isolation")
    calc_id: str = Field(..., description="UUID of the linked DeterministicCalculation")
    input_claim_ids: list[str] = Field(
        default_factory=list,
        description="List of claim UUIDs used as inputs to the calculation",
    )
    input_min_sanad_grade: SanadGrade = Field(
        ..., description="Minimum grade across all input claim sanads"
    )
    calc_grade: SanadGrade = Field(..., description="Derived grade for this calculation (A/B/C/D)")
    explanation: list[GradeExplanationEntry] = Field(
        default_factory=list,
        description="Step-by-step explanation of grade derivation",
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow, description="Record creation timestamp"
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow, description="Record update timestamp"
    )

    def to_db_dict(self) -> dict[str, Any]:
        """Convert to dictionary for database insertion."""
        return {
            "calc_sanad_id": self.calc_sanad_id,
            "tenant_id": self.tenant_id,
            "calc_id": self.calc_id,
            "input_claim_ids": self.input_claim_ids,
            "input_min_sanad_grade": self.input_min_sanad_grade.value,
            "calc_grade": self.calc_grade.value,
            "explanation": [e.model_dump() for e in self.explanation],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    model_config = {"frozen": False, "extra": "forbid"}
