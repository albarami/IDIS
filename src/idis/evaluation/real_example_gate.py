"""Private local gate for the fund-supplied real_example data room."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import logging
import multiprocessing as mp
import os
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from queue import Empty
from typing import Any

from idis.models.document_classification import DocumentSupportStatus, DocumentTriageStatus
from idis.parsers.registry import parse_bytes
from idis.services.documents.parser_capabilities import capability_for_document, triage_document

logger = logging.getLogger(__name__)

DEFAULT_REAL_EXAMPLE_ROOT = Path("real_example")
DEFAULT_LEDGER_PATH = Path(".local_reports") / "real_example_gate_ledger.json"
DEFAULT_PER_FILE_TIMEOUT_SECONDS = 30.0
LEDGER_VERSION = 1
SUPPORTED_PARSE_EXTENSIONS = frozenset({".pdf", ".xlsx", ".docx", ".pptx"})
SUPPORTED_PARSER_NAMES = frozenset({"pdf", "xlsx", "docx", "pptx"})
RETRYABLE_REASON_CODES = frozenset(
    {
        "internal_error",
        "max_memory_exceeded",
        "max_runtime_exceeded",
        "parse_timeout",
        "parser_failed",
    }
)


class GateMode(StrEnum):
    """Supported private gate execution modes."""

    INVENTORY_ONLY = "inventory_only"
    PARSE_SUPPORTED = "parse_supported"


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
    def unsupported(cls, *, reason_code: str) -> ParseAttempt:
        """Return an unsupported parse attempt."""
        return cls(status="unsupported", parser_outcome="not_attempted", reason_code=reason_code)

    def to_ledger_entry(self, *, extension: str, size_bytes: int) -> dict[str, object]:
        """Serialize without paths, filenames, text, or excerpts."""
        return {
            "extension": extension,
            "size_bytes": size_bytes,
            "status": self.status,
            "parser_outcome": self.parser_outcome,
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True, slots=True)
class _InventoryFile:
    path: Path
    extension: str
    size_bytes: int
    sha256: str
    read_error_reason: str | None = None


ParseAttemptFn = Callable[[Path], ParseAttempt]
ProgressFn = Callable[[dict[str, object]], None]


def run_real_example_gate(
    *,
    root: str | Path = DEFAULT_REAL_EXAMPLE_ROOT,
    ledger_path: str | Path = DEFAULT_LEDGER_PATH,
    mode: GateMode = GateMode.INVENTORY_ONLY,
    safe_summary: bool = True,
    per_file_timeout_seconds: float = DEFAULT_PER_FILE_TIMEOUT_SECONDS,
    max_runtime_seconds: float | None = None,
    max_memory_mb: int | None = None,
    emit_progress: bool = True,
    parse_attempt_fn: ParseAttemptFn | None = None,
    progress_fn: ProgressFn | None = None,
) -> dict[str, object]:
    """Run the local private real_example gate and return a safe aggregate summary.

    Args:
        root: Local data room root. The returned summary never includes it.
        ledger_path: Local resumable ledger path. The returned summary never includes it.
        mode: Inventory-only or supported-file parsing mode.
        safe_summary: Must remain true for parse-supported runs.
        per_file_timeout_seconds: Maximum seconds for one parse attempt.
        max_runtime_seconds: Optional total runtime budget.
        max_memory_mb: Optional process memory budget checked before each file.
        emit_progress: Whether to emit safe per-file progress to stderr.
        parse_attempt_fn: Optional test seam for parse attempts.
        progress_fn: Optional test seam for progress emission.

    Returns:
        Aggregate counts by extension, status, parser outcome, and reason code only.

    Raises:
        ValueError: If arguments would allow unsafe output.
        FileNotFoundError: If the root does not exist.
        NotADirectoryError: If the root is not a directory.
    """
    resolved_mode = GateMode(mode)
    if resolved_mode == GateMode.PARSE_SUPPORTED and not safe_summary:
        raise ValueError("--parse-supported requires --safe-summary")

    root_path = Path(root)
    _validate_root(root_path)
    files = _inventory_files(root_path)
    ledger_file = Path(ledger_path)
    ledger = _load_ledger(ledger_file)
    resume_entries = dict(ledger["entries"])
    progress = progress_fn or _emit_progress
    started_at = time.monotonic()
    records: list[dict[str, object]] = []

    for index, file in enumerate(files, start=1):
        attempt = _attempt_for_file(
            file=file,
            mode=resolved_mode,
            resume_entries=resume_entries,
            started_at=started_at,
            max_runtime_seconds=max_runtime_seconds,
            max_memory_mb=max_memory_mb,
            per_file_timeout_seconds=per_file_timeout_seconds,
            parse_attempt_fn=parse_attempt_fn,
        )
        records.append(
            {
                "extension": file.extension,
                "status": attempt.status,
                "parser_outcome": attempt.parser_outcome,
                "reason_code": attempt.reason_code,
            }
        )
        _record_ledger_entry(
            ledger=ledger,
            file=file,
            attempt=attempt,
            mode=resolved_mode,
        )
        _save_ledger(ledger_file, ledger)
        if emit_progress:
            progress(
                {
                    "index": index,
                    "total": len(files),
                    "extension": file.extension,
                    "status": attempt.status,
                    "reason_code": attempt.reason_code,
                }
            )

    _save_ledger(ledger_file, ledger)
    return _safe_summary(mode=resolved_mode, records=records, ledger=ledger)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint for the private real_example gate."""
    parser = argparse.ArgumentParser(
        description="Run the local-only private real_example gate.",
        epilog=(
            "Examples:\n"
            "  python scripts/run_real_example_gate.py --inventory-only\n"
            "  python scripts/run_real_example_gate.py --parse-supported --safe-summary\n"
            "  python scripts/run_real_example_gate.py --parse-supported --safe-summary "
            "--per-file-timeout-seconds 20 --max-runtime-seconds 900"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--inventory-only", action="store_true")
    modes.add_argument("--parse-supported", action="store_true")
    parser.add_argument("--safe-summary", action="store_true")
    parser.add_argument("--root", default=str(DEFAULT_REAL_EXAMPLE_ROOT))
    parser.add_argument("--ledger", default=str(DEFAULT_LEDGER_PATH))
    parser.add_argument(
        "--per-file-timeout-seconds",
        type=float,
        default=DEFAULT_PER_FILE_TIMEOUT_SECONDS,
    )
    parser.add_argument("--max-runtime-seconds", type=float)
    parser.add_argument("--max-memory-mb", type=int)
    parser.add_argument("--no-progress", action="store_true")
    args = parser.parse_args(argv)

    mode = GateMode.PARSE_SUPPORTED if args.parse_supported else GateMode.INVENTORY_ONLY
    if mode == GateMode.PARSE_SUPPORTED and not args.safe_summary:
        parser.error("--parse-supported requires --safe-summary")

    try:
        summary = run_real_example_gate(
            root=args.root,
            ledger_path=args.ledger,
            mode=mode,
            safe_summary=True,
            per_file_timeout_seconds=args.per_file_timeout_seconds,
            max_runtime_seconds=args.max_runtime_seconds,
            max_memory_mb=args.max_memory_mb,
            emit_progress=not args.no_progress,
        )
    except (FileNotFoundError, NotADirectoryError, RuntimeError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "gate": "real_example_private_v1",
                    "safe_summary": True,
                    "status": "failed",
                    "reason_code": _safe_cli_reason(exc),
                },
                sort_keys=True,
                indent=2,
            )
        )
        return 1
    print(json.dumps(summary, sort_keys=True, indent=2))
    return 0


