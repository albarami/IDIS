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

from idis.api.openapi_loader import OpenAPILoadError, load_openapi_spec

HARNESS_DEAL_STAGE_REQUIRED = "HARNESS_DEAL_STAGE_REQUIRED"
HARNESS_DEAL_STAGE_INVALID = "HARNESS_DEAL_STAGE_INVALID"
HARNESS_OPENAPI_UNAVAILABLE = "HARNESS_OPENAPI_UNAVAILABLE"
_DEAL_STAGE_SCHEMA_NAME = "DealStage"


class HarnessContractError(Exception):
    """Fail-closed harness input error. The message is the stable code only."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def deal_stage_for_request(value: object) -> str:
    """Return ``value`` only when it is an exact OpenAPI ``DealStage`` member.

    ``None``, a non-string, and a blank string are missing. Any other string
    that is not an exact enum member is invalid. The exception carries the
    stable code only and does not echo the supplied value.

    Args:
        value: Stage text the operator or dataset case supplied.

    Returns:
        The same string, unchanged, when it is an exact ``DealStage`` member.

    Raises:
        HarnessContractError: ``HARNESS_DEAL_STAGE_REQUIRED``,
            ``HARNESS_DEAL_STAGE_INVALID``, or ``HARNESS_OPENAPI_UNAVAILABLE``.
            A missing OpenAPI document becomes ``HARNESS_OPENAPI_UNAVAILABLE``
            with no path in the message or exception chain.
    """
    if not isinstance(value, str) or not value.strip():
        raise HarnessContractError(HARNESS_DEAL_STAGE_REQUIRED)
    allowed: frozenset[str] | None
    try:
        allowed = _openapi_deal_stage_values()
    except OpenAPILoadError:
        allowed = None
    if allowed is None:
        raise HarnessContractError(HARNESS_OPENAPI_UNAVAILABLE) from None
    if value not in allowed:
        raise HarnessContractError(HARNESS_DEAL_STAGE_INVALID)
    return value


def _openapi_deal_stage_values() -> frozenset[str]:
    spec = load_openapi_spec()
    components = spec.get("components")
    schemas = components.get("schemas") if isinstance(components, dict) else None
    schema = schemas.get(_DEAL_STAGE_SCHEMA_NAME) if isinstance(schemas, dict) else None
    raw_enum = schema.get("enum") if isinstance(schema, dict) else None
    if not isinstance(raw_enum, list) or not raw_enum:
        raise HarnessContractError(HARNESS_DEAL_STAGE_INVALID)
    values = [item for item in raw_enum if isinstance(item, str) and item]
    if len(values) != len(raw_enum):
        raise HarnessContractError(HARNESS_DEAL_STAGE_INVALID)
    return frozenset(values)


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
