"""Future audit contract for Phase 2.8 Sanad creation boundary results."""

from __future__ import annotations

from typing import Final

SANAD_CREATION_BOUNDARY_AUDIT_EVENTS: Final[set[str]] = {
    "sanad.creation_boundary.started",
    "sanad.creation_boundary.chain_built",
    "sanad.creation_boundary.sanad_created",
    "sanad.creation_boundary.claim_link_deferred",
    "sanad.creation_boundary.blocked",
    "sanad.creation_boundary.completed",
    "sanad.creation_boundary.failed",
}

SANAD_CREATION_BOUNDARY_AUDIT_EVENT_PAYLOAD_KEYS: Final[set[str]] = {
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
