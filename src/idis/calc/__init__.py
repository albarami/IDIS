"""IDIS Deterministic Calculation Engine.

Phase 4.1: Calc engine framework with Calc-Sanad provenance.

This package provides:
- CalcEngine: Run deterministic calculations with full provenance
- FormulaRegistry: Versioned formula specifications with stable hashes
- Exceptions: Typed exceptions for fail-closed validation
"""

from idis.calc.engine import (
    CalcEngine,
    CalcEngineResult,
    CalcIntegrityError,
    CalcMissingInputError,
    CalcUnsupportedValueError,
)
from idis.calc.formulas.registry import FormulaRegistry, FormulaSpec

__all__ = [
    "CalcEngine",
    "CalcEngineResult",
    "CalcIntegrityError",
    "CalcMissingInputError",
    "CalcUnsupportedValueError",
    "FormulaRegistry",
    "FormulaSpec",
]
