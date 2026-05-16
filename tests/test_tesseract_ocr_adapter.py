"""Tests for the real process-isolated Tesseract OCR adapter."""

from __future__ import annotations

import io
import time
from multiprocessing import Queue
from pathlib import Path
from typing import Any

import pytest

from idis.evaluation.real_example_gate import GateMode, run_real_example_gate
from idis.parsers.base import ParseErrorCode
from idis.parsers.ocr import OcrConfig, TesseractOcrAdapter
from idis.parsers.pdf import parse_pdf

try:
    from PIL import Image, ImageDraw, ImageFont
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    IMAGE_DEPS_AVAILABLE = True
except ImportError:
    IMAGE_DEPS_AVAILABLE = False


def test_tesseract_adapter_missing_dependency_returns_safe_unavailable() -> None:
    adapter = TesseractOcrAdapter(worker_target=_unavailable_ocr_worker)

    result = parse_pdf(
        _create_image_text_pdf("SLICE33 OCR UNAVAILABLE"),
        ocr_config=OcrConfig(enabled=True, adapter=adapter, max_pages=1),
    )

    assert result.success is False
    assert result.spans == []
    assert [error.code for error in result.errors] == [ParseErrorCode.OCR_UNAVAILABLE]
    assert result.errors[0].details == {}


def test_tesseract_adapter_timeout_is_structured_and_safe() -> None:
    adapter = TesseractOcrAdapter(worker_target=_slow_ocr_worker)

    result = parse_pdf(
        _create_image_text_pdf("SLICE33 OCR TIMEOUT"),
        ocr_config=OcrConfig(
            enabled=True,
            adapter=adapter,
            max_pages=1,
            timeout_seconds=0.05,
        ),
    )

    encoded = str(result.to_dict())
    assert result.success is False
    assert result.spans == []
    assert [error.code for error in result.errors] == [ParseErrorCode.OCR_TIMEOUT]
    assert result.errors[0].details == {}
    assert "SLICE33 OCR TIMEOUT" not in encoded


def test_tesseract_adapter_worker_failure_is_structured_and_safe() -> None:
    adapter = TesseractOcrAdapter(worker_target=_failed_ocr_worker)

    result = parse_pdf(
        _create_image_text_pdf("SLICE33 OCR FAILURE"),
        ocr_config=OcrConfig(enabled=True, adapter=adapter, max_pages=1),
    )

    encoded = str(result.to_dict())
    assert result.success is False
    assert result.spans == []
    assert [error.code for error in result.errors] == [ParseErrorCode.OCR_FAILED]
    assert result.errors[0].details == {}
    assert "SLICE33 OCR FAILURE" not in encoded


def test_tesseract_adapter_success_creates_deterministic_spans() -> None:
    adapter = TesseractOcrAdapter(dpi=220)
    pdf_bytes = _create_image_text_pdf("SLICE33 OCR 123")

    first = parse_pdf(
        pdf_bytes,
        ocr_config=OcrConfig(enabled=True, adapter=adapter, max_pages=1, timeout_seconds=20),
    )
    second = parse_pdf(
        pdf_bytes,
        ocr_config=OcrConfig(enabled=True, adapter=adapter, max_pages=1, timeout_seconds=20),
    )

    assert first.success is True
    assert first.errors == []
    assert first.metadata["ocr_performed"] is True
    assert [span.locator for span in first.spans] == [{"page": 1, "line": 1, "source": "ocr"}]
    assert [span.content_hash for span in first.spans] == [
        span.content_hash for span in second.spans
    ]
    assert "OCR" in " ".join(span.text_excerpt for span in first.spans)


def test_tesseract_adapter_respects_max_page_window_before_span_creation() -> None:
    adapter = TesseractOcrAdapter(worker_target=_out_of_window_ocr_worker)

    result = parse_pdf(
        _create_image_text_pdf("SLICE33 OCR PAGE WINDOW", pages=2),
        ocr_config=OcrConfig(enabled=True, adapter=adapter, max_pages=1, timeout_seconds=10),
    )

    assert result.success is False
    assert result.spans == []
    assert [error.code for error in result.errors] == [ParseErrorCode.OCR_FAILED]
    assert result.errors[0].details == {}


def test_private_gate_ocr_enabled_remains_aggregate_only(tmp_path: Path) -> None:
    root = tmp_path / "real_example"
    confidential_dir = root / "Confidential OCR Folder"
    confidential_dir.mkdir(parents=True)
    (confidential_dir / "secret scanned appendix.pdf").write_bytes(
        _create_image_text_pdf("SLICE33 PRIVATE OCR")
    )

    summary = run_real_example_gate(
        root=root,
        ledger_path=tmp_path / "ledger.json",
        mode=GateMode.PARSE_SUPPORTED,
        safe_summary=True,
        emit_progress=False,
        ocr_enabled=True,
        ocr_max_pages=1,
        ocr_timeout_seconds=20,
    )

    assert summary["counts_by_status"] == {"parsed": 1}
    assert summary["counts_by_parser_outcome"] == {"success": 1}
    assert summary["counts_by_reason_code"] == {"parsed": 1}
    _assert_safe_summary(
        summary,
        forbidden=[str(root), "Confidential", "secret", "scanned", "SLICE33", "PRIVATE"],
    )


