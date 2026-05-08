"""Tests for future methodology extraction execution audit contract."""

from __future__ import annotations

from idis.models.extraction_execution import MethodologyTaskExecutionStatus
from idis.services.extraction.execution_audit import (
    EXTRACTION_EXECUTION_AUDIT_EVENT_PAYLOAD_KEYS,
    EXTRACTION_EXECUTION_AUDIT_EVENTS,
    event_for_task_execution_status,
)

REQUIRED_EVENTS = {
    "extraction.execution.started",
    "extraction.execution.task.started",
    "extraction.execution.task.completed",
    "extraction.execution.task.skipped",
    "extraction.execution.task.failed",
    "extraction.execution.completed",
    "extraction.execution.failed",
}

REQUIRED_PAYLOAD_KEYS = {
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


def test_required_execution_audit_events_are_defined() -> None:
    assert REQUIRED_EVENTS.issubset(EXTRACTION_EXECUTION_AUDIT_EVENTS)


def test_every_task_execution_status_maps_to_future_audit_event() -> None:
    for status in MethodologyTaskExecutionStatus:
        assert event_for_task_execution_status(status) in EXTRACTION_EXECUTION_AUDIT_EVENTS


def test_required_payload_keys_are_defined() -> None:
    assert REQUIRED_PAYLOAD_KEYS.issubset(EXTRACTION_EXECUTION_AUDIT_EVENT_PAYLOAD_KEYS)


def test_audit_contract_has_no_confidential_content() -> None:
    serialized = "\n".join(
        sorted(EXTRACTION_EXECUTION_AUDIT_EVENTS | EXTRACTION_EXECUTION_AUDIT_EVENT_PAYLOAD_KEYS)
    )

    assert "real_example" not in serialized
    assert "financial Due Diligence.xlsx" not in serialized
    assert ".local_reports" not in serialized
    assert ".quarantine_real_example_removed" not in serialized
