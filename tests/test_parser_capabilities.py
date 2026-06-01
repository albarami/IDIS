"""Tests for deterministic parser capability registry."""

from __future__ import annotations

import pytest

from idis.models.document_classification import DocumentSupportStatus, DocumentTriageStatus
from idis.parsers.base import ParseError, ParseErrorCode, ParseResult
from idis.services.documents.parser_capabilities import (
    DEFAULT_MAX_CLASSIFICATION_BYTES,
    capability_for_document,
    triage_document,
)


def test_pdf_is_partially_supported() -> None:
    capability = capability_for_document(filename="synthetic_market_report.pdf")

    assert capability.file_type == "PDF"
    assert capability.support_status == DocumentSupportStatus.PARTIALLY_SUPPORTED
    assert capability.triage_status == DocumentTriageStatus.PARTIAL


def test_xlsx_is_partially_supported() -> None:
    capability = capability_for_document(filename="synthetic_financial_model.xlsx")

    assert capability.file_type == "XLSX"
    assert capability.support_status == DocumentSupportStatus.PARTIALLY_SUPPORTED
    assert capability.parser_name == "xlsx"


def test_docx_is_supported_or_partial() -> None:
    capability = capability_for_document(filename="synthetic_contract.docx")

    assert capability.file_type == "DOCX"
    assert capability.support_status in {
        DocumentSupportStatus.SUPPORTED,
        DocumentSupportStatus.PARTIALLY_SUPPORTED,
    }


def test_pptx_is_partially_supported() -> None:
    capability = capability_for_document(filename="synthetic_market_research.pptx")

    assert capability.file_type == "PPTX"
    assert capability.support_status == DocumentSupportStatus.PARTIALLY_SUPPORTED


def test_mp4_is_unsupported_and_conversion_required() -> None:
    capability = capability_for_document(filename="synthetic_management_interview.mp4")

    assert capability.support_status == DocumentSupportStatus.CONVERSION_REQUIRED
    assert capability.triage_status == DocumentTriageStatus.CONVERSION_REQUIRED
    assert capability.requires_conversion is True


def test_onenote_is_unsupported_and_conversion_required() -> None:
    for filename in ("synthetic_notes.one", "synthetic_notes.onetoc2"):
        capability = capability_for_document(filename=filename)

        assert capability.support_status == DocumentSupportStatus.CONVERSION_REQUIRED
        assert capability.triage_status == DocumentTriageStatus.CONVERSION_REQUIRED


def test_png_requires_ocr() -> None:
    capability = capability_for_document(filename="synthetic_scanned_invoice.png")

    assert capability.support_status == DocumentSupportStatus.SCANNED_OR_IMAGE_ONLY
    assert capability.triage_status == DocumentTriageStatus.OCR_REQUIRED
    assert capability.requires_ocr is True


def test_html_and_text_are_supported_with_text_parser() -> None:
    for filename in ("synthetic_page.html", "synthetic_page.htm", "synthetic_notes.txt"):
        capability = capability_for_document(filename=filename)

        assert capability.support_status == DocumentSupportStatus.SUPPORTED
        assert capability.triage_status == DocumentTriageStatus.READY
        assert capability.parser_name == "html_text"
        assert "text_parser_available" in capability.reason_codes
        assert capability.requires_ocr is False
        assert capability.requires_conversion is False


def test_csv_archives_and_mail_remain_unsupported_blockers() -> None:
    for filename in (
        "synthetic_export.csv",
        "synthetic_archive.zip",
        "synthetic_archive.rar",
        "synthetic_archive.7z",
        "synthetic_mail.msg",
        "synthetic_mail.eml",
    ):
        capability = capability_for_document(filename=filename)

        assert capability.support_status == DocumentSupportStatus.UNSUPPORTED
        assert capability.triage_status == DocumentTriageStatus.UNSUPPORTED_SOURCE
        assert capability.reason_codes == ["unsupported_format"]


def test_file_above_ingestion_limit_is_too_large() -> None:
    capability = capability_for_document(
        filename="synthetic_model.xlsx",
        file_size_bytes=DEFAULT_MAX_CLASSIFICATION_BYTES + 1,
    )

    assert capability.support_status == DocumentSupportStatus.TOO_LARGE
    assert capability.triage_status == DocumentTriageStatus.TOO_LARGE


