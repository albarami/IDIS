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
import json
import math
import time
import unicodedata
from dataclasses import asdict, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from idis.parsers.base import (
    ParseError,
    ParseErrorCode,
    ParseLimits,
    ParseResult,
    SpanDraft,
)
from idis.parsers.image_only_page import (
    IMAGE_ONLY_DOCUMENT_REVIEW_REQUIRED,
    IMAGE_ONLY_PAGE_RECEIPT_SCHEMA_VERSION,
    LEGACY_IMAGE_ONLY_PAGE_RECEIPT_SCHEMA_VERSION,
    ImageOnlyPageContractError,
    evaluate_image_only_page_receipts,
)
from idis.parsers.ocr import (
    MAX_OCR_IMAGE_PIXELS,
    OCR_RENDER_ADMISSION_SCHEMA_VERSION,
    OCR_RESCUE_MATRIX,
    OCR_RESCUE_POLICY_SHA256,
    OCR_RESCUE_POLICY_VERSION,
    POPPLER_EXACT_COMPARISON_MODE,
    POPPLER_EXPECTED_DIMENSION_POLICY_ID,
    POPPLER_EXPECTED_DIMENSION_POLICY_SHA256,
    OcrConfig,
    OcrError,
    OcrNoConsensusFileProof,
    OcrPageExecutionReceipt,
    OcrPageRenderAdmission,
    OcrPageText,
    OcrRescueNoConsensusError,
    OcrRescueProvenance,
    OcrTimeoutError,
    OcrUnavailableError,
    TrustedImageOnlyPageAdmission,
    TrustedImageOnlyPageAdmissionSet,
    _run_ocr_worker_until,
    ocr_page_execution_receipt_sha256,
    overall_mean_confidence,
    validate_ocr_no_consensus_file_proof,
    validate_partial_ocr_pages,
)
from idis.parsers.poppler_runtime import (
    PinnedPopplerRuntime,
    resolve_pinned_poppler_runtime,
    verify_pinned_poppler_runtime,
)
from idis.parsers.watermark_page_classifier import (
    WatermarkPageClassificationError,
    WatermarkPageEligibility,
    WatermarkPageRuntimeConfig,
    classify_pre_ocr_runtime_watermark_candidate,
    classify_runtime_watermark_page,
)

if TYPE_CHECKING:
    pass

from pypdf import PdfReader
from pypdf.errors import PdfReadError, PdfStreamError

PDF_DIAGNOSTIC_REASON_KEY = "pdf_diagnostic_reason"
PDF_DIAGNOSTIC_PARSED_TEXT = "parsed_text"
PDF_DIAGNOSTIC_PARSED_EMPTY_PASSWORD_ENCRYPTED = "parsed_empty_password_encrypted"
PDF_DIAGNOSTIC_PARSED_PASSWORD_ENCRYPTED = "parsed_password_encrypted"
PDF_DIAGNOSTIC_PARSED_OCR = "parsed_ocr"
PDF_DIAGNOSTIC_SUBPHASE_ELAPSED_KEY = "pdf_subphase_elapsed_seconds"
PDF_ENCRYPTION_DISPOSITION_KEY = "pdf_encryption_disposition"
PDF_ENCRYPTION_NOT_MARKED = "not_encryption_marked"
PDF_ENCRYPTION_EMPTY_CREDENTIAL = "empty_credential_openable"
PDF_ENCRYPTION_CONFIGURED_CREDENTIAL = "configured_credential_openable"
PDF_ENCRYPTION_CREDENTIAL_REQUIRED = "credential_required"
PDF_ENCRYPTION_CREDENTIAL_INVALID = "credential_invalid"
PDF_PARSE_SUBPHASE_READER_INIT = "reader_init"
PDF_PARSE_SUBPHASE_EMPTY_CREDENTIAL_DECRYPT = "decrypt_empty_password"
PDF_PARSE_SUBPHASE_CONFIGURED_CREDENTIAL_DECRYPT = "decrypt_configured_password"
PDF_PARSE_SUBPHASE_PAGE_COUNT = "page_count"
PDF_PARSE_SUBPHASE_TEXT_EXTRACTION_SPAN_BUILD = "text_extraction/span_build"
PDF_OCR_DPI_LADDER_VERSION = "ocr-dpi-ladder-v1"
PDF_OCR_DPI_LADDER = (200, 150, 120, 96, 72)


class _OcrImageTooLargeError(OcrError):
    """One PDF page cannot fit the fixed OCR ladder at its 72-DPI floor."""

    def __init__(self, *, page_number: int, projected_pixel_count: int) -> None:
        super().__init__("PDF page exceeds the OCR image bound at the DPI floor")
        self.page_number = page_number
        self.projected_pixel_count = projected_pixel_count


def _compute_content_hash(text: str) -> str:
    """Compute SHA-256 hash of text content."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_pdf(
    data: bytes,
    limits: ParseLimits | None = None,
    ocr_config: OcrConfig | None = None,
    password: str | None = None,
    source_path: str | Path | None = None,
    watermark_page_config: WatermarkPageRuntimeConfig | None = None,
) -> ParseResult:
    """Parse PDF bytes and extract text spans with page/line locators.

    Args:
        data: Raw PDF file bytes.
        limits: Optional parsing limits (defaults to ParseLimits()).
        ocr_config: Optional explicit OCR execution config.
        password: Optional password resolved from a content-hash keyed secure manifest.
        source_path: Optional already-verified staged path used by file-backed OCR.
        watermark_page_config: Explicit verified watermark classifier material.

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

    encryption_disposition = PDF_ENCRYPTION_NOT_MARKED
    opened_with_empty_password = False
    opened_with_configured_password = False
    if reader.is_encrypted:
        decrypt_started_at = time.monotonic()
        decrypted = _decrypt_with_empty_password(reader)
        subphase_elapsed_seconds[PDF_PARSE_SUBPHASE_EMPTY_CREDENTIAL_DECRYPT] = (
            time.monotonic() - decrypt_started_at
        )
        if not decrypted:
            if password is None:
                return _password_failure_result(
                    disposition="required",
                    encryption_disposition=PDF_ENCRYPTION_CREDENTIAL_REQUIRED,
                    subphase_elapsed_seconds=subphase_elapsed_seconds,
                )
            decrypt_started_at = time.monotonic()
            decrypted = _decrypt_with_password(reader, password)
            subphase_elapsed_seconds[PDF_PARSE_SUBPHASE_CONFIGURED_CREDENTIAL_DECRYPT] = (
                time.monotonic() - decrypt_started_at
            )
            if not decrypted:
                return _password_failure_result(
                    disposition="invalid",
                    encryption_disposition=PDF_ENCRYPTION_CREDENTIAL_INVALID,
                    subphase_elapsed_seconds=subphase_elapsed_seconds,
                )
            opened_with_configured_password = True
            encryption_disposition = PDF_ENCRYPTION_CONFIGURED_CREDENTIAL
        else:
            opened_with_empty_password = True
            encryption_disposition = PDF_ENCRYPTION_EMPTY_CREDENTIAL

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
                encryption_disposition=encryption_disposition,
                subphase_elapsed_seconds=subphase_elapsed_seconds,
            ),
        )

    spans: list[SpanDraft] = []
    total_text_length = 0
    native_text_pages: set[int] = set()
    ocr_required_pages: set[int] = set()
    blank_pages: set[int] = set()

    text_extraction_started_at = time.monotonic()
    for page_idx, page in enumerate(reader.pages):
        page_num = page_idx + 1  # 1-indexed
        try:
            page_text = page.extract_text() or ""
        except Exception:
            return ParseResult(
                doc_type="PDF",
                success=False,
                errors=[
                    ParseError(
                        code=ParseErrorCode.CORRUPTED_FILE,
                        message="PDF page text extraction failed",
                        details={"page_disposition": "text_extraction_failed"},
                    )
                ],
                private_diagnostics=_pdf_private_diagnostics(
                    encryption_disposition=encryption_disposition,
                    subphase_elapsed_seconds=subphase_elapsed_seconds,
                ),
            )

        if not page_text.strip():
            # Fail closed: an empty page is OCR-required even if its resource tree
            # does not expose a directly detectable image XObject.
            ocr_required_pages.add(page_num)
            continue

        native_text_pages.add(page_num)
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

    if ocr_required_pages:
        if ocr_config is None or not ocr_config.enabled:
            unavailable = _ocr_error(
                ParseErrorCode.OCR_UNAVAILABLE,
                "PDF contains image pages but OCR is not configured",
            )
            unavailable.private_diagnostics = _pdf_private_diagnostics(
                encryption_disposition=encryption_disposition,
                subphase_elapsed_seconds=subphase_elapsed_seconds,
            )
            return unavailable
        ocr_result = _parse_pdf_with_ocr(
            data=data,
            source_path=Path(source_path) if source_path is not None else None,
            page_count=total_pages,
            ocr_config=ocr_config,
            required_page_numbers=ocr_required_pages,
            native_spans=spans,
            native_text_length=total_text_length,
            native_text_page_count=len(native_text_pages),
            blank_page_count=len(blank_pages),
            watermark_page_config=watermark_page_config,
        )
        ocr_result.private_diagnostics = {
            **_pdf_private_diagnostics(
                encryption_disposition=encryption_disposition,
                subphase_elapsed_seconds=subphase_elapsed_seconds,
            ),
            **ocr_result.private_diagnostics,
        }
        return ocr_result

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
            private_diagnostics=_pdf_private_diagnostics(
                encryption_disposition=encryption_disposition,
                subphase_elapsed_seconds=subphase_elapsed_seconds,
            ),
        )

    if opened_with_configured_password:
        diagnostic_reason = PDF_DIAGNOSTIC_PARSED_PASSWORD_ENCRYPTED
    elif opened_with_empty_password:
        diagnostic_reason = PDF_DIAGNOSTIC_PARSED_EMPTY_PASSWORD_ENCRYPTED
    else:
        diagnostic_reason = PDF_DIAGNOSTIC_PARSED_TEXT
    return ParseResult(
        doc_type="PDF",
        success=True,
        spans=spans,
        metadata={
            "page_count": total_pages,
            "span_count": len(spans),
            "total_text_length": total_text_length,
            "native_text_page_count": len(native_text_pages),
            "ocr_required_page_count": 0,
            "blank_page_count": len(blank_pages),
        },
        private_diagnostics=_pdf_private_diagnostics(
            reason=diagnostic_reason,
            encryption_disposition=encryption_disposition,
            subphase_elapsed_seconds=subphase_elapsed_seconds,
        ),
    )


