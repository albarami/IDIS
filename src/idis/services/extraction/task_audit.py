"""Future audit contract for extraction task planning."""

from __future__ import annotations

from idis.models.extraction_task import ExtractionTaskStatus

EXTRACTION_TASK_AUDIT_EVENTS: set[str] = {
    "extraction.task.planning.started",
    "extraction.task.ready",
    "extraction.task.blocked",
    "extraction.task.evidence_missing",
    "extraction.task.planning.completed",
    "extraction.task.planning.failed",
}

EXTRACTION_TASK_AUDIT_EVENT_PAYLOAD_KEYS: set[str] = {
    "tenant_id",
    "deal_id",
    "run_id",
    "extraction_task_id",
    "methodology_id",
    "methodology_version_id",
    "methodology_question_id",
    "document_id",
    "classification_id",
    "source_span_ids",
    "status",
    "reason_codes",
    "blocker_reason",
}

_STATUS_EVENTS: dict[ExtractionTaskStatus, str] = {
    ExtractionTaskStatus.READY: "extraction.task.ready",
    ExtractionTaskStatus.BLOCKED: "extraction.task.blocked",
    ExtractionTaskStatus.EVIDENCE_MISSING: "extraction.task.evidence_missing",
    ExtractionTaskStatus.NOT_APPLICABLE: "extraction.task.blocked",
}


def event_for_extraction_task_status(status: ExtractionTaskStatus) -> str:
    """Return future audit event name for an extraction task status."""
    return _STATUS_EVENTS[status]
