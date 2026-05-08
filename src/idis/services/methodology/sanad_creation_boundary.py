"""Synthetic-only Phase 2.8 Sanad creation boundary service."""

from __future__ import annotations

from collections.abc import Sequence

from idis.models.sanad_coverage_boundary import (
    MethodologyClaimEvidenceReference,
    SanadCoverageBoundaryResult,
)
from idis.models.sanad_creation_boundary import (
    ClaimSanadLinkDecision,
    SanadCreationMapping,
    SanadCreationReason,
    SanadCreationRejection,
    SanadCreationResult,
)
from idis.services.methodology.sanad_creation_boundary_results import (
    build_result,
    context_rejection,
    duplicate_conflicting_rejections,
    readiness_sort_key,
    rejection_from_decision,
    same_scope,
)
from idis.services.methodology.sanad_creation_boundary_support import (
    creation_for_decision,
)
from idis.services.sanad.service import SanadService


class SanadCreationBoundaryService:
    """Create synthetic Sanads from ready boundary decisions without live promotion."""

    def create_sanads_for_ready_decisions(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        sanad_coverage_result: SanadCoverageBoundaryResult,
        evidence_references: Sequence[MethodologyClaimEvidenceReference],
        sanad_service: SanadService,
        extraction_confidence: float = 0.9,
        dhabt_score: float | None = None,
    ) -> SanadCreationResult:
        """Create Sanads for selected synthetic Phase 2.7 readiness decisions."""
        decisions = sorted(
            sanad_coverage_result.readiness_decisions,
            key=readiness_sort_key,
        )
        if not same_scope(
            sanad_coverage_result,
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
        ):
            return build_result(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                total_readiness_decisions=len(decisions),
                selected_decision_count=0,
                mappings=[],
                rejections=[
                    rejection_from_decision(
                        tenant_id=tenant_id,
                        deal_id=deal_id,
                        run_id=run_id,
                        decision=decision,
                        reason=SanadCreationReason.TENANT_OR_RUN_MISMATCH,
                        message="coverage boundary result is outside requested scope",
                    )
                    for decision in decisions
                ]
                or [
                    context_rejection(
                        tenant_id=tenant_id,
                        deal_id=deal_id,
                        run_id=run_id,
                        reason=SanadCreationReason.TENANT_OR_RUN_MISMATCH,
                        message="coverage boundary result is outside requested scope",
                    )
                ],
                claim_link_decisions=[],
            )

        if sanad_service.tenant_id != tenant_id:
            return build_result(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                total_readiness_decisions=len(decisions),
                selected_decision_count=0,
                mappings=[],
                rejections=[
                    rejection_from_decision(
                        tenant_id=tenant_id,
                        deal_id=deal_id,
                        run_id=run_id,
                        decision=decision,
                        reason=SanadCreationReason.TENANT_OR_SERVICE_MISMATCH,
                        message="injected Sanad service tenant does not match boundary tenant",
                    )
                    for decision in decisions
                ]
                or [
                    context_rejection(
                        tenant_id=tenant_id,
                        deal_id=deal_id,
                        run_id=run_id,
                        reason=SanadCreationReason.TENANT_OR_SERVICE_MISMATCH,
                        message="injected Sanad service tenant does not match boundary tenant",
                    )
                ],
                claim_link_decisions=[],
            )

        duplicate_rejections = duplicate_conflicting_rejections(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            decisions=decisions,
        )
        if duplicate_rejections:
            return build_result(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                total_readiness_decisions=len(decisions),
                selected_decision_count=0,
                mappings=[],
                rejections=duplicate_rejections,
                claim_link_decisions=[],
            )

        mappings: list[SanadCreationMapping] = []
        rejections: list[SanadCreationRejection] = []
        claim_link_decisions: list[ClaimSanadLinkDecision] = []
        selected_decision_count = 0
        for decision in decisions:
            outcome = creation_for_decision(
                tenant_id=tenant_id,
                deal_id=deal_id,
                run_id=run_id,
                decision=decision,
                evidence_references=evidence_references,
                sanad_service=sanad_service,
                extraction_confidence=extraction_confidence,
                dhabt_score=dhabt_score,
            )
            if isinstance(outcome, SanadCreationRejection):
                rejections.append(outcome)
                continue
            mapping, link_decision = outcome
            selected_decision_count += 1
            mappings.append(mapping)
            claim_link_decisions.append(link_decision)

        selected_decision_count += sum(
            1
            for rejection in rejections
            if rejection.reason
            in {
                SanadCreationReason.CHAIN_BUILD_FAILED,
                SanadCreationReason.SANAD_CREATION_FAILED,
            }
        )
        return build_result(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            total_readiness_decisions=len(decisions),
            selected_decision_count=selected_decision_count,
            mappings=mappings,
            rejections=rejections,
            claim_link_decisions=claim_link_decisions,
        )
