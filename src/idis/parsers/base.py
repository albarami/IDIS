"""Base types for document parsing — shared across all parsers.

Provides:
- SpanDraft: Intermediate span representation (before DB IDs assigned)
- ParseError: Structured error with code and details
- ParseResult: Unified result type for all parsers
- ParseLimits: Configurable bounds to prevent resource exhaustion
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal


class ParseErrorCode(str, Enum):
    """Standardized error codes for parsing failures."""

    UNSUPPORTED_FORMAT = "unsupported_format"
    CORRUPTED_FILE = "corrupted_file"
    ENCRYPTED_PDF = "encrypted_pdf"
    NO_TEXT_EXTRACTED = "no_text_extracted"
    SCANNED_PDF_UNSUPPORTED = "scanned_pdf_unsupported"
    MAX_PAGES_EXCEEDED = "max_pages_exceeded"
    MAX_SHEETS_EXCEEDED = "max_sheets_exceeded"
    MAX_CELLS_EXCEEDED = "max_cells_exceeded"
    MAX_SIZE_EXCEEDED = "max_size_exceeded"
    INVALID_XLSX = "invalid_xlsx"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True, slots=True)
class ParseError:
    """Structured parsing error.

    Attributes:
        code: Standardized error code from ParseErrorCode enum.
        message: Human-readable error description.
        details: Optional additional context (e.g., page number, exception info).
    """

    code: ParseErrorCode
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "code": self.code.value,
            "message": self.message,
            "details": self.details,
        }


@dataclass(frozen=True, slots=True)
class SpanDraft:
    """Intermediate span representation before DB materialization.

    SpanDraft contains all data needed to create a DocumentSpan,
    but without tenant_id/document_id which are assigned during ingestion.

    Attributes:
        span_type: Type of span (PAGE_TEXT, CELL, etc.) — matches SpanType enum.
        locator: Stable JSON locator for reproducible citation.
        text_excerpt: Extracted text content.
        content_hash: Optional SHA-256 of text_excerpt for dedup/integrity.
    """

    span_type: Literal["PAGE_TEXT", "PARAGRAPH", "CELL", "TIMECODE"]
    locator: dict[str, Any]
    text_excerpt: str
    content_hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "span_type": self.span_type,
            "locator": self.locator,
            "text_excerpt": self.text_excerpt,
            "content_hash": self.content_hash,
        }


@dataclass(frozen=True, slots=True)
class ParseLimits:
    """Configurable parsing bounds to prevent resource exhaustion.

    Attributes:
        max_bytes: Maximum file size in bytes (default 50MB).
        max_pages: Maximum PDF pages to process (default 500).
        max_sheets: Maximum XLSX sheets to process (default 50).
        max_cells_per_sheet: Maximum cells per sheet (default 100,000).
        max_total_cells: Maximum total cells across all sheets (default 500,000).
    """

    max_bytes: int = 50 * 1024 * 1024  # 50 MB
    max_pages: int = 500
    max_sheets: int = 50
    max_cells_per_sheet: int = 100_000
    max_total_cells: int = 500_000


@dataclass(slots=True)
class ParseResult:
    """Unified result type for all document parsers.

    Attributes:
        doc_type: Document type (PDF, XLSX).
        success: True if parsing succeeded without fatal errors.
        spans: List of extracted spans (empty if success=False).
        metadata: Document-level metadata (page_count, sheet_names, etc.).
        errors: List of structured errors (empty if success=True).
        warnings: Non-fatal issues encountered during parsing.
    """

    doc_type: Literal["PDF", "XLSX", "DOCX", "PPTX", "UNKNOWN"]
    success: bool
    spans: list[SpanDraft] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    errors: list[ParseError] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "doc_type": self.doc_type,
            "success": self.success,
            "spans": [s.to_dict() for s in self.spans],
            "metadata": self.metadata,
            "errors": [e.to_dict() for e in self.errors],
            "warnings": self.warnings,
        }
