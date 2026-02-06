"""Tests for document-type-specific chunkers and ChunkingService.

12 tests covering:
- PDF chunking (page grouping, large page splitting, table separation)
- XLSX chunking (sheet grouping)
- DOCX chunking (section grouping)
- PPTX chunking (slide grouping)
- ChunkingService routing and fail-closed behavior
- Span ID preservation, deterministic ordering, empty/no-text handling
"""

from __future__ import annotations

import pytest

from idis.services.extraction.chunking.base import (
    UnsupportedDocumentTypeError,
)
from idis.services.extraction.chunking.docx_chunker import DocxChunker
from idis.services.extraction.chunking.pdf_chunker import PdfChunker
from idis.services.extraction.chunking.pptx_chunker import PptxChunker
from idis.services.extraction.chunking.service import ChunkingService
from idis.services.extraction.chunking.xlsx_chunker import XlsxChunker

DOC_ID = "doc00001-0000-0000-0000-000000000001"


def _make_span(
    span_id: str,
    text: str,
    locator: dict,
    span_type: str = "PAGE_TEXT",
) -> dict:
    """Helper to build a span dict for testing."""
    return {
        "span_id": span_id,
        "text_excerpt": text,
        "locator": locator,
        "span_type": span_type,
    }


class TestPdfChunker:
    """Tests for PdfChunker."""

    def test_pdf_chunker_groups_by_page(self) -> None:
        """Same-page spans become one chunk."""
        spans = [
            _make_span("s1", "Revenue was $5M.", {"page": 1, "line": 1}),
            _make_span("s2", "Margin was 85%.", {"page": 1, "line": 2}),
        ]
        chunker = PdfChunker()
        chunks = chunker.chunk(spans, document_id=DOC_ID)

        assert len(chunks) == 1
        assert chunks[0].doc_type == "PDF"
        assert "s1" in chunks[0].span_ids
        assert "s2" in chunks[0].span_ids

    def test_pdf_chunker_splits_large_page(self) -> None:
        """>500 tokens on one page produces multiple chunks."""
        long_text = " ".join(["word"] * 400)
        spans = [
            _make_span("s1", long_text, {"page": 1, "line": 1}),
            _make_span("s2", long_text, {"page": 1, "line": 2}),
        ]
        chunker = PdfChunker(max_tokens=500)
        chunks = chunker.chunk(spans, document_id=DOC_ID)

        assert len(chunks) >= 2
        all_span_ids = set()
        for c in chunks:
            all_span_ids.update(c.span_ids)
        assert "s1" in all_span_ids
        assert "s2" in all_span_ids

    def test_pdf_chunker_table_separate_chunk(self) -> None:
        """Table spans get their own chunk."""
        spans = [
            _make_span("s1", "Page text here.", {"page": 1, "line": 1}),
            _make_span("s2", "Table cell data.", {"page": 1, "table": 0, "row": 0, "col": 0}),
        ]
        chunker = PdfChunker()
        chunks = chunker.chunk(spans, document_id=DOC_ID)

        assert len(chunks) == 2
        page_chunk = [c for c in chunks if "s1" in c.span_ids][0]
        table_chunk = [c for c in chunks if "s2" in c.span_ids][0]
        assert page_chunk.chunk_id != table_chunk.chunk_id


class TestXlsxChunker:
    """Tests for XlsxChunker."""

    def test_xlsx_chunker_groups_by_sheet(self) -> None:
        """Same-sheet cells become one chunk."""
        spans = [
            _make_span("s1", "$5,000,000", {"sheet": "P&L", "cell": "B12", "row": 11, "col": 1}),
            _make_span("s2", "85%", {"sheet": "P&L", "cell": "C12", "row": 11, "col": 2}),
            _make_span("s3", "100", {"sheet": "Metrics", "cell": "A1", "row": 0, "col": 0}),
        ]
        chunker = XlsxChunker()
        chunks = chunker.chunk(spans, document_id=DOC_ID)

        assert len(chunks) == 2
        sheet_names = set()
        for c in chunks:
            if "s1" in c.span_ids:
                assert "s2" in c.span_ids
                sheet_names.add("P&L")
            if "s3" in c.span_ids:
                sheet_names.add("Metrics")
        assert sheet_names == {"P&L", "Metrics"}


