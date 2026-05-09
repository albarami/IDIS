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
from idis.models.claim_materialization import (
    MaterializedClaimSourceRef,
    MethodologyOutputClaimMaterializationRunResult,
    RunScopedMaterializedClaim,
    RunScopedMaterializedClaimShell,
)
from idis.models.deterministic_calculation import CalcType
from idis.models.document_preflight import DocumentPreflightResult
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
    methodology_materialized_claims: list[
        RunScopedMaterializedClaim | RunScopedMaterializedClaimShell
    ] = field(default_factory=list)
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


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
