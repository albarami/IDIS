"""Deterministic parser capability registry and triage rules."""

from __future__ import annotations

from pathlib import PurePath
from typing import Any

from idis.models.document_classification import (
    DocumentSupportStatus,
    DocumentTriageStatus,
    ParserCapability,
)
from idis.parsers.base import ParseErrorCode, ParseResult
from idis.parsers.registry import detect_format
from idis.services.ingestion.service import DEFAULT_MAX_BYTES

DEFAULT_MAX_CLASSIFICATION_BYTES = DEFAULT_MAX_BYTES

_SUPPORTED_CAPABILITIES: dict[str, ParserCapability] = {
    "PDF": ParserCapability(
        file_type="PDF",
        parser_name="pdf",
        support_status=DocumentSupportStatus.PARTIALLY_SUPPORTED,
        triage_status=DocumentTriageStatus.PARTIAL,
        reason_codes=["pdf_text_only_no_ocr"],
        warnings=["PDF parser extracts text only; OCR/table extraction is not claimed"],
    ),
    "XLSX": ParserCapability(
        file_type="XLSX",
        parser_name="xlsx",
        support_status=DocumentSupportStatus.PARTIALLY_SUPPORTED,
        triage_status=DocumentTriageStatus.PARTIAL,
        reason_codes=["xlsx_partial_table_semantics"],
        warnings=["XLSX parser extracts cell spans but not workbook methodology semantics"],
    ),
    "DOCX": ParserCapability(
        file_type="DOCX",
        parser_name="docx",
        support_status=DocumentSupportStatus.SUPPORTED,
        triage_status=DocumentTriageStatus.READY,
        reason_codes=["docx_text_parser_available"],
    ),
    "PPTX": ParserCapability(
        file_type="PPTX",
        parser_name="pptx",
        support_status=DocumentSupportStatus.PARTIALLY_SUPPORTED,
        triage_status=DocumentTriageStatus.PARTIAL,
        reason_codes=["pptx_partial_slide_text"],
        warnings=["PPTX parser extracts slide text only"],
    ),
}

_CONVERSION_REQUIRED_EXTENSIONS = {
    ".mp4": "video_conversion_required",
    ".one": "onenote_conversion_required",
    ".onetoc2": "onenote_conversion_required",
}
_OCR_REQUIRED_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".tif",
    ".tiff",
    ".bmp",
}
_UNSUPPORTED_EXTENSIONS = {
    ".html",
    ".htm",
    ".txt",
    ".csv",
    ".zip",
    ".rar",
    ".7z",
    ".msg",
    ".eml",
}
_EXTENSION_TO_FORMAT = {
    ".pdf": "PDF",
    ".xlsx": "XLSX",
    ".xlsm": "XLSX",
    ".docx": "DOCX",
    ".pptx": "PPTX",
}
_LIMIT_ERROR_CODES = {
    ParseErrorCode.MAX_SIZE_EXCEEDED,
    ParseErrorCode.MAX_PAGES_EXCEEDED,
    ParseErrorCode.MAX_SHEETS_EXCEEDED,
    ParseErrorCode.MAX_CELLS_EXCEEDED,
}
_OCR_ERROR_REASON_CODES = {
    ParseErrorCode.OCR_FAILED: "ocr_failed",
    ParseErrorCode.OCR_TIMEOUT: "ocr_timeout",
    ParseErrorCode.OCR_UNAVAILABLE: "ocr_unavailable",
    ParseErrorCode.OCR_NO_TEXT_EXTRACTED: "ocr_no_text_extracted",
}
_MEDIA_ERROR_REASON_CODES = {
    ParseErrorCode.MEDIA_TRANSCRIPTION_UNAVAILABLE: "media_transcription_unavailable",
    ParseErrorCode.MEDIA_TRANSCRIPTION_TIMEOUT: "media_transcription_timeout",
    ParseErrorCode.MEDIA_TRANSCRIPTION_FAILED: "media_transcription_failed",
    ParseErrorCode.MEDIA_DURATION_EXCEEDED: "media_duration_exceeded",
    ParseErrorCode.MEDIA_NO_TEXT_EXTRACTED: "media_no_text_extracted",
}


