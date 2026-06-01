"""Tests for the HTML/TEXT chunker and ChunkingService HTML/TEXT routing (Slice78).

Pure text grouping of html_text parser PARAGRAPH spans into deterministic,
token-bounded extraction chunks. No provider/LLM/OCR/media/network calls.
"""

from __future__ import annotations

import pytest

from idis.services.extraction.chunking.base import UnsupportedDocumentTypeError
from idis.services.extraction.chunking.service import ChunkingService
from idis.services.extraction.chunking.text_chunker import TextChunker

DOC_ID = "doc00001-0000-0000-0000-000000000001"


def _span(span_id: str, text: str, locator: dict) -> dict:
    return {
        "span_id": span_id,
        "text_excerpt": text,
        "locator": locator,
        "span_type": "PARAGRAPH",
    }


def test_html_paragraph_spans_chunk_with_doc_type_html() -> None:
    spans = [
        _span("h1", "Visible heading", {"node": 1, "source": "html"}),
        _span("h2", "Visible paragraph.", {"node": 2, "source": "html"}),
    ]

    chunks = TextChunker(doc_type="HTML").chunk(spans, document_id=DOC_ID)

    assert len(chunks) == 1
    assert chunks[0].doc_type == "HTML"
    assert chunks[0].span_ids == ("h1", "h2")
    assert "Visible heading" in chunks[0].content
    assert "Visible paragraph." in chunks[0].content
    assert chunks[0].token_estimate > 0


def test_text_paragraph_spans_chunk_with_doc_type_text() -> None:
    spans = [
        _span("t1", "First line", {"line": 1, "source": "text"}),
        _span("t2", "Second line", {"line": 3, "source": "text"}),
    ]

    chunks = TextChunker(doc_type="TEXT").chunk(spans, document_id=DOC_ID)

    assert len(chunks) == 1
    assert chunks[0].doc_type == "TEXT"
    assert chunks[0].span_ids == ("t1", "t2")


def test_text_chunker_is_deterministic_and_sorts_by_locator() -> None:
    spans = [
        _span("t2", "Second", {"line": 2, "source": "text"}),
        _span("t1", "First", {"line": 1, "source": "text"}),
    ]

    first = TextChunker(doc_type="TEXT").chunk(spans, document_id=DOC_ID)
    second = TextChunker(doc_type="TEXT").chunk(spans, document_id=DOC_ID)

    assert first == second
    assert first[0].span_ids == ("t1", "t2")  # sorted by locator


def test_text_chunker_splits_oversized_span_within_token_limit() -> None:
    big = " ".join(f"word{i}" for i in range(2000))
    spans = [_span("t1", big, {"line": 1, "source": "text"})]

    chunks = TextChunker(doc_type="TEXT", max_tokens=100).chunk(spans, document_id=DOC_ID)

    assert len(chunks) > 1
    assert all(chunk.token_estimate <= 100 for chunk in chunks)
    assert all(chunk.span_ids == ("t1",) for chunk in chunks)


def test_text_chunker_skips_empty_or_whitespace_spans() -> None:
    spans = [_span("t1", "   ", {"line": 1, "source": "text"})]

    assert TextChunker(doc_type="TEXT").chunk(spans, document_id=DOC_ID) == []


def test_chunking_service_routes_html_and_text() -> None:
    service = ChunkingService()
    html_chunks = service.chunk_spans(
        [_span("h1", "Hello", {"node": 1, "source": "html"})],
        document_id=DOC_ID,
        doc_type="HTML",
    )
    text_chunks = service.chunk_spans(
        [_span("t1", "Hello", {"line": 1, "source": "text"})],
        document_id=DOC_ID,
        doc_type="TEXT",
    )

    assert len(html_chunks) == 1
    assert html_chunks[0].doc_type == "HTML"
    assert len(text_chunks) == 1
    assert text_chunks[0].doc_type == "TEXT"


def test_chunking_service_still_fails_closed_on_truly_unsupported_doc_type() -> None:
    service = ChunkingService()
    with pytest.raises(UnsupportedDocumentTypeError):
        service.chunk_spans([_span("z1", "x", {"line": 1})], document_id=DOC_ID, doc_type="ZIP")
