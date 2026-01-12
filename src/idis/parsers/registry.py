"""Parser registry — format detection and parser dispatch.

Provides a single entrypoint for parsing documents by detecting
format from magic bytes and dispatching to the appropriate parser.

Requirements:
- Deterministic format detection via magic bytes (not extension/mime)
- Fail-closed: unknown formats return structured error
- Never raises exceptions: all failures captured in ParseResult
"""

from __future__ import annotations

import zipfile
from io import BytesIO

from idis.parsers.base import (
    ParseError,
    ParseErrorCode,
    ParseLimits,
    ParseResult,
)
from idis.parsers.pdf import parse_pdf
from idis.parsers.xlsx import parse_xlsx

PDF_MAGIC = b"%PDF-"
ZIP_MAGIC = b"PK\x03\x04"


def _is_pdf(data: bytes) -> bool:
    """Check if data starts with PDF magic bytes."""
    return data[:5] == PDF_MAGIC


def _is_xlsx(data: bytes) -> bool:
    """Check if data is a valid XLSX (ZIP with xl/workbook.xml).

    XLSX files are ZIP archives containing xl/workbook.xml.
    We verify both the ZIP signature and the presence of the workbook.
    """
    if len(data) < 4 or data[:4] != ZIP_MAGIC:
        return False

    try:
        with zipfile.ZipFile(BytesIO(data), "r") as zf:
            names = zf.namelist()
            return "xl/workbook.xml" in names
    except (zipfile.BadZipFile, Exception):
        return False


def detect_format(data: bytes) -> str | None:
    """Detect document format from magic bytes.

    Args:
        data: Raw file bytes.

    Returns:
        Format string ("PDF", "XLSX") or None if unknown.

    Detection priority:
        1. PDF: %PDF- magic bytes
        2. XLSX: ZIP with xl/workbook.xml
    """
    if _is_pdf(data):
        return "PDF"
    if _is_xlsx(data):
        return "XLSX"
    return None


def parse_bytes(
    data: bytes,
    filename: str | None = None,
    mime_type: str | None = None,
    limits: ParseLimits | None = None,
) -> ParseResult:
    """Parse document bytes by detecting format and dispatching to parser.

    Args:
        data: Raw file bytes.
        filename: Optional filename (used for error context, not detection).
        mime_type: Optional MIME type (used for error context, not detection).
        limits: Optional parsing limits (defaults to ParseLimits()).

    Returns:
        ParseResult from the appropriate parser, or error result for
        unsupported/undetectable formats.

    Behavior:
        - Format detection is based on magic bytes, not filename/mime.
        - Unsupported formats return success=False with UNSUPPORTED_FORMAT error.
        - Never raises exceptions; all failures captured in result.
    """
    if limits is None:
        limits = ParseLimits()

    if len(data) == 0:
        return ParseResult(
            doc_type="PDF",  # Arbitrary default for error case
            success=False,
            errors=[
                ParseError(
                    code=ParseErrorCode.CORRUPTED_FILE,
                    message="Empty file",
                    details={"filename": filename, "mime_type": mime_type},
                )
            ],
        )

    detected_format = detect_format(data)

    if detected_format == "PDF":
        return parse_pdf(data, limits=limits)

    if detected_format == "XLSX":
        return parse_xlsx(data, limits=limits)

    return ParseResult(
        doc_type="PDF",  # Arbitrary default for error case
        success=False,
        errors=[
            ParseError(
                code=ParseErrorCode.UNSUPPORTED_FORMAT,
                message="Unknown or unsupported file format",
                details={
                    "filename": filename,
                    "mime_type": mime_type,
                    "header_bytes": data[:16].hex() if len(data) >= 16 else data.hex(),
                },
            )
        ],
    )
