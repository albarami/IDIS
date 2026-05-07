"""Future audit contract for document classification and parser triage."""

from __future__ import annotations

from idis.models.document_classification import DocumentTriageStatus

DOCUMENT_CLASSIFICATION_AUDIT_EVENTS: set[str] = {
    "document.classification.started",
    "document.classification.completed",
    "document.classification.failed",
    "document.triage.completed",
    "document.triage.blocked",
    "document.triage.conversion_required",
    "document.triage.unsupported_source",
}

DOCUMENT_CLASSIFICATION_AUDIT_EVENT_PAYLOAD_KEYS: set[str] = {
    "tenant_id",
    "deal_id",
    "document_id",
    "classification_id",
    "fdd_category",
    "cdd_category",
    "parser_status",
    "triage_status",
    "reason_codes",
    "source",
    "request_id",
}

_TRIAGE_EVENTS: dict[DocumentTriageStatus, str] = {
    DocumentTriageStatus.READY: "document.triage.completed",
    DocumentTriageStatus.PARTIAL: "document.triage.completed",
    DocumentTriageStatus.BLOCKED: "document.triage.blocked",
    DocumentTriageStatus.CONVERSION_REQUIRED: "document.triage.conversion_required",
    DocumentTriageStatus.UNSUPPORTED_SOURCE: "document.triage.unsupported_source",
    DocumentTriageStatus.OCR_REQUIRED: "document.triage.conversion_required",
    DocumentTriageStatus.TOO_LARGE: "document.triage.blocked",
    DocumentTriageStatus.UNKNOWN: "document.triage.unsupported_source",
}


def event_for_triage_status(status: DocumentTriageStatus) -> str:
    """Return future audit event name for a triage status."""
    return _TRIAGE_EVENTS[status]
