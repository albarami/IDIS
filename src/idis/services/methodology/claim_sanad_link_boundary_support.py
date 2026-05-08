"""Support helpers for Phase 2.9 Claim-Sanad link boundary."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import Any

from idis.models.claim_sanad_link_boundary import (
    ClaimSanadLinkApplicationMapping,
    ClaimSanadLinkApplicationResult,
    ClaimSanadLinkApplyDecision,
    ClaimSanadLinkReason,
    ClaimSanadLinkRejection,
    ClaimSanadLinkStatus,
    ClaimSanadLinkSummary,
)
from idis.models.sanad_creation_boundary import (
    ClaimSanadLinkDecision,
    SanadCreationMapping,
)
from idis.services.claims.service import ClaimNotFoundError, ClaimService, UpdateClaimInput


def apply_one_decision(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    decision: ClaimSanadLinkApplyDecision,
    claim_service: ClaimService,
    request_id: str | None,
) -> ClaimSanadLinkApplicationMapping | ClaimSanadLinkRejection:
    if not same_scope(decision, tenant_id, deal_id, run_id):
        return rejection_from_decision(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            decision=decision,
            reason=ClaimSanadLinkReason.TENANT_OR_RUN_MISMATCH,
            message="link decision is outside requested scope",
        )
    try:
        existing_claim = claim_service.get(decision.claim_id)
    except ClaimNotFoundError as exc:
        return rejection_from_decision(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            decision=decision,
            reason=ClaimSanadLinkReason.CLAIM_NOT_FOUND,
            message=str(exc),
        )
    except Exception as exc:
        return rejection_from_decision(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            decision=decision,
            reason=ClaimSanadLinkReason.CLAIM_NOT_FOUND,
            message=str(exc),
        )

    try:
        sanad = claim_service.get_sanad(decision.sanad_id)
    except Exception as exc:
        return rejection_from_decision(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            decision=decision,
            reason=ClaimSanadLinkReason.SANAD_NOT_FOUND,
            message=str(exc),
        )

    pre_update_rejection = pre_update_rejection_for_decision(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        decision=decision,
        claim=existing_claim,
        sanad=sanad,
    )
    if pre_update_rejection is not None:
        return pre_update_rejection

    pre_link_grade = str(existing_claim.get("claim_grade"))
    update_input = UpdateClaimInput(sanad_id=decision.sanad_id, request_id=request_id)
    try:
        updated_claim = claim_service.update(decision.claim_id, update_input)
    except Exception as exc:
        return rejection_from_decision(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            decision=decision,
            reason=ClaimSanadLinkReason.SERVICE_UPDATE_FAILED,
            message=str(exc),
        )

    drift_reason = protected_field_drift_reason(
        claim=updated_claim,
        tenant_id=tenant_id,
        deal_id=deal_id,
        claim_id=decision.claim_id,
        intended_sanad_id=decision.sanad_id,
        pre_link_grade=pre_link_grade,
    )
    if drift_reason is not None:
        return rejection_from_decision(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            decision=decision,
            reason=drift_reason,
            message="ClaimService.update returned protected field drift",
        )

    mapping_payload: dict[str, Any] = {
        "tenant_id": tenant_id,
        "deal_id": deal_id,
        "run_id": run_id,
        "claim_id": decision.claim_id,
        "methodology_question_id": decision.methodology_question_id,
        "sanad_id": decision.sanad_id,
        "source_span_ids": decision.source_span_ids,
        "evidence_ids": decision.evidence_ids,
        "claim_grade": str(updated_claim.get("claim_grade")),
        "claim_verdict": str(updated_claim.get("claim_verdict")),
        "claim_action": str(updated_claim.get("claim_action")),
        "ic_bound": bool(updated_claim.get("ic_bound")),
    }
    return ClaimSanadLinkApplicationMapping(**mapping_payload)


def pre_update_rejection_for_decision(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    decision: ClaimSanadLinkApplyDecision,
    claim: dict[str, Any],
    sanad: dict[str, Any] | None,
) -> ClaimSanadLinkRejection | None:
    def reject(*, reason: ClaimSanadLinkReason, message: str) -> ClaimSanadLinkRejection:
        return rejection_from_decision(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            decision=decision,
            reason=reason,
            message=message,
        )

    if (
        claim.get("tenant_id") != tenant_id
        or claim.get("deal_id") != deal_id
        or claim.get("claim_id") != decision.claim_id
    ):
        return reject(
            reason=ClaimSanadLinkReason.CLAIM_SANAD_SCOPE_MISMATCH,
            message="claim does not match requested tenant/deal/claim scope",
        )
    if claim.get("ic_bound") is not False:
        return reject(
            reason=ClaimSanadLinkReason.BOUNDARY_VIOLATION,
            message="claim is already IC-bound and cannot be linked in Phase 2.9",
        )
    existing_sanad_id = claim.get("sanad_id")
    if existing_sanad_id == decision.sanad_id:
        return reject(
            reason=ClaimSanadLinkReason.ALREADY_LINKED,
            message="claim is already linked to the requested Sanad",
        )
    if existing_sanad_id:
        return reject(
            reason=ClaimSanadLinkReason.EXISTING_CONFLICTING_SANAD,
            message="claim already links to a different Sanad",
        )
    if sanad is None:
        return reject(
            reason=ClaimSanadLinkReason.SANAD_NOT_FOUND,
            message="Sanad was not found through ClaimService",
        )
    if (
        sanad.get("tenant_id") != tenant_id
        or sanad.get("deal_id") != deal_id
        or sanad.get("claim_id") != decision.claim_id
    ):
        return reject(
            reason=ClaimSanadLinkReason.CLAIM_SANAD_SCOPE_MISMATCH,
            message="Sanad does not match requested tenant/deal/claim scope",
        )
    return None


def protected_field_drift_reason(
    *,
    claim: dict[str, Any],
    tenant_id: str,
    deal_id: str,
    claim_id: str,
    intended_sanad_id: str,
    pre_link_grade: str,
) -> ClaimSanadLinkReason | None:
    scope_drift = (
        claim.get("tenant_id") != tenant_id
        or claim.get("deal_id") != deal_id
        or claim.get("claim_id") != claim_id
    )
    if scope_drift:
        return ClaimSanadLinkReason.CLAIM_SANAD_SCOPE_MISMATCH
    protected_drift = (
        claim.get("sanad_id") != intended_sanad_id
        or claim.get("ic_bound") is not False
        or claim.get("claim_verdict") == "VERIFIED"
        or claim.get("claim_action") == "NONE"
        or str(claim.get("claim_grade")) != pre_link_grade
    )
    if protected_drift:
        return ClaimSanadLinkReason.PROTECTED_FIELD_DRIFT
    return None


def initial_mapping_rejection(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    mapping: SanadCreationMapping,
    link_decisions: Sequence[ClaimSanadLinkDecision],
) -> ClaimSanadLinkRejection | None:
    if not same_scope(mapping, tenant_id, deal_id, run_id):
        return rejection_from_mapping(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            mapping=mapping,
            reason=ClaimSanadLinkReason.TENANT_OR_RUN_MISMATCH,
            message="Sanad creation mapping is outside requested scope",
        )
    if not str(getattr(mapping, "claim_id", "") or "").strip():
        return rejection_from_mapping(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            mapping=mapping,
            reason=ClaimSanadLinkReason.MISSING_CLAIM_ID,
            message="Sanad creation mapping is missing claim_id",
        )
    if not str(getattr(mapping, "sanad_id", "") or "").strip():
        return rejection_from_mapping(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            mapping=mapping,
            reason=ClaimSanadLinkReason.MISSING_SANAD_ID,
            message="Sanad creation mapping is missing sanad_id",
        )
    if not has_matching_phase_2_8_link(mapping, link_decisions):
        return rejection_from_mapping(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            mapping=mapping,
            reason=ClaimSanadLinkReason.STALE_MAPPING,
            message="Sanad creation mapping lacks matching Phase 2.8 link decision",
        )
    return None


def has_matching_phase_2_8_link(
    mapping: SanadCreationMapping,
    link_decisions: Sequence[ClaimSanadLinkDecision],
) -> bool:
    return any(
        link.tenant_id == mapping.tenant_id
        and link.deal_id == mapping.deal_id
        and link.run_id == mapping.run_id
        and link.claim_id == mapping.claim_id
        and link.methodology_question_id == mapping.methodology_question_id
        and link.sanad_id == mapping.sanad_id
        and link.coverage_update_status == "not_applied"
        for link in link_decisions
    )


def duplicate_conflicting_rejections(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    mappings: Sequence[SanadCreationMapping],
) -> list[ClaimSanadLinkRejection]:
    by_claim: dict[str, set[str]] = {}
    by_target: Counter[tuple[str, str]] = Counter()
    for mapping in mappings:
        claim_id = str(getattr(mapping, "claim_id", "") or "")
        sanad_id = str(getattr(mapping, "sanad_id", "") or "")
        if claim_id and sanad_id:
            by_claim.setdefault(claim_id, set()).add(sanad_id)
            by_target[(claim_id, sanad_id)] += 1
    conflicting_claims = {
        claim_id for claim_id, sanad_ids in by_claim.items() if len(sanad_ids) > 1
    }
    duplicate_targets = {target for target, target_count in by_target.items() if target_count > 1}
    if not conflicting_claims and not duplicate_targets:
        return []
    return [
        rejection_from_mapping(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            mapping=mapping,
            reason=ClaimSanadLinkReason.EXISTING_CONFLICTING_SANAD,
            message="multiple Sanad mappings target the same claim",
        )
        for mapping in mappings
        if getattr(mapping, "claim_id", None) in conflicting_claims
        or (
            str(getattr(mapping, "claim_id", "") or ""),
            str(getattr(mapping, "sanad_id", "") or ""),
        )
        in duplicate_targets
    ]


def build_result(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    total_creation_mappings: int,
    decisions: Sequence[ClaimSanadLinkApplyDecision],
    mappings: Sequence[ClaimSanadLinkApplicationMapping],
    rejections: Sequence[ClaimSanadLinkRejection],
) -> ClaimSanadLinkApplicationResult:
    status = aggregate_status(mappings=mappings or decisions, rejections=rejections)
    summary = build_summary(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        total_creation_mappings=total_creation_mappings,
        decision_count=len(decisions),
        applied_link_count=len(mappings),
        status=status,
        rejections=rejections,
    )
    return ClaimSanadLinkApplicationResult(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        status=status,
        decisions=list(decisions),
        mappings=list(mappings),
        rejections=list(rejections),
        summary=summary,
    )


def build_summary(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    total_creation_mappings: int,
    decision_count: int,
    applied_link_count: int,
    status: ClaimSanadLinkStatus,
    rejections: Sequence[ClaimSanadLinkRejection],
) -> ClaimSanadLinkSummary:
    by_reason = Counter(rejection.reason.value for rejection in rejections)
    return ClaimSanadLinkSummary(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        total_creation_mappings=total_creation_mappings,
        decision_count=decision_count,
        applied_link_count=applied_link_count,
        rejected_decision_count=len(rejections),
        already_linked_count=by_reason.get(ClaimSanadLinkReason.ALREADY_LINKED.value, 0),
        by_status={status.value: 1},
        by_reason=dict(sorted(by_reason.items())),
    )


def aggregate_status(
    *,
    mappings: Sequence[object],
    rejections: Sequence[ClaimSanadLinkRejection],
) -> ClaimSanadLinkStatus:
    has_blocking_rejection = any(
        rejection.reason != ClaimSanadLinkReason.ALREADY_LINKED for rejection in rejections
    )
    if mappings and has_blocking_rejection:
        return ClaimSanadLinkStatus.PARTIAL
    if has_blocking_rejection:
        return ClaimSanadLinkStatus.FAILED
    return ClaimSanadLinkStatus.COMPLETED


def rejection_from_mapping(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    mapping: SanadCreationMapping,
    reason: ClaimSanadLinkReason,
    message: str,
) -> ClaimSanadLinkRejection:
    return ClaimSanadLinkRejection(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        claim_id=optional_string(getattr(mapping, "claim_id", None)),
        methodology_question_id=optional_string(getattr(mapping, "methodology_question_id", None)),
        sanad_id=optional_string(getattr(mapping, "sanad_id", None)),
        source_span_ids=list(getattr(mapping, "source_span_ids", []) or []),
        evidence_ids=list(getattr(mapping, "evidence_ids", []) or []),
        reason=reason,
        reason_codes=[reason.value],
        message=message,
    )


def rejection_from_decision(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    decision: ClaimSanadLinkApplyDecision,
    reason: ClaimSanadLinkReason,
    message: str,
) -> ClaimSanadLinkRejection:
    return ClaimSanadLinkRejection(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        claim_id=decision.claim_id,
        methodology_question_id=decision.methodology_question_id,
        sanad_id=decision.sanad_id,
        source_span_ids=decision.source_span_ids,
        evidence_ids=decision.evidence_ids,
        reason=reason,
        reason_codes=[reason.value],
        message=message,
    )


def context_rejection(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    reason: ClaimSanadLinkReason,
    message: str,
) -> ClaimSanadLinkRejection:
    return ClaimSanadLinkRejection(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        methodology_question_id="__claim_sanad_link_context__",
        reason=reason,
        reason_codes=[reason.value],
        message=message,
    )


def same_scope(item: object, tenant_id: str, deal_id: str, run_id: str) -> bool:
    return (
        getattr(item, "tenant_id", None) == tenant_id
        and getattr(item, "deal_id", None) == deal_id
        and getattr(item, "run_id", None) == run_id
    )


def mapping_sort_key(mapping: SanadCreationMapping) -> tuple[str, str, str]:
    return (
        str(getattr(mapping, "methodology_question_id", "") or ""),
        str(getattr(mapping, "claim_id", "") or ""),
        str(getattr(mapping, "sanad_id", "") or ""),
    )


def decision_sort_key(decision: ClaimSanadLinkApplyDecision) -> tuple[str, str, str]:
    return (decision.methodology_question_id, decision.claim_id, decision.sanad_id)


def optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
