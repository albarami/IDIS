"""RunStep model — tracks individual pipeline step execution within a Run.

Each RunStep records the lifecycle of a single orchestration step:
start, completion/failure, error details, and retry count.
Steps are ordered by a canonical step_order for deterministic iteration.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class StepName(StrEnum):
    """Canonical pipeline step names in execution order."""

    DATA_ROOM_INVENTORY_PACKAGE = "DATA_ROOM_INVENTORY_PACKAGE"
    DATA_ROOM_INGESTION_HANDOFF = "DATA_ROOM_INGESTION_HANDOFF"
    INGEST_CHECK = "INGEST_CHECK"
    DOCUMENT_PREFLIGHT = "DOCUMENT_PREFLIGHT"
    METHODOLOGY_COVERAGE_INIT = "METHODOLOGY_COVERAGE_INIT"
    METHODOLOGY_EXTRACTION_TASK_PLANNING = "METHODOLOGY_EXTRACTION_TASK_PLANNING"
    METHODOLOGY_EXTRACTION_TASK_EXECUTION = "METHODOLOGY_EXTRACTION_TASK_EXECUTION"
    METHODOLOGY_CLAIM_MATERIALIZATION = "METHODOLOGY_CLAIM_MATERIALIZATION"
    METHODOLOGY_EVIDENCE_ITEM_MATERIALIZATION = "METHODOLOGY_EVIDENCE_ITEM_MATERIALIZATION"
    METHODOLOGY_SANAD_CREATION_LINKING_GRADING = "METHODOLOGY_SANAD_CREATION_LINKING_GRADING"
    METHODOLOGY_DETERMINISTIC_CALCULATION = "METHODOLOGY_DETERMINISTIC_CALCULATION"
    METHODOLOGY_TRUTH_DASHBOARD = "METHODOLOGY_TRUTH_DASHBOARD"
    METHODOLOGY_EVIDENCE_TRUST_COURT = "METHODOLOGY_EVIDENCE_TRUST_COURT"
    METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE = "METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE"
    METHODOLOGY_EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN = (
        "METHODOLOGY_EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN"
    )
    METHODOLOGY_COMPANY_IDENTITY_PACKAGE = "METHODOLOGY_COMPANY_IDENTITY_PACKAGE"
    METHODOLOGY_LAYER2_READINESS_PACKAGE = "METHODOLOGY_LAYER2_READINESS_PACKAGE"
    EXTRACT = "EXTRACT"
    GRADE = "GRADE"
    CALC = "CALC"
    GRAPH_EVIDENCE = "GRAPH_EVIDENCE"
    ENRICHMENT = "ENRICHMENT"
    DEBATE = "DEBATE"
    ANALYSIS = "ANALYSIS"
    SCORING = "SCORING"
    DELIVERABLES = "DELIVERABLES"


class StepStatus(StrEnum):
    """Lifecycle status of a single pipeline step."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"


STEP_ORDER: dict[StepName, int] = {
    StepName.DATA_ROOM_INVENTORY_PACKAGE: 0,
    StepName.DATA_ROOM_INGESTION_HANDOFF: 1,
    StepName.INGEST_CHECK: 2,
    StepName.DOCUMENT_PREFLIGHT: 3,
    StepName.METHODOLOGY_COVERAGE_INIT: 4,
    StepName.METHODOLOGY_EXTRACTION_TASK_PLANNING: 5,
    StepName.METHODOLOGY_EXTRACTION_TASK_EXECUTION: 6,
    StepName.METHODOLOGY_CLAIM_MATERIALIZATION: 7,
    StepName.METHODOLOGY_EVIDENCE_ITEM_MATERIALIZATION: 8,
    StepName.METHODOLOGY_SANAD_CREATION_LINKING_GRADING: 9,
    StepName.METHODOLOGY_DETERMINISTIC_CALCULATION: 10,
    StepName.METHODOLOGY_TRUTH_DASHBOARD: 11,
    StepName.METHODOLOGY_EVIDENCE_TRUST_COURT: 12,
    StepName.METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE: 13,
    StepName.METHODOLOGY_EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN: 14,
    StepName.METHODOLOGY_COMPANY_IDENTITY_PACKAGE: 15,
    StepName.METHODOLOGY_LAYER2_READINESS_PACKAGE: 16,
    StepName.EXTRACT: 17,
    StepName.GRADE: 18,
    StepName.CALC: 19,
    StepName.GRAPH_EVIDENCE: 20,
    StepName.ENRICHMENT: 21,
    StepName.DEBATE: 22,
    StepName.ANALYSIS: 23,
    StepName.SCORING: 24,
    StepName.DELIVERABLES: 25,
}
"""Canonical ordering for deterministic step iteration."""

SNAPSHOT_STEPS: list[StepName] = [
    StepName.DATA_ROOM_INVENTORY_PACKAGE,
    StepName.DATA_ROOM_INGESTION_HANDOFF,
    StepName.INGEST_CHECK,
    StepName.DOCUMENT_PREFLIGHT,
    StepName.METHODOLOGY_COVERAGE_INIT,
    StepName.EXTRACT,
    StepName.GRADE,
    StepName.CALC,
]
"""Steps executed during a SNAPSHOT run."""