def _attempt_for_file(
    *,
    file: _InventoryFile,
    mode: GateMode,
    resume_entries: dict[str, object],
    started_at: float,
    max_runtime_seconds: float | None,
    max_memory_mb: int | None,
    per_file_timeout_seconds: float,
    parse_attempt_fn: ParseAttemptFn | None,
) -> ParseAttempt:
    if mode == GateMode.INVENTORY_ONLY:
        return ParseAttempt(
            status="inventoried",
            parser_outcome="not_attempted",
            reason_code="inventory_only",
        )
    if file.read_error_reason is not None:
        return ParseAttempt(
            status="failed",
            parser_outcome="not_attempted",
            reason_code=file.read_error_reason,
        )

    existing = _terminal_ledger_entry(
        entries=resume_entries,
        sha256=file.sha256,
        extension=file.extension,
    )
    if existing is not None:
        return ParseAttempt(
            status=str(existing["status"]),
            parser_outcome="resumed",
            reason_code=str(existing["reason_code"]),
        )

    capability = capability_for_document(
        filename=f"file{file.extension}",
        file_size_bytes=file.size_bytes,
    )
    if file.extension not in SUPPORTED_PARSE_EXTENSIONS:
        reason_code = (
            "unsupported_in_slice_29"
            if file.extension == ".xlsm"
            else _capability_reason_code(
                support_status=capability.support_status,
                triage_status=capability.triage_status,
                reason_codes=capability.reason_codes,
            )
        )
        if capability.support_status == DocumentSupportStatus.UNSUPPORTED:
            return ParseAttempt.unsupported(reason_code=reason_code)
        return ParseAttempt.deferred(reason_code=reason_code)

    if not _should_parse(capability.support_status, capability.parser_name):
        reason_code = _capability_reason_code(
            support_status=capability.support_status,
            triage_status=capability.triage_status,
            reason_codes=capability.reason_codes,
        )
        if capability.support_status == DocumentSupportStatus.UNSUPPORTED:
            return ParseAttempt.unsupported(reason_code=reason_code)
        return ParseAttempt.deferred(reason_code=reason_code)

    if _runtime_exceeded(started_at, max_runtime_seconds):
        return ParseAttempt.deferred(reason_code="max_runtime_exceeded")
    if _memory_exceeded(max_memory_mb):
        return ParseAttempt.deferred(reason_code="max_memory_exceeded")

    if parse_attempt_fn is not None:
        return _run_injected_parse_with_timeout(
            file.path,
            parse_attempt_fn=parse_attempt_fn,
            timeout_seconds=per_file_timeout_seconds,
        )
    return _run_parse_subprocess(
        file.path,
        timeout_seconds=per_file_timeout_seconds,
        max_memory_mb=max_memory_mb,
    )


