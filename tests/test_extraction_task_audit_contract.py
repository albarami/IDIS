"""Tests for future extraction task audit contract."""

from __future__ import annotations

from idis.models.extraction_task import ExtractionTaskStatus
from idis.services.extraction.task_audit import (
    EXTRACTION_TASK_AUDIT_EVENT_PAYLOAD_KEYS,
    EXTRACTION_TASK_AUDIT_EVENTS,
    event_for_extraction_task_status,
)

REQUIRED_EVENTS = {
    "extraction.task.planning.started",
    "extraction.task.ready",
    "extraction.task.blocked",
    "extraction.task.evidence_missing",
    "extraction.task.planning.completed",
    "extraction.task.planning.failed",
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
    "classification_id",
    "source_span_ids",
    "status",
    "reason_codes",
    "blocker_reason",
}


def test_required_extraction_task_audit_events_are_defined() -> None:
    assert REQUIRED_EVENTS.issubset(EXTRACTION_TASK_AUDIT_EVENTS)


def test_every_task_status_maps_to_future_audit_event() -> None:
    for status in ExtractionTaskStatus:
        assert event_for_extraction_task_status(status) in EXTRACTION_TASK_AUDIT_EVENTS


def test_required_payload_keys_are_defined() -> None:
    assert REQUIRED_PAYLOAD_KEYS.issubset(EXTRACTION_TASK_AUDIT_EVENT_PAYLOAD_KEYS)


def test_audit_contract_has_no_confidential_content() -> None:
    serialized = "\n".join(
        sorted(EXTRACTION_TASK_AUDIT_EVENTS | EXTRACTION_TASK_AUDIT_EVENT_PAYLOAD_KEYS)
    )

    assert "real_example" not in serialized
    assert "financial Due Diligence.xlsx" not in serialized
    assert ".local_reports" not in serialized
    assert ".quarantine_real_example_removed" not in serialized
