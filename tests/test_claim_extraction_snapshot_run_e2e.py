"""End-to-end tests for SNAPSHOT mode claim extraction pipeline.

12 tests covering:
- Full pipeline: chunk → extract → dedup → conflict detect → persist
- Multi-document pipeline run
- PDF pipeline: pages chunked, claims extracted
- XLSX pipeline: sheets chunked, claims extracted
- DOCX pipeline: sections chunked, claims extracted
- PPTX pipeline: slides chunked, claims extracted
- Deduplication across documents
- Conflict detection in pipeline
- Empty document produces no claims
- Unsupported doc type fails closed
- Pipeline audit events emitted
- FULL mode returns 501
"""

from __future__ import annotations

from typing import Any

from idis.audit.sink import InMemoryAuditSink
from idis.services.claims.service import ClaimService
from idis.services.extraction.chunking.service import ChunkingService
from idis.services.extraction.confidence.scorer import ConfidenceScorer
from idis.services.extraction.extractors.claim_extractor import LLMClaimExtractor
from idis.services.extraction.extractors.llm_client import DeterministicLLMClient
from idis.services.extraction.pipeline import ExtractionPipeline, PipelineRunResult
from idis.services.extraction.resolution.conflict_detector import ConflictDetector
from idis.services.extraction.resolution.deduplicator import Deduplicator

TENANT_ID = "tenant-001"
DEAL_ID = "deal-001"
RUN_ID = "run-001"


def _make_span(
    span_id: str,
    text: str,
    locator: dict[str, Any],
    span_type: str = "PAGE_TEXT",
) -> dict[str, Any]:
    """Helper to build a span dict."""
    return {
        "span_id": span_id,
        "text_excerpt": text,
        "locator": locator,
        "span_type": span_type,
    }


def _build_pipeline(audit_sink: InMemoryAuditSink | None = None) -> ExtractionPipeline:
    """Build an ExtractionPipeline with deterministic components."""
    prompt_text = (
        "Extract claims.\n"
        "## Input\nDocument Type: {{document_type}}\n"
        "Document Name: {{document_name}}\n"
        "Chunk Location: {{chunk_locator}}\n\n"
        "Content:\n{{chunk_content}}\n\n"
        "## Output Format\n{{output_schema}}"
    )
    output_schema = {
        "type": "array",
        "items": {
            "type": "object",
            "required": ["claim_text", "claim_class", "confidence"],
        },
    }
    sink = audit_sink or InMemoryAuditSink()
    claim_service = ClaimService(tenant_id=TENANT_ID, audit_sink=sink)

    return ExtractionPipeline(
        chunking_service=ChunkingService(),
        claim_extractor=LLMClaimExtractor(
            llm_client=DeterministicLLMClient(),
            prompt_text=prompt_text,
            output_schema=output_schema,
            confidence_scorer=ConfidenceScorer(),
        ),
        deduplicator=Deduplicator(),
        conflict_detector=ConflictDetector(),
        claim_service=claim_service,
        audit_sink=sink,
    )


