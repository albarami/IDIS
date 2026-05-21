"""PDF parser — deterministic text extraction with page/line locators.

Extracts text from PDF files page by page, splitting into lines,
and produces SpanDraft objects with stable locators.

Requirements:
- Deterministic: same bytes in → same ordered spans out
- Fail-closed: malformed/password-locked PDFs return structured errors
- No OCR: scanned PDFs with no extractable text fail with explicit error
"""

from __future__ import annotations

import hashlib
import io
import time
from typing import TYPE_CHECKING

from idis.parsers.base import (
    ParseError,
    ParseErrorCode,
    ParseLimits,
    ParseResult,
    SpanDraft,
)
from idis.parsers.ocr import (
    OcrConfig,
    OcrError,
    OcrPageText,
    OcrTimeoutError,
    OcrUnavailableError,
)

if TYPE_CHECKING:
    pass

from pypdf import PdfReader
from pypdf.errors import PdfReadError, PdfStreamError

PDF_DIAGNOSTIC_REASON_KEY = "pdf_diagnostic_reason"
PDF_DIAGNOSTIC_PARSED_TEXT = "parsed_text"
PDF_DIAGNOSTIC_PARSED_EMPTY_PASSWORD_ENCRYPTED = "parsed_empty_password_encrypted"
PDF_DIAGNOSTIC_PARSED_OCR = "parsed_ocr"
PDF_DIAGNOSTIC_SUBPHASE_ELAPSED_KEY = "pdf_subphase_elapsed_seconds"
PDF_PARSE_SUBPHASE_READER_INIT = "reader_init"
PDF_PARSE_SUBPHASE_EMPTY_CREDENTIAL_DECRYPT = "decrypt_empty_password"
PDF_PARSE_SUBPHASE_PAGE_COUNT = "page_count"
PDF_PARSE_SUBPHASE_TEXT_EXTRACTION_SPAN_BUILD = "text_extraction/span_build"


def _compute_content_hash(text: str) -> str:
    """Compute SHA-256 hash of text content."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_pdf(
    data: bytes,
    limits: ParseLimits | None = None,
    ocr_config: OcrConfig | None = None,
) -> ParseResult:
    """Parse PDF bytes and extract text spans with page/line locators.

    Args:
        data: Raw PDF file bytes.
        limits: Optional parsing limits (defaults to ParseLimits()).
        ocr_config: Optional explicit OCR execution config.

    Returns:
        ParseResult with success=True and spans if extraction succeeded,
        or success=False with structured errors if parsing failed.

    Behavior:
        - Pages are processed in document order (1-indexed).
        - Text is split into lines deterministically.
        - Each line becomes a SpanDraft with locator {page, line}.
        - Empty-password-openable encrypted PDFs parse.
        - Password-locked encrypted PDFs fail with ENCRYPTED_PDF error.
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

    subphase_elapsed_seconds: dict[str, float] = {}

    reader_init_started_at = time.monotonic()
    try:
        reader = PdfReader(io.BytesIO(data))
    except (PdfReadError, PdfStreamError) as e:
        subphase_elapsed_seconds[PDF_PARSE_SUBPHASE_READER_INIT] = (
            time.monotonic() - reader_init_started_at
        )
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
            private_diagnostics=_pdf_private_diagnostics(
                subphase_elapsed_seconds=subphase_elapsed_seconds
            ),
        )
    except Exception as e:
        subphase_elapsed_seconds[PDF_PARSE_SUBPHASE_READER_INIT] = (
            time.monotonic() - reader_init_started_at
        )
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
            private_diagnostics=_pdf_private_diagnostics(
                subphase_elapsed_seconds=subphase_elapsed_seconds
            ),
        )
    subphase_elapsed_seconds[PDF_PARSE_SUBPHASE_READER_INIT] = (
        time.monotonic() - reader_init_started_at
    )

    opened_with_empty_password = False
    if reader.is_encrypted:
        decrypt_started_at = time.monotonic()
        decrypted = _decrypt_with_empty_password(reader)
        subphase_elapsed_seconds[PDF_PARSE_SUBPHASE_EMPTY_CREDENTIAL_DECRYPT] = (
            time.monotonic() - decrypt_started_at
        )
        if not decrypted:
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
                private_diagnostics=_pdf_private_diagnostics(
                    subphase_elapsed_seconds=subphase_elapsed_seconds
                ),
            )
        opened_with_empty_password = True

    page_count_started_at = time.monotonic()
    total_pages = len(reader.pages)
    subphase_elapsed_seconds[PDF_PARSE_SUBPHASE_PAGE_COUNT] = (
        time.monotonic() - page_count_started_at
    )
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
            private_diagnostics=_pdf_private_diagnostics(
                subphase_elapsed_seconds=subphase_elapsed_seconds
            ),
        )

    spans: list[SpanDraft] = []
    warnings: list[str] = []
    total_text_length = 0

    text_extraction_started_at = time.monotonic()
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
    subphase_elapsed_seconds[PDF_PARSE_SUBPHASE_TEXT_EXTRACTION_SPAN_BUILD] = (
        time.monotonic() - text_extraction_started_at
    )

    if total_text_length == 0:
        if ocr_config is not None and ocr_config.enabled:
            return _parse_pdf_with_ocr(
                data=data,
                page_count=total_pages,
                ocr_config=ocr_config,
            )
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
            private_diagnostics=_pdf_private_diagnostics(
                subphase_elapsed_seconds=subphase_elapsed_seconds
            ),
        )

    diagnostic_reason = (
        PDF_DIAGNOSTIC_PARSED_EMPTY_PASSWORD_ENCRYPTED
        if opened_with_empty_password
        else PDF_DIAGNOSTIC_PARSED_TEXT
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
        private_diagnostics=_pdf_private_diagnostics(
            reason=diagnostic_reason,
            subphase_elapsed_seconds=subphase_elapsed_seconds,
        ),
        warnings=warnings,
    )