@pytest.mark.parametrize(
    "error_code",
    [
        ParseErrorCode.MAX_SIZE_EXCEEDED,
        ParseErrorCode.MAX_PAGES_EXCEEDED,
        ParseErrorCode.MAX_SHEETS_EXCEEDED,
        ParseErrorCode.MAX_CELLS_EXCEEDED,
    ],
)
def test_all_parser_limit_failures_triage_to_too_large(
    error_code: ParseErrorCode,
) -> None:
    parse_result = ParseResult(
        doc_type="XLSX",
        success=False,
        errors=[ParseError(code=error_code, message=error_code.value)],
    )

    capability = triage_document(
        filename="synthetic_model.xlsx",
        parse_result=parse_result,
    )

    assert capability.support_status == DocumentSupportStatus.TOO_LARGE
    assert capability.triage_status == DocumentTriageStatus.TOO_LARGE


def test_unknown_extension_is_unknown_or_unsupported() -> None:
    capability = capability_for_document(filename="synthetic_artifact.bin")

    assert capability.support_status in {
        DocumentSupportStatus.UNKNOWN,
        DocumentSupportStatus.UNSUPPORTED,
    }
    assert capability.triage_status in {
        DocumentTriageStatus.UNKNOWN,
        DocumentTriageStatus.UNSUPPORTED_SOURCE,
    }


def test_office_and_pdf_capability_triage_is_deterministic() -> None:
    expected = {
        "synthetic_contract.docx": (
            DocumentSupportStatus.SUPPORTED,
            DocumentTriageStatus.READY,
            "docx_text_parser_available",
        ),
        "synthetic_report.pdf": (
            DocumentSupportStatus.PARTIALLY_SUPPORTED,
            DocumentTriageStatus.PARTIAL,
            "pdf_text_only_no_ocr",
        ),
        "synthetic_model.xlsx": (
            DocumentSupportStatus.PARTIALLY_SUPPORTED,
            DocumentTriageStatus.PARTIAL,
            "xlsx_partial_table_semantics",
        ),
        "synthetic_model.xlsm": (
            DocumentSupportStatus.PARTIALLY_SUPPORTED,
            DocumentTriageStatus.PARTIAL,
            "xlsx_partial_table_semantics",
        ),
        "synthetic_deck.pptx": (
            DocumentSupportStatus.PARTIALLY_SUPPORTED,
            DocumentTriageStatus.PARTIAL,
            "pptx_partial_slide_text",
        ),
    }
    for filename, (support, triage, reason) in expected.items():
        capability = capability_for_document(filename=filename)

        assert capability.support_status == support
        assert capability.triage_status == triage
        assert reason in capability.reason_codes
        assert capability.requires_ocr is False
        assert capability.requires_conversion is False


def test_conversion_required_classes_are_reason_coded_only() -> None:
    for filename in (
        "synthetic_interview.mp4",
        "synthetic_notes.one",
        "synthetic_notes.onetoc2",
    ):
        capability = capability_for_document(filename=filename)

        assert capability.support_status == DocumentSupportStatus.CONVERSION_REQUIRED
        assert capability.triage_status == DocumentTriageStatus.CONVERSION_REQUIRED
        assert capability.requires_conversion is True
        assert "conversion_required" in capability.reason_codes
        assert capability.requires_ocr is False


def test_capability_classification_invokes_no_parser_or_media_execution() -> None:
    from unittest.mock import patch

    boom = AssertionError("classification must not execute parsers/media")
    with (
        patch("idis.parsers.registry.parse_bytes", side_effect=boom),
        patch("idis.parsers.media.parse_media", side_effect=boom),
        patch("idis.parsers.image.parse_image", side_effect=boom),
    ):
        mp4 = capability_for_document(filename="synthetic_interview.mp4")
        triage_document(filename="synthetic_interview.mp4")
        for filename in ("x.docx", "x.pdf", "x.html", "x.txt", "x.csv", "x.png"):
            capability_for_document(filename=filename)

    assert mp4.support_status == DocumentSupportStatus.CONVERSION_REQUIRED


def test_html_and_text_are_not_unsupported_blockers() -> None:
    for filename in ("synthetic_page.html", "synthetic_page.htm", "synthetic_notes.txt"):
        capability = capability_for_document(filename=filename)

        assert capability.support_status != DocumentSupportStatus.UNSUPPORTED
        assert capability.triage_status != DocumentTriageStatus.UNSUPPORTED_SOURCE
        assert "unsupported_format" not in capability.reason_codes
