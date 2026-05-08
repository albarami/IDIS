"""Future audit contract for methodology extraction execution."""

from __future__ import annotations

from idis.models.extraction_execution import MethodologyTaskExecutionStatus

EXTRACTION_EXECUTION_AUDIT_EVENTS: set[str] = {
    "extraction.execution.started",
    "extraction.execution.task.started",
    "extraction.execution.task.completed",
    "extraction.execution.task.skipped",
    "extraction.execution.task.failed",
    "extraction.execution.completed",
    "extraction.execution.failed",
}

EXTRACTION_EXECUTION_AUDIT_EVENT_PAYLOAD_KEYS: set[str] = {
    "tenant_id",
    "deal_id",
    "run_id",
    "extraction_task_id",
    "methodology_id",
    "methodology_version_id",
    "methodology_question_id",
    "document_id",
    "source_span_ids",
    "status",
    "reason",
    "reason_codes",
    "accepted_draft_count",
    "rejected_draft_count",
}


def event_for_task_execution_status(status: MethodologyTaskExecutionStatus) -> str:
    """Return the future audit event name for a task execution status."""
    if status in {
        MethodologyTaskExecutionStatus.COMPLETED,
        MethodologyTaskExecutionStatus.PARTIAL,
    }:
        return "extraction.execution.task.completed"
    if status == MethodologyTaskExecutionStatus.SKIPPED:
        return "extraction.execution.task.skipped"
    return "extraction.execution.task.failed"