class TestDocxChunker:
    """Tests for DocxChunker."""

    def test_docx_chunker_groups_by_section(self) -> None:
        """Heading + paragraphs grouped into section chunk."""
        spans = [
            _make_span("s1", "Introduction", {"paragraph": 0}, span_type="PARAGRAPH"),
            _make_span("s2", "We are a SaaS company.", {"paragraph": 1}, span_type="PARAGRAPH"),
            _make_span("s3", "Revenue growing fast.", {"paragraph": 2}, span_type="PARAGRAPH"),
        ]
        chunker = DocxChunker()
        chunks = chunker.chunk(spans, document_id=DOC_ID)

        assert len(chunks) >= 1
        assert chunks[0].doc_type == "DOCX"
        all_span_ids: set[str] = set()
        for c in chunks:
            all_span_ids.update(c.span_ids)
        assert {"s1", "s2", "s3"} == all_span_ids


class TestPptxChunker:
    """Tests for PptxChunker."""

    def test_pptx_chunker_groups_by_slide(self) -> None:
        """Same-slide spans become one chunk."""
        spans = [
            _make_span("s1", "Title slide text", {"slide": 0, "shape": 0, "paragraph": 0}),
            _make_span("s2", "Subtitle text", {"slide": 0, "shape": 1, "paragraph": 0}),
            _make_span("s3", "Slide 2 content", {"slide": 1, "shape": 0, "paragraph": 0}),
        ]
        chunker = PptxChunker()
        chunks = chunker.chunk(spans, document_id=DOC_ID)

        assert len(chunks) == 2
        slide0_chunk = [c for c in chunks if "s1" in c.span_ids][0]
        assert "s2" in slide0_chunk.span_ids
        slide1_chunk = [c for c in chunks if "s3" in c.span_ids][0]
        assert slide1_chunk.doc_type == "PPTX"


class TestChunkingService:
    """Tests for ChunkingService routing and fail-closed."""

    def test_chunking_service_routes_by_doc_type(self) -> None:
        """Correct chunker selected for each doc type."""
        service = ChunkingService()
        spans = [_make_span("s1", "Revenue data.", {"page": 1, "line": 1})]

        chunks = service.chunk_spans(spans, document_id=DOC_ID, doc_type="PDF")
        assert len(chunks) == 1
        assert chunks[0].doc_type == "PDF"

    def test_chunking_service_unknown_type_fails_closed(self) -> None:
        """Unknown doc_type raises UnsupportedDocumentTypeError."""
        service = ChunkingService()
        spans = [_make_span("s1", "Some text.", {"page": 1})]

        with pytest.raises(UnsupportedDocumentTypeError) as exc_info:
            service.chunk_spans(spans, document_id=DOC_ID, doc_type="MP3")

        assert "MP3" in str(exc_info.value)


class TestChunkInvariants:
    """Tests for cross-cutting chunk invariants."""

    def test_chunks_preserve_span_ids(self) -> None:
        """Every chunk has source span_ids from input spans."""
        spans = [
            _make_span("s1", "First page text.", {"page": 1, "line": 1}),
            _make_span("s2", "Second page text.", {"page": 2, "line": 1}),
        ]
        chunker = PdfChunker()
        chunks = chunker.chunk(spans, document_id=DOC_ID)

        all_span_ids: set[str] = set()
        for c in chunks:
            assert len(c.span_ids) >= 1
            all_span_ids.update(c.span_ids)
        assert all_span_ids == {"s1", "s2"}

    def test_chunks_have_deterministic_ordering(self) -> None:
        """Same input produces same chunk order regardless of input order."""
        spans_forward = [
            _make_span("s1", "Page 1 text.", {"page": 1, "line": 1}),
            _make_span("s2", "Page 2 text.", {"page": 2, "line": 1}),
        ]
        spans_reversed = list(reversed(spans_forward))

        chunker = PdfChunker()
        chunks_a = chunker.chunk(spans_forward, document_id=DOC_ID)
        chunks_b = chunker.chunk(spans_reversed, document_id=DOC_ID)

        assert len(chunks_a) == len(chunks_b)
        for a, b in zip(chunks_a, chunks_b, strict=True):
            assert a.span_ids == b.span_ids
            assert a.content == b.content

    def test_empty_spans_produce_no_chunks(self) -> None:
        """Empty span list produces empty chunk list."""
        chunker = PdfChunker()
        chunks = chunker.chunk([], document_id=DOC_ID)
        assert chunks == []

    def test_spans_without_text_skipped(self) -> None:
        """Spans with no text_excerpt are skipped gracefully."""
        spans = [
            _make_span("s1", "", {"page": 1, "line": 1}),
            {"span_id": "s2", "locator": {"page": 1, "line": 2}},
            _make_span("s3", "Valid text.", {"page": 1, "line": 3}),
        ]
        chunker = PdfChunker()
        chunks = chunker.chunk(spans, document_id=DOC_ID)

        assert len(chunks) == 1
        assert "s3" in chunks[0].span_ids
        assert "s1" not in chunks[0].span_ids
