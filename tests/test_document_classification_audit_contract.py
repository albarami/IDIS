"""Tests for future document classification audit contract."""

from __future__ import annotations

from idis.models.document_classification import DocumentTriageStatus
from idis.services.documents.audit import (
    DOCUMENT_CLASSIFICATION_AUDIT_EVENT_PAYLOAD_KEYS,
    DOCUMENT_CLASSIFICATION_AUDIT_EVENTS,
    event_for_triage_status,
)

REQUIRED_EVENTS = {
    "document.classification.started",
    "document.classification.completed",
    "document.classification.failed",
    "document.triage.completed",
    "document.triage.blocked",
    "document.triage.conversion_required",
    "document.triage.unsupported_source",
}

REQUIRED_PAYLOAD_KEYS = {
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


def test_required_document_classification_audit_events_are_defined() -> None:
    assert REQUIRED_EVENTS.issubset(DOCUMENT_CLASSIFICATION_AUDIT_EVENTS)


def test_every_triage_status_maps_to_future_audit_event() -> None:
    for status in DocumentTriageStatus:
        assert event_for_triage_status(status) in DOCUMENT_CLASSIFICATION_AUDIT_EVENTS


def test_required_payload_keys_are_defined() -> None:
    assert REQUIRED_PAYLOAD_KEYS.issubset(DOCUMENT_CLASSIFICATION_AUDIT_EVENT_PAYLOAD_KEYS)


def test_audit_contract_has_no_confidential_content() -> None:
    serialized = "\n".join(
        sorted(
            DOCUMENT_CLASSIFICATION_AUDIT_EVENTS | DOCUMENT_CLASSIFICATION_AUDIT_EVENT_PAYLOAD_KEYS
        )
    )

    assert "real_example" not in serialized
    assert "financial Due Diligence.xlsx" not in serialized
    assert ".quarantine_real_example_removed" not in serialized
