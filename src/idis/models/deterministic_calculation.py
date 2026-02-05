"""DeterministicCalculation model for reproducible numeric computations.

Phase 4.1: Deterministic calculation with formula_hash and reproducibility_hash.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class CalcType(StrEnum):
    """Supported calculation types."""

    IRR = "IRR"
    MOIC = "MOIC"
    GROSS_MARGIN = "GROSS_MARGIN"
    NET_REVENUE_RETENTION = "NRR"
    CAC_PAYBACK = "CAC_PAYBACK"
    VALUATION_MULTIPLE = "VALUATION_MULTIPLE"
    RUNWAY = "RUNWAY"
    BURN_RATE = "BURN_RATE"
    LTV = "LTV"
    LTV_CAC_RATIO = "LTV_CAC_RATIO"


class CalcInputs(BaseModel):
    """Typed inputs for a deterministic calculation.

    All numeric values are Decimal to ensure deterministic arithmetic.
    """

    claim_ids: list[str] = Field(
        default_factory=list,
        description="Claim IDs providing source values for this calculation",
    )
    values: dict[str, Decimal] = Field(
        default_factory=dict,
        description="Named numeric inputs (all Decimal, no floats)",
    )
    metadata: dict[str, str] = Field(
        default_factory=dict,
        description="Non-numeric metadata (units, currency, time_window)",
    )

    model_config = {"frozen": False, "extra": "forbid"}


class CalcOutput(BaseModel):
    """Typed output from a deterministic calculation.

    All numeric results are Decimal for reproducibility.
    """

    primary_value: Decimal = Field(..., description="Main computed result")
    secondary_values: dict[str, Decimal] = Field(
        default_factory=dict,
        description="Additional computed values (e.g., intermediate results)",
    )
    unit: str | None = Field(None, description="Unit of the primary value")
    currency: str | None = Field(None, description="Currency if applicable")

    model_config = {"frozen": False, "extra": "forbid"}


class DeterministicCalculation(BaseModel):
    """A deterministic calculation with full reproducibility provenance.

    Every numeric output in IDIS must come from a DeterministicCalculation
    to satisfy the No-Free-Facts and zero-numerical-hallucination invariants.
    """

    calc_id: str = Field(..., description="UUID for this calculation")
    tenant_id: str = Field(..., description="Tenant UUID for isolation")
    deal_id: str = Field(..., description="Deal UUID this calculation belongs to")
    calc_type: CalcType = Field(..., description="Type of calculation performed")
    inputs: CalcInputs = Field(..., description="All inputs to the calculation")
    formula_hash: str = Field(
        ...,
        description="SHA256 hash of {calc_type, formula_version, expression_id}",
    )
    code_version: str = Field(..., description="Package version or git SHA of the calc engine")
    output: CalcOutput = Field(..., description="Computed output values")
    reproducibility_hash: str = Field(
        ...,
        description="SHA256 hash of calc determinism inputs",
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
            "calc_id": self.calc_id,
            "tenant_id": self.tenant_id,
            "deal_id": self.deal_id,
            "calc_type": self.calc_type.value,
            "inputs": {
                "claim_ids": self.inputs.claim_ids,
                "values": {k: str(v) for k, v in self.inputs.values.items()},
                "metadata": self.inputs.metadata,
            },
            "formula_hash": self.formula_hash,
            "code_version": self.code_version,
            "output": {
                "primary_value": str(self.output.primary_value),
                "secondary_values": {k: str(v) for k, v in self.output.secondary_values.items()},
                "unit": self.output.unit,
                "currency": self.output.currency,
            },
            "reproducibility_hash": self.reproducibility_hash,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    model_config = {"frozen": False, "extra": "forbid"}