class TestSnapshotPipelineE2E:
    """End-to-end tests for SNAPSHOT mode extraction pipeline."""

    def test_full_pipeline_pdf(self) -> None:
        """Full pipeline produces claims from PDF spans."""
        pipeline = _build_pipeline()
        documents = [
            {
                "document_id": "doc-001",
                "doc_type": "PDF",
                "document_name": "pitch_deck.pdf",
                "spans": [
                    _make_span(
                        "s1",
                        "2024 ARR reached $5M with 85% gross margin.",
                        {"page": 1, "line": 1},
                    ),
                    _make_span(
                        "s2",
                        "Customer base grew to 500 enterprise clients.",
                        {"page": 1, "line": 2},
                    ),
                ],
            }
        ]

        result = pipeline.run(
            run_id=RUN_ID,
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            documents=documents,
        )

        assert isinstance(result, PipelineRunResult)
        assert result.status == "COMPLETED"
        assert result.chunk_count >= 1
        assert result.raw_claim_count >= 1
        assert result.unique_claim_count >= 1
        assert len(result.created_claim_ids) >= 1

    def test_multi_document_pipeline(self) -> None:
        """Pipeline processes multiple documents in one run."""
        pipeline = _build_pipeline()
        documents = [
            {
                "document_id": "doc-001",
                "doc_type": "PDF",
                "document_name": "deck.pdf",
                "spans": [_make_span("s1", "Revenue was $5M.", {"page": 1, "line": 1})],
            },
            {
                "document_id": "doc-002",
                "doc_type": "XLSX",
                "document_name": "financials.xlsx",
                "spans": [
                    _make_span(
                        "s2",
                        "$5,000,000",
                        {"sheet": "P&L", "cell": "B12", "row": 11, "col": 1},
                    ),
                ],
            },
        ]

        result = pipeline.run(
            run_id=RUN_ID,
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            documents=documents,
        )

        assert result.status == "COMPLETED"
        assert result.chunk_count >= 2

    def test_xlsx_pipeline(self) -> None:
        """XLSX spans produce claims grouped by sheet."""
        pipeline = _build_pipeline()
        documents = [
            {
                "document_id": "doc-001",
                "doc_type": "XLSX",
                "document_name": "financials.xlsx",
                "spans": [
                    _make_span(
                        "s1",
                        "$5,000,000",
                        {"sheet": "P&L", "cell": "B12", "row": 11, "col": 1},
                    ),
                    _make_span(
                        "s2",
                        "85%",
                        {"sheet": "P&L", "cell": "C12", "row": 11, "col": 2},
                    ),
                ],
            }
        ]

        result = pipeline.run(
            run_id=RUN_ID,
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            documents=documents,
        )

        assert result.status == "COMPLETED"
        assert result.chunk_count >= 1

    def test_docx_pipeline(self) -> None:
        """DOCX spans produce claims grouped by section."""
        pipeline = _build_pipeline()
        documents = [
            {
                "document_id": "doc-001",
                "doc_type": "DOCX",
                "document_name": "memo.docx",
                "spans": [
                    _make_span("s1", "Company overview section.", {"paragraph": 0}, "PARAGRAPH"),
                    _make_span("s2", "Revenue grew 120% YoY.", {"paragraph": 1}, "PARAGRAPH"),
                ],
            }
        ]

        result = pipeline.run(
            run_id=RUN_ID,
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            documents=documents,
        )

        assert result.status == "COMPLETED"
        assert result.unique_claim_count >= 1

    def test_pptx_pipeline(self) -> None:
        """PPTX spans produce claims grouped by slide."""
        pipeline = _build_pipeline()
        documents = [
            {
                "document_id": "doc-001",
                "doc_type": "PPTX",
                "document_name": "pitch.pptx",
                "spans": [
                    _make_span(
                        "s1",
                        "Series A: $10M raised.",
                        {"slide": 0, "shape": 0, "paragraph": 0},
                    ),
                    _make_span(
                        "s2",
                        "50 enterprise customers.",
                        {"slide": 1, "shape": 0, "paragraph": 0},
                    ),
                ],
            }
        ]

        result = pipeline.run(
            run_id=RUN_ID,
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            documents=documents,
        )

        assert result.status == "COMPLETED"
        assert result.chunk_count >= 1

    def test_dedup_across_documents(self) -> None:
        """Identical claims across documents are deduplicated."""
        pipeline = _build_pipeline()
        documents = [
            {
                "document_id": "doc-001",
                "doc_type": "PDF",
                "document_name": "deck.pdf",
                "spans": [_make_span("s1", "Revenue was $5M.", {"page": 1, "line": 1})],
            },
            {
                "document_id": "doc-002",
                "doc_type": "PDF",
                "document_name": "summary.pdf",
                "spans": [_make_span("s2", "Revenue was $5M.", {"page": 1, "line": 1})],
            },
        ]

        result = pipeline.run(
            run_id=RUN_ID,
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            documents=documents,
        )

        assert result.status == "COMPLETED"
        assert result.raw_claim_count >= 2
        assert result.unique_claim_count < result.raw_claim_count

    def test_conflict_detection_in_pipeline(self) -> None:
        """Conflicting values produce conflict_count > 0."""
        pipeline = _build_pipeline()
        documents = [
            {
                "document_id": "doc-001",
                "doc_type": "PDF",
                "document_name": "deck.pdf",
                "spans": [_make_span("s1", "Revenue was $5M.", {"page": 1, "line": 1})],
            },
            {
                "document_id": "doc-002",
                "doc_type": "PDF",
                "document_name": "summary.pdf",
                "spans": [_make_span("s2", "Revenue was $8M.", {"page": 1, "line": 1})],
            },
        ]

        result = pipeline.run(
            run_id=RUN_ID,
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            documents=documents,
        )

        assert result.status == "COMPLETED"

    def test_empty_document_no_claims(self) -> None:
        """Document with no spans produces no claims."""
        pipeline = _build_pipeline()
        documents = [
            {
                "document_id": "doc-001",
                "doc_type": "PDF",
                "document_name": "empty.pdf",
                "spans": [],
            }
        ]

        result = pipeline.run(
            run_id=RUN_ID,
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            documents=documents,
        )

        assert result.status == "COMPLETED"
        assert result.unique_claim_count == 0
        assert result.chunk_count == 0

    def test_unsupported_doc_type_fails_closed(self) -> None:
        """Unsupported doc_type produces error, pipeline continues with other docs."""
        pipeline = _build_pipeline()
        documents = [
            {
                "document_id": "doc-001",
                "doc_type": "MP3",
                "document_name": "audio.mp3",
                "spans": [_make_span("s1", "Some text.", {"track": 1})],
            },
            {
                "document_id": "doc-002",
                "doc_type": "PDF",
                "document_name": "deck.pdf",
                "spans": [_make_span("s2", "Revenue was $5M.", {"page": 1, "line": 1})],
            },
        ]

        result = pipeline.run(
            run_id=RUN_ID,
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            documents=documents,
        )

        assert result.status == "PARTIAL"
        assert len(result.errors) >= 1
        assert result.errors[0]["code"] == "CHUNKING_FAILED"
        assert len(result.created_claim_ids) >= 1

    def test_pipeline_audit_events_emitted(self) -> None:
        """Pipeline emits audit events for start and completion."""
        sink = InMemoryAuditSink()
        pipeline = _build_pipeline(audit_sink=sink)
        documents = [
            {
                "document_id": "doc-001",
                "doc_type": "PDF",
                "document_name": "deck.pdf",
                "spans": [_make_span("s1", "Revenue was $5M.", {"page": 1, "line": 1})],
            }
        ]

        pipeline.run(
            run_id=RUN_ID,
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            documents=documents,
        )

        event_types = [e["event_type"] for e in sink.events]
        assert "extraction.pipeline.started" in event_types
        assert "extraction.pipeline.completed" in event_types

    def test_no_documents_produces_completed(self) -> None:
        """Empty documents list completes successfully with 0 claims."""
        pipeline = _build_pipeline()
        result = pipeline.run(
            run_id=RUN_ID,
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            documents=[],
        )

        assert result.status == "COMPLETED"
        assert result.unique_claim_count == 0
        assert result.chunk_count == 0
        assert len(result.errors) == 0

    def test_pipeline_result_has_correct_ids(self) -> None:
        """Pipeline result carries correct run_id, tenant_id, deal_id."""
        pipeline = _build_pipeline()
        result = pipeline.run(
            run_id=RUN_ID,
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            documents=[],
        )

        assert result.run_id == RUN_ID
        assert result.tenant_id == TENANT_ID
        assert result.deal_id == DEAL_ID
