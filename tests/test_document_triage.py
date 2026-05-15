"""Tests for parser triage from parse outcomes and metadata."""

from __future__ import annotations

from idis.models.document_classification import DocumentSupportStatus, DocumentTriageStatus
from idis.parsers.base import ParseError, ParseErrorCode, ParseResult
from idis.services.documents.classifier import DocumentDescriptor
from idis.services.documents.parser_capabilities import triage_document

TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEAL_ID = "22222222-2222-2222-2222-222222222222"
DOCUMENT_ID = "33333333-3333-3333-3333-333333333333"


def _descriptor(filename: str, size: int = 1024) -> DocumentDescriptor:
    return DocumentDescriptor(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        document_id=DOCUMENT_ID,
        filename=filename,
        file_size_bytes=size,
    )


def _failed_parse(code: ParseErrorCode, doc_type: str = "PDF") -> ParseResult:
    return ParseResult(
        doc_type=doc_type,  # type: ignore[arg-type]
        success=False,
        errors=[ParseError(code=code, message=code.value)],
    )


def test_encrypted_pdf_parser_error_triages_as_encrypted() -> None:
    result = triage_document(
        _descriptor("synthetic_locked.pdf"),
        parse_result=_failed_parse(ParseErrorCode.ENCRYPTED_PDF),
    )

    assert result.support_status == DocumentSupportStatus.ENCRYPTED
    assert result.triage_status == DocumentTriageStatus.BLOCKED


def test_no_text_pdf_triages_as_scanned_or_image_only() -> None:
    result = triage_document(
        _descriptor("synthetic_scanned.pdf"),
        parse_result=_failed_parse(ParseErrorCode.NO_TEXT_EXTRACTED),
    )

    assert result.support_status == DocumentSupportStatus.SCANNED_OR_IMAGE_ONLY
    assert result.triage_status == DocumentTriageStatus.OCR_REQUIRED
    assert result.requires_ocr is True
    assert result.reason_codes == ["ocr_required"]


def test_no_text_docx_does_not_claim_ocr_required() -> None:
    result = triage_document(
        _descriptor("synthetic_empty.docx"),
        parse_result=_failed_parse(ParseErrorCode.NO_TEXT_EXTRACTED, doc_type="DOCX"),
    )

    assert result.support_status == DocumentSupportStatus.UNKNOWN
    assert result.triage_status == DocumentTriageStatus.BLOCKED
    assert result.requires_ocr is False
    assert result.reason_codes == ["no_text_extracted"]


def test_corrupted_openxml_triages_as_corrupted() -> None:
    result = triage_document(
        _descriptor("synthetic_corrupt.xlsx"),
        parse_result=_failed_parse(ParseErrorCode.INVALID_XLSX, doc_type="XLSX"),
    )

    assert result.support_status == DocumentSupportStatus.CORRUPTED
    assert result.triage_status == DocumentTriageStatus.BLOCKED


def test_unsupported_file_triages_as_unsupported() -> None:
    result = triage_document(_descriptor("synthetic_video.mp4"))

    assert result.support_status == DocumentSupportStatus.CONVERSION_REQUIRED
    assert result.triage_status == DocumentTriageStatus.CONVERSION_REQUIRED


def test_large_file_triages_as_too_large() -> None:
    result = triage_document(_descriptor("synthetic_large.pdf", size=100 * 1024 * 1024))

    assert result.support_status == DocumentSupportStatus.TOO_LARGE
    assert result.triage_status == DocumentTriageStatus.TOO_LARGE


def test_partial_parser_support_has_partial_status() -> None:
    result = triage_document(_descriptor("synthetic_financial_model.xlsx"))

    assert result.support_status == DocumentSupportStatus.PARTIALLY_SUPPORTED
    assert result.triage_status == DocumentTriageStatus.PARTIAL


def test_conversion_required_flags_for_video_onenote_and_image() -> None:
    for filename in ("synthetic_video.mp4", "synthetic_notes.one", "synthetic_scan.png"):
        result = triage_document(_descriptor(filename))

        assert result.requires_conversion or result.requires_ocr
        assert result.usable_without_conversion is False
