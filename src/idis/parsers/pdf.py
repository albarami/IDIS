"""PDF parser — deterministic text extraction with page/line locators.

Extracts text from PDF files page by page, splitting into lines,
and produces SpanDraft objects with stable locators.

Requirements:
- Deterministic: same bytes in → same ordered spans out
- Fail-closed: malformed/encrypted PDFs return structured errors
- No OCR: scanned PDFs with no extractable text fail with explicit error
"""

from __future__ import annotations

import hashlib
import io
from typing import TYPE_CHECKING

from idis.parsers.base import (
    ParseError,
    ParseErrorCode,
    ParseLimits,
    ParseResult,
    SpanDraft,
)

if TYPE_CHECKING:
    pass

from pypdf import PdfReader
from pypdf.errors import PdfReadError, PdfStreamError


def _compute_content_hash(text: str) -> str:
    """Compute SHA-256 hash of text content."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_pdf(
    data: bytes,
    limits: ParseLimits | None = None,
) -> ParseResult:
    """Parse PDF bytes and extract text spans with page/line locators.

    Args:
        data: Raw PDF file bytes.
        limits: Optional parsing limits (defaults to ParseLimits()).

    Returns:
        ParseResult with success=True and spans if extraction succeeded,
        or success=False with structured errors if parsing failed.

    Behavior:
        - Pages are processed in document order (1-indexed).
        - Text is split into lines deterministically.
        - Each line becomes a SpanDraft with locator {page, line}.
        - Encrypted PDFs fail with ENCRYPTED_PDF error.
        - PDFs with no extractable text fail with NO_TEXT_EXTRACTED.
        - Malformed PDFs fail with CORRUPTED_FILE error.
    """
    if limits is None:
        limits = ParseLimits()

    if len(data) > limits.max_bytes:
        return ParseResult(
            doc_type="PDF",
            success=False,
            errors=[
                ParseError(
                    code=ParseErrorCode.MAX_SIZE_EXCEEDED,
                    message=f"File size {len(data)} bytes exceeds limit {limits.max_bytes}",
                    details={"size": len(data), "limit": limits.max_bytes},
                )
            ],
        )

    try:
        reader = PdfReader(io.BytesIO(data))
    except (PdfReadError, PdfStreamError) as e:
        return ParseResult(
            doc_type="PDF",
            success=False,
            errors=[
                ParseError(
                    code=ParseErrorCode.CORRUPTED_FILE,
                    message="Failed to read PDF file",
                    details={"error": str(e)},
                )
            ],
        )
    except Exception as e:
        return ParseResult(
            doc_type="PDF",
            success=False,
            errors=[
                ParseError(
                    code=ParseErrorCode.CORRUPTED_FILE,
                    message="Unexpected error reading PDF",
                    details={"error": str(e), "type": type(e).__name__},
                )
            ],
        )

    if reader.is_encrypted:
        return ParseResult(
            doc_type="PDF",
            success=False,
            errors=[
                ParseError(
                    code=ParseErrorCode.ENCRYPTED_PDF,
                    message="PDF is encrypted and cannot be parsed without password",
                    details={},
                )
            ],
        )

    total_pages = len(reader.pages)
    if total_pages > limits.max_pages:
        return ParseResult(
            doc_type="PDF",
            success=False,
            errors=[
                ParseError(
                    code=ParseErrorCode.MAX_PAGES_EXCEEDED,
                    message=f"PDF has {total_pages} pages, exceeds limit {limits.max_pages}",
                    details={"pages": total_pages, "limit": limits.max_pages},
                )
            ],
        )

    spans: list[SpanDraft] = []
    warnings: list[str] = []
    total_text_length = 0

    for page_idx, page in enumerate(reader.pages):
        page_num = page_idx + 1  # 1-indexed
        try:
            page_text = page.extract_text() or ""
        except Exception as e:
            warnings.append(f"Page {page_num}: text extraction failed ({e})")
            continue

        if not page_text.strip():
            continue

        total_text_length += len(page_text)

        lines = page_text.split("\n")
        for line_idx, line_text in enumerate(lines):
            line_num = line_idx + 1  # 1-indexed
            stripped = line_text.strip()
            if not stripped:
                continue

            spans.append(
                SpanDraft(
                    span_type="PAGE_TEXT",
                    locator={"page": page_num, "line": line_num},
                    text_excerpt=stripped,
                    content_hash=_compute_content_hash(stripped),
                )
            )

    if total_text_length == 0:
        return ParseResult(
            doc_type="PDF",
            success=False,
            errors=[
                ParseError(
                    code=ParseErrorCode.NO_TEXT_EXTRACTED,
                    message="No extractable text found in PDF (may be scanned/image-only)",
                    details={"pages": total_pages},
                )
            ],
        )

    return ParseResult(
        doc_type="PDF",
        success=True,
        spans=spans,
        metadata={
            "page_count": total_pages,
            "span_count": len(spans),
            "total_text_length": total_text_length,
        },
        warnings=warnings,
    )
