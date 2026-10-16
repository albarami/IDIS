"""RunOrchestrator — pure orchestration logic for pipeline step execution.

Executes pipeline steps in canonical order, records each step in the
RunStep ledger, emits audit events at every transition, and enforces
fail-closed semantics on audit failures.

SNAPSHOT: INGEST_CHECK -> DOCUMENT_PREFLIGHT -> METHODOLOGY_COVERAGE_INIT -> EXTRACT
          -> GRADE -> CALC.
FULL: INGEST_CHECK -> DOCUMENT_PREFLIGHT -> METHODOLOGY_COVERAGE_INIT -> EXTRACT
      -> GRADE -> CALC -> GRAPH_EVIDENCE -> RAG_EVIDENCE -> ENRICHMENT -> DEBATE
      -> ANALYSIS -> SCORING -> DELIVERABLES.

No FastAPI globals. All dependencies injected via constructor or execute().
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel

from idis.audit.sink import AuditSink, AuditSinkError
from idis.methodology.models import MethodologyRegistry
from idis.models.calc_materialization import (
    MethodologyCalculationRunResult,
    RunScopedCalcSanadRecord,
    RunScopedCalcSanadShell,
    RunScopedCalculationShell,
    RunScopedDeterministicCalculationRecord,
)
from idis.models.calc_sanad import SanadGrade as CalcSanadGrade
from idis.models.claim_materialization import (
    MaterializedClaimSourceRef,
    RunScopedMaterializedClaim,
    RunScopedMaterializedClaimShell,
)
from idis.models.company_identity_package_materialization import (
    MethodologyCompanyIdentityPackageConstructionStatus,
    MethodologyCompanyIdentityPackageRunResult,
    MethodologyCompanyIdentityStatus,
    RunScopedCompanyIdentityPackageRecord,
    RunScopedCompanyIdentityPackageShell,
)
from idis.models.data_room_ingestion_handoff import DataRoomIngestionHandoffRunResult
from idis.models.data_room_inventory_package_materialization import (
    DataRoomInventoryPackageConstructionStatus,
    DataRoomInventoryPackageRunResult,
    RunScopedDataRoomInventoryPackageRecord,
    RunScopedDataRoomInventoryPackageShell,
)
from idis.models.defect import CureProtocol, DefectSeverity, DefectStatus, DefectType
from idis.models.deterministic_calculation import CalcType
from idis.models.document_preflight import DocumentPreflightResult
from idis.models.evidence_item_materialization import (
    RunScopedEvidenceItemRecord,
    RunScopedEvidenceItemShell,
    RunScopedEvidenceProvenanceRef,
)
from idis.models.evidence_trust_court_materialization import (
    MethodologyEvidenceTrustCourtRunResult,
    RunScopedEvidenceTrustCourtRecord,
    RunScopedEvidenceTrustCourtShell,
    RunScopedEvidenceTrustCourtSummary,
)
from idis.models.external_intelligence_conflict_check_plan_materialization import (
    ExternalIntelligencePlanCheckStatus,
    MethodologyExternalIntelligenceConflictCheckPlanRunResult,
    MethodologyExternalIntelligenceConflictCheckPlanStatus,
    RunScopedExternalIntelligenceConflictCheckPlanRecord,
    RunScopedExternalIntelligenceConflictCheckPlanShell,
    RunScopedExternalIntelligenceConflictCheckPlanSummary,
)
from idis.models.extraction_execution import (
    MethodologyExtractionExecutionReason,
    MethodologyExtractionExecutionResult,
    MethodologyExtractionExecutionStatus,
    MethodologyExtractionExecutionSummary,
    MethodologyTaskExecutionResult,
    MethodologyTaskExecutionStatus,
)
from idis.models.extraction_task import ExtractionTaskPlanningRunResult, PlannedExtractionTask
from idis.models.layer2_readiness_package_materialization import (
    MethodologyLayer2ReadinessPackageConstructionStatus,
    MethodologyLayer2ReadinessPackageRunResult,
    MethodologyLayer2ReadinessStatus,
    RunScopedLayer2ReadinessPackageRecord,
    RunScopedLayer2ReadinessPackageShell,
    RunScopedLayer2ReadinessPackageSummary,
)
from idis.models.methodology_coverage import (
    DurableMethodologyCoverageInitializationResult,
    MethodologyCoverageInitializationResult,
    MethodologyCoverageRecord,
)
from idis.models.run_step import (
    FULL_STEPS,
    IMPLEMENTED_STEPS,
    SNAPSHOT_STEPS,
    STEP_ORDER,
    RunStep,
    StepName,
    StepStatus,
)
from idis.models.sanad import SanadGrade
from idis.models.sanad_materialization import (
    RunScopedSanadDefectRecord,
    RunScopedSanadDefectShell,
    RunScopedSanadGradeRecord,
    RunScopedSanadLinkRecord,
    RunScopedSanadRecord,
    RunScopedSanadShell,
)
from idis.models.truth_dashboard_materialization import (
    MethodologyTruthDashboardRunResult,
    RunScopedTruthDashboardRecord,
    RunScopedTruthDashboardShell,
)
from idis.models.validated_evidence_package_materialization import (
    MethodologyValidatedEvidencePackageRunResult,
    MethodologyValidatedEvidencePackageStatus,
    RunScopedValidatedEvidencePackageRecord,
    RunScopedValidatedEvidencePackageShell,
    RunScopedValidatedEvidencePackageSummary,
)
from idis.observability.runtime_signals import RUN_CANCELLED as RUN_CANCELLED_EVENT
from idis.observability.runtime_signals import emit_run_signal
from idis.persistence.repositories.run_steps import RunStepsRepo
from idis.pipeline.contract_registry import (
    PIPELINE_CONTRACTS,
    PipelineContractError,
    PipelineContractSpec,
    require_pipeline_contract_for_admission,
)
from idis.validators.audit_event_validator import validate_audit_event

logger = logging.getLogger(__name__)

BLOCK_REASON_DEBATE_NOT_IMPLEMENTED = "DEBATE_NOT_IMPLEMENTED"
BLOCK_REASON_NO_INGESTED_DOCUMENTS = "NO_INGESTED_DOCUMENTS"
BLOCK_REASON_NO_USABLE_DOCUMENTS = "NO_USABLE_DOCUMENTS"
BLOCK_REASON_NO_ELIGIBLE_EXTRACTION_TASKS = "NO_ELIGIBLE_EXTRACTION_TASKS"
BLOCK_REASON_NO_PLANNED_EXTRACTION_TASKS = "NO_PLANNED_EXTRACTION_TASKS"
SAFE_STEP_ERROR_MESSAGE = "Run step failed; see error code for details."
SENSITIVE_AUDIT_RESULT_KEY_PARTS = frozenset(
    {
        "base64",
        "bytes",
        "excerpt",
        "file_content",
        "header",
        "path",
        "raw",
        "span",
        "text",
        "transcript",
        "uri",
    }
)


def canonical_result_summary_sha256(result_summary: dict[str, Any]) -> str:
    """Hash the canonical JSON form persisted in ``RunStep.result_summary``."""
    canonical = json.dumps(result_summary, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class RunStepBlockedError(ValueError):
    """Machine-readable fail-closed run step blocker."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        result_summary: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.result_summary = result_summary


@dataclass
class OrchestratorResult:
    """Aggregate result of a pipeline orchestration run.

    Attributes:
        status: Final run status (SUCCEEDED, FAILED) per DB constraint.
        steps: All RunStep records in canonical order.
        block_reason: Diagnostic reason code when run failed due to a blocked step.
        error_code: Top-level error code on failure.
        error_message: Top-level error message on failure.
    """

    status: str
    steps: list[RunStep] = field(default_factory=list)
    block_reason: str | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass
class RunContext:
    """All inputs needed by the orchestrator to execute a run.

    Attributes:
        run_id: Pipeline run UUID.
        tenant_id: Tenant scope.
        deal_id: Deal UUID.
        mode: SNAPSHOT or FULL.
        documents: Extraction-ready parsed document dicts.
        preflight_corpus: Full persisted corpus, including failed/no-span rows.
        methodology_coverage_records: Initialized run-scoped coverage records.
        extract_fn: Callable that executes extraction, returns result dict.
        grade_fn: Callable that executes grading, returns summary dict.
        calc_fn: Optional callable that executes calculations, returns result dict.
        calc_types: Optional list of CalcType to run. None means run all registered.
    """

    run_id: str
    tenant_id: str
    deal_id: str
    mode: str
    documents: list[dict[str, Any]]
    extract_fn: Callable[..., dict[str, Any]]
    grade_fn: Callable[..., dict[str, Any]]
    pipeline_contract_version: str | None = None
    pipeline_contract_hash: str | None = None
    pipeline_resume_state: str | None = None
    created_by_actor_id: str | None = None
    created_by_actor_type: str | None = None
    strict_preflight_proof_id: str | None = None
    deal_metadata: dict[str, Any] | None = None
    data_room_root_path: str | Path | None = None
    preflight_corpus: list[dict[str, Any]] = field(default_factory=list)
    document_preflight_summary: dict[str, Any] | None = None
    data_room_inventory_fn: (
        Callable[
            ...,
            tuple[
                DataRoomInventoryPackageRunResult,
                list[RunScopedDataRoomInventoryPackageRecord],
                list[dict[str, Any]],
            ],
        ]
        | None
    ) = None
    data_room_inventory_package: (
        RunScopedDataRoomInventoryPackageRecord | RunScopedDataRoomInventoryPackageShell | None
    ) = None
    data_room_ingestion_handoff_fn: (
        Callable[..., tuple[DataRoomIngestionHandoffRunResult, list[dict[str, Any]]]] | None
    ) = None
    document_preflight_fn: (
        Callable[..., tuple[DocumentPreflightResult, list[dict[str, Any]]]] | None
    ) = None
    no_substantive_content_receipt_verifier: Callable[[Mapping[str, Any]], bool] | None = None
    methodology_registry: MethodologyRegistry | None = None
    methodology_registry_loader_fn: Callable[[], MethodologyRegistry] | None = None
    methodology_coverage_init_fn: (
        Callable[
            ...,
            tuple[
                MethodologyCoverageInitializationResult
                | DurableMethodologyCoverageInitializationResult,
                list[MethodologyCoverageRecord],
            ],
        ]
        | None
    ) = None
    methodology_coverage_rehydrate_fn: Callable[..., list[MethodologyCoverageRecord]] | None = None
    methodology_coverage_records: list[MethodologyCoverageRecord] = field(default_factory=list)
    methodology_extraction_task_planning_fn: Callable[..., Any] | None = None
    methodology_extraction_tasks: list[PlannedExtractionTask] = field(default_factory=list)
    methodology_extraction_task_execution_fn: Callable[..., Any] | None = None
    methodology_extraction_execution_result: MethodologyExtractionExecutionResult | None = None
    methodology_claim_materialization_fn: Callable[..., Any] | None = None
    methodology_materialized_claims: list[Any] | None = None
    methodology_canonical_claim_ids: tuple[str, ...] = ()
    active_run_step_id: str | None = None
    methodology_pending_native_output_writes: dict[StepName, tuple[Any, ...]] = field(
        default_factory=dict
    )
    methodology_evidence_item_materialization_fn: Callable[..., Any] | None = None
    methodology_evidence_items: list[Any] | None = None
    methodology_evidence_source_provenance: list[RunScopedEvidenceProvenanceRef] = field(
        default_factory=list
    )
    methodology_sanad_creation_linking_grading_fn: Callable[..., Any] | None = None
    methodology_sanads: list[RunScopedSanadRecord | RunScopedSanadShell] = field(
        default_factory=list
    )
    methodology_sanad_links: list[RunScopedSanadLinkRecord] = field(default_factory=list)
    methodology_sanad_grades: list[RunScopedSanadGradeRecord] | None = None
    methodology_sanad_defects: list[RunScopedSanadDefectRecord | RunScopedSanadDefectShell] = field(
        default_factory=list
    )
    methodology_deterministic_calculation_fn: (
        Callable[
            ...,
            tuple[
                MethodologyCalculationRunResult,
                list[RunScopedDeterministicCalculationRecord],
                list[RunScopedCalcSanadRecord],
            ],
        ]
        | None
    ) = None
    methodology_calculations: list[
        RunScopedDeterministicCalculationRecord | RunScopedCalculationShell
    ] = field(default_factory=list)
    methodology_calc_sanads: list[RunScopedCalcSanadRecord | RunScopedCalcSanadShell] = field(
        default_factory=list
    )
    methodology_truth_dashboard_fn: (
        Callable[
            ...,
            tuple[
                MethodologyTruthDashboardRunResult,
                list[RunScopedTruthDashboardRecord],
            ],
        ]
        | None
    ) = None
    methodology_truth_dashboard: (
        RunScopedTruthDashboardRecord | RunScopedTruthDashboardShell | None
    ) = None
    methodology_evidence_trust_court_fn: (
        Callable[
            ...,
            tuple[
                MethodologyEvidenceTrustCourtRunResult,
                list[RunScopedEvidenceTrustCourtRecord],
            ],
        ]
        | None
    ) = None
    methodology_evidence_trust_court: (
        RunScopedEvidenceTrustCourtRecord | RunScopedEvidenceTrustCourtShell | None
    ) = None
    methodology_validated_evidence_package_fn: (
        Callable[
            ...,
            tuple[
                MethodologyValidatedEvidencePackageRunResult,
                list[RunScopedValidatedEvidencePackageRecord],
            ],
        ]
        | None
    ) = None
    # Durable Layer-1 evidence repository (Slice92). ``None`` falls back to the
    # in-memory twin so non-database runs stay green; injected Postgres repos make
    # court findings, Muhasabah records, and the VEP candidate durable.
    layer1_evidence_repository: Any = None
    methodology_validated_evidence_package: (
        RunScopedValidatedEvidencePackageRecord | RunScopedValidatedEvidencePackageShell | None
    ) = None
    methodology_external_intelligence_conflict_check_plan_fn: (
        Callable[
            ...,
            tuple[
                MethodologyExternalIntelligenceConflictCheckPlanRunResult,
                list[RunScopedExternalIntelligenceConflictCheckPlanRecord],
            ],
        ]
        | None
    ) = None
    methodology_external_intelligence_conflict_check_plan: (
        RunScopedExternalIntelligenceConflictCheckPlanRecord
        | RunScopedExternalIntelligenceConflictCheckPlanShell
        | None
    ) = None
    methodology_company_identity_package_fn: (
        Callable[
            ...,
            tuple[
                MethodologyCompanyIdentityPackageRunResult,
                list[RunScopedCompanyIdentityPackageRecord],
            ],
        ]
        | None
    ) = None
    methodology_company_identity_package: (
        RunScopedCompanyIdentityPackageRecord | RunScopedCompanyIdentityPackageShell | None
    ) = None
    methodology_layer2_readiness_package_fn: (
        Callable[
            ...,
            tuple[
                MethodologyLayer2ReadinessPackageRunResult,
                list[RunScopedLayer2ReadinessPackageRecord],
            ],
        ]
        | None
    ) = None
    methodology_layer2_readiness_package: (
        RunScopedLayer2ReadinessPackageRecord | RunScopedLayer2ReadinessPackageShell | None
    ) = None
    calc_fn: Callable[..., dict[str, Any]] | None = None
    calc_types: list[CalcType] | None = None
    graph_fn: Callable[..., dict[str, Any]] | None = None
    rag_fn: Callable[..., dict[str, Any]] | None = None
    enrich_fn: Callable[..., dict[str, Any]] | None = None
    debate_fn: Callable[..., dict[str, Any]] | None = None
    layer2_ic_challenge_fn: Callable[..., dict[str, Any]] | None = None
    layer2_ic_challenge_record: Any = None
    analysis_fn: Callable[..., dict[str, Any]] | None = None
    scoring_fn: Callable[..., dict[str, Any]] | None = None
    deliverables_fn: Callable[..., dict[str, Any]] | None = None
    is_cancellation_requested_fn: Callable[[str, str], bool] | None = None
    strict_resume_validation_required: bool = False
    completed_step_resume_validator: Callable[[RunContext, RunStep], bool] | None = None
    lease_owner_id: str | None = None
    lease_fencing_token: int | None = None
    step_boundary_uow_fn: Callable[..., None] | None = None
    after_step_boundary_commit_fn: Callable[..., None] | None = None
    execute_with_lease_heartbeat_fn: Callable[..., Any] | None = None
    stage_native_output_writes_fn: Callable[..., tuple[Any, ...]] | None = None
    provider_output_guard_fn: Callable[..., None] | None = None
    task7_step_completion_fn: Callable[..., None] | None = None


class CancellationRunsRepository(Protocol):
    """Repository surface for cancellation checks during orchestration."""

    def get(self, run_id: str) -> dict[str, object] | None:
        """Return persisted run row for cancellation/status checks."""


