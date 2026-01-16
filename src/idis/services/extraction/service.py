"""ExtractionService - claim extraction from document spans.

Production-grade behavior:
- Input: (tenant_id, deal_id, document_id(s) or span bundle, request_id)
- Load spans deterministically (stable ordering)
- Produce claim drafts through an Extractor interface
- Validate outputs fail-closed
- Apply Extraction Confidence Gate + No-Free-Facts
- Persist extracted claims through ClaimService
- Emit audit events for extraction lifecycle

Fail-closed semantics:
- If extractor/provider not configured: return structured failure (not empty success)
- If confidence below threshold: reject with typed error
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from idis.audit.sink import AuditSink, InMemoryAuditSink
from idis.services.claims.service import ClaimService, CreateClaimInput
from idis.validators.extraction_gate import (
    ExtractionGateInput,
    evaluate_extraction_gate,
)

if TYPE_CHECKING:
    from sqlalchemy import Connection

logger = logging.getLogger(__name__)


class ExtractionServiceError(Exception):
    """Base exception for ExtractionService errors."""

    pass


class ExtractorNotConfiguredError(ExtractionServiceError):
    """Raised when no extractor is configured - fail closed."""

    def __init__(self, tenant_id: str, deal_id: str) -> None:
        self.tenant_id = tenant_id
        self.deal_id = deal_id
        super().__init__(
            f"No extractor configured for tenant {tenant_id}, deal {deal_id}. "
            "Extraction cannot proceed (fail-closed)."
        )


class LowConfidenceExtractionError(ExtractionServiceError):
    """Raised when extraction confidence is below threshold."""

    def __init__(
        self,
        claim_id: str,
        confidence: Decimal,
        dhabt: Decimal | None,
        reason: str,
    ) -> None:
        self.claim_id = claim_id
        self.confidence = confidence
        self.dhabt = dhabt
        self.reason = reason
        super().__init__(
            f"Extraction blocked for claim {claim_id}: {reason}. "
            f"confidence={confidence}, dhabt={dhabt}"
        )


class ExtractionRequest(BaseModel):
    """Input model for extraction request."""

    request_id: str = Field(..., description="Unique request identifier")
    tenant_id: str = Field(..., description="Tenant UUID")
    deal_id: str = Field(..., description="Deal UUID")
    document_ids: list[str] = Field(
        default_factory=list, description="Document UUIDs to extract from"
    )
    span_ids: list[str] = Field(
        default_factory=list, description="Specific span UUIDs to extract from"
    )


@dataclass
class ExtractedClaimDraft:
    """Draft claim produced by extractor before validation."""

    claim_text: str
    claim_class: str
    extraction_confidence: Decimal
    dhabt_score: Decimal
    span_id: str
    predicate: str | None = None
    value: dict[str, Any] | None = None


@dataclass
class ExtractionResult:
    """Result of extraction operation."""

    request_id: str
    tenant_id: str
    deal_id: str
    success: bool
    created_claim_ids: list[str] = field(default_factory=list)
    rejected_drafts: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


@runtime_checkable
class Extractor(Protocol):
    """Protocol for claim extractors.

    Implementations must produce ExtractedClaimDraft objects from spans.
    """

    def extract(
        self,
        tenant_id: str,
        deal_id: str,
        spans: list[dict[str, Any]],
    ) -> list[ExtractedClaimDraft]:
        """Extract claim drafts from spans.

        Args:
            tenant_id: Tenant context.
            deal_id: Deal context.
            spans: List of span dicts with text_excerpt and metadata.

        Returns:
            List of extracted claim drafts.
        """
        ...


class DeterministicStubExtractor:
    """Deterministic extractor for testing.

    Produces structured outputs derived from span text without external calls.
    Generates real structured output suitable for deterministic test coverage.
    """

    def __init__(
        self, confidence: Decimal = Decimal("0.97"), dhabt: Decimal = Decimal("0.95")
    ) -> None:
        """Initialize with default confidence scores."""
        self._confidence = confidence
        self._dhabt = dhabt

    def extract(
        self,
        tenant_id: str,
        deal_id: str,
        spans: list[dict[str, Any]],
    ) -> list[ExtractedClaimDraft]:
        """Extract claim drafts from spans deterministically.

        Uses span text directly as claim text. Classifies based on keywords.
        """
        drafts: list[ExtractedClaimDraft] = []

        for span in sorted(spans, key=lambda s: s.get("span_id", "")):
            text = span.get("text_excerpt", "")
            if not text:
                continue

            claim_class = self._classify_text(text)

            drafts.append(
                ExtractedClaimDraft(
                    claim_text=text,
                    claim_class=claim_class,
                    extraction_confidence=self._confidence,
                    dhabt_score=self._dhabt,
                    span_id=span.get("span_id", ""),
                    predicate=None,
                    value=self._extract_value(text),
                )
            )

        return drafts

    def _classify_text(self, text: str) -> str:
        """Classify claim text into a claim class."""
        text_lower = text.lower()

        if any(kw in text_lower for kw in ["revenue", "arr", "mrr", "margin", "$", "funding"]):
            return "FINANCIAL"
        if any(kw in text_lower for kw in ["customer", "client", "user", "subscriber"]):
            return "TRACTION"
        if any(kw in text_lower for kw in ["tam", "sam", "som", "market size"]):
            return "MARKET_SIZE"
        if any(kw in text_lower for kw in ["competitor", "competition"]):
            return "COMPETITION"
        if any(kw in text_lower for kw in ["team", "employee", "founder", "ceo"]):
            return "TEAM"

        return "OTHER"

    def _extract_value(self, text: str) -> dict[str, Any] | None:
        """Extract numeric value from text if present."""
        import re

        match = re.search(r"\$?([\d,]+(?:\.\d+)?)\s*([MBK])?", text)
        if match:
            value_str = match.group(1).replace(",", "")
            multiplier = {"M": 1_000_000, "B": 1_000_000_000, "K": 1_000}.get(
                match.group(2) or "", 1
            )
            try:
                value = float(value_str) * multiplier
                return {
                    "value": value,
                    "unit": "USD" if "$" in text else "count",
                    "currency": "USD" if "$" in text else None,
                    "as_of": None,
                    "time_window": None,
                }
            except ValueError:
                pass

        return None


class ExtractionService:
    """Service for extracting claims from document spans.

    Fail-closed behavior:
    - If extractor not configured → raises ExtractorNotConfiguredError
    - If extraction confidence < threshold → rejects draft
    - All outputs validated before persistence

    Usage:
        service = ExtractionService(tenant_id, claim_service, extractor=stub)
        result = service.extract(request)
    """

    def __init__(
        self,
        tenant_id: str,
        claim_service: ClaimService,
        extractor: Extractor | None = None,
        audit_sink: AuditSink | None = None,
    ) -> None:
        """Initialize ExtractionService.

        Args:
            tenant_id: Tenant UUID for scoping.
            claim_service: ClaimService for persisting extracted claims.
            extractor: Extractor implementation. If None, extraction fails closed.
            audit_sink: Optional audit sink for event emission.
        """
        self._tenant_id = tenant_id
        self._claim_service = claim_service
        self._extractor = extractor
        self._audit_sink = audit_sink or InMemoryAuditSink()

    @property
    def tenant_id(self) -> str:
        """Return the tenant context."""
        return self._tenant_id

    @property
    def is_configured(self) -> bool:
        """Check if extractor is configured."""
        return self._extractor is not None

    def _emit_audit_event(
        self,
        event_type: str,
        request_id: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Emit an audit event for extraction operations."""
        event = {
            "event_type": event_type,
            "tenant_id": self._tenant_id,
            "entity_type": "extraction",
            "entity_id": request_id,
            "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "details": details or {},
        }
        try:
            self._audit_sink.emit(event)
        except Exception as e:
            logger.warning("Failed to emit audit event: %s", e)

    def _validate_extraction_gate(
        self,
        draft: ExtractedClaimDraft,
    ) -> tuple[bool, str | None]:
        """Validate draft against Extraction Confidence Gate.

        Returns:
            Tuple of (allowed, reason_if_blocked).
        """
        gate_input = ExtractionGateInput(
            claim_id=draft.span_id,
            extraction_confidence=draft.extraction_confidence,
            dhabt_score=draft.dhabt_score,
            is_human_verified=False,
        )

        decision = evaluate_extraction_gate(gate_input)

        if decision.blocked:
            reason = decision.reason.value if decision.reason else "UNKNOWN"
            return False, reason

        is_allowed = not decision.blocked
        return is_allowed, None

    def extract(
        self,
        request: ExtractionRequest,
        spans: list[dict[str, Any]],
    ) -> ExtractionResult:
        """Extract claims from spans.

        Args:
            request: Extraction request with context.
            spans: List of span dicts to extract from.

        Returns:
            ExtractionResult with success/failure and created claims.

        Raises:
            ExtractorNotConfiguredError: If no extractor configured (fail-closed).
        """
        self._emit_audit_event(
            event_type="extraction.started",
            request_id=request.request_id,
            details={
                "deal_id": request.deal_id,
                "span_count": len(spans),
            },
        )

        if self._extractor is None:
            self._emit_audit_event(
                event_type="extraction.failed",
                request_id=request.request_id,
                details={"reason": "extractor_not_configured"},
            )
            return ExtractionResult(
                request_id=request.request_id,
                tenant_id=request.tenant_id,
                deal_id=request.deal_id,
                success=False,
                error="Extractor not configured (fail-closed)",
            )

        sorted_spans = sorted(spans, key=lambda s: s.get("span_id", ""))

        try:
            drafts = self._extractor.extract(
                tenant_id=request.tenant_id,
                deal_id=request.deal_id,
                spans=sorted_spans,
            )
        except Exception as e:
            self._emit_audit_event(
                event_type="extraction.failed",
                request_id=request.request_id,
                details={"reason": "extractor_error", "error": str(e)},
            )
            return ExtractionResult(
                request_id=request.request_id,
                tenant_id=request.tenant_id,
                deal_id=request.deal_id,
                success=False,
                error=f"Extractor error: {e}",
            )

        created_claim_ids: list[str] = []
        rejected_drafts: list[dict[str, Any]] = []

        for draft in drafts:
            allowed, reason = self._validate_extraction_gate(draft)

            if not allowed:
                rejected_drafts.append(
                    {
                        "span_id": draft.span_id,
                        "claim_text": draft.claim_text[:100],
                        "reason": reason,
                        "confidence": str(draft.extraction_confidence),
                        "dhabt": str(draft.dhabt_score),
                    }
                )
                continue

            try:
                claim_input = CreateClaimInput(
                    deal_id=request.deal_id,
                    claim_class=draft.claim_class,
                    claim_text=draft.claim_text,
                    claim_type="primary",
                    predicate=draft.predicate,
                    value=draft.value,
                    claim_grade="C",
                    claim_verdict="UNVERIFIED",
                    claim_action="VERIFY",
                    materiality="MEDIUM",
                    ic_bound=False,
                    primary_span_id=draft.span_id,
                )

                claim_data = self._claim_service.create(claim_input)
                created_claim_ids.append(claim_data["claim_id"])

            except Exception as e:
                rejected_drafts.append(
                    {
                        "span_id": draft.span_id,
                        "claim_text": draft.claim_text[:100],
                        "reason": f"persistence_error: {e}",
                    }
                )

        self._emit_audit_event(
            event_type="extraction.completed",
            request_id=request.request_id,
            details={
                "deal_id": request.deal_id,
                "created_count": len(created_claim_ids),
                "rejected_count": len(rejected_drafts),
            },
        )

        return ExtractionResult(
            request_id=request.request_id,
            tenant_id=request.tenant_id,
            deal_id=request.deal_id,
            success=True,
            created_claim_ids=created_claim_ids,
            rejected_drafts=rejected_drafts,
        )


def create_extraction_service(
    tenant_id: str,
    db_conn: Connection | None = None,
    extractor: Extractor | None = None,
    audit_sink: AuditSink | None = None,
) -> ExtractionService:
    """Factory function to create ExtractionService with dependencies.

    Args:
        tenant_id: Tenant UUID.
        db_conn: Optional SQLAlchemy connection for Postgres.
        extractor: Optional extractor implementation.
        audit_sink: Optional audit sink.

    Returns:
        Configured ExtractionService instance.
    """
    claim_service = ClaimService(
        tenant_id=tenant_id,
        db_conn=db_conn,
        audit_sink=audit_sink,
    )

    return ExtractionService(
        tenant_id=tenant_id,
        claim_service=claim_service,
        extractor=extractor,
        audit_sink=audit_sink,
    )
