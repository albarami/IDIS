"""Support helpers for the Phase 2.8 Sanad creation boundary."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from idis.models.evidence_item import EvidenceItem, SourceGrade, VerificationStatus
from idis.models.methodology_coverage import MethodologyCoverageStatus
from idis.models.sanad_coverage_boundary import (
    ICPromotionStatus,
    MethodologyClaimEvidenceReference,
    SanadCoverageBoundaryReason,
    SanadReadinessDecision,
)
from idis.models.sanad_creation_boundary import (
    ClaimSanadLinkDecision,
    SanadCreationMapping,
    SanadCreationReason,
    SanadCreationRejection,
)
from idis.services.methodology.sanad_creation_boundary_results import (
    default_verification_status,
    rejection_from_decision,
    same_scope,
    sorted_unique,
)
from idis.services.sanad import chain_builder
from idis.services.sanad.chain_builder import ChainBuildError
from idis.services.sanad.service import CreateSanadInput, SanadService, SanadServiceError

_VALID_NODE_TYPES = {
    "INGEST",
    "EXTRACT",
    "NORMALIZE",
    "RECONCILE",
    "CALCULATE",
    "INFER",
    "HUMAN_VERIFY",
    "EXPORT",
}
_VALID_ACTOR_TYPES = {"AGENT", "HUMAN", "SYSTEM"}
_REQUIRED_NODE_FIELDS = {
    "node_id",
    "node_type",
    "actor_type",
    "actor_id",
    "timestamp",
    "input_refs",
    "output_refs",
}


def creation_for_decision(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    decision: SanadReadinessDecision,
    evidence_references: Sequence[MethodologyClaimEvidenceReference],
    sanad_service: SanadService,
    extraction_confidence: float,
    dhabt_score: float | None,
) -> tuple[SanadCreationMapping, ClaimSanadLinkDecision] | SanadCreationRejection:
    """Create one Sanad or return a deterministic rejection."""
    initial_rejection = initial_rejection_for_decision(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        decision=decision,
    )
    if initial_rejection is not None:
        return initial_rejection

    matching_references_or_rejection = matching_evidence_references(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        decision=decision,
        evidence_references=evidence_references,
    )
    if isinstance(matching_references_or_rejection, SanadCreationRejection):
        return matching_references_or_rejection
    matching_references = matching_references_or_rejection
    evidence_items = evidence_items_from_references(matching_references)
    evidence_item_payloads = [item.to_canonical_dict() for item in evidence_items]

    claim_id = str(decision.claim_id)
    try:
        chain_data = chain_builder.build_sanad_chain(
            tenant_id=tenant_id,
            deal_id=deal_id,
            claim_id=claim_id,
            evidence_items=evidence_item_payloads,
            extraction_metadata={
                "confidence": extraction_confidence,
                "deduped": False,
                "methodology_question_id": decision.methodology_question_id,
                "run_id": run_id,
            },
        )
    except ChainBuildError as exc:
        return rejection_from_decision(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            decision=decision,
            reason=SanadCreationReason.CHAIN_BUILD_FAILED,
            message=exc.reason,
        )
    except (TypeError, ValueError) as exc:
        return rejection_from_decision(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            decision=decision,
            reason=SanadCreationReason.CHAIN_BUILD_FAILED,
            message=str(exc),
        )

    validated_chain_or_rejection = validated_chain_data(
        tenant_id=tenant_id,
        deal_id=deal_id,
        claim_id=claim_id,
        decision=decision,
        chain_data=chain_data,
        evidence_ids=[item.evidence_id for item in evidence_items],
    )
    if isinstance(validated_chain_or_rejection, SanadCreationRejection):
        return validated_chain_or_rejection
    primary_evidence_id, transmission_chain = validated_chain_or_rejection

    evidence_ids = sorted_unique(item.evidence_id for item in evidence_items)
    corroborating_evidence_ids = [
        evidence_id for evidence_id in evidence_ids if evidence_id != primary_evidence_id
    ]
    sanad_input_kwargs: dict[str, Any] = {
        "claim_id": claim_id,
        "deal_id": deal_id,
        "primary_evidence_id": primary_evidence_id,
        "corroborating_evidence_ids": corroborating_evidence_ids,
        "transmission_chain": transmission_chain,
        "extraction_confidence": extraction_confidence,
    }
    if dhabt_score is not None and "dhabt_score" in CreateSanadInput.model_fields:
        sanad_input_kwargs["dhabt_score"] = dhabt_score
    sanad_input = CreateSanadInput(**sanad_input_kwargs)
    try:
        created_sanad = sanad_service.create(sanad_input)
    except (SanadServiceError, TypeError, ValueError) as exc:
        return rejection_from_decision(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            decision=decision,
            reason=SanadCreationReason.SANAD_CREATION_FAILED,
            message=str(exc),
        )
    except Exception as exc:
        return rejection_from_decision(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            decision=decision,
            reason=SanadCreationReason.SANAD_CREATION_FAILED,
            message=str(exc),
        )

    persisted_sanad_id = str(created_sanad["sanad_id"])
    created_chain = created_sanad.get("transmission_chain") or transmission_chain
    mapping = SanadCreationMapping(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        claim_id=claim_id,
        methodology_question_id=decision.methodology_question_id,
        source_span_ids=decision.source_span_ids,
        evidence_ids=evidence_ids,
        primary_evidence_id=primary_evidence_id,
        corroborating_evidence_ids=corroborating_evidence_ids,
        sanad_id=persisted_sanad_id,
        transmission_chain_node_count=len(created_chain),
        chain_node_types=[str(node.get("node_type", "")) for node in created_chain],
        extraction_confidence=extraction_confidence,
        dhabt_score=dhabt_score,
    )
    link_decision = ClaimSanadLinkDecision(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        claim_id=claim_id,
        methodology_question_id=decision.methodology_question_id,
        sanad_id=persisted_sanad_id,
    )
    return mapping, link_decision


def validated_chain_data(
    *,
    tenant_id: str,
    deal_id: str,
    claim_id: str,
    decision: SanadReadinessDecision,
    chain_data: dict[str, Any],
    evidence_ids: Sequence[str],
) -> tuple[str, list[dict[str, Any]]] | SanadCreationRejection:
    """Validate builder output so service creation never relies on fallback behavior."""
    if (
        chain_data.get("tenant_id") != tenant_id
        or chain_data.get("deal_id") != deal_id
        or chain_data.get("claim_id") != claim_id
    ):
        return rejection_from_decision(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=decision.run_id,
            decision=decision,
            reason=SanadCreationReason.CHAIN_BUILD_FAILED,
            message="chain builder returned mismatched scope or claim linkage",
        )
    primary_evidence_id = chain_data.get("primary_evidence_id")
    if not isinstance(primary_evidence_id, str) or not primary_evidence_id.strip():
        return rejection_from_decision(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=decision.run_id,
            decision=decision,
            reason=SanadCreationReason.CHAIN_BUILD_FAILED,
            message="chain builder returned missing primary evidence linkage",
        )
    if primary_evidence_id not in set(evidence_ids):
        return rejection_from_decision(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=decision.run_id,
            decision=decision,
            reason=SanadCreationReason.CHAIN_BUILD_FAILED,
            message="chain builder returned primary evidence outside canonical inputs",
        )
    transmission_chain = chain_data.get("transmission_chain")
    if not isinstance(transmission_chain, list) or not transmission_chain:
        return rejection_from_decision(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=decision.run_id,
            decision=decision,
            reason=SanadCreationReason.CHAIN_BUILD_FAILED,
            message="chain builder returned empty or malformed transmission chain",
        )
    if _malformed_chain_nodes(transmission_chain):
        return rejection_from_decision(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=decision.run_id,
            decision=decision,
            reason=SanadCreationReason.CHAIN_BUILD_FAILED,
            message="chain builder returned malformed transmission nodes",
        )
    return primary_evidence_id, transmission_chain


def _malformed_chain_nodes(transmission_chain: list[Any]) -> bool:
    previous_node_id: str | None = None
    for index, node in enumerate(transmission_chain):
        if not isinstance(node, dict):
            return True
        if any(field not in node for field in _REQUIRED_NODE_FIELDS):
            return True
        node_id = node.get("node_id")
        if not isinstance(node_id, str) or not _is_uuid(node_id):
            return True
        if node.get("node_type") not in _VALID_NODE_TYPES:
            return True
        if node.get("actor_type") not in _VALID_ACTOR_TYPES:
            return True
        if not isinstance(node.get("actor_id"), str) or not node["actor_id"].strip():
            return True
        if not isinstance(node.get("timestamp"), str) or not node["timestamp"].strip():
            return True
        if not isinstance(node.get("input_refs"), list) or not node["input_refs"]:
            return True
        if not isinstance(node.get("output_refs"), list) or not node["output_refs"]:
            return True
        prev_node_id = node.get("prev_node_id")
        if index == 0 and prev_node_id is not None:
            return True
        if index > 0 and prev_node_id != previous_node_id:
            return True
        previous_node_id = node_id
    return False


def _is_uuid(value: str) -> bool:
    try:
        UUID(value)
    except ValueError:
        return False
    return True


def initial_rejection_for_decision(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    decision: SanadReadinessDecision,
) -> SanadCreationRejection | None:
    """Return a rejection for a decision that is not selectable."""
    if not same_scope(decision, tenant_id=tenant_id, deal_id=deal_id, run_id=run_id):
        return rejection_from_decision(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            decision=decision,
            reason=SanadCreationReason.TENANT_OR_RUN_MISMATCH,
            message="readiness decision is outside requested scope",
        )
    if decision.sanad_id is not None:
        return rejection_from_decision(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            decision=decision,
            reason=SanadCreationReason.ALREADY_CREATED,
            message="readiness decision already references an existing Sanad",
        )
    if decision.ic_promotion_status != ICPromotionStatus.DEFERRED_UNTIL_SANAD:
        return rejection_from_decision(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            decision=decision,
            reason=SanadCreationReason.BLOCKED,
            message="readiness decision is not eligible for synthetic Sanad creation",
        )
    if not decision.ready_for_future_sanad or (
        decision.reason != SanadCoverageBoundaryReason.READY_FOR_FUTURE_SANAD
    ):
        reason = _creation_reason_for_non_ready_decision(decision.reason)
        return rejection_from_decision(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            decision=decision,
            reason=reason,
            message="readiness decision reason is not eligible for Sanad creation",
        )
    if not (
        decision.claim_id
        and decision.methodology_question_id.strip()
        and decision.source_span_ids
    ):
        return rejection_from_decision(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            decision=decision,
            reason=SanadCreationReason.MISSING_CLAIM_LINKAGE,
            message="decision is missing claim, methodology, or source-span linkage",
        )
    if not decision.evidence_ids:
        return rejection_from_decision(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            decision=decision,
            reason=SanadCreationReason.EVIDENCE_MISSING,
            message="decision is missing synthetic evidence ids",
        )
    return None


def _creation_reason_for_non_ready_decision(
    reason: SanadCoverageBoundaryReason,
) -> SanadCreationReason:
    if reason == SanadCoverageBoundaryReason.EVIDENCE_MISSING:
        return SanadCreationReason.EVIDENCE_MISSING
    if reason == SanadCoverageBoundaryReason.SOURCE_SPAN_MISMATCH:
        return SanadCreationReason.SOURCE_SPAN_MISMATCH
    if reason == SanadCoverageBoundaryReason.TENANT_OR_RUN_MISMATCH:
        return SanadCreationReason.TENANT_OR_RUN_MISMATCH
    if reason == SanadCoverageBoundaryReason.MISSING_METHODOLOGY_LINKAGE:
        return SanadCreationReason.MISSING_CLAIM_LINKAGE
    if reason == SanadCoverageBoundaryReason.DUPLICATE_CONFLICTING_MAPPING:
        return SanadCreationReason.DUPLICATE_CONFLICTING_READINESS_DECISION
    if reason == SanadCoverageBoundaryReason.CONTRADICTED:
        return SanadCreationReason.CONTRADICTED_EVIDENCE
    return SanadCreationReason.BLOCKED


def matching_evidence_references(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    decision: SanadReadinessDecision,
    evidence_references: Sequence[MethodologyClaimEvidenceReference],
) -> list[MethodologyClaimEvidenceReference] | SanadCreationRejection:
    """Return source-span scoped synthetic evidence references for one decision."""
    candidates = [
        reference
        for reference in evidence_references
        if reference.methodology_question_id == decision.methodology_question_id
        and reference.claim_id == decision.claim_id
    ]
    scoped_candidates = [
        reference
        for reference in candidates
        if same_scope(reference, tenant_id=tenant_id, deal_id=deal_id, run_id=run_id)
    ]
    if candidates and not scoped_candidates:
        return rejection_from_decision(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            decision=decision,
            reason=SanadCreationReason.TENANT_OR_RUN_MISMATCH,
            message="synthetic evidence reference is outside requested scope",
        )

    decision_evidence_ids = set(decision.evidence_ids)
    decision_source_span_ids = set(decision.source_span_ids)
    mismatched_references = [
        reference
        for reference in scoped_candidates
        if reference.source_span_id not in decision_source_span_ids
    ]
    if mismatched_references:
        return rejection_from_decision(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            decision=decision,
            reason=SanadCreationReason.SOURCE_SPAN_MISMATCH,
            message="synthetic evidence reference source span is outside the decision spans",
        )

    contradicted_references = [
        reference
        for reference in scoped_candidates
        if reference.target_status == MethodologyCoverageStatus.CONTRADICTED
    ]
    if contradicted_references:
        return rejection_from_decision(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            decision=decision,
            reason=SanadCreationReason.CONTRADICTED_EVIDENCE,
            message="contradicted synthetic evidence is not eligible for Phase 2.8 creation",
        )

    matching = [
        reference
        for reference in scoped_candidates
        if reference.evidence_id in decision_evidence_ids
        and reference.source_span_id in decision_source_span_ids
    ]
    if {reference.evidence_id for reference in matching} != decision_evidence_ids:
        return rejection_from_decision(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            decision=decision,
            reason=SanadCreationReason.EVIDENCE_MISSING,
            message="synthetic evidence references do not cover the decision evidence ids",
        )

    malformed = [
        reference
        for reference in matching
        if reference.target_status == MethodologyCoverageStatus.CONTRADICTED
        and not (reference.conflict_ids or reference.defect_ids)
    ]
    if malformed:
        return rejection_from_decision(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            decision=decision,
            reason=SanadCreationReason.MALFORMED_EVIDENCE_REFERENCE,
            message="contradicted synthetic evidence lacks conflict or defect linkage",
        )
    return sorted(
        matching,
        key=lambda reference: (
            reference.evidence_id,
            reference.source_span_id,
            reference.methodology_question_id,
        ),
    )


def evidence_items_from_references(
    references: Sequence[MethodologyClaimEvidenceReference],
) -> list[EvidenceItem]:
    """Build canonical in-memory evidence payloads from synthetic references."""
    return [
        EvidenceItem(
            evidence_id=reference.evidence_id,
            tenant_id=reference.tenant_id,
            deal_id=reference.deal_id,
            source_span_id=reference.source_span_id,
            source_system="synthetic_methodology_boundary",
            upstream_origin_id=reference.source_span_id,
            verification_status=(
                VerificationStatus.CONTRADICTED
                if reference.target_status == MethodologyCoverageStatus.CONTRADICTED
                else default_verification_status()
            ),
            source_grade=SourceGrade.D,
            rationale={
                "methodology_question_id": reference.methodology_question_id,
                "claim_id": reference.claim_id,
                "run_id": reference.run_id,
                "calc_ids": reference.calc_ids,
                "conflict_ids": reference.conflict_ids,
                "defect_ids": reference.defect_ids,
            },
        )
        for reference in references
    ]


