"""ExtractionPipeline — orchestrates the full claim extraction flow.

Pipeline steps:
1. Chunk spans by document type via ChunkingService
2. Extract claims from each chunk via LLMClaimExtractor
3. Deduplicate claims via Deduplicator
4. Detect conflicts via ConflictDetector
5. Persist claims and evidence via ClaimService
6. Emit audit events for extraction lifecycle

Fail-closed on:
- Unknown doc_type (no silent fallback)
- LLM extraction failures after retries
- Missing extractor configuration
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from idis.audit.sink import AuditSink, InMemoryAuditSink
from idis.persistence.repositories.claims import InMemoryEvidenceRepository
from idis.persistence.repositories.evidence import EvidenceRepo
from idis.services.claims.service import ClaimService, CreateClaimInput
from idis.services.extraction.chunking.service import ChunkingService
from idis.services.extraction.confidence.scorer import (
    CONFIDENCE_ACCEPT_WITH_FLAG,
    CONFIDENCE_AUTO_ACCEPT,
    CONFIDENCE_HUMAN_REVIEW,
)
from idis.services.extraction.extractors.claim_extractor import (
    ChunkExtractionResult,
    LLMClaimExtractor,
)
from idis.services.extraction.resolution.conflict_detector import (
    ConflictDetectionResult,
    ConflictDetector,
)
from idis.services.extraction.resolution.deduplicator import (
    DeduplicatedClaim,
    DeduplicationResult,
    Deduplicator,
)
from idis.services.extraction.service import ExtractedClaimDraft

logger = logging.getLogger(__name__)


@dataclass
class PipelineRunResult:
    """Result of a full extraction pipeline run.

    Attributes:
        run_id: Pipeline run UUID.
        tenant_id: Tenant context.
        deal_id: Deal context.
        status: COMPLETED, FAILED, PARTIAL.
        created_claim_ids: IDs of persisted claims.
        chunk_count: Number of chunks processed.
        raw_claim_count: Claims before dedup.
        unique_claim_count: Claims after dedup.
        conflict_count: Number of detected conflicts.
        errors: Structured errors from extraction.
    """

    run_id: str
    tenant_id: str
    deal_id: str
    status: str = "COMPLETED"
    created_claim_ids: list[str] = field(default_factory=list)
    chunk_count: int = 0
    raw_claim_count: int = 0
    unique_claim_count: int = 0
    conflict_count: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)


class ExtractionPipeline:
    """Orchestrates the full claim extraction flow.

    Wires together ChunkingService, LLMClaimExtractor, Deduplicator,
    ConflictDetector, and ClaimService for persistence.
    """

    def __init__(
        self,
        *,
        chunking_service: ChunkingService,
        claim_extractor: LLMClaimExtractor,
        deduplicator: Deduplicator,
        conflict_detector: ConflictDetector,
        claim_service: ClaimService,
        evidence_repo: EvidenceRepo | None = None,
        audit_sink: AuditSink | None = None,
    ) -> None:
        """Initialize the extraction pipeline.

        Args:
            chunking_service: Routes spans to correct chunker.
            claim_extractor: LLM-backed claim extractor.
            deduplicator: Duplicate claim detector.
            conflict_detector: Value conflict detector.
            claim_service: Service for persisting claims.
            evidence_repo: Repository for evidence persistence.
            audit_sink: Audit event sink (defaults to in-memory).
        """
        self._chunking = chunking_service
        self._extractor = claim_extractor
        self._deduplicator = deduplicator
        self._conflict_detector = conflict_detector
        self._claim_service = claim_service
        self._evidence_repo = evidence_repo or InMemoryEvidenceRepository(
            claim_service.tenant_id,
        )
        self._audit_sink = audit_sink or InMemoryAuditSink()

    def run(
        self,
        *,
        run_id: str,
        tenant_id: str,
        deal_id: str,
        documents: list[dict[str, Any]],
    ) -> PipelineRunResult:
        """Execute the full extraction pipeline.

        Args:
            run_id: Pipeline run UUID.
            tenant_id: Tenant context for isolation.
            deal_id: Deal context.
            documents: List of document dicts with doc_type, document_id, spans.

        Returns:
            PipelineRunResult with extraction stats and claim IDs.
        """
        self._emit_audit(
            "extraction.pipeline.started",
            tenant_id=tenant_id,
            details={"run_id": run_id, "deal_id": deal_id, "doc_count": len(documents)},
        )

        all_drafts: list[ExtractedClaimDraft] = []
        all_errors: list[dict[str, Any]] = []
        chunk_count = 0

        for doc in documents:
            doc_id = doc.get("document_id", "")
            doc_type = doc.get("doc_type", "")
            spans = doc.get("spans", [])
            doc_name = doc.get("document_name", "unknown")

            try:
                chunks = self._chunking.chunk_spans(
                    spans,
                    document_id=doc_id,
                    doc_type=doc_type,
                )
            except Exception as e:
                logger.error("Chunking failed for doc %s: %s", doc_id, e)
                all_errors.append(
                    {
                        "code": "CHUNKING_FAILED",
                        "document_id": doc_id,
                        "message": str(e),
                    }
                )
                self._emit_audit(
                    "extraction.chunking.failed",
                    tenant_id=tenant_id,
                    details={"document_id": doc_id, "error": str(e)},
                )
                continue

            chunk_count += len(chunks)

            for chunk in chunks:
                result: ChunkExtractionResult = self._extractor.extract_from_chunk(
                    chunk_content=chunk.content,
                    chunk_locator=chunk.locator,
                    document_type=doc_type,
                    document_name=doc_name,
                    span_ids=chunk.span_ids,
                )

                all_drafts.extend(result.drafts)

                for err in result.errors:
                    all_errors.append(
                        {
                            "code": err.code,
                            "message": err.message,
                            "chunk_id": chunk.chunk_id,
                            "attempt": err.attempt,
                        }
                    )
                    self._emit_audit(
                        "extraction.chunk.failed",
                        tenant_id=tenant_id,
                        details={
                            "chunk_id": chunk.chunk_id,
                            "error_code": err.code,
                            "attempt": err.attempt,
                        },
                    )

        raw_claim_count = len(all_drafts)

        claim_dicts = [
            {
                "claim_text": d.claim_text,
                "claim_class": d.claim_class,
                "extraction_confidence": str(d.extraction_confidence),
                "span_id": d.span_id,
                "predicate": d.predicate,
                "value": d.value,
            }
            for d in all_drafts
        ]

        dedup_result: DeduplicationResult = self._deduplicator.deduplicate(
            claim_dicts,
            deal_id=deal_id,
        )

        conflict_result: ConflictDetectionResult = self._conflict_detector.detect(
            dedup_result.unique_claims,
        )

        created_ids = self._persist_claims(
            unique_claims=dedup_result.unique_claims,
            tenant_id=tenant_id,
            deal_id=deal_id,
        )

        status = "COMPLETED"
        if all_errors and not created_ids:
            status = "FAILED"
        elif all_errors:
            status = "PARTIAL"

        self._emit_audit(
            "extraction.pipeline.completed",
            tenant_id=tenant_id,
            details={
                "run_id": run_id,
                "deal_id": deal_id,
                "status": status,
                "chunk_count": chunk_count,
                "raw_claim_count": raw_claim_count,
                "unique_claim_count": len(dedup_result.unique_claims),
                "conflict_count": conflict_result.conflict_count,
                "created_claim_count": len(created_ids),
                "error_count": len(all_errors),
            },
        )

        return PipelineRunResult(
            run_id=run_id,
            tenant_id=tenant_id,
            deal_id=deal_id,
            status=status,
            created_claim_ids=created_ids,
            chunk_count=chunk_count,
            raw_claim_count=raw_claim_count,
            unique_claim_count=len(dedup_result.unique_claims),
            conflict_count=conflict_result.conflict_count,
            errors=all_errors,
        )

    def _persist_claims(
        self,
        *,
        unique_claims: list[DeduplicatedClaim],
        tenant_id: str,
        deal_id: str,
    ) -> list[str]:
        """Persist deduplicated claims via ClaimService.

        Creates real claim records with primary_span_id for evidence linkage.
        Also creates evidence items and claim-evidence join records.

        Args:
            unique_claims: Deduplicated claims to persist.
            tenant_id: Tenant UUID.
            deal_id: Deal UUID.

        Returns:
            List of created claim IDs.
        """
        created_ids: list[str] = []

        for claim in unique_claims:
            confidence = claim.extraction_confidence
            claim_action = _confidence_to_action(confidence)
            primary_span_id = claim.span_ids[0] if claim.span_ids else None

            try:
                claim_input = CreateClaimInput(
                    deal_id=deal_id,
                    claim_class=claim.claim_class,
                    claim_text=claim.claim_text,
                    claim_type="primary",
                    predicate=claim.predicate,
                    value=claim.value,
                    claim_grade="D",
                    claim_verdict="UNVERIFIED",
                    claim_action=claim_action,
                    materiality="MEDIUM",
                    ic_bound=False,
                    primary_span_id=primary_span_id,
                )
                claim_data = self._claim_service.create(claim_input)
                claim_id = claim_data["claim_id"]
                created_ids.append(claim_id)

                self._persist_evidence(
                    claim_id=claim_id,
                    tenant_id=tenant_id,
                    deal_id=deal_id,
                    span_ids=claim.span_ids,
                )

            except Exception as e:
                logger.error(
                    "Failed to persist claim '%s': %s",
                    claim.claim_text[:80],
                    e,
                )
                continue

            self._emit_audit(
                "claim.created",
                tenant_id=tenant_id,
                details={
                    "claim_id": claim_id,
                    "deal_id": deal_id,
                    "claim_class": claim.claim_class,
                    "confidence": str(confidence),
                    "action": claim_action,
                    "span_ids": claim.span_ids,
                },
            )

        return created_ids

    def _persist_evidence(
        self,
        *,
        claim_id: str,
        tenant_id: str,
        deal_id: str,
        span_ids: list[str],
    ) -> None:
        """Create evidence items for a claim's source spans.

        Each span_id becomes an evidence row with source_grade=D
        and verification_status=UNVERIFIED, persisted via the
        evidence repository.

        Args:
            claim_id: The persisted claim ID.
            tenant_id: Tenant UUID.
            deal_id: Deal UUID.
            span_ids: Source span UUIDs from extraction.
        """
        for span_id in span_ids:
            if not span_id:
                continue
            evidence_id = str(uuid.uuid4())
            evidence_record = self._evidence_repo.create(
                evidence_id=evidence_id,
                tenant_id=tenant_id,
                deal_id=deal_id,
                claim_id=claim_id,
                source_span_id=span_id,
                source_grade="D",
                verification_status="UNVERIFIED",
            )
            self._emit_audit(
                "evidence.created",
                tenant_id=tenant_id,
                details=evidence_record,
            )

    def _emit_audit(
        self,
        event_type: str,
        *,
        tenant_id: str,
        details: dict[str, Any],
    ) -> None:
        """Emit an audit event.

        Args:
            event_type: Event type string.
            tenant_id: Tenant context.
            details: Event details dict.
        """
        event = {
            "event_type": event_type,
            "tenant_id": tenant_id,
            "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "details": details,
        }
        try:
            self._audit_sink.emit(event)
        except Exception as e:
            logger.warning("Failed to emit audit event: %s", e)


def _confidence_to_action(confidence: Decimal) -> str:
    """Map confidence score to claim action.

    Args:
        confidence: Extraction confidence score.

    Returns:
        Claim action string.
    """
    if confidence >= CONFIDENCE_AUTO_ACCEPT:
        return "NONE"
    if confidence >= CONFIDENCE_ACCEPT_WITH_FLAG:
        return "FLAG"
    if confidence >= CONFIDENCE_HUMAN_REVIEW:
        return "HUMAN_GATE"
    return "RED_FLAG"
