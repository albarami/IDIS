"""Tests for PDF parser — Phase 1.2.

Tests cover:
- Successful parsing with span extraction
- Locator correctness (page + line, 1-indexed)
- Determinism (same bytes → same output)
- Fail-closed behavior for corrupted/encrypted PDFs
- Size limit enforcement
"""

from __future__ import annotations

import io

import pytest

from idis.parsers.base import ParseErrorCode, ParseLimits
from idis.parsers.pdf import parse_pdf

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False


def create_test_pdf(text_lines: list[str], num_pages: int = 1) -> bytes:
    """Create a simple PDF with given text lines using reportlab.

    Args:
        text_lines: Lines of text to include on each page.
        num_pages: Number of pages to create.

    Returns:
        PDF file as bytes.
    """
    if not REPORTLAB_AVAILABLE:
        pytest.skip("reportlab not installed")

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)

    for page_num in range(num_pages):
        y_position = 750
        for line in text_lines:
            c.drawString(72, y_position, line)
            y_position -= 14
        if page_num < num_pages - 1:
            c.showPage()

    c.save()
    return buffer.getvalue()


class TestPDFParserSuccess:
    """Test successful PDF parsing scenarios."""

    def test_parse_simple_pdf(self) -> None:
        """Parse a simple PDF and verify spans are extracted."""
        text_lines = [
            "Revenue: $10M",
            "Growth Rate: 150%",
            "Team Size: 25",
        ]
        pdf_bytes = create_test_pdf(text_lines)

        result = parse_pdf(pdf_bytes)

        assert result.success is True
        assert result.doc_type == "PDF"
        assert len(result.errors) == 0
        assert len(result.spans) >= 1
        assert result.metadata["page_count"] == 1

    def test_locator_contains_page_and_line(self) -> None:
        """Verify locators contain page and line (1-indexed integers)."""
        text_lines = ["Line one", "Line two", "Line three"]
        pdf_bytes = create_test_pdf(text_lines)

        result = parse_pdf(pdf_bytes)

        assert result.success is True
        for span in result.spans:
            locator = span.locator
            assert "page" in locator, "Locator must contain 'page'"
            assert "line" in locator, "Locator must contain 'line'"
            assert isinstance(locator["page"], int)
            assert isinstance(locator["line"], int)
            assert locator["page"] >= 1, "Page must be 1-indexed"
            assert locator["line"] >= 1, "Line must be 1-indexed"

    def test_extracted_text_contains_content(self) -> None:
        """Verify extracted text contains the original content."""
        unique_text = "UNIQUE_REVENUE_123456789"
        pdf_bytes = create_test_pdf([unique_text])

        result = parse_pdf(pdf_bytes)

        assert result.success is True
        all_text = " ".join(span.text_excerpt for span in result.spans)
        assert unique_text in all_text

    def test_span_type_is_page_text(self) -> None:
        """Verify all spans have span_type PAGE_TEXT."""
        pdf_bytes = create_test_pdf(["Test content"])

        result = parse_pdf(pdf_bytes)

        assert result.success is True
        for span in result.spans:
            assert span.span_type == "PAGE_TEXT"

    def test_spans_have_content_hash(self) -> None:
        """Verify all spans have content_hash populated."""
        pdf_bytes = create_test_pdf(["Test content"])

        result = parse_pdf(pdf_bytes)

        assert result.success is True
        for span in result.spans:
            assert span.content_hash is not None
            assert len(span.content_hash) == 64  # SHA-256 hex

    def test_multi_page_pdf(self) -> None:
        """Parse multi-page PDF and verify page locators."""
        text_lines = ["Page content line"]
        pdf_bytes = create_test_pdf(text_lines, num_pages=3)

        result = parse_pdf(pdf_bytes)

        assert result.success is True
        assert result.metadata["page_count"] == 3

        pages_seen = {span.locator["page"] for span in result.spans}
        assert 1 in pages_seen
        assert 2 in pages_seen
        assert 3 in pages_seen


