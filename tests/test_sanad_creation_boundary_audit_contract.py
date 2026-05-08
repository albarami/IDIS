"""Tests for future Sanad creation boundary audit contract."""

from __future__ import annotations

from idis.services.methodology.sanad_creation_boundary_audit import (
    SANAD_CREATION_BOUNDARY_AUDIT_EVENT_PAYLOAD_KEYS,
    SANAD_CREATION_BOUNDARY_AUDIT_EVENTS,
)

REQUIRED_EVENTS = {
    "sanad.creation_boundary.started",
    "sanad.creation_boundary.chain_built",
    "sanad.creation_boundary.sanad_created",
    "sanad.creation_boundary.claim_link_deferred",
    "sanad.creation_boundary.blocked",
    "sanad.creation_boundary.completed",
    "sanad.creation_boundary.failed",
}

REQUIRED_PAYLOAD_KEYS = {
    "tenant_id",
    "deal_id",
    "run_id",
    "methodology_question_id",
    "claim_id",
    "evidence_ids",
    "source_span_ids",
    "sanad_id",
    "coverage_update_status",
    "claim_link_status",
    "ic_promotion_status",
    "reason",
    "reason_codes",
}


def test_required_audit_events_are_defined_without_live_emission() -> None:
    assert REQUIRED_EVENTS.issubset(SANAD_CREATION_BOUNDARY_AUDIT_EVENTS)


def test_required_payload_keys_are_defined() -> None:
    assert REQUIRED_PAYLOAD_KEYS.issubset(SANAD_CREATION_BOUNDARY_AUDIT_EVENT_PAYLOAD_KEYS)


def test_audit_contract_has_no_confidential_or_raw_source_content() -> None:
    serialized = "\n".join(
        sorted(
            SANAD_CREATION_BOUNDARY_AUDIT_EVENTS | SANAD_CREATION_BOUNDARY_AUDIT_EVENT_PAYLOAD_KEYS
        )
    )

    assert "real_example" not in serialized
    assert "financial Due Diligence.xlsx" not in serialized
    assert ".local_reports" not in serialized
    assert ".quarantine_real_example_removed" not in serialized
    assert "raw_text" not in serialized
    assert "source_text" not in serialized
    assert "content" not in serialized
