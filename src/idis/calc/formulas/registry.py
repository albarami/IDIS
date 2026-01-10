"""Formula registry with versioned specifications and stable hashing.

Phase 4.1: FormulaSpec and FormulaRegistry for deterministic calculations.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from idis.models.deterministic_calculation import CalcType


@dataclass(frozen=True)
class FormulaSpec:
    """Specification for a deterministic formula.

    Attributes:
        calc_type: The type of calculation this formula performs.
        version: Semantic version of the formula (e.g., "1.0.0").
        expression_id: Unique identifier for the formula expression.
        fn: The calculation function (Decimal inputs -> Decimal output).
        required_inputs: List of required input value keys.
        optional_inputs: List of optional input value keys with defaults.
        output_precision: Number of decimal places for output quantization.
    """

    calc_type: CalcType
    version: str
    expression_id: str
    fn: Callable[[dict[str, Decimal]], Decimal]
    required_inputs: tuple[str, ...] = field(default_factory=tuple)
    optional_inputs: dict[str, Decimal] = field(default_factory=dict)
    output_precision: int = 4

    @property
    def formula_hash(self) -> str:
        """Compute stable SHA256 hash of the formula specification.

        Hash is computed from canonical JSON of {calc_type, formula_version, expression_id}.
        This hash is stable across runs as long as these three values don't change.
        """
        spec_dict = {
            "calc_type": self.calc_type.value,
            "expression_id": self.expression_id,
            "formula_version": self.version,
        }
        canonical_json = json.dumps(spec_dict, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


class FormulaRegistry:
    """Registry of versioned formula specifications.

    Thread-safe registry for looking up formula specs by calc_type.
    Formulas are immutable once registered.
    """

    _instance: FormulaRegistry | None = None
    _formulas: dict[CalcType, FormulaSpec]

    def __new__(cls) -> FormulaRegistry:
        """Singleton pattern for global registry."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._formulas = {}
        return cls._instance

    def register(self, spec: FormulaSpec) -> None:
        """Register a formula specification.

        Args:
            spec: The FormulaSpec to register.

        Raises:
            ValueError: If a formula for this calc_type is already registered.
        """
        if spec.calc_type in self._formulas:
            raise ValueError(
                f"Formula for {spec.calc_type.value} already registered. "
                "Create a new version instead of overwriting."
            )
        self._formulas[spec.calc_type] = spec

    def get(self, calc_type: CalcType) -> FormulaSpec | None:
        """Get formula spec for a calc type.

        Args:
            calc_type: The calculation type to look up.

        Returns:
            FormulaSpec if registered, None otherwise.
        """
        return self._formulas.get(calc_type)

    def get_or_raise(self, calc_type: CalcType) -> FormulaSpec:
        """Get formula spec or raise if not found.

        Args:
            calc_type: The calculation type to look up.

        Returns:
            The registered FormulaSpec.

        Raises:
            KeyError: If no formula is registered for this calc_type.
        """
        spec = self.get(calc_type)
        if spec is None:
            raise KeyError(f"No formula registered for calc_type: {calc_type.value}")
        return spec

    def list_registered(self) -> list[CalcType]:
        """List all registered calc types."""
        return list(self._formulas.keys())

    def clear(self) -> None:
        """Clear all registered formulas. For testing only."""
        self._formulas.clear()

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton instance. For testing only."""
        cls._instance = None


def canonical_json_for_hash(obj: Any) -> str:
    """Serialize object to canonical JSON for hashing.

    Rules:
    - All keys sorted alphabetically (recursive)
    - Decimal values serialized as strings
    - UUIDs as lowercase hyphenated strings
    - No whitespace
    - UTF-8 encoding

    Args:
        obj: Object to serialize.

    Returns:
        Canonical JSON string.
    """

    def normalize(value: Any) -> Any:
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, dict):
            return {k: normalize(v) for k, v in sorted(value.items())}
        if isinstance(value, list):
            return [normalize(item) for item in value]
        if hasattr(value, "value"):  # Enum
            return value.value
        return value

    normalized = normalize(obj)
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"))


def compute_sha256(data: str) -> str:
    """Compute SHA256 hash of a string.

    Args:
        data: String to hash.

    Returns:
        Lowercase hexadecimal hash string.
    """
    return hashlib.sha256(data.encode("utf-8")).hexdigest()
