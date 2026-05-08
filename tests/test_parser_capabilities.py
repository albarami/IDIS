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


def test_html_txt_csv_are_unsupported_without_parser() -> None:
    for filename in ("synthetic_page.html", "synthetic_notes.txt", "synthetic_export.csv"):
        capability = capability_for_document(filename=filename)

        assert capability.support_status == DocumentSupportStatus.UNSUPPORTED
        assert capability.triage_status == DocumentTriageStatus.UNSUPPORTED_SOURCE


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
