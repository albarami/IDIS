"""RED contracts for bounded runtime watermark rendering and classification.

The tests use a deterministic OCR terminal proof and a verified typed runtime
configuration.  They do not run OCR, Poppler, an API, or a database.  The large-
page case changes only the PDF MediaBox; it never allocates a large raster.
"""

from __future__ import annotations

import hashlib
import io
import math
from dataclasses import dataclass

import pdf2image
import pytest
from pypdf import PdfWriter

from idis.parsers import ocr
from idis.parsers import pdf as pdf_parser
from idis.parsers.watermark_page_classifier import (
    WatermarkPageRuntimeConfig,
    default_watermark_page_policy,
)
from tests.slice100_task4.test_watermark_composition_root_red import (
    _fixture_page,
    _fixture_pdf,
    _verified_runtime_config,
)


def _runtime_config() -> WatermarkPageRuntimeConfig:
    return _verified_runtime_config(_fixture_pdf())


def _complete_no_consensus() -> ocr.OcrRescueNoConsensusError:
    receipt = ocr._no_consensus_page_execution_receipt(  # noqa: SLF001
        page_number=1,
        candidate_receipt_sha256=hashlib.sha256(
            b"fictional-complete-watermark-runtime-candidates"
        ).hexdigest(),
    )
    return ocr.OcrRescueNoConsensusError(
        "OCR rescue produced no unique consensus",
        file_proof=ocr._ocr_no_consensus_file_proof((receipt,)),  # noqa: SLF001
    )


@dataclass
class _Clock:
    now: float

    def monotonic(self) -> float:
        return self.now


def test_classification_does_not_start_after_render_exhausts_original_ocr_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rendering may not hand expired work to an unbounded classifier call."""

    clock = _Clock(now=100.0)
    deadline = 102.0
    classifier_calls = 0

    def render_then_expire(*_args: object, **_kwargs: object) -> list[object]:
        page = _fixture_page()
        clock.now = deadline + 0.001
        return [page]

    def record_classifier_call(**_kwargs: object) -> dict[str, object]:
        nonlocal classifier_calls
        classifier_calls += 1
        return {"page_number": 1}

    monkeypatch.setattr(pdf_parser.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(pdf2image, "convert_from_bytes", render_then_expire)
    monkeypatch.setattr(
        pdf_parser,
        "classify_runtime_watermark_page",
        record_classifier_call,
    )

    with pytest.raises(ocr.OcrTimeoutError):
        pdf_parser._classify_watermark_no_consensus_pages(  # noqa: SLF001
            data=_fixture_pdf(),
            source_path=None,
            error=_complete_no_consensus(),
            required_page_numbers={1},
            runtime_config=_runtime_config(),
            deadline=deadline,
        )

    assert classifier_calls == 0, (
        "watermark classification ran after page rendering had already exhausted "
        "the original OCR deadline"
    )


def _oversized_mediabox_pdf(*, width_points: float, height_points: float) -> bytes:
    output = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=width_points, height=height_points)
    writer.write(output)
    return output.getvalue()


def test_oversized_page_is_rejected_before_parent_process_rasterization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tiny PDF declaring huge dimensions must not reach Poppler in the API process."""

    policy = default_watermark_page_policy()
    width_points = 100_000.0
    height_points = 100_000.0
    projected_width = math.ceil(width_points * policy.render_dpi / 72)
    projected_height = math.ceil(height_points * policy.render_dpi / 72)
    assert projected_width * projected_height > ocr.MAX_OCR_IMAGE_PIXELS
    data = _oversized_mediabox_pdf(
        width_points=width_points,
        height_points=height_points,
    )
    renderer_calls = 0

    def parent_renderer_bomb(*_args: object, **_kwargs: object) -> list[object]:
        nonlocal renderer_calls
        renderer_calls += 1
        raise AssertionError("unbounded parent-process rasterization was attempted")

    monkeypatch.setattr(pdf2image, "convert_from_bytes", parent_renderer_bomb)
    monkeypatch.setattr(pdf_parser.time, "monotonic", lambda: 100.0)

    with pytest.raises(ocr.OcrError):
        pdf_parser._classify_watermark_no_consensus_pages(  # noqa: SLF001
            data=data,
            source_path=None,
            error=_complete_no_consensus(),
            required_page_numbers={1},
            runtime_config=_runtime_config(),
            deadline=200.0,
        )

    assert renderer_calls == 0, (
        "the watermark path attempted to rasterize a page whose declared 200-DPI "
        "pixel count exceeds the admitted OCR resource bound"
    )
