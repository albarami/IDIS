"""Future audit event contract for methodology registry and coverage."""

from __future__ import annotations

from idis.models.methodology_coverage import MethodologyCoverageStatus

METHODOLOGY_AUDIT_EVENTS: set[str] = {
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

METHODOLOGY_AUDIT_EVENT_PAYLOAD_KEYS: set[str] = {
    "tenant_id",
    "deal_id",
    "run_id",
    "methodology_id",
    "methodology_version_id",
    "methodology_question_id",
    "status",
    "reason_code",
    "claim_ids",
    "evidence_ids",
    "calc_ids",
}

_STATUS_EVENTS: dict[MethodologyCoverageStatus, str] = {
    MethodologyCoverageStatus.NOT_STARTED: "methodology.question.started",
    MethodologyCoverageStatus.EVIDENCE_MISSING: "methodology.question.evidence_missing",
    MethodologyCoverageStatus.UNSUPPORTED_SOURCE: "methodology.question.unsupported_source",
    MethodologyCoverageStatus.EXTRACTED: "methodology.question.extracted",
    MethodologyCoverageStatus.PARTIALLY_ANSWERED: "methodology.question.partially_answered",
    MethodologyCoverageStatus.ANSWERED: "methodology.question.answered",
    MethodologyCoverageStatus.CONTRADICTED: "methodology.question.contradicted",
    MethodologyCoverageStatus.NOT_APPLICABLE: "methodology.question.not_applicable",
    MethodologyCoverageStatus.BLOCKED: "methodology.question.blocked",
}


def event_for_coverage_status(status: MethodologyCoverageStatus) -> str:
    """Return future audit event name for a coverage status."""
    return _STATUS_EVENTS[status]
