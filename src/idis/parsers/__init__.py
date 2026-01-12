"""IDIS Document Parsers — Phase 1.2.

This package provides deterministic document parsing for PDF, XLSX, DOCX, and PPTX
files, producing DocumentSpan-compatible objects with stable locators.

Modules:
    base: Shared types (ParseResult, SpanDraft, ParseError)
    pdf: PDF text extraction with page/line locators
    xlsx: XLSX cell extraction with sheet/cell locators
    docx: DOCX text extraction with paragraph/table locators
    pptx: PPTX text extraction with slide/shape locators
    registry: Format detection and parser dispatch
"""

from idis.parsers.base import ParseError, ParseResult, SpanDraft
from idis.parsers.docx import parse_docx
from idis.parsers.pdf import parse_pdf
from idis.parsers.pptx import parse_pptx
from idis.parsers.registry import parse_bytes
from idis.parsers.xlsx import parse_xlsx

__all__ = [
    "ParseError",
    "ParseResult",
    "SpanDraft",
    "parse_bytes",
    "parse_docx",
    "parse_pdf",
    "parse_pptx",
    "parse_xlsx",
]
