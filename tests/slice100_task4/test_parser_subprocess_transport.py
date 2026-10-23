"""Task 4 RED contracts for bounded parser subprocess result transfer."""

from __future__ import annotations

import multiprocessing as mp
import time
from collections.abc import Callable
from pathlib import Path
from queue import Empty
from typing import Any

import pytest

from idis.parsers import media, ocr
from idis.parsers.base import ParseErrorCode
from idis.parsers.media import (
    FasterWhisperMediaAdapter,
    FasterWhisperMediaConfig,
    MediaConfig,
    MediaSegmentText,
    MediaTimeoutError,
    parse_media,
)
from idis.parsers.ocr import OcrTimeoutError, TesseractOcrAdapter

_LARGE_RESULT_TEXT = "x" * (2 * 1024 * 1024)


class _TrackedProcessQueue:
    """Pickle-safe queue proxy that records parent-side lifecycle calls."""

    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate
        self.closed = False
        self.joined = False
        self.cancelled = False

    def put(self, value: object) -> None:
        self._delegate.put(value)

    def get(self, *, timeout: float | None = None) -> object:
        return self._delegate.get(timeout=timeout)

    def get_nowait(self) -> object:
        return self._delegate.get_nowait()

    def close(self) -> None:
        self.closed = True
        self._delegate.close()

    def join_thread(self) -> None:
        self.joined = True
        self._delegate.join_thread()

    def cancel_join_thread(self) -> None:
        self.cancelled = True
        self._delegate.cancel_join_thread()


class _TrackedProcess:
    """Parent-side process proxy that proves OS handles are explicitly closed."""

    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate
        self.closed = False

    @property
    def pid(self) -> int | None:
        value = self._delegate.pid
        return value if isinstance(value, int) else None

    def start(self) -> None:
        self._delegate.start()

    def join(self, timeout: float | None = None) -> None:
        self._delegate.join(timeout)

    def is_alive(self) -> bool:
        return bool(self._delegate.is_alive())

    def terminate(self) -> None:
        self._delegate.terminate()

    def kill(self) -> None:
        self._delegate.kill()

    def close(self) -> None:
        self.closed = True
        self._delegate.close()


class _DeadlineTestQueue:
    """Queue double whose join would expose cleanup outside a parser deadline."""

    def __init__(self) -> None:
        self.closed = False
        self.cancelled = False

    def get(self, *, timeout: float | None = None) -> object:
        time.sleep(max(0.0, float(timeout or 0.0)))
        raise Empty

    def get_nowait(self) -> object:
        raise Empty

    def close(self) -> None:
        self.closed = True

    def join_thread(self) -> None:
        time.sleep(2.0)

    def cancel_join_thread(self) -> None:
        self.cancelled = True


class _DeadlineTestProcess:
    """Stubborn process double proving cleanup waits consume only reserved budget."""

    def __init__(self) -> None:
        self.alive = True
        self.closed = False

    @property
    def pid(self) -> None:
        return None

    def start(self) -> None:
        return None

    def join(self, timeout: float | None = None) -> None:
        if self.alive:
            time.sleep(max(0.0, float(timeout or 0.0)))

    def is_alive(self) -> bool:
        return self.alive

    def terminate(self) -> None:
        return None

    def kill(self) -> None:
        self.alive = False

    def close(self) -> None:
        self.closed = True


def _large_pdf_ocr_worker(
    data: bytes,
    max_pages: int,
    dpi: int,
    language: str,
    timeout_seconds: float,
    queue: Any,
) -> None:
    del data, max_pages, dpi, language, timeout_seconds
    queue.put(
        {
            "status": "success",
            "pages": [{"page_number": 1, "text": _LARGE_RESULT_TEXT}],
        }
    )


def _large_image_ocr_worker(
    data: bytes,
    dpi: int,
    language: str,
    timeout_seconds: float,
    queue: Any,
) -> None:
    del data, dpi, language, timeout_seconds
    queue.put(
        {
            "status": "success",
            "pages": [{"page_number": 1, "text": _LARGE_RESULT_TEXT}],
        }
    )


def _small_pdf_ocr_worker(
    data: bytes,
    max_pages: int,
    dpi: int,
    language: str,
    timeout_seconds: float,
    queue: Any,
) -> None:
    del data, max_pages, dpi, language, timeout_seconds
    queue.put({"status": "success", "pages": [{"page_number": 1, "text": "ok"}]})


def _slow_pdf_ocr_worker(
    data: bytes,
    max_pages: int,
    dpi: int,
    language: str,
    timeout_seconds: float,
    queue: Any,
) -> None:
    del data, max_pages, dpi, language, timeout_seconds, queue
    time.sleep(5)


def _large_media_worker(
    source: object,
    config: FasterWhisperMediaConfig,
    timeout_seconds: float,
    queue: Any,
) -> None:
    del source, config, timeout_seconds
    queue.put(
        {
            "status": "success",
            "segments": [{"start_ms": 0, "end_ms": 1_000, "text": _LARGE_RESULT_TEXT}],
        }
    )