def _inventory_files(root: Path) -> list[_InventoryFile]:
    records: list[_InventoryFile] = []
    for path in sorted((item for item in root.rglob("*") if item.is_file()), key=_sort_key(root)):
        extension = path.suffix.lower() or ".unknown"
        try:
            data = path.read_bytes()
            records.append(
                _InventoryFile(
                    path=path,
                    extension=extension,
                    size_bytes=len(data),
                    sha256=hashlib.sha256(data).hexdigest(),
                )
            )
        except OSError:
            records.append(
                _InventoryFile(
                    path=path,
                    extension=extension,
                    size_bytes=0,
                    sha256=_unreadable_file_key(root=root, path=path),
                    read_error_reason="file_scan_failed",
                )
            )
    return records


def _sort_key(root: Path) -> Callable[[Path], str]:
    def key(path: Path) -> str:
        return path.relative_to(root).as_posix().lower()

    return key


def _run_injected_parse_with_timeout(
    path: Path,
    *,
    parse_attempt_fn: ParseAttemptFn,
    timeout_seconds: float,
) -> ParseAttempt:
    deadline = time.monotonic() + timeout_seconds
    with _suppress_parser_output():
        attempt = parse_attempt_fn(path)
    if time.monotonic() > deadline:
        return ParseAttempt.timed_out()
    return attempt


def _run_parse_subprocess(
    path: Path,
    *,
    timeout_seconds: float,
    max_memory_mb: int | None,
) -> ParseAttempt:
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


def _parse_file_worker(
    path: str,
    max_memory_mb: int | None,
    queue: mp.Queue[dict[str, str]],
) -> None:
    try:
        if _memory_exceeded(max_memory_mb):
            _put_attempt(queue, ParseAttempt.deferred(reason_code="max_memory_exceeded"))
            return
        with _suppress_parser_output():
            data = Path(path).read_bytes()
            result = parse_bytes(data, filename=None)
        if _memory_exceeded(max_memory_mb):
            _put_attempt(queue, ParseAttempt.deferred(reason_code="max_memory_exceeded"))
            return
        if result.success:
            attempt = ParseAttempt.parsed()
        else:
            capability = triage_document(filename="file", parse_result=result)
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


def _should_parse(support_status: DocumentSupportStatus, parser_name: str | None) -> bool:
    return (
        support_status
        in {
            DocumentSupportStatus.SUPPORTED,
            DocumentSupportStatus.PARTIALLY_SUPPORTED,
        }
        and parser_name in SUPPORTED_PARSER_NAMES
    )


def _capability_reason_code(
    *,
    support_status: DocumentSupportStatus,
    triage_status: DocumentTriageStatus,
    reason_codes: list[str],
) -> str:
    if support_status == DocumentSupportStatus.CONVERSION_REQUIRED:
        return "conversion_required"
    if triage_status == DocumentTriageStatus.OCR_REQUIRED:
        return "ocr_required"
    if support_status == DocumentSupportStatus.UNSUPPORTED:
        return "unsupported_format"
    if support_status == DocumentSupportStatus.TOO_LARGE:
        return "file_too_large"
    return _first_reason_code(reason_codes, default="unknown_format")


def _first_reason_code(reason_codes: list[str], *, default: str) -> str:
    if not reason_codes:
        return default
    return sorted(reason_codes)[0]


def _runtime_exceeded(started_at: float, max_runtime_seconds: float | None) -> bool:
    return max_runtime_seconds is not None and time.monotonic() - started_at >= max_runtime_seconds


def _memory_exceeded(max_memory_mb: int | None) -> bool:
    if max_memory_mb is None:
        return False
    return _current_memory_mb() >= max_memory_mb


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
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
        return float(counters.WorkingSetSize) / (1024.0 * 1024.0)
    except (AttributeError, OSError, ValueError):
        return 0.0


