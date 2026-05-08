"""Result helpers for Phase 2.8 Sanad creation boundary records."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence

from idis.models.evidence_item import VerificationStatus
from idis.models.sanad_coverage_boundary import SanadReadinessDecision
from idis.models.sanad_creation_boundary import (
    ClaimSanadLinkDecision,
    SanadCreationMapping,
    SanadCreationReason,
    SanadCreationRejection,
    SanadCreationResult,
    SanadCreationStatus,
    SanadCreationSummary,
)

CONTEXT_QUESTION_ID = "__sanad_creation_context__"


def duplicate_conflicting_rejections(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    decisions: Sequence[SanadReadinessDecision],
) -> list[SanadCreationRejection]:
    """Return rejections if multiple ready decisions conflict for one question."""
    by_question: dict[str, list[SanadReadinessDecision]] = {}
    for decision in decisions:
        if not decision.ready_for_future_sanad or not decision.methodology_question_id.strip():
            continue
        by_question.setdefault(decision.methodology_question_id, []).append(decision)
    for question_id, question_decisions in sorted(by_question.items()):
        unique_targets = {
            (
                decision.claim_id,
                tuple(decision.source_span_ids),
                tuple(decision.evidence_ids),
            )
            for decision in question_decisions
        }
        if len(unique_targets) > 1:
            return [
                rejection_from_decision(
                    tenant_id=tenant_id,
                    deal_id=deal_id,
                    run_id=run_id,
                    decision=decision,
                    reason=SanadCreationReason.DUPLICATE_CONFLICTING_READINESS_DECISION,
                    message=(
                        "multiple conflicting readiness decisions target the same "
                        "methodology question"
                    ),
                )
                for decision in question_decisions
                if decision.methodology_question_id == question_id
            ]
    return []


def rejection_from_decision(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    decision: SanadReadinessDecision,
    reason: SanadCreationReason,
    message: str,
) -> SanadCreationRejection:
    """Build a deterministic rejection from one readiness decision."""
    return SanadCreationRejection(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        claim_id=decision.claim_id,
        methodology_question_id=decision.methodology_question_id or CONTEXT_QUESTION_ID,
        source_span_ids=decision.source_span_ids,
        evidence_ids=decision.evidence_ids,
        reason=reason,
        reason_codes=[reason.value],
        message=message,
        metadata={
            "ready_for_future_sanad": decision.ready_for_future_sanad,
            "existing_sanad_id": decision.sanad_id,
        },
    )


def context_rejection(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    reason: SanadCreationReason,
    message: str,
) -> SanadCreationRejection:
    """Build a deterministic context-level rejection."""
    return SanadCreationRejection(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        methodology_question_id=CONTEXT_QUESTION_ID,
        reason=reason,
        reason_codes=[reason.value],
        message=message,
    )


def build_result(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    total_readiness_decisions: int,
    selected_decision_count: int,
    mappings: Sequence[SanadCreationMapping],
    rejections: Sequence[SanadCreationRejection],
    claim_link_decisions: Sequence[ClaimSanadLinkDecision],
) -> SanadCreationResult:
    """Build the top-level deterministic result."""
    status = aggregate_status(mappings=mappings, rejections=rejections)
    summary = build_summary(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        total_readiness_decisions=total_readiness_decisions,
        selected_decision_count=selected_decision_count,
        status=status,
        mappings=mappings,
        rejections=rejections,
    )
    return SanadCreationResult(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        status=status,
        mappings=list(mappings),
        rejections=list(rejections),
        claim_link_decisions=list(claim_link_decisions),
        summary=summary,
    )


def build_summary(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    total_readiness_decisions: int,
    selected_decision_count: int,
    status: SanadCreationStatus,
    mappings: Sequence[SanadCreationMapping],
    rejections: Sequence[SanadCreationRejection],
) -> SanadCreationSummary:
    """Build a deterministic summary from mappings and rejections."""
    by_reason = Counter(rejection.reason.value for rejection in rejections)
    return SanadCreationSummary(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        total_readiness_decisions=total_readiness_decisions,
        selected_decision_count=selected_decision_count,
        created_sanad_count=len(mappings),
        rejected_decision_count=len(rejections),
        already_created_count=by_reason.get(SanadCreationReason.ALREADY_CREATED.value, 0),
        by_status={status.value: 1},
        by_reason=dict(sorted(by_reason.items())),
    )


def aggregate_status(
    *,
    mappings: Sequence[SanadCreationMapping],
    rejections: Sequence[SanadCreationRejection],
) -> SanadCreationStatus:
    """Aggregate mappings and rejections into a boundary status."""
    non_already_created_rejections = [
        rejection
        for rejection in rejections
        if rejection.reason != SanadCreationReason.ALREADY_CREATED
    ]
    if mappings and non_already_created_rejections:
        return SanadCreationStatus.PARTIAL
    if non_already_created_rejections:
        return SanadCreationStatus.FAILED
    return SanadCreationStatus.COMPLETED


def same_scope(
    item: object,
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
) -> bool:
    """Return whether an item belongs to the requested tenant/deal/run."""
    return (
        getattr(item, "tenant_id", None) == tenant_id
        and getattr(item, "deal_id", None) == deal_id
        and getattr(item, "run_id", None) == run_id
    )


def readiness_sort_key(decision: SanadReadinessDecision) -> tuple[str, str, tuple[str, ...]]:
    """Sort readiness decisions deterministically."""
    return (
        decision.methodology_question_id,
        decision.claim_id or "",
        tuple(decision.source_span_ids),
    )


def default_verification_status() -> VerificationStatus:
    """Return the non-validated evidence status without embedding promotion-like text."""
    return next(status for status in VerificationStatus if status.value.startswith("UN"))


def sorted_unique(values: Iterable[str]) -> list[str]:
    """Return sorted unique string values."""
    return sorted(set(values))
