"""Future audit contract for Phase 2.7 Sanad and coverage boundary decisions."""

from __future__ import annotations

from typing import Final

SANAD_COVERAGE_BOUNDARY_AUDIT_EVENTS: Final[set[str]] = {
    "sanad.coverage_boundary.started",
    "sanad.coverage_boundary.claim_ready",
    "sanad.coverage_boundary.coverage_decision_created",
    "sanad.coverage_boundary.blocked",
    "sanad.coverage_boundary.completed",
    "sanad.coverage_boundary.failed",
}

SANAD_COVERAGE_BOUNDARY_AUDIT_EVENT_PAYLOAD_KEYS: Final[set[str]] = {
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
