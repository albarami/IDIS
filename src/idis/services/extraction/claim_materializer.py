"""Materialize methodology claim drafts through ClaimService."""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Iterable
from typing import Any

from idis.models.claim_materialization import (
    ClaimMaterializationDraftRejection,
    ClaimMaterializationReason,
    ClaimMaterializationResult,
    ClaimMaterializationStatus,
    DraftClaimMapping,
    MethodologyClaimMaterializationSummary,
    rejection,
)
from idis.models.extraction_execution import (
    MethodologyClaimDraft,
    MethodologyExtractionExecutionResult,
    generate_methodology_claim_draft_id,
)
from idis.services.claims.service import ClaimService, CreateClaimInput
from idis.validators.extraction_gate import (
    ExtractionGateBlockReason,
    ExtractionGateInput,
    evaluate_extraction_gate,
)

_RAW_SOURCE_METADATA_KEYS = {"text", "text_excerpt", "content", "raw_text"}
logger = logging.getLogger(__name__)


class MethodologyClaimMaterializationService:
    """Create claims from methodology claim drafts through ClaimService only."""

    def __init__(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        claim_service: ClaimService,
    ) -> None:
        """Initialize with scoped context and an injected ClaimService."""
        self._tenant_id = tenant_id
        self._deal_id = deal_id
        self._run_id = run_id
        self._claim_service = claim_service

    def materialize(
        self,
        *,
        drafts: list[MethodologyClaimDraft] | None = None,
        execution_result: MethodologyExtractionExecutionResult | None = None,
    ) -> ClaimMaterializationResult:
        """Materialize accepted methodology drafts into persisted claims."""
        source_drafts = (
            execution_result.accepted_claim_drafts if execution_result is not None else drafts or []
        )
        mappings: list[DraftClaimMapping] = []
        rejections: list[ClaimMaterializationDraftRejection] = []
        seen_draft_ids: set[str] = set()

        context_rejection = self._context_rejection(execution_result)
        if context_rejection is not None:
            rejections = _context_rejections(
                drafts=source_drafts,
                reason=context_rejection,
            )
            return self._result(
                total_drafts=len(source_drafts),
                mappings=mappings,
                rejections=rejections,
            )

        for draft in sorted(
            source_drafts,
            key=lambda item: (
                item.methodology_claim_draft_id or "",
                item.methodology_question_id,
                item.document_id,
            ),
        ):
            draft_id = draft.methodology_claim_draft_id
            validation_reason = self._validate_draft(
                draft=draft,
                seen_draft_ids=seen_draft_ids,
            )
            if validation_reason is not None:
                rejections.append(
                    rejection(
                        methodology_claim_draft_id=draft_id,
                        reason=validation_reason,
                        message=validation_reason.value,
                    )
                )
                continue

            seen_draft_ids.add(draft_id or "")

            try:
                claim_input = _build_claim_input(draft)
            except (TypeError, ValueError) as exc:
                logger.warning(
                    "Failed to build claim input for methodology draft %s: %s",
                    draft_id,
                    exc,
                )
                rejections.append(
                    rejection(
                        methodology_claim_draft_id=draft_id,
                        reason=ClaimMaterializationReason.MALFORMED_CLAIM_DRAFT,
                        message="failed to build claim input",
                    )
                )
                continue

            try:
                claim_data = self._claim_service.create(claim_input)
            except Exception as exc:
                logger.warning(
                    "ClaimService.create failed for methodology draft %s: %s",
                    draft_id,
                    exc,
                )
                rejections.append(
                    rejection(
                        methodology_claim_draft_id=draft_id,
                        reason=ClaimMaterializationReason.CLAIM_SERVICE_CREATE_FAILED,
                        message="claim service failed to create claim",
                    )
                )
                continue

            mappings.append(
                DraftClaimMapping(
                    methodology_claim_draft_id=draft_id or "",
                    claim_id=str(claim_data["claim_id"]),
                    extraction_task_id=draft.extraction_task_id,
                    methodology_question_id=draft.methodology_question_id,
                    document_id=draft.document_id,
                    source_span_ids=draft.source_span_ids,
                )
            )

        return self._result(
            total_drafts=len(source_drafts),
            mappings=mappings,
            rejections=rejections,
        )

    def _context_rejection(
        self,
        execution_result: MethodologyExtractionExecutionResult | None,
    ) -> ClaimMaterializationReason | None:
        if self._claim_service.tenant_id != self._tenant_id:
            return ClaimMaterializationReason.TENANT_OR_RUN_MISMATCH
        if execution_result is None:
            return None
        if (
            execution_result.tenant_id != self._tenant_id
            or execution_result.deal_id != self._deal_id
            or execution_result.run_id != self._run_id
        ):
            return ClaimMaterializationReason.TENANT_OR_RUN_MISMATCH
        return None

    def _result(
        self,
        *,
        total_drafts: int,
        mappings: list[DraftClaimMapping],
        rejections: list[ClaimMaterializationDraftRejection],
    ) -> ClaimMaterializationResult:
        summary = _build_summary(
            tenant_id=self._tenant_id,
            deal_id=self._deal_id,
            run_id=self._run_id,
            total_drafts=total_drafts,
            mappings=mappings,
            rejections=rejections,
        )
        return ClaimMaterializationResult(
            tenant_id=self._tenant_id,
            deal_id=self._deal_id,
            run_id=self._run_id,
            status=_aggregate_status(
                mappings=mappings,
                rejections=rejections,
                total_drafts=total_drafts,
            ),
            draft_claim_mappings=mappings,
            rejected_drafts=rejections,
            summary=summary,
        )

    def _validate_draft(
        self,
        *,
        draft: MethodologyClaimDraft,
        seen_draft_ids: set[str],
    ) -> ClaimMaterializationReason | None:
        if (
            draft.tenant_id != self._tenant_id
            or draft.deal_id != self._deal_id
            or draft.run_id != self._run_id
        ):
            return ClaimMaterializationReason.TENANT_OR_RUN_MISMATCH

        if not _has_methodology_linkage(draft):
            return ClaimMaterializationReason.MISSING_METHODOLOGY_LINKAGE
        if not draft.source_span_ids:
            return ClaimMaterializationReason.MISSING_SOURCE_SPAN
        if not _has_valid_claim_shape(draft):
            return ClaimMaterializationReason.MALFORMED_CLAIM_DRAFT
        if not draft.methodology_claim_draft_id:
            return ClaimMaterializationReason.STALE_OR_INVALID_DRAFT_ID
        if draft.methodology_claim_draft_id in seen_draft_ids:
            return ClaimMaterializationReason.DUPLICATE_DRAFT_ID
        if draft.methodology_claim_draft_id != _recompute_draft_id(draft):
            return ClaimMaterializationReason.STALE_OR_INVALID_DRAFT_ID

        if not _source_span_metadata_is_valid(draft):
            return ClaimMaterializationReason.SOURCE_SPAN_METADATA_MISMATCH

        gate_decision = evaluate_extraction_gate(
            ExtractionGateInput(
                claim_id=draft.methodology_claim_draft_id,
                extraction_confidence=draft.extraction_confidence,
                dhabt_score=draft.dhabt_score,
                is_human_verified=False,
            )
        )
        if gate_decision.blocked:
            return _reason_from_gate(gate_decision.reason)

        return None