def _pdf_private_diagnostics(
    *,
    subphase_elapsed_seconds: dict[str, float],
    reason: str | None = None,
    encryption_disposition: str | None = None,
) -> dict[str, object]:
    diagnostics: dict[str, object] = {}
    if reason is not None:
        diagnostics[PDF_DIAGNOSTIC_REASON_KEY] = reason
    if encryption_disposition is not None:
        diagnostics[PDF_ENCRYPTION_DISPOSITION_KEY] = encryption_disposition
    if subphase_elapsed_seconds:
        diagnostics[PDF_DIAGNOSTIC_SUBPHASE_ELAPSED_KEY] = dict(subphase_elapsed_seconds)
    return diagnostics


def _decrypt_with_empty_password(reader: PdfReader) -> bool:
    """Open PDFs that are encrypted but allow an empty user password."""
    try:
        return bool(reader.decrypt(""))
    except (PdfReadError, PdfStreamError, KeyError, TypeError, ValueError):
        return False


def _decrypt_with_password(reader: PdfReader, password: str) -> bool:
    """Try a configured password without surfacing its value."""
    try:
        return bool(reader.decrypt(password))
    except (PdfReadError, PdfStreamError, KeyError, TypeError, ValueError):
        return False


def _password_failure_result(
    *,
    disposition: str,
    encryption_disposition: str,
    subphase_elapsed_seconds: dict[str, float],
) -> ParseResult:
    """Return one secret-free encrypted-PDF terminal result."""
    return ParseResult(
        doc_type="PDF",
        success=False,
        errors=[
            ParseError(
                code=ParseErrorCode.ENCRYPTED_PDF,
                message="PDF requires a valid configured document password",
                details={"password_disposition": disposition},
            )
        ],
        private_diagnostics=_pdf_private_diagnostics(
            encryption_disposition=encryption_disposition,
            subphase_elapsed_seconds=subphase_elapsed_seconds,
        ),
    )


