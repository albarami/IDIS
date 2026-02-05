"""Evaluation harness types and exit code semantics.

Exit codes (aligned with Gate 3 blocked convention):
    0 = PASS
    1 = FAIL
    2 = BLOCKED
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum, StrEnum
from typing import Any, Literal


class ExitCode(int, Enum):
    """CLI exit codes for evaluation harness."""

    PASS = 0
    FAIL = 1
    BLOCKED = 2


class GateStatus(StrEnum):
    """Status for gate evaluation results."""

    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"

    def to_exit_code(self) -> ExitCode:
        """Convert gate status to exit code."""
        return {
            GateStatus.PASS: ExitCode.PASS,
            GateStatus.FAIL: ExitCode.FAIL,
            GateStatus.BLOCKED: ExitCode.BLOCKED,
        }[self]


class CaseStatus(StrEnum):
    """Status for individual case execution."""

    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    SKIPPED = "SKIPPED"


SuiteId = Literal["gdbs-s", "gdbs-f", "gdbs-a"]

VALID_SUITE_IDS: frozenset[SuiteId] = frozenset({"gdbs-s", "gdbs-f", "gdbs-a"})

SUITE_DESCRIPTIONS: dict[SuiteId, str] = {
    "gdbs-s": "GDBS-S (Screening): 20 deals, quick regression",
    "gdbs-f": "GDBS-F (Full): 100 deals, broad coverage",
    "gdbs-a": "GDBS-A (Adversarial): 30 deals, injected failures",
}


@dataclass
class GdbsCase:
    """A single case (deal) in the GDBS benchmark suite."""

    case_id: str
    deal_id: str
    deal_key: str
    scenario: str
    directory: str
    description: str
    expected_outcome_path: str | None = None

    def sort_key(self) -> tuple[str, str]:
        """Return deterministic sort key for stable ordering."""
        return (self.deal_id, self.case_id)


@dataclass
class CaseResult:
    """Result of executing a single benchmark case."""

    case_id: str
    deal_id: str
    status: CaseStatus
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    blockers: list[str] = field(default_factory=list)
    execution_time_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict with deterministic key ordering."""
        return {
            "blockers": sorted(self.blockers),
            "case_id": self.case_id,
            "deal_id": self.deal_id,
            "errors": sorted(self.errors),
            "execution_time_ms": self.execution_time_ms,
            "metrics": dict(sorted(self.metrics.items())),
            "status": self.status.value,
            "warnings": sorted(self.warnings),
        }


@dataclass
class SuiteResult:
    """Result of running a benchmark suite."""

    suite_id: SuiteId
    status: GateStatus
    mode: Literal["validate", "execute"]
    started_at: str
    finished_at: str
    dataset_hash: str
    cases: list[CaseResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def now_iso() -> str:
        """Return current UTC time as ISO string."""
        return datetime.now(UTC).isoformat()

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict with deterministic key ordering for JSON serialization."""
        cases_sorted = sorted(self.cases, key=lambda c: (c.deal_id, c.case_id))
        return {
            "blockers": sorted(self.blockers),
            "cases": [c.to_dict() for c in cases_sorted],
            "dataset_hash": self.dataset_hash,
            "errors": sorted(self.errors),
            "finished_at": self.finished_at,
            "metrics": dict(sorted(self.metrics.items())),
            "mode": self.mode,
            "started_at": self.started_at,
            "status": self.status.value,
            "suite_id": self.suite_id,
        }


@dataclass
class LoadResult:
    """Result of loading a GDBS dataset."""

    success: bool
    cases: list[GdbsCase] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    dataset_hash: str = ""
    manifest_version: str = ""
    dataset_id: str = ""