def _pdf_private_diagnostics(
    *,
    subphase_elapsed_seconds: dict[str, float],
    reason: str | None = None,
) -> dict[str, object]:
    diagnostics: dict[str, object] = {}
    if reason is not None:
        diagnostics[PDF_DIAGNOSTIC_REASON_KEY] = reason
    if subphase_elapsed_seconds:
        diagnostics[PDF_DIAGNOSTIC_SUBPHASE_ELAPSED_KEY] = dict(subphase_elapsed_seconds)
    return diagnostics


def _decrypt_with_empty_password(reader: PdfReader) -> bool:
    """Open PDFs that are encrypted but allow an empty user password."""
    try:
        return bool(reader.decrypt(""))
    except (PdfReadError, PdfStreamError, KeyError, TypeError, ValueError):
        return False


def _parse_pdf_with_ocr(
    *,
    data: bytes,
    page_count: int,
    ocr_config: OcrConfig,
) -> ParseResult:
    if ocr_config.max_pages < 1:
        return _ocr_error(ParseErrorCode.OCR_FAILED, "OCR page limit must be positive")
    if ocr_config.adapter is None:
        return ParseResult(
            doc_type="PDF",
            success=False,
            errors=[
                ParseError(
                    code=ParseErrorCode.OCR_UNAVAILABLE,
                    message="OCR is enabled but no OCR adapter is configured",
                    details={},
                )
            ],
        )
    try:
        pages = ocr_config.adapter.extract_pdf_text(
            data,
            max_pages=ocr_config.max_pages,
            timeout_seconds=ocr_config.timeout_seconds,
        )
        return _parse_ocr_pages(
            page_count=page_count,
            max_pages=ocr_config.max_pages,
            pages=pages,
        )
    except OcrTimeoutError:
        return _ocr_error(ParseErrorCode.OCR_TIMEOUT, "OCR timed out")
    except OcrUnavailableError:
        return _ocr_error(ParseErrorCode.OCR_UNAVAILABLE, "OCR unavailable")
    except OcrError:
        return _ocr_error(ParseErrorCode.OCR_FAILED, "OCR failed")
    except Exception:
        return _ocr_error(ParseErrorCode.OCR_FAILED, "OCR failed")


def _ocr_error(code: ParseErrorCode, message: str) -> ParseResult:
    return ParseResult(
        doc_type="PDF",
        success=False,
        errors=[ParseError(code=code, message=message, details={})],
    )


def _parse_ocr_pages(
    *,
    page_count: int,
    max_pages: int,
    pages: list[OcrPageText],
) -> ParseResult:
    page_window = min(page_count, max_pages)
    if len(pages) > page_window:
        return _ocr_error(ParseErrorCode.OCR_FAILED, "OCR returned invalid page results")

    spans: list[SpanDraft] = []
    total_text_length = 0
    seen_pages: set[int] = set()
    for page in pages:
        if not isinstance(page.page_number, int) or not 1 <= page.page_number <= page_window:
            return _ocr_error(ParseErrorCode.OCR_FAILED, "OCR returned invalid page results")
        if page.page_number in seen_pages:
            return _ocr_error(ParseErrorCode.OCR_FAILED, "OCR returned invalid page results")
        if not isinstance(page.text, str):
            return _ocr_error(ParseErrorCode.OCR_FAILED, "OCR returned invalid page results")
        seen_pages.add(page.page_number)
        total_text_length += len(page.text)
        for line_idx, line_text in enumerate(page.text.split("\n"), start=1):
            stripped = line_text.strip()
            if not stripped:
                continue
            spans.append(
                SpanDraft(
                    span_type="PAGE_TEXT",
                    locator={"page": page.page_number, "line": line_idx, "source": "ocr"},
                    text_excerpt=stripped,
                    content_hash=_compute_content_hash(stripped),
                )
            )

    if not spans:
        return ParseResult(
            doc_type="PDF",
            success=False,
            errors=[
                ParseError(
                    code=ParseErrorCode.OCR_NO_TEXT_EXTRACTED,
                    message="OCR completed but no extractable text was found",
                    details={"pages": page_count, "ocr_pages": len(pages)},
                )
            ],
        )

    return ParseResult(
        doc_type="PDF",
        success=True,
        spans=spans,
        metadata={
            "page_count": page_count,
            "span_count": len(spans),
            "total_text_length": total_text_length,
            "ocr_performed": True,
            "ocr_page_count": len(pages),
        },
        private_diagnostics={PDF_DIAGNOSTIC_REASON_KEY: PDF_DIAGNOSTIC_PARSED_OCR},
    )
