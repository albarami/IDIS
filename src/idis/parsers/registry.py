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
from idis.parsers.docx import parse_docx
from idis.parsers.pdf import parse_pdf
from idis.parsers.pptx import parse_pptx
from idis.parsers.xlsx import parse_xlsx

PDF_MAGIC = b"%PDF-"
ZIP_MAGIC = b"PK\x03\x04"


def _is_pdf(data: bytes) -> bool:
    """Check if data starts with PDF magic bytes."""
    return data[:5] == PDF_MAGIC


def _detect_zip_format(data: bytes) -> str | None:
    """Detect Office Open XML format from ZIP contents.

    Args:
        data: Raw file bytes (must start with ZIP magic).

    Returns:
        Format string ("XLSX", "DOCX", "PPTX") or None if not recognized.

    Detection order:
        1. XLSX: contains xl/workbook.xml
        2. DOCX: contains word/document.xml
        3. PPTX: contains ppt/presentation.xml
    """
    if len(data) < 4 or data[:4] != ZIP_MAGIC:
        return None

    try:
        with zipfile.ZipFile(BytesIO(data), "r") as zf:
            names = zf.namelist()
            if "xl/workbook.xml" in names:
                return "XLSX"
            if "word/document.xml" in names:
                return "DOCX"
            if "ppt/presentation.xml" in names:
                return "PPTX"
    except (zipfile.BadZipFile, Exception):
        return None

    return None


def detect_format(data: bytes) -> str | None:
    """Detect document format from magic bytes.

    Args:
        data: Raw file bytes.

    Returns:
        Format string ("PDF", "XLSX", "DOCX", "PPTX") or None if unknown.

    Detection priority:
        1. PDF: %PDF- magic bytes
        2. XLSX: ZIP with xl/workbook.xml
        3. DOCX: ZIP with word/document.xml
        4. PPTX: ZIP with ppt/presentation.xml
    """
    if _is_pdf(data):
        return "PDF"

    zip_format = _detect_zip_format(data)
    if zip_format:
        return zip_format

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
        - Empty bytes return UNSUPPORTED_FORMAT with doc_type UNKNOWN.
        - Unsupported formats return success=False with UNSUPPORTED_FORMAT error.
        - Never raises exceptions; all failures captured in result.
    """
    if limits is None:
        limits = ParseLimits()

    if len(data) == 0:
        return ParseResult(
            doc_type="UNKNOWN",
            success=False,
            errors=[
                ParseError(
                    code=ParseErrorCode.UNSUPPORTED_FORMAT,
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

    if detected_format == "DOCX":
        return parse_docx(data, limits=limits)

    if detected_format == "PPTX":
        return parse_pptx(data, limits=limits)

    return ParseResult(
        doc_type="UNKNOWN",
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
