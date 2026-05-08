"""Tests for future Sanad/coverage boundary audit contract."""

from __future__ import annotations

from idis.services.methodology.sanad_coverage_boundary_audit import (
    SANAD_COVERAGE_BOUNDARY_AUDIT_EVENT_PAYLOAD_KEYS,
    SANAD_COVERAGE_BOUNDARY_AUDIT_EVENTS,
)

REQUIRED_EVENTS = {
    "sanad.coverage_boundary.started",
    "sanad.coverage_boundary.claim_ready",
    "sanad.coverage_boundary.coverage_decision_created",
    "sanad.coverage_boundary.blocked",
    "sanad.coverage_boundary.completed",
    "sanad.coverage_boundary.failed",
}

REQUIRED_PAYLOAD_KEYS = {
    "tenant_id",
    "deal_id",
    "run_id",
    "methodology_question_id",
    "claim_id",
    "evidence_ids",
    "source_span_ids",
    "target_status",
    "ready_for_future_sanad",
    "ic_promotion_status",
    "reason",
    "reason_codes",
}


def test_required_audit_events_are_defined_without_live_emission() -> None:
    assert REQUIRED_EVENTS.issubset(SANAD_COVERAGE_BOUNDARY_AUDIT_EVENTS)


def test_required_payload_keys_are_defined() -> None:
    assert REQUIRED_PAYLOAD_KEYS.issubset(SANAD_COVERAGE_BOUNDARY_AUDIT_EVENT_PAYLOAD_KEYS)


def test_audit_contract_has_no_confidential_content() -> None:
    serialized = "\n".join(
        sorted(
            SANAD_COVERAGE_BOUNDARY_AUDIT_EVENTS
            | SANAD_COVERAGE_BOUNDARY_AUDIT_EVENT_PAYLOAD_KEYS
        )
    )

    assert "real_example" not in serialized
    assert "financial Due Diligence.xlsx" not in serialized
    assert ".local_reports" not in serialized
    assert ".quarantine_real_example_removed" not in serialized