FULL_STEPS: list[StepName] = [
    StepName.DATA_ROOM_INVENTORY_PACKAGE,
    StepName.DATA_ROOM_INGESTION_HANDOFF,
    StepName.INGEST_CHECK,
    StepName.DOCUMENT_PREFLIGHT,
    StepName.METHODOLOGY_COVERAGE_INIT,
    StepName.METHODOLOGY_EXTRACTION_TASK_PLANNING,
    StepName.METHODOLOGY_EXTRACTION_TASK_EXECUTION,
    StepName.METHODOLOGY_CLAIM_MATERIALIZATION,
    StepName.METHODOLOGY_EVIDENCE_ITEM_MATERIALIZATION,
    StepName.METHODOLOGY_SANAD_CREATION_LINKING_GRADING,
    StepName.METHODOLOGY_DETERMINISTIC_CALCULATION,
    StepName.METHODOLOGY_TRUTH_DASHBOARD,
    StepName.METHODOLOGY_EVIDENCE_TRUST_COURT,
    StepName.METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE,
    StepName.METHODOLOGY_EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN,
    StepName.METHODOLOGY_COMPANY_IDENTITY_PACKAGE,
    StepName.METHODOLOGY_LAYER2_READINESS_PACKAGE,
    StepName.EXTRACT,
    StepName.GRADE,
    StepName.CALC,
    StepName.GRAPH_EVIDENCE,
    StepName.ENRICHMENT,
    StepName.DEBATE,
    StepName.ANALYSIS,
    StepName.SCORING,
    StepName.DELIVERABLES,
]
"""Steps executed during a FULL run."""

FULL_ONLY_STEPS: frozenset[StepName] = frozenset(
    {
        StepName.METHODOLOGY_EXTRACTION_TASK_PLANNING,
        StepName.METHODOLOGY_EXTRACTION_TASK_EXECUTION,
        StepName.METHODOLOGY_CLAIM_MATERIALIZATION,
        StepName.METHODOLOGY_EVIDENCE_ITEM_MATERIALIZATION,
        StepName.METHODOLOGY_SANAD_CREATION_LINKING_GRADING,
        StepName.METHODOLOGY_DETERMINISTIC_CALCULATION,
        StepName.METHODOLOGY_TRUTH_DASHBOARD,
        StepName.METHODOLOGY_EVIDENCE_TRUST_COURT,
        StepName.METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE,
        StepName.METHODOLOGY_EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN,
        StepName.METHODOLOGY_COMPANY_IDENTITY_PACKAGE,
        StepName.METHODOLOGY_LAYER2_READINESS_PACKAGE,
        StepName.ENRICHMENT,
        StepName.DEBATE,
        StepName.ANALYSIS,
        StepName.SCORING,
        StepName.DELIVERABLES,
        StepName.GRAPH_EVIDENCE,
    }
)
"""Steps that execute only in FULL mode, never in SNAPSHOT."""

IMPLEMENTED_STEPS: frozenset[StepName] = frozenset(
    {
        StepName.DATA_ROOM_INVENTORY_PACKAGE,
        StepName.DATA_ROOM_INGESTION_HANDOFF,
        StepName.INGEST_CHECK,
        StepName.DOCUMENT_PREFLIGHT,
        StepName.METHODOLOGY_COVERAGE_INIT,
        StepName.METHODOLOGY_EXTRACTION_TASK_PLANNING,
        StepName.METHODOLOGY_EXTRACTION_TASK_EXECUTION,
        StepName.METHODOLOGY_CLAIM_MATERIALIZATION,
        StepName.METHODOLOGY_EVIDENCE_ITEM_MATERIALIZATION,
        StepName.METHODOLOGY_SANAD_CREATION_LINKING_GRADING,
        StepName.METHODOLOGY_DETERMINISTIC_CALCULATION,
        StepName.METHODOLOGY_TRUTH_DASHBOARD,
        StepName.METHODOLOGY_EVIDENCE_TRUST_COURT,
        StepName.METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE,
        StepName.METHODOLOGY_EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN,
        StepName.METHODOLOGY_COMPANY_IDENTITY_PACKAGE,
        StepName.METHODOLOGY_LAYER2_READINESS_PACKAGE,
        StepName.EXTRACT,
        StepName.GRADE,
        StepName.CALC,
        StepName.GRAPH_EVIDENCE,
        StepName.ENRICHMENT,
        StepName.DEBATE,
        StepName.ANALYSIS,
        StepName.SCORING,
        StepName.DELIVERABLES,
    }
)
"""Steps with working implementations. Unimplemented steps trigger BLOCKED."""


class StepError(BaseModel):
    """Structured error envelope persisted on step failure.

    Attributes:
        code: Stable machine-readable error code.
        message: Human-readable description.
    """

    code: str
    message: str


class RunStep(BaseModel):
    """Single pipeline step record within a Run.

    Attributes:
        step_id: Unique UUID for this step execution.
        run_id: Parent run UUID.
        tenant_id: Tenant scope for isolation.
        step_name: Canonical step name enum.
        step_order: Integer for deterministic ordering.
        status: Current lifecycle status.
        started_at: ISO timestamp when step began.
        finished_at: ISO timestamp when step ended.
        error_code: Stable error code on failure.
        error_message: Human-readable error on failure.
        retry_count: Number of retry attempts so far.
        result_summary: Step-specific output data for downstream consumption.
    """

    step_id: str
    run_id: str
    tenant_id: str
    step_name: StepName
    step_order: int
    status: StepStatus = StepStatus.PENDING
    started_at: str | None = None
    finished_at: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    retry_count: int = 0
    result_summary: dict[str, Any] = Field(default_factory=dict)
