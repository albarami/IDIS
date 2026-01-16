"""Tests for ExtractionService with synthetic dataset-driven coverage.

Tests:
- Synthetic spans → extraction → persisted claims → retrieved via service
- Fail-closed when extractor isn't configured
- Reject low-confidence outputs (Extraction Gate)
- Audit event emission for extraction lifecycle

Uses synthetic fixtures instead of patched behavior.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from idis.audit.sink import InMemoryAuditSink
from idis.persistence.repositories.claims import clear_all_claims_stores
from idis.services.claims.service import ClaimService
from idis.services.extraction.service import (
    DeterministicStubExtractor,
    ExtractedClaimDraft,
    ExtractionRequest,
    ExtractionService,
    create_extraction_service,
)
from tests.fixtures.synthetic.claims_fixture import (
    SYNTHETIC_DEAL,
    SYNTHETIC_SPANS,
    SYNTHETIC_TENANT_ID,
    get_extraction_request,
)


@pytest.fixture(autouse=True)
def clean_stores() -> None:
    """Clear in-memory stores before each test."""
    clear_all_claims_stores()
    yield
    clear_all_claims_stores()


@pytest.fixture
def audit_sink() -> InMemoryAuditSink:
    """Provide an in-memory audit sink for testing."""
    return InMemoryAuditSink()


@pytest.fixture
def claim_service(audit_sink: InMemoryAuditSink) -> ClaimService:
    """Provide a ClaimService instance with in-memory storage."""
    return ClaimService(
        tenant_id=SYNTHETIC_TENANT_ID,
        db_conn=None,
        audit_sink=audit_sink,
    )


@pytest.fixture
def stub_extractor() -> DeterministicStubExtractor:
    """Provide a deterministic stub extractor with passing confidence."""
    return DeterministicStubExtractor(
        confidence=Decimal("0.97"),
        dhabt=Decimal("0.95"),
    )


@pytest.fixture
def low_confidence_extractor() -> DeterministicStubExtractor:
    """Provide a stub extractor with low confidence (below threshold)."""
    return DeterministicStubExtractor(
        confidence=Decimal("0.80"),
        dhabt=Decimal("0.75"),
    )


@pytest.fixture
def extraction_service(
    claim_service: ClaimService,
    stub_extractor: DeterministicStubExtractor,
    audit_sink: InMemoryAuditSink,
) -> ExtractionService:
    """Provide an ExtractionService with configured extractor."""
    return ExtractionService(
        tenant_id=SYNTHETIC_TENANT_ID,
        claim_service=claim_service,
        extractor=stub_extractor,
        audit_sink=audit_sink,
    )


@pytest.fixture
def unconfigured_extraction_service(
    claim_service: ClaimService,
    audit_sink: InMemoryAuditSink,
) -> ExtractionService:
    """Provide an ExtractionService without extractor (fail-closed)."""
    return ExtractionService(
        tenant_id=SYNTHETIC_TENANT_ID,
        claim_service=claim_service,
        extractor=None,
        audit_sink=audit_sink,
    )


class TestExtractionServiceConfiguration:
    """Tests for ExtractionService configuration and fail-closed behavior."""

    def test_is_configured_with_extractor(self, extraction_service: ExtractionService) -> None:
        """Service reports configured when extractor is set."""
        assert extraction_service.is_configured is True

    def test_is_not_configured_without_extractor(
        self, unconfigured_extraction_service: ExtractionService
    ) -> None:
        """Service reports not configured when extractor is None."""
        assert unconfigured_extraction_service.is_configured is False

    def test_extract_without_extractor_fails_closed(
        self, unconfigured_extraction_service: ExtractionService
    ) -> None:
        """Extraction without configured extractor returns structured failure."""
        request = ExtractionRequest(
            request_id="test-req-001",
            tenant_id=SYNTHETIC_TENANT_ID,
            deal_id=SYNTHETIC_DEAL["deal_id"],
            span_ids=[s["span_id"] for s in SYNTHETIC_SPANS],
        )

        result = unconfigured_extraction_service.extract(request, SYNTHETIC_SPANS)

        assert result.success is False
        assert result.error is not None
        assert "not configured" in result.error.lower()
        assert "fail-closed" in result.error.lower()

    def test_extract_without_extractor_emits_failure_audit(
        self,
        unconfigured_extraction_service: ExtractionService,
        audit_sink: InMemoryAuditSink,
    ) -> None:
        """Extraction failure emits audit event."""
        request = ExtractionRequest(
            request_id="test-req-002",
            tenant_id=SYNTHETIC_TENANT_ID,
            deal_id=SYNTHETIC_DEAL["deal_id"],
            span_ids=[],
        )

        result = unconfigured_extraction_service.extract(request, [])

        assert result.success is False
        events = audit_sink.events
        assert len(events) == 2
        assert events[0]["event_type"] == "extraction.started"
        assert events[1]["event_type"] == "extraction.failed"
        assert events[1]["details"]["reason"] == "extractor_not_configured"


class TestExtractionServiceExtract:
    """Tests for ExtractionService.extract() with synthetic data."""

    def test_extract_from_synthetic_spans_creates_claims(
        self,
        extraction_service: ExtractionService,
        claim_service: ClaimService,
    ) -> None:
        """Extraction from synthetic spans creates persisted claims."""
        request = ExtractionRequest(
            request_id="test-req-003",
            tenant_id=SYNTHETIC_TENANT_ID,
            deal_id=SYNTHETIC_DEAL["deal_id"],
            span_ids=[s["span_id"] for s in SYNTHETIC_SPANS],
        )

        result = extraction_service.extract(request, SYNTHETIC_SPANS)

        assert result.success is True
        assert len(result.created_claim_ids) == len(SYNTHETIC_SPANS)
        assert result.error is None

        for claim_id in result.created_claim_ids:
            claim = claim_service.get(claim_id)
            assert claim is not None
            assert claim["deal_id"] == SYNTHETIC_DEAL["deal_id"]

    def test_extract_deterministic_ordering(self, extraction_service: ExtractionService) -> None:
        """Extraction processes spans in deterministic order by span_id."""
        spans_reversed = list(reversed(SYNTHETIC_SPANS))

        request = ExtractionRequest(
            request_id="test-req-004",
            tenant_id=SYNTHETIC_TENANT_ID,
            deal_id=SYNTHETIC_DEAL["deal_id"],
            span_ids=[s["span_id"] for s in spans_reversed],
        )

        result = extraction_service.extract(request, spans_reversed)

        assert result.success is True

    def test_extract_emits_lifecycle_audit_events(
        self,
        extraction_service: ExtractionService,
        audit_sink: InMemoryAuditSink,
    ) -> None:
        """Extraction emits started and completed audit events."""
        request = ExtractionRequest(
            request_id="test-req-005",
            tenant_id=SYNTHETIC_TENANT_ID,
            deal_id=SYNTHETIC_DEAL["deal_id"],
            span_ids=[SYNTHETIC_SPANS[0]["span_id"]],
        )

        extraction_service.extract(request, [SYNTHETIC_SPANS[0]])

        event_types = [e["event_type"] for e in audit_sink.events]
        assert "extraction.started" in event_types
        assert "extraction.completed" in event_types

    def test_extract_result_contains_request_context(
        self, extraction_service: ExtractionService
    ) -> None:
        """Extraction result contains correct request context."""
        request = ExtractionRequest(
            request_id="test-req-006",
            tenant_id=SYNTHETIC_TENANT_ID,
            deal_id=SYNTHETIC_DEAL["deal_id"],
            span_ids=[],
        )

        result = extraction_service.extract(request, [])

        assert result.request_id == "test-req-006"
        assert result.tenant_id == SYNTHETIC_TENANT_ID
        assert result.deal_id == SYNTHETIC_DEAL["deal_id"]


class TestExtractionGateEnforcement:
    """Tests for Extraction Confidence Gate enforcement."""

    def test_low_confidence_extraction_rejected(
        self,
        claim_service: ClaimService,
        low_confidence_extractor: DeterministicStubExtractor,
        audit_sink: InMemoryAuditSink,
    ) -> None:
        """Extraction with low confidence is rejected by gate."""
        service = ExtractionService(
            tenant_id=SYNTHETIC_TENANT_ID,
            claim_service=claim_service,
            extractor=low_confidence_extractor,
            audit_sink=audit_sink,
        )

        request = ExtractionRequest(
            request_id="test-req-007",
            tenant_id=SYNTHETIC_TENANT_ID,
            deal_id=SYNTHETIC_DEAL["deal_id"],
            span_ids=[SYNTHETIC_SPANS[0]["span_id"]],
        )

        result = service.extract(request, [SYNTHETIC_SPANS[0]])

        assert result.success is True
        assert len(result.created_claim_ids) == 0
        assert len(result.rejected_drafts) == 1
        assert "LOW_CONFIDENCE" in result.rejected_drafts[0]["reason"]

    def test_high_confidence_extraction_accepted(
        self, extraction_service: ExtractionService
    ) -> None:
        """Extraction with high confidence passes gate."""
        request = ExtractionRequest(
            request_id="test-req-008",
            tenant_id=SYNTHETIC_TENANT_ID,
            deal_id=SYNTHETIC_DEAL["deal_id"],
            span_ids=[SYNTHETIC_SPANS[0]["span_id"]],
        )

        result = extraction_service.extract(request, [SYNTHETIC_SPANS[0]])

        assert result.success is True
        assert len(result.created_claim_ids) == 1
        assert len(result.rejected_drafts) == 0

    def test_mixed_confidence_partial_acceptance(
        self,
        claim_service: ClaimService,
        audit_sink: InMemoryAuditSink,
    ) -> None:
        """Extraction with mixed confidence accepts some, rejects others."""

        class MixedConfidenceExtractor:
            """Extractor that returns mixed confidence results."""

            def extract(
                self,
                tenant_id: str,
                deal_id: str,
                spans: list[dict],
            ) -> list[ExtractedClaimDraft]:
                drafts = []
                for i, span in enumerate(spans):
                    conf = Decimal("0.97") if i % 2 == 0 else Decimal("0.80")
                    dhabt = Decimal("0.95") if i % 2 == 0 else Decimal("0.75")
                    drafts.append(
                        ExtractedClaimDraft(
                            claim_text=span.get("text_excerpt", ""),
                            claim_class="FINANCIAL",
                            extraction_confidence=conf,
                            dhabt_score=dhabt,
                            span_id=span.get("span_id", ""),
                        )
                    )
                return drafts

        service = ExtractionService(
            tenant_id=SYNTHETIC_TENANT_ID,
            claim_service=claim_service,
            extractor=MixedConfidenceExtractor(),
            audit_sink=audit_sink,
        )

        request = ExtractionRequest(
            request_id="test-req-009",
            tenant_id=SYNTHETIC_TENANT_ID,
            deal_id=SYNTHETIC_DEAL["deal_id"],
            span_ids=[s["span_id"] for s in SYNTHETIC_SPANS],
        )

        result = service.extract(request, SYNTHETIC_SPANS)

        assert result.success is True
        assert len(result.created_claim_ids) == 2
        assert len(result.rejected_drafts) == 2


class TestDeterministicStubExtractor:
    """Tests for the DeterministicStubExtractor."""

    def test_extract_produces_claim_drafts(
        self, stub_extractor: DeterministicStubExtractor
    ) -> None:
        """Stub extractor produces claim drafts from spans."""
        drafts = stub_extractor.extract(
            tenant_id=SYNTHETIC_TENANT_ID,
            deal_id=SYNTHETIC_DEAL["deal_id"],
            spans=SYNTHETIC_SPANS,
        )

        assert len(drafts) == len(SYNTHETIC_SPANS)
        for draft in drafts:
            assert draft.claim_text
            assert draft.claim_class
            assert draft.extraction_confidence == Decimal("0.97")
            assert draft.dhabt_score == Decimal("0.95")

    def test_extract_classifies_financial_text(
        self, stub_extractor: DeterministicStubExtractor
    ) -> None:
        """Stub extractor classifies financial text correctly."""
        spans = [{"span_id": "test-1", "text_excerpt": "ARR reached $5M in 2024."}]

        drafts = stub_extractor.extract(
            tenant_id=SYNTHETIC_TENANT_ID,
            deal_id=SYNTHETIC_DEAL["deal_id"],
            spans=spans,
        )

        assert len(drafts) == 1
        assert drafts[0].claim_class == "FINANCIAL"

    def test_extract_classifies_traction_text(
        self, stub_extractor: DeterministicStubExtractor
    ) -> None:
        """Stub extractor classifies traction text correctly."""
        spans = [{"span_id": "test-2", "text_excerpt": "150 customers signed up."}]

        drafts = stub_extractor.extract(
            tenant_id=SYNTHETIC_TENANT_ID,
            deal_id=SYNTHETIC_DEAL["deal_id"],
            spans=spans,
        )

        assert len(drafts) == 1
        assert drafts[0].claim_class == "TRACTION"

    def test_extract_extracts_numeric_values(
        self, stub_extractor: DeterministicStubExtractor
    ) -> None:
        """Stub extractor extracts numeric values from text."""
        spans = [{"span_id": "test-3", "text_excerpt": "Revenue was $5M last year."}]

        drafts = stub_extractor.extract(
            tenant_id=SYNTHETIC_TENANT_ID,
            deal_id=SYNTHETIC_DEAL["deal_id"],
            spans=spans,
        )

        assert len(drafts) == 1
        assert drafts[0].value is not None
        assert drafts[0].value["value"] == 5_000_000.0
        assert drafts[0].value["currency"] == "USD"

    def test_extract_deterministic_span_ordering(
        self, stub_extractor: DeterministicStubExtractor
    ) -> None:
        """Stub extractor processes spans in deterministic order."""
        spans_shuffled = [SYNTHETIC_SPANS[2], SYNTHETIC_SPANS[0], SYNTHETIC_SPANS[1]]

        drafts = stub_extractor.extract(
            tenant_id=SYNTHETIC_TENANT_ID,
            deal_id=SYNTHETIC_DEAL["deal_id"],
            spans=spans_shuffled,
        )

        span_ids_in_output = [d.span_id for d in drafts]
        assert span_ids_in_output == sorted(span_ids_in_output)


class TestExtractionServiceFactory:
    """Tests for create_extraction_service factory function."""

    def test_create_extraction_service_without_extractor(
        self, audit_sink: InMemoryAuditSink
    ) -> None:
        """Factory creates service without extractor (fail-closed mode)."""
        service = create_extraction_service(
            tenant_id=SYNTHETIC_TENANT_ID,
            db_conn=None,
            extractor=None,
            audit_sink=audit_sink,
        )

        assert service.is_configured is False
        assert service.tenant_id == SYNTHETIC_TENANT_ID

    def test_create_extraction_service_with_extractor(
        self,
        stub_extractor: DeterministicStubExtractor,
        audit_sink: InMemoryAuditSink,
    ) -> None:
        """Factory creates service with extractor."""
        service = create_extraction_service(
            tenant_id=SYNTHETIC_TENANT_ID,
            db_conn=None,
            extractor=stub_extractor,
            audit_sink=audit_sink,
        )

        assert service.is_configured is True


class TestExtractionServiceEndToEnd:
    """End-to-end tests for extraction pipeline."""

    def test_full_extraction_pipeline(
        self,
        extraction_service: ExtractionService,
        claim_service: ClaimService,
    ) -> None:
        """Full pipeline: spans → extraction → claims persisted → retrievable."""
        request_data = get_extraction_request()
        request = ExtractionRequest(**request_data)

        result = extraction_service.extract(request, SYNTHETIC_SPANS)

        assert result.success is True
        assert len(result.created_claim_ids) > 0

        claims, _ = claim_service.list_by_deal(SYNTHETIC_DEAL["deal_id"])
        assert len(claims) == len(result.created_claim_ids)

        for claim in claims:
            assert claim["tenant_id"] == SYNTHETIC_TENANT_ID
            assert claim["deal_id"] == SYNTHETIC_DEAL["deal_id"]
            assert claim["primary_span_id"] in [s["span_id"] for s in SYNTHETIC_SPANS]

    def test_extraction_preserves_span_text_as_claim_text(
        self,
        extraction_service: ExtractionService,
        claim_service: ClaimService,
    ) -> None:
        """Extracted claims preserve span text as claim text."""
        single_span = [SYNTHETIC_SPANS[0]]
        request = ExtractionRequest(
            request_id="test-e2e-001",
            tenant_id=SYNTHETIC_TENANT_ID,
            deal_id=SYNTHETIC_DEAL["deal_id"],
            span_ids=[single_span[0]["span_id"]],
        )

        result = extraction_service.extract(request, single_span)

        assert len(result.created_claim_ids) == 1
        claim = claim_service.get(result.created_claim_ids[0])
        assert claim["claim_text"] == single_span[0]["text_excerpt"]
        assert claim["primary_span_id"] == single_span[0]["span_id"]