def test_private_gate_ocr_enabled_retries_prior_ocr_required_ledger_entry(
    tmp_path: Path,
) -> None:
    root = tmp_path / "real_example"
    confidential_dir = root / "Confidential OCR Resume"
    confidential_dir.mkdir(parents=True)
    (confidential_dir / "secret scanned appendix.pdf").write_bytes(
        _create_image_text_pdf("SLICE33 OCR RESUME")
    )
    ledger_path = tmp_path / "ledger.json"

    default_summary = run_real_example_gate(
        root=root,
        ledger_path=ledger_path,
        mode=GateMode.PARSE_SUPPORTED,
        safe_summary=True,
        emit_progress=False,
    )
    ocr_summary = run_real_example_gate(
        root=root,
        ledger_path=ledger_path,
        mode=GateMode.PARSE_SUPPORTED,
        safe_summary=True,
        emit_progress=False,
        ocr_enabled=True,
        ocr_max_pages=1,
        ocr_timeout_seconds=20,
    )

    assert default_summary["counts_by_reason_code"] == {"ocr_required": 1}
    assert ocr_summary["counts_by_status"] == {"parsed": 1}
    assert ocr_summary["counts_by_parser_outcome"] == {"success": 1}
    _assert_safe_summary(
        ocr_summary,
        forbidden=[str(root), "Confidential", "secret", "scanned", "SLICE33", "RESUME"],
    )


def test_private_gate_rejects_unbounded_ocr_options(tmp_path: Path) -> None:
    root = tmp_path / "real_example"
    root.mkdir()
    (root / "secret scanned appendix.pdf").write_bytes(_create_image_text_pdf("SLICE33 OCR BOUNDS"))

    with pytest.raises(ValueError, match="OCR max pages"):
        run_real_example_gate(
            root=root,
            ledger_path=tmp_path / "ledger.json",
            mode=GateMode.PARSE_SUPPORTED,
            safe_summary=True,
            emit_progress=False,
            ocr_enabled=True,
            ocr_max_pages=999,
        )


def _unavailable_ocr_worker(
    data: bytes,
    max_pages: int,
    dpi: int,
    language: str,
    timeout_seconds: float,
    queue: Queue,
) -> None:
    del data, max_pages, dpi, language, timeout_seconds
    queue.put({"status": "unavailable"})


def _slow_ocr_worker(
    data: bytes,
    max_pages: int,
    dpi: int,
    language: str,
    timeout_seconds: float,
    queue: Queue,
) -> None:
    del data, max_pages, dpi, language, timeout_seconds, queue
    time.sleep(10)


def _failed_ocr_worker(
    data: bytes,
    max_pages: int,
    dpi: int,
    language: str,
    timeout_seconds: float,
    queue: Queue,
) -> None:
    del data, max_pages, dpi, language, timeout_seconds
    queue.put({"status": "failed"})


def _out_of_window_ocr_worker(
    data: bytes,
    max_pages: int,
    dpi: int,
    language: str,
    timeout_seconds: float,
    queue: Queue,
) -> None:
    del data, max_pages, dpi, language, timeout_seconds
    queue.put(
        {
            "status": "success",
            "pages": [{"page_number": 2, "text": "Out of window text"}],
        }
    )


def _create_image_text_pdf(text: str, *, pages: int = 1) -> bytes:
    if not IMAGE_DEPS_AVAILABLE:
        pytest.skip("PIL/reportlab image dependencies are not installed")

    image = Image.new("RGB", (1400, 360), color="white")
    draw = ImageDraw.Draw(image)
    draw.text((60, 120), text, fill="black", font=_large_font())
    image_buffer = io.BytesIO()
    image.save(image_buffer, format="PNG")
    image_buffer.seek(0)

    pdf_buffer = io.BytesIO()
    c = canvas.Canvas(pdf_buffer, pagesize=letter)
    for page_index in range(pages):
        c.drawImage(ImageReader(image_buffer), 72, 520, width=460, height=120)
        if page_index < pages - 1:
            c.showPage()
            image_buffer.seek(0)
    c.save()
    return pdf_buffer.getvalue()


def _large_font() -> Any:
    font_candidates = [
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    ]
    for path in font_candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, 72)
    return ImageFont.load_default()


def _assert_safe_summary(summary: dict[str, object], *, forbidden: list[str]) -> None:
    encoded = str(summary)
    assert "filename" not in encoded
    assert "path" not in encoded
    assert "text_excerpt" not in encoded
    for token in forbidden:
        assert token not in encoded
