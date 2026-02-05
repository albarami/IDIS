"""TransmissionNode model for chain of custody/transformation steps.

Phase 3.3: Sanad Trust Framework transmission chain nodes.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class NodeType(StrEnum):
    """Type of transformation/operation performed at this node."""

    INGEST = "INGEST"
    EXTRACT = "EXTRACT"
    NORMALIZE = "NORMALIZE"
    RECONCILE = "RECONCILE"
    CALCULATE = "CALCULATE"
    INFER = "INFER"
    HUMAN_VERIFY = "HUMAN_VERIFY"
    EXPORT = "EXPORT"


class ActorType(StrEnum):
    """Type of actor that performed this operation."""

    AGENT = "AGENT"
    HUMAN = "HUMAN"
    SYSTEM = "SYSTEM"


class VerificationMethod(StrEnum):
    """How this node's output was verified."""

    AUTO = "auto"
    CROSS_CHECK = "cross-check"
    HUMAN_VERIFIED = "human-verified"


class TransmissionNode(BaseModel):
    """A step in the chain of custody/transformation for evidence or claims.

    TransmissionNodes form the backbone of the Sanad chain, documenting
    every transformation from raw source to final claim. Each node records:
    - What operation was performed (node_type)
    - Who performed it (actor_type + actor_id)
    - When it happened (timestamp)
    - What inputs were consumed and outputs produced
    - Confidence and verification metadata
    """

    node_id: str = Field(..., description="UUID for this transmission node")
    node_type: NodeType = Field(
        ..., description="Type of transformation/operation performed at this node"
    )
    actor_type: ActorType = Field(..., description="Type of actor that performed this operation")
    actor_id: str = Field(
        ..., description="Identifier of the actor (agent_id, user_id, or system component)"
    )
    input_refs: list[dict[str, Any]] = Field(
        default_factory=list, description="References to inputs consumed by this node"
    )
    output_refs: list[dict[str, Any]] = Field(
        default_factory=list, description="References to outputs produced by this node"
    )
    timestamp: datetime = Field(..., description="When this transformation occurred")
    confidence: float | None = Field(
        default=None, ge=0.0, le=1.0, description="Extraction/transformation confidence score"
    )
    dhabt_score: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Historical precision score for this actor/operation type",
    )
    verification_method: VerificationMethod | None = Field(
        default=None, description="How this node's output was verified"
    )
    notes: str | None = Field(
        default=None, description="Additional notes about this transformation"
    )

    @field_validator("node_id", "actor_id", mode="before")
    @classmethod
    def validate_required_string_fields(cls, v: Any) -> str:
        """Validate required string fields are non-empty."""
        if not isinstance(v, str) or not v.strip():
            raise ValueError("Field must be a non-empty string")
        return v

    @model_validator(mode="after")
    def validate_confidence_bounds(self) -> TransmissionNode:
        """Validate confidence and dhabt_score are within bounds."""
        if self.confidence is not None and not (0.0 <= self.confidence <= 1.0):
            raise ValueError("confidence must be between 0 and 1")
        if self.dhabt_score is not None and not (0.0 <= self.dhabt_score <= 1.0):
            raise ValueError("dhabt_score must be between 0 and 1")
        return self

    def to_canonical_dict(self) -> dict[str, Any]:
        """Convert to canonical dictionary with stable key ordering.

        Returns a dictionary suitable for deterministic serialization,
        with keys in sorted order and consistent value representations.
        """
        data = self.model_dump(mode="json")
        # Sort nested lists of dicts by their string representation for stability
        if data.get("input_refs"):
            data["input_refs"] = sorted(
                data["input_refs"], key=lambda x: json.dumps(x, sort_keys=True)
            )
        if data.get("output_refs"):
            data["output_refs"] = sorted(
                data["output_refs"], key=lambda x: json.dumps(x, sort_keys=True)
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
            "node_id": self.node_id,
            "node_type": self.node_type.value,
            "actor_type": self.actor_type.value,
            "actor_id": self.actor_id,
            "input_refs": self.input_refs,
            "output_refs": self.output_refs,
            "timestamp": self.timestamp,
            "confidence": self.confidence,
            "dhabt_score": self.dhabt_score,
            "verification_method": (
                self.verification_method.value if self.verification_method else None
            ),
            "notes": self.notes,
        }

    model_config = {"frozen": False, "extra": "forbid"}