def _small_media_worker(
    source: object,
    config: FasterWhisperMediaConfig,
    timeout_seconds: float,
    queue: Any,
) -> None:
    del source, config, timeout_seconds
    queue.put(
        {
            "status": "success",
            "segments": [{"start_ms": 0, "end_ms": 1_000, "text": "ok"}],
        }
    )


def _slow_media_worker(
    source: object,
    config: FasterWhisperMediaConfig,
    timeout_seconds: float,
    queue: Any,
) -> None:
    del source, config, timeout_seconds, queue
    time.sleep(5)


class _ZeroDurationMediaAdapter:
    def extract_text(self, data: bytes, *, timeout_seconds: float) -> list[MediaSegmentText]:
        del data, timeout_seconds
        return [MediaSegmentText(start_ms=1_000, end_ms=1_000, text="invalid")]


def _model_dir(tmp_path: Path) -> Path:
    model = tmp_path / "fictional-model"
    model.mkdir(exist_ok=True)
    (model / "config.json").write_text("{}", encoding="utf-8")
    (model / "model.bin").write_bytes(b"fictional-model")
    return model


def _media_adapter(
    tmp_path: Path,
    worker_target: Callable[..., None],
) -> FasterWhisperMediaAdapter:
    return FasterWhisperMediaAdapter(
        config=FasterWhisperMediaConfig(
            model_path=str(_model_dir(tmp_path)),
            allow_model_download=False,
            max_duration_seconds=10,
        ),
        binary_resolver=lambda _name: "fictional-binary",
        duration_probe=lambda _source, _config, _timeout: 1.0,
        worker_target=worker_target,
    )


def _wait_for_no_new_children(before_pids: set[int | None]) -> None:
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if not [
            child
            for child in mp.active_children()
            if child.pid not in before_pids and child.is_alive()
        ]:
            return
        time.sleep(0.01)
    assert not [
        child for child in mp.active_children() if child.pid not in before_pids and child.is_alive()
    ]


def _tracked_queue_factory(
    original_factory: Callable[..., Any],
    created: list[_TrackedProcessQueue],
) -> Callable[..., _TrackedProcessQueue]:
    def factory(*, maxsize: int = 0) -> _TrackedProcessQueue:
        tracked = _TrackedProcessQueue(original_factory(maxsize=maxsize))
        created.append(tracked)
        return tracked

    return factory


def _tracked_process_factory(
    original_factory: Callable[..., Any],
    created: list[_TrackedProcess],
) -> Callable[..., _TrackedProcess]:
    def factory(*args: object, **kwargs: object) -> _TrackedProcess:
        tracked = _TrackedProcess(original_factory(*args, **kwargs))
        created.append(tracked)
        return tracked

    return factory


def test_ocr_large_success_payload_is_drained_before_worker_join() -> None:
    adapter = TesseractOcrAdapter(worker_target=_large_pdf_ocr_worker)

    # Transport proof uses a generous harness budget so suite load cannot mimic a deadlock.
    pages = adapter.extract_pdf_text(b"fictional-pdf", max_pages=1, timeout_seconds=15)

    assert len(pages) == 1
    assert pages[0].text == _LARGE_RESULT_TEXT


def test_image_ocr_large_success_payload_is_drained_before_worker_join() -> None:
    adapter = TesseractOcrAdapter(image_worker_target=_large_image_ocr_worker)

    # Transport proof uses a generous harness budget so suite load cannot mimic a deadlock.
    pages = adapter.extract_image_text(b"fictional-image", timeout_seconds=15)

    assert len(pages) == 1
    assert pages[0].text == _LARGE_RESULT_TEXT


