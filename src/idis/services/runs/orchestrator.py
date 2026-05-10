"""RunOrchestrator — pure orchestration logic for pipeline step execution.

Executes pipeline steps in canonical order, records each step in the
RunStep ledger, emits audit events at every transition, and enforces
fail-closed semantics on audit failures.

SNAPSHOT: INGEST_CHECK -> DOCUMENT_PREFLIGHT -> METHODOLOGY_COVERAGE_INIT -> EXTRACT
          -> GRADE -> CALC.
FULL: INGEST_CHECK -> DOCUMENT_PREFLIGHT -> METHODOLOGY_COVERAGE_INIT -> EXTRACT
      -> GRADE -> CALC -> ENRICHMENT -> DEBATE -> ANALYSIS -> SCORING -> DELIVERABLES.

No FastAPI globals. All dependencies injected via constructor or execute().
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

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
    MethodologyOutputClaimMaterializationRunResult,
    RunScopedMaterializedClaim,
    RunScopedMaterializedClaimShell,
)
from idis.models.defect import CureProtocol, DefectSeverity, DefectStatus, DefectType
from idis.models.deterministic_calculation import CalcType
from idis.models.document_preflight import DocumentPreflightResult
from idis.models.evidence_item_materialization import (
    MethodologyEvidenceItemMaterializationRunResult,
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
    MethodologyExtractionExecutionRunResult,
    MethodologyExtractionExecutionStatus,
    MethodologyExtractionExecutionSummary,
    MethodologyTaskExecutionResult,
    MethodologyTaskExecutionStatus,
)
from idis.models.extraction_task import ExtractionTask, ExtractionTaskPlanningRunResult
from idis.models.layer2_readiness_package_materialization import (
    MethodologyLayer2ReadinessPackageConstructionStatus,
    MethodologyLayer2ReadinessPackageRunResult,
    MethodologyLayer2ReadinessStatus,
    RunScopedLayer2ReadinessPackageRecord,
    RunScopedLayer2ReadinessPackageShell,
    RunScopedLayer2ReadinessPackageSummary,
)
from idis.models.methodology_coverage import (
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
    MethodologySanadMaterializationRunResult,
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
from idis.persistence.repositories.run_steps import RunStepsRepo

logger = logging.getLogger(__name__)

BLOCK_REASON_DEBATE_NOT_IMPLEMENTED = "DEBATE_NOT_IMPLEMENTED"
BLOCK_REASON_NO_INGESTED_DOCUMENTS = "NO_INGESTED_DOCUMENTS"
BLOCK_REASON_NO_USABLE_DOCUMENTS = "NO_USABLE_DOCUMENTS"
BLOCK_REASON_NO_ELIGIBLE_EXTRACTION_TASKS = "NO_ELIGIBLE_EXTRACTION_TASKS"
BLOCK_REASON_NO_PLANNED_EXTRACTION_TASKS = "NO_PLANNED_EXTRACTION_TASKS"


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
    preflight_corpus: list[dict[str, Any]] = field(default_factory=list)
    document_preflight_fn: (
        Callable[..., tuple[DocumentPreflightResult, list[dict[str, Any]]]] | None
    ) = None
    methodology_registry: MethodologyRegistry | None = None
    methodology_registry_loader_fn: Callable[[], MethodologyRegistry] | None = None
    methodology_coverage_init_fn: (
        Callable[
            ..., tuple[MethodologyCoverageInitializationResult, list[MethodologyCoverageRecord]]
        ]
        | None
    ) = None
    methodology_coverage_records: list[MethodologyCoverageRecord] = field(default_factory=list)
    methodology_extraction_task_planning_fn: (
        Callable[..., tuple[ExtractionTaskPlanningRunResult, list[ExtractionTask]]] | None
    ) = None
    methodology_extraction_tasks: list[ExtractionTask] = field(default_factory=list)
    methodology_extraction_task_execution_fn: (
        Callable[
            ...,
            tuple[MethodologyExtractionExecutionRunResult, MethodologyExtractionExecutionResult],
        ]
        | None
    ) = None
    methodology_extraction_execution_result: MethodologyExtractionExecutionResult | None = None
    methodology_claim_materialization_fn: (
        Callable[
            ...,
            tuple[
                MethodologyOutputClaimMaterializationRunResult,
                list[RunScopedMaterializedClaim],
            ],
        ]
        | None
    ) = None
    methodology_materialized_claims: (
        list[RunScopedMaterializedClaim | RunScopedMaterializedClaimShell] | None
    ) = None
    methodology_evidence_item_materialization_fn: (
        Callable[
            ...,
            tuple[
                MethodologyEvidenceItemMaterializationRunResult,
                list[RunScopedEvidenceItemRecord],
            ],
        ]
        | None
    ) = None
    methodology_evidence_items: (
        list[RunScopedEvidenceItemRecord | RunScopedEvidenceItemShell] | None
    ) = None
    methodology_evidence_source_provenance: list[RunScopedEvidenceProvenanceRef] = field(
        default_factory=list
    )
    methodology_sanad_creation_linking_grading_fn: (
        Callable[
            ...,
            tuple[
                MethodologySanadMaterializationRunResult,
                list[RunScopedSanadRecord],
                list[RunScopedSanadLinkRecord],
                list[RunScopedSanadGradeRecord],
                list[RunScopedSanadDefectRecord],
            ],
        ]
        | None
    ) = None
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
    enrich_fn: Callable[..., dict[str, Any]] | None = None
    debate_fn: Callable[..., dict[str, Any]] | None = None
    analysis_fn: Callable[..., dict[str, Any]] | None = None
    scoring_fn: Callable[..., dict[str, Any]] | None = None
    deliverables_fn: Callable[..., dict[str, Any]] | None = None


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
    ) -> None:
        """Initialize the orchestrator.

        Args:
            audit_sink: Audit sink for event emission.
            run_steps_repo: Tenant-scoped step repository.
        """
        self._audit = audit_sink
        self._steps_repo = run_steps_repo

    def execute(self, ctx: RunContext) -> OrchestratorResult:
        """Execute all pipeline steps for the given run context.

        For SNAPSHOT: INGEST_CHECK -> DOCUMENT_PREFLIGHT -> METHODOLOGY_COVERAGE_INIT
                      -> EXTRACT -> GRADE -> CALC.
        For FULL: INGEST_CHECK -> DOCUMENT_PREFLIGHT -> METHODOLOGY_COVERAGE_INIT
                  -> EXTRACT -> GRADE -> CALC -> ENRICHMENT -> DEBATE
                  -> ANALYSIS -> SCORING -> DELIVERABLES.

        Skips steps that are already COMPLETED (idempotent resume).
        Fails closed on audit emission errors.

        Args:
            ctx: RunContext with all execution inputs.

        Returns:
            OrchestratorResult with final status and step records.

        Raises:
            AuditSinkError: Propagated when audit emission fails (fail-closed).
        """
        step_sequence = FULL_STEPS if ctx.mode == "FULL" else SNAPSHOT_STEPS
        accumulated: dict[str, Any] = {}

        for step_name in step_sequence:
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
                accumulated.update(existing.result_summary)
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
                if step_name == StepName.METHODOLOGY_LAYER2_READINESS_PACKAGE:
                    self._rehydrate_methodology_layer2_readiness_package(
                        ctx,
                        existing.result_summary,
                    )
                continue

            step = self._start_step(ctx, step_name, existing)

            try:
                result = self._dispatch_step(step_name, ctx, accumulated)
            except AuditSinkError:
                raise
            except Exception as exc:
                self._fail_step(step, exc)
                all_steps = self._steps_repo.get_by_run_id(ctx.run_id)
                block_reason = step.error_code if isinstance(exc, RunStepBlockedError) else None
                return OrchestratorResult(
                    status="FAILED",
                    steps=all_steps,
                    block_reason=block_reason,
                    error_code=step.error_code,
                    error_message=step.error_message,
                )

            self._complete_step(step, result)
            accumulated.update(result)

        all_steps = self._steps_repo.get_by_run_id(ctx.run_id)
        final_status = self._compute_final_status(all_steps)
        return OrchestratorResult(status=final_status, steps=all_steps)

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
        if step_name == StepName.METHODOLOGY_LAYER2_READINESS_PACKAGE:
            return self._execute_methodology_layer2_readiness_package(ctx, accumulated)
        if step_name == StepName.EXTRACT:
            return self._execute_extract(ctx)
        if step_name == StepName.GRADE:
            return self._execute_grade(ctx, accumulated)
        if step_name == StepName.CALC:
            return self._execute_calc(ctx, accumulated)
        if step_name == StepName.ENRICHMENT:
            return self._execute_enrichment(ctx, accumulated)
        if step_name == StepName.DEBATE:
            return self._execute_debate(ctx, accumulated)
        if step_name == StepName.ANALYSIS:
            return self._execute_analysis(ctx, accumulated)
        if step_name == StepName.SCORING:
            return self._execute_scoring(ctx, accumulated)
        if step_name == StepName.DELIVERABLES:
            return self._execute_deliverables(ctx, accumulated)
        raise ValueError(f"No handler for step: {step_name.value}")

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

            preflight_result, eligible_documents = InMemoryRunDocumentPreflightService().run(
                tenant_id=ctx.tenant_id,
                deal_id=ctx.deal_id,
                run_id=ctx.run_id,
                corpus=corpus,
            )

        summary: dict[str, Any] = preflight_result.to_run_step_summary()
        if not eligible_documents:
            raise RunStepBlockedError(
                BLOCK_REASON_NO_USABLE_DOCUMENTS,
                "No usable documents remain after document preflight",
                result_summary=summary,
            )
        ctx.documents = eligible_documents
        return summary

    def _execute_methodology_coverage_init(self, ctx: RunContext) -> dict[str, Any]:
        """Initialize NOT_STARTED methodology coverage records for this run."""
        if ctx.methodology_coverage_init_fn is not None:
            init_result, records = ctx.methodology_coverage_init_fn(
                tenant_id=ctx.tenant_id,
                deal_id=ctx.deal_id,
                run_id=ctx.run_id,
                registry=ctx.methodology_registry,
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
        """Reattach in-memory coverage records when a completed init step is skipped."""
        if ctx.methodology_coverage_records:
            return

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
        if ctx.methodology_extraction_task_planning_fn is not None:
            planning_result, tasks = ctx.methodology_extraction_task_planning_fn(
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
        return planning_result.to_run_step_summary()

    def _rehydrate_methodology_extraction_tasks(
        self,
        ctx: RunContext,
        accumulated: dict[str, Any],
    ) -> None:
        """Reattach in-memory planned tasks when a completed planning step is skipped."""
        if ctx.methodology_extraction_tasks:
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
    ) -> tuple[ExtractionTaskPlanningRunResult, list[ExtractionTask]]:
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

        if ctx.methodology_extraction_task_execution_fn is not None:
            run_result, execution_result = ctx.methodology_extraction_task_execution_fn(
                tenant_id=ctx.tenant_id,
                deal_id=ctx.deal_id,
                run_id=ctx.run_id,
                tasks=ctx.methodology_extraction_tasks,
                documents=ctx.documents,
            )
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
        return run_result.to_run_step_summary()

    def _execute_methodology_claim_materialization(self, ctx: RunContext) -> dict[str, Any]:
        """Materialize accepted neutral execution outputs into in-memory claims."""
        if ctx.methodology_extraction_execution_result is None:
            raise RunStepBlockedError(
                "METHODOLOGY_EXECUTION_RESULT_MISSING",
                "Methodology claim materialization requires execution results",
            )

        if ctx.methodology_claim_materialization_fn is not None:
            run_result, materialized_claims = ctx.methodology_claim_materialization_fn(
                tenant_id=ctx.tenant_id,
                deal_id=ctx.deal_id,
                run_id=ctx.run_id,
                execution_result=ctx.methodology_extraction_execution_result,
            )
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
        return run_result.to_run_step_summary()

    def _execute_methodology_evidence_item_materialization(self, ctx: RunContext) -> dict[str, Any]:
        """Materialize EvidenceItems from Slice 6 run-scoped claims."""
        if ctx.methodology_materialized_claims is None:
            raise RunStepBlockedError(
                "METHODOLOGY_MATERIALIZED_CLAIMS_MISSING",
                "Evidence item materialization requires claim materialization context",
            )

        if ctx.methodology_evidence_item_materialization_fn is not None:
            run_result, evidence_records = ctx.methodology_evidence_item_materialization_fn(
                tenant_id=ctx.tenant_id,
                deal_id=ctx.deal_id,
                run_id=ctx.run_id,
                materialized_claims=ctx.methodology_materialized_claims,
            )
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
        return run_result.to_run_step_summary()

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

        if ctx.methodology_sanad_creation_linking_grading_fn is not None:
            run_result, sanad_records, links, grades, defects = (
                ctx.methodology_sanad_creation_linking_grading_fn(
                    tenant_id=ctx.tenant_id,
                    deal_id=ctx.deal_id,
                    run_id=ctx.run_id,
                    materialized_claims=ctx.methodology_materialized_claims,
                    evidence_items=evidence_items,
                    source_provenance=ctx.methodology_evidence_source_provenance,
                )
            )
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
        return run_result.to_run_step_summary()

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

            run_result, courts = InMemoryRunMethodologyEvidenceTrustCourtService().run(
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
        return summary

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
        """Attach a safe execution result shell when completed execution is skipped."""
        if ctx.methodology_extraction_execution_result is not None:
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
        """Attach safe materialized claim shells when completed materialization is skipped."""
        if ctx.methodology_materialized_claims:
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
        """Attach safe EvidenceItem shells when completed Slice 7 step is skipped."""
        if ctx.methodology_evidence_items:
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
        """Attach safe Sanad shells when completed Slice 8 step is skipped."""
        if ctx.methodology_sanads:
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

    def _execute_extract(self, ctx: RunContext) -> dict[str, Any]:
        """Run extraction pipeline via injected callable.

        Args:
            ctx: Run context with extract_fn.

        Returns:
            Extraction result dict including created_claim_ids.
        """
        return ctx.extract_fn(
            run_id=ctx.run_id,
            tenant_id=ctx.tenant_id,
            deal_id=ctx.deal_id,
            documents=ctx.documents,
        )

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

        Returns:
            Debate result dict including stop_reason and muhasabah_passed.

        Raises:
            ValueError: If ctx.debate_fn is None (fail-closed).
        """
        if ctx.debate_fn is None:
            raise ValueError("debate_fn not provided")

        created_claim_ids = accumulated.get("created_claim_ids", [])
        calc_ids = accumulated.get("calc_ids", [])
        return ctx.debate_fn(
            run_id=ctx.run_id,
            tenant_id=ctx.tenant_id,
            deal_id=ctx.deal_id,
            created_claim_ids=created_claim_ids,
            calc_ids=calc_ids,
        )

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
        return ctx.analysis_fn(
            run_id=ctx.run_id,
            tenant_id=ctx.tenant_id,
            deal_id=ctx.deal_id,
            created_claim_ids=created_claim_ids,
            calc_ids=calc_ids,
            enrichment_refs=enrichment_refs,
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
        )

    def _start_step(
        self,
        ctx: RunContext,
        step_name: StepName,
        existing: RunStep | None,
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

        if existing is not None:
            existing.status = StepStatus.RUNNING
            existing.started_at = now
            existing.finished_at = None
            existing.error_code = None
            existing.error_message = None
            existing.retry_count += 1
            step = self._steps_repo.update(existing)
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
            step = self._steps_repo.create(step)

        self._emit_audit_event(
            event_type=f"run.step.{step_name.value.lower()}.started",
            tenant_id=ctx.tenant_id,
            details={
                "run_id": ctx.run_id,
                "step_id": step.step_id,
                "step_name": step_name.value,
                "retry_count": step.retry_count,
            },
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

    def _complete_step(self, step: RunStep, result: dict[str, Any]) -> None:
        """Mark a step COMPLETED and persist its result summary.

        Args:
            step: The running step to complete.
            result: Step output to store in result_summary.
        """
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        step.status = StepStatus.COMPLETED
        step.finished_at = now
        step.result_summary = self._sanitize_for_json(result)
        self._steps_repo.update(step)

        self._emit_audit_event(
            event_type=f"run.step.{step.step_name.value.lower()}.completed",
            tenant_id=step.tenant_id,
            details={
                "run_id": step.run_id,
                "step_id": step.step_id,
                "step_name": step.step_name.value,
                "result_keys": list(result.keys()),
            },
        )

    def _fail_step(self, step: RunStep, exc: Exception) -> None:
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
        self._steps_repo.update(step)

        self._emit_audit_event(
            event_type=f"run.step.{step.step_name.value.lower()}.failed",
            tenant_id=step.tenant_id,
            details={
                "run_id": step.run_id,
                "step_id": step.step_id,
                "step_name": step.step_name.value,
                "error_code": step.error_code,
                "error_message": step.error_message,
            },
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
    ) -> None:
        """Emit an audit event, fail-closed on any error.

        Args:
            event_type: Audit event type string.
            tenant_id: Tenant UUID for the event.
            details: Event payload.

        Raises:
            AuditSinkError: If audit emission fails (fail-closed).
        """
        event: dict[str, Any] = {
            "event_id": str(uuid.uuid4()),
            "event_type": event_type,
            "tenant_id": tenant_id,
            "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "details": details,
        }
        self._audit.emit(event)

    @staticmethod
    def _compute_final_status(steps: list[RunStep]) -> str:
        """Derive the final run status from step statuses.

        Args:
            steps: All step records for the run.

        Returns:
            SUCCEEDED if all steps completed, FAILED otherwise (fail-closed).
        """
        if not steps:
            return "FAILED"

        has_failed = any(s.status == StepStatus.FAILED for s in steps)

        if has_failed:
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


def _tasks_request_calculations(tasks: list[ExtractionTask]) -> bool:
    return any(task.expected_answer_schema.required_calculations for task in tasks)
