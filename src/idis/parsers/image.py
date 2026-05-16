"""Image OCR parser for explicit, config-gated OCR execution."""

from __future__ import annotations

import hashlib

from idis.parsers.base import ParseError, ParseErrorCode, ParseLimits, ParseResult, SpanDraft
from idis.parsers.ocr import OcrConfig, OcrError, OcrPageText, OcrTimeoutError, OcrUnavailableError


def parse_image(
    data: bytes,
    limits: ParseLimits | None = None,
    ocr_config: OcrConfig | None = None,
) -> ParseResult:
    """Parse image bytes through an explicit OCR adapter."""
    if limits is None:
        limits = ParseLimits()

    if len(data) > limits.max_bytes:
        return ParseResult(
            doc_type="IMAGE",
            success=False,
            errors=[
                ParseError(
                    code=ParseErrorCode.MAX_SIZE_EXCEEDED,
                    message=f"File size {len(data)} bytes exceeds limit {limits.max_bytes}",
                    details={"size": len(data), "limit": limits.max_bytes},
                )
            ],
        )

    if ocr_config is None or not ocr_config.enabled or ocr_config.adapter is None:
        return _ocr_error(ParseErrorCode.OCR_UNAVAILABLE, "Image OCR unavailable")

    try:
        pages = ocr_config.adapter.extract_image_text(
            data,
            timeout_seconds=ocr_config.timeout_seconds,
        )
        return _parse_image_ocr_pages(pages)
    except OcrTimeoutError:
        return _ocr_error(ParseErrorCode.OCR_TIMEOUT, "Image OCR timed out")
    except OcrUnavailableError:
        return _ocr_error(ParseErrorCode.OCR_UNAVAILABLE, "Image OCR unavailable")
    except OcrError:
        return _ocr_error(ParseErrorCode.OCR_FAILED, "Image OCR failed")
    except Exception:
        return _ocr_error(ParseErrorCode.OCR_FAILED, "Image OCR failed")


def _parse_image_ocr_pages(pages: list[OcrPageText]) -> ParseResult:
    if len(pages) > 1:
        return _ocr_error(ParseErrorCode.OCR_FAILED, "Image OCR returned invalid page results")

    spans: list[SpanDraft] = []
    total_text_length = 0
    for page in pages:
        if not isinstance(page.page_number, int) or page.page_number != 1:
            return _ocr_error(ParseErrorCode.OCR_FAILED, "Image OCR returned invalid page results")
        if not isinstance(page.text, str):
            return _ocr_error(ParseErrorCode.OCR_FAILED, "Image OCR returned invalid page results")
        total_text_length += len(page.text)
        for line_idx, line_text in enumerate(page.text.split("\n"), start=1):
            stripped = line_text.strip()
            if not stripped:
                continue
            spans.append(
                SpanDraft(
                    span_type="PAGE_TEXT",
                    locator={"page": 1, "line": line_idx, "source": "ocr_image"},
                    text_excerpt=stripped,
                    content_hash=_compute_content_hash(stripped),
                )
            )

    if not spans:
        return ParseResult(
            doc_type="IMAGE",
            success=False,
            errors=[
                ParseError(
                    code=ParseErrorCode.NO_TEXT_EXTRACTED,
                    message="Image OCR completed but no extractable text was found",
                    details={},
                )
            ],
        )

    return ParseResult(
        doc_type="IMAGE",
        success=True,
        spans=spans,
        metadata={
            "span_count": len(spans),
            "total_text_length": total_text_length,
            "ocr_performed": True,
            "ocr_image_count": 1,
        },
    )


def _ocr_error(code: ParseErrorCode, message: str) -> ParseResult:
    return ParseResult(
        doc_type="IMAGE",
        success=False,
        errors=[ParseError(code=code, message=message, details={})],
    )


def _compute_content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