def _build_claim_input(draft: MethodologyClaimDraft) -> CreateClaimInput:
    return CreateClaimInput(
        deal_id=draft.deal_id,
        claim_class=draft.claim_class,
        claim_text=draft.claim_text,
        claim_type="primary",
        predicate=draft.predicate,
        value=draft.value,
        sanad_id=None,
        claim_grade="D",
        corroboration=_build_corroboration(draft),
        claim_verdict="UNVERIFIED",
        claim_action="VERIFY",
        materiality="MEDIUM",
        ic_bound=False,
        primary_span_id=draft.source_span_ids[0],
        request_id=draft.methodology_claim_draft_id,
    )


def _build_corroboration(draft: MethodologyClaimDraft) -> dict[str, Any]:
    return {
        "level": "AHAD",
        "independent_chain_count": 1,
        "methodology_claim_draft_id": draft.methodology_claim_draft_id,
        "extraction_task_id": draft.extraction_task_id,
        "methodology_id": draft.methodology_id,
        "methodology_version_id": draft.methodology_version_id,
        "methodology_question_id": draft.methodology_question_id,
        "document_id": draft.document_id,
        "source_span_ids": draft.source_span_ids,
        "source_span_metadata": _source_span_metadata(draft),
        "extraction_confidence": str(draft.extraction_confidence),
        "dhabt_score": str(draft.dhabt_score),
        "sanad_status": "deferred",
        "coverage_status": "deferred",
    }


def _has_methodology_linkage(draft: MethodologyClaimDraft) -> bool:
    required = [
        draft.extraction_task_id,
        draft.methodology_id,
        draft.methodology_version_id,
        draft.methodology_question_id,
        draft.document_id,
    ]
    return all(value and str(value).strip() for value in required)


def _has_valid_claim_shape(draft: MethodologyClaimDraft) -> bool:
    return (
        bool(draft.claim_text.strip())
        and bool(draft.claim_class.strip())
        and bool(draft.predicate.strip())
        and isinstance(draft.value, dict)
        and bool(draft.value)
    )