def test_media_large_success_payload_is_drained_before_worker_join(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(media.importlib.metadata, "version", lambda _name: "1.2.3")
    adapter = _media_adapter(tmp_path, _large_media_worker)

    # Transport proof uses a generous harness budget so suite load cannot mimic a deadlock.
    segments = adapter.extract_text(b"fictional-media", timeout_seconds=15)

    assert len(segments) == 1
    assert segments[0].text == _LARGE_RESULT_TEXT


def test_ocr_repeated_success_and_timeout_close_queue_and_process_handles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_queue_factory = mp.Queue
    original_process_factory = mp.Process
    created: list[_TrackedProcessQueue] = []
    created_processes: list[_TrackedProcess] = []
    monkeypatch.setattr(
        ocr.mp,
        "Queue",
        _tracked_queue_factory(original_queue_factory, created),
    )
    monkeypatch.setattr(
        ocr.mp,
        "Process",
        _tracked_process_factory(original_process_factory, created_processes),
    )
    before_pids = {child.pid for child in mp.active_children()}

    for _attempt in range(2):
        pages = TesseractOcrAdapter(worker_target=_small_pdf_ocr_worker).extract_pdf_text(
            b"fictional-pdf",
            max_pages=1,
            timeout_seconds=2,
        )
        assert pages[0].text == "ok"
        with pytest.raises(OcrTimeoutError):
            TesseractOcrAdapter(worker_target=_slow_pdf_ocr_worker).extract_pdf_text(
                b"fictional-pdf",
                max_pages=1,
                timeout_seconds=0.05,
            )

    assert len(created) == 4
    assert all(queue.closed and queue.cancelled for queue in created)
    assert len(created_processes) == 4
    assert all(process.closed for process in created_processes)
    _wait_for_no_new_children(before_pids)


def test_media_repeated_success_and_timeout_close_queue_and_process_handles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(media.importlib.metadata, "version", lambda _name: "1.2.3")
    original_queue_factory = mp.Queue
    original_process_factory = mp.Process
    created: list[_TrackedProcessQueue] = []
    created_processes: list[_TrackedProcess] = []
    monkeypatch.setattr(
        media.mp,
        "Queue",
        _tracked_queue_factory(original_queue_factory, created),
    )
    monkeypatch.setattr(
        media.mp,
        "Process",
        _tracked_process_factory(original_process_factory, created_processes),
    )
    before_pids = {child.pid for child in mp.active_children()}

    for _attempt in range(2):
        segments = _media_adapter(tmp_path, _small_media_worker).extract_text(
            b"fictional-media",
            timeout_seconds=2,
        )
        assert segments[0].text == "ok"
        with pytest.raises(MediaTimeoutError):
            _media_adapter(tmp_path, _slow_media_worker).extract_text(
                b"fictional-media",
                timeout_seconds=0.05,
            )

    assert len(created) == 4
    assert all(queue.closed and queue.cancelled for queue in created)
    assert len(created_processes) == 4
    assert all(process.closed for process in created_processes)
    _wait_for_no_new_children(before_pids)


def test_media_timeout_cleanup_stays_inside_one_bounded_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stuck join/queue feeder cannot add fixed multi-second waits after timeout."""

    queues: list[_DeadlineTestQueue] = []
    processes: list[_DeadlineTestProcess] = []

    def queue_factory(*, maxsize: int = 0) -> _DeadlineTestQueue:
        assert maxsize == 1
        queue = _DeadlineTestQueue()
        queues.append(queue)
        return queue

    def process_factory(*_args: object, **_kwargs: object) -> _DeadlineTestProcess:
        process = _DeadlineTestProcess()
        processes.append(process)
        return process

    monkeypatch.setattr(media.mp, "Queue", queue_factory)
    monkeypatch.setattr(media.mp, "Process", process_factory)
    deadline = media._MediaDeadline.start(  # noqa: SLF001 - deadline transport contract
        timeout_seconds=0.10,
        clock=time.monotonic,
    )

    started_at = time.monotonic()
    with pytest.raises(MediaTimeoutError):
        media._run_media_worker(  # noqa: SLF001 - deadline transport contract
            target=_slow_media_worker,
            args=(object(), FasterWhisperMediaConfig(), 1.0),
            deadline=deadline,
        )
    elapsed = time.monotonic() - started_at

    assert elapsed < 0.75
    assert len(queues) == 1
    assert queues[0].closed is True
    assert queues[0].cancelled is True
    assert len(processes) == 1
    assert processes[0].alive is False
    assert processes[0].closed is True


def test_ocr_timeout_cleanup_stays_inside_one_bounded_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OCR cleanup cannot add fixed process/queue waits after its timeout."""

    queues: list[_DeadlineTestQueue] = []
    processes: list[_DeadlineTestProcess] = []

    def queue_factory(*, maxsize: int = 0) -> _DeadlineTestQueue:
        assert maxsize == 1
        queue = _DeadlineTestQueue()
        queues.append(queue)
        return queue

    def process_factory(*_args: object, **_kwargs: object) -> _DeadlineTestProcess:
        process = _DeadlineTestProcess()
        processes.append(process)
        return process

    monkeypatch.setattr(ocr.mp, "Queue", queue_factory)
    monkeypatch.setattr(ocr.mp, "Process", process_factory)

    started_at = time.monotonic()
    with pytest.raises(OcrTimeoutError):
        ocr._run_ocr_worker(  # noqa: SLF001 - deadline transport contract
            target=_slow_pdf_ocr_worker,
            args=(b"fictional-pdf", 1, 200, "eng", 1.0),
            timeout_seconds=0.10,
        )
    elapsed = time.monotonic() - started_at

    assert elapsed < 0.75
    assert len(queues) == 1
    assert queues[0].closed is True
    assert queues[0].cancelled is True
    assert len(processes) == 1
    assert processes[0].alive is False
    assert processes[0].closed is True


def test_media_zero_duration_segment_is_rejected_before_span_creation() -> None:
    result = parse_media(
        b"fictional-media",
        media_config=MediaConfig(
            enabled=True,
            adapter=_ZeroDurationMediaAdapter(),
            timeout_seconds=1,
        ),
    )

    assert result.success is False
    assert result.spans == []
    assert [error.code for error in result.errors] == [ParseErrorCode.MEDIA_TRANSCRIPTION_FAILED]
