"""Run lifecycle service for retry/resume transitions."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

from idis.models.run_step import STEP_ORDER, RunStep, StepName, StepStatus


class RetryableRunsRepository(Protocol):
    """Repository behavior required for retry/resume lifecycle transitions."""

    def complete(self, run_id: str, *, status: str, finished_at: str | None) -> None:
        """Set terminal status for a run."""

    def try_requeue_failed(self, run_id: str) -> bool:
        """Attempt to atomically transition FAILED -> QUEUED."""

    def try_cancel_active(self, run_id: str) -> bool:
        """Attempt to atomically transition QUEUED/RUNNING -> CANCELLED."""


class RunLifecycleService:
    """Shared lifecycle boundary for retry/resume transitions."""

    def __init__(
        self,
        *,
        runs_repo: RetryableRunsRepository,
        run_steps_repo: Any,
    ) -> None:
        self._runs_repo = runs_repo
        self._run_steps_repo = run_steps_repo

    def request_retry(self, *, run_id: str) -> bool:
        """Requeue a failed run for future worker execution."""
        return self._runs_repo.try_requeue_failed(run_id)

    def request_resume(self, *, run_id: str) -> bool:
        """Alias of retry for failed run requeue."""
        return self.request_retry(run_id=run_id)

    def request_cancel(self, *, run_id: str, tenant_id: str) -> bool:
        """Cancel a queued/running run and persist safe lifecycle ledger evidence."""
        cancelled = self._runs_repo.try_cancel_active(run_id)
        if not cancelled:
            return False
        self._persist_lifecycle_evidence(
            run_id=run_id,
            tenant_id=tenant_id,
            reason_code="RUN_CANCELLED",
            message="Run cancelled by lifecycle request",
        )
        return True

    def persist_failed_block(
        self,
        *,
        run_id: str,
        tenant_id: str,
        reason_code: str,
        message: str,
        provenance_items: list[dict[str, Any]] | None = None,
    ) -> None:
        """Persist a safe failure ledger record without executing a run.

        Optional provenance_items (pre-serialized, safe StepProvenance dicts) are
        attached to the lifecycle step result_summary for operator diagnostics.
        """
        finished_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        self._runs_repo.complete(run_id, status="FAILED", finished_at=finished_at)
        self._persist_lifecycle_evidence(
            run_id=run_id,
            tenant_id=tenant_id,
            reason_code=reason_code,
            message=message,
            occurred_at=finished_at,
            provenance_items=provenance_items,
        )

    def _persist_lifecycle_evidence(
        self,
        *,
        run_id: str,
        tenant_id: str,
        reason_code: str,
        message: str,
        occurred_at: str | None = None,
        provenance_items: list[dict[str, Any]] | None = None,
    ) -> None:
        """Create or update the dedicated lifecycle ledger step."""
        timestamp = occurred_at or datetime.now(UTC).isoformat().replace("+00:00", "Z")
        lifecycle_event = {
            "reason_code": reason_code,
            "occurred_at": timestamp,
        }
        existing = self._run_steps_repo.get_step(run_id, StepName.RUN_LIFECYCLE)
        if existing is not None:
            events = list(existing.result_summary.get("lifecycle_events", []))
            events.append(lifecycle_event)
            updated_summary = {
                **existing.result_summary,
                "reason_code": reason_code,
                "lifecycle_events": events,
            }
            if provenance_items:
                updated_summary["provenance_items"] = provenance_items
            existing.result_summary = updated_summary
            existing.status = StepStatus.FAILED
            existing.finished_at = timestamp
            existing.error_code = reason_code
            existing.error_message = message
            self._run_steps_repo.update(existing)
            return

        result_summary: dict[str, Any] = {
            "reason_code": reason_code,
            "lifecycle_events": [lifecycle_event],
        }
        if provenance_items:
            result_summary["provenance_items"] = provenance_items

        step = RunStep(
            step_id=str(uuid.uuid4()),
            run_id=run_id,
            tenant_id=tenant_id,
            step_name=StepName.RUN_LIFECYCLE,
            step_order=STEP_ORDER[StepName.RUN_LIFECYCLE],
            status=StepStatus.FAILED,
            started_at=timestamp,
            finished_at=timestamp,
            result_summary=result_summary,
            error_code=reason_code,
            error_message=message,
        )
        self._run_steps_repo.create(step)