class TestPDFParserDeterminism:
    """Test deterministic behavior of PDF parser."""

    def test_same_bytes_same_output(self) -> None:
        """Parsing same bytes twice produces identical results."""
        text_lines = ["Revenue: $10M", "Growth: 150%"]
        pdf_bytes = create_test_pdf(text_lines)

        result1 = parse_pdf(pdf_bytes)
        result2 = parse_pdf(pdf_bytes)

        assert result1.success is True
        assert result2.success is True
        assert len(result1.spans) == len(result2.spans)

        for span1, span2 in zip(result1.spans, result2.spans, strict=True):
            assert span1.locator == span2.locator
            assert span1.text_excerpt == span2.text_excerpt
            assert span1.content_hash == span2.content_hash
            assert span1.span_type == span2.span_type


class TestPDFParserFailClosed:
    """Test fail-closed behavior for invalid inputs."""

    def test_corrupted_bytes_with_pdf_header(self) -> None:
        """Corrupted data with PDF header returns error, no exception."""
        corrupted = b"%PDF-1.4\n" + b"\x00\xff" * 100

        result = parse_pdf(corrupted)

        assert result.success is False
        assert len(result.errors) > 0
        error_codes = [e.code for e in result.errors]
        assert ParseErrorCode.CORRUPTED_FILE in error_codes

    def test_completely_invalid_bytes(self) -> None:
        """Completely invalid bytes return error, no exception."""
        invalid = b"This is not a PDF at all"

        result = parse_pdf(invalid)

        assert result.success is False
        assert len(result.errors) > 0

    def test_empty_bytes(self) -> None:
        """Empty bytes return error, no exception."""
        result = parse_pdf(b"")

        assert result.success is False
        assert len(result.errors) > 0

    def test_truncated_pdf(self) -> None:
        """Truncated PDF returns error, no exception."""
        text_lines = ["Test content"]
        pdf_bytes = create_test_pdf(text_lines)
        truncated = pdf_bytes[: len(pdf_bytes) // 3]

        result = parse_pdf(truncated)

        assert result.success is False
        assert len(result.errors) > 0


class TestPDFParserLimits:
    """Test parsing limit enforcement."""

    def test_max_size_exceeded(self) -> None:
        """File exceeding max_bytes limit returns error."""
        text_lines = ["Test content"]
        pdf_bytes = create_test_pdf(text_lines)
        tiny_limit = ParseLimits(max_bytes=100)

        result = parse_pdf(pdf_bytes, limits=tiny_limit)

        assert result.success is False
        assert len(result.errors) > 0
        error_codes = [e.code for e in result.errors]
        assert ParseErrorCode.MAX_SIZE_EXCEEDED in error_codes

    def test_max_pages_exceeded(self) -> None:
        """PDF exceeding max_pages limit returns error."""
        text_lines = ["Page content"]
        pdf_bytes = create_test_pdf(text_lines, num_pages=10)
        small_limit = ParseLimits(max_pages=5)

        result = parse_pdf(pdf_bytes, limits=small_limit)

        assert result.success is False
        assert len(result.errors) > 0
        error_codes = [e.code for e in result.errors]
        assert ParseErrorCode.MAX_PAGES_EXCEEDED in error_codes


class TestPDFParserMetadata:
    """Test metadata extraction."""

    def test_metadata_contains_page_count(self) -> None:
        """Metadata includes page_count."""
        pdf_bytes = create_test_pdf(["Test"], num_pages=5)

        result = parse_pdf(pdf_bytes)

        assert result.success is True
        assert "page_count" in result.metadata
        assert result.metadata["page_count"] == 5

    def test_metadata_contains_span_count(self) -> None:
        """Metadata includes span_count."""
        pdf_bytes = create_test_pdf(["Line 1", "Line 2"])

        result = parse_pdf(pdf_bytes)

        assert result.success is True
        assert "span_count" in result.metadata
        assert result.metadata["span_count"] == len(result.spans)
