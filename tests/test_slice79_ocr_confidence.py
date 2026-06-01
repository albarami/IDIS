"""Slice79 Task 4 — safe OCR confidence diagnostics.

TDD RED-first. Confidence is surfaced only as safe ParseResult.metadata (per-page /
per-image mean, Tesseract 0-100 normalized to 0-1; invalid/-1 ignored). No OCR text,
paths, env values, command output, or secrets are surfaced. SpanDraft is unchanged;
existing adapters without confidence remain backward compatible.
"""

from __future__ import annotations

import json

from idis.parsers.image import parse_image
from idis.parsers.ocr import (
    OcrConfig,
    OcrPageText,
    _pages_from_worker_payload,
    normalize_ocr_confidence,
    overall_mean_confidence,
)
from idis.parsers.pdf import parse_pdf
from tests.test_pdf_ocr_adapter import RecordingOcrAdapter, _create_image_only_pdf

_PNG_BYTES = b"\x89PNG\r\n\x1a\nfake-image-bytes-ignored-by-mock-adapter"


# 1. Worker/page payload confidence values -> normalized 0-1 OcrPageText confidence.
def test_normalize_confidence_means_and_scales_to_unit_interval() -> None:
    assert normalize_ocr_confidence([90, 80]) == 0.85
    assert normalize_ocr_confidence([100]) == 1.0
    assert normalize_ocr_confidence(["95", "85"]) == 0.9


def test_worker_payload_with_confidences_produces_normalized_ocr_page_text() -> None:
    pages = _pages_from_worker_payload(
        {
            "status": "success",
            "pages": [{"page_number": 1, "text": "Revenue", "confidences": [90, 80]}],
        }
    )
    assert pages[0].text == "Revenue"
    assert pages[0].confidence == 0.85


# 2. Invalid confidence values are ignored safely.
def test_normalize_confidence_ignores_invalid_values() -> None:
    assert normalize_ocr_confidence([-1, 90, "x", "", None, 80]) == 0.85
    assert normalize_ocr_confidence([-1, -1]) is None
    assert normalize_ocr_confidence([150, -1]) is None
    assert normalize_ocr_confidence([]) is None
    assert normalize_ocr_confidence("not-a-list") is None


def test_overall_mean_confidence_ignores_none_pages() -> None:
    pages = [
        OcrPageText(page_number=1, text="a", confidence=0.9),
        OcrPageText(page_number=2, text="b", confidence=None),
        OcrPageText(page_number=3, text="c", confidence=0.7),
    ]
    assert overall_mean_confidence(pages) == 0.8
    assert overall_mean_confidence([OcrPageText(page_number=1, text="a")]) is None


# 3. PDF OCR metadata includes safe confidence diagnostics and no OCR text leak.
def test_pdf_ocr_metadata_includes_safe_confidence_without_text_leak() -> None:
    confidential = "CONFIDENTIAL_PDF_OCR_TEXT_MARKER"
    adapter = RecordingOcrAdapter(
        [
            OcrPageText(page_number=1, text=confidential, confidence=0.9),
            OcrPageText(page_number=2, text="second page text", confidence=0.8),
        ]
    )

    result = parse_pdf(
        _create_image_only_pdf(num_pages=2),
        ocr_config=OcrConfig(enabled=True, adapter=adapter, max_pages=3),
    )

    assert result.success is True
    assert result.metadata["ocr_performed"] is True
    assert result.metadata["ocr_page_count"] == 2
    assert result.metadata["ocr_mean_confidence"] == 0.85
    assert result.metadata["ocr_confidence_by_page"] == [0.9, 0.8]
    # OCR text lives in spans (by design); the confidence metadata must stay text-free.
    metadata_blob = json.dumps(result.metadata, sort_keys=True, default=str)
    assert confidential not in metadata_blob


# 4. Image OCR metadata includes safe confidence diagnostics and no OCR text leak.
def test_image_ocr_metadata_includes_safe_confidence_without_text_leak() -> None:
    confidential = "CONFIDENTIAL_IMAGE_OCR_TEXT_MARKER"
    adapter = RecordingOcrAdapter([OcrPageText(page_number=1, text=confidential, confidence=0.77)])

    result = parse_image(_PNG_BYTES, ocr_config=OcrConfig(enabled=True, adapter=adapter))

    assert result.success is True
    assert result.metadata["ocr_performed"] is True
    assert result.metadata["ocr_image_count"] == 1
    assert result.metadata["ocr_mean_confidence"] == 0.77
    # OCR text lives in spans (by design); the confidence metadata must stay text-free.
    metadata_blob = json.dumps(result.metadata, sort_keys=True, default=str)
    assert confidential not in metadata_blob


# 5. Existing adapter payloads without confidence remain backward compatible.
def test_adapter_without_confidence_is_backward_compatible() -> None:
    adapter = RecordingOcrAdapter([OcrPageText(page_number=1, text="plain page text")])

    result = parse_pdf(
        _create_image_only_pdf(num_pages=1),
        ocr_config=OcrConfig(enabled=True, adapter=adapter, max_pages=1),
    )

    assert result.success is True
    assert result.metadata["ocr_mean_confidence"] is None
    assert result.metadata["ocr_confidence_by_page"] == [None]

    pages = _pages_from_worker_payload(
        {"status": "success", "pages": [{"page_number": 1, "text": "x"}]}
    )
    assert pages[0].confidence is None
