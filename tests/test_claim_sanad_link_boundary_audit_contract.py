"""Future audit contract tests for Phase 2.9 Claim-Sanad link boundary."""

from __future__ import annotations

REQUIRED_EVENTS = {
    "claim.sanad_link_boundary.started",
    "claim.sanad_link_boundary.decision_created",
    "claim.sanad_link_boundary.link_applied",
    "claim.sanad_link_boundary.link_deferred",
    "claim.sanad_link_boundary.blocked",
    "claim.sanad_link_boundary.completed",
    "claim.sanad_link_boundary.failed",
}

REQUIRED_PAYLOAD_KEYS = {
    "tenant_id",
    "deal_id",
    "run_id",
    "claim_id",
    "sanad_id",
    "methodology_question_id",
    "claim_link_status",
    "coverage_update_status",
    "claim_promotion_status",
    "reason",
    "reason_codes",
}


def test_claim_sanad_link_boundary_audit_events_are_declared() -> None:
    from idis.services.methodology.claim_sanad_link_boundary_audit import (
        CLAIM_SANAD_LINK_BOUNDARY_AUDIT_EVENTS,
    )

    assert REQUIRED_EVENTS <= CLAIM_SANAD_LINK_BOUNDARY_AUDIT_EVENTS


def test_claim_sanad_link_boundary_audit_payload_keys_are_safe() -> None:
    from idis.services.methodology.claim_sanad_link_boundary_audit import (
        CLAIM_SANAD_LINK_BOUNDARY_AUDIT_EVENT_PAYLOAD_KEYS,
    )

    assert REQUIRED_PAYLOAD_KEYS <= CLAIM_SANAD_LINK_BOUNDARY_AUDIT_EVENT_PAYLOAD_KEYS
    forbidden = {"raw_text", "source_text", "claim_text", "document_text", "page_content"}
    assert forbidden.isdisjoint(CLAIM_SANAD_LINK_BOUNDARY_AUDIT_EVENT_PAYLOAD_KEYS)