def _load_ledger(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": LEDGER_VERSION, "entries": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": LEDGER_VERSION, "entries": {}}
    if not isinstance(payload, dict) or payload.get("version") != LEDGER_VERSION:
        return {"version": LEDGER_VERSION, "entries": {}}
    entries = payload.get("entries")
    if not isinstance(entries, dict):
        return {"version": LEDGER_VERSION, "entries": {}}
    return {"version": LEDGER_VERSION, "entries": entries}


def _save_ledger(path: Path, ledger: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f"{path.name}.tmp")
        temp_path.write_text(json.dumps(ledger, sort_keys=True, indent=2), encoding="utf-8")
        temp_path.replace(path)
    except OSError:
        raise RuntimeError("real_example gate ledger write failed") from None


def _terminal_ledger_entry(
    *,
    entries: dict[str, object],
    sha256: str,
    extension: str,
) -> dict[str, object] | None:
    entry = _ledger_entry_for_extension(entries=entries, sha256=sha256, extension=extension)
    if not isinstance(entry, dict):
        return None
    if entry.get("status") == "inventoried":
        return None
    reason_code = entry.get("reason_code")
    if not isinstance(reason_code, str) or reason_code in RETRYABLE_REASON_CODES:
        return None
    if entry.get("status") == "failed":
        return None
    return entry


def _ledger_entry_for_extension(
    *,
    entries: dict[str, object],
    sha256: str,
    extension: str,
) -> dict[str, object] | None:
    bucket = entries.get(sha256)
    if not isinstance(bucket, dict):
        return None
    by_extension = bucket.get("by_extension")
    if isinstance(by_extension, dict):
        entry = by_extension.get(extension)
        return entry if isinstance(entry, dict) else None
    if bucket.get("extension") == extension:
        return bucket
    return None


def _record_ledger_entry(
    *,
    ledger: dict[str, Any],
    file: _InventoryFile,
    attempt: ParseAttempt,
    mode: GateMode,
) -> None:
    if mode == GateMode.PARSE_SUPPORTED and attempt.parser_outcome == "resumed":
        return
    entries = ledger["entries"]
    bucket = entries.get(file.sha256)
    if isinstance(bucket, dict) and isinstance(bucket.get("by_extension"), dict):
        by_extension = bucket["by_extension"]
    else:
        by_extension = {}
        if isinstance(bucket, dict):
            legacy_extension = bucket.get("extension")
            if isinstance(legacy_extension, str):
                by_extension[legacy_extension] = bucket
        bucket = {"by_extension": by_extension}
        entries[file.sha256] = bucket
    by_extension[file.extension] = attempt.to_ledger_entry(
        extension=file.extension,
        size_bytes=file.size_bytes,
    )


def _safe_summary(
    *,
    mode: GateMode,
    records: list[dict[str, object]],
    ledger: dict[str, Any],
) -> dict[str, object]:
    return {
        "gate": "real_example_private_v1",
        "safe_summary": True,
        "mode": mode.value,
        "total_files": len(records),
        "processed_files": len(records),
        "ledger_entry_count": _ledger_entry_count(ledger),
        "counts_by_extension": _count(records, "extension"),
        "counts_by_status": _count(records, "status"),
        "counts_by_parser_outcome": _count(records, "parser_outcome"),
        "counts_by_reason_code": _count(records, "reason_code"),
    }


def _count(records: list[dict[str, object]], key: str) -> dict[str, int]:
    return dict(sorted(Counter(str(record[key]) for record in records).items()))


def _ledger_entry_count(ledger: dict[str, Any]) -> int:
    count = 0
    for bucket in ledger["entries"].values():
        if isinstance(bucket, dict) and isinstance(bucket.get("by_extension"), dict):
            count += len(bucket["by_extension"])
        elif isinstance(bucket, dict):
            count += 1
    return count


def _validate_root(root: Path) -> None:
    if not root.exists():
        raise FileNotFoundError("real_example root does not exist")
    if not root.is_dir():
        raise NotADirectoryError("real_example root is not a directory")


def _safe_cli_reason(exc: BaseException) -> str:
    if isinstance(exc, FileNotFoundError):
        return "root_not_found"
    if isinstance(exc, NotADirectoryError):
        return "root_not_directory"
    if isinstance(exc, RuntimeError):
        return "ledger_write_failed"
    if isinstance(exc, ValueError):
        return "invalid_arguments"
    return "internal_error"


def _unreadable_file_key(*, root: Path, path: Path) -> str:
    relative = path.relative_to(root).as_posix().encode("utf-8", errors="replace")
    return f"unreadable:{hashlib.sha256(relative).hexdigest()}"


def _emit_progress(event: dict[str, object]) -> None:
    print(json.dumps({"progress": event}, sort_keys=True), file=sys.stderr)


__all__ = [
    "GateMode",
    "ParseAttempt",
    "main",
    "run_real_example_gate",
]
