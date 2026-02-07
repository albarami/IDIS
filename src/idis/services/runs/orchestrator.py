"""RunOrchestrator — pure orchestration logic for pipeline step execution.

Executes pipeline steps in canonical order, records each step in the
RunStep ledger, emits audit events at every transition, and enforces
fail-closed semantics on audit failures.

No FastAPI globals. All dependencies injected via constructor or execute().
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from idis.audit.sink import AuditSink, AuditSinkError
from idis.models.deterministic_calculation import CalcType
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


@dataclass
class OrchestratorResult:
    """Aggregate result of a pipeline orchestration run.

    Attributes:
        status: Final run status (COMPLETED, FAILED, BLOCKED, PARTIAL).
        steps: All RunStep records in canonical order.
        block_reason: Stable reason code when status is BLOCKED.
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
        documents: Ingested document dicts gathered by the route handler.
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
    calc_fn: Callable[..., dict[str, Any]] | None = None
    calc_types: list[CalcType] | None = None
    debate_fn: Callable[..., dict[str, Any]] | None = None


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

        For SNAPSHOT: INGEST_CHECK -> EXTRACT -> GRADE -> CALC.
        For FULL: INGEST_CHECK -> EXTRACT -> GRADE -> CALC -> DEBATE.

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
                    status="BLOCKED",
                    steps=all_steps,
                    block_reason=BLOCK_REASON_DEBATE_NOT_IMPLEMENTED,
                )

            existing = self._steps_repo.get_step(ctx.run_id, step_name)
            if existing is not None and existing.status == StepStatus.COMPLETED:
                accumulated.update(existing.result_summary)
                continue

            step = self._start_step(ctx, step_name, existing)

            try:
                result = self._dispatch_step(step_name, ctx, accumulated)
            except AuditSinkError:
                raise
            except Exception as exc:
                self._fail_step(step, exc)
                all_steps = self._steps_repo.get_by_run_id(ctx.run_id)
                return OrchestratorResult(
                    status="FAILED",
                    steps=all_steps,
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
        if step_name == StepName.EXTRACT:
            return self._execute_extract(ctx)
        if step_name == StepName.GRADE:
            return self._execute_grade(ctx, accumulated)
        if step_name == StepName.CALC:
            return self._execute_calc(ctx, accumulated)
        if step_name == StepName.DEBATE:
            return self._execute_debate(ctx, accumulated)
        raise ValueError(f"No handler for step: {step_name.value}")

    def _execute_ingest_check(self, ctx: RunContext) -> dict[str, Any]:
        """Verify at least one ingested document exists for the deal.

        Args:
            ctx: Run context with documents list.

        Returns:
            Dict with document_count.

        Raises:
            ValueError: If no documents found.
        """
        if not ctx.documents:
            raise ValueError("No ingested documents found for this deal")
        return {"document_count": len(ctx.documents)}

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

    def _complete_step(self, step: RunStep, result: dict[str, Any]) -> None:
        """Mark a step COMPLETED and persist its result summary.

        Args:
            step: The running step to complete.
            result: Step output to store in result_summary.
        """
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        step.status = StepStatus.COMPLETED
        step.finished_at = now
        step.result_summary = result
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
        step.error_code = type(exc).__name__.upper()
        step.error_message = str(exc)[:500]
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
            COMPLETED if all passed, FAILED if any failed, PARTIAL if mixed.
        """
        if not steps:
            return "FAILED"

        has_failed = any(s.status == StepStatus.FAILED for s in steps)
        has_completed = any(s.status == StepStatus.COMPLETED for s in steps)

        if has_failed and not has_completed:
            return "FAILED"
        if has_failed and has_completed:
            return "PARTIAL"
        return "COMPLETED"
