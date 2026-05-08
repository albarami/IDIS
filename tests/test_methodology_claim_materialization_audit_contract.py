"""Tests for future claim materialization audit contract."""

from __future__ import annotations

from idis.models.claim_materialization import ClaimMaterializationReason
from idis.services.extraction.claim_materialization_audit import (
    CLAIM_MATERIALIZATION_AUDIT_EVENT_PAYLOAD_KEYS,
    CLAIM_MATERIALIZATION_AUDIT_EVENTS,
)

REQUIRED_EVENTS = {
    "claim.materialization.started",
    "claim.materialization.claim_created",
    "claim.materialization.draft_rejected",
    "claim.materialization.completed",
    "claim.materialization.failed",
}

REQUIRED_PAYLOAD_KEYS = {
    "tenant_id",
    "deal_id",
    "run_id",
    "methodology_claim_draft_id",
    "claim_id",
    "extraction_task_id",
    "methodology_id",
    "methodology_version_id",
    "methodology_question_id",
    "document_id",
    "source_span_ids",
    "status",
    "reason",
    "reason_codes",
}


def test_required_claim_materialization_audit_events_are_defined() -> None:
    assert REQUIRED_EVENTS.issubset(CLAIM_MATERIALIZATION_AUDIT_EVENTS)


def test_required_payload_keys_are_defined() -> None:
    assert REQUIRED_PAYLOAD_KEYS.issubset(CLAIM_MATERIALIZATION_AUDIT_EVENT_PAYLOAD_KEYS)


def test_rejection_reasons_are_machine_readable_for_audit() -> None:
    values = {reason.value for reason in ClaimMaterializationReason}

    assert "stale_or_invalid_draft_id" in values
    assert "tenant_or_run_mismatch" in values
    assert "claim_service_create_failed" in values


def test_audit_contract_has_no_confidential_content() -> None:
    serialized = "\n".join(
        sorted(CLAIM_MATERIALIZATION_AUDIT_EVENTS | CLAIM_MATERIALIZATION_AUDIT_EVENT_PAYLOAD_KEYS)
    )

    assert "real_example" not in serialized
    assert "financial Due Diligence.xlsx" not in serialized
    assert ".local_reports" not in serialized
    assert ".quarantine_real_example_removed" not in serialized
