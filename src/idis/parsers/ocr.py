"""Config-gated OCR adapter boundary for parser tests and future provisioning."""

from __future__ import annotations

import contextlib
import multiprocessing as mp
import os
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from queue import Empty
from typing import Any, Protocol

MAX_OCR_IMAGE_PIXELS = 20_000_000


class OcrError(Exception):
    """Base class for safe OCR adapter failures."""


class OcrTimeoutError(OcrError):
    """Raised by OCR adapters when OCR exceeds a configured runtime budget."""


class OcrUnavailableError(OcrError):
    """Raised when OCR dependencies or binaries are not available."""


@dataclass(frozen=True, slots=True)
class OcrPageText:
    """OCR text extracted for one PDF page.

    ``confidence`` is the page/image mean OCR confidence normalized to 0-1, or None
    when no valid confidence is available (backward-compatible default).
    """

    page_number: int
    text: str
    confidence: float | None = None


class OcrAdapter(Protocol):
    """Adapter interface for opt-in OCR implementations."""

    def extract_pdf_text(
        self,
        data: bytes,
        *,
        max_pages: int,
        timeout_seconds: float,
    ) -> list[OcrPageText]:
        """Return OCR text by page for PDF bytes."""

    def extract_image_text(
        self,
        data: bytes,
        *,
        timeout_seconds: float,
    ) -> list[OcrPageText]:
        """Return OCR text for image bytes."""


@dataclass(frozen=True, slots=True)
class OcrConfig:
    """Config-gated OCR execution settings."""

    enabled: bool = False
    adapter: OcrAdapter | None = None
    max_pages: int = 10
    timeout_seconds: float = 30.0


def normalize_ocr_confidence(values: Any) -> float | None:
    """Return the mean of valid Tesseract confidences (0-100) scaled to 0-1.

    Invalid entries (``-1``, non-numeric, empty, out-of-range) are ignored. Returns
    None when no valid confidence value exists.
    """
    if not isinstance(values, (list, tuple)):
        return None
    valid: list[float] = []
    for value in values:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if numeric < 0.0 or numeric > 100.0:
            continue
        valid.append(numeric / 100.0)
    if not valid:
        return None
    return round(sum(valid) / len(valid), 4)


def overall_mean_confidence(pages: list[OcrPageText]) -> float | None:
    """Return the mean of page/image confidences (0-1), ignoring None, else None."""
    values = [page.confidence for page in pages if page.confidence is not None]
    if not values:
        return None
    return round(sum(values) / len(values), 4)


PdfWorkerTarget = Callable[[bytes, int, int, str, float, Any], None]
ImageWorkerTarget = Callable[[bytes, int, str, float, Any], None]


class TesseractOcrAdapter:
    """Process-isolated OCR adapter backed by pdf2image and pytesseract."""

    def __init__(
        self,
        *,
        dpi: int = 200,
        language: str = "eng",
        worker_target: PdfWorkerTarget | None = None,
        image_worker_target: ImageWorkerTarget | None = None,
    ) -> None:
        self._dpi = dpi
        self._language = language
        self._worker_target = worker_target or _tesseract_ocr_worker
        self._image_worker_target = image_worker_target or _tesseract_image_ocr_worker

    def extract_pdf_text(
        self,
        data: bytes,
        *,
        max_pages: int,
        timeout_seconds: float,
    ) -> list[OcrPageText]:
        """Return OCR text from the configured page window."""
        if max_pages < 1:
            raise OcrError("OCR max_pages must be positive")
        if timeout_seconds <= 0:
            raise OcrTimeoutError("OCR timeout must be positive")

        queue: mp.Queue[dict[str, object]] = mp.Queue(maxsize=1)
        process = mp.Process(
            target=self._worker_target,
            args=(data, max_pages, self._dpi, self._language, timeout_seconds, queue),
        )
        process.start()
        process.join(timeout_seconds)
        if process.is_alive():
            _terminate_process_tree(process)
            process.join()
            raise OcrTimeoutError("OCR timed out")

        payload = _read_worker_payload(queue)
        return _pages_from_worker_payload(payload)

    def extract_image_text(
        self,
        data: bytes,
        *,
        timeout_seconds: float,
    ) -> list[OcrPageText]:
        """Return OCR text from one image in a process-isolated worker."""
        if timeout_seconds <= 0:
            raise OcrTimeoutError("OCR timeout must be positive")

        queue: mp.Queue[dict[str, object]] = mp.Queue(maxsize=1)
        process = mp.Process(
            target=self._image_worker_target,
            args=(data, self._dpi, self._language, timeout_seconds, queue),
        )
        process.start()
        process.join(timeout_seconds)
        if process.is_alive():
            _terminate_process_tree(process)
            process.join()
            raise OcrTimeoutError("OCR timed out")

        payload = _read_worker_payload(queue)
        return _pages_from_worker_payload(payload)


