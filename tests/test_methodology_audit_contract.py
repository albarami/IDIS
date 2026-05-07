"""Tests for methodology audit event contract definitions."""

from __future__ import annotations

from idis.methodology.audit import (
    METHODOLOGY_AUDIT_EVENT_PAYLOAD_KEYS,
    METHODOLOGY_AUDIT_EVENTS,
    event_for_coverage_status,
)
from idis.models.methodology_coverage import MethodologyCoverageStatus

REQUIRED_EVENT_NAMES = {
    "methodology.registry.loaded",
    "methodology.registry.validated",
    "methodology.coverage.initialized",
    "methodology.question.started",
    "methodology.question.evidence_missing",
    "methodology.question.unsupported_source",
    "methodology.question.extracted",
    "methodology.question.partially_answered",
    "methodology.question.answered",
    "methodology.question.contradicted",
    "methodology.question.not_applicable",
    "methodology.question.blocked",
}

REQUIRED_PAYLOAD_KEYS = {
    "tenant_id",
    "deal_id",
    "run_id",
    "methodology_id",
    "methodology_version_id",
    "methodology_question_id",
    "status",
}


def test_required_methodology_audit_event_names_are_defined() -> None:
    """Phase 2.2 defines future audit event names without wiring emission yet."""
    assert REQUIRED_EVENT_NAMES.issubset(set(METHODOLOGY_AUDIT_EVENTS))


def test_every_coverage_status_maps_to_future_audit_event() -> None:
    """Each coverage status has a deterministic future audit event name."""
    for status in MethodologyCoverageStatus:
        event_name = event_for_coverage_status(status)
        assert event_name in METHODOLOGY_AUDIT_EVENTS
        assert event_name.startswith("methodology.question.")


def test_required_audit_payload_keys_are_defined() -> None:
    """Audit payloads must include stable methodology and provenance identifiers."""
    assert REQUIRED_PAYLOAD_KEYS.issubset(METHODOLOGY_AUDIT_EVENT_PAYLOAD_KEYS)
    assert "claim_ids" in METHODOLOGY_AUDIT_EVENT_PAYLOAD_KEYS
    assert "evidence_ids" in METHODOLOGY_AUDIT_EVENT_PAYLOAD_KEYS
    assert "calc_ids" in METHODOLOGY_AUDIT_EVENT_PAYLOAD_KEYS
    assert "reason_code" in METHODOLOGY_AUDIT_EVENT_PAYLOAD_KEYS


def test_audit_contract_does_not_reference_confidential_sources() -> None:
    """Audit contract must not include file paths or confidential workbook names."""
    serialized = "\n".join(
        sorted(METHODOLOGY_AUDIT_EVENTS | METHODOLOGY_AUDIT_EVENT_PAYLOAD_KEYS)
    )

    assert "financial Due Diligence.xlsx" not in serialized
    assert "real_example" not in serialized
    assert ".local_reports" not in serialized
