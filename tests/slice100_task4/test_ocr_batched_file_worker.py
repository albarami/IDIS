"""Task 4 contract for bounded, file-backed PDF OCR execution."""

from __future__ import annotations

import concurrent.futures
import os
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from idis.parsers import ocr
from tests.slice100_task4.test_linux_poppler_parent_guard import (
    inject_inline_governed_child_seam,
)


class _CaptureQueue:
    def __init__(self) -> None:
        self.items: list[dict[str, object]] = []

    def put(self, payload: dict[str, object]) -> None:
        self.items.append(payload)


class _FakeImage:
    def __init__(self, page_number: int, *, batch_number: int = 1) -> None:
        self.page_number = page_number
        self.batch_number = batch_number
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _PdfInfoNotInstalledError(Exception):
    pass


class _PdfPopplerTimeoutError(Exception):
    pass


class _TesseractError(Exception):
    pass


class _TesseractNotFoundError(Exception):
    pass


class _Output:
    DICT = "dict"


def _install_fake_runtime(
    monkeypatch: pytest.MonkeyPatch,
    *,
    convert_from_path: Callable[..., list[_FakeImage]],
    image_to_data: Callable[..., dict[str, object]],
) -> None:
    pdf2image = ModuleType("pdf2image")
    pdf2image.convert_from_path = convert_from_path  # type: ignore[attr-defined]
    pdf2image_exceptions = ModuleType("pdf2image.exceptions")
    pdf2image_exceptions.PDFInfoNotInstalledError = _PdfInfoNotInstalledError  # type: ignore[attr-defined]
    pdf2image_exceptions.PDFPopplerTimeoutError = _PdfPopplerTimeoutError  # type: ignore[attr-defined]
    pytesseract = ModuleType("pytesseract")
    pytesseract.Output = _Output  # type: ignore[attr-defined]
    pytesseract.TesseractError = _TesseractError  # type: ignore[attr-defined]
    pytesseract.TesseractNotFoundError = _TesseractNotFoundError  # type: ignore[attr-defined]
    pytesseract.image_to_data = image_to_data  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pdf2image", pdf2image)
    monkeypatch.setitem(sys.modules, "pdf2image.exceptions", pdf2image_exceptions)
    monkeypatch.setitem(sys.modules, "pytesseract", pytesseract)
    inject_inline_governed_child_seam(monkeypatch, ocr)


def _run_file_worker(
    source_path: Path,
    *,
    max_pages: int,
    timeout_seconds: float = 3.0,
) -> _CaptureQueue:
    queue = _CaptureQueue()
    worker = ocr._tesseract_pdf_file_ocr_worker
    worker(
        source_path,
        max_pages,
        200,
        "eng",
        time.monotonic() + timeout_seconds,
        queue,
    )
    return queue


def _success_file_worker(
    source_path: str | Path,
    max_pages: int,
    dpi: int,
    language: str,
    deadline: float,
    queue: Any,
) -> None:
    assert Path(source_path).name == "fictional-staged.pdf"
    assert (max_pages, dpi, language) == (2, 200, "eng")
    assert deadline > time.monotonic()
    queue.put(
        {
            "status": "success",
            "pages": [
                {
                    "page_number": 1,
                    "text": "fictional file-backed OCR",
                    "confidences": ["93"],
                }
            ],
        }
    )


def _payload(page_number: int) -> dict[str, object]:
    return {
        # Deliberately return line two before line one and word two before word
        # one. Production must derive stable coordinates from this same payload.
        "text": [
            f"tail-second-{page_number}",
            f"head-second-{page_number}",
            f"tail-first-{page_number}",
            f"head-first-{page_number}",
        ],
        "conf": ["81", "93", "77", "88"],
        "block_num": [1, 1, 1, 1],
        "par_num": [1, 1, 1, 1],
        "line_num": [2, 2, 1, 1],
        "word_num": [2, 1, 2, 1],
    }


def test_file_adapter_passes_the_caller_owned_path_to_an_injected_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = tmp_path / "fictional-staged.pdf"
    source_path.write_bytes(b"fictional-pdf")

    def forbidden_read_bytes(_self: Path) -> bytes:
        raise AssertionError("file-backed OCR must not create a byte-backed PDF copy")

    monkeypatch.setattr(Path, "read_bytes", forbidden_read_bytes)
    adapter = ocr.TesseractOcrAdapter(file_worker_target=_success_file_worker)

    pages = adapter.extract_pdf_file(source_path, max_pages=2, timeout_seconds=2)

    assert [(page.page_number, page.text, page.confidence) for page in pages] == [
        (1, "fictional file-backed OCR", 0.93)
    ]