def _read_worker_payload(queue: mp.Queue[dict[str, object]]) -> dict[str, object]:
    try:
        return queue.get_nowait()
    except Empty as exc:
        raise OcrError("OCR worker produced no result") from exc


def _pages_from_worker_payload(payload: dict[str, object]) -> list[OcrPageText]:
    status = payload.get("status")
    if status == "success":
        pages = payload.get("pages")
        if not isinstance(pages, list):
            raise OcrError("OCR worker returned malformed pages")
        results: list[OcrPageText] = []
        for page in pages:
            if not isinstance(page, dict):
                raise OcrError("OCR worker returned malformed page")
            results.append(
                OcrPageText(
                    page_number=int(page["page_number"]),
                    text=str(page["text"]),
                    confidence=normalize_ocr_confidence(page.get("confidences")),
                )
            )
        return results
    if status == "timeout":
        raise OcrTimeoutError("OCR timed out")
    if status == "unavailable":
        raise OcrUnavailableError("OCR dependencies are unavailable")
    raise OcrError("OCR failed")


def _tesseract_ocr_worker(
    data: bytes,
    max_pages: int,
    dpi: int,
    language: str,
    timeout_seconds: float,
    queue: Any,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    try:
        with _suppress_output():
            try:
                from pdf2image import convert_from_bytes
                from pdf2image.exceptions import PDFInfoNotInstalledError, PDFPopplerTimeoutError
                from pytesseract import (
                    Output,
                    TesseractError,
                    TesseractNotFoundError,
                    image_to_data,
                    image_to_string,
                )
            except ImportError:
                _put_payload(queue, {"status": "unavailable"})
                return

            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    _put_payload(queue, {"status": "timeout"})
                    return
                images = convert_from_bytes(
                    data,
                    dpi=dpi,
                    first_page=1,
                    last_page=max_pages,
                    fmt="png",
                    grayscale=True,
                    thread_count=1,
                    timeout=max(1, int(remaining)),
                )
            except PDFInfoNotInstalledError:
                _put_payload(queue, {"status": "unavailable"})
                return
            except PDFPopplerTimeoutError:
                _put_payload(queue, {"status": "timeout"})
                return

            pages: list[dict[str, object]] = []
            for page_number, image in enumerate(images, start=1):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    _put_payload(queue, {"status": "timeout"})
                    return
                try:
                    text = image_to_string(image, lang=language, timeout=remaining)
                except TesseractNotFoundError:
                    _put_payload(queue, {"status": "unavailable"})
                    return
                except TesseractError as exc:
                    if _is_tesseract_unavailable(exc):
                        _put_payload(queue, {"status": "unavailable"})
                    else:
                        _put_payload(queue, {"status": "failed"})
                    return
                except RuntimeError as exc:
                    if _is_timeout_error(exc):
                        _put_payload(queue, {"status": "timeout"})
                    else:
                        _put_payload(queue, {"status": "failed"})
                    return
                confidences = _word_confidences(
                    image_to_data, Output, image, language, deadline - time.monotonic()
                )
                pages.append({"page_number": page_number, "text": text, "confidences": confidences})
            _put_payload(queue, {"status": "success", "pages": pages})
    except Exception:
        _put_payload(queue, {"status": "failed"})


def _tesseract_image_ocr_worker(
    data: bytes,
    dpi: int,
    language: str,
    timeout_seconds: float,
    queue: Any,
) -> None:
    del dpi
    try:
        with _suppress_output():
            try:
                from io import BytesIO

                from PIL import Image
                from pytesseract import (
                    Output,
                    TesseractError,
                    TesseractNotFoundError,
                    image_to_data,
                    image_to_string,
                )
            except ImportError:
                _put_payload(queue, {"status": "unavailable"})
                return

            try:
                with Image.open(BytesIO(data)) as image:
                    if not _image_within_resource_bounds(
                        width=image.size[0],
                        height=image.size[1],
                        frame_count=int(getattr(image, "n_frames", 1)),
                    ):
                        _put_payload(queue, {"status": "failed"})
                        return
                    rgb_image = image.convert("RGB")
                    text = image_to_string(
                        rgb_image,
                        lang=language,
                        timeout=timeout_seconds,
                    )
                    confidences = _word_confidences(
                        image_to_data, Output, rgb_image, language, timeout_seconds
                    )
            except TesseractNotFoundError:
                _put_payload(queue, {"status": "unavailable"})
                return
            except TesseractError as exc:
                if _is_tesseract_unavailable(exc):
                    _put_payload(queue, {"status": "unavailable"})
                else:
                    _put_payload(queue, {"status": "failed"})
                return
            except RuntimeError as exc:
                if _is_timeout_error(exc):
                    _put_payload(queue, {"status": "timeout"})
                else:
                    _put_payload(queue, {"status": "failed"})
                return

            _put_payload(
                queue,
                {
                    "status": "success",
                    "pages": [{"page_number": 1, "text": text, "confidences": confidences}],
                },
            )
    except Exception:
        _put_payload(queue, {"status": "failed"})


def _put_payload(queue: Any, payload: dict[str, object]) -> None:
    queue.put(payload)


def _word_confidences(
    image_to_data: Any,
    output: Any,
    image: Any,
    language: str,
    timeout: float,
) -> list[str]:
    """Best-effort per-word OCR confidences; never raises, returns [] on any failure.

    Confidence is purely additive diagnostics — text extraction via image_to_string is
    unchanged. Failures here (timeout, missing data) degrade to no confidence, not an
    OCR failure.
    """
    if timeout <= 0:
        return []
    try:
        data = image_to_data(
            image,
            lang=language,
            timeout=max(1, int(timeout)),
            output_type=output.DICT,
        )
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    values = data.get("conf", [])
    if not isinstance(values, (list, tuple)):
        return []
    return [str(value) for value in values]


def _image_within_resource_bounds(*, width: int, height: int, frame_count: int) -> bool:
    if width < 1 or height < 1 or frame_count != 1:
        return False
    return width * height <= MAX_OCR_IMAGE_PIXELS


def _terminate_process_tree(process: mp.Process) -> None:
    pid = process.pid
    if pid is None:
        process.terminate()
        return
    try:
        import psutil

        root = psutil.Process(pid)
        children = root.children(recursive=True)
        for child in children:
            child.terminate()
        root.terminate()
        gone, alive = psutil.wait_procs([*children, root], timeout=2)
        del gone
        for child in alive:
            child.kill()
    except Exception:
        process.terminate()


def _is_timeout_error(exc: RuntimeError) -> bool:
    return "timeout" in str(exc).lower()


def _is_tesseract_unavailable(exc: Exception) -> bool:
    lowered = str(exc).lower()
    unavailable_tokens = (
        "error opening data file",
        "failed loading language",
        "could not initialize tesseract",
    )
    return any(token in lowered for token in unavailable_tokens)


def _suppress_output() -> contextlib.AbstractContextManager[None]:
    @contextlib.contextmanager
    def suppress() -> Iterator[None]:
        with (
            open(os.devnull, "w", encoding="utf-8") as devnull,
            contextlib.redirect_stdout(devnull),
            contextlib.redirect_stderr(devnull),
        ):
            yield

    return suppress()
