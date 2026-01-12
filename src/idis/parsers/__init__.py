"""IDIS Document Parsers — Phase 1.2.

This package provides deterministic document parsing for PDF and XLSX files,
producing DocumentSpan-compatible objects with stable locators.

Modules:
    base: Shared types (ParseResult, SpanDraft, ParseError)
    pdf: PDF text extraction with page/line locators
    xlsx: XLSX cell extraction with sheet/cell locators
    registry: Format detection and parser dispatch
"""

from idis.parsers.base import ParseError, ParseResult, SpanDraft
from idis.parsers.pdf import parse_pdf
from idis.parsers.registry import parse_bytes
from idis.parsers.xlsx import parse_xlsx

__all__ = [
    "ParseError",
    "ParseResult",
    "SpanDraft",
    "parse_bytes",
    "parse_pdf",
    "parse_xlsx",
]
