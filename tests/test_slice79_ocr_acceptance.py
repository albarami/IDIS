"""Slice79 Task 6 — acceptance proof for OCR/Image ingestion.

Proves (deterministically, without requiring real tesseract/poppler in CI):
1. Generated scanned-PDF and image fixtures produce OCR PAGE_TEXT spans with page/line
   locators (source "ocr" for scanned PDF, "ocr_image" for image).
2. Private real_example OCR-required behavior is honest: with OCR disabled, OCR-required
   files are explicitly blocked with the accepted ``ocr_required`` reason (not silently
   dropped); with OCR enabled+healthy, the OCR-required count goes to zero.

Honest note on the gate path: ``INVENTORY_ONLY`` is genuinely inventory-only
(parser_outcome="not_attempted", reason_code="inventory_only") and never parses or
classifies OCR-required reasons. The OCR-required count proof therefore uses the
``PARSE_SUPPORTED`` safe-aggregate path (the gate's real classification path), with a
deterministic injected parse seam for the enabled case. Safe aggregates expose only
counts by extension/status/parser-outcome/reason-code — never OCR text, raw paths,
filenames, storage URIs, env values, command output, or secrets.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from idis.evaluation.real_example_gate import GateMode, run_real_example_gate
from idis.evaluation.real_example_gate_runtime import ParseAttempt
from idis.parsers.image import parse_image
from idis.parsers.ocr import OcrConfig, OcrPageText
from idis.parsers.pdf import parse_pdf
from tests.test_pdf_ocr_adapter import RecordingOcrAdapter, _create_image_only_pdf
from tests.test_tesseract_ocr_adapter import _create_image_text_png

# --- 1. Generated fixtures produce OCR spans ---


def test_generated_scanned_pdf_produces_ocr_page_text_spans() -> None:
    adapter = RecordingOcrAdapter(
        [
            OcrPageText(page_number=1, text="Revenue 10M", confidence=0.9),
            OcrPageText(page_number=2, text="Margin 40%", confidence=0.8),
        ]
    )

    result = parse_pdf(
        _create_image_only_pdf(num_pages=2),
        ocr_config=OcrConfig(enabled=True, adapter=adapter, max_pages=3),
    )

    assert result.success is True
    assert [s.span_type for s in result.spans] == ["PAGE_TEXT", "PAGE_TEXT"]
    assert all(s.locator.get("source") == "ocr" for s in result.spans)
    assert all("page" in s.locator and "line" in s.locator for s in result.spans)


def test_generated_image_produces_ocr_image_page_text_spans() -> None:
    adapter = RecordingOcrAdapter(
        [OcrPageText(page_number=1, text="image line one\nimage line two", confidence=0.7)]
    )

    result = parse_image(
        _create_image_text_png("generated image fixture text"),
        ocr_config=OcrConfig(enabled=True, adapter=adapter),
    )

    assert result.success is True
    assert [s.span_type for s in result.spans] == ["PAGE_TEXT", "PAGE_TEXT"]
    assert all(s.locator.get("source") == "ocr_image" for s in result.spans)
    assert all("page" in s.locator and "line" in s.locator for s in result.spans)


# --- 2. real_example OCR-required behavior is honest (safe aggregate) ---


def _data_room(tmp_path: Path, filename: str, data: bytes) -> Path:
    root = tmp_path / "real_example"
    root.mkdir()
    (root / filename).write_bytes(data)
    return root


def _assert_safe_aggregate(summary: dict[str, Any], *, root: Path, forbidden: list[str]) -> None:
    assert summary["safe_summary"] is True
    blob = json.dumps(summary, default=str)
    assert str(root) not in blob
    for token in forbidden:
        assert token not in blob


def test_inventory_only_does_not_compute_ocr_required_counts(tmp_path: Path) -> None:
    root = _data_room(tmp_path, "scan-secret-name.png", b"\x89PNG\r\n\x1a\nfake")

    summary = run_real_example_gate(
        root=root,
        ledger_path=tmp_path / "ledger.json",
        mode=GateMode.INVENTORY_ONLY,
    )

    # INVENTORY_ONLY is inventory-only: it never parses/classifies, so no ocr_required.
    assert summary["counts_by_reason_code"].get("ocr_required", 0) == 0
    assert summary["counts_by_reason_code"].get("inventory_only", 0) >= 1
    assert summary["counts_by_parser_outcome"].get("not_attempted", 0) >= 1
    _assert_safe_aggregate(summary, root=root, forbidden=["scan-secret-name", "secret"])


def test_real_example_ocr_required_is_blocked_with_reason_when_disabled(tmp_path: Path) -> None:
    root = _data_room(tmp_path, "scan-secret-name.png", b"\x89PNG\r\n\x1a\nfake-bytes")

    summary = run_real_example_gate(
        root=root,
        ledger_path=tmp_path / "ledger.json",
        mode=GateMode.PARSE_SUPPORTED,
        ocr_enabled=False,
    )

    # OCR-required file is explicitly blocked with an accepted reason, not silently dropped.
    assert summary["counts_by_reason_code"].get("ocr_required", 0) >= 1
    assert summary["counts_by_status"].get("deferred", 0) >= 1
    _assert_safe_aggregate(summary, root=root, forbidden=["scan-secret-name", "secret"])


def test_real_example_ocr_required_count_goes_to_zero_when_enabled(tmp_path: Path) -> None:
    root = _data_room(tmp_path, "scan-secret-name.png", b"\x89PNG\r\n\x1a\nfake-bytes")

    summary = run_real_example_gate(
        root=root,
        ledger_path=tmp_path / "ledger.json",
        mode=GateMode.PARSE_SUPPORTED,
        ocr_enabled=True,
        parse_attempt_fn=lambda _path: ParseAttempt.parsed(),
    )

    # With OCR enabled+healthy, the OCR-required count goes to zero (the file is parsed).
    assert summary["counts_by_reason_code"].get("ocr_required", 0) == 0
    assert summary["counts_by_status"].get("parsed", 0) >= 1
    _assert_safe_aggregate(summary, root=root, forbidden=["scan-secret-name", "secret"])
