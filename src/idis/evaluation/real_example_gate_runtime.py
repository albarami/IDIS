"""Runtime helpers for the private real_example gate."""

from __future__ import annotations

import contextlib
import logging
import multiprocessing as mp
import os
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from queue import Empty
from typing import Any, cast

from idis.parsers.registry import parse_bytes
from idis.services.documents.parser_capabilities import triage_document

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ParseAttempt:
    """Safe parse attempt outcome for one file."""

    status: str
    parser_outcome: str
    reason_code: str

    @classmethod
    def parsed(cls) -> ParseAttempt:
        """Return a successful parse attempt."""
        return cls(status="parsed", parser_outcome="success", reason_code="parsed")

    @classmethod
    def failed(cls, *, reason_code: str) -> ParseAttempt:
        """Return a failed parse attempt."""
        return cls(status="failed", parser_outcome="error", reason_code=reason_code)

    @classmethod
    def timed_out(cls) -> ParseAttempt:
        """Return a timed-out parse attempt."""
        return cls(status="timed_out", parser_outcome="timeout", reason_code="parse_timeout")

    @classmethod
    def deferred(cls, *, reason_code: str) -> ParseAttempt:
        """Return a deferred parse attempt."""
        return cls(status="deferred", parser_outcome="not_attempted", reason_code=reason_code)

    @classmethod
    def ocr_required(cls) -> ParseAttempt:
        """Return a deferred parse attempt for OCR-required documents."""
        return cls(status="deferred", parser_outcome="ocr_required", reason_code="ocr_required")

    @classmethod
    def unsupported(cls, *, reason_code: str) -> ParseAttempt:
        """Return an unsupported parse attempt."""
        return cls(status="unsupported", parser_outcome="not_attempted", reason_code=reason_code)


ParseAttemptFn = Callable[[Path], ParseAttempt]


def run_injected_parse_with_timeout(
    path: Path,
    *,
    parse_attempt_fn: ParseAttemptFn,
    timeout_seconds: float,
) -> ParseAttempt:
    """Run an injected parser while suppressing accidental parser diagnostics."""
    deadline = time.monotonic() + timeout_seconds
    with _suppress_parser_output():
        attempt = parse_attempt_fn(path)
    if time.monotonic() > deadline:
        return ParseAttempt.timed_out()
    return attempt


def run_parse_subprocess(
    path: Path,
    *,
    timeout_seconds: float,
    max_memory_mb: int | None,
) -> ParseAttempt:
    """Run production parsing in a child process with timeout and safe output."""
    queue: mp.Queue[dict[str, str]] = mp.Queue(maxsize=1)
    process = mp.Process(target=_parse_file_worker, args=(str(path), max_memory_mb, queue))
    process.start()
    process.join(timeout_seconds)
    if process.is_alive():
        process.terminate()
        process.join()
        return ParseAttempt.timed_out()

    try:
        payload = queue.get_nowait()
    except Empty:
        return ParseAttempt.failed(reason_code="parser_failed")
    return ParseAttempt(
        status=payload["status"],
        parser_outcome=payload["parser_outcome"],
        reason_code=payload["reason_code"],
    )


def memory_exceeded(max_memory_mb: int | None) -> bool:
    """Return whether the current process is over the configured memory budget."""
    if max_memory_mb is None:
        return False
    return _current_memory_mb() >= max_memory_mb


def _parse_file_worker(
    path: str,
    max_memory_mb: int | None,
    queue: mp.Queue[dict[str, str]],
) -> None:
    try:
        if memory_exceeded(max_memory_mb):
            _put_attempt(queue, ParseAttempt.deferred(reason_code="max_memory_exceeded"))
            return
        with _suppress_parser_output():
            data = Path(path).read_bytes()
            result = parse_bytes(data, filename=None)
        if memory_exceeded(max_memory_mb):
            _put_attempt(queue, ParseAttempt.deferred(reason_code="max_memory_exceeded"))
            return
        if result.success:
            attempt = ParseAttempt.parsed()
        else:
            capability = triage_document(filename="file", parse_result=result)
            if capability.requires_ocr:
                attempt = ParseAttempt.ocr_required()
            else:
                reason_code = _first_reason_code(capability.reason_codes, default="parser_failed")
                attempt = ParseAttempt.failed(reason_code=reason_code)
        _put_attempt(queue, attempt)
    except Exception as exc:
        logger.warning("Private real_example parse worker failed safely: %s", type(exc).__name__)
        _put_attempt(queue, ParseAttempt.failed(reason_code="internal_error"))


def _put_attempt(queue: mp.Queue[dict[str, str]], attempt: ParseAttempt) -> None:
    queue.put(
        {
            "status": attempt.status,
            "parser_outcome": attempt.parser_outcome,
            "reason_code": attempt.reason_code,
        }
    )


def _suppress_parser_output() -> contextlib.AbstractContextManager[None]:
    @contextlib.contextmanager
    def suppress() -> Iterator[None]:
        with (
            open(os.devnull, "w", encoding="utf-8") as devnull,
            contextlib.redirect_stdout(devnull),
            contextlib.redirect_stderr(devnull),
        ):
            yield

    return suppress()


def _first_reason_code(reason_codes: list[str], *, default: str) -> str:
    if not reason_codes:
        return default
    return sorted(reason_codes)[0]


def _current_memory_mb() -> float:
    if os.name == "nt":
        return _current_windows_memory_mb()
    try:
        resource_module = __import__("resource")
        usage = resource_module.getrusage(resource_module.RUSAGE_SELF)
    except (ImportError, OSError):
        return 0.0
    return float(usage.ru_maxrss) / 1024.0


def _current_windows_memory_mb() -> float:
    try:
        import ctypes
        from ctypes import wintypes

        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(ProcessMemoryCounters)
        windll = cast(Any, getattr(ctypes, "windll", None))
        if windll is None:
            return 0.0
        handle = windll.kernel32.GetCurrentProcess()
        windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
        return float(counters.WorkingSetSize) / (1024.0 * 1024.0)
    except (AttributeError, OSError, ValueError):
        return 0.0
