"""Future audit contract constants for Phase 2.9 Claim-Sanad link boundary."""

from __future__ import annotations

CLAIM_SANAD_LINK_BOUNDARY_AUDIT_EVENTS: set[str] = {
    "claim.sanad_link_boundary.started",
    "claim.sanad_link_boundary.decision_created",
    "claim.sanad_link_boundary.link_applied",
    "claim.sanad_link_boundary.link_deferred",
    "claim.sanad_link_boundary.blocked",
    "claim.sanad_link_boundary.completed",
    "claim.sanad_link_boundary.failed",
}

CLAIM_SANAD_LINK_BOUNDARY_AUDIT_EVENT_PAYLOAD_KEYS: set[str] = {
    "tenant_id",
    "deal_id",
    "run_id",
    "claim_id",
    "sanad_id",
    "methodology_question_id",
    "source_span_ids",
    "evidence_ids",
    "claim_link_status",
    "coverage_update_status",
    "claim_promotion_status",
    "reason",
    "reason_codes",
}
