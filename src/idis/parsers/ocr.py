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


class OcrError(Exception):
    """Base class for safe OCR adapter failures."""


class OcrTimeoutError(OcrError):
    """Raised by OCR adapters when OCR exceeds a configured runtime budget."""


class OcrUnavailableError(OcrError):
    """Raised when OCR dependencies or binaries are not available."""


@dataclass(frozen=True, slots=True)
class OcrPageText:
    """OCR text extracted for one PDF page."""

    page_number: int
    text: str


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


@dataclass(frozen=True, slots=True)
class OcrConfig:
    """Config-gated OCR execution settings."""

    enabled: bool = False
    adapter: OcrAdapter | None = None
    max_pages: int = 10
    timeout_seconds: float = 30.0


WorkerTarget = Callable[[bytes, int, int, str, float, Any], None]


class TesseractOcrAdapter:
    """Process-isolated OCR adapter backed by pdf2image and pytesseract."""

    def __init__(
        self,
        *,
        dpi: int = 200,
        language: str = "eng",
        worker_target: WorkerTarget | None = None,
    ) -> None:
        self._dpi = dpi
        self._language = language
        self._worker_target = worker_target or _tesseract_ocr_worker

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

        try:
            payload = queue.get_nowait()
        except Empty as exc:
            raise OcrError("OCR worker produced no result") from exc

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
                    OcrPageText(page_number=int(page["page_number"]), text=str(page["text"]))
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
                from pytesseract import TesseractError, TesseractNotFoundError, image_to_string
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
                pages.append({"page_number": page_number, "text": text})
            _put_payload(queue, {"status": "success", "pages": pages})
    except Exception:
        _put_payload(queue, {"status": "failed"})


def _put_payload(queue: Any, payload: dict[str, object]) -> None:
    queue.put(payload)


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