def test_file_worker_batches_pages_and_bounds_page_ocr_concurrency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = tmp_path / "caller-owned.pdf"
    images: list[_FakeImage] = []
    render_calls: list[dict[str, object]] = []
    ocr_calls: dict[int, int] = {}
    omp_values: list[str | None] = []
    active = 0
    max_active = 0
    active_lock = threading.Lock()
    four_started = threading.Event()

    def convert_from_path(path: str, **kwargs: object) -> list[_FakeImage]:
        assert Path(path) == source_path
        render_calls.append(dict(kwargs))
        first_page = int(kwargs["first_page"])
        last_page = int(kwargs["last_page"])
        batch_number = len(render_calls)
        batch = [
            _FakeImage(page_number, batch_number=batch_number)
            for page_number in range(first_page, last_page + 1)
        ]
        images.extend(batch)
        return batch

    def image_to_data(image: _FakeImage, **kwargs: object) -> dict[str, object]:
        nonlocal active, max_active
        assert kwargs["output_type"] == _Output.DICT
        omp_values.append(os.environ.get("OMP_THREAD_LIMIT"))
        ocr_calls[image.page_number] = ocr_calls.get(image.page_number, 0) + 1
        with active_lock:
            active += 1
            max_active = max(max_active, active)
            if active == 4:
                four_started.set()
        assert four_started.wait(timeout=1)
        # Invert completion order without changing final page ordering.
        time.sleep((9 - ((image.page_number - 1) % 8)) / 2000)
        with active_lock:
            active -= 1
        return _payload(image.page_number)

    shutdowns: list[tuple[bool, bool]] = []
    worker_counts: list[int] = []

    class _TrackingExecutor(concurrent.futures.ThreadPoolExecutor):
        def __init__(self, max_workers: int) -> None:
            worker_counts.append(max_workers)
            super().__init__(max_workers=max_workers)

        def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
            shutdowns.append((wait, cancel_futures))
            super().shutdown(wait=wait, cancel_futures=cancel_futures)

    _install_fake_runtime(
        monkeypatch,
        convert_from_path=convert_from_path,
        image_to_data=image_to_data,
    )
    monkeypatch.setattr(ocr, "ThreadPoolExecutor", _TrackingExecutor)

    queue = _run_file_worker(source_path, max_pages=18)

    assert [(call["first_page"], call["last_page"]) for call in render_calls] == [
        (1, 8),
        (9, 16),
        (17, 18),
    ]
    assert all(int(call["last_page"]) - int(call["first_page"]) + 1 <= 8 for call in render_calls)
    assert all(call["thread_count"] == 1 for call in render_calls)
    assert worker_counts == [4]
    assert max_active == 4
    assert ocr_calls == dict.fromkeys(range(1, 19), 1)
    assert set(omp_values) == {"1"}
    assert all(image.closed for image in images)
    assert shutdowns
    assert queue.items[0]["status"] == "success"
    pages = queue.items[0]["pages"]
    assert isinstance(pages, list)
    assert [page["page_number"] for page in pages] == list(range(1, 19))
    assert [page["text"] for page in pages] == [
        (
            f"head-first-{page_number} tail-first-{page_number}\n"
            f"head-second-{page_number} tail-second-{page_number}"
        )
        for page_number in range(1, 19)
    ]
    assert all(page["confidences"] == ["81", "93", "77", "88"] for page in pages)


def test_render_and_page_jobs_share_one_decreasing_absolute_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = tmp_path / "deadline.pdf"
    render_timeouts: list[float] = []
    page_timeouts: list[tuple[int, float]] = []

    def convert_from_path(_path: str, **kwargs: object) -> list[_FakeImage]:
        render_timeouts.append(float(kwargs["timeout"]))
        time.sleep(0.01)
        return [
            _FakeImage(page_number, batch_number=len(render_timeouts))
            for page_number in range(int(kwargs["first_page"]), int(kwargs["last_page"]) + 1)
        ]

    def image_to_data(image: _FakeImage, **kwargs: object) -> dict[str, object]:
        page_timeouts.append((image.batch_number, float(kwargs["timeout"])))
        time.sleep(0.002)
        return _payload(image.page_number)

    _install_fake_runtime(
        monkeypatch,
        convert_from_path=convert_from_path,
        image_to_data=image_to_data,
    )

    queue = _run_file_worker(source_path, max_pages=9)

    assert queue.items[0]["status"] == "success"
    assert len(render_timeouts) == 2
    assert render_timeouts[1] < render_timeouts[0]
    assert page_timeouts
    for batch_number, page_timeout in page_timeouts:
        assert 0 < page_timeout <= render_timeouts[batch_number - 1]


@pytest.mark.parametrize(
    "invalid_case",
    ("column_length", "missing_word_num", "duplicate_word_coordinate", "zero_word_coordinate"),
)
def test_malformed_page_payload_fails_without_partial_results_and_closes_images(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid_case: str,
) -> None:
    source_path = tmp_path / "malformed.pdf"
    images = [_FakeImage(1), _FakeImage(2)]

    def convert_from_path(_path: str, **_kwargs: object) -> list[_FakeImage]:
        return images

    def image_to_data(image: _FakeImage, **_kwargs: object) -> dict[str, object]:
        if image.page_number == 1:
            return _payload(1)
        malformed = _payload(2)
        if invalid_case == "column_length":
            malformed["conf"] = ["90"]
        elif invalid_case == "missing_word_num":
            malformed.pop("word_num")
        elif invalid_case == "duplicate_word_coordinate":
            malformed["word_num"] = [2, 2, 2, 1]
        else:
            malformed["word_num"] = [0, 1, 2, 1]
        return malformed

    _install_fake_runtime(
        monkeypatch,
        convert_from_path=convert_from_path,
        image_to_data=image_to_data,
    )

    queue = _run_file_worker(source_path, max_pages=2)

    assert queue.items == [{"status": "failed"}]
    assert all(image.closed for image in images)


def test_page_timeout_fails_without_partial_results_and_closes_images(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = tmp_path / "timeout.pdf"
    images = [_FakeImage(1), _FakeImage(2)]

    def convert_from_path(_path: str, **_kwargs: object) -> list[_FakeImage]:
        return images

    def image_to_data(image: _FakeImage, **_kwargs: object) -> dict[str, object]:
        if image.page_number == 2:
            raise RuntimeError("Tesseract process timeout")
        return _payload(1)

    _install_fake_runtime(
        monkeypatch,
        convert_from_path=convert_from_path,
        image_to_data=image_to_data,
    )

    queue = _run_file_worker(source_path, max_pages=2)

    assert queue.items == [{"status": "timeout"}]
    assert all(image.closed for image in images)
