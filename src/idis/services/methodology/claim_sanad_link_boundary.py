"""Synthetic-only Phase 2.9 Claim-Sanad link application boundary."""

from __future__ import annotations

from collections.abc import Sequence

from idis.models.claim_sanad_link_boundary import (
    ClaimSanadLinkApplicationMapping,
    ClaimSanadLinkApplicationResult,
    ClaimSanadLinkApplyDecision,
    ClaimSanadLinkReason,
    ClaimSanadLinkRejection,
)
from idis.models.sanad_creation_boundary import SanadCreationResult
from idis.services.claims.service import ClaimService
from idis.services.methodology.claim_sanad_link_boundary_support import (
    apply_one_decision,
    build_result,
    context_rejection,
    decision_sort_key,
    duplicate_conflicting_rejections,
    initial_mapping_rejection,
    mapping_sort_key,
    rejection_from_decision,
    rejection_from_mapping,
    same_scope,
)


class ClaimSanadLinkBoundaryService:
    """Build and explicitly apply synthetic claim-to-Sanad link decisions."""

    def build_claim_sanad_link_decisions(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        sanad_creation_result: SanadCreationResult,
    ) -> ClaimSanadLinkApplicationResult:
        """Build deterministic Claim-Sanad link decisions from Phase 2.8 output."""
        mappings = sorted(sanad_creation_result.mappings, key=mapping_sort_key)
        if not same_scope(sanad_creation_result, tenant_id, deal_id, run_id):
            return build_result(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                total_creation_mappings=len(mappings),
                decisions=[],
                mappings=[],
                rejections=[
                    rejection_from_mapping(
                        tenant_id=tenant_id,
                        deal_id=deal_id,
                        run_id=run_id,
                        mapping=mapping,
                        reason=ClaimSanadLinkReason.TENANT_OR_RUN_MISMATCH,
                        message="Sanad creation result is outside requested scope",
                    )
                    for mapping in mappings
                ]
                or [
                    context_rejection(
                        tenant_id=tenant_id,
                        deal_id=deal_id,
                        run_id=run_id,
                        reason=ClaimSanadLinkReason.TENANT_OR_RUN_MISMATCH,
                        message="Sanad creation result is outside requested scope",
                    )
                ],
            )

        duplicate_rejections = duplicate_conflicting_rejections(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            mappings=mappings,
        )
        rejected_targets = {
            (rejection.claim_id, rejection.sanad_id) for rejection in duplicate_rejections
        }

        decisions: list[ClaimSanadLinkApplyDecision] = []
        rejections: list[ClaimSanadLinkRejection] = list(duplicate_rejections)
        for mapping in mappings:
            if (str(mapping.claim_id), str(mapping.sanad_id)) in rejected_targets:
                continue
            rejection = initial_mapping_rejection(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                mapping=mapping,
                link_decisions=sanad_creation_result.claim_link_decisions,
            )
            if rejection is not None:
                rejections.append(rejection)
                continue
            decisions.append(
                ClaimSanadLinkApplyDecision(
                    tenant_id=tenant_id,
                    deal_id=deal_id,
                    run_id=run_id,
                    claim_id=str(mapping.claim_id),
                    methodology_question_id=str(mapping.methodology_question_id),
                    sanad_id=str(mapping.sanad_id),
                    source_span_ids=list(mapping.source_span_ids),
                    evidence_ids=list(mapping.evidence_ids),
                )
            )

        return build_result(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            total_creation_mappings=len(mappings),
            decisions=decisions,
            mappings=[],
            rejections=rejections,
        )

    def apply_claim_sanad_links(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        link_decisions: Sequence[ClaimSanadLinkApplyDecision],
        claim_service: ClaimService,
        request_id: str | None = None,
    ) -> ClaimSanadLinkApplicationResult:
        """Apply claim-to-Sanad links through an explicitly injected ClaimService."""
        decisions = sorted(link_decisions, key=decision_sort_key)
        if claim_service.tenant_id != tenant_id:
            return build_result(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                total_creation_mappings=len(decisions),
                decisions=decisions,
                mappings=[],
                rejections=[
                    rejection_from_decision(
                        tenant_id=tenant_id,
                        deal_id=deal_id,
                        run_id=run_id,
                        decision=decision,
                        reason=ClaimSanadLinkReason.TENANT_OR_SERVICE_MISMATCH,
                        message="injected ClaimService tenant does not match boundary tenant",
                    )
                    for decision in decisions
                ],
            )

        mappings: list[ClaimSanadLinkApplicationMapping] = []
        rejections: list[ClaimSanadLinkRejection] = []
        for decision in decisions:
            outcome = apply_one_decision(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                decision=decision,
                claim_service=claim_service,
                request_id=request_id,
            )
            if isinstance(outcome, ClaimSanadLinkRejection):
                rejections.append(outcome)
                continue
            mappings.append(outcome)

        return build_result(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            total_creation_mappings=len(decisions),
            decisions=decisions,
            mappings=mappings,
            rejections=rejections,
        )
