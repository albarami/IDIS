"""Future audit contract for methodology claim materialization."""

from __future__ import annotations

CLAIM_MATERIALIZATION_AUDIT_EVENTS: set[str] = {
    "claim.materialization.started",
    "claim.materialization.claim_created",
    "claim.materialization.draft_rejected",
    "claim.materialization.completed",
    "claim.materialization.failed",
}

CLAIM_MATERIALIZATION_AUDIT_EVENT_PAYLOAD_KEYS: set[str] = {
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