def capability_for_document(
    *,
    filename: str,
    file_size_bytes: int | None = None,
    data: bytes | None = None,
    detected_format: str | None = None,
    max_bytes: int = DEFAULT_MAX_CLASSIFICATION_BYTES,
) -> ParserCapability:
    """Return deterministic parser capability for a document descriptor."""
    if file_size_bytes is not None and file_size_bytes > max_bytes:
        return ParserCapability(
            file_type=_infer_file_type(filename, detected_format),
            support_status=DocumentSupportStatus.TOO_LARGE,
            triage_status=DocumentTriageStatus.TOO_LARGE,
            reason_codes=["file_too_large"],
            usable_without_conversion=False,
        )

    normalized_format = _normalize_detected_format(data=data, detected_format=detected_format)
    if normalized_format in _SUPPORTED_CAPABILITIES:
        return _SUPPORTED_CAPABILITIES[normalized_format].model_copy(deep=True)

    extension = PurePath(filename).suffix.lower()
    if extension in _EXTENSION_TO_FORMAT:
        return _SUPPORTED_CAPABILITIES[_EXTENSION_TO_FORMAT[extension]].model_copy(deep=True)

    if extension in _CONVERSION_REQUIRED_EXTENSIONS:
        return ParserCapability(
            file_type=extension.lstrip(".").upper(),
            support_status=DocumentSupportStatus.CONVERSION_REQUIRED,
            triage_status=DocumentTriageStatus.CONVERSION_REQUIRED,
            reason_codes=["conversion_required"],
            requires_conversion=True,
            usable_without_conversion=False,
        )

    if extension in _OCR_REQUIRED_EXTENSIONS:
        return ParserCapability(
            file_type=extension.lstrip(".").upper(),
            support_status=DocumentSupportStatus.SCANNED_OR_IMAGE_ONLY,
            triage_status=DocumentTriageStatus.OCR_REQUIRED,
            reason_codes=["ocr_required"],
            requires_ocr=True,
            usable_without_conversion=False,
        )

    if extension in _UNSUPPORTED_EXTENSIONS:
        return ParserCapability(
            file_type=extension.lstrip(".").upper(),
            support_status=DocumentSupportStatus.UNSUPPORTED,
            triage_status=DocumentTriageStatus.UNSUPPORTED_SOURCE,
            reason_codes=["unsupported_format"],
            usable_without_conversion=False,
        )

    return ParserCapability(
        file_type=_infer_file_type(filename, detected_format),
        support_status=DocumentSupportStatus.UNKNOWN,
        triage_status=DocumentTriageStatus.UNKNOWN,
        reason_codes=["unknown_format"],
        usable_without_conversion=False,
    )