def _parse_pdf_with_ocr(
    *,
    data: bytes,
    source_path: Path | None,
    page_count: int,
    ocr_config: OcrConfig,
    required_page_numbers: set[int],
    native_spans: list[SpanDraft],
    native_text_length: int,
    native_text_page_count: int,
    blank_page_count: int,
    watermark_page_config: WatermarkPageRuntimeConfig | None = None,
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
    if not required_page_numbers or max(required_page_numbers) > ocr_config.max_pages:
        return _ocr_error(
            ParseErrorCode.OCR_FAILED,
            "OCR page limit does not cover every required image page",
        )
    ocr_deadline = time.monotonic() + ocr_config.timeout_seconds
    content_sha256 = hashlib.sha256(data).hexdigest()
    pre_ocr_classifications: list[dict[str, object]] = []
    remaining_required_pages = set(required_page_numbers)
    render_admissions: tuple[OcrPageRenderAdmission, ...] = ()
    try:
        image_only_admissions = _trusted_image_only_admissions_for_content(
            ocr_config.image_only_page_admissions,
            content_sha256=content_sha256,
        )
        adapter_poppler_runtime = getattr(ocr_config.adapter, "poppler_runtime", None)
        if adapter_poppler_runtime is not None and not isinstance(
            adapter_poppler_runtime, PinnedPopplerRuntime
        ):
            raise OcrError("OCR Poppler runtime binding is invalid")
        if watermark_page_config is not None:
            pre_ocr_classifications = _classify_watermark_before_ocr(
                data=data,
                source_path=source_path,
                page_numbers=tuple(sorted(required_page_numbers)),
                runtime_config=watermark_page_config,
                deadline=ocr_deadline,
                poppler_runtime=adapter_poppler_runtime,
            )
            classified_pages = _validated_pre_ocr_classification_pages(
                classifications=pre_ocr_classifications,
                content_sha256=content_sha256,
                required_page_numbers=required_page_numbers,
            )
            remaining_required_pages -= classified_pages

        if not remaining_required_pages:
            return _compose_pre_ocr_watermark_result(
                page_count=page_count,
                required_page_numbers=required_page_numbers,
                native_spans=native_spans,
                native_text_length=native_text_length,
                native_text_page_count=native_text_page_count,
                blank_page_count=blank_page_count,
                classifications=pre_ocr_classifications,
            )

        if _uses_governed_pdf_render_admission(ocr_config.adapter):
            render_admissions = _build_pdf_ocr_render_admissions(
                data=data,
                page_numbers=tuple(sorted(remaining_required_pages)),
                configured_dpi=_configured_ocr_dpi(ocr_config.adapter),
                poppler_runtime=adapter_poppler_runtime,
            )
            pages = _extract_pdf_pages_with_render_admissions(
                adapter=ocr_config.adapter,
                data=data,
                source_path=source_path,
                max_pages=ocr_config.max_pages,
                deadline=ocr_deadline,
                render_admissions=render_admissions,
                retain_partial_results=bool(image_only_admissions),
            )
            pages = _bind_and_validate_render_admissions(
                pages=pages,
                render_admissions=render_admissions,
            )
        else:
            if pre_ocr_classifications:
                raise OcrError("OCR adapter cannot exclude preclassified watermark pages")
            pages = _extract_pdf_pages_legacy(
                adapter=ocr_config.adapter,
                data=data,
                source_path=source_path,
                max_pages=ocr_config.max_pages,
                timeout_seconds=ocr_config.timeout_seconds,
                retain_partial_results=bool(image_only_admissions),
            )
        parsed = _parse_ocr_pages(
            page_count=page_count,
            max_pages=ocr_config.max_pages,
            pages=pages,
            required_page_numbers=remaining_required_pages,
            native_spans=native_spans,
            native_text_length=native_text_length,
            native_text_page_count=native_text_page_count,
            blank_page_count=blank_page_count,
        )
        if pre_ocr_classifications and parsed.success:
            return _merge_pre_ocr_watermark_classifications(
                parsed=parsed,
                classifications=pre_ocr_classifications,
                total_required_page_count=len(required_page_numbers),
            )
        return parsed
    except _OcrImageTooLargeError as exc:
        return ParseResult(
            doc_type="PDF",
            success=False,
            errors=[
                ParseError(
                    code=ParseErrorCode.OCR_IMAGE_TOO_LARGE,
                    message="PDF page cannot fit the bounded OCR render ladder",
                    details={
                        "page": exc.page_number,
                        "ladder_version": PDF_OCR_DPI_LADDER_VERSION,
                        "floor_dpi": PDF_OCR_DPI_LADDER[-1],
                        "projected_pixel_count": exc.projected_pixel_count,
                        "max_pixel_count": MAX_OCR_IMAGE_PIXELS,
                    },
                )
            ],
        )
    except OcrTimeoutError:
        return _ocr_error(ParseErrorCode.OCR_TIMEOUT, "OCR timed out")
    except OcrUnavailableError:
        return _ocr_error(ParseErrorCode.OCR_UNAVAILABLE, "OCR unavailable")
    except OcrRescueNoConsensusError as exc:
        try:
            _validate_no_consensus_render_admissions(
                error=exc,
                planned_admissions=render_admissions,
            )
            if image_only_admissions:
                result = _parse_pdf_with_disjoint_page_dispositions(
                    data=data,
                    error=exc,
                    page_count=page_count,
                    max_pages=ocr_config.max_pages,
                    required_page_numbers=remaining_required_pages,
                    native_spans=native_spans,
                    native_text_length=native_text_length,
                    native_text_page_count=native_text_page_count,
                    blank_page_count=blank_page_count,
                    classifications=[],
                    admissions=image_only_admissions,
                    preclassified_watermark_pages={
                        cast(int, row["page_number"]) for row in pre_ocr_classifications
                    },
                )
                if pre_ocr_classifications and result.success:
                    return _merge_pre_ocr_watermark_classifications(
                        parsed=result,
                        classifications=pre_ocr_classifications,
                        total_required_page_count=len(required_page_numbers),
                    )
                return result
            return _ocr_no_consensus_result(
                error=exc,
                page_count=page_count,
                required_page_numbers=remaining_required_pages,
                native_span_count=len(native_spans),
                native_text_length=native_text_length,
                native_text_page_count=native_text_page_count,
                blank_page_count=blank_page_count,
            )
        except OcrTimeoutError:
            return _ocr_error(ParseErrorCode.OCR_TIMEOUT, "OCR timed out")
        except (ImageOnlyPageContractError, OcrError):
            return _ocr_error(ParseErrorCode.OCR_FAILED, "OCR failed")
    except OcrError:
        return _ocr_error(ParseErrorCode.OCR_FAILED, "OCR failed")
    except Exception:
        return _ocr_error(ParseErrorCode.OCR_FAILED, "OCR failed")


def _configured_ocr_dpi(adapter: object) -> int:
    value = getattr(adapter, "dpi", PDF_OCR_DPI_LADDER[0])
    if type(value) is not int or value < 1:
        raise OcrError("OCR render DPI is invalid")
    return value


def _uses_governed_pdf_render_admission(adapter: object) -> bool:
    """Select the strict ladder without retrofitting legacy/custom test adapters."""

    try:
        configured_dpi = _configured_ocr_dpi(adapter)
    except OcrError:
        return False
    if configured_dpi != PDF_OCR_DPI_LADDER[0]:
        return False
    return callable(getattr(adapter, "extract_pdf_file_with_render_admissions", None)) or (
        callable(getattr(adapter, "for_dpi", None)) or callable(getattr(adapter, "with_dpi", None))
    )


def _extract_pdf_pages_legacy(
    *,
    adapter: object,
    data: bytes,
    source_path: Path | None,
    max_pages: int,
    timeout_seconds: float,
    retain_partial_results: bool,
) -> list[OcrPageText]:
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise OcrTimeoutError("OCR timed out")
    if source_path is not None:
        extractor_name = (
            "extract_pdf_file_with_partial_results"
            if retain_partial_results
            else "extract_pdf_file"
        )
        extractor = getattr(adapter, extractor_name, None)
        if callable(extractor):
            return cast(
                list[OcrPageText],
                extractor(
                    source_path,
                    max_pages=max_pages,
                    timeout_seconds=timeout_seconds,
                ),
            )
    extractor = getattr(adapter, "extract_pdf_text", None)
    if not callable(extractor):
        raise OcrError("OCR adapter is unavailable")
    return cast(
        list[OcrPageText],
        extractor(data, max_pages=max_pages, timeout_seconds=timeout_seconds),
    )


def _build_pdf_ocr_render_admissions(
    *,
    data: bytes,
    page_numbers: tuple[int, ...],
    configured_dpi: int,
    poppler_runtime: PinnedPopplerRuntime | None = None,
) -> tuple[OcrPageRenderAdmission, ...]:
    """Choose exactly one bounded render DPI for every requested PDF page."""

    if configured_dpi != PDF_OCR_DPI_LADDER[0]:
        # The acceptance ladder is deliberately tied to the governed 200-DPI
        # production configuration. A different configured ceiling is not
        # silently relabelled as the approved ladder.
        raise OcrError("OCR configured DPI does not match the governed ladder")
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not _decrypt_with_empty_password(reader):
            raise OcrError("OCR page dimensions are unavailable")
    except OcrError:
        raise
    except Exception as exc:
        raise OcrError("OCR page dimensions are unavailable") from exc

    admissions: list[OcrPageRenderAdmission] = []
    for page_number in page_numbers:
        if page_number < 1 or page_number > len(reader.pages):
            raise OcrError("OCR page dimensions are unavailable")
        page = reader.pages[page_number - 1]
        try:
            user_unit = float(page.get("/UserUnit", 1))
            width_points = abs(float(page.mediabox.width)) * user_unit
            height_points = abs(float(page.mediabox.height)) * user_unit
            poppler_width_points = _poppler_media_box_extent(page.mediabox[0], page.mediabox[2])
            poppler_height_points = _poppler_media_box_extent(page.mediabox[1], page.mediabox[3])
            rotation = int(page.get("/Rotate", 0)) % 360
        except (TypeError, ValueError, OverflowError) as exc:
            raise OcrError("OCR page dimensions are unavailable") from exc
        if (
            not math.isfinite(width_points)
            or not math.isfinite(height_points)
            or width_points <= 0
            or height_points <= 0
        ):
            raise OcrError("OCR page dimensions are unavailable")
        if rotation not in {0, 90, 180, 270}:
            raise OcrError("OCR page rotation is invalid")
        if rotation in {90, 270}:
            width_points, height_points = height_points, width_points
            poppler_width_points, poppler_height_points = (
                poppler_height_points,
                poppler_width_points,
            )

        chosen: tuple[int, int, int, int] | None = None
        floor_pixel_count = 0
        for dpi in PDF_OCR_DPI_LADDER:
            width_pixels = math.ceil(width_points * dpi / 72)
            height_pixels = math.ceil(height_points * dpi / 72)
            pixel_count = width_pixels * height_pixels
            if dpi == PDF_OCR_DPI_LADDER[-1]:
                floor_pixel_count = pixel_count
            if width_pixels > 0 and height_pixels > 0 and pixel_count <= MAX_OCR_IMAGE_PIXELS:
                chosen = (dpi, width_pixels, height_pixels, pixel_count)
                break
        if chosen is None:
            raise _OcrImageTooLargeError(
                page_number=page_number,
                projected_pixel_count=floor_pixel_count,
            )
        dpi, width_pixels, height_pixels, pixel_count = chosen
        if poppler_runtime is None:
            admissions.append(
                OcrPageRenderAdmission(
                    page_number=page_number,
                    schema_version="ocr-render-admission-v1",
                    ladder_version=PDF_OCR_DPI_LADDER_VERSION,
                    configured_dpi=configured_dpi,
                    chosen_dpi=dpi,
                    projected_width=width_pixels,
                    projected_height=height_pixels,
                    projected_pixel_count=pixel_count,
                    max_pixel_count=MAX_OCR_IMAGE_PIXELS,
                )
            )
            continue

        expected_width = _poppler_expected_axis_pixels(poppler_width_points, dpi=dpi)
        expected_height = _poppler_expected_axis_pixels(poppler_height_points, dpi=dpi)
        admissions.append(
            OcrPageRenderAdmission(
                page_number=page_number,
                schema_version=OCR_RENDER_ADMISSION_SCHEMA_VERSION,
                ladder_version=PDF_OCR_DPI_LADDER_VERSION,
                configured_dpi=configured_dpi,
                chosen_dpi=dpi,
                projected_width=width_pixels,
                projected_height=height_pixels,
                projected_pixel_count=pixel_count,
                max_pixel_count=MAX_OCR_IMAGE_PIXELS,
                expected_render_width=expected_width,
                expected_render_height=expected_height,
                expected_render_pixel_count=expected_width * expected_height,
                poppler_version=poppler_runtime.version,
                poppler_runtime_identity_sha256=(poppler_runtime.runtime_identity_sha256),
                expected_dimension_policy_id=POPPLER_EXPECTED_DIMENSION_POLICY_ID,
                expected_dimension_policy_sha256=(POPPLER_EXPECTED_DIMENSION_POLICY_SHA256),
                comparison_mode=POPPLER_EXACT_COMPARISON_MODE,
                tolerance_pixels=0,
                tolerance_used=False,
            )
        )
    return tuple(admissions)


def _poppler_number_from_normalized_lexeme(value: object) -> float:
    """Replay Poppler 25.12's PDF-number accumulation on a pypdf lexeme.

    Pypdf intentionally normalizes numeric objects, so this does not claim to
    recover arbitrary source spelling.  The strict worker's exact comparison
    remains the fail-closed authority if normalization changes the renderer's
    binary64 result.
    """

    lexeme = str(value).strip()
    negative = lexeme.startswith("-")
    if lexeme[:1] in {"+", "-"}:
        lexeme = lexeme[1:]
    integer_digits, separator, fractional_digits = lexeme.partition(".")
    if (
        not lexeme
        or (not integer_digits and not fractional_digits)
        or (integer_digits and not integer_digits.isascii())
        or (integer_digits and not integer_digits.isdigit())
        or (fractional_digits and not fractional_digits.isascii())
        or (fractional_digits and not fractional_digits.isdigit())
        or (not separator and not integer_digits)
    ):
        raise OcrError("OCR page dimensions are unavailable")
    try:
        parsed = float(int(integer_digits or "0"))
        scale = 0.1
        for digit in fractional_digits:
            parsed += (ord(digit) - ord("0")) * scale
            scale *= 0.1
        if negative:
            parsed = -parsed
    except (OverflowError, ValueError):
        raise OcrError("OCR page dimensions are unavailable") from None
    if not math.isfinite(parsed):
        raise OcrError("OCR page dimensions are unavailable")
    return parsed


def _poppler_media_box_extent(lower: object, upper: object) -> float:
    extent = abs(
        _poppler_number_from_normalized_lexeme(upper)
        - _poppler_number_from_normalized_lexeme(lower)
    )
    if not math.isfinite(extent) or extent <= 0:
        raise OcrError("OCR page dimensions are unavailable")
    return extent


def _poppler_expected_axis_pixels(extent_points: float, *, dpi: int) -> int:
    """Mirror pdftoppm's extent*dpi/72.0 binary64 order, then ceil."""

    scaled = extent_points * dpi / 72.0
    if not math.isfinite(scaled):
        raise OcrError("OCR page dimensions are unavailable")
    pixels = math.ceil(scaled)
    if pixels < 1:
        raise OcrError("OCR page dimensions are unavailable")
    return pixels


def build_pdf_ocr_render_admissions(
    *,
    data: bytes,
    page_numbers: tuple[int, ...],
    configured_dpi: int = PDF_OCR_DPI_LADDER[0],
    poppler_runtime: PinnedPopplerRuntime | None = None,
) -> tuple[OcrPageRenderAdmission, ...]:
    """Derive the production OCR render admissions from immutable PDF geometry.

    The acceptance gate uses this public constructor so its source-bound
    expectation cannot drift from the parser's highest-fitting DPI selection.
    Callers may inject the already-admitted runtime; otherwise this boundary
    resolves the pinned runtime exactly once for the resulting plan.
    """

    if poppler_runtime is not None and not isinstance(poppler_runtime, PinnedPopplerRuntime):
        raise OcrError("OCR Poppler runtime binding is invalid")
    runtime = poppler_runtime or resolve_pinned_poppler_runtime()
    return _build_pdf_ocr_render_admissions(
        data=data,
        page_numbers=page_numbers,
        configured_dpi=configured_dpi,
        poppler_runtime=runtime,
    )


def _extract_pdf_pages_with_render_admissions(
    *,
    adapter: object,
    data: bytes,
    source_path: Path | None,
    max_pages: int,
    deadline: float,
    render_admissions: tuple[OcrPageRenderAdmission, ...],
    retain_partial_results: bool,
) -> list[OcrPageText]:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise OcrTimeoutError("OCR timed out")
    governed_extractor = getattr(adapter, "extract_pdf_file_with_render_admissions", None)
    if source_path is not None and callable(governed_extractor):
        return cast(
            list[OcrPageText],
            governed_extractor(
                source_path,
                render_admissions=render_admissions,
                deadline=deadline,
                retain_partial_results=retain_partial_results,
            ),
        )

    chosen_dpis = {admission.chosen_dpi for admission in render_admissions}
    if len(chosen_dpis) != 1:
        raise OcrError("OCR adapter cannot execute a per-page render plan")
    chosen_dpi = next(iter(chosen_dpis))
    selected_adapter = adapter
    for selector_name in ("for_dpi", "with_dpi"):
        selector = getattr(adapter, selector_name, None)
        if callable(selector):
            selected_adapter = selector(chosen_dpi)
            break
    if _configured_ocr_dpi(selected_adapter) != chosen_dpi:
        raise OcrError("OCR adapter cannot execute the admitted render DPI")

    if source_path is not None:
        extractor_name = (
            "extract_pdf_file_with_partial_results"
            if retain_partial_results
            else "extract_pdf_file"
        )
        extractor = getattr(selected_adapter, extractor_name, None)
        if not callable(extractor):
            raise OcrError("OCR file adapter is unavailable")
        return cast(
            list[OcrPageText],
            extractor(
                source_path,
                max_pages=max_pages,
                timeout_seconds=remaining,
            ),
        )
    extractor = getattr(selected_adapter, "extract_pdf_text", None)
    if not callable(extractor):
        raise OcrError("OCR byte adapter is unavailable")
    return cast(
        list[OcrPageText],
        extractor(data, max_pages=max_pages, timeout_seconds=remaining),
    )


def _bind_and_validate_render_admissions(
    *,
    pages: list[OcrPageText],
    render_admissions: tuple[OcrPageRenderAdmission, ...],
) -> list[OcrPageText]:
    admissions_by_page = {admission.page_number: admission for admission in render_admissions}
    if len(admissions_by_page) != len(render_admissions):
        raise OcrError("OCR render admissions are duplicated")
    bound: list[OcrPageText] = []
    for page in pages:
        admission = admissions_by_page.get(page.page_number)
        if admission is None:
            raise OcrError("OCR returned an unadmitted page")
        observed = getattr(page, "render_admission", None)
        if observed is None:
            if admission.schema_version == OCR_RENDER_ADMISSION_SCHEMA_VERSION:
                raise OcrError("OCR render provenance is incomplete")
            bound.append(replace(page, render_admission=admission))
            continue
        if not admission.same_render_plan(observed) or (
            admission.schema_version == OCR_RENDER_ADMISSION_SCHEMA_VERSION
            and not observed.has_observed_render
        ):
            raise OcrError("OCR render provenance drifted from admission")
        # Preserve the renderer-observed dimensions.  Replacing this receipt
        # with the pre-render plan would erase the proof exact mode just made.
        bound.append(replace(page, render_admission=observed))
    return bound


def _validate_no_consensus_render_admissions(
    *,
    error: OcrRescueNoConsensusError,
    planned_admissions: tuple[OcrPageRenderAdmission, ...],
) -> tuple[OcrPageRenderAdmission, ...]:
    """Cross-bind worker-observed receipts to the parser's immutable render plans."""

    observed_admissions = error.render_admissions
    if not isinstance(observed_admissions, tuple) or any(
        not isinstance(admission, OcrPageRenderAdmission) for admission in observed_admissions
    ):
        raise OcrError("OCR render provenance is malformed")
    if not planned_admissions:
        if observed_admissions:
            raise OcrError("OCR returned unplanned render admissions")
        return ()
    if len(observed_admissions) != len(planned_admissions):
        raise OcrError("OCR render provenance is incomplete")
    for planned, observed in zip(planned_admissions, observed_admissions, strict=True):
        if (
            planned.page_number != observed.page_number
            or not planned.same_render_plan(observed)
            or planned.schema_version != OCR_RENDER_ADMISSION_SCHEMA_VERSION
            or observed.schema_version != OCR_RENDER_ADMISSION_SCHEMA_VERSION
            or not observed.has_observed_render
        ):
            raise OcrError("OCR render provenance drifted from admission")
    return observed_admissions


def _canonical_json_digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _render_admission_metadata(pages: list[OcrPageText]) -> tuple[list[dict[str, object]], str]:
    records = [
        page.render_admission.to_metadata_dict()
        for page in sorted(pages, key=lambda value: value.page_number)
        if page.render_admission is not None
    ]
    return records, _canonical_json_digest(records)


def _validated_pre_ocr_classification_pages(
    *,
    classifications: list[dict[str, object]],
    content_sha256: str,
    required_page_numbers: set[int],
) -> set[int]:
    pages: set[int] = set()
    for classification in classifications:
        page_number = classification.get("page_number")
        if (
            type(page_number) is not int
            or page_number not in required_page_numbers
            or page_number in pages
            or classification.get("schema_version") != "watermark-page-classification-v2"
            or classification.get("classification") != "watermark_only"
            or classification.get("classification_phase") != "pre_ocr"
            or classification.get("full_resolution_ocr_attempted") is not False
            or classification.get("content_sha256") != content_sha256
            or "ocr_terminal_proof_sha256" in classification
        ):
            raise OcrError("Pre-OCR watermark classifications are malformed")
        pages.add(page_number)
    return pages


def _compose_pre_ocr_watermark_result(
    *,
    page_count: int,
    required_page_numbers: set[int],
    native_spans: list[SpanDraft],
    native_text_length: int,
    native_text_page_count: int,
    blank_page_count: int,
    classifications: list[dict[str, object]],
) -> ParseResult:
    metadata: dict[str, object] = {
        "page_count": page_count,
        "span_count": len(native_spans),
        "total_text_length": native_text_length,
        "ocr_performed": False,
        "ocr_page_count": 0,
        "ocr_required_page_count": len(required_page_numbers),
        "native_text_page_count": native_text_page_count,
        "blank_page_count": blank_page_count,
        "ocr_render_admissions": [],
        "ocr_render_admissions_sha256": _canonical_json_digest([]),
        "watermark_preclassified_page_count": len(classifications),
        "watermark_page_classifications": classifications,
    }
    if native_spans:
        return ParseResult(
            doc_type="PDF",
            success=True,
            spans=list(native_spans),
            metadata=metadata,
            private_diagnostics={PDF_DIAGNOSTIC_REASON_KEY: PDF_DIAGNOSTIC_PARSED_TEXT},
        )
    return ParseResult(
        doc_type="PDF",
        success=False,
        spans=[],
        metadata=metadata,
        errors=[
            ParseError(
                code=ParseErrorCode.OCR_NO_TEXT_EXTRACTED,
                message="PDF page classified watermark-only before OCR",
                details={"classified_page_count": len(classifications)},
            )
        ],
        private_diagnostics={PDF_DIAGNOSTIC_REASON_KEY: "classified_watermark_only_pre_ocr"},
    )


def _merge_pre_ocr_watermark_classifications(
    *,
    parsed: ParseResult,
    classifications: list[dict[str, object]],
    total_required_page_count: int,
) -> ParseResult:
    parsed.metadata = {
        **parsed.metadata,
        "ocr_required_page_count": total_required_page_count,
        "watermark_preclassified_page_count": len(classifications),
        "watermark_page_classifications": classifications,
    }
    return parsed


def _classify_watermark_before_ocr(
    *,
    data: bytes,
    source_path: Path | None,
    page_numbers: tuple[int, ...],
    runtime_config: WatermarkPageRuntimeConfig,
    deadline: float,
    poppler_runtime: PinnedPopplerRuntime | None = None,
) -> list[dict[str, object]]:
    if not isinstance(runtime_config, WatermarkPageRuntimeConfig):
        raise OcrError("Watermark page configuration is untrusted")
    payload = _run_ocr_worker_until(
        target=_watermark_page_pre_ocr_worker,
        args=(
            data if source_path is None else None,
            str(source_path) if source_path is not None else None,
            hashlib.sha256(data).hexdigest(),
            page_numbers,
            runtime_config,
            poppler_runtime,
        ),
        deadline=deadline,
        worker_uses_execution_deadline=True,
    )
    if payload.get("status") == "timeout":
        raise OcrTimeoutError("OCR timed out")
    if payload.get("status") == "unavailable":
        raise OcrError("Watermark page renderer is unavailable")
    if set(payload) != {"status", "classifications"} or payload.get("status") != "success":
        raise OcrError("Watermark page classification failed")
    classifications = payload.get("classifications")
    if not isinstance(classifications, list) or any(
        not isinstance(classification, dict) for classification in classifications
    ):
        raise OcrError("Watermark page classifier returned malformed results")
    return classifications


def _watermark_page_pre_ocr_worker(
    data: bytes | None,
    source_path: str | None,
    content_sha256: str,
    page_numbers: tuple[int, ...],
    runtime_config: WatermarkPageRuntimeConfig,
    poppler_runtime: PinnedPopplerRuntime | None,
    execution_deadline: float,
    queue: object,
) -> None:
    """Render fixed 512x512 candidates in one killable pre-OCR worker."""

    def put(payload: dict[str, object]) -> None:
        sender = getattr(queue, "put", None)
        if callable(sender):
            sender(payload)

    try:
        if (source_path is None) == (data is None):
            put({"status": "failed"})
            return
        try:
            from pdf2image import convert_from_bytes, convert_from_path
        except ImportError:
            put({"status": "unavailable"})
            return
        render_from_bytes = cast(Any, convert_from_bytes)
        render_from_path = cast(Any, convert_from_path)
        poppler_path: str | None = None
        if poppler_runtime is not None:
            verify_pinned_poppler_runtime(poppler_runtime)
            poppler_path = str(poppler_runtime.binary_dir)
        classifications: list[dict[str, object]] = []
        for page_number in page_numbers:
            remaining = execution_deadline - time.monotonic()
            if remaining < 1:
                put({"status": "timeout"})
                return
            rendered = (
                render_from_path(
                    source_path,
                    first_page=page_number,
                    last_page=page_number,
                    fmt="png",
                    grayscale=True,
                    thread_count=1,
                    size=(512, 512),
                    timeout=int(remaining),
                    poppler_path=poppler_path,
                )
                if source_path is not None
                else render_from_bytes(
                    cast(bytes, data),
                    first_page=page_number,
                    last_page=page_number,
                    fmt="png",
                    grayscale=True,
                    thread_count=1,
                    size=(512, 512),
                    timeout=int(remaining),
                    poppler_path=poppler_path,
                )
            )
            if not isinstance(rendered, list) or len(rendered) != 1:
                for malformed in rendered if isinstance(rendered, list) else []:
                    close = getattr(malformed, "close", None)
                    if callable(close):
                        close()
                put({"status": "failed"})
                return
            image = rendered[0]
            try:
                if image.size != (512, 512) or time.monotonic() >= execution_deadline:
                    put(
                        {
                            "status": (
                                "timeout" if time.monotonic() >= execution_deadline else "failed"
                            )
                        }
                    )
                    return
                try:
                    result = classify_pre_ocr_runtime_watermark_candidate(
                        candidate_image=image,
                        page_number=page_number,
                        content_sha256=content_sha256,
                        native_text_character_count=0,
                        runtime_config=runtime_config,
                    )
                except WatermarkPageClassificationError as exc:
                    if exc.code == "WATERMARK_PAGE_CLASSIFICATION_BELOW_THRESHOLD":
                        continue
                    put({"status": "failed"})
                    return
                classifications.append(result.to_dict())
            finally:
                close = getattr(image, "close", None)
                if callable(close):
                    close()
        put({"status": "success", "classifications": classifications})
    except Exception:
        put({"status": "failed"})


def _classify_watermark_no_consensus_pages(
    *,
    data: bytes,
    source_path: Path | None,
    error: OcrRescueNoConsensusError,
    required_page_numbers: set[int],
    runtime_config: WatermarkPageRuntimeConfig | None,
    deadline: float,
    projected_pixel_counts: dict[int, int] | None = None,
    poppler_runtime: PinnedPopplerRuntime | None = None,
) -> list[dict[str, object]]:
    """Classify eligible unresolved pages under the original OCR wall clock."""

    if runtime_config is None:
        return []
    if not isinstance(runtime_config, WatermarkPageRuntimeConfig):
        raise OcrError("Watermark page configuration is untrusted")
    proof = _validated_no_consensus_proof(error)
    if proof is None:
        raise OcrError("Watermark classification requires complete OCR proof")
    proof_by_page = {receipt.page_number: receipt for receipt in proof.page_receipts}
    page_numbers = tuple(
        page_number
        for page_number in sorted(required_page_numbers)
        if proof_by_page.get(page_number) is not None
        and proof_by_page[page_number].outcome == "no_consensus"
    )
    if not page_numbers:
        return []

    admitted_pixel_counts = (
        _validated_watermark_render_pixel_counts(
            data=data,
            page_numbers=page_numbers,
            render_dpi=runtime_config.policy.render_dpi,
        )
        if projected_pixel_counts is None
        else _validate_supplied_watermark_render_pixel_counts(
            projected_pixel_counts,
            page_numbers=page_numbers,
        )
    )
    if time.monotonic() >= deadline:
        raise OcrTimeoutError("OCR timed out")

    jobs = tuple(
        (
            page_number,
            proof_by_page[page_number].primary_token_count,
            proof_by_page[page_number].rescue_attempted_candidate_count,
            proof_by_page[page_number].rescue_completed_candidate_count,
            proof_by_page[page_number].outcome,
            ocr_page_execution_receipt_sha256(proof_by_page[page_number]),
        )
        for page_number in page_numbers
    )
    payload = _run_ocr_worker_until(
        target=_watermark_page_runtime_worker,
        args=(
            data if source_path is None else None,
            str(source_path) if source_path is not None else None,
            hashlib.sha256(data).hexdigest(),
            jobs,
            admitted_pixel_counts,
            runtime_config,
            poppler_runtime,
        ),
        deadline=deadline,
        worker_uses_execution_deadline=True,
    )
    status = payload.get("status")
    if status == "timeout":
        raise OcrTimeoutError("OCR timed out")
    if status == "unavailable":
        raise OcrError("Watermark page renderer is unavailable")
    if status != "success" or set(payload) != {"status", "classifications"}:
        raise OcrError("Watermark page classification failed")
    classifications = payload.get("classifications")
    if not isinstance(classifications, list) or any(
        not isinstance(classification, dict) for classification in classifications
    ):
        raise OcrError("Watermark page classifier returned malformed results")
    return classifications


def _validate_supplied_watermark_render_pixel_counts(
    value: dict[int, int],
    *,
    page_numbers: tuple[int, ...],
) -> dict[int, int]:
    if set(value) != set(page_numbers) or any(
        type(page_number) is not int
        or type(pixel_count) is not int
        or pixel_count < 1
        or pixel_count > MAX_OCR_IMAGE_PIXELS
        for page_number, pixel_count in value.items()
    ):
        raise OcrError("Watermark page render dimensions exceed the OCR resource bound")
    return dict(value)


def _validated_watermark_render_pixel_counts(
    *,
    data: bytes,
    page_numbers: tuple[int, ...],
    render_dpi: int,
) -> dict[int, int]:
    """Admit declared PDF geometry before any Poppler raster is materialized."""

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not _decrypt_with_empty_password(reader):
            raise OcrError("Watermark page dimensions are unavailable")
        return _watermark_render_pixel_counts_from_reader(
            reader=reader,
            page_numbers=page_numbers,
            render_dpi=render_dpi,
        )
    except OcrError:
        raise
    except Exception as exc:
        raise OcrError("Watermark page dimensions are unavailable") from exc


def _watermark_render_pixel_counts_from_reader(
    *,
    reader: PdfReader,
    page_numbers: tuple[int, ...],
    render_dpi: int,
) -> dict[int, int]:
    if type(render_dpi) is not int or render_dpi < 1:
        raise OcrError("Watermark render DPI is invalid")
    counts: dict[int, int] = {}
    for page_number in page_numbers:
        if page_number < 1 or page_number > len(reader.pages):
            raise OcrError("Watermark page dimensions are unavailable")
        page = reader.pages[page_number - 1]
        try:
            user_unit = float(page.get("/UserUnit", 1))
            width_points = abs(float(page.mediabox.width)) * user_unit
            height_points = abs(float(page.mediabox.height)) * user_unit
        except (TypeError, ValueError, OverflowError) as exc:
            raise OcrError("Watermark page dimensions are unavailable") from exc
        if (
            not math.isfinite(width_points)
            or not math.isfinite(height_points)
            or width_points <= 0
            or height_points <= 0
        ):
            raise OcrError("Watermark page dimensions are unavailable")
        width_pixels = math.ceil(width_points * render_dpi / 72)
        height_pixels = math.ceil(height_points * render_dpi / 72)
        pixel_count = width_pixels * height_pixels
        if pixel_count < 1 or pixel_count > MAX_OCR_IMAGE_PIXELS:
            raise OcrError("Watermark page render dimensions exceed the OCR resource bound")
        counts[page_number] = pixel_count
    return counts


def _watermark_page_runtime_worker(
    data: bytes | None,
    source_path: str | None,
    content_sha256: str,
    jobs: tuple[tuple[int, int, int, int, str, str], ...],
    admitted_pixel_counts: dict[int, int],
    runtime_config: WatermarkPageRuntimeConfig,
    poppler_runtime: PinnedPopplerRuntime | None,
    execution_deadline: float,
    queue: object,
) -> None:
    """Render and classify pages only inside one killable, deadline-bound worker."""

    def put(payload: dict[str, object]) -> None:
        sender = getattr(queue, "put", None)
        if callable(sender):
            sender(payload)

    try:
        if (source_path is None) == (data is None):
            put({"status": "failed"})
            return
        try:
            from pdf2image import convert_from_bytes, convert_from_path
        except ImportError:
            put({"status": "unavailable"})
            return
        render_from_bytes = cast(Any, convert_from_bytes)
        render_from_path = cast(Any, convert_from_path)
        poppler_path: str | None = None
        if poppler_runtime is not None:
            verify_pinned_poppler_runtime(poppler_runtime)
            poppler_path = str(poppler_runtime.binary_dir)
        classifications: list[dict[str, object]] = []
        for (
            page_number,
            primary_token_count,
            rescue_candidate_count,
            rescue_completed_candidate_count,
            rescue_outcome,
            terminal_proof_sha256,
        ) in jobs:
            remaining = execution_deadline - time.monotonic()
            if remaining < 1:
                put({"status": "timeout"})
                return
            rendered = (
                render_from_path(
                    source_path,
                    dpi=runtime_config.policy.render_dpi,
                    first_page=page_number,
                    last_page=page_number,
                    fmt="png",
                    grayscale=True,
                    thread_count=1,
                    timeout=int(remaining),
                    poppler_path=poppler_path,
                )
                if source_path is not None
                else render_from_bytes(
                    cast(bytes, data),
                    dpi=runtime_config.policy.render_dpi,
                    first_page=page_number,
                    last_page=page_number,
                    fmt="png",
                    grayscale=True,
                    thread_count=1,
                    timeout=int(remaining),
                    poppler_path=poppler_path,
                )
            )
            if not isinstance(rendered, list) or len(rendered) != 1:
                for malformed_image in rendered if isinstance(rendered, list) else []:
                    close = getattr(malformed_image, "close", None)
                    if callable(close):
                        close()
                put({"status": "failed"})
                return
            image = rendered[0]
            try:
                actual_pixels = int(image.width) * int(image.height)
                if (
                    actual_pixels < 1
                    or actual_pixels > MAX_OCR_IMAGE_PIXELS
                    or page_number not in admitted_pixel_counts
                    or time.monotonic() >= execution_deadline
                ):
                    put(
                        {
                            "status": (
                                "timeout" if time.monotonic() >= execution_deadline else "failed"
                            )
                        }
                    )
                    return
                try:
                    classification = classify_runtime_watermark_page(
                        page_image=image,
                        render_dpi=runtime_config.policy.render_dpi,
                        page_number=page_number,
                        content_sha256=content_sha256,
                        eligibility=WatermarkPageEligibility(
                            native_text_character_count=0,
                            primary_ocr_token_count=primary_token_count,
                            rescue_candidate_count=rescue_candidate_count,
                            rescue_completed_candidate_count=(rescue_completed_candidate_count),
                            rescue_outcome=rescue_outcome,
                        ),
                        ocr_terminal_proof_sha256=terminal_proof_sha256,
                        runtime_config=runtime_config,
                    )
                except WatermarkPageClassificationError as exc:
                    if exc.code == "WATERMARK_PAGE_CLASSIFICATION_BELOW_THRESHOLD":
                        continue
                    put({"status": "failed"})
                    return
                classifications.append(classification)
            finally:
                close = getattr(image, "close", None)
                if callable(close):
                    close()
            if time.monotonic() >= execution_deadline:
                put({"status": "timeout"})
                return
        put({"status": "success", "classifications": classifications})
    except Exception:
        put({"status": "failed"})


def _parse_pdf_with_disjoint_page_dispositions(
    *,
    data: bytes,
    error: OcrRescueNoConsensusError,
    page_count: int,
    max_pages: int,
    required_page_numbers: set[int],
    native_spans: list[SpanDraft],
    native_text_length: int,
    native_text_page_count: int,
    blank_page_count: int,
    classifications: list[dict[str, object]],
    admissions: tuple[TrustedImageOnlyPageAdmission, ...],
    preclassified_watermark_pages: set[int] | None = None,
) -> ParseResult:
    """Compose disjoint watermark and reviewed-image page terminal states."""

    proof = _validated_no_consensus_proof(error)
    if proof is None:
        raise OcrError("Page disposition requires complete OCR proof")
    if tuple(receipt.page_number for receipt in proof.page_receipts) != tuple(
        sorted(required_page_numbers)
    ):
        raise OcrError("OCR proof does not cover every required PDF page")
    accepted_pages = validate_partial_ocr_pages(pages=error.accepted_pages, proof=proof)
    proof_by_page = {receipt.page_number: receipt for receipt in proof.page_receipts}
    unresolved_required_pages = {
        page_number
        for page_number in required_page_numbers
        if proof_by_page[page_number].outcome == "no_consensus"
    }
    content_sha256 = hashlib.sha256(data).hexdigest()
    classification_pages = [row.get("page_number") for row in classifications]
    if (
        any(type(page_number) is not int for page_number in classification_pages)
        or len(classification_pages) != len(set(classification_pages))
        or any(row.get("content_sha256") != content_sha256 for row in classifications)
    ):
        raise OcrError("Watermark classifications are malformed")
    watermark_pages = {cast(int, page_number) for page_number in classification_pages}
    preclassified_pages = preclassified_watermark_pages or set()
    if (
        any(
            type(page_number) is not int or not 1 <= page_number <= page_count
            for page_number in preclassified_pages
        )
        or preclassified_pages & required_page_numbers
    ):
        raise OcrError("Preclassified watermark pages are inconsistent")
    admissions_by_page = {admission.page_number: admission for admission in admissions}
    image_only_pages = set(admissions_by_page)
    if (
        watermark_pages & image_only_pages
        or watermark_pages | image_only_pages != unresolved_required_pages
    ):
        raise OcrError("Page dispositions overlap or do not cover every unresolved page")

    image_only_receipts: list[dict[str, object]] = []
    if image_only_pages:
        render_admissions_by_page = {
            admission.page_number: admission for admission in error.render_admissions
        }
        page_content_sha256s = dict(error.page_content_sha256s)
        all_no_consensus_pages = {
            receipt.page_number
            for receipt in proof.page_receipts
            if receipt.outcome == "no_consensus"
        }
        if (
            len(page_content_sha256s) != len(error.page_content_sha256s)
            or set(page_content_sha256s) != all_no_consensus_pages
        ):
            raise OcrError("OCR page-content proof is incomplete or mismatched")
        image_only_receipts = [
            _image_only_page_receipt(
                admission=admissions_by_page[page_number],
                page_proof=proof_by_page[page_number],
                page_content_sha256=page_content_sha256s[page_number],
                content_sha256=content_sha256,
                render_admission=render_admissions_by_page.get(page_number),
            )
            for page_number in sorted(image_only_pages)
        ]

    accepted_required_page_numbers = required_page_numbers - unresolved_required_pages
    accepted_required_pages = [
        page for page in accepted_pages if page.page_number in accepted_required_page_numbers
    ]
    if {page.page_number for page in accepted_required_pages} != accepted_required_page_numbers:
        raise OcrError("OCR accepted-page proof is incomplete")
    if accepted_required_page_numbers:
        substantive = _parse_ocr_pages(
            page_count=page_count,
            max_pages=max_pages,
            pages=accepted_required_pages,
            required_page_numbers=accepted_required_page_numbers,
            native_spans=native_spans,
            native_text_length=native_text_length,
            native_text_page_count=native_text_page_count,
            blank_page_count=blank_page_count,
        )
        if not substantive.success:
            return substantive
    else:
        substantive = ParseResult(
            doc_type="PDF",
            success=True,
            spans=list(native_spans),
            metadata={
                "page_count": page_count,
                "span_count": len(native_spans),
                "total_text_length": native_text_length,
                "ocr_performed": True,
                "ocr_page_count": len(required_page_numbers),
                "ocr_required_page_count": len(required_page_numbers),
                "native_text_page_count": native_text_page_count,
                "blank_page_count": blank_page_count,
                "ocr_mean_confidence": None,
                "ocr_confidence_by_page": [],
                "ocr_rescue_page_count": 0,
                "ocr_rescue_span_count": 0,
                "ocr_rescue_receipt_sha256": _ocr_rescue_receipt_sha256([]),
            },
            private_diagnostics={PDF_DIAGNOSTIC_REASON_KEY: PDF_DIAGNOSTIC_PARSED_OCR},
        )

    metadata = {
        **substantive.metadata,
        "ocr_page_count": len(required_page_numbers),
        "ocr_required_page_count": len(required_page_numbers),
        "ocr_rescue_policy_version": OCR_RESCUE_POLICY_VERSION,
        "ocr_rescue_policy_sha256": OCR_RESCUE_POLICY_SHA256,
        "ocr_no_consensus_page_receipts": [receipt.to_payload() for receipt in proof.page_receipts],
        "ocr_result_sha256": proof.ocr_result_sha256,
    }
    if error.render_admissions:
        observed_records = [admission.to_metadata_dict() for admission in error.render_admissions]
        metadata["ocr_render_admissions"] = observed_records
        metadata["ocr_render_admissions_sha256"] = _canonical_json_digest(observed_records)
    if classifications:
        metadata["watermark_page_classifications"] = classifications
    if image_only_receipts:
        if not substantive.spans and watermark_pages:
            raise OcrError("Mixed terminal pages have no substantive parent span")
        evaluation = evaluate_image_only_page_receipts(
            receipts=image_only_receipts,
            content_sha256=content_sha256,
            page_count=page_count,
            durable_substantive_spans=_durable_span_proofs(substantive.spans),
            excluded_watermark_page_numbers=tuple(sorted(watermark_pages | preclassified_pages)),
        )
        metadata["image_only_page_receipts"] = image_only_receipts
        metadata["image_only_page_evaluation"] = evaluation
        if evaluation["may_proceed"] is not True:
            return ParseResult(
                doc_type="PDF",
                success=False,
                spans=[],
                metadata=metadata,
                errors=[
                    ParseError(
                        code=ParseErrorCode.OCR_NO_TEXT_EXTRACTED,
                        message="Reviewed image-only PDF has no substantive text spans",
                        details={"terminal_code": IMAGE_ONLY_DOCUMENT_REVIEW_REQUIRED},
                    )
                ],
                private_diagnostics=substantive.private_diagnostics,
            )
    if substantive.spans:
        substantive.metadata = metadata
        return substantive
    return ParseResult(
        doc_type="PDF",
        success=False,
        spans=[],
        metadata=metadata,
        errors=[
            ParseError(
                code=ParseErrorCode.OCR_RESCUE_NO_CONSENSUS,
                message="OCR rescue produced no unique consensus; page classified watermark-only",
                details={"classified_page_count": len(classifications)},
            )
        ],
        private_diagnostics=substantive.private_diagnostics,
    )


def _parse_pdf_with_watermark_classifications(
    *,
    data: bytes,
    error: OcrRescueNoConsensusError,
    page_count: int,
    max_pages: int,
    required_page_numbers: set[int],
    native_spans: list[SpanDraft],
    native_text_length: int,
    native_text_page_count: int,
    blank_page_count: int,
    classifications: list[dict[str, object]],
) -> ParseResult:
    """Exclude classified pages while retaining independently extracted spans."""

    proof = _validated_no_consensus_proof(error)
    if proof is None:
        raise OcrError("Watermark classification requires complete OCR proof")
    if tuple(receipt.page_number for receipt in proof.page_receipts) != tuple(
        sorted(required_page_numbers)
    ):
        raise OcrError("OCR proof does not cover every required PDF page")
    accepted_pages = validate_partial_ocr_pages(pages=error.accepted_pages, proof=proof)
    proof_by_page = {receipt.page_number: receipt for receipt in proof.page_receipts}
    unresolved_required_pages = {
        page_number
        for page_number in required_page_numbers
        if proof_by_page[page_number].outcome == "no_consensus"
    }
    classification_pages = [row.get("page_number") for row in classifications]
    if (
        any(type(page_number) is not int for page_number in classification_pages)
        or len(classification_pages) != len(set(classification_pages))
        or set(classification_pages) != unresolved_required_pages
        or any(
            row.get("content_sha256") != hashlib.sha256(data).hexdigest() for row in classifications
        )
    ):
        raise OcrError("Watermark classifications do not cover the unresolved pages")

    accepted_required_page_numbers = required_page_numbers - unresolved_required_pages
    accepted_required_pages = [
        page for page in accepted_pages if page.page_number in accepted_required_page_numbers
    ]
    if {page.page_number for page in accepted_required_pages} != accepted_required_page_numbers:
        raise OcrError("OCR accepted-page proof is incomplete")

    if accepted_required_page_numbers:
        substantive = _parse_ocr_pages(
            page_count=page_count,
            max_pages=max_pages,
            pages=accepted_required_pages,
            required_page_numbers=accepted_required_page_numbers,
            native_spans=native_spans,
            native_text_length=native_text_length,
            native_text_page_count=native_text_page_count,
            blank_page_count=blank_page_count,
        )
        if not substantive.success:
            return substantive
    else:
        substantive = ParseResult(
            doc_type="PDF",
            success=True,
            spans=list(native_spans),
            metadata={
                "page_count": page_count,
                "span_count": len(native_spans),
                "total_text_length": native_text_length,
                "ocr_performed": True,
                "ocr_page_count": len(required_page_numbers),
                "ocr_required_page_count": len(required_page_numbers),
                "native_text_page_count": native_text_page_count,
                "blank_page_count": blank_page_count,
                "ocr_mean_confidence": None,
                "ocr_confidence_by_page": [],
                "ocr_rescue_page_count": 0,
                "ocr_rescue_span_count": 0,
                "ocr_rescue_receipt_sha256": _ocr_rescue_receipt_sha256([]),
            },
            private_diagnostics={PDF_DIAGNOSTIC_REASON_KEY: PDF_DIAGNOSTIC_PARSED_OCR},
        )

    metadata = {
        **substantive.metadata,
        "ocr_page_count": len(required_page_numbers),
        "ocr_required_page_count": len(required_page_numbers),
        "ocr_rescue_policy_version": OCR_RESCUE_POLICY_VERSION,
        "ocr_rescue_policy_sha256": OCR_RESCUE_POLICY_SHA256,
        "ocr_no_consensus_page_receipts": [receipt.to_payload() for receipt in proof.page_receipts],
        "ocr_result_sha256": proof.ocr_result_sha256,
        "watermark_page_classifications": classifications,
    }
    if substantive.spans:
        substantive.metadata = metadata
        return substantive
    return ParseResult(
        doc_type="PDF",
        success=False,
        spans=[],
        metadata=metadata,
        errors=[
            ParseError(
                code=ParseErrorCode.OCR_RESCUE_NO_CONSENSUS,
                message="OCR rescue produced no unique consensus; page classified watermark-only",
                details={"classified_page_count": len(classifications)},
            )
        ],
        private_diagnostics=substantive.private_diagnostics,
    )


def _trusted_image_only_admissions_for_content(
    value: object,
    *,
    content_sha256: str,
) -> tuple[TrustedImageOnlyPageAdmission, ...]:
    if value is None:
        return ()
    if not isinstance(value, TrustedImageOnlyPageAdmissionSet):
        raise OcrError("Reviewed image-only page admissions are untrusted")
    admissions = tuple(
        admission for admission in value.admissions if admission.content_sha256 == content_sha256
    )
    if len({admission.page_number for admission in admissions}) != len(admissions):
        raise OcrError("Reviewed image-only page admissions are duplicated")
    return admissions


def _canonical_json_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _safe_image_only_page_locator(
    *,
    content_sha256: str,
    page_number: int,
) -> dict[str, object]:
    scheme = "safe-pdf-page-v1"
    digest = hashlib.sha256(
        f"{content_sha256}\x00{scheme}\x00{page_number}".encode("ascii")
    ).hexdigest()
    return {
        "scheme": scheme,
        "page_number": page_number,
        "locator_sha256": digest,
    }


def _image_only_page_receipt(
    *,
    admission: TrustedImageOnlyPageAdmission,
    page_proof: OcrPageExecutionReceipt,
    page_content_sha256: str,
    content_sha256: str,
    render_admission: OcrPageRenderAdmission | None = None,
) -> dict[str, object]:
    proof_sha256 = ocr_page_execution_receipt_sha256(page_proof)
    if (
        page_proof.page_number != admission.page_number
        or page_proof.outcome != "no_consensus"
        or page_proof.primary_completed is not True
        or page_proof.primary_token_count != 0
        or page_proof.accepted_text is not False
        or page_proof.accepted_text_token_count != 0
        or page_proof.accepted_text_span_count != 0
        or page_proof.accepted_text_codepoint_count != 0
        or proof_sha256 != admission.ocr_terminal_proof_sha256
        or page_content_sha256 != admission.page_content_sha256
    ):
        raise OcrError("Reviewed image-only page admission does not match fresh OCR proof")
    if render_admission is not None and (
        render_admission.page_number != admission.page_number
        or render_admission.schema_version != OCR_RENDER_ADMISSION_SCHEMA_VERSION
        or not render_admission.has_observed_render
    ):
        raise OcrError("Reviewed image-only page render proof is malformed")
    receipt: dict[str, object] = {
        "schema_version": (
            IMAGE_ONLY_PAGE_RECEIPT_SCHEMA_VERSION
            if render_admission is not None
            else LEGACY_IMAGE_ONLY_PAGE_RECEIPT_SCHEMA_VERSION
        ),
        "classification": "image_only_page",
        "content_sha256": content_sha256,
        "page_number": admission.page_number,
        "page_content_sha256": page_content_sha256,
        "safe_page_locator": _safe_image_only_page_locator(
            content_sha256=content_sha256,
            page_number=admission.page_number,
        ),
        "native_text_character_count": 0,
        "primary_ocr_token_count": 0,
        "rescue_accepted_token_count": 0,
        "ocr_terminal_proof_sha256": proof_sha256,
        "watermark_classifier_outcome": "BELOW_THRESHOLD",
        "watermark_classifier_receipt_sha256": (admission.watermark_classifier_receipt_sha256),
        "emitted_span_count": 0,
        "emitted_text_character_count": 0,
        "occurrence_scope": "REVIEWER_CONFIRMED",
        "review_status": "REVIEWED_SUBSTANTIVE_IMAGE",
    }
    if render_admission is not None:
        receipt["ocr_page_execution_receipt"] = page_proof.to_payload()
        receipt["ocr_render_admission"] = render_admission.to_span_dict()
    return receipt


def _durable_span_proofs(spans: list[SpanDraft]) -> list[dict[str, object]]:
    proofs: list[dict[str, object]] = []
    for span in spans:
        page_number = span.locator.get("page")
        if (
            isinstance(page_number, bool)
            or not isinstance(page_number, int)
            or page_number < 1
            or span.content_hash is None
        ):
            raise OcrError("PDF substantive span proof is malformed")
        proofs.append(
            {
                "page_number": page_number,
                "span_type": span.span_type,
                "content_hash": span.content_hash,
                "locator_sha256": _canonical_json_sha256(span.locator),
                # Ingestion persists the returned metadata and spans atomically;
                # a failed persistence transaction cannot publish this evaluation.
                "persistence_status": "DURABLE",
            }
        )
    return proofs


def _parse_pdf_with_image_only_admissions(
    *,
    data: bytes,
    error: OcrRescueNoConsensusError,
    page_count: int,
    max_pages: int,
    required_page_numbers: set[int],
    native_spans: list[SpanDraft],
    native_text_length: int,
    native_text_page_count: int,
    blank_page_count: int,
    admissions: tuple[TrustedImageOnlyPageAdmission, ...],
) -> ParseResult:
    proof = _validated_no_consensus_proof(error)
    if proof is None:
        raise OcrError("Reviewed image-only page admission requires complete OCR proof")
    if tuple(receipt.page_number for receipt in proof.page_receipts) != tuple(
        range(1, page_count + 1)
    ):
        raise OcrError("OCR proof does not cover every PDF page")
    accepted_pages = validate_partial_ocr_pages(
        pages=error.accepted_pages,
        proof=proof,
    )
    proof_by_page = {receipt.page_number: receipt for receipt in proof.page_receipts}
    unresolved_required_pages = {
        page_number
        for page_number in required_page_numbers
        if proof_by_page[page_number].outcome == "no_consensus"
    }
    admissions_by_page = {admission.page_number: admission for admission in admissions}
    if set(admissions_by_page) != unresolved_required_pages:
        raise OcrError("Reviewed image-only page admissions are incomplete or mismatched")
    page_content_sha256s = dict(error.page_content_sha256s)
    all_no_consensus_pages = {
        receipt.page_number for receipt in proof.page_receipts if receipt.outcome == "no_consensus"
    }
    if (
        len(page_content_sha256s) != len(error.page_content_sha256s)
        or set(page_content_sha256s) != all_no_consensus_pages
    ):
        raise OcrError("OCR page-content proof is incomplete or mismatched")

    content_sha256 = hashlib.sha256(data).hexdigest()
    render_admissions_by_page = {
        admission.page_number: admission for admission in error.render_admissions
    }
    image_only_receipts = [
        _image_only_page_receipt(
            admission=admissions_by_page[page_number],
            page_proof=proof_by_page[page_number],
            page_content_sha256=page_content_sha256s[page_number],
            content_sha256=content_sha256,
            render_admission=render_admissions_by_page.get(page_number),
        )
        for page_number in sorted(unresolved_required_pages)
    ]
    accepted_required_page_numbers = required_page_numbers - unresolved_required_pages
    accepted_required_pages = [
        page for page in accepted_pages if page.page_number in accepted_required_page_numbers
    ]
    if {page.page_number for page in accepted_required_pages} != accepted_required_page_numbers:
        raise OcrError("OCR accepted-page proof is incomplete")

    if accepted_required_page_numbers:
        substantive = _parse_ocr_pages(
            page_count=page_count,
            max_pages=max_pages,
            pages=accepted_required_pages,
            required_page_numbers=accepted_required_page_numbers,
            native_spans=native_spans,
            native_text_length=native_text_length,
            native_text_page_count=native_text_page_count,
            blank_page_count=blank_page_count,
        )
        if not substantive.success:
            return substantive
    else:
        substantive = ParseResult(
            doc_type="PDF",
            success=True,
            spans=list(native_spans),
            metadata={
                "page_count": page_count,
                "span_count": len(native_spans),
                "total_text_length": native_text_length,
                "ocr_performed": True,
                "ocr_page_count": len(required_page_numbers),
                "ocr_required_page_count": len(required_page_numbers),
                "native_text_page_count": native_text_page_count,
                "blank_page_count": blank_page_count,
                "ocr_mean_confidence": None,
                "ocr_confidence_by_page": [],
                "ocr_rescue_page_count": 0,
                "ocr_rescue_span_count": 0,
                "ocr_rescue_receipt_sha256": _ocr_rescue_receipt_sha256([]),
            },
            private_diagnostics={PDF_DIAGNOSTIC_REASON_KEY: PDF_DIAGNOSTIC_PARSED_OCR},
        )

    evaluation = evaluate_image_only_page_receipts(
        receipts=image_only_receipts,
        content_sha256=content_sha256,
        page_count=page_count,
        durable_substantive_spans=_durable_span_proofs(substantive.spans),
    )
    metadata = {
        **substantive.metadata,
        "ocr_page_count": len(required_page_numbers),
        "ocr_required_page_count": len(required_page_numbers),
        "ocr_rescue_policy_version": OCR_RESCUE_POLICY_VERSION,
        "ocr_rescue_policy_sha256": OCR_RESCUE_POLICY_SHA256,
        "ocr_no_consensus_page_receipts": [receipt.to_payload() for receipt in proof.page_receipts],
        "ocr_result_sha256": proof.ocr_result_sha256,
        "image_only_page_receipts": image_only_receipts,
        "image_only_page_evaluation": evaluation,
    }
    if error.render_admissions:
        observed_records = [admission.to_metadata_dict() for admission in error.render_admissions]
        metadata["ocr_render_admissions"] = observed_records
        metadata["ocr_render_admissions_sha256"] = _canonical_json_digest(observed_records)
    if evaluation["may_proceed"] is not True:
        return ParseResult(
            doc_type="PDF",
            success=False,
            spans=[],
            metadata=metadata,
            errors=[
                ParseError(
                    code=ParseErrorCode.OCR_NO_TEXT_EXTRACTED,
                    message="Reviewed image-only PDF has no substantive text spans",
                    details={"terminal_code": IMAGE_ONLY_DOCUMENT_REVIEW_REQUIRED},
                )
            ],
            private_diagnostics=substantive.private_diagnostics,
        )
    substantive.metadata = metadata
    return substantive


def _ocr_error(code: ParseErrorCode, message: str) -> ParseResult:
    return ParseResult(
        doc_type="PDF",
        success=False,
        errors=[ParseError(code=code, message=message, details={})],
    )


def _validated_no_consensus_proof(
    error: OcrRescueNoConsensusError,
) -> OcrNoConsensusFileProof | None:
    if error.file_proof is None:
        return None
    return validate_ocr_no_consensus_file_proof(error.file_proof)


def _ocr_no_consensus_result(
    *,
    error: OcrRescueNoConsensusError,
    page_count: int,
    required_page_numbers: set[int],
    native_span_count: int,
    native_text_length: int,
    native_text_page_count: int,
    blank_page_count: int,
) -> ParseResult:
    proof = _validated_no_consensus_proof(error)
    if proof is None:
        return _ocr_error(
            ParseErrorCode.OCR_RESCUE_NO_CONSENSUS,
            "OCR rescue produced no unique consensus",
        )

    receipts = proof.page_receipts
    rescue_candidate_count = len(OCR_RESCUE_MATRIX)
    primary_completed = sum(receipt.primary_completed for receipt in receipts)
    rescue_attempted = sum(receipt.rescue_attempted_candidate_count for receipt in receipts)
    rescue_completed = sum(receipt.rescue_completed_candidate_count for receipt in receipts)
    rescue_completed_pages = sum(
        receipt.rescue_completed_candidate_count
        == receipt.rescue_attempted_candidate_count
        == rescue_candidate_count
        for receipt in receipts
    )
    accepted_text_pages = sum(receipt.accepted_text for receipt in receipts)
    accepted_span_count = sum(receipt.accepted_text_span_count for receipt in receipts)
    accepted_codepoint_count = sum(receipt.accepted_text_codepoint_count for receipt in receipts)
    rescue_accepted_pages = sum(receipt.outcome == "rescue_text" for receipt in receipts)
    no_consensus_pages = sum(receipt.outcome == "no_consensus" for receipt in receipts)
    complete_no_text = (
        tuple(receipt.page_number for receipt in receipts) == tuple(range(1, page_count + 1))
        and required_page_numbers == set(range(1, page_count + 1))
        and native_span_count == native_text_length == native_text_page_count == 0
        and accepted_text_pages == accepted_span_count == accepted_codepoint_count == 0
        and primary_completed == rescue_completed_pages == no_consensus_pages == page_count
        and rescue_attempted == rescue_completed == page_count * rescue_candidate_count
    )

    return ParseResult(
        doc_type="PDF",
        success=False,
        spans=[],
        metadata={
            "page_count": page_count,
            "span_count": native_span_count + accepted_span_count,
            "total_text_length": native_text_length + accepted_codepoint_count,
            "ocr_performed": True,
            "ocr_page_count": len(receipts),
            "ocr_required_page_count": len(required_page_numbers),
            "native_text_page_count": native_text_page_count,
            "blank_page_count": blank_page_count,
            "primary_completed_page_count": primary_completed,
            "rescue_completed_page_count": rescue_completed_pages,
            "rescue_candidate_attempt_count": rescue_attempted,
            "rescue_candidate_completed_count": rescue_completed,
            "rescue_accepted_clique_page_count": rescue_accepted_pages,
            "accepted_text_page_count": accepted_text_pages,
            "parser_runtime": "tesseract",
            "parser_runtime_status": (
                "completed_no_extractable_text"
                if complete_no_text
                else "completed_with_unresolved_no_consensus"
            ),
            "ocr_rescue_policy_version": OCR_RESCUE_POLICY_VERSION,
            "ocr_rescue_policy_sha256": OCR_RESCUE_POLICY_SHA256,
            "ocr_no_consensus_page_receipts": [receipt.to_payload() for receipt in receipts],
            "ocr_result_sha256": proof.ocr_result_sha256,
        },
        errors=[
            ParseError(
                code=ParseErrorCode.OCR_RESCUE_NO_CONSENSUS,
                message="OCR rescue produced no unique consensus",
                details={
                    "page_count": page_count,
                    "attempted_candidate_count": rescue_attempted,
                    "completed_candidate_count": rescue_completed,
                    "no_consensus_page_count": no_consensus_pages,
                },
            )
        ],
    )


def _normalized_ocr_rescue_text_sha256(text: str) -> str:
    characters = [
        character if unicodedata.category(character).startswith(("L", "N")) else " "
        for character in unicodedata.normalize("NFKC", text).casefold()
    ]
    normalized = " ".join("".join(characters).split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _ocr_rescue_receipt_sha256(rows: list[dict[str, object]]) -> str:
    encoded = json.dumps(
        rows,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _parse_ocr_pages(
    *,
    page_count: int,
    max_pages: int,
    pages: list[OcrPageText],
    required_page_numbers: set[int],
    native_spans: list[SpanDraft],
    native_text_length: int,
    native_text_page_count: int,
    blank_page_count: int,
) -> ParseResult:
    page_window = min(page_count, max_pages)
    if len(pages) > page_window:
        return _ocr_error(ParseErrorCode.OCR_FAILED, "OCR returned invalid page results")

    ocr_spans: list[SpanDraft] = []
    ocr_text_length = 0
    ocr_rescue_span_count = 0
    ocr_rescue_receipts: list[dict[str, object]] = []
    seen_pages: set[int] = set()
    pages_by_number: dict[int, OcrPageText] = {}
    for page in pages:
        if not isinstance(page.page_number, int) or not 1 <= page.page_number <= page_window:
            return _ocr_error(ParseErrorCode.OCR_FAILED, "OCR returned invalid page results")
        if page.page_number in seen_pages:
            return _ocr_error(ParseErrorCode.OCR_FAILED, "OCR returned invalid page results")
        if not isinstance(page.text, str):
            return _ocr_error(ParseErrorCode.OCR_FAILED, "OCR returned invalid page results")
        if page.rescue_provenance is not None and not isinstance(
            page.rescue_provenance, OcrRescueProvenance
        ):
            return _ocr_error(ParseErrorCode.OCR_FAILED, "OCR returned invalid page results")
        if page.render_admission is not None and not isinstance(
            page.render_admission, OcrPageRenderAdmission
        ):
            return _ocr_error(ParseErrorCode.OCR_FAILED, "OCR returned invalid page results")
        seen_pages.add(page.page_number)
        pages_by_number[page.page_number] = page

    selected_pages: list[OcrPageText] = []
    for page_number in sorted(required_page_numbers):
        required_page = pages_by_number.get(page_number)
        if required_page is None:
            return _ocr_error(
                ParseErrorCode.OCR_NO_TEXT_EXTRACTED,
                "OCR omitted a required image page",
            )
        selected_pages.append(required_page)
        page_span_count = 0
        ocr_text_length += len(required_page.text)
        rescue_provenance = required_page.rescue_provenance
        rescue_locator_fields: dict[str, object] = {}
        render_locator_fields: dict[str, object] = {}
        if required_page.render_admission is not None:
            render_locator_fields = {
                "ocr_render_admission": required_page.render_admission.to_span_dict()
            }
        if rescue_provenance is not None:
            rescue_locator_fields = asdict(rescue_provenance)
            if (
                rescue_provenance.normalized_page_text_sha256
                != _normalized_ocr_rescue_text_sha256(required_page.text)
                or required_page.confidence != rescue_provenance.winner_mean_confidence
            ):
                return _ocr_error(
                    ParseErrorCode.OCR_FAILED,
                    "OCR rescue provenance does not match its selected page",
                )
            ocr_rescue_receipts.append(
                {
                    "page": required_page.page_number,
                    **rescue_locator_fields,
                }
            )
        for line_idx, line_text in enumerate(required_page.text.split("\n"), start=1):
            stripped = line_text.strip()
            if not stripped:
                continue
            page_span_count += 1
            if rescue_provenance is not None:
                ocr_rescue_span_count += 1
            ocr_spans.append(
                SpanDraft(
                    span_type="PAGE_TEXT",
                    locator={
                        "page": required_page.page_number,
                        "line": line_idx,
                        "source": ("ocr_rescue" if rescue_provenance is not None else "ocr"),
                        **render_locator_fields,
                        **rescue_locator_fields,
                    },
                    text_excerpt=stripped,
                    content_hash=_compute_content_hash(stripped),
                )
            )
        if page_span_count == 0:
            return _ocr_error(
                ParseErrorCode.OCR_NO_TEXT_EXTRACTED,
                "OCR completed but a required image page has no text",
            )

    if not ocr_spans:
        return ParseResult(
            doc_type="PDF",
            success=False,
            errors=[
                ParseError(
                    code=ParseErrorCode.OCR_NO_TEXT_EXTRACTED,
                    message="OCR completed but no extractable text was found",
                    details={
                        "pages": page_count,
                        "ocr_required_pages": len(required_page_numbers),
                    },
                )
            ],
        )

    spans = [*native_spans, *ocr_spans]
    spans.sort(
        key=lambda span: (
            int(span.locator.get("page", 0)),
            int(span.locator.get("line", 0)),
        )
    )
    total_text_length = native_text_length + ocr_text_length
    ocr_rescue_receipts.sort(key=lambda receipt: cast(int, receipt["page"]))
    render_admission_records, render_admissions_sha256 = _render_admission_metadata(selected_pages)

    return ParseResult(
        doc_type="PDF",
        success=True,
        spans=spans,
        metadata={
            "page_count": page_count,
            "span_count": len(spans),
            "total_text_length": total_text_length,
            "ocr_performed": True,
            "ocr_page_count": len(required_page_numbers),
            "ocr_required_page_count": len(required_page_numbers),
            "native_text_page_count": native_text_page_count,
            "blank_page_count": blank_page_count,
            "ocr_mean_confidence": overall_mean_confidence(selected_pages),
            "ocr_confidence_by_page": [page.confidence for page in selected_pages],
            "ocr_rescue_page_count": len(ocr_rescue_receipts),
            "ocr_rescue_span_count": ocr_rescue_span_count,
            "ocr_rescue_receipt_sha256": _ocr_rescue_receipt_sha256(ocr_rescue_receipts),
            "ocr_render_admissions": render_admission_records,
            "ocr_render_admissions_sha256": render_admissions_sha256,
        },
        private_diagnostics={PDF_DIAGNOSTIC_REASON_KEY: PDF_DIAGNOSTIC_PARSED_OCR},
    )
