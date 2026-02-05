"""Defect model for structured faults in the evidence chain.

Phase 3.3: Sanad Trust Framework defect tracking (ʿIlal-inspired).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class DefectType(StrEnum):
    """Categorical type of the defect."""

    BROKEN_CHAIN = "BROKEN_CHAIN"
    MISSING_LINK = "MISSING_LINK"
    UNKNOWN_SOURCE = "UNKNOWN_SOURCE"
    CONCEALMENT = "CONCEALMENT"
    INCONSISTENCY = "INCONSISTENCY"
    ANOMALY_VS_STRONGER_SOURCES = "ANOMALY_VS_STRONGER_SOURCES"
    CHRONO_IMPOSSIBLE = "CHRONO_IMPOSSIBLE"
    CHAIN_GRAFTING = "CHAIN_GRAFTING"
    CIRCULARITY = "CIRCULARITY"
    STALENESS = "STALENESS"
    UNIT_MISMATCH = "UNIT_MISMATCH"
    TIME_WINDOW_MISMATCH = "TIME_WINDOW_MISMATCH"
    SCOPE_DRIFT = "SCOPE_DRIFT"
    IMPLAUSIBILITY = "IMPLAUSIBILITY"


class DefectSeverity(StrEnum):
    """Impact severity of this defect."""

    FATAL = "FATAL"
    MAJOR = "MAJOR"
    MINOR = "MINOR"


class CureProtocol(StrEnum):
    """Required action to address this defect."""

    REQUEST_SOURCE = "REQUEST_SOURCE"
    REQUIRE_REAUDIT = "REQUIRE_REAUDIT"
    HUMAN_ARBITRATION = "HUMAN_ARBITRATION"
    RECONSTRUCT_CHAIN = "RECONSTRUCT_CHAIN"
    DISCARD_CLAIM = "DISCARD_CLAIM"


class DefectStatus(StrEnum):
    """Current status of this defect."""

    OPEN = "OPEN"
    CURED = "CURED"
    WAIVED = "WAIVED"


class Defect(BaseModel):
    """A structured fault in the evidence chain (ʿIlal-inspired).

    Defects represent identified problems in the Sanad chain that affect
    the reliability or validity of claims. Each defect has:
    - A categorical type (what kind of fault)
    - Severity (how bad is it)
    - Cure protocol (what must be done to fix it)
    - Status tracking (open/cured/waived)

    All defects are:
    - Tenant-isolated when tenant_id is provided
    - Linked to affected claims
    - Timestamped for audit trail
    """

    defect_id: str = Field(..., description="UUID for this defect")
    tenant_id: str | None = Field(default=None, description="Tenant UUID for isolation")
    deal_id: str | None = Field(default=None, description="Deal UUID this defect belongs to")
    defect_type: DefectType = Field(..., description="Categorical type of the defect")
    severity: DefectSeverity = Field(..., description="Impact severity of this defect")
    detected_by: str | None = Field(default=None, description="Actor who detected this defect")
    description: str = Field(
        ..., min_length=1, description="Human-readable description of the defect"
    )
    evidence_refs: list[dict[str, Any]] = Field(
        default_factory=list,
        description="References to evidence supporting this defect finding",
    )
    cure_protocol: CureProtocol = Field(..., description="Required action to address this defect")
    status: DefectStatus = Field(..., description="Current status of this defect")
    waiver_reason: str | None = Field(
        default=None, description="Justification if defect was waived"
    )
    waived_by: str | None = Field(default=None, description="Actor who approved the waiver")
    affected_claim_ids: list[str] = Field(..., description="Claims affected by this defect")
    timestamp: datetime = Field(..., description="When this defect was detected")
    created_at: datetime | None = Field(default=None, description="Record creation timestamp")
    updated_at: datetime | None = Field(default=None, description="Record update timestamp")

    @field_validator("defect_id", mode="before")
    @classmethod
    def validate_defect_id(cls, v: Any) -> str:
        """Validate defect_id is a non-empty string."""
        if not isinstance(v, str) or not v.strip():
            raise ValueError("defect_id must be a non-empty string")
        return v

    @field_validator("description", mode="before")
    @classmethod
    def validate_description(cls, v: Any) -> str:
        """Validate description is a non-empty string."""
        if not isinstance(v, str) or not v.strip():
            raise ValueError("description must be a non-empty string")
        return v

    @field_validator("affected_claim_ids", mode="before")
    @classmethod
    def validate_affected_claim_ids(cls, v: Any) -> list[str]:
        """Validate affected_claim_ids is a list."""
        if not isinstance(v, list):
            raise ValueError("affected_claim_ids must be a list")
        return v

    def to_canonical_dict(self) -> dict[str, Any]:
        """Convert to canonical dictionary with stable key ordering.

        Returns a dictionary suitable for deterministic serialization,
        with keys in sorted order and consistent value representations.
        """
        data = self.model_dump(mode="json")
        # Sort affected_claim_ids for stability
        if data.get("affected_claim_ids"):
            data["affected_claim_ids"] = sorted(data["affected_claim_ids"])
        # Sort evidence_refs by their string representation
        if data.get("evidence_refs"):
            data["evidence_refs"] = sorted(
                data["evidence_refs"], key=lambda x: json.dumps(x, sort_keys=True)
            )
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
            "defect_id": self.defect_id,
            "tenant_id": self.tenant_id,
            "deal_id": self.deal_id,
            "defect_type": self.defect_type.value,
            "severity": self.severity.value,
            "detected_by": self.detected_by,
            "description": self.description,
            "evidence_refs": self.evidence_refs,
            "cure_protocol": self.cure_protocol.value,
            "status": self.status.value,
            "waiver_reason": self.waiver_reason,
            "waived_by": self.waived_by,
            "affected_claim_ids": self.affected_claim_ids,
            "timestamp": self.timestamp,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    model_config = {"frozen": False, "extra": "forbid"}