class RunOrchestrator:
    """Orchestrates pipeline steps with durable step ledger and audit emissions.

    Fail-closed: any audit emission failure aborts the run immediately.
    Tenant-scoped: all step reads/writes go through a tenant-scoped repository.
    Stable ordering: steps are always processed and returned in canonical order.

    Args:
        audit_sink: Audit event sink (required).
        run_steps_repo: Tenant-scoped RunStep repository.
    """

    def __init__(
        self,
        *,
        audit_sink: AuditSink,
        run_steps_repo: RunStepsRepo,
        runs_repo: CancellationRunsRepository | None = None,
        pipeline_contract_registry: Mapping[str, PipelineContractSpec] = PIPELINE_CONTRACTS,
    ) -> None:
        """Initialize the orchestrator.

        Args:
            audit_sink: Audit sink for event emission.
            run_steps_repo: Tenant-scoped step repository.
        """
        self._audit = audit_sink
        self._steps_repo = run_steps_repo
        self._runs_repo = runs_repo
        self._pipeline_contract_registry = pipeline_contract_registry
        self._origin_actor_id: str | None = None
        self._origin_actor_type: str | None = None
        self._origin_deal_id: str | None = None

    def execute(self, ctx: RunContext) -> OrchestratorResult:
        """Execute all pipeline steps for the given run context.

        For SNAPSHOT: INGEST_CHECK -> DOCUMENT_PREFLIGHT -> METHODOLOGY_COVERAGE_INIT
                      -> EXTRACT -> GRADE -> CALC.
        For FULL: INGEST_CHECK -> DOCUMENT_PREFLIGHT -> METHODOLOGY_COVERAGE_INIT
                  -> EXTRACT -> GRADE -> CALC -> GRAPH_EVIDENCE
                  -> ENRICHMENT -> DEBATE -> ANALYSIS -> SCORING -> DELIVERABLES.

        Skips steps that are already COMPLETED (idempotent resume).
        Fails closed on audit emission errors.

        Args:
            ctx: RunContext with all execution inputs.

        Returns:
            OrchestratorResult with final status and step records.

        Raises:
            AuditSinkError: Propagated when audit emission fails (fail-closed).
        """
        self._origin_actor_id = ctx.created_by_actor_id
        self._origin_actor_type = ctx.created_by_actor_type
        self._origin_deal_id = ctx.deal_id

        step_sequence = self._resolve_pipeline_step_sequence(ctx)
        accumulated: dict[str, Any] = {}

        for step_name in step_sequence:
            if self._is_cancellation_requested(ctx):
                return self._cancelled_result(ctx)

            if step_name not in IMPLEMENTED_STEPS:
                self._create_blocked_step(ctx, step_name)
                self._emit_audit_event(
                    event_type="run.step.blocked",
                    tenant_id=ctx.tenant_id,
                    details={
                        "run_id": ctx.run_id,
                        "step_name": step_name.value,
                        "block_reason": BLOCK_REASON_DEBATE_NOT_IMPLEMENTED,
                    },
                )
                all_steps = self._steps_repo.get_by_run_id(ctx.run_id)
                return OrchestratorResult(
                    status="FAILED",
                    steps=all_steps,
                    block_reason=BLOCK_REASON_DEBATE_NOT_IMPLEMENTED,
                )

            existing = self._steps_repo.get_step(ctx.run_id, step_name)
            if existing is not None and existing.status == StepStatus.COMPLETED:
                if ctx.strict_resume_validation_required:
                    try:
                        self._validate_completed_step_for_resume(ctx, existing)
                    except AuditSinkError:
                        raise
                    except Exception as exc:
                        if self._is_full_strict_v21_context(ctx):
                            all_steps = self._steps_repo.get_by_run_id(ctx.run_id)
                            code = getattr(exc, "code", "STEP_REHYDRATION_FAILED")
                            return OrchestratorResult(
                                status="FAILED",
                                steps=all_steps,
                                block_reason=code,
                                error_code=code,
                                error_message="Completed step rehydration failed",
                            )
                        self._fail_completed_resume_boundary(ctx, existing, exc)
                        all_steps = self._steps_repo.get_by_run_id(ctx.run_id)
                        block_reason = (
                            existing.error_code if isinstance(exc, RunStepBlockedError) else None
                        )
                        return OrchestratorResult(
                            status="FAILED",
                            steps=all_steps,
                            block_reason=block_reason,
                            error_code=existing.error_code,
                            error_message=existing.error_message,
                        )
                accumulated.update(existing.result_summary)
                if step_name == StepName.DATA_ROOM_INVENTORY_PACKAGE:
                    self._rehydrate_data_room_inventory_package(ctx, existing.result_summary)
                if step_name == StepName.DOCUMENT_PREFLIGHT:
                    self._rehydrate_document_preflight_summary(ctx, existing.result_summary)
                if step_name == StepName.METHODOLOGY_COVERAGE_INIT:
                    self._rehydrate_methodology_coverage_records(ctx)
                if step_name == StepName.METHODOLOGY_EXTRACTION_TASK_PLANNING:
                    self._rehydrate_methodology_extraction_tasks(ctx, accumulated)
                if step_name == StepName.METHODOLOGY_EXTRACTION_TASK_EXECUTION:
                    self._rehydrate_methodology_extraction_execution(ctx, existing.result_summary)
                if step_name == StepName.METHODOLOGY_CLAIM_MATERIALIZATION:
                    self._rehydrate_methodology_materialized_claims(ctx, existing.result_summary)
                if step_name == StepName.METHODOLOGY_EVIDENCE_ITEM_MATERIALIZATION:
                    self._rehydrate_methodology_evidence_items(ctx, existing.result_summary)
                if step_name == StepName.METHODOLOGY_SANAD_CREATION_LINKING_GRADING:
                    self._rehydrate_methodology_sanads(ctx, existing.result_summary)
                if step_name == StepName.METHODOLOGY_DETERMINISTIC_CALCULATION:
                    self._rehydrate_methodology_calculations(ctx, existing.result_summary)
                if step_name == StepName.METHODOLOGY_TRUTH_DASHBOARD:
                    self._rehydrate_methodology_truth_dashboard(ctx, existing.result_summary)
                if step_name == StepName.METHODOLOGY_EVIDENCE_TRUST_COURT:
                    self._rehydrate_methodology_evidence_trust_court(
                        ctx,
                        existing.result_summary,
                    )
                if step_name == StepName.METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE:
                    self._rehydrate_methodology_validated_evidence_package(
                        ctx,
                        existing.result_summary,
                    )
                if step_name == StepName.METHODOLOGY_EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN:
                    self._rehydrate_methodology_external_intelligence_conflict_check_plan(
                        ctx,
                        existing.result_summary,
                    )
                if step_name == StepName.METHODOLOGY_COMPANY_IDENTITY_PACKAGE:
                    self._rehydrate_methodology_company_identity_package(
                        ctx,
                        existing.result_summary,
                    )
                if step_name == StepName.METHODOLOGY_LAYER2_READINESS_PACKAGE:
                    self._rehydrate_methodology_layer2_readiness_package(
                        ctx,
                        existing.result_summary,
                    )
                continue

            step = self._start_step_boundary(ctx, step_name, existing)
            ctx.active_run_step_id = step.step_id

            try:

                def operation(
                    current_step: StepName = step_name,
                    current_accumulated: dict[str, Any] = accumulated,
                ) -> dict[str, Any]:
                    return self._dispatch_guarded_step(current_step, ctx, current_accumulated)

                if ctx.execute_with_lease_heartbeat_fn is not None:
                    result = ctx.execute_with_lease_heartbeat_fn(
                        step_name=step_name,
                        operation=operation,
                    )
                else:
                    result = operation()
            except AuditSinkError:
                raise
            except Exception as exc:
                if self._is_lease_control_failure(exc):
                    raise
                if self._is_cancellation_requested(ctx):
                    return self._cancelled_result(ctx)
                self._fail_step_boundary(ctx, step, exc)
                all_steps = self._steps_repo.get_by_run_id(ctx.run_id)
                block_reason = step.error_code if isinstance(exc, RunStepBlockedError) else None
                return OrchestratorResult(
                    status="FAILED",
                    steps=all_steps,
                    block_reason=block_reason,
                    error_code=step.error_code,
                    error_message=step.error_message,
                )

            # Cancellation clears the active lease.  Recheck after provider work
            # and before any completion/output write so the in-flight result is
            # discarded instead of attempting a now-fenced late mutation.
            if self._is_cancellation_requested(ctx):
                return self._cancelled_result(ctx)
            self._complete_step_boundary(ctx, step, result)
            ctx.active_run_step_id = None
            accumulated.update(result)
            if self._is_cancellation_requested(ctx):
                return self._cancelled_result(ctx)

        all_steps = self._steps_repo.get_by_run_id(ctx.run_id)
        final_status = self._compute_final_status(
            all_steps,
            expected_step_sequence=(
                step_sequence if self._is_full_strict_v21_context(ctx) else None
            ),
        )
        return OrchestratorResult(status=final_status, steps=all_steps)

    @staticmethod
    def _is_full_strict_v21_context(ctx: RunContext) -> bool:
        """Return whether this claimed run is bound to the frozen v20 contract."""
        return ctx.pipeline_contract_version == "full-strict-v21"

    def _resolve_pipeline_step_sequence(self, ctx: RunContext) -> tuple[StepName, ...]:
        """Resolve a claimed run's immutable contract-local step ordering."""
        if (
            ctx.pipeline_contract_version is None
            and ctx.pipeline_contract_hash is None
            and ctx.pipeline_resume_state is None
        ):
            legacy = FULL_STEPS if ctx.mode == "FULL" else SNAPSHOT_STEPS
            return tuple(legacy)
        if (
            ctx.pipeline_contract_version is None
            or ctx.pipeline_contract_hash is None
            or ctx.pipeline_resume_state is None
        ):
            raise RunStepBlockedError(
                "PIPELINE_CONTRACT_UNAVAILABLE",
                "Claimed run has an incomplete pipeline contract identity",
            )
        if ctx.pipeline_resume_state != "RESUMABLE":
            raise RunStepBlockedError(
                "STEP_REHYDRATION_FAILED",
                "Claimed run is not resumable under its pipeline contract",
            )
        try:
            contract = require_pipeline_contract_for_admission(
                registry=self._pipeline_contract_registry,
                contract_version=ctx.pipeline_contract_version,
                proof_contract_hash=ctx.pipeline_contract_hash,
            )
            return tuple(StepName(step.step_name) for step in contract.steps)
        except PipelineContractError as exc:
            raise RunStepBlockedError(exc.code, str(exc)) from exc
        except ValueError as exc:
            raise RunStepBlockedError(
                "PIPELINE_CONTRACT_HASH_MISMATCH",
                "Pipeline contract contains an unknown canonical step",
            ) from exc

    @staticmethod
    def _validate_completed_step_for_resume(ctx: RunContext, step: RunStep) -> None:
        """Revalidate a completed strict step before trusting its persisted output.

        Task 7 supplies the contract-aware durable validator.  Until that
        validator is wired, strict resume fails closed instead of treating a
        status label or serialized result summary as proof of completion.
        """
        validator = ctx.completed_step_resume_validator
        if validator is None:
            raise RunStepBlockedError(
                "PIPELINE_CONTRACT_UNAVAILABLE",
                "Strict completed-step resume validation is unavailable",
            )

        try:
            accepted = validator(ctx, step)
        except RunStepBlockedError:
            raise
        except Exception as exc:
            raise RunStepBlockedError(
                "STEP_SEMANTIC_VALIDATION_FAILED",
                "Strict completed-step resume validation failed",
            ) from exc

        if accepted is not True:
            raise RunStepBlockedError(
                "STEP_SEMANTIC_VALIDATION_FAILED",
                "Strict completed-step resume validation rejected persisted output",
            )

    def _cancelled_result(self, ctx: RunContext) -> OrchestratorResult:
        """Return a safe cancellation result without overwriting terminal state."""
        all_steps = self._steps_repo.get_by_run_id(ctx.run_id)
        emit_run_signal(
            self._audit,
            event_type=RUN_CANCELLED_EVENT,
            tenant_id=ctx.tenant_id,
            details={"run_id": ctx.run_id, "code": "RUN_CANCELLED"},
        )
        return OrchestratorResult(
            status="CANCELLED",
            steps=all_steps,
            block_reason="RUN_CANCELLED",
            error_code="RUN_CANCELLED",
            error_message="Run cancelled by lifecycle request",
        )

    @staticmethod
    def _run_signals_cancellation(run_data: dict[str, Any] | None) -> bool:
        """A run signals cancellation once cancel_requested_at is set or its status is CANCELLED.

        Consulting cancel_requested_at lets a still-RUNNING run stop cooperatively as soon as
        cancellation is requested; retry/resume clears cancel_requested_at, so a resumed run is
        never spuriously cancelled.
        """
        if run_data is None:
            return False
        return (
            run_data.get("cancel_requested_at") is not None
            or str(run_data.get("status")) == "CANCELLED"
        )

    def _is_cancellation_requested(self, ctx: RunContext) -> bool:
        """Check whether run cancellation has been requested between steps."""
        if ctx.is_cancellation_requested_fn is not None:
            return bool(ctx.is_cancellation_requested_fn(ctx.run_id, ctx.tenant_id))

        if self._runs_repo is not None:
            get_run = getattr(self._runs_repo, "get", None)
            if callable(get_run):
                return self._run_signals_cancellation(get_run(ctx.run_id))

        from idis.persistence.repositories.runs import _in_memory_runs_store

        run_data = _in_memory_runs_store.get(ctx.run_id)
        if run_data is None or str(run_data.get("tenant_id")) != ctx.tenant_id:
            return False
        return self._run_signals_cancellation(run_data)

    def _dispatch_guarded_step(
        self,
        step_name: StepName,
        ctx: RunContext,
        accumulated: dict[str, Any],
    ) -> dict[str, Any]:
        """Dispatch only after preflight, then reject any swallowed write attempt."""
        if ctx.provider_output_guard_fn is not None:
            ctx.provider_output_guard_fn(step_name=step_name, phase="before")
        dispatched = self._dispatch_step(step_name, ctx, accumulated)
        if ctx.provider_output_guard_fn is not None:
            ctx.provider_output_guard_fn(step_name=step_name, phase="after")
        return dispatched

    def _dispatch_step(
        self,
        step_name: StepName,
        ctx: RunContext,
        accumulated: dict[str, Any],
    ) -> dict[str, Any]:
        """Route step execution to the appropriate handler.

        Args:
            step_name: Which step to execute.
            ctx: Run context.
            accumulated: Results from prior steps.

        Returns:
            Step result dict to merge into accumulated state.

        Raises:
            ValueError: If step_name has no handler.
        """
        if step_name == StepName.DATA_ROOM_INVENTORY_PACKAGE:
            return self._execute_data_room_inventory_package(ctx)
        if step_name == StepName.DATA_ROOM_INGESTION_HANDOFF:
            return self._execute_data_room_ingestion_handoff(ctx)
        if step_name == StepName.INGEST_CHECK:
            return self._execute_ingest_check(ctx)
        if step_name == StepName.DOCUMENT_PREFLIGHT:
            return self._execute_document_preflight(ctx)
        if step_name == StepName.METHODOLOGY_COVERAGE_INIT:
            return self._execute_methodology_coverage_init(ctx)
        if step_name == StepName.METHODOLOGY_EXTRACTION_TASK_PLANNING:
            return self._execute_methodology_extraction_task_planning(ctx, accumulated)
        if step_name == StepName.METHODOLOGY_EXTRACTION_TASK_EXECUTION:
            return self._execute_methodology_extraction_task_execution(ctx)
        if step_name == StepName.METHODOLOGY_CLAIM_MATERIALIZATION:
            return self._execute_methodology_claim_materialization(ctx)
        if step_name == StepName.METHODOLOGY_EVIDENCE_ITEM_MATERIALIZATION:
            return self._execute_methodology_evidence_item_materialization(ctx)
        if step_name == StepName.METHODOLOGY_SANAD_CREATION_LINKING_GRADING:
            return self._execute_methodology_sanad_creation_linking_grading(ctx)
        if step_name == StepName.METHODOLOGY_DETERMINISTIC_CALCULATION:
            return self._execute_methodology_deterministic_calculation(ctx)
        if step_name == StepName.METHODOLOGY_TRUTH_DASHBOARD:
            return self._execute_methodology_truth_dashboard(ctx)
        if step_name == StepName.METHODOLOGY_EVIDENCE_TRUST_COURT:
            return self._execute_methodology_evidence_trust_court(ctx)
        if step_name == StepName.METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE:
            return self._execute_methodology_validated_evidence_package(ctx, accumulated)
        if step_name == StepName.METHODOLOGY_EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN:
            return self._execute_methodology_external_intelligence_conflict_check_plan(
                ctx,
                accumulated,
            )
        if step_name == StepName.METHODOLOGY_COMPANY_IDENTITY_PACKAGE:
            return self._execute_methodology_company_identity_package(ctx)
        if step_name == StepName.METHODOLOGY_LAYER2_READINESS_PACKAGE:
            return self._execute_methodology_layer2_readiness_package(ctx, accumulated)
        if step_name == StepName.EXTRACT:
            return self._execute_extract(ctx)
        if step_name == StepName.GRADE:
            return self._execute_grade(ctx, accumulated)
        if step_name == StepName.CALC:
            return self._execute_calc(ctx, accumulated)
        if step_name == StepName.GRAPH_EVIDENCE:
            return self._execute_graph_evidence(ctx, accumulated)
        if step_name == StepName.RAG_EVIDENCE:
            return self._execute_rag_evidence(ctx, accumulated)
        if step_name == StepName.ENRICHMENT:
            return self._execute_enrichment(ctx, accumulated)
        if step_name == StepName.DEBATE:
            return self._execute_debate(ctx, accumulated)
        if step_name == StepName.LAYER2_IC_CHALLENGE:
            return self._execute_layer2_ic_challenge(ctx, accumulated)
        if step_name == StepName.ANALYSIS:
            return self._execute_analysis(ctx, accumulated)
        if step_name == StepName.SCORING:
            return self._execute_scoring(ctx, accumulated)
        if step_name == StepName.DELIVERABLES:
            return self._execute_deliverables(ctx, accumulated)
        raise ValueError(f"No handler for step: {step_name.value}")

    def _execute_data_room_inventory_package(self, ctx: RunContext) -> dict[str, Any]:
        """Inventory an optional local data-room folder before ingest checks."""
        if ctx.data_room_inventory_fn is not None:
            run_result, packages, corpus = ctx.data_room_inventory_fn(
                tenant_id=ctx.tenant_id,
                deal_id=ctx.deal_id,
                run_id=ctx.run_id,
                root_path=ctx.data_room_root_path,
            )
        else:
            from idis.services.runs.data_room_inventory_package import (
                InMemoryRunDataRoomInventoryPackageService,
            )

            run_result, packages, corpus = InMemoryRunDataRoomInventoryPackageService().run(
                tenant_id=ctx.tenant_id,
                deal_id=ctx.deal_id,
                run_id=ctx.run_id,
                root_path=ctx.data_room_root_path,
            )

        ctx.data_room_inventory_package = packages[0] if packages else None
        if corpus:
            ctx.preflight_corpus = corpus
            ctx.documents = list(corpus)
        summary = run_result.to_run_step_summary()
        if run_result.construction_status == DataRoomInventoryPackageConstructionStatus.FAILED:
            raise RunStepBlockedError(
                "DATA_ROOM_INVENTORY_PACKAGE_FAILED",
                "Data-room inventory package failed closed",
                result_summary=summary,
            )
        return summary

    def _execute_data_room_ingestion_handoff(self, ctx: RunContext) -> dict[str, Any]:
        """Optionally hand supported inventory files into durable ingestion."""
        if ctx.data_room_ingestion_handoff_fn is not None:
            run_result, corpus = ctx.data_room_ingestion_handoff_fn(
                tenant_id=ctx.tenant_id,
                deal_id=ctx.deal_id,
                run_id=ctx.run_id,
                root_path=ctx.data_room_root_path,
                inventory_package=ctx.data_room_inventory_package,
                inventory_corpus=ctx.preflight_corpus,
            )
        else:
            from idis.services.runs.data_room_ingestion_handoff import (
                InMemoryRunDataRoomIngestionHandoffService,
            )

            run_result, corpus = InMemoryRunDataRoomIngestionHandoffService().run(
                tenant_id=ctx.tenant_id,
                deal_id=ctx.deal_id,
                run_id=ctx.run_id,
                root_path=ctx.data_room_root_path,
                inventory_package=ctx.data_room_inventory_package,
                inventory_corpus=ctx.preflight_corpus,
            )
        if corpus:
            ctx.preflight_corpus = corpus
            ctx.documents = list(corpus)
        return run_result.to_run_step_summary()

    def _execute_ingest_check(self, ctx: RunContext) -> dict[str, Any]:
        """Verify at least one ingested document exists for the deal.

        Args:
            ctx: Run context with documents list.

        Returns:
            Dict with document_count.

        Raises:
            RunStepBlockedError: If no documents found.
        """
        corpus = ctx.preflight_corpus or ctx.documents
        if not corpus:
            raise RunStepBlockedError(
                BLOCK_REASON_NO_INGESTED_DOCUMENTS,
                "No ingested documents found for this deal",
            )
        return {"document_count": len(corpus)}

    def _execute_document_preflight(self, ctx: RunContext) -> dict[str, Any]:
        """Classify/triage the full corpus and filter extraction inputs."""
        corpus = ctx.preflight_corpus or ctx.documents
        if ctx.document_preflight_fn is not None:
            preflight_result, eligible_documents = ctx.document_preflight_fn(
                tenant_id=ctx.tenant_id,
                deal_id=ctx.deal_id,
                run_id=ctx.run_id,
                corpus=corpus,
            )
        else:
            from idis.services.runs.document_preflight import (
                InMemoryRunDocumentPreflightService,
            )

            preflight_result, eligible_documents = InMemoryRunDocumentPreflightService(
                no_substantive_content_receipt_verifier=(
                    ctx.no_substantive_content_receipt_verifier
                ),
            ).run(
                tenant_id=ctx.tenant_id,
                deal_id=ctx.deal_id,
                run_id=ctx.run_id,
                corpus=corpus,
            )

        summary: dict[str, Any] = preflight_result.to_run_step_summary()
        ctx.document_preflight_summary = dict(summary)
        if not eligible_documents:
            raise RunStepBlockedError(
                BLOCK_REASON_NO_USABLE_DOCUMENTS,
                "No usable documents remain after document preflight",
                result_summary=summary,
            )
        ctx.documents = eligible_documents
        return summary

    @staticmethod
    def _rehydrate_document_preflight_summary(
        ctx: RunContext,
        result_summary: Mapping[str, Any],
    ) -> None:
        """Restore the exact preflight envelope instead of the flat accumulator."""

        ctx.document_preflight_summary = dict(result_summary)

    def _execute_methodology_coverage_init(self, ctx: RunContext) -> dict[str, Any]:
        """Initialize NOT_STARTED methodology coverage records for this run."""
        if ctx.methodology_coverage_init_fn is not None:
            init_result, records = ctx.methodology_coverage_init_fn(
                tenant_id=ctx.tenant_id,
                deal_id=ctx.deal_id,
                run_id=ctx.run_id,
                registry=ctx.methodology_registry,
            )
        elif ctx.strict_resume_validation_required:
            raise RunStepBlockedError(
                "METHODOLOGY_COVERAGE_INCOMPLETE",
                "Durable methodology coverage initialization is unavailable",
            )
        else:
            from idis.services.runs.methodology_coverage_init import (
                InMemoryRunMethodologyCoverageInitService,
            )

            init_result, records = InMemoryRunMethodologyCoverageInitService(
                registry_loader_fn=ctx.methodology_registry_loader_fn,
            ).run(
                tenant_id=ctx.tenant_id,
                deal_id=ctx.deal_id,
                run_id=ctx.run_id,
                registry=ctx.methodology_registry,
            )

        ctx.methodology_coverage_records = records
        return init_result.to_run_step_summary()

    def _rehydrate_methodology_coverage_records(self, ctx: RunContext) -> None:
        """Reattach durable coverage on strict resume, retaining the legacy local seam."""
        if ctx.methodology_coverage_records:
            return

        if ctx.methodology_coverage_rehydrate_fn is not None:
            from idis.services.runs.methodology_coverage_init import (
                MethodologyCoverageInitializationError,
                validate_durable_methodology_coverage_records,
            )

            try:
                records = ctx.methodology_coverage_rehydrate_fn(
                    tenant_id=ctx.tenant_id,
                    deal_id=ctx.deal_id,
                    run_id=ctx.run_id,
                )
                ctx.methodology_coverage_records = validate_durable_methodology_coverage_records(
                    records=records,
                    tenant_id=ctx.tenant_id,
                    deal_id=ctx.deal_id,
                    run_id=ctx.run_id,
                )
            except MethodologyCoverageInitializationError as exc:
                raise RunStepBlockedError(
                    exc.code,
                    "Persisted methodology coverage is incomplete",
                ) from exc
            return

        if ctx.strict_resume_validation_required:
            raise RunStepBlockedError(
                "METHODOLOGY_COVERAGE_INCOMPLETE",
                "Durable methodology coverage rehydration is unavailable",
            )

        from idis.services.runs.methodology_coverage_init import (
            InMemoryRunMethodologyCoverageInitService,
        )

        _init_result, records = InMemoryRunMethodologyCoverageInitService(
            registry_loader_fn=ctx.methodology_registry_loader_fn,
        ).run(
            tenant_id=ctx.tenant_id,
            deal_id=ctx.deal_id,
            run_id=ctx.run_id,
            registry=ctx.methodology_registry,
        )
        ctx.methodology_coverage_records = records

    def _execute_methodology_extraction_task_planning(
        self,
        ctx: RunContext,
        accumulated: dict[str, Any],
    ) -> dict[str, Any]:
        """Plan methodology extraction tasks without executing extraction."""
        planning_callback = ctx.methodology_extraction_task_planning_fn
        if ctx.strict_resume_validation_required:
            _require_strict_methodology_callback(planning_callback, external_call=False)
            assert planning_callback is not None
            if ctx.document_preflight_summary is None:
                raise RunStepBlockedError(
                    "METHODOLOGY_EXTRACTION_TASK_PLANNING_INPUT_INVALID",
                    "Strict methodology planning requires the exact document preflight envelope",
                )
            planning_result, tasks, staged_write = planning_callback(
                tenant_id=ctx.tenant_id,
                deal_id=ctx.deal_id,
                run_id=ctx.run_id,
                run_step_id=_require_active_run_step_id(ctx),
                document_preflight_summary=ctx.document_preflight_summary,
            )
            ctx.methodology_pending_native_output_writes[
                StepName.METHODOLOGY_EXTRACTION_TASK_PLANNING
            ] = (staged_write,)
        elif planning_callback is not None:
            planning_result, tasks = planning_callback(
                tenant_id=ctx.tenant_id,
                deal_id=ctx.deal_id,
                run_id=ctx.run_id,
                registry=self._methodology_registry_for_planning(ctx),
                coverage_records=ctx.methodology_coverage_records,
                document_preflight_summary=accumulated,
            )
        else:
            planning_result, tasks = self._run_default_methodology_extraction_task_planning(
                ctx,
                accumulated,
            )

        if not tasks:
            raise RunStepBlockedError(
                BLOCK_REASON_NO_ELIGIBLE_EXTRACTION_TASKS,
                "No methodology extraction tasks could be planned",
                result_summary=planning_result.to_run_step_summary(status="FAILED"),
            )
        ctx.methodology_extraction_tasks = tasks
        summary = dict(planning_result.to_run_step_summary())
        if ctx.strict_resume_validation_required:
            _validate_strict_methodology_summary(summary)
        return summary

    def _rehydrate_methodology_extraction_tasks(
        self,
        ctx: RunContext,
        accumulated: dict[str, Any],
    ) -> None:
        """Reattach planned tasks from the durable strict loader on resume."""
        if ctx.methodology_extraction_tasks:
            return
        if ctx.strict_resume_validation_required:
            tasks = _run_strict_methodology_rehydrator(
                ctx,
                ctx.methodology_extraction_task_planning_fn,
                external_call=False,
            )
            if not isinstance(tasks, list) or not tasks:
                raise RunStepBlockedError(
                    "METHODOLOGY_TASK_COVERAGE_INCOMPLETE",
                    "Durable methodology task rehydration returned no executable tasks",
                )
            ctx.methodology_extraction_tasks = tasks
            return
        _planning_result, tasks = self._run_default_methodology_extraction_task_planning(
            ctx,
            accumulated,
        )
        ctx.methodology_extraction_tasks = tasks

    def _run_default_methodology_extraction_task_planning(
        self,
        ctx: RunContext,
        accumulated: dict[str, Any],
    ) -> tuple[ExtractionTaskPlanningRunResult, list[PlannedExtractionTask]]:
        from idis.services.runs.methodology_extraction_task_planning import (
            InMemoryRunMethodologyExtractionTaskPlanningService,
        )

        return InMemoryRunMethodologyExtractionTaskPlanningService().run(
            tenant_id=ctx.tenant_id,
            deal_id=ctx.deal_id,
            run_id=ctx.run_id,
            registry=self._methodology_registry_for_planning(ctx),
            coverage_records=ctx.methodology_coverage_records,
            document_preflight_summary=accumulated,
        )

    def _methodology_registry_for_planning(self, ctx: RunContext) -> MethodologyRegistry:
        if ctx.methodology_registry is not None:
            return ctx.methodology_registry
        if ctx.methodology_registry_loader_fn is not None:
            ctx.methodology_registry = ctx.methodology_registry_loader_fn()
            return ctx.methodology_registry

        from idis.services.runs.methodology_coverage_init import load_default_methodology_registry

        ctx.methodology_registry = load_default_methodology_registry()
        return ctx.methodology_registry

    def _execute_methodology_extraction_task_execution(
        self,
        ctx: RunContext,
    ) -> dict[str, Any]:
        """Execute planned methodology tasks without materializing claims."""
        if not ctx.methodology_extraction_tasks:
            raise RunStepBlockedError(
                BLOCK_REASON_NO_PLANNED_EXTRACTION_TASKS,
                "No planned methodology extraction tasks are attached to the run context",
            )

        execution_callback = ctx.methodology_extraction_task_execution_fn
        if ctx.strict_resume_validation_required:
            _require_strict_methodology_callback(execution_callback, external_call=True)
        if execution_callback is not None:
            execution_kwargs: dict[str, Any] = {
                "tenant_id": ctx.tenant_id,
                "deal_id": ctx.deal_id,
                "run_id": ctx.run_id,
                "tasks": ctx.methodology_extraction_tasks,
                "documents": ctx.documents,
            }
            if ctx.strict_resume_validation_required:
                execution_kwargs["run_step_id"] = _require_active_run_step_id(ctx)
            execution_values = execution_callback(**execution_kwargs)
            if ctx.strict_resume_validation_required:
                try:
                    run_result, execution_result, staged_write = execution_values
                except (TypeError, ValueError) as exc:
                    raise RunStepBlockedError(
                        "METHODOLOGY_TASK_COVERAGE_INCOMPLETE",
                        "Strict methodology execution did not stage its exact-set proof",
                    ) from exc
                ctx.methodology_pending_native_output_writes[
                    StepName.METHODOLOGY_EXTRACTION_TASK_EXECUTION
                ] = (staged_write,)
            else:
                run_result, execution_result = execution_values
        else:
            from idis.services.runs.methodology_extraction_task_execution import (
                InMemoryRunMethodologyExtractionTaskExecutionService,
            )

            execution_service = InMemoryRunMethodologyExtractionTaskExecutionService()
            run_result, execution_result = execution_service.run(
                tenant_id=ctx.tenant_id,
                deal_id=ctx.deal_id,
                run_id=ctx.run_id,
                tasks=ctx.methodology_extraction_tasks,
                documents=ctx.documents,
                extractor=None,
            )

        ctx.methodology_extraction_execution_result = execution_result
        summary = dict(run_result.to_run_step_summary())
        if ctx.strict_resume_validation_required:
            _validate_strict_methodology_summary(summary)
        return summary

    def _execute_methodology_claim_materialization(self, ctx: RunContext) -> dict[str, Any]:
        """Materialize accepted neutral execution outputs into in-memory claims."""
        if ctx.methodology_extraction_execution_result is None:
            raise RunStepBlockedError(
                "METHODOLOGY_EXECUTION_RESULT_MISSING",
                "Methodology claim materialization requires execution results",
            )

        materialize_callback = ctx.methodology_claim_materialization_fn
        if ctx.strict_resume_validation_required:
            _require_strict_methodology_callback(materialize_callback, external_call=False)
        if materialize_callback is not None:
            materialize_kwargs: dict[str, Any] = {
                "tenant_id": ctx.tenant_id,
                "deal_id": ctx.deal_id,
                "run_id": ctx.run_id,
                "execution_result": ctx.methodology_extraction_execution_result,
            }
            if ctx.strict_resume_validation_required:
                materialize_kwargs["run_step_id"] = _require_active_run_step_id(ctx)
            materialized_values = materialize_callback(**materialize_kwargs)
            if ctx.strict_resume_validation_required:
                try:
                    run_result, materialized_claims, staged_write = materialized_values
                except (TypeError, ValueError) as exc:
                    raise RunStepBlockedError(
                        "METHODOLOGY_CLAIM_MATERIALIZATION_INCOMPLETE",
                        "Strict claim materialization did not stage its durable write",
                    ) from exc
                ctx.methodology_pending_native_output_writes[
                    StepName.METHODOLOGY_CLAIM_MATERIALIZATION
                ] = (staged_write,)
            else:
                run_result, materialized_claims = materialized_values
        else:
            from idis.services.runs.methodology_claim_materialization import (
                InMemoryRunMethodologyClaimMaterializationService,
            )

            run_result, materialized_claims = (
                InMemoryRunMethodologyClaimMaterializationService().run(
                    tenant_id=ctx.tenant_id,
                    deal_id=ctx.deal_id,
                    run_id=ctx.run_id,
                    execution_result=ctx.methodology_extraction_execution_result,
                )
            )

        claims_for_context: list[RunScopedMaterializedClaim | RunScopedMaterializedClaimShell] = (
            list(materialized_claims)
        )
        ctx.methodology_materialized_claims = claims_for_context
        ctx.methodology_canonical_claim_ids = tuple(
            sorted(
                {
                    str(claim.claim_id)
                    for claim in claims_for_context
                    if getattr(claim, "claim_id", None)
                }
            )
        )
        summary = dict(run_result.to_run_step_summary())
        if ctx.strict_resume_validation_required:
            _validate_strict_methodology_summary(summary)
        return summary

    def _execute_methodology_evidence_item_materialization(self, ctx: RunContext) -> dict[str, Any]:
        """Materialize EvidenceItems from Slice 6 run-scoped claims."""
        if ctx.methodology_materialized_claims is None:
            raise RunStepBlockedError(
                "METHODOLOGY_MATERIALIZED_CLAIMS_MISSING",
                "Evidence item materialization requires claim materialization context",
            )

        materialize_callback = ctx.methodology_evidence_item_materialization_fn
        if ctx.strict_resume_validation_required:
            _require_strict_methodology_callback(materialize_callback, external_call=False)
        if materialize_callback is not None:
            materialize_kwargs = {
                "tenant_id": ctx.tenant_id,
                "deal_id": ctx.deal_id,
                "run_id": ctx.run_id,
                "materialized_claims": ctx.methodology_materialized_claims,
            }
            if ctx.strict_resume_validation_required:
                materialize_kwargs["run_step_id"] = _require_active_run_step_id(ctx)
            materialized_values = materialize_callback(**materialize_kwargs)
            if ctx.strict_resume_validation_required:
                try:
                    run_result, evidence_records, staged_write = materialized_values
                except (TypeError, ValueError) as exc:
                    raise RunStepBlockedError(
                        "METHODOLOGY_EVIDENCE_MATERIALIZATION_INCOMPLETE",
                        "Strict evidence materialization did not stage its durable write",
                    ) from exc
                ctx.methodology_pending_native_output_writes[
                    StepName.METHODOLOGY_EVIDENCE_ITEM_MATERIALIZATION
                ] = (staged_write,)
            else:
                run_result, evidence_records = materialized_values
        else:
            from idis.services.runs.methodology_evidence_item_materialization import (
                InMemoryRunMethodologyEvidenceItemMaterializationService,
            )

            run_result, evidence_records = (
                InMemoryRunMethodologyEvidenceItemMaterializationService().run(
                    tenant_id=ctx.tenant_id,
                    deal_id=ctx.deal_id,
                    run_id=ctx.run_id,
                    materialized_claims=ctx.methodology_materialized_claims,
                )
            )

        evidence_for_context: list[RunScopedEvidenceItemRecord | RunScopedEvidenceItemShell] = list(
            evidence_records
        )
        ctx.methodology_evidence_items = evidence_for_context
        ctx.methodology_evidence_source_provenance = [
            record.source_ref for record in evidence_records
        ]
        summary = dict(run_result.to_run_step_summary())
        if ctx.strict_resume_validation_required:
            _validate_strict_methodology_summary(summary)
        return summary

    def _execute_methodology_sanad_creation_linking_grading(
        self,
        ctx: RunContext,
    ) -> dict[str, Any]:
        """Create, link, and grade run-scoped Sanads from Slice 6/7 outputs."""
        if ctx.methodology_materialized_claims is None:
            raise RunStepBlockedError(
                "METHODOLOGY_MATERIALIZED_CLAIMS_MISSING",
                "Sanad creation requires claim materialization context",
            )
        if ctx.methodology_evidence_items is None:
            raise RunStepBlockedError(
                "METHODOLOGY_EVIDENCE_ITEMS_MISSING",
                "Sanad creation requires evidence item materialization context",
            )
        evidence_items = ctx.methodology_evidence_items

        materialize_callback = ctx.methodology_sanad_creation_linking_grading_fn
        if ctx.strict_resume_validation_required:
            _require_strict_methodology_callback(materialize_callback, external_call=False)
        if materialize_callback is not None:
            materialize_kwargs = {
                "tenant_id": ctx.tenant_id,
                "deal_id": ctx.deal_id,
                "run_id": ctx.run_id,
                "materialized_claims": ctx.methodology_materialized_claims,
                "evidence_items": evidence_items,
                "source_provenance": ctx.methodology_evidence_source_provenance,
            }
            if ctx.strict_resume_validation_required:
                materialize_kwargs["run_step_id"] = _require_active_run_step_id(ctx)
            materialized_values = materialize_callback(**materialize_kwargs)
            if ctx.strict_resume_validation_required:
                try:
                    (
                        run_result,
                        sanad_records,
                        links,
                        grades,
                        defects,
                        staged_write,
                    ) = materialized_values
                except (TypeError, ValueError) as exc:
                    raise RunStepBlockedError(
                        "METHODOLOGY_SANAD_INCOMPLETE",
                        "Strict Sanad materialization did not stage its durable write",
                    ) from exc
                ctx.methodology_pending_native_output_writes[
                    StepName.METHODOLOGY_SANAD_CREATION_LINKING_GRADING
                ] = (staged_write,)
            else:
                run_result, sanad_records, links, grades, defects = materialized_values
        else:
            from idis.services.runs.methodology_sanad_creation_linking_grading import (
                InMemoryRunMethodologySanadCreationLinkingGradingService,
            )

            run_result, sanad_records, links, grades, defects = (
                InMemoryRunMethodologySanadCreationLinkingGradingService().run(
                    tenant_id=ctx.tenant_id,
                    deal_id=ctx.deal_id,
                    run_id=ctx.run_id,
                    materialized_claims=ctx.methodology_materialized_claims,
                    evidence_items=evidence_items,
                    source_provenance=ctx.methodology_evidence_source_provenance,
                )
            )

        ctx.methodology_sanads = list(sanad_records)
        ctx.methodology_sanad_links = list(links)
        ctx.methodology_sanad_grades = list(grades)
        ctx.methodology_sanad_defects = list(defects)
        summary = dict(run_result.to_run_step_summary())
        if ctx.strict_resume_validation_required:
            _validate_strict_methodology_summary(summary)
        return summary

    def _execute_methodology_deterministic_calculation(self, ctx: RunContext) -> dict[str, Any]:
        """Run deterministic CDD/FDD calculations from Slice 6/8 outputs."""
        if ctx.methodology_materialized_claims is None:
            raise RunStepBlockedError(
                "METHODOLOGY_MATERIALIZED_CLAIMS_MISSING",
                "Deterministic calculation requires claim materialization context",
            )
        if ctx.methodology_sanad_grades is None and _tasks_request_calculations(
            ctx.methodology_extraction_tasks
        ):
            raise RunStepBlockedError(
                "METHODOLOGY_SANAD_GRADES_MISSING",
                "Deterministic calculation requires Slice 8 Sanad grades",
            )

        if ctx.methodology_deterministic_calculation_fn is not None:
            run_result, calculations, calc_sanads = ctx.methodology_deterministic_calculation_fn(
                tenant_id=ctx.tenant_id,
                deal_id=ctx.deal_id,
                run_id=ctx.run_id,
                materialized_claims=ctx.methodology_materialized_claims,
                sanads=ctx.methodology_sanads,
                sanad_grades=ctx.methodology_sanad_grades or [],
                extraction_tasks=ctx.methodology_extraction_tasks,
            )
        else:
            from idis.services.runs.methodology_deterministic_calculation import (
                InMemoryRunMethodologyDeterministicCalculationService,
            )

            run_result, calculations, calc_sanads = (
                InMemoryRunMethodologyDeterministicCalculationService().run(
                    tenant_id=ctx.tenant_id,
                    deal_id=ctx.deal_id,
                    run_id=ctx.run_id,
                    materialized_claims=ctx.methodology_materialized_claims,
                    sanads=ctx.methodology_sanads,
                    sanad_grades=ctx.methodology_sanad_grades or [],
                    extraction_tasks=ctx.methodology_extraction_tasks,
                )
            )

        ctx.methodology_calculations = list(calculations)
        ctx.methodology_calc_sanads = list(calc_sanads)
        summary = run_result.to_run_step_summary()
        if run_result.status.value == "failed":
            raise RunStepBlockedError(
                "METHODOLOGY_DETERMINISTIC_CALCULATION_FAILED",
                "Deterministic calculation failed closed",
                result_summary=summary,
            )
        return summary

    def _execute_methodology_truth_dashboard(self, ctx: RunContext) -> dict[str, Any]:
        """Build a run-scoped Truth Dashboard from Slice 6-9 outputs."""
        if ctx.methodology_materialized_claims is None:
            raise RunStepBlockedError(
                "METHODOLOGY_MATERIALIZED_CLAIMS_MISSING",
                "Truth Dashboard requires claim materialization context",
            )
        if ctx.methodology_evidence_items is None:
            raise RunStepBlockedError(
                "METHODOLOGY_EVIDENCE_ITEMS_MISSING",
                "Truth Dashboard requires evidence item materialization context",
            )
        no_claims = len(ctx.methodology_materialized_claims) == 0
        if not no_claims and not ctx.methodology_sanads:
            raise RunStepBlockedError(
                "METHODOLOGY_SANADS_MISSING",
                "Truth Dashboard requires run-scoped Sanads",
            )
        if ctx.methodology_sanad_grades is None:
            raise RunStepBlockedError(
                "METHODOLOGY_SANAD_GRADES_MISSING",
                "Truth Dashboard requires run-scoped Sanad grades",
            )

        if ctx.methodology_truth_dashboard_fn is not None:
            run_result, dashboards = ctx.methodology_truth_dashboard_fn(
                tenant_id=ctx.tenant_id,
                deal_id=ctx.deal_id,
                run_id=ctx.run_id,
                materialized_claims=ctx.methodology_materialized_claims,
                evidence_items=ctx.methodology_evidence_items,
                source_provenance=ctx.methodology_evidence_source_provenance,
                sanads=ctx.methodology_sanads,
                sanad_grades=ctx.methodology_sanad_grades,
                sanad_defects=ctx.methodology_sanad_defects,
                calculations=ctx.methodology_calculations,
                calc_sanads=ctx.methodology_calc_sanads,
            )
        else:
            from idis.services.runs.methodology_truth_dashboard import (
                InMemoryRunMethodologyTruthDashboardService,
            )

            run_result, dashboards = InMemoryRunMethodologyTruthDashboardService().run(
                tenant_id=ctx.tenant_id,
                deal_id=ctx.deal_id,
                run_id=ctx.run_id,
                materialized_claims=ctx.methodology_materialized_claims,
                evidence_items=ctx.methodology_evidence_items,
                source_provenance=ctx.methodology_evidence_source_provenance,
                sanads=ctx.methodology_sanads,
                sanad_grades=ctx.methodology_sanad_grades,
                sanad_defects=ctx.methodology_sanad_defects,
                calculations=ctx.methodology_calculations,
                calc_sanads=ctx.methodology_calc_sanads,
            )

        ctx.methodology_truth_dashboard = dashboards[0] if dashboards else None
        summary = run_result.to_run_step_summary()
        if run_result.status.value == "failed":
            raise RunStepBlockedError(
                "METHODOLOGY_TRUTH_DASHBOARD_FAILED",
                "Truth Dashboard failed closed",
                result_summary=summary,
            )
        return summary

    def _execute_methodology_evidence_trust_court(self, ctx: RunContext) -> dict[str, Any]:
        """Build a Layer 1 Evidence Trust Court record from Slice 6-10 outputs."""
        if ctx.methodology_materialized_claims is None:
            raise RunStepBlockedError(
                "METHODOLOGY_MATERIALIZED_CLAIMS_MISSING",
                "Evidence Trust Court requires claim materialization context",
            )
        if ctx.methodology_evidence_items is None:
            raise RunStepBlockedError(
                "METHODOLOGY_EVIDENCE_ITEMS_MISSING",
                "Evidence Trust Court requires evidence item materialization context",
            )
        no_claims = len(ctx.methodology_materialized_claims) == 0
        if not no_claims and not ctx.methodology_sanads:
            raise RunStepBlockedError(
                "METHODOLOGY_SANADS_MISSING",
                "Evidence Trust Court requires run-scoped Sanads",
            )
        if ctx.methodology_sanad_grades is None:
            raise RunStepBlockedError(
                "METHODOLOGY_SANAD_GRADES_MISSING",
                "Evidence Trust Court requires run-scoped Sanad grades",
            )
        if ctx.methodology_truth_dashboard is None:
            if no_claims:
                return _empty_evidence_trust_court_summary(
                    tenant_id=ctx.tenant_id,
                    deal_id=ctx.deal_id,
                    run_id=ctx.run_id,
                )
            raise RunStepBlockedError(
                "METHODOLOGY_TRUTH_DASHBOARD_MISSING",
                "Evidence Trust Court requires a full Truth Dashboard record",
            )

        muhasabah_records: list[Any] = []
        if ctx.methodology_evidence_trust_court_fn is not None:
            run_result, courts = ctx.methodology_evidence_trust_court_fn(
                tenant_id=ctx.tenant_id,
                deal_id=ctx.deal_id,
                run_id=ctx.run_id,
                materialized_claims=ctx.methodology_materialized_claims,
                evidence_items=ctx.methodology_evidence_items,
                source_provenance=ctx.methodology_evidence_source_provenance,
                sanads=ctx.methodology_sanads,
                sanad_grades=ctx.methodology_sanad_grades,
                sanad_defects=ctx.methodology_sanad_defects,
                calculations=ctx.methodology_calculations,
                calc_sanads=ctx.methodology_calc_sanads,
                truth_dashboards=[ctx.methodology_truth_dashboard],
            )
        else:
            from idis.services.runs.methodology_evidence_trust_court import (
                InMemoryRunMethodologyEvidenceTrustCourtService,
            )

            run_result, courts = InMemoryRunMethodologyEvidenceTrustCourtService(
                muhasabah_sink=muhasabah_records
            ).run(
                tenant_id=ctx.tenant_id,
                deal_id=ctx.deal_id,
                run_id=ctx.run_id,
                materialized_claims=ctx.methodology_materialized_claims,
                evidence_items=ctx.methodology_evidence_items,
                source_provenance=ctx.methodology_evidence_source_provenance,
                sanads=ctx.methodology_sanads,
                sanad_grades=ctx.methodology_sanad_grades,
                sanad_defects=ctx.methodology_sanad_defects,
                calculations=ctx.methodology_calculations,
                calc_sanads=ctx.methodology_calc_sanads,
                truth_dashboards=[ctx.methodology_truth_dashboard],
            )

        ctx.methodology_evidence_trust_court = courts[0] if courts else None
        summary = run_result.to_run_step_summary()
        if run_result.status.value == "failed":
            raise RunStepBlockedError(
                "METHODOLOGY_EVIDENCE_TRUST_COURT_FAILED",
                "Evidence Trust Court failed closed",
                result_summary=summary,
            )
        persistence = self._persist_layer1_court_rows(
            ctx,
            record=ctx.methodology_evidence_trust_court,
            muhasabah_records=muhasabah_records,
        )
        if persistence is not None:
            summary["layer1_persistence"] = persistence
        return summary

    def _layer1_evidence_repository(self, ctx: RunContext) -> Any:
        """Return the injected Layer-1 repository or the in-memory twin default."""
        if ctx.layer1_evidence_repository is not None:
            return ctx.layer1_evidence_repository
        from idis.persistence.repositories.layer1_evidence import (
            get_layer1_evidence_repository,
        )

        return get_layer1_evidence_repository(None, ctx.tenant_id)

    def _persist_layer1_court_rows(
        self,
        ctx: RunContext,
        *,
        record: Any,
        muhasabah_records: list[Any],
    ) -> dict[str, Any] | None:
        """Persist court findings + court-scoped Muhasabah rows (Slice92, DEC-F).

        Write failures fail the step closed with a reason-coded blocker; the
        in-memory twin default cannot fail, so non-database runs stay green.
        Returns a safe ids/counts block for the step summary, or None when there
        is no full court record to persist.
        """
        if not isinstance(record, RunScopedEvidenceTrustCourtRecord):
            return None
        from idis.models.layer1_durability import (
            EvidenceTrustFindingRow,
            MuhasabahRecordRow,
        )

        try:
            repository = self._layer1_evidence_repository(ctx)
            finding_row_count = 0
            for finding in record.findings:
                repository.upsert_evidence_trust_finding(
                    EvidenceTrustFindingRow.from_finding(
                        finding,
                        tenant_id=record.tenant_id,
                        deal_id=record.deal_id,
                        run_id=record.run_id,
                        court_id=record.court_id,
                    )
                )
                finding_row_count += 1
            muhasabah_row_count = 0
            for muhasabah_record in muhasabah_records:
                repository.upsert_muhasabah_record(
                    MuhasabahRecordRow.from_debate_record(
                        muhasabah_record,
                        tenant_id=record.tenant_id,
                        deal_id=record.deal_id,
                        run_id=record.run_id,
                        source_step=StepName.METHODOLOGY_EVIDENCE_TRUST_COURT.value,
                    )
                )
                muhasabah_row_count += 1
        except Exception as exc:
            # The message is ledger-visible via step.error_message — never include
            # raw exception text; the cause stays chained for logs/debugging.
            raise RunStepBlockedError(
                "METHODOLOGY_LAYER1_PERSISTENCE_FAILED",
                "Layer 1 evidence persistence failed closed",
            ) from exc
        return {
            "status": "persisted",
            "finding_row_count": finding_row_count,
            "muhasabah_row_count": muhasabah_row_count,
        }

    def _persist_layer1_vep_row(
        self,
        ctx: RunContext,
        *,
        record: Any,
    ) -> dict[str, Any] | None:
        """Persist the durable VEP candidate row (Slice92, DEC-F fail-closed)."""
        if not isinstance(record, RunScopedValidatedEvidencePackageRecord):
            return None
        from idis.models.layer1_durability import ValidatedEvidencePackageRow

        try:
            repository = self._layer1_evidence_repository(ctx)
            repository.upsert_validated_evidence_package(
                ValidatedEvidencePackageRow.from_record(record)
            )
        except Exception as exc:
            # The message is ledger-visible via step.error_message — never include
            # raw exception text; the cause stays chained for logs/debugging.
            raise RunStepBlockedError(
                "METHODOLOGY_LAYER1_PERSISTENCE_FAILED",
                "Layer 1 evidence persistence failed closed",
            ) from exc
        return {
            "status": "persisted",
            "package_row_count": 1,
            "package_ids": [record.package_id],
        }

    def _execute_methodology_validated_evidence_package(
        self,
        ctx: RunContext,
        accumulated: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build a Layer 1 Validated Evidence Package from a full court record."""
        if ctx.methodology_evidence_trust_court is None:
            if accumulated is not None and _is_empty_evidence_trust_court_summary(accumulated):
                return _empty_validated_evidence_package_summary(
                    tenant_id=ctx.tenant_id,
                    deal_id=ctx.deal_id,
                    run_id=ctx.run_id,
                )
            raise RunStepBlockedError(
                "METHODOLOGY_EVIDENCE_TRUST_COURT_MISSING",
                "Validated Evidence Package requires Evidence Trust Court context",
            )

        if ctx.methodology_validated_evidence_package_fn is not None:
            run_result, packages = ctx.methodology_validated_evidence_package_fn(
                tenant_id=ctx.tenant_id,
                deal_id=ctx.deal_id,
                run_id=ctx.run_id,
                evidence_trust_courts=[ctx.methodology_evidence_trust_court],
            )
        else:
            from idis.services.runs.methodology_validated_evidence_package import (
                InMemoryRunMethodologyValidatedEvidencePackageService,
            )

            run_result, packages = InMemoryRunMethodologyValidatedEvidencePackageService().run(
                tenant_id=ctx.tenant_id,
                deal_id=ctx.deal_id,
                run_id=ctx.run_id,
                evidence_trust_courts=[ctx.methodology_evidence_trust_court],
            )

        ctx.methodology_validated_evidence_package = packages[0] if packages else None
        summary = run_result.to_run_step_summary()
        if run_result.status.value == "failed":
            raise RunStepBlockedError(
                "METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE_FAILED",
                "Validated Evidence Package failed closed",
                result_summary=summary,
            )
        persistence: dict[str, Any] | None
        if ctx.stage_native_output_writes_fn is not None and isinstance(
            ctx.methodology_validated_evidence_package,
            RunScopedValidatedEvidencePackageRecord,
        ):
            persistence = {
                "status": "staged",
                "package_row_count": 1,
                "package_ids": [ctx.methodology_validated_evidence_package.package_id],
            }
        else:
            persistence = self._persist_layer1_vep_row(
                ctx, record=ctx.methodology_validated_evidence_package
            )
        if persistence is not None:
            summary["layer1_persistence"] = persistence
        return summary

    def _execute_methodology_external_intelligence_conflict_check_plan(
        self,
        ctx: RunContext,
        accumulated: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build a safe external intelligence conflict-check plan from VEP data."""
        if ctx.methodology_validated_evidence_package is None:
            if accumulated is not None and _is_empty_validated_evidence_package_summary(
                accumulated
            ):
                return _empty_external_intelligence_conflict_check_plan_summary(
                    tenant_id=ctx.tenant_id,
                    deal_id=ctx.deal_id,
                    run_id=ctx.run_id,
                )
            raise RunStepBlockedError(
                "METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE_MISSING",
                "External intelligence conflict-check plan requires VEP context",
            )

        if ctx.methodology_external_intelligence_conflict_check_plan_fn is not None:
            run_result, plans = ctx.methodology_external_intelligence_conflict_check_plan_fn(
                tenant_id=ctx.tenant_id,
                deal_id=ctx.deal_id,
                run_id=ctx.run_id,
                validated_evidence_packages=[ctx.methodology_validated_evidence_package],
            )
        else:
            from idis.services.runs.methodology_external_intelligence_conflict_check_plan import (
                InMemoryRunMethodologyExternalIntelligenceConflictCheckPlanService,
            )

            run_result, plans = (
                InMemoryRunMethodologyExternalIntelligenceConflictCheckPlanService().run(
                    tenant_id=ctx.tenant_id,
                    deal_id=ctx.deal_id,
                    run_id=ctx.run_id,
                    validated_evidence_packages=[ctx.methodology_validated_evidence_package],
                )
            )

        ctx.methodology_external_intelligence_conflict_check_plan = plans[0] if plans else None
        summary = run_result.to_run_step_summary()
        if run_result.status.value == "failed":
            raise RunStepBlockedError(
                "METHODOLOGY_EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN_FAILED",
                "External intelligence conflict-check plan failed closed",
                result_summary=summary,
            )
        return summary

    def _execute_methodology_company_identity_package(
        self,
        ctx: RunContext,
    ) -> dict[str, Any]:
        """Build a safe company identity package from explicit deal metadata."""
        if ctx.methodology_company_identity_package_fn is not None:
            run_result, packages = ctx.methodology_company_identity_package_fn(
                tenant_id=ctx.tenant_id,
                deal_id=ctx.deal_id,
                run_id=ctx.run_id,
                deal_metadata=ctx.deal_metadata,
            )
        else:
            from idis.services.runs.methodology_company_identity_package import (
                InMemoryRunMethodologyCompanyIdentityPackageService,
            )

            run_result, packages = InMemoryRunMethodologyCompanyIdentityPackageService().run(
                tenant_id=ctx.tenant_id,
                deal_id=ctx.deal_id,
                run_id=ctx.run_id,
                deal_metadata=ctx.deal_metadata,
            )

        ctx.methodology_company_identity_package = packages[0] if packages else None
        summary = run_result.to_run_step_summary()
        if run_result.construction_status.value == "failed":
            raise RunStepBlockedError(
                "METHODOLOGY_COMPANY_IDENTITY_PACKAGE_FAILED",
                "Company identity package failed closed",
                result_summary=summary,
            )
        return summary

    def _execute_methodology_layer2_readiness_package(
        self,
        ctx: RunContext,
        accumulated: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build a safe Layer 2 readiness package from VEP and Slice 13 plan data."""
        if ctx.methodology_validated_evidence_package is None:
            if (
                accumulated is not None
                and _is_empty_external_intelligence_conflict_check_plan_summary(accumulated)
            ):
                return _empty_layer2_readiness_package_summary(
                    tenant_id=ctx.tenant_id,
                    deal_id=ctx.deal_id,
                    run_id=ctx.run_id,
                )
            raise RunStepBlockedError(
                "METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE_MISSING",
                "Layer 2 readiness package requires VEP context",
            )
        if ctx.methodology_external_intelligence_conflict_check_plan is None:
            if (
                accumulated is not None
                and _is_empty_external_intelligence_conflict_check_plan_summary(accumulated)
            ):
                return _empty_layer2_readiness_package_summary(
                    tenant_id=ctx.tenant_id,
                    deal_id=ctx.deal_id,
                    run_id=ctx.run_id,
                )
            raise RunStepBlockedError(
                "METHODOLOGY_EXTERNAL_INTELLIGENCE_CONFLICT_CHECK_PLAN_MISSING",
                "Layer 2 readiness package requires external intelligence plan context",
            )

        if ctx.methodology_layer2_readiness_package_fn is not None:
            run_result, packages = ctx.methodology_layer2_readiness_package_fn(
                tenant_id=ctx.tenant_id,
                deal_id=ctx.deal_id,
                run_id=ctx.run_id,
                validated_evidence_packages=[ctx.methodology_validated_evidence_package],
                external_intelligence_conflict_check_plans=[
                    ctx.methodology_external_intelligence_conflict_check_plan
                ],
                company_identity_ids=_company_identity_ids(
                    ctx.methodology_company_identity_package
                ),
            )
        else:
            from idis.services.runs.methodology_layer2_readiness_package import (
                InMemoryRunMethodologyLayer2ReadinessPackageService,
            )

            run_result, packages = InMemoryRunMethodologyLayer2ReadinessPackageService().run(
                tenant_id=ctx.tenant_id,
                deal_id=ctx.deal_id,
                run_id=ctx.run_id,
                validated_evidence_packages=[ctx.methodology_validated_evidence_package],
                external_intelligence_conflict_check_plans=[
                    ctx.methodology_external_intelligence_conflict_check_plan
                ],
                company_identity_ids=_company_identity_ids(
                    ctx.methodology_company_identity_package
                ),
            )

        ctx.methodology_layer2_readiness_package = packages[0] if packages else None
        summary = run_result.to_run_step_summary()
        if run_result.construction_status.value == "failed":
            raise RunStepBlockedError(
                "METHODOLOGY_LAYER2_READINESS_PACKAGE_FAILED",
                "Layer 2 readiness package failed closed",
                result_summary=summary,
            )
        return summary

    def _rehydrate_methodology_extraction_execution(
        self,
        ctx: RunContext,
        result_summary: dict[str, Any],
    ) -> None:
        """Attach durable terminal execution state when completed execution is skipped."""
        if ctx.methodology_extraction_execution_result is not None:
            return
        if ctx.strict_resume_validation_required:
            rehydrated = _run_strict_methodology_rehydrator(
                ctx,
                ctx.methodology_extraction_task_execution_fn,
                external_call=True,
            )
            try:
                _run_result, execution_result = rehydrated
            except (TypeError, ValueError) as exc:
                raise RunStepBlockedError(
                    "METHODOLOGY_TASK_COVERAGE_INCOMPLETE",
                    "Durable methodology execution rehydration is malformed",
                ) from exc
            ctx.methodology_extraction_execution_result = execution_result
            return
        ctx.methodology_extraction_execution_result = _execution_result_shell_from_summary(
            tenant_id=ctx.tenant_id,
            deal_id=ctx.deal_id,
            run_id=ctx.run_id,
            result_summary=result_summary,
        )

    def _rehydrate_methodology_materialized_claims(
        self,
        ctx: RunContext,
        result_summary: dict[str, Any],
    ) -> None:
        """Attach durable canonical claims when completed materialization is skipped."""
        if ctx.methodology_materialized_claims:
            return
        if ctx.strict_resume_validation_required:
            rehydrated = _run_strict_methodology_rehydrator(
                ctx,
                ctx.methodology_claim_materialization_fn,
                external_call=False,
            )
            try:
                _run_result, claims = rehydrated
                claims_for_context = list(claims)
            except (TypeError, ValueError) as exc:
                raise RunStepBlockedError(
                    "METHODOLOGY_CANONICAL_MATERIALIZATION_INCOMPLETE",
                    "Durable methodology claim rehydration is malformed",
                ) from exc
            ctx.methodology_materialized_claims = claims_for_context
            ctx.methodology_canonical_claim_ids = tuple(
                sorted(
                    {
                        str(claim.claim_id)
                        for claim in claims_for_context
                        if getattr(claim, "claim_id", None)
                    }
                )
            )
            return
        raw_mappings = result_summary.get("output_claim_mappings")
        mappings: list[Any] = raw_mappings if isinstance(raw_mappings, list) else []
        shells: list[RunScopedMaterializedClaimShell] = []
        for mapping in mappings:
            if not isinstance(mapping, dict):
                continue
            shell = _materialized_claim_shell_from_mapping(
                tenant_id=ctx.tenant_id,
                deal_id=ctx.deal_id,
                run_id=ctx.run_id,
                mapping=mapping,
            )
            if shell is not None:
                shells.append(shell)
        shell_claims: list[RunScopedMaterializedClaim | RunScopedMaterializedClaimShell] = []
        shell_claims.extend(shells)
        ctx.methodology_materialized_claims = shell_claims

    def _rehydrate_methodology_evidence_items(
        self,
        ctx: RunContext,
        result_summary: dict[str, Any],
    ) -> None:
        """Attach durable EvidenceItems when completed materialization is skipped."""
        if ctx.methodology_evidence_items:
            return
        if ctx.strict_resume_validation_required:
            rehydrated = _run_strict_methodology_rehydrator(
                ctx,
                ctx.methodology_evidence_item_materialization_fn,
                external_call=False,
            )
            try:
                _run_result, evidence_records = rehydrated
                evidence_for_context = list(evidence_records)
            except (TypeError, ValueError) as exc:
                raise RunStepBlockedError(
                    "METHODOLOGY_CANONICAL_MATERIALIZATION_INCOMPLETE",
                    "Durable methodology evidence rehydration is malformed",
                ) from exc
            ctx.methodology_evidence_items = evidence_for_context
            ctx.methodology_evidence_source_provenance = [
                record.source_ref
                for record in evidence_for_context
                if getattr(record, "source_ref", None) is not None
            ]
            return
        raw_mappings = result_summary.get("evidence_item_mappings")
        mappings: list[Any] = raw_mappings if isinstance(raw_mappings, list) else []
        shells: list[RunScopedEvidenceItemShell] = []
        provenance_refs: list[RunScopedEvidenceProvenanceRef] = []
        for mapping in mappings:
            if not isinstance(mapping, dict):
                continue
            shell = _evidence_item_shell_from_mapping(
                tenant_id=ctx.tenant_id,
                deal_id=ctx.deal_id,
                run_id=ctx.run_id,
                mapping=mapping,
            )
            provenance_ref = _evidence_provenance_from_mapping(mapping)
            if shell is not None:
                shells.append(shell)
            if provenance_ref is not None:
                provenance_refs.append(provenance_ref)
        evidence_items: list[RunScopedEvidenceItemRecord | RunScopedEvidenceItemShell] = []
        evidence_items.extend(shells)
        ctx.methodology_evidence_items = evidence_items
        ctx.methodology_evidence_source_provenance = provenance_refs

    def _rehydrate_methodology_sanads(
        self,
        ctx: RunContext,
        result_summary: dict[str, Any],
    ) -> None:
        """Attach durable canonical Sanad state when completed materialization is skipped."""
        if ctx.methodology_sanads:
            return
        if ctx.strict_resume_validation_required:
            rehydrated = _run_strict_methodology_rehydrator(
                ctx,
                ctx.methodology_sanad_creation_linking_grading_fn,
                external_call=False,
            )
            try:
                _run_result, sanads, links, grades, defects = rehydrated
                ctx.methodology_sanads = list(sanads)
                ctx.methodology_sanad_links = list(links)
                ctx.methodology_sanad_grades = list(grades)
                ctx.methodology_sanad_defects = list(defects)
            except (TypeError, ValueError) as exc:
                raise RunStepBlockedError(
                    "METHODOLOGY_CANONICAL_MATERIALIZATION_INCOMPLETE",
                    "Durable methodology Sanad rehydration is malformed",
                ) from exc
            return
        raw_mappings = result_summary.get("sanad_mappings")
        mappings: list[Any] = raw_mappings if isinstance(raw_mappings, list) else []
        shells: list[RunScopedSanadShell] = []
        for mapping in mappings:
            if not isinstance(mapping, dict):
                continue
            shell = _sanad_shell_from_mapping(
                tenant_id=ctx.tenant_id,
                deal_id=ctx.deal_id,
                run_id=ctx.run_id,
                mapping=mapping,
            )
            if shell is not None:
                shells.append(shell)
        sanad_shells: list[RunScopedSanadRecord | RunScopedSanadShell] = []
        sanad_shells.extend(shells)
        ctx.methodology_sanads = sanad_shells
        ctx.methodology_sanad_links = _sanad_links_from_summary(
            tenant_id=ctx.tenant_id,
            deal_id=ctx.deal_id,
            run_id=ctx.run_id,
            result_summary=result_summary,
        )
        ctx.methodology_sanad_grades = _sanad_grades_from_summary(
            tenant_id=ctx.tenant_id,
            deal_id=ctx.deal_id,
            run_id=ctx.run_id,
            result_summary=result_summary,
        )
        defect_shells: list[RunScopedSanadDefectRecord | RunScopedSanadDefectShell] = []
        defect_shells.extend(
            _sanad_defects_from_summary(
                tenant_id=ctx.tenant_id,
                deal_id=ctx.deal_id,
                run_id=ctx.run_id,
                result_summary=result_summary,
            )
        )
        ctx.methodology_sanad_defects = defect_shells

    def _rehydrate_methodology_calculations(
        self,
        ctx: RunContext,
        result_summary: dict[str, Any],
    ) -> None:
        """Attach safe calculation shells when completed Slice 9 step is skipped."""
        if ctx.methodology_calculations:
            return
        raw_shells = result_summary.get("calculation_shells")
        shells: list[Any] = raw_shells if isinstance(raw_shells, list) else []
        calculation_shells: list[RunScopedCalculationShell] = []
        for shell in shells:
            if not isinstance(shell, dict):
                continue
            calc_shell = _calculation_shell_from_summary(
                tenant_id=ctx.tenant_id,
                deal_id=ctx.deal_id,
                run_id=ctx.run_id,
                shell=shell,
            )
            if calc_shell is not None:
                calculation_shells.append(calc_shell)
        calculations: list[RunScopedDeterministicCalculationRecord | RunScopedCalculationShell] = []
        calculations.extend(calculation_shells)
        ctx.methodology_calculations = calculations

        raw_sanad_shells = result_summary.get("calc_sanad_shells")
        sanad_shells: list[Any] = raw_sanad_shells if isinstance(raw_sanad_shells, list) else []
        calc_sanad_shells: list[RunScopedCalcSanadRecord | RunScopedCalcSanadShell] = []
        calc_sanad_shells.extend(
            shell
            for shell in (
                _calc_sanad_shell_from_summary(
                    tenant_id=ctx.tenant_id,
                    deal_id=ctx.deal_id,
                    run_id=ctx.run_id,
                    shell=raw_shell,
                )
                for raw_shell in sanad_shells
                if isinstance(raw_shell, dict)
            )
            if shell is not None
        )
        ctx.methodology_calc_sanads = calc_sanad_shells

    def _rehydrate_methodology_truth_dashboard(
        self,
        ctx: RunContext,
        result_summary: dict[str, Any],
    ) -> None:
        """Attach a safe Truth Dashboard shell when completed Slice 10 is skipped."""
        if ctx.methodology_truth_dashboard is not None:
            return
        shell = _truth_dashboard_shell_from_summary(
            tenant_id=ctx.tenant_id,
            deal_id=ctx.deal_id,
            run_id=ctx.run_id,
            result_summary=result_summary,
        )
        if shell is not None:
            ctx.methodology_truth_dashboard = shell

    def _rehydrate_methodology_evidence_trust_court(
        self,
        ctx: RunContext,
        result_summary: dict[str, Any],
    ) -> None:
        """Attach a safe Evidence Trust Court shell when completed Slice 11 is skipped."""
        if ctx.methodology_evidence_trust_court is not None:
            return
        shell = _evidence_trust_court_shell_from_summary(
            tenant_id=ctx.tenant_id,
            deal_id=ctx.deal_id,
            run_id=ctx.run_id,
            result_summary=result_summary,
        )
        if shell is not None:
            ctx.methodology_evidence_trust_court = shell

    def _rehydrate_methodology_validated_evidence_package(
        self,
        ctx: RunContext,
        result_summary: dict[str, Any],
    ) -> None:
        """Attach a safe Validated Evidence Package shell when Slice 12 is skipped."""
        if ctx.methodology_validated_evidence_package is not None:
            return
        shell = _validated_evidence_package_shell_from_summary(
            tenant_id=ctx.tenant_id,
            deal_id=ctx.deal_id,
            run_id=ctx.run_id,
            result_summary=result_summary,
        )
        if shell is not None:
            ctx.methodology_validated_evidence_package = shell

    def _rehydrate_methodology_external_intelligence_conflict_check_plan(
        self,
        ctx: RunContext,
        result_summary: dict[str, Any],
    ) -> None:
        """Attach a safe external intelligence plan shell when Slice 13 is skipped."""
        if ctx.methodology_external_intelligence_conflict_check_plan is not None:
            return
        shell = _external_intelligence_conflict_check_plan_shell_from_summary(
            tenant_id=ctx.tenant_id,
            deal_id=ctx.deal_id,
            run_id=ctx.run_id,
            result_summary=result_summary,
        )
        if shell is not None:
            ctx.methodology_external_intelligence_conflict_check_plan = shell

    def _rehydrate_data_room_inventory_package(
        self,
        ctx: RunContext,
        result_summary: dict[str, Any],
    ) -> None:
        """Attach a safe data-room inventory package shell when Slice 16 is skipped."""
        if ctx.data_room_inventory_package is not None:
            return
        shell = _data_room_inventory_package_shell_from_summary(
            tenant_id=ctx.tenant_id,
            deal_id=ctx.deal_id,
            run_id=ctx.run_id,
            result_summary=result_summary,
        )
        if shell is not None:
            ctx.data_room_inventory_package = shell

    def _rehydrate_methodology_layer2_readiness_package(
        self,
        ctx: RunContext,
        result_summary: dict[str, Any],
    ) -> None:
        """Attach a safe Layer 2 readiness package shell when Slice 14 is skipped."""
        if ctx.methodology_layer2_readiness_package is not None:
            return
        shell = _layer2_readiness_package_shell_from_summary(
            tenant_id=ctx.tenant_id,
            deal_id=ctx.deal_id,
            run_id=ctx.run_id,
            result_summary=result_summary,
        )
        if shell is not None:
            ctx.methodology_layer2_readiness_package = shell

    def _rehydrate_methodology_company_identity_package(
        self,
        ctx: RunContext,
        result_summary: dict[str, Any],
    ) -> None:
        """Attach a safe company identity package shell when Slice 15 is skipped."""
        if ctx.methodology_company_identity_package is not None:
            return
        shell = _company_identity_package_shell_from_summary(
            tenant_id=ctx.tenant_id,
            deal_id=ctx.deal_id,
            run_id=ctx.run_id,
            result_summary=result_summary,
        )
        if shell is not None:
            ctx.methodology_company_identity_package = shell

    def _execute_extract(self, ctx: RunContext) -> dict[str, Any]:
        """Run extraction pipeline via injected callable.

        Args:
            ctx: Run context with extract_fn.

        Returns:
            Extraction result dict including created_claim_ids.
        """
        result = ctx.extract_fn(
            run_id=ctx.run_id,
            tenant_id=ctx.tenant_id,
            deal_id=ctx.deal_id,
            documents=ctx.documents,
        )
        from idis.services.extraction.canonical_methodology_materialization import (
            merge_main_evidence_claim_ids,
        )

        merged = merge_main_evidence_claim_ids(
            methodology_claim_ids=ctx.methodology_canonical_claim_ids,
            generic_new_claim_ids=_str_list(result.get("created_claim_ids")),
            reused_claim_ids=_str_list(result.get("reused_claim_ids")),
        )
        result["created_claim_ids"] = list(merged)
        return result

    def _execute_grade(
        self,
        ctx: RunContext,
        accumulated: dict[str, Any],
    ) -> dict[str, Any]:
        """Run Sanad auto-grading via injected callable.

        Args:
            ctx: Run context with grade_fn and audit_sink.
            accumulated: Must contain created_claim_ids from EXTRACT step.

        Returns:
            Grading summary dict.
        """
        created_claim_ids = accumulated.get("created_claim_ids", [])
        return ctx.grade_fn(
            run_id=ctx.run_id,
            tenant_id=ctx.tenant_id,
            deal_id=ctx.deal_id,
            created_claim_ids=created_claim_ids,
            audit_sink=self._audit,
        )

    def _execute_calc(
        self,
        ctx: RunContext,
        accumulated: dict[str, Any],
    ) -> dict[str, Any]:
        """Run deterministic calculations via injected callable.

        Fail-closed: raises ValueError if calc_fn is not provided.

        Args:
            ctx: Run context with optional calc_fn and calc_types.
            accumulated: Must contain created_claim_ids from EXTRACT step.

        Returns:
            Calculation result dict including calc_ids and hashes.

        Raises:
            ValueError: If ctx.calc_fn is None (fail-closed).
        """
        if ctx.calc_fn is None:
            raise ValueError("calc_fn not provided")

        created_claim_ids = accumulated.get("created_claim_ids", [])
        return ctx.calc_fn(
            run_id=ctx.run_id,
            tenant_id=ctx.tenant_id,
            deal_id=ctx.deal_id,
            created_claim_ids=created_claim_ids,
            calc_types=ctx.calc_types,
        )

    def _execute_enrichment(
        self,
        ctx: RunContext,
        accumulated: dict[str, Any],
    ) -> dict[str, Any]:
        """Run enrichment via injected callable.

        Fail-closed: raises ValueError if enrich_fn is not provided.

        Args:
            ctx: Run context with optional enrich_fn.
            accumulated: Must contain created_claim_ids and calc_ids from prior steps.

        Returns:
            Enrichment result dict with provider_count, result_count, blocked_count.

        Raises:
            ValueError: If ctx.enrich_fn is None (fail-closed).
        """
        if ctx.enrich_fn is None:
            raise ValueError("enrich_fn not provided")

        created_claim_ids = accumulated.get("created_claim_ids", [])
        calc_ids = accumulated.get("calc_ids", [])
        return ctx.enrich_fn(
            run_id=ctx.run_id,
            tenant_id=ctx.tenant_id,
            deal_id=ctx.deal_id,
            created_claim_ids=created_claim_ids,
            calc_ids=calc_ids,
        )

    def _execute_graph_evidence(
        self,
        ctx: RunContext,
        accumulated: dict[str, Any],
    ) -> dict[str, Any]:
        """Run Neo4j graph projection/retrieval visibility via injected callable."""
        if ctx.graph_fn is None:
            return {
                "graph_status": "skipped",
                "graph_projection": {"status": "skipped"},
                "graph_retrieval": {"status": "skipped"},
            }

        return ctx.graph_fn(
            run_id=ctx.run_id,
            tenant_id=ctx.tenant_id,
            deal_id=ctx.deal_id,
            documents=ctx.documents,
            created_claim_ids=accumulated.get("created_claim_ids", []),
            calc_ids=accumulated.get("calc_ids", []),
        )

    def _execute_rag_evidence(
        self,
        ctx: RunContext,
        accumulated: dict[str, Any],
    ) -> dict[str, Any]:
        """Run pgvector indexing/probe retrieval visibility via injected callable."""
        if ctx.rag_fn is None:
            return {
                "rag_status": "skipped",
                "rag_indexing": {"status": "skipped", "indexed_span_count": 0},
                "rag_retrieval": {
                    "status": "skipped",
                    "retrieval_mode": "probe",
                    "probe_count": 0,
                    "match_count": 0,
                    "matches": [],
                },
            }

        graph_retrieval = accumulated.get("graph_retrieval")
        graph_conclusions = (
            graph_retrieval.get("graph_conclusions") if isinstance(graph_retrieval, dict) else None
        )
        return ctx.rag_fn(
            run_id=ctx.run_id,
            tenant_id=ctx.tenant_id,
            deal_id=ctx.deal_id,
            documents=ctx.documents,
            calc_ids=accumulated.get("calc_ids", []),
            graph_conclusions=graph_conclusions,
        )

    def _execute_debate(
        self,
        ctx: RunContext,
        accumulated: dict[str, Any],
    ) -> dict[str, Any]:
        """Run debate via injected callable.

        Fail-closed: raises ValueError if debate_fn is not provided.

        Args:
            ctx: Run context with optional debate_fn.
            accumulated: Must contain created_claim_ids and calc_ids from prior steps.
                May contain rag_retrieval from the RAG_EVIDENCE step (Slice91).

        Returns:
            Debate result dict including stop_reason and muhasabah_passed.

        Raises:
            ValueError: If ctx.debate_fn is None (fail-closed).
        """
        if ctx.debate_fn is None:
            raise ValueError("debate_fn not provided")

        created_claim_ids = accumulated.get("created_claim_ids", [])
        calc_ids = accumulated.get("calc_ids", [])
        rag_retrieval = accumulated.get("rag_retrieval")
        return ctx.debate_fn(
            run_id=ctx.run_id,
            tenant_id=ctx.tenant_id,
            deal_id=ctx.deal_id,
            created_claim_ids=created_claim_ids,
            calc_ids=calc_ids,
            rag_retrieval=rag_retrieval if isinstance(rag_retrieval, dict) else None,
        )

    def _execute_layer2_ic_challenge(
        self,
        ctx: RunContext,
        accumulated: dict[str, Any],
    ) -> dict[str, Any]:
        """Run the distinct Layer 2 IC challenge via injected callable."""
        if ctx.layer2_ic_challenge_fn is None:
            raise ValueError("layer2_ic_challenge_fn not provided")

        created_claim_ids = accumulated.get("created_claim_ids", [])
        calc_ids = accumulated.get("calc_ids", [])
        debate_summary = {
            "debate_id": accumulated.get("debate_id"),
            "stop_reason": accumulated.get("stop_reason"),
            "round_number": accumulated.get("round_number"),
            "muhasabah_passed": accumulated.get("muhasabah_passed"),
            "agent_output_count": accumulated.get("agent_output_count"),
        }
        # Durable Layer-1 reference (Slice92): the VEP step's persisted package ids
        # arrive via its layer1_persistence summary block; null-safe like rag_retrieval.
        layer1_persistence = accumulated.get("layer1_persistence")
        vep_package_ids = (
            layer1_persistence.get("package_ids") if isinstance(layer1_persistence, dict) else None
        )
        call_kwargs = {
            "run_id": ctx.run_id,
            "tenant_id": ctx.tenant_id,
            "deal_id": ctx.deal_id,
            "debate_summary": debate_summary,
            "created_claim_ids": created_claim_ids,
            "calc_ids": calc_ids,
            "vep_package_ids": vep_package_ids if isinstance(vep_package_ids, list) else None,
            "graph_evidence": {
                "graph_status": accumulated.get("graph_status"),
                "graph_projection": accumulated.get("graph_projection"),
                "graph_retrieval": accumulated.get("graph_retrieval"),
            },
            "rag_evidence": {
                "rag_status": accumulated.get("rag_status"),
                "rag_indexing": accumulated.get("rag_indexing"),
                "rag_retrieval": accumulated.get("rag_retrieval"),
            },
            "enrichment_refs": accumulated.get("enrichment_refs", {}),
        }
        if ctx.stage_native_output_writes_fn is not None:
            call_kwargs["defer_persistence"] = True
        result = ctx.layer2_ic_challenge_fn(**call_kwargs)
        record = result.pop("_layer2_ic_challenge_record", None)
        if record is not None:
            ctx.layer2_ic_challenge_record = record
        return result

    def _execute_analysis(
        self,
        ctx: RunContext,
        accumulated: dict[str, Any],
    ) -> dict[str, Any]:
        """Run analysis agents via injected callable.

        Fail-closed: raises ValueError if analysis_fn is not provided.

        Args:
            ctx: Run context with optional analysis_fn.
            accumulated: Must contain created_claim_ids, calc_ids, enrichment_refs.
                May contain rag_retrieval from the RAG_EVIDENCE step (Slice91).

        Returns:
            Analysis result dict with agent_count, report_ids, bundle_id.

        Raises:
            ValueError: If ctx.analysis_fn is None (fail-closed).
        """
        if ctx.analysis_fn is None:
            raise ValueError("analysis_fn not provided")

        created_claim_ids = accumulated.get("created_claim_ids", [])
        calc_ids = accumulated.get("calc_ids", [])
        enrichment_refs = accumulated.get("enrichment_refs", {})
        rag_retrieval = accumulated.get("rag_retrieval")
        return ctx.analysis_fn(
            run_id=ctx.run_id,
            tenant_id=ctx.tenant_id,
            deal_id=ctx.deal_id,
            created_claim_ids=created_claim_ids,
            calc_ids=calc_ids,
            enrichment_refs=enrichment_refs,
            rag_retrieval=rag_retrieval if isinstance(rag_retrieval, dict) else None,
        )

    def _execute_scoring(
        self,
        ctx: RunContext,
        accumulated: dict[str, Any],
    ) -> dict[str, Any]:
        """Run scoring engine via injected callable.

        Fail-closed: raises ValueError if scoring_fn is not provided.

        Args:
            ctx: Run context with optional scoring_fn.
            accumulated: Must contain _analysis_bundle and _analysis_context.

        Returns:
            Scoring result dict with composite_score, band, routing.

        Raises:
            ValueError: If ctx.scoring_fn is None (fail-closed).
        """
        if ctx.scoring_fn is None:
            raise ValueError("scoring_fn not provided")

        return ctx.scoring_fn(
            run_id=ctx.run_id,
            tenant_id=ctx.tenant_id,
            deal_id=ctx.deal_id,
            analysis_bundle=accumulated.get("_analysis_bundle"),
            analysis_context=accumulated.get("_analysis_context"),
        )

    def _execute_deliverables(
        self,
        ctx: RunContext,
        accumulated: dict[str, Any],
    ) -> dict[str, Any]:
        """Run deliverables generation via injected callable.

        Fail-closed: raises ValueError if deliverables_fn is not provided.

        Args:
            ctx: Run context with optional deliverables_fn.
            accumulated: Must contain _analysis_bundle, _analysis_context, _scorecard.

        Returns:
            Deliverables result dict with deliverable_count, types, deliverable_ids.

        Raises:
            ValueError: If ctx.deliverables_fn is None (fail-closed).
        """
        if ctx.deliverables_fn is None:
            raise ValueError("deliverables_fn not provided")

        return ctx.deliverables_fn(
            run_id=ctx.run_id,
            tenant_id=ctx.tenant_id,
            deal_id=ctx.deal_id,
            analysis_bundle=accumulated.get("_analysis_bundle"),
            analysis_context=accumulated.get("_analysis_context"),
            scorecard=accumulated.get("_scorecard"),
            graph_evidence={
                "graph_status": accumulated.get("graph_status"),
                "graph_projection": accumulated.get("graph_projection"),
                "graph_retrieval": accumulated.get("graph_retrieval"),
            },
            rag_evidence={
                "rag_status": accumulated.get("rag_status"),
                "rag_indexing": accumulated.get("rag_indexing"),
                "rag_retrieval": accumulated.get("rag_retrieval"),
            },
            layer2_evidence={
                "status": accumulated.get("status"),
                "layer2_challenge_ids": accumulated.get("layer2_challenge_ids"),
                "source_debate_ids": accumulated.get("source_debate_ids"),
                "claim_ids": accumulated.get("claim_ids"),
                "calc_ids": accumulated.get("calc_ids"),
                "finding_ids": accumulated.get("finding_ids"),
                "finding_count": accumulated.get("finding_count"),
                "unresolved_question_count": accumulated.get("unresolved_question_count"),
                "by_finding_type": accumulated.get("by_finding_type"),
                "by_severity": accumulated.get("by_severity"),
                "muhasabah_passed": accumulated.get("muhasabah_passed"),
            },
            enrichment_evidence={
                "enrichment_ledger": accumulated.get("enrichment_ledger"),
                "enrichment_refs": accumulated.get("enrichment_refs"),
            },
            # Durable Layer-1 VEP visibility (Slice92): the VEP step's persisted
            # summary block feeds the safe vep_evidence export package; null-safe.
            vep_evidence=(
                accumulated.get("layer1_persistence")
                if isinstance(accumulated.get("layer1_persistence"), dict)
                else None
            ),
            # Run-level LLM provenance (Slice94): the per-step provenance blocks feed the
            # safe provenance appendix; each is whitelisted again at export (null-safe).
            run_provenance={
                "extraction_provenance": accumulated.get("extraction_provenance"),
                "debate_provenance": accumulated.get("debate_provenance"),
                "analysis_provenance": accumulated.get("analysis_provenance"),
                "scoring_provenance": accumulated.get("scoring_provenance"),
                "layer2_provenance": accumulated.get("layer2_provenance"),
            },
        )

    @staticmethod
    def _is_lease_control_failure(exc: BaseException) -> bool:
        return getattr(exc, "code", None) in {
            "WORKER_LEASE_FENCED",
            "WORKER_LEASE_HEARTBEAT_FAILED",
            "WORKER_LEASE_BOUNDARY_COMMIT_FAILED",
        }

    def _start_step_boundary(
        self,
        ctx: RunContext,
        step_name: StepName,
        existing: RunStep | None,
    ) -> RunStep:
        """Commit lease renewal, RUNNING ledger state, and audit atomically."""
        if ctx.step_boundary_uow_fn is None:
            return self._start_step(
                ctx,
                step_name,
                existing,
                lease_owner_id=ctx.lease_owner_id,
                lease_fencing_token=ctx.lease_fencing_token,
            )
        if ctx.lease_owner_id is None or ctx.lease_fencing_token is None:
            raise RunStepBlockedError(
                "WORKER_LEASE_FENCED",
                "Step boundary is missing its complete lease identity",
            )

        captured: dict[str, RunStep] = {}

        def mutation(
            *,
            db_conn: Any = None,
            lease_owner_id: str | None = ctx.lease_owner_id,
            lease_fencing_token: int | None = ctx.lease_fencing_token,
            **_kwargs: Any,
        ) -> None:
            captured["step"] = self._start_step(
                ctx,
                step_name,
                existing,
                db_conn=db_conn,
                lease_owner_id=lease_owner_id,
                lease_fencing_token=lease_fencing_token,
            )

        ctx.step_boundary_uow_fn(
            run_id=ctx.run_id,
            tenant_id=ctx.tenant_id,
            step_name=step_name,
            boundary="START",
            lease_owner_id=ctx.lease_owner_id,
            lease_fencing_token=ctx.lease_fencing_token,
            mutation=mutation,
            native_output_writes=(),
        )
        if ctx.after_step_boundary_commit_fn is not None:
            ctx.after_step_boundary_commit_fn(step_name=step_name, boundary="START")
        return captured["step"]

    def _complete_step_boundary(
        self,
        ctx: RunContext,
        step: RunStep,
        result: dict[str, Any],
    ) -> None:
        """Commit staged native outputs, COMPLETED ledger state, and audit together."""
        if ctx.step_boundary_uow_fn is None:
            if ctx.stage_native_output_writes_fn is not None:
                raise RunStepBlockedError(
                    "PIPELINE_CONTRACT_UNAVAILABLE",
                    "Staged output completion requires a boundary unit of work",
                )
            self._complete_step(
                step,
                result,
                lease_owner_id=ctx.lease_owner_id,
                lease_fencing_token=ctx.lease_fencing_token,
            )
            return
        if ctx.lease_owner_id is None or ctx.lease_fencing_token is None:
            raise RunStepBlockedError(
                "WORKER_LEASE_FENCED",
                "Step boundary is missing its complete lease identity",
            )

        staged = (
            tuple(
                ctx.stage_native_output_writes_fn(
                    ctx=ctx,
                    step_name=step.step_name,
                    result_summary=result,
                )
            )
            if ctx.stage_native_output_writes_fn is not None
            else ()
        )
        staged += ctx.methodology_pending_native_output_writes.get(step.step_name, ())

        if self._is_full_strict_v21_context(ctx):
            if ctx.task7_step_completion_fn is None:
                raise RunStepBlockedError(
                    "PIPELINE_CONTRACT_UNAVAILABLE",
                    "Task 7 strict completion composition is unavailable",
                )
            ctx.task7_step_completion_fn(
                ctx=ctx,
                step=step,
                result_summary=result,
                staged_native_output_writes=staged,
            )
            ctx.methodology_pending_native_output_writes.pop(step.step_name, None)
            if ctx.after_step_boundary_commit_fn is not None:
                ctx.after_step_boundary_commit_fn(
                    step_name=step.step_name,
                    boundary="COMPLETE",
                )
            return

        def mutation(
            *,
            db_conn: Any = None,
            lease_owner_id: str | None = ctx.lease_owner_id,
            lease_fencing_token: int | None = ctx.lease_fencing_token,
            **_kwargs: Any,
        ) -> None:
            self._complete_step(
                step,
                result,
                db_conn=db_conn,
                lease_owner_id=lease_owner_id,
                lease_fencing_token=lease_fencing_token,
            )

        ctx.step_boundary_uow_fn(
            run_id=ctx.run_id,
            tenant_id=ctx.tenant_id,
            step_name=step.step_name,
            boundary="COMPLETE",
            lease_owner_id=ctx.lease_owner_id,
            lease_fencing_token=ctx.lease_fencing_token,
            mutation=mutation,
            native_output_writes=staged,
        )
        ctx.methodology_pending_native_output_writes.pop(step.step_name, None)
        if ctx.after_step_boundary_commit_fn is not None:
            ctx.after_step_boundary_commit_fn(step_name=step.step_name, boundary="COMPLETE")

    def _fail_step_boundary(
        self,
        ctx: RunContext,
        step: RunStep,
        exc: Exception,
    ) -> None:
        """Persist FAILED under the same fenced completion-boundary contract."""
        if ctx.step_boundary_uow_fn is None:
            self._fail_step(
                step,
                exc,
                lease_owner_id=ctx.lease_owner_id,
                lease_fencing_token=ctx.lease_fencing_token,
            )
            return
        if ctx.lease_owner_id is None or ctx.lease_fencing_token is None:
            raise RunStepBlockedError(
                "WORKER_LEASE_FENCED",
                "Step boundary is missing its complete lease identity",
            )
        result_summary = getattr(exc, "result_summary", None) or {
            "error_code": getattr(exc, "code", type(exc).__name__.upper())
        }
        staged = (
            tuple(
                ctx.stage_native_output_writes_fn(
                    ctx=ctx,
                    step_name=step.step_name,
                    result_summary=result_summary,
                )
            )
            if ctx.stage_native_output_writes_fn is not None
            else ()
        )

        def mutation(
            *,
            db_conn: Any = None,
            lease_owner_id: str | None = ctx.lease_owner_id,
            lease_fencing_token: int | None = ctx.lease_fencing_token,
            **_kwargs: Any,
        ) -> None:
            self._fail_step(
                step,
                exc,
                db_conn=db_conn,
                lease_owner_id=lease_owner_id,
                lease_fencing_token=lease_fencing_token,
            )

        ctx.step_boundary_uow_fn(
            run_id=ctx.run_id,
            tenant_id=ctx.tenant_id,
            step_name=step.step_name,
            boundary="COMPLETE",
            lease_owner_id=ctx.lease_owner_id,
            lease_fencing_token=ctx.lease_fencing_token,
            mutation=mutation,
            native_output_writes=staged,
        )
        if ctx.after_step_boundary_commit_fn is not None:
            ctx.after_step_boundary_commit_fn(step_name=step.step_name, boundary="COMPLETE")

    def _fail_completed_resume_boundary(
        self,
        ctx: RunContext,
        step: RunStep,
        exc: Exception,
    ) -> None:
        """Fence a completed ledger row whose immutable resume checkpoint is invalid."""
        if self._is_full_strict_v21_context(ctx):
            if isinstance(exc, RunStepBlockedError):
                raise exc
            raise RunStepBlockedError(
                "STEP_REHYDRATION_FAILED",
                "Completed strict step failed immutable rehydration",
            ) from exc
        if (
            ctx.step_boundary_uow_fn is None
            and ctx.lease_owner_id is None
            and ctx.lease_fencing_token is None
        ):
            # Legacy/non-worker strict validation has no lease to fence.  Preserve
            # its fail-closed ledger behavior; the production claimed path below
            # must always use the fenced boundary.
            self._fail_step(step, exc)
            return
        if (
            ctx.step_boundary_uow_fn is None
            or ctx.lease_owner_id is None
            or ctx.lease_fencing_token is None
        ):
            raise RunStepBlockedError(
                "PIPELINE_CONTRACT_UNAVAILABLE",
                "Fenced completed-step resume failure boundary is unavailable",
            ) from exc

        def mutation(
            *,
            db_conn: Any = None,
            lease_owner_id: str | None = ctx.lease_owner_id,
            lease_fencing_token: int | None = ctx.lease_fencing_token,
            **_kwargs: Any,
        ) -> None:
            self._fail_step(
                step,
                exc,
                db_conn=db_conn,
                lease_owner_id=lease_owner_id,
                lease_fencing_token=lease_fencing_token,
            )

        ctx.step_boundary_uow_fn(
            run_id=ctx.run_id,
            tenant_id=ctx.tenant_id,
            step_name=step.step_name,
            boundary="COMPLETE",
            lease_owner_id=ctx.lease_owner_id,
            lease_fencing_token=ctx.lease_fencing_token,
            mutation=mutation,
            native_output_writes=(),
        )
        if ctx.after_step_boundary_commit_fn is not None:
            ctx.after_step_boundary_commit_fn(step_name=step.step_name, boundary="COMPLETE")

    def _boundary_steps_repository(self, ctx: RunContext, db_conn: Any) -> RunStepsRepo:
        if db_conn is None:
            return self._steps_repo
        from idis.persistence.repositories.run_steps import get_run_steps_repository

        return get_run_steps_repository(db_conn, ctx.tenant_id)

    def _require_v5_persisted_step(
        self,
        ctx: RunContext,
        step_name: StepName,
        existing: RunStep | None,
    ) -> RunStep | None:
        """Require strict admission's precreated step and exact immutable identity."""
        if not self._is_full_strict_v21_context(ctx):
            return existing
        if existing is None:
            raise RunStepBlockedError(
                "STEP_REHYDRATION_FAILED",
                "Strict admission did not persist the required canonical step",
            )
        if ctx.pipeline_contract_hash is None:
            raise RunStepBlockedError(
                "PIPELINE_CONTRACT_UNAVAILABLE",
                "Strict run has no pipeline contract hash",
            )
        try:
            contract = require_pipeline_contract_for_admission(
                registry=self._pipeline_contract_registry,
                contract_version="full-strict-v21",
                proof_contract_hash=ctx.pipeline_contract_hash,
            )
        except PipelineContractError as exc:
            raise RunStepBlockedError(exc.code, str(exc)) from exc
        spec = next(
            (candidate for candidate in contract.steps if candidate.step_name == step_name.value),
            None,
        )
        if (
            spec is None
            or existing.tenant_id != ctx.tenant_id
            or existing.run_id != ctx.run_id
            or existing.step_name is not step_name
            or existing.step_order != spec.step_order
            or existing.pipeline_contract_version != contract.contract_version
            or existing.pipeline_contract_hash != contract.contract_hash
            or existing.semantic_rule_version != spec.semantic_rule_version
            or existing.semantic_rule_hash != spec.semantic_rule_hash
        ):
            raise RunStepBlockedError(
                "PIPELINE_CONTRACT_HASH_MISMATCH",
                "Persisted strict step differs from the reviewed contract",
            )
        return existing

    def _start_step(
        self,
        ctx: RunContext,
        step_name: StepName,
        existing: RunStep | None,
        *,
        db_conn: Any = None,
        lease_owner_id: str | None = None,
        lease_fencing_token: int | None = None,
    ) -> RunStep:
        """Create or reuse a RunStep record and mark it RUNNING.

        Args:
            ctx: Run context.
            step_name: Canonical step name.
            existing: Previously persisted step (for retry), or None.

        Returns:
            RunStep in RUNNING status.
        """
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")

        repository = self._boundary_steps_repository(ctx, db_conn)
        existing = self._require_v5_persisted_step(ctx, step_name, existing)
        if existing is not None:
            existing.status = StepStatus.RUNNING
            existing.started_at = now
            existing.finished_at = None
            existing.error_code = None
            existing.error_message = None
            existing.retry_count += 1
            if lease_owner_id is not None and lease_fencing_token is not None:
                step = repository.update(
                    existing,
                    lease_owner_id=lease_owner_id,
                    lease_fencing_token=lease_fencing_token,
                )
            else:
                step = repository.update(existing)
        else:
            step = RunStep(
                step_id=str(uuid.uuid4()),
                run_id=ctx.run_id,
                tenant_id=ctx.tenant_id,
                step_name=step_name,
                step_order=STEP_ORDER[step_name],
                status=StepStatus.RUNNING,
                started_at=now,
            )
            if lease_owner_id is not None and lease_fencing_token is not None:
                step = repository.create(
                    step,
                    lease_owner_id=lease_owner_id,
                    lease_fencing_token=lease_fencing_token,
                )
            else:
                step = repository.create(step)

        self._emit_audit_event(
            event_type=f"run.step.{step_name.value.lower()}.started",
            tenant_id=ctx.tenant_id,
            details={
                "run_id": ctx.run_id,
                "step_id": step.step_id,
                "step_name": step_name.value,
                "retry_count": step.retry_count,
            },
            db_conn=db_conn,
        )
        return step

    @staticmethod
    def _sanitize_for_json(obj: Any) -> Any:
        """Recursively convert non-serializable objects for JSON storage.

        Handles Pydantic models, UUIDs, datetimes, and nested structures.
        Fail-closed: raises TypeError for truly exotic types that
        json.dumps cannot handle.

        Args:
            obj: Any object that may appear in a step result_summary.

        Returns:
            A JSON-safe equivalent of the input.
        """
        if isinstance(obj, BaseModel):
            return obj.model_dump(mode="json")
        if isinstance(obj, dict):
            return {k: RunOrchestrator._sanitize_for_json(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [RunOrchestrator._sanitize_for_json(item) for item in obj]
        if hasattr(obj, "isoformat"):
            return obj.isoformat()
        if isinstance(obj, (str, int, float, bool, type(None))):
            return obj
        try:
            json.dumps(obj)
            return obj
        except TypeError:
            return str(obj)

    def _complete_step(
        self,
        step: RunStep,
        result: dict[str, Any],
        *,
        db_conn: Any = None,
        lease_owner_id: str | None = None,
        lease_fencing_token: int | None = None,
    ) -> None:
        """Mark a step COMPLETED and persist its result summary.

        Args:
            step: The running step to complete.
            result: Step output to store in result_summary.
        """
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        step.status = StepStatus.COMPLETED
        step.finished_at = now
        step.result_summary = self._sanitize_for_json(result)
        result_summary_sha256 = canonical_result_summary_sha256(step.result_summary)
        if db_conn is None:
            repository = self._steps_repo
        else:
            from idis.persistence.repositories.run_steps import get_run_steps_repository

            repository = get_run_steps_repository(db_conn, step.tenant_id)
        if lease_owner_id is not None and lease_fencing_token is not None:
            repository.update(
                step,
                lease_owner_id=lease_owner_id,
                lease_fencing_token=lease_fencing_token,
            )
        else:
            repository.update(step)

        self._emit_audit_event(
            event_type=f"run.step.{step.step_name.value.lower()}.completed",
            tenant_id=step.tenant_id,
            details={
                "run_id": step.run_id,
                "step_id": step.step_id,
                "step_name": step.step_name.value,
                "result_keys": self._safe_audit_result_keys(result),
                "result_summary_sha256": result_summary_sha256,
            },
            db_conn=db_conn,
        )

    def _fail_step(
        self,
        step: RunStep,
        exc: Exception,
        *,
        db_conn: Any = None,
        lease_owner_id: str | None = None,
        lease_fencing_token: int | None = None,
    ) -> None:
        """Mark a step FAILED and persist error details.

        Args:
            step: The running step that failed.
            exc: The exception that caused failure.
        """
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        step.status = StepStatus.FAILED
        step.finished_at = now
        step.error_code = getattr(exc, "code", type(exc).__name__.upper())
        step.error_message = str(exc)[:500]
        result_summary = getattr(exc, "result_summary", None)
        if result_summary is not None:
            step.result_summary = self._sanitize_for_json(result_summary)
        if db_conn is None:
            repository = self._steps_repo
        else:
            from idis.persistence.repositories.run_steps import get_run_steps_repository

            repository = get_run_steps_repository(db_conn, step.tenant_id)
        if lease_owner_id is not None and lease_fencing_token is not None:
            repository.update(
                step,
                lease_owner_id=lease_owner_id,
                lease_fencing_token=lease_fencing_token,
            )
        else:
            repository.update(step)

        self._emit_audit_event(
            event_type=f"run.step.{step.step_name.value.lower()}.failed",
            tenant_id=step.tenant_id,
            details={
                "run_id": step.run_id,
                "step_id": step.step_id,
                "step_name": step.step_name.value,
                "error_code": step.error_code,
                "error_message": SAFE_STEP_ERROR_MESSAGE,
            },
            db_conn=db_conn,
        )

    def _create_blocked_step(self, ctx: RunContext, step_name: StepName) -> RunStep:
        """Create a BLOCKED step record for unimplemented steps.

        Args:
            ctx: Run context.
            step_name: The unimplemented step.

        Returns:
            RunStep in BLOCKED status.
        """
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        step = RunStep(
            step_id=str(uuid.uuid4()),
            run_id=ctx.run_id,
            tenant_id=ctx.tenant_id,
            step_name=step_name,
            step_order=STEP_ORDER[step_name],
            status=StepStatus.BLOCKED,
            started_at=now,
            finished_at=now,
            error_code=BLOCK_REASON_DEBATE_NOT_IMPLEMENTED,
            error_message="Step is not yet implemented",
        )
        return self._steps_repo.create(step)

    def _emit_audit_event(
        self,
        event_type: str,
        tenant_id: str,
        details: dict[str, Any],
        *,
        db_conn: Any = None,
    ) -> None:
        """Emit an audit event, fail-closed on any error.

        Args:
            event_type: Audit event type string.
            tenant_id: Tenant UUID for the event.
            details: Event payload.

        Raises:
            AuditSinkError: If audit emission fails (fail-closed).

        The event is shaped to satisfy the v6.3 audit-event validator: a real
        actor (originating authenticated actor, or the defined service principal
        for system-only runs — never "unknown"), under the deal.* taxonomy with
        resource_type=deal and safe-only payload fields.
        """
        actor_id, actor_type = self._resolve_origin_actor()
        run_id = str(details.get("run_id") or "")
        taxonomy_event_type = event_type if event_type.startswith("deal.") else f"deal.{event_type}"
        severity = "MEDIUM" if event_type.endswith((".failed", ".blocked")) else "LOW"
        deal_id = self._origin_deal_id

        result_summary_sha256 = details.get("result_summary_sha256")
        checkpoint_hashes = (
            [result_summary_sha256] if isinstance(result_summary_sha256, str) else []
        )
        event: dict[str, Any] = {
            "event_id": str(uuid.uuid4()),
            "occurred_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "tenant_id": tenant_id,
            "actor": {"actor_type": actor_type, "actor_id": actor_id},
            "request": {
                "request_id": run_id or str(uuid.uuid4()),
                "method": "POST",
                "path": f"/v1/runs/{run_id}" if run_id else "/v1/runs",
                "status_code": 200,
            },
            "resource": {
                "resource_type": "deal",
                "resource_id": deal_id or run_id or "unknown",
                "deal_id": deal_id,
            },
            "event_type": taxonomy_event_type,
            "severity": severity,
            "summary": f"{taxonomy_event_type} for run {run_id}" if run_id else taxonomy_event_type,
            "payload": {"safe": details, "hashes": checkpoint_hashes, "refs": []},
        }

        validation_result = validate_audit_event(event)
        if not validation_result.passed:
            error_codes = [error.code for error in validation_result.errors]
            raise AuditSinkError(
                f"Run-step audit event failed validation, refusing to emit: {error_codes}"
            )

        emit_in_tx = getattr(self._audit, "emit_in_tx", None)
        if db_conn is not None and callable(emit_in_tx):
            emit_in_tx(db_conn, event)
        else:
            self._audit.emit(event)

    DEFAULT_SERVICE_ACTOR_ID = "idis-worker"

    def _resolve_origin_actor(self) -> tuple[str, str]:
        """Return the (actor_id, actor_type) for run-step audit events.

        User-originated runs use the persisted originating actor; system-only
        runs use the defined service principal. Never returns "unknown".
        """
        if self._origin_actor_id:
            actor_type = (
                self._origin_actor_type
                if self._origin_actor_type in ("HUMAN", "SERVICE")
                else "SERVICE"
            )
            return self._origin_actor_id, actor_type
        return self.DEFAULT_SERVICE_ACTOR_ID, "SERVICE"

    @staticmethod
    def _safe_audit_result_keys(result: dict[str, Any]) -> list[str]:
        """Return non-sensitive summary keys for audit metadata."""
        safe_keys: list[str] = []
        for key in result:
            normalized = str(key).lower()
            if any(part in normalized for part in SENSITIVE_AUDIT_RESULT_KEY_PARTS):
                continue
            safe_keys.append(str(key))
        return safe_keys

    @staticmethod
    def _compute_final_status(
        steps: list[RunStep],
        *,
        expected_step_sequence: tuple[StepName, ...] | None = None,
    ) -> str:
        """Derive the final run status from step statuses.

        Args:
            steps: All step records for the run.

        Returns:
            SUCCEEDED if all steps completed, FAILED otherwise (fail-closed).
        """
        executable_steps = [step for step in steps if step.step_name != StepName.RUN_LIFECYCLE]
        if not executable_steps:
            return "FAILED"

        if expected_step_sequence is not None:
            actual_sequence = tuple(step.step_name for step in executable_steps)
            actual_orders = tuple(step.step_order for step in executable_steps)
            if (
                actual_sequence != expected_step_sequence
                or actual_orders != tuple(range(len(expected_step_sequence)))
                or len({step.step_id for step in executable_steps}) != len(expected_step_sequence)
                or any(step.status is not StepStatus.COMPLETED for step in executable_steps)
            ):
                return "FAILED"
            return "SUCCEEDED"

        if any(step.status == StepStatus.FAILED for step in executable_steps):
            return "FAILED"
        return "SUCCEEDED"


def _materialized_claim_shell_from_mapping(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    mapping: dict[str, Any],
) -> RunScopedMaterializedClaimShell | None:
    source_span_ids = mapping.get("source_span_ids")
    document_id = _optional_str(mapping.get("document_id"))
    if not isinstance(source_span_ids, list) or document_id is None:
        return None
    source_refs = [
        MaterializedClaimSourceRef(
            document_id=document_id,
            source_span_id=str(source_span_id),
            locator=None,
        )
        for source_span_id in source_span_ids
        if str(source_span_id).strip()
    ]
    if not source_refs:
        return None
    try:
        return RunScopedMaterializedClaimShell(
            claim_id=str(mapping.get("claim_id") or ""),
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            source_refs=source_refs,
            methodology_question_id=str(mapping.get("methodology_question_id") or ""),
            coverage_record_id=str(mapping.get("coverage_record_id") or ""),
            extraction_task_id=str(mapping.get("extraction_task_id") or ""),
            extraction_output_id=str(mapping.get("extraction_output_id") or ""),
            status="materialized_unverified",
        )
    except ValueError:
        return None


def _evidence_item_shell_from_mapping(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    mapping: dict[str, Any],
) -> RunScopedEvidenceItemShell | None:
    try:
        return RunScopedEvidenceItemShell(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            claim_id=str(mapping.get("claim_id") or ""),
            evidence_id=str(mapping.get("evidence_id") or ""),
            document_id=str(mapping.get("document_id") or ""),
            source_span_id=str(mapping.get("source_span_id") or ""),
            methodology_question_id=str(mapping.get("methodology_question_id") or ""),
            coverage_record_id=str(mapping.get("coverage_record_id") or ""),
            extraction_task_id=str(mapping.get("extraction_task_id") or ""),
            extraction_output_id=str(mapping.get("extraction_output_id") or ""),
            status="materialized_unverified",
        )
    except ValueError:
        return None


def _evidence_provenance_from_mapping(
    mapping: dict[str, Any],
) -> RunScopedEvidenceProvenanceRef | None:
    try:
        return RunScopedEvidenceProvenanceRef(
            document_id=str(mapping.get("document_id") or ""),
            source_span_id=str(mapping.get("source_span_id") or ""),
            locator=None,
        )
    except ValueError:
        return None


def _sanad_shell_from_mapping(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    mapping: dict[str, Any],
) -> RunScopedSanadShell | None:
    try:
        evidence_ids = _str_list(mapping.get("evidence_ids"))
        defect_ids = _str_list(mapping.get("defect_ids"))
        chain_node_types = _str_list(mapping.get("chain_node_types"))
        return RunScopedSanadShell(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            claim_id=str(mapping.get("claim_id") or ""),
            sanad_id=str(mapping.get("sanad_id") or ""),
            primary_evidence_id=str(mapping.get("primary_evidence_id") or ""),
            evidence_ids=evidence_ids,
            source_span_ids=_str_list(mapping.get("source_span_ids")),
            sanad_grade=SanadGrade(str(mapping.get("sanad_grade") or "D")),
            defect_ids=defect_ids,
            transmission_chain_node_count=_int_value(
                mapping.get("transmission_chain_node_count"),
                len(chain_node_types) or 1,
            ),
            chain_node_types=chain_node_types or ["INGEST", "EXTRACT"],
            methodology_question_id=str(mapping.get("methodology_question_id") or ""),
            coverage_record_id=str(mapping.get("coverage_record_id") or ""),
            extraction_task_id=str(mapping.get("extraction_task_id") or ""),
            extraction_output_id=str(mapping.get("extraction_output_id") or ""),
            status="created_linked_graded",
        )
    except ValueError:
        return None


def _sanad_links_from_summary(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    result_summary: dict[str, Any],
) -> list[RunScopedSanadLinkRecord]:
    raw_links = result_summary.get("claim_sanad_links")
    links: list[Any] = raw_links if isinstance(raw_links, list) else []
    records: list[RunScopedSanadLinkRecord] = []
    for link in links:
        if not isinstance(link, dict):
            continue
        try:
            records.append(
                RunScopedSanadLinkRecord(
                    tenant_id=tenant_id,
                    deal_id=deal_id,
                    run_id=run_id,
                    claim_id=str(link.get("claim_id") or ""),
                    sanad_id=str(link.get("sanad_id") or ""),
                    evidence_ids=_str_list(link.get("evidence_ids")),
                    source_span_ids=_str_list(link.get("source_span_ids")),
                    claim_link_status=str(link.get("claim_link_status") or "linked_run_scoped"),
                )
            )
        except ValueError:
            continue
    return records


def _sanad_grades_from_summary(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    result_summary: dict[str, Any],
) -> list[RunScopedSanadGradeRecord]:
    raw_grades = result_summary.get("grade_records")
    grades: list[Any] = raw_grades if isinstance(raw_grades, list) else []
    records: list[RunScopedSanadGradeRecord] = []
    for grade in grades:
        if not isinstance(grade, dict):
            continue
        try:
            records.append(
                RunScopedSanadGradeRecord(
                    tenant_id=tenant_id,
                    deal_id=deal_id,
                    run_id=run_id,
                    claim_id=str(grade.get("claim_id") or ""),
                    sanad_id=str(grade.get("sanad_id") or ""),
                    sanad_grade=SanadGrade(str(grade.get("sanad_grade") or "D")),
                    grade_reason_codes=_str_list(grade.get("grade_reason_codes")),
                    defect_ids=_str_list(grade.get("defect_ids")),
                    fatal_defect_count=_int_value(grade.get("fatal_defect_count"), 0),
                    major_defect_count=_int_value(grade.get("major_defect_count"), 0),
                    minor_defect_count=_int_value(grade.get("minor_defect_count"), 0),
                )
            )
        except ValueError:
            continue
    return records


def _sanad_defects_from_summary(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    result_summary: dict[str, Any],
) -> list[RunScopedSanadDefectShell]:
    raw_defects = result_summary.get("defect_shells")
    defects: list[Any] = raw_defects if isinstance(raw_defects, list) else []
    shells: list[RunScopedSanadDefectShell] = []
    for defect in defects:
        if not isinstance(defect, dict):
            continue
        try:
            shells.append(
                RunScopedSanadDefectShell(
                    tenant_id=tenant_id,
                    deal_id=deal_id,
                    run_id=run_id,
                    claim_id=str(defect.get("claim_id") or ""),
                    sanad_id=str(defect.get("sanad_id") or ""),
                    defect_id=str(defect.get("defect_id") or ""),
                    defect_type=DefectType(str(defect.get("defect_type") or "INCONSISTENCY")),
                    severity=DefectSeverity(str(defect.get("severity") or "MINOR")),
                    cure_protocol=CureProtocol(
                        str(defect.get("cure_protocol") or "HUMAN_ARBITRATION")
                    ),
                    status=DefectStatus(str(defect.get("status") or "OPEN")),
                )
            )
        except ValueError:
            continue
    return shells


def _calculation_shell_from_summary(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    shell: dict[str, Any],
) -> RunScopedCalculationShell | None:
    try:
        return RunScopedCalculationShell(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            calc_id=str(shell.get("calc_id") or ""),
            calc_type=CalcType(str(shell.get("calc_type") or "GROSS_MARGIN")),
            input_claim_ids=_str_list(shell.get("input_claim_ids")),
            input_sanad_ids=_str_list(shell.get("input_sanad_ids")),
            formula_hash=str(shell.get("formula_hash") or ""),
            reproducibility_hash=str(shell.get("reproducibility_hash") or ""),
            output_primary_value=str(shell.get("output_primary_value") or ""),
            output_unit=_optional_str(shell.get("output_unit")),
            output_currency=_optional_str(shell.get("output_currency")),
            methodology_question_id=str(shell.get("methodology_question_id") or ""),
            extraction_task_id=str(shell.get("extraction_task_id") or ""),
            coverage_record_id=str(shell.get("coverage_record_id") or ""),
            status=str(shell.get("status") or "created"),
        )
    except ValueError:
        return None


def _calc_sanad_shell_from_summary(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    shell: dict[str, Any],
) -> RunScopedCalcSanadShell | None:
    try:
        return RunScopedCalcSanadShell(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            calc_id=str(shell.get("calc_id") or ""),
            calc_sanad_id=str(shell.get("calc_sanad_id") or ""),
            input_claim_ids=_str_list(shell.get("input_claim_ids")),
            input_min_sanad_grade=CalcSanadGrade(str(shell.get("input_min_sanad_grade") or "D")),
            calc_grade=CalcSanadGrade(str(shell.get("calc_grade") or "D")),
            methodology_question_id=str(shell.get("methodology_question_id") or ""),
            extraction_task_id=str(shell.get("extraction_task_id") or ""),
            coverage_record_id=str(shell.get("coverage_record_id") or ""),
            status=str(shell.get("status") or "created"),
        )
    except ValueError:
        return None


def _truth_dashboard_shell_from_summary(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    result_summary: dict[str, Any],
) -> RunScopedTruthDashboardShell | None:
    dashboard_ids = _str_list(result_summary.get("dashboard_ids"))
    if not dashboard_ids:
        return None
    raw_summary = result_summary.get("summary")
    summary: dict[str, Any] = raw_summary if isinstance(raw_summary, dict) else {}
    try:
        return RunScopedTruthDashboardShell(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            dashboard_id=dashboard_ids[0],
            row_ids=_str_list(result_summary.get("row_ids")),
            claim_ids=_str_list(result_summary.get("claim_ids")),
            evidence_ids=_str_list(result_summary.get("evidence_ids")),
            sanad_ids=_str_list(result_summary.get("sanad_ids")),
            calc_ids=_str_list(result_summary.get("calc_ids")),
            defect_ids=_str_list(result_summary.get("defect_ids")),
            row_count=_int_from_summary(summary, "created_row_count", 0),
            by_verdict=_dict_from_summary(summary, "by_verdict"),
            by_grade=_dict_from_summary(summary, "by_grade"),
            status=str(result_summary.get("status") or "completed"),
        )
    except ValueError:
        return None


def _evidence_trust_court_shell_from_summary(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    result_summary: dict[str, Any],
) -> RunScopedEvidenceTrustCourtShell | None:
    court_ids = _str_list(result_summary.get("court_ids"))
    dashboard_ids = _str_list(result_summary.get("dashboard_ids"))
    if not court_ids or not dashboard_ids:
        return None
    raw_summary = result_summary.get("summary")
    summary: dict[str, Any] = raw_summary if isinstance(raw_summary, dict) else {}
    try:
        return RunScopedEvidenceTrustCourtShell(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            court_id=court_ids[0],
            dashboard_id=dashboard_ids[0],
            claim_ids=_str_list(result_summary.get("claim_ids")),
            evidence_ids=_str_list(result_summary.get("evidence_ids")),
            source_span_ids=_str_list(result_summary.get("source_span_ids")),
            sanad_ids=_str_list(result_summary.get("sanad_ids")),
            calc_ids=_str_list(result_summary.get("calc_ids")),
            defect_ids=_str_list(result_summary.get("defect_ids")),
            finding_ids=_str_list(result_summary.get("finding_ids")),
            assessed_claim_count=_int_from_summary(summary, "assessed_claim_count", 0),
            finding_count=_int_from_summary(summary, "finding_count", 0),
            by_disposition=_dict_from_summary(summary, "by_disposition"),
            by_grade=_dict_from_summary(summary, "by_grade"),
            by_dashboard_verdict=_dict_from_summary(summary, "by_dashboard_verdict"),
            status=str(result_summary.get("status") or "completed"),
        )
    except ValueError:
        return None


def _validated_evidence_package_shell_from_summary(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    result_summary: dict[str, Any],
) -> RunScopedValidatedEvidencePackageShell | None:
    package_ids = _str_list(result_summary.get("package_ids"))
    court_ids = _str_list(result_summary.get("court_ids"))
    dashboard_ids = _str_list(result_summary.get("dashboard_ids"))
    if not package_ids or not court_ids or not dashboard_ids:
        return None
    raw_summary = result_summary.get("summary")
    summary: dict[str, Any] = raw_summary if isinstance(raw_summary, dict) else {}
    try:
        return RunScopedValidatedEvidencePackageShell(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            package_id=package_ids[0],
            court_id=court_ids[0],
            dashboard_id=dashboard_ids[0],
            claim_ids_by_disposition=_dict_list_from_summary(
                result_summary,
                "claim_ids_by_disposition",
            ),
            evidence_ids=_str_list(result_summary.get("evidence_ids")),
            source_span_ids=_str_list(result_summary.get("source_span_ids")),
            sanad_ids=_str_list(result_summary.get("sanad_ids")),
            defect_ids=_str_list(result_summary.get("defect_ids")),
            calc_ids=_str_list(result_summary.get("calc_ids")),
            finding_ids=_str_list(result_summary.get("finding_ids")),
            finding_types=_str_list(result_summary.get("finding_types")),
            role_names=_str_list(result_summary.get("role_names")),
            reason_codes=_str_list(result_summary.get("reason_codes")),
            by_disposition=_dict_from_summary(summary, "by_disposition"),
            by_grade=_dict_from_summary(summary, "by_grade"),
            by_dashboard_verdict=_dict_from_summary(summary, "by_dashboard_verdict"),
            by_finding_type=_dict_from_summary(summary, "by_finding_type"),
            by_reason=_dict_from_summary(summary, "by_reason"),
            status=MethodologyValidatedEvidencePackageStatus(
                str(result_summary.get("status") or "completed")
            ),
        )
    except ValueError:
        return None


def _external_intelligence_conflict_check_plan_shell_from_summary(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    result_summary: dict[str, Any],
) -> RunScopedExternalIntelligenceConflictCheckPlanShell | None:
    plan_ids = _str_list(result_summary.get("plan_ids"))
    package_ids = _str_list(result_summary.get("package_ids"))
    if not plan_ids or not package_ids:
        return None
    raw_summary = result_summary.get("summary")
    summary: dict[str, Any] = raw_summary if isinstance(raw_summary, dict) else {}
    try:
        return RunScopedExternalIntelligenceConflictCheckPlanShell(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            plan_id=plan_ids[0],
            package_id=package_ids[0],
            provider_check_ids=_str_list(result_summary.get("provider_check_ids")),
            provider_ids=_str_list(result_summary.get("provider_ids")),
            check_statuses=_str_list(result_summary.get("check_statuses")),
            reason_codes=_str_list(result_summary.get("reason_codes")),
            by_status=_dict_from_summary(summary, "by_status"),
            by_provider=_dict_from_summary(summary, "by_provider"),
            by_rights_class=_dict_from_summary(summary, "by_rights_class"),
            by_reason=_dict_from_summary(summary, "by_reason"),
            status=MethodologyExternalIntelligenceConflictCheckPlanStatus(
                str(result_summary.get("status") or "completed")
            ),
        )
    except ValueError:
        return None


def _data_room_inventory_package_shell_from_summary(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    result_summary: dict[str, Any],
) -> RunScopedDataRoomInventoryPackageShell | None:
    inventory_package_ids = _str_list(result_summary.get("inventory_package_ids"))
    if not inventory_package_ids:
        return None
    raw_summary = result_summary.get("summary")
    summary: dict[str, Any] = raw_summary if isinstance(raw_summary, dict) else {}
    try:
        return RunScopedDataRoomInventoryPackageShell(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            inventory_package_id=inventory_package_ids[0],
            root_path_hash=str(result_summary.get("root_path_hash") or "unknown-root-path"),
            construction_status=DataRoomInventoryPackageConstructionStatus(
                str(result_summary.get("construction_status") or "completed")
            ),
            file_ids=_str_list(result_summary.get("file_ids")),
            supported_document_ids=_str_list(result_summary.get("supported_document_ids")),
            deferred_file_ids=_str_list(result_summary.get("deferred_file_ids")),
            blocked_file_ids=_str_list(result_summary.get("blocked_file_ids")),
            reason_codes=_str_list(result_summary.get("reason_codes")),
            by_extension=_dict_from_summary(summary, "by_extension"),
            by_file_status=_dict_from_summary(summary, "by_file_status"),
            by_reason=_dict_from_summary(summary, "by_reason"),
        )
    except ValueError:
        return None


def _layer2_readiness_package_shell_from_summary(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    result_summary: dict[str, Any],
) -> RunScopedLayer2ReadinessPackageShell | None:
    readiness_package_ids = _str_list(result_summary.get("readiness_package_ids"))
    vep_package_ids = _str_list(result_summary.get("source_vep_package_ids"))
    external_plan_ids = _str_list(result_summary.get("source_external_intelligence_plan_ids"))
    if not readiness_package_ids or not vep_package_ids or not external_plan_ids:
        return None
    raw_summary = result_summary.get("summary")
    summary: dict[str, Any] = raw_summary if isinstance(raw_summary, dict) else {}
    try:
        return RunScopedLayer2ReadinessPackageShell(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            readiness_package_id=readiness_package_ids[0],
            source_vep_package_id=vep_package_ids[0],
            source_external_intelligence_plan_id=external_plan_ids[0],
            claim_ids=_str_list(result_summary.get("claim_ids")),
            calc_ids=_str_list(result_summary.get("calc_ids")),
            provider_check_ids=_str_list(result_summary.get("provider_check_ids")),
            executed_provider_check_ids=_str_list(
                result_summary.get("executed_provider_check_ids")
            ),
            company_identity_ids=_str_list(result_summary.get("company_identity_ids")),
            enrichment_fact_ids=_str_list(result_summary.get("enrichment_fact_ids")),
            construction_status=MethodologyLayer2ReadinessPackageConstructionStatus(
                str(result_summary.get("construction_status") or "completed")
            ),
            readiness_status=MethodologyLayer2ReadinessStatus(
                str(result_summary.get("readiness_status") or "deferred")
            ),
            reason_codes=_str_list(result_summary.get("reason_codes")),
            blocker_ids=_str_list(result_summary.get("blocker_ids")),
            by_reason=_dict_from_summary(summary, "by_reason"),
            by_blocker_severity=_dict_from_summary(summary, "by_blocker_severity"),
        )
    except ValueError:
        return None


def _company_identity_package_shell_from_summary(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    result_summary: dict[str, Any],
) -> RunScopedCompanyIdentityPackageShell | None:
    identity_package_ids = _str_list(result_summary.get("identity_package_ids"))
    source_deal_ids = _str_list(result_summary.get("source_deal_ids"))
    if not identity_package_ids or not source_deal_ids:
        return None
    raw_summary = result_summary.get("summary")
    summary: dict[str, Any] = raw_summary if isinstance(raw_summary, dict) else {}
    try:
        return RunScopedCompanyIdentityPackageShell(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            identity_package_id=identity_package_ids[0],
            source_deal_id=source_deal_ids[0],
            company_identity_ids=_str_list(result_summary.get("company_identity_ids")),
            construction_status=MethodologyCompanyIdentityPackageConstructionStatus(
                str(result_summary.get("construction_status") or "completed")
            ),
            identity_status=MethodologyCompanyIdentityStatus(
                str(result_summary.get("identity_status") or "mapped")
            ),
            reason_codes=_str_list(result_summary.get("reason_codes")),
            blocker_ids=_str_list(result_summary.get("blocker_ids")),
            source_fields_present=_str_list(result_summary.get("source_fields_present")),
            identifier_types_present=_str_list(result_summary.get("identifier_types_present")),
            by_reason=_dict_from_summary(summary, "by_reason"),
            by_blocker_severity=_dict_from_summary(summary, "by_blocker_severity"),
        )
    except ValueError:
        return None


def _company_identity_ids(
    package: RunScopedCompanyIdentityPackageRecord | RunScopedCompanyIdentityPackageShell | None,
) -> list[str]:
    if package is None:
        return []
    return list(package.company_identity_ids)


def _empty_evidence_trust_court_summary(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
) -> dict[str, Any]:
    summary = RunScopedEvidenceTrustCourtSummary(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        total_claims=0,
        assessed_claim_count=0,
        finding_count=0,
        rejected_count=0,
        by_disposition={},
        by_reason={},
        by_grade={},
        by_dashboard_verdict={},
    )
    return MethodologyEvidenceTrustCourtRunResult(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        status=summary.aggregate_status(),
        court_shells=[],
        role_summaries=[],
        rejections=[],
        summary=summary,
    ).to_run_step_summary()


def _empty_validated_evidence_package_summary(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
) -> dict[str, Any]:
    summary = RunScopedValidatedEvidencePackageSummary(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        package_count=0,
        packaged_claim_count=0,
        finding_count=0,
        by_disposition={},
        by_grade={},
        by_dashboard_verdict={},
        by_finding_type={},
        by_reason={},
    )
    return MethodologyValidatedEvidencePackageRunResult(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        status=MethodologyValidatedEvidencePackageStatus.COMPLETED,
        package_shells=[],
        rejections=[],
        summary=summary,
    ).to_run_step_summary()


def _empty_external_intelligence_conflict_check_plan_summary(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
) -> dict[str, Any]:
    summary = RunScopedExternalIntelligenceConflictCheckPlanSummary(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        plan_count=0,
        check_count=0,
        by_status={ExternalIntelligencePlanCheckStatus.NO_OP.value: 1},
        by_provider={},
        by_rights_class={},
        by_reason={},
    )
    return MethodologyExternalIntelligenceConflictCheckPlanRunResult(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        status=MethodologyExternalIntelligenceConflictCheckPlanStatus.COMPLETED,
        plan_shells=[],
        rejections=[],
        summary=summary,
    ).to_run_step_summary()


def _empty_layer2_readiness_package_summary(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
) -> dict[str, Any]:
    summary = RunScopedLayer2ReadinessPackageSummary(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        package_count=0,
        claim_count=0,
        calc_count=0,
        provider_check_count=0,
        executed_provider_check_count=0,
        blocker_count=0,
        construction_status=MethodologyLayer2ReadinessPackageConstructionStatus.COMPLETED,
        readiness_status=MethodologyLayer2ReadinessStatus.DEFERRED,
        by_reason={},
        by_blocker_severity={},
    )
    return MethodologyLayer2ReadinessPackageRunResult(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        construction_status=MethodologyLayer2ReadinessPackageConstructionStatus.COMPLETED,
        readiness_status=MethodologyLayer2ReadinessStatus.DEFERRED,
        package_shells=[],
        rejections=[],
        summary=summary,
    ).to_run_step_summary()


def _is_empty_evidence_trust_court_summary(result_summary: dict[str, Any]) -> bool:
    raw_summary = result_summary.get("summary")
    summary: dict[str, Any] = raw_summary if isinstance(raw_summary, dict) else {}
    return (
        not _str_list(result_summary.get("court_ids"))
        and _int_from_summary(summary, "total_claims", 0) == 0
        and _int_from_summary(summary, "assessed_claim_count", 0) == 0
    )


def _is_empty_validated_evidence_package_summary(result_summary: dict[str, Any]) -> bool:
    raw_summary = result_summary.get("summary")
    summary: dict[str, Any] = raw_summary if isinstance(raw_summary, dict) else {}
    return (
        not _str_list(result_summary.get("package_ids"))
        and _int_from_summary(summary, "package_count", 0) == 0
        and _int_from_summary(summary, "packaged_claim_count", 0) == 0
    )


def _is_empty_external_intelligence_conflict_check_plan_summary(
    result_summary: dict[str, Any],
) -> bool:
    raw_summary = result_summary.get("summary")
    summary: dict[str, Any] = raw_summary if isinstance(raw_summary, dict) else {}
    return (
        not _str_list(result_summary.get("plan_ids"))
        and _int_from_summary(summary, "plan_count", 0) == 0
        and _int_from_summary(summary, "check_count", 0) == 0
    )


def _execution_result_shell_from_summary(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    result_summary: dict[str, Any],
) -> MethodologyExtractionExecutionResult:
    raw_summary = result_summary.get("summary")
    summary_data: dict[str, Any] = raw_summary if isinstance(raw_summary, dict) else {}
    raw_task_records = result_summary.get("task_results")
    task_records: list[Any] = raw_task_records if isinstance(raw_task_records, list) else []
    task_results = [
        _task_result_shell_from_summary(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            record=record,
        )
        for record in task_records
        if isinstance(record, dict)
    ]
    summary = MethodologyExtractionExecutionSummary(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        total_tasks=_int_from_summary(summary_data, "total_tasks", len(task_results)),
        executed_tasks=_int_from_summary(summary_data, "executed_tasks", 0),
        skipped_tasks=_int_from_summary(summary_data, "skipped_tasks", 0),
        failed_tasks=_int_from_summary(summary_data, "failed_tasks", 0),
        accepted_output_count=_int_from_summary(summary_data, "accepted_output_count", 0),
        rejected_output_count=_int_from_summary(summary_data, "rejected_output_count", 0),
        accepted_draft_count=0,
        rejected_draft_count=0,
        by_status=_dict_from_summary(summary_data, "by_status"),
        by_reason=_dict_from_summary(summary_data, "by_reason"),
    )
    return MethodologyExtractionExecutionResult(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        status=_execution_status_from_summary(result_summary, summary),
        task_results=task_results,
        accepted_outputs=[],
        summary=summary,
    )


def _task_result_shell_from_summary(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    record: dict[str, Any],
) -> MethodologyTaskExecutionResult:
    reason = _execution_reason(record.get("reason"))
    reason_codes = record.get("reason_codes")
    source_span_ids = record.get("source_span_ids")
    return MethodologyTaskExecutionResult(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        extraction_task_id=str(record.get("extraction_task_id") or "unknown_execution_task"),
        methodology_question_id=_optional_str(record.get("methodology_question_id")),
        coverage_record_id=_optional_str(record.get("coverage_record_id")),
        status=_task_status(record.get("status")),
        accepted_outputs=[],
        rejected_outputs=[],
        reason=reason,
        reason_codes=(
            [str(item) for item in reason_codes]
            if isinstance(reason_codes, list) and reason_codes
            else ([reason.value] if reason is not None else ["rehydrated"])
        ),
        source_span_ids=(
            [str(item) for item in source_span_ids] if isinstance(source_span_ids, list) else []
        ),
    )


def _execution_status_from_summary(
    result_summary: dict[str, Any],
    summary: MethodologyExtractionExecutionSummary,
) -> MethodologyExtractionExecutionStatus:
    raw_status = str(result_summary.get("status") or "").lower()
    try:
        return MethodologyExtractionExecutionStatus(raw_status)
    except ValueError:
        if summary.failed_tasks == summary.total_tasks and summary.total_tasks > 0:
            return MethodologyExtractionExecutionStatus.FAILED
        if summary.failed_tasks > 0:
            return MethodologyExtractionExecutionStatus.PARTIAL
        return MethodologyExtractionExecutionStatus.COMPLETED


def _task_status(value: object) -> MethodologyTaskExecutionStatus:
    try:
        return MethodologyTaskExecutionStatus(str(value).lower())
    except ValueError:
        return MethodologyTaskExecutionStatus.FAILED


def _execution_reason(value: object) -> MethodologyExtractionExecutionReason | None:
    if value is None:
        return None
    try:
        return MethodologyExtractionExecutionReason(str(value).lower())
    except ValueError:
        return MethodologyExtractionExecutionReason.MALFORMED_EXTRACTOR_OUTPUT


def _int_from_summary(summary_data: dict[str, Any], key: str, default: int) -> int:
    value = summary_data.get(key)
    return value if isinstance(value, int) and value >= 0 else default


def _dict_from_summary(summary_data: dict[str, Any], key: str) -> dict[str, int]:
    value = summary_data.get(key)
    if not isinstance(value, dict):
        return {}
    return {
        str(item_key): item_value
        for item_key, item_value in value.items()
        if isinstance(item_value, int) and item_value >= 0
    }


def _dict_list_from_summary(summary_data: dict[str, Any], key: str) -> dict[str, list[str]]:
    value = summary_data.get(key)
    if not isinstance(value, dict):
        return {}
    return {
        str(item_key): _str_list(item_value)
        for item_key, item_value in value.items()
        if str(item_key).strip()
    }


def _str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _int_value(value: object, default: int) -> int:
    return value if isinstance(value, int) and value >= 0 else default


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _require_strict_methodology_callback(
    callback: Callable[..., Any] | None,
    *,
    external_call: bool,
) -> None:
    """Reject every legacy/synthetic methodology seam in strict composition."""
    if callback is None:
        raise RunStepBlockedError(
            "STRICT_METHODOLOGY_COMPOSITION_REQUIRED",
            "Strict methodology execution requires a durable production callback",
        )
    if getattr(callback, "persistence_backend", None) != "postgresql":
        raise RunStepBlockedError(
            "STRICT_METHODOLOGY_COMPOSITION_REQUIRED",
            "Strict methodology execution requires PostgreSQL persistence",
        )
    if getattr(callback, "external_calls_enabled", None) is not external_call:
        requirement = (
            "requires the production extractor"
            if external_call
            else "requires external calls to be disabled"
        )
        raise RunStepBlockedError(
            "STRICT_METHODOLOGY_COMPOSITION_REQUIRED",
            f"Strict methodology execution {requirement}",
        )


def _require_active_run_step_id(ctx: RunContext) -> str:
    step_id = str(ctx.active_run_step_id or "").strip()
    if not step_id:
        raise RunStepBlockedError(
            "STRICT_METHODOLOGY_COMPOSITION_REQUIRED",
            "Strict methodology callback requires the durable RUNNING step identity",
        )
    return step_id


def _run_strict_methodology_rehydrator(
    ctx: RunContext,
    callback: Callable[..., Any] | None,
    *,
    external_call: bool,
) -> Any:
    """Load Task-6 state from durable rows, never from a result-summary shell."""
    _require_strict_methodology_callback(callback, external_call=external_call)
    rehydrate = getattr(callback, "rehydrate", None)
    if not callable(rehydrate):
        raise RunStepBlockedError(
            "STRICT_METHODOLOGY_COMPOSITION_REQUIRED",
            "Strict methodology callback has no durable restart loader",
        )
    try:
        return rehydrate(
            tenant_id=ctx.tenant_id,
            deal_id=ctx.deal_id,
            run_id=ctx.run_id,
        )
    except RunStepBlockedError:
        raise
    except Exception as exc:
        raise RunStepBlockedError(
            "METHODOLOGY_CANONICAL_MATERIALIZATION_INCOMPLETE",
            "Strict methodology durable restart loading failed closed",
        ) from exc


_STRICT_METHODOLOGY_FAILURE_STATUSES = frozenset(
    {"FAILED", "FAILURE", "PARTIAL", "DEFERRED", "BLOCKED", "SKIPPED", "NOT_ATTEMPTED"}
)
_STRICT_METHODOLOGY_FAILURE_COUNT_TOKENS = (
    "failed",
    "failure",
    "partial",
    "deferred",
    "blocked",
    "skipped",
    "rejected",
)


def _validate_strict_methodology_summary(summary: Mapping[str, Any]) -> None:
    """Reject completed-looking Task-6 summaries containing semantic failures."""
    if not isinstance(summary, Mapping) or not summary:
        raise RunStepBlockedError(
            "METHODOLOGY_TASK_COVERAGE_INCOMPLETE",
            "Strict methodology step returned no semantic evidence",
        )

    def visit(value: Any, path: str) -> None:
        if isinstance(value, Mapping):
            for raw_key, item in value.items():
                key = str(raw_key).strip().lower()
                item_path = f"{path}.{key}" if path else key
                if (
                    key.endswith("status")
                    and isinstance(item, str)
                    and item.strip().upper() in _STRICT_METHODOLOGY_FAILURE_STATUSES
                ):
                    raise RunStepBlockedError(
                        "METHODOLOGY_TASK_COVERAGE_INCOMPLETE",
                        "Strict methodology step contains a non-success status",
                    )
                if (
                    key.endswith("count")
                    and any(token in key for token in _STRICT_METHODOLOGY_FAILURE_COUNT_TOKENS)
                    and isinstance(item, int)
                    and not isinstance(item, bool)
                    and item > 0
                ):
                    raise RunStepBlockedError(
                        "METHODOLOGY_TASK_COVERAGE_INCOMPLETE",
                        "Strict methodology step contains a nonzero failure count",
                    )
                visit(item, item_path)
        elif isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                visit(item, f"{path}[{index}]")

    visit(summary, "")


def _tasks_request_calculations(tasks: list[PlannedExtractionTask]) -> bool:
    return any(task.expected_answer_schema.required_calculations for task in tasks)
