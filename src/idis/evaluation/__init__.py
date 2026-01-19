"""IDIS Evaluation Harness - GDBS benchmark suite runner.

This module provides the evaluation harness for running Golden Deal Benchmark Suite (GDBS)
tests with deterministic ordering and fail-closed semantics.

Exit codes:
    0 = PASS (all validations/executions succeeded)
    1 = FAIL (validation failed, dataset error, or execution error)
    2 = BLOCKED (execution blocked due to missing dependencies/endpoints)
"""

from idis.evaluation.types import (
    CaseResult,
    CaseStatus,
    ExitCode,
    GateStatus,
    GdbsCase,
    SuiteResult,
)

__all__ = [
    "CaseResult",
    "CaseStatus",
    "ExitCode",
    "GateStatus",
    "GdbsCase",
    "SuiteResult",
]
