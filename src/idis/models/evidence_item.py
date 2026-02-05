"""EvidenceItem model for source evidence supporting claims.

Phase 3.3: Sanad Trust Framework evidence items with source grading.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class VerificationStatus(StrEnum):
    """Verification status of evidence."""

    UNVERIFIED = "UNVERIFIED"
    VERIFIED = "VERIFIED"
    CONTRADICTED = "CONTRADICTED"


class SourceGrade(StrEnum):
    """Public source grade (Tier 1).

    Ordering: A > B > C > D (A is highest quality).
    """

    A = "A"
    B = "B"
    C = "C"
    D = "D"


class SourceSubgrade(StrEnum):
    """Internal analytics subgrade (not used in gating)."""

    A_PLUS = "A+"
    A = "A"
    A_MINUS = "A-"
    B_PLUS = "B+"
    B = "B"
    B_MINUS = "B-"
    C_PLUS = "C+"
    C = "C"
    C_MINUS = "C-"
    D = "D"


class EvidenceItem(BaseModel):
    """An evidence item that supports or contradicts a claim.

    Evidence items are the foundational data units in the Sanad chain,
    representing extracted or retrieved information from source documents
    or external systems.

    All evidence items are:
    - Tenant-isolated (tenant_id required)
    - Deal-scoped (deal_id required)
    - Graded for source quality (source_grade required)
    """

    evidence_id: str = Field(..., description="UUID for this evidence item")
    tenant_id: str = Field(..., description="Tenant UUID for isolation")
    deal_id: str = Field(..., description="Deal UUID this evidence belongs to")
    source_span_id: str | None = Field(
        default=None,
        description="Reference to the document span where this evidence was extracted",
    )
    source_system: str | None = Field(
        default=None,
        description="Origin system (e.g., Stripe, QuickBooks, Bank, Audit, Deck, ResearchProvider)",
    )
    upstream_origin_id: str | None = Field(
        default=None,
        description="Required for independence tests in corroboration",
    )
    retrieval_timestamp: datetime | None = Field(
        default=None,
        description="When this evidence was retrieved from the source",
    )
    verification_status: VerificationStatus = Field(
        ..., description="Current verification status of this evidence"
    )
    source_grade: SourceGrade = Field(..., description="Public source grade (Tier 1)")
    source_rank_subgrade: SourceSubgrade | None = Field(
        default=None, description="Internal analytics subgrade (not used in gating)"
    )
    rationale: dict[str, Any] | None = Field(
        default=None, description="Explanation for the assigned grade"
    )
    created_at: datetime | None = Field(default=None, description="Record creation timestamp")
    updated_at: datetime | None = Field(default=None, description="Record update timestamp")

    @field_validator("evidence_id", "tenant_id", "deal_id", mode="before")
    @classmethod
    def validate_required_uuid_fields(cls, v: Any) -> str:
        """Validate required UUID fields are non-empty strings."""
        if not isinstance(v, str) or not v.strip():
            raise ValueError("Field must be a non-empty string")
        return v

    def to_canonical_dict(self) -> dict[str, Any]:
        """Convert to canonical dictionary with stable key ordering.

        Returns a dictionary suitable for deterministic serialization,
        with keys in sorted order and consistent value representations.
        """
        data = self.model_dump(mode="json")
        return dict(sorted(data.items()))

    def stable_hash(self) -> str:
        """Compute SHA256 hash over canonical JSON representation.

        Returns a stable hash that can be used for integrity verification
        and deduplication.
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
            "evidence_id": self.evidence_id,
            "tenant_id": self.tenant_id,
            "deal_id": self.deal_id,
            "source_span_id": self.source_span_id,
            "source_system": self.source_system,
            "upstream_origin_id": self.upstream_origin_id,
            "retrieval_timestamp": self.retrieval_timestamp,
            "verification_status": self.verification_status.value,
            "source_grade": self.source_grade.value,
            "source_rank_subgrade": (
                self.source_rank_subgrade.value if self.source_rank_subgrade else None
            ),
            "rationale": self.rationale,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    model_config = {"frozen": False, "extra": "forbid"}