def _recompute_draft_id(draft: MethodologyClaimDraft) -> str:
    return generate_methodology_claim_draft_id(
        tenant_id=draft.tenant_id,
        deal_id=draft.deal_id,
        run_id=draft.run_id,
        extraction_task_id=draft.extraction_task_id,
        methodology_question_id=draft.methodology_question_id,
        document_id=draft.document_id,
        source_span_ids=draft.source_span_ids,
        predicate=draft.predicate,
        claim_text=draft.claim_text,
        value=draft.value,
    )


def _source_span_metadata(draft: MethodologyClaimDraft) -> list[dict[str, Any]]:
    metadata_by_span_id = {item.get("span_id"): item for item in _raw_source_span_metadata(draft)}
    sanitized: list[dict[str, Any]] = []
    for span_id in draft.source_span_ids:
        item = metadata_by_span_id.get(span_id)
        if not isinstance(item, dict):
            continue
        sanitized.append(
            {
                "span_id": item["span_id"],
                "document_id": item["document_id"],
                "locator": item["locator"],
                "evidence_tags": list(item["evidence_tags"]),
            }
        )
    return sanitized


def _raw_source_span_metadata(draft: MethodologyClaimDraft) -> list[dict[str, Any]]:
    metadata = draft.future_claim_input_preview.get("corroboration", {}).get(
        "source_span_metadata", []
    )
    if not isinstance(metadata, list):
        return []
    return [item for item in metadata if isinstance(item, dict)]


def _source_span_metadata_is_valid(draft: MethodologyClaimDraft) -> bool:
    metadata = _raw_source_span_metadata(draft)
    if not metadata:
        return False

    source_span_ids = set(draft.source_span_ids)
    metadata_span_ids: set[str] = set()
    for item in metadata:
        if _contains_raw_source_key(item):
            return False
        span_id = item.get("span_id")
        document_id = item.get("document_id")
        if not isinstance(span_id, str) or span_id not in source_span_ids:
            return False
        if document_id != draft.document_id:
            return False
        if not isinstance(item.get("locator"), dict):
            return False
        evidence_tags = item.get("evidence_tags")
        if not isinstance(evidence_tags, list) or any(
            not isinstance(tag, str) or not tag.strip() for tag in evidence_tags
        ):
            return False
        metadata_span_ids.add(span_id)

    return metadata_span_ids == source_span_ids


def _contains_raw_source_key(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            key in _RAW_SOURCE_METADATA_KEYS or _contains_raw_source_key(nested_value)
            for key, nested_value in value.items()
        )
    if isinstance(value, list):
        return any(_contains_raw_source_key(item) for item in value)
    return False


def _context_rejections(
    *,
    drafts: list[MethodologyClaimDraft],
    reason: ClaimMaterializationReason,
) -> list[ClaimMaterializationDraftRejection]:
    if not drafts:
        return [
            rejection(
                methodology_claim_draft_id=None,
                reason=reason,
                message=reason.value,
            )
        ]
    return [
        rejection(
            methodology_claim_draft_id=draft.methodology_claim_draft_id,
            reason=reason,
            message=reason.value,
        )
        for draft in drafts
    ]


def _reason_from_gate(
    reason: ExtractionGateBlockReason | None,
) -> ClaimMaterializationReason:
    if reason == ExtractionGateBlockReason.LOW_CONFIDENCE:
        return ClaimMaterializationReason.BELOW_CONFIDENCE_THRESHOLD
    if reason == ExtractionGateBlockReason.LOW_DHABT:
        return ClaimMaterializationReason.BELOW_DHABT_THRESHOLD
    return ClaimMaterializationReason.MISSING_GATE_METADATA


def _aggregate_status(
    *,
    mappings: list[DraftClaimMapping],
    rejections: list[ClaimMaterializationDraftRejection],
    total_drafts: int,
) -> ClaimMaterializationStatus:
    if mappings and rejections:
        return ClaimMaterializationStatus.PARTIAL
    if rejections:
        return ClaimMaterializationStatus.FAILED
    if total_drafts == 0 or mappings:
        return ClaimMaterializationStatus.COMPLETED
    return ClaimMaterializationStatus.FAILED


def _build_summary(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    total_drafts: int,
    mappings: list[DraftClaimMapping],
    rejections: list[ClaimMaterializationDraftRejection],
) -> MethodologyClaimMaterializationSummary:
    status_counts = Counter(["completed"] * len(mappings) + ["rejected"] * len(rejections))
    return MethodologyClaimMaterializationSummary(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        total_drafts=total_drafts,
        created_claim_count=len(mappings),
        rejected_draft_count=len(rejections),
        by_status=dict(sorted(status_counts.items())),
        by_reason=_counter(rejection_item.reason.value for rejection_item in rejections),
    )


def _counter(items: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(items).items()))
