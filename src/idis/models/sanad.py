"""Sanad model for claim-level evidence chains.

Phase 3.3: Sanad Trust Framework with grade, corroboration, and defects.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from idis.models.defect import Defect
from idis.models.transmission_node import TransmissionNode


class CorroborationStatus(StrEnum):
    """Level of independent corroboration."""

    NONE = "NONE"
    AHAD_1 = "AHAD_1"
    AHAD_2 = "AHAD_2"
    MUTAWATIR = "MUTAWATIR"


class SanadGrade(StrEnum):
    """Computed grade for this Sanad.

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


class Sanad(BaseModel):
    """Claim-level evidence chain with grade, corroboration, and defects.

    A Sanad represents the complete provenance chain for a claim, including:
    - The primary evidence supporting the claim
    - Any corroborating evidence from independent sources
    - The chain of transformations from source to claim
    - Any defects found in the chain
    - The computed grade based on evidence quality

    All Sanads are:
    - Tenant-isolated (tenant_id required)
    - Claim-linked (claim_id required)
    - Graded (sanad_grade required)
    """

    sanad_id: str = Field(..., description="UUID for this Sanad")
    tenant_id: str = Field(..., description="Tenant UUID for isolation")
    claim_id: str = Field(..., description="The claim this Sanad supports")
    deal_id: str | None = Field(default=None, description="Deal UUID this Sanad belongs to")
    primary_evidence_id: str = Field(..., description="Primary evidence item for this claim")
    corroborating_evidence_ids: list[str] = Field(
        default_factory=list,
        description="Additional evidence items supporting this claim",
    )
    extraction_confidence: float = Field(
        ..., ge=0.0, le=1.0, description="Confidence of extraction (0-1)"
    )
    dhabt_score: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Historical precision score for extractor",
    )
    corroboration_status: CorroborationStatus = Field(
        ..., description="Level of independent corroboration"
    )
    sanad_grade: SanadGrade = Field(..., description="Computed grade for this Sanad")
    grade_explanation: list[dict[str, Any]] = Field(
        default_factory=list, description="Rationale for the computed grade"
    )
    transmission_chain: list[TransmissionNode] = Field(
        ..., min_length=1, description="Chain of custody/transformation nodes"
    )
    defects: list[Defect] = Field(default_factory=list, description="Defects found in this Sanad")
    created_at: datetime | None = Field(default=None, description="Record creation timestamp")
    updated_at: datetime | None = Field(default=None, description="Record update timestamp")

    @field_validator("sanad_id", "tenant_id", "claim_id", "primary_evidence_id", mode="before")
    @classmethod
    def validate_required_uuid_fields(cls, v: Any) -> str:
        """Validate required UUID fields are non-empty strings."""
        if not isinstance(v, str) or not v.strip():
            raise ValueError("Field must be a non-empty string")
        return v

    @field_validator("transmission_chain", mode="before")
    @classmethod
    def validate_transmission_chain(cls, v: Any) -> list[Any]:
        """Validate transmission_chain is a non-empty list."""
        if not isinstance(v, list):
            raise ValueError("transmission_chain must be a list")
        if len(v) == 0:
            raise ValueError("transmission_chain must have at least one node")
        return v

    @model_validator(mode="after")
    def validate_confidence_bounds(self) -> Sanad:
        """Validate extraction_confidence and dhabt_score are within bounds."""
        if not (0.0 <= self.extraction_confidence <= 1.0):
            raise ValueError("extraction_confidence must be between 0 and 1")
        if self.dhabt_score is not None and not (0.0 <= self.dhabt_score <= 1.0):
            raise ValueError("dhabt_score must be between 0 and 1")
        return self

    @model_validator(mode="after")
    def validate_defect_tenant_consistency(self) -> Sanad:
        """Validate nested defects have consistent tenant_id and deal_id.

        Enforces tenant isolation invariant:
        - Each defect's tenant_id must match the Sanad's tenant_id
        - If both Sanad and defect have deal_id, they must match

        Fails closed on any unexpected type or missing attribute.
        """
        for idx, defect in enumerate(self.defects):
            # Fail closed: verify defect is the expected type
            if not hasattr(defect, "tenant_id") or not hasattr(defect, "deal_id"):
                raise ValueError(
                    f"defects[{idx}]: invalid defect structure, missing required attributes"
                )

            # Check tenant_id consistency
            defect_tenant = getattr(defect, "tenant_id", None)
            if defect_tenant is not None and defect_tenant != self.tenant_id:
                raise ValueError(
                    f"defects[{idx}]: tenant_id mismatch (defect tenant differs from sanad tenant)"
                )

            # Check deal_id consistency when both are present
            defect_deal = getattr(defect, "deal_id", None)
            if self.deal_id is not None and defect_deal is not None and defect_deal != self.deal_id:
                raise ValueError(
                    f"defects[{idx}]: deal_id mismatch (defect deal differs from sanad deal)"
                )

        return self

    def to_canonical_dict(self) -> dict[str, Any]:
        """Convert to canonical dictionary with stable key ordering.

        Returns a dictionary suitable for deterministic serialization,
        with keys in sorted order and consistent value representations.
        """
        data = self.model_dump(mode="json")
        # Sort corroborating_evidence_ids for stability
        if data.get("corroborating_evidence_ids"):
            data["corroborating_evidence_ids"] = sorted(data["corroborating_evidence_ids"])
        # Sort grade_explanation by string representation
        if data.get("grade_explanation"):
            data["grade_explanation"] = sorted(
                data["grade_explanation"], key=lambda x: json.dumps(x, sort_keys=True)
            )
        # Transmission chain maintains order (chronological)
        # Defects maintain order (detection order)
        return dict(sorted(data.items()))

    def stable_hash(self) -> str:
        """Compute SHA256 hash over canonical JSON representation.

        Returns a stable hash that can be used for integrity verification.
        """
        canonical = json.dumps(
            self.to_canonical_dict(),
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def to_db_dict(self) -> dict[str, Any]:
        """Convert to dictionary for database insertion."""
        return {
            "sanad_id": self.sanad_id,
            "tenant_id": self.tenant_id,
            "claim_id": self.claim_id,
            "deal_id": self.deal_id,
            "primary_evidence_id": self.primary_evidence_id,
            "corroborating_evidence_ids": self.corroborating_evidence_ids,
            "extraction_confidence": self.extraction_confidence,
            "dhabt_score": self.dhabt_score,
            "corroboration_status": self.corroboration_status.value,
            "sanad_grade": self.sanad_grade.value,
            "grade_explanation": self.grade_explanation,
            "transmission_chain": [n.to_db_dict() for n in self.transmission_chain],
            "defects": [d.to_db_dict() for d in self.defects],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    model_config = {"frozen": False, "extra": "forbid"}