def triage_document(
    descriptor: Any | None = None,
    *,
    filename: str | None = None,
    parse_result: ParseResult | None = None,
    max_bytes: int = DEFAULT_MAX_CLASSIFICATION_BYTES,
) -> ParserCapability:
    """Return parser triage for a descriptor and optional parse result."""
    file_size_bytes = getattr(descriptor, "file_size_bytes", None)
    resolved_filename = str(filename or getattr(descriptor, "filename", ""))
    if parse_result is None:
        return capability_for_document(
            filename=resolved_filename,
            file_size_bytes=file_size_bytes,
            max_bytes=max_bytes,
        )

    error_codes = {error.code for error in parse_result.errors}
    file_type = parse_result.doc_type

    if error_codes & _LIMIT_ERROR_CODES:
        return ParserCapability(
            file_type=file_type,
            support_status=DocumentSupportStatus.TOO_LARGE,
            triage_status=DocumentTriageStatus.TOO_LARGE,
            reason_codes=["file_too_large"],
            usable_without_conversion=False,
        )

    if ParseErrorCode.ENCRYPTED_PDF in error_codes:
        return ParserCapability(
            file_type="PDF",
            support_status=DocumentSupportStatus.ENCRYPTED,
            triage_status=DocumentTriageStatus.BLOCKED,
            reason_codes=["encrypted_pdf"],
            usable_without_conversion=False,
        )

    if ParseErrorCode.SCANNED_PDF_UNSUPPORTED in error_codes or (
        ParseErrorCode.NO_TEXT_EXTRACTED in error_codes and file_type == "PDF"
    ):
        return ParserCapability(
            file_type=file_type,
            support_status=DocumentSupportStatus.SCANNED_OR_IMAGE_ONLY,
            triage_status=DocumentTriageStatus.OCR_REQUIRED,
            reason_codes=["ocr_required"],
            requires_ocr=True,
            usable_without_conversion=False,
        )

    if error_codes & set(_OCR_ERROR_REASON_CODES):
        return ParserCapability(
            file_type=file_type,
            support_status=DocumentSupportStatus.SCANNED_OR_IMAGE_ONLY,
            triage_status=DocumentTriageStatus.OCR_REQUIRED,
            reason_codes=[
                _OCR_ERROR_REASON_CODES[error_code]
                for error_code in _OCR_ERROR_REASON_CODES
                if error_code in error_codes
            ],
            requires_ocr=True,
            usable_without_conversion=False,
        )

    if error_codes & set(_MEDIA_ERROR_REASON_CODES):
        return ParserCapability(
            file_type=file_type,
            support_status=DocumentSupportStatus.CONVERSION_REQUIRED,
            triage_status=DocumentTriageStatus.CONVERSION_REQUIRED,
            reason_codes=[
                _MEDIA_ERROR_REASON_CODES[error_code]
                for error_code in _MEDIA_ERROR_REASON_CODES
                if error_code in error_codes
            ],
            requires_conversion=True,
            usable_without_conversion=False,
        )

    if ParseErrorCode.NO_TEXT_EXTRACTED in error_codes:
        return ParserCapability(
            file_type=file_type,
            support_status=DocumentSupportStatus.UNKNOWN,
            triage_status=DocumentTriageStatus.BLOCKED,
            reason_codes=["no_text_extracted"],
            usable_without_conversion=False,
        )

    if error_codes & {ParseErrorCode.CORRUPTED_FILE, ParseErrorCode.INVALID_XLSX}:
        return ParserCapability(
            file_type=file_type,
            support_status=DocumentSupportStatus.CORRUPTED,
            triage_status=DocumentTriageStatus.BLOCKED,
            reason_codes=["corrupted_file"],
            usable_without_conversion=False,
        )

    if ParseErrorCode.UNSUPPORTED_FORMAT in error_codes:
        return ParserCapability(
            file_type=file_type,
            support_status=DocumentSupportStatus.UNSUPPORTED,
            triage_status=DocumentTriageStatus.UNSUPPORTED_SOURCE,
            reason_codes=["unsupported_format"],
            usable_without_conversion=False,
        )

    capability = capability_for_document(
        filename=resolved_filename,
        file_size_bytes=file_size_bytes,
        detected_format=parse_result.doc_type,
        max_bytes=max_bytes,
    )
    if parse_result.warnings:
        capability.warnings.extend(parse_result.warnings)
    return capability


def _normalize_detected_format(
    *,
    data: bytes | None,
    detected_format: str | None,
) -> str | None:
    if detected_format:
        return detected_format.upper()
    if data is not None:
        detected = detect_format(data)
        return detected.upper() if detected else None
    return None


def _infer_file_type(filename: str, detected_format: str | None) -> str:
    if detected_format:
        return detected_format.upper()
    extension = PurePath(filename).suffix.lower()
    if extension:
        return extension.lstrip(".").upper()
    return "UNKNOWN"
